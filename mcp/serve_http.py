"""Entrypoint HTTP (remoto) da fachada MCP — Fase 3.

Serve o MESMO objeto FastMCP do ``mcp_server.py`` (as mesmas tools + resources da
Fase 1), porem via transporte **Streamable HTTP** (uvicorn), protegido por um TOKEN
estatico: ``Authorization: Bearer <SIS_MCP_TOKEN>``.

Topologia (Opcao B): roda como servico NA .11; os clientes MCP (Claude Desktop/Code)
apontam para ``http://<.11>:8078/mcp`` com o header. A stdio (``mcp_server.py``) segue
intacta para uso local.

Seguranca:
- ENTRADA: exige ``SIS_MCP_TOKEN`` (este arquivo). Ausente/errado -> 401.
- SAIDA: o ``mcp_server`` chama a API em ``SIS_API_BASE`` (=127.0.0.1:8077 na .11),
  entao a ``OS_API_KEY`` (``SIS_API_KEY``) NUNCA sai do servidor nem trafega na LAN.

Por que nao ``mcp.run("streamable-http")``: aquele caminho constroi o app Starlette e
sobe o uvicorn INTERNAMENTE, sem hook para injetar auth. Para exigir o token, montamos
o app ASGI (``mcp.streamable_http_app()``) + o uvicorn nos mesmos, com um middleware.

Config (via ``mcp/.env``, carregado pelo ``mcp_server``):
    SIS_MCP_TOKEN   segredo que o Bearer deve trazer (obrigatorio).
    SIS_MCP_HOST    bind (default 0.0.0.0).
    SIS_MCP_PORT    porta (default 8078).
    SIS_API_BASE    URL da API (na .11 = http://127.0.0.1:8077).
    SIS_API_KEY     a OS_API_KEY (injetada server-side no X-API-Key).

Rodar:  python serve_http.py     (ou via run_mcp.bat / servico NSSM OrcaView-MCP)
"""

from __future__ import annotations

import hmac
import logging
import logging.config
import os

import uvicorn
from mcp.server.transport_security import TransportSecuritySettings

# Importa o FastMCP ja montado (tools + resources) — isto tambem carrega o mcp/.env.
from mcp_server import mcp

_TOKEN = os.environ.get("SIS_MCP_TOKEN", "").strip()
_HOST = os.environ.get("SIS_MCP_HOST", "0.0.0.0")
_PORT = int(os.environ.get("SIS_MCP_PORT", "8078"))
_PATH = "/mcp"


def _build_log_config() -> dict:
    """Log em arquivo rotativo próprio (``logs/mcp_service.log``), 6 dias.

    O processo Python passa a ser o **único** dono do arquivo — mesmo padrão de
    ``api.py`` / ``scheduled_execution.py`` (``TimedRotatingFileHandler``,
    ``backupCount=6``, deleta sozinho o que passa de 6 dias). Por isso o serviço
    NSSM ``OrcaView-MCP`` NÃO deve mais redirecionar/rotacionar esse arquivo
    (senão o handle aberto do NSSM trava o rename da rotação à meia-noite).
    O log de acesso por-requisição fica desligado (``access_log=False``) para
    manter o arquivo enxuto.
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


class StaticBearerMiddleware:
    """ASGI: exige ``Authorization: Bearer <token>`` nas requisicoes HTTP.

    Encaminha escopos NAO-http (``lifespan``, ``websocket``) intactos — sem isso o
    ``StreamableHTTPSessionManager`` do FastMCP nem inicia (o lifespan do Starlette
    precisa disparar), e as requisicoes falhariam.
    """

    def __init__(self, app, token: str) -> None:
        self.app = app
        self._expected = f"Bearer {token}".encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        enviado = dict(scope.get("headers") or []).get(b"authorization", b"")
        # compare_digest = comparacao em tempo constante (evita timing attack)
        if not hmac.compare_digest(enviado, self._expected):
            await send({
                "type": "http.response.start",
                "status": 401,
                "headers": [(b"content-type", b"application/json"),
                            (b"www-authenticate", b"Bearer")],
            })
            await send({"type": "http.response.body", "body": b'{"error":"unauthorized"}'})
            return
        await self.app(scope, receive, send)


def build_app():
    """Monta o app ASGI (FastMCP Streamable HTTP) embrulhado no guard de token.

    Libera o acesso pela LAN desligando a proteção DNS-rebinding (validação de `Host`)
    do transporte StreamableHTTP. Sem isto, o SDK responde **421 "Invalid Host header"**
    para qualquer `Host` != loopback (default do `mcp` 1.28.1) — o que barra os clientes
    que chegam por `http://192.168.7.11:8078/mcp`. É seguro aqui porque:
      - o acesso já é barrado ANTES pelo `StaticBearerMiddleware` (token Bearer);
      - a rede é interna e os clientes são apps MCP (não navegadores), então o
        DNS-rebinding (ataque via browser contra localhost) não se aplica.
    `settings.transport_security` é lido DENTRO de `mcp.streamable_http_app()` (que cria
    o session manager de forma lazy), por isso é setado ANTES da chamada.
    """
    if not _TOKEN:
        raise SystemExit(
            "SIS_MCP_TOKEN ausente — defina no mcp/.env antes de subir o MCP HTTP."
        )
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
    return StaticBearerMiddleware(mcp.streamable_http_app(), _TOKEN)


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
