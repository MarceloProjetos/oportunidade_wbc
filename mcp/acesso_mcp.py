"""The door of the MCP over HTTP (F1 of docs/PLANO_MIRA_AGENTE_11.md, 02/10/2026).

Until then the MCP took ONE static token: whoever held it called every tool, with no name, and
nothing recorded which tool was called (the access log was off). This ASGI middleware replaces
the plain token check of ``serve_http.py``:

- **Who** (rule 2): a key registered with ``python -m seguranca criar NOME --escopos mcp,...``
  identifies the client; the old ``SIS_MCP_TOKEN`` keeps working as "mcp-legado" (everything),
  so current clients do not break.
- **What** (rule 2): each tool needs one scope (``ESCOPO_DA_FERRAMENTA``); a tool outside the
  client's scopes is refused before it runs. An unknown tool needs ``admin`` — a tool added
  later is closed until someone gives it a scope here (and a test fails until then).
- **Agent rules** (rules 7 and 8): the off switch and business hours (``seguranca.agente``).
- **Record** (rule 4): every ``tools/call`` and ``resources/read`` — allowed or refused — goes
  to ``logs/auditoria/mcp-*.jsonl`` with the client, the person (``X-SIS-Usuario``, only from a
  client allowed to declare it) and the arguments.

The JSON-RPC body is read here to know the tool, then replayed to the app untouched.
"""
from __future__ import annotations

import hmac
import json
import sys
from pathlib import Path

# `seguranca/` lives at the repository root; the MCP runs with `mcp/` first on sys.path.
# Appended, never inserted: the root holds this `mcp/` folder, which must not shadow the SDK.
_RAIZ = str(Path(__file__).resolve().parents[1])
if _RAIZ not in sys.path:
    sys.path.append(_RAIZ)

from seguranca import agente as seguranca_agente  # noqa: E402
from seguranca import auditoria, credenciais  # noqa: E402
from seguranca.credenciais import Cliente  # noqa: E402

#: The scope each tool needs. Writes are the two that change data.
ESCOPO_DA_FERRAMENTA: dict[str, str] = {
    "verificar_saude": "leitura",
    "listar_sincronizacoes_os": "leitura",
    "listar_sincronizacoes_oportunidades": "leitura",
    "info_oportunidades": "leitura",
    "listar_pedidos_com_os": "leitura",
    "detalhe_pedido_os": "leitura",
    "estado_tarefa_wbc": "leitura",
    "estado_integracao_wbc": "leitura",
    "estado_windows_update": "leitura",
    "ultimos_erros": "leitura",
    "situacao_op": "leitura",
    "estado_orcamento_wbc": "leitura",
    "situacao_pedido": "leitura",
    "pedidos_bloqueados": "leitura",
    "panorama_pedidos": "leitura",
    "listar_colaboradores": "rh",
    "resumo_colaboradores": "rh",
    "sincronizar_pedido_os": "os:sincronizar",
    "forcar_carga_oportunidades": "oportunidades:carga",
}
ESCOPO_DO_RECURSO: dict[str, str] = {
    "sap-integracao://status": "leitura",
    "sap-integracao://historico-os": "leitura",
    "sap-integracao://colaboradores": "rh",
}
ESCRITAS = frozenset({"os:sincronizar", "oportunidades:carga"})
LIMITE_USUARIO = 80


def _cabecalho(scope: dict, nome: bytes) -> str:
    for chave, valor in scope.get("headers") or []:
        if chave.lower() == nome:
            return valor.decode("latin-1")
    return ""


def identificar(token: str, legado: str) -> Cliente | None:
    """The client of a Bearer token, or ``None`` (wrong, revoked, or without ``mcp``)."""
    if not token:
        return None
    if legado and hmac.compare_digest(token.encode("utf-8"), legado.encode("utf-8")):
        return Cliente("mcp-legado", frozenset({"admin"}))
    cliente = credenciais.identificar(token)
    return cliente if cliente and cliente.pode("mcp") else None


def _usuario(scope: dict, cliente: Cliente) -> str | None:
    if not cliente.declara_usuario:
        return None
    valor = _cabecalho(scope, b"x-sis-usuario").strip()
    if not valor or len(valor) > LIMITE_USUARIO or any(ord(c) < 32 or ord(c) == 127 for c in valor):
        return None
    return valor


def _chamadas(corpo: bytes) -> list[dict]:
    """The JSON-RPC messages of the body (one or a batch); anything unreadable → none."""
    try:
        dados = json.loads(corpo or b"null")
    except ValueError:
        return []
    mensagens = dados if isinstance(dados, list) else [dados]
    return [m for m in mensagens if isinstance(m, dict)]


def avaliar(cliente: Cliente, mensagem: dict) -> tuple[str | None, str | None, str | None]:
    """``(alvo, escopo, motivo da recusa)`` of one JSON-RPC message; alvo ``None`` = not audited."""
    metodo = mensagem.get("method")
    params = mensagem.get("params") if isinstance(mensagem.get("params"), dict) else {}
    if metodo == "tools/call":
        alvo = str(params.get("name") or "")
        escopo = ESCOPO_DA_FERRAMENTA.get(alvo, "admin")
    elif metodo == "resources/read":
        alvo = str(params.get("uri") or "")
        escopo = ESCOPO_DO_RECURSO.get(alvo, "admin")
    else:
        # initialize, tools/list, ping…: only the off switch stops them.
        motivo = seguranca_agente.recusa(cliente, escrita=False)
        return None, None, motivo
    if not cliente.pode(escopo):
        return alvo, escopo, f"A credencial '{cliente.nome}' não tem o escopo '{escopo}' para '{alvo}'."
    return alvo, escopo, seguranca_agente.recusa(cliente, escrita=escopo in ESCRITAS)


async def _responder(send, status: int, corpo: dict, extra_headers: list | None = None) -> None:
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), *(extra_headers or [])]})
    await send({"type": "http.response.body", "body": json.dumps(corpo, ensure_ascii=False).encode("utf-8")})


class PortaDoMcp:
    """ASGI: identify → read the JSON-RPC → scope + agent rules → audit → the app."""

    def __init__(self, app, token_legado: str = "") -> None:
        self.app = app
        self.legado = token_legado

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        autorizacao = _cabecalho(scope, b"authorization")
        token = autorizacao[len("Bearer "):] if autorizacao.startswith("Bearer ") else ""
        cliente = identificar(token, self.legado)
        ip = (scope.get("client") or ("-",))[0]
        if cliente is None:
            auditoria.registrar("mcp", cliente=None, ip=ip, resultado="recusado: token ausente ou inválido")
            await _responder(send, 401, {"error": "unauthorized"}, [(b"www-authenticate", b"Bearer")])
            return

        # Read the whole body, then replay it: the app must see exactly what was sent.
        partes, mais = [], True
        while mais:
            mensagem = await receive()
            if mensagem["type"] != "http.request":
                break
            partes.append(mensagem.get("body", b""))
            mais = mensagem.get("more_body", False)
        corpo = b"".join(partes)

        usuario = _usuario(scope, cliente)
        for chamada in _chamadas(corpo):
            alvo, escopo, motivo = avaliar(cliente, chamada)
            if alvo is not None or motivo:
                params = chamada.get("params") if isinstance(chamada.get("params"), dict) else {}
                auditoria.registrar(
                    "mcp", cliente=cliente.nome, usuario=usuario, ip=ip, metodo=chamada.get("method"),
                    alvo=alvo, escopo=escopo,
                    argumentos=json.dumps(params.get("arguments", {}), ensure_ascii=False, default=str),
                    resultado=f"recusado: {motivo}" if motivo else "permitido",
                )
            if motivo:
                await _responder(send, 403, {"jsonrpc": "2.0", "id": chamada.get("id"),
                                             "error": {"code": -32001, "message": motivo}})
                return

        entregue = False

        async def reenvia():
            nonlocal entregue
            if not entregue:
                entregue = True
                return {"type": "http.request", "body": corpo, "more_body": False}
            return await receive()

        await self.app(scope, reenvia, send)
