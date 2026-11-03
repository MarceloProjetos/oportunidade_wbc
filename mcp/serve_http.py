"""HTTP (remote) entrypoint of the MCP facade -- Phase 3.

Serves the SAME FastMCP object as ``mcp_server.py`` (the same tools + resources as
Phase 1), but over the **Streamable HTTP** transport (uvicorn), behind the
``acesso_mcp.PortaDoMcp`` door (a Bearer token per client).

Topology (Option B): runs as a service ON the .11; MCP clients (Claude Desktop/Code)
point to ``http://<.11>:8078/mcp`` with the header. The stdio entrypoint (``mcp_server.py``)
stays untouched for local use.

Security:
- INBOUND (since 02/10/2026, F1 of PLANO_MIRA_AGENTE_11.md (removed 2026-10-06)): ``acesso_mcp.PortaDoMcp`` --
  one token per client (``python -m seguranca criar NOME --escopos mcp,...``), each tool
  in its own scope, the agent's off switch/business hours and an audit line per call. The
  old ``SIS_MCP_TOKEN`` still works as the client "mcp-legado". No token -> 401.
- OUTBOUND: ``mcp_server`` calls the API at ``SIS_API_BASE`` (=127.0.0.1:8077 on the .11),
  so the ``OS_API_KEY`` (``SIS_API_KEY``) NEVER leaves the server nor travels on the LAN.

Why not ``mcp.run("streamable-http")``: that path builds the Starlette app and starts
uvicorn INTERNALLY, with no hook to inject auth. To require the token we assemble the ASGI
app (``mcp.streamable_http_app()``) and uvicorn ourselves, with a middleware.

Config (via ``mcp/.env``, loaded by ``mcp_server``):
    SIS_MCP_TOKEN   old token, client "mcp-legado" (optional since 02/10/2026).
    SIS_MCP_HOST    bind (default 0.0.0.0).
    SIS_MCP_PORT    port (default 8078).
    SIS_API_BASE    API URL (on the .11 = http://127.0.0.1:8077).
    SIS_API_KEY     the OS_API_KEY (injected server-side into X-API-Key).

Run:  python serve_http.py     (or via run_mcp.bat / NSSM service OrcaView-MCP)
"""

from __future__ import annotations

import logging
import logging.config
import os

import uvicorn

# Importa o FastMCP ja montado (tools + resources) — isto tambem carrega o mcp/.env.
from acesso_mcp import PortaDoMcp
from mcp.server.transport_security import TransportSecuritySettings
from mcp_server import mcp

_TOKEN = os.environ.get("SIS_MCP_TOKEN", "").strip()
_HOST = os.environ.get("SIS_MCP_HOST", "0.0.0.0")
_PORT = int(os.environ.get("SIS_MCP_PORT", "8078"))
_PATH = "/mcp"


def _build_log_config() -> dict:
    """Own rotating log file (``logs/mcp_service.log``), 6 days.

    The Python process is the file's **only** owner -- same pattern as
    ``api.py`` / ``scheduled_execution.py`` (``TimedRotatingFileHandler``,
    ``backupCount=6``, deletes anything older than 6 days by itself). So the NSSM
    service ``OrcaView-MCP`` must NOT redirect/rotate this file anymore (otherwise
    NSSM's open handle blocks the midnight rotation rename).
    The per-request access log is off (``access_log=False``) to keep the file lean.
    """
    log_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs"
    )
    os.makedirs(log_dir, exist_ok=True)
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            },
        },
        "handlers": {
            "file": {
                "class": "logging.handlers.TimedRotatingFileHandler",
                "formatter": "default",
                "filename": os.path.join(log_dir, "mcp_service.log"),
                "when": "midnight",
                "interval": 1,
                "backupCount": 6,
                "encoding": "utf-8",
            },
        },
        "loggers": {
            "uvicorn": {"handlers": ["file"], "level": "INFO", "propagate": False},
            "uvicorn.error": {"handlers": ["file"], "level": "INFO", "propagate": False},
            "uvicorn.access": {"handlers": ["file"], "level": "WARNING", "propagate": False},
        },
        "root": {"handlers": ["file"], "level": "INFO"},
    }


def build_app() -> PortaDoMcp:
    """Build the ASGI app (FastMCP Streamable HTTP) wrapped in the ``PortaDoMcp`` door.

    Opens access from the LAN by turning off the DNS-rebinding protection (`Host`
    validation) of the StreamableHTTP transport. Without this, the SDK answers
    **421 "Invalid Host header"** for any `Host` != loopback (default of `mcp` 1.28.1) --
    which blocks clients arriving through `http://192.168.7.11:8078/mcp`. It is safe here because:
      - access is already stopped BEFORE by ``PortaDoMcp`` (Bearer token per client);
      - the network is internal and the clients are MCP apps (not browsers), so
        DNS rebinding (a browser attack against localhost) does not apply.
    `settings.transport_security` is read INSIDE `mcp.streamable_http_app()` (which creates
    the session manager lazily), so it is set BEFORE the call.
    """
    # Without SIS_MCP_TOKEN only the clients registered with `python -m seguranca` get in.
    mcp.settings.host = _HOST
    mcp.settings.port = _PORT
    mcp.settings.streamable_http_path = _PATH
    mcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    )
    # Alternativa mais restritiva — manter a proteção e só liberar a LAN (o "*" cobre
    # qualquer porta): TransportSecuritySettings(
    #     enable_dns_rebinding_protection=True,
    #     allowed_hosts=["192.168.7.11:*", "127.0.0.1:*", "localhost:*", "0.0.0.0:*"],
    #     allowed_origins=["http://192.168.7.11:*", "http://127.0.0.1:*",
    #                      "http://localhost:*", "http://0.0.0.0:*"])
    # Defensivo: garante que o session manager seja (re)criado com o setting acima.
    if getattr(mcp, "_session_manager", None) is not None:
        mcp._session_manager = None
    return PortaDoMcp(mcp.streamable_http_app(), _TOKEN)


if __name__ == "__main__":
    log_config = _build_log_config()
    # Ativa o arquivo já aqui: erros de subida (ex.: SIS_MCP_TOKEN ausente) caem no log.
    logging.config.dictConfig(log_config)
    try:
        app = build_app()
    except SystemExit as exc:
        logging.getLogger("mcp.serve_http").error("Falha ao subir MCP: %s", exc)
        raise
    uvicorn.run(app, host=_HOST, port=_PORT, log_config=log_config, access_log=False)
