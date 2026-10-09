"""The door of the MCP over HTTP (F1 of PLANO_MIRA_AGENTE_11.md (removed 2026-10-06), 02/10/2026).

Loaded by path (``mcp/`` is not a package and the name ``mcp`` belongs to the SDK); it needs
no SDK — the middleware is plain ASGI, tested here with a stand-in app.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import re
from pathlib import Path

import pytest

from seguranca import agente, auditoria, credenciais

RAIZ = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("_acesso_mcp", RAIZ / "mcp" / "acesso_mcp.py")
acesso = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acesso)

LEGADO = "token-antigo-do-mcp"


class _App:
    """Stand-in for the FastMCP app: records the body it received and answers 200."""

    def __init__(self):
        self.recebido = None
        self.scope = None

    async def __call__(self, scope, receive, send):
        self.scope = scope
        mensagem = await receive()
        self.recebido = mensagem.get("body")
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b'{"ok":true}'})


def _chamar(porta, token: str | None, corpo, usuario: str | None = None) -> tuple[int, dict, _App]:
    headers = [(b"content-type", b"application/json")]
    if token is not None:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    if usuario:
        headers.append((b"x-sis-usuario", usuario.encode()))
    bruto = json.dumps(corpo).encode()
    pedacos = [bruto[:10], bruto[10:]]          # a body in two chunks, as uvicorn may deliver
    enviados, respostas = list(pedacos), []

    async def receive():
        corpo_parcial = enviados.pop(0)
        return {"type": "http.request", "body": corpo_parcial, "more_body": bool(enviados)}

    async def send(mensagem):
        respostas.append(mensagem)

    scope = {"type": "http", "method": "POST", "path": "/mcp", "headers": headers, "client": ("192.168.0.90", 5000)}
    asyncio.run(porta(scope, receive, send))
    status = respostas[0]["status"]
    corpo_resposta = json.loads(respostas[1]["body"] or b"{}")
    return status, corpo_resposta, porta.app


def _ferramenta(nome: str, **argumentos) -> dict:
    return {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": nome, "arguments": argumentos}}


@pytest.fixture
def porta():
    return acesso.PortaDoMcp(_App(), token_legado=LEGADO)


def test_sem_token_ou_token_errado_e_401(porta):
    for token in (None, "", "inventado"):
        status, _, app = _chamar(porta, token, _ferramenta("verificar_saude"))
        assert status == 401 and app.recebido is None


def test_token_antigo_continua_valendo_para_tudo(porta):
    """Current MCP clients (SIS_MCP_TOKEN) do not break."""
    status, _, app = _chamar(porta, LEGADO, _ferramenta("listar_colaboradores"))
    assert status == 200 and json.loads(app.recebido)["params"]["name"] == "listar_colaboradores"


def test_cliente_sem_escopo_mcp_nao_entra(porta):
    chave = credenciais.criar("so-api", ["leitura"])
    assert _chamar(porta, chave, _ferramenta("verificar_saude"))[0] == 401


def test_ferramenta_fora_do_escopo_e_recusada_antes_de_rodar(porta):
    chave = credenciais.criar("mira-agente", ["mcp", "leitura"], agente=True)
    status, corpo, app = _chamar(porta, chave, _ferramenta("listar_colaboradores", empresa="Altamira"))
    assert status == 403 and app.recebido is None
    assert corpo["error"]["message"] == "A credencial 'mira-agente' não tem o escopo 'rh' para 'listar_colaboradores'."
    assert corpo["id"] == 7

    status, _, app = _chamar(porta, chave, _ferramenta("sincronizar_pedido_os", nped=84080, confirmar=True))
    assert status == 403 and app.recebido is None


def test_ferramenta_permitida_passa_com_o_corpo_intacto(porta):
    chave = credenciais.criar("mira-agente", ["mcp", "leitura"], agente=True)
    corpo = _ferramenta("situacao_pedido", pedido=84453)
    status, _, app = _chamar(porta, chave, corpo)
    assert status == 200 and json.loads(app.recebido) == corpo


def test_ferramenta_desconhecida_exige_admin(porta):
    chave = credenciais.criar("mira-agente", ["mcp", "leitura"], agente=True)
    assert _chamar(porta, chave, _ferramenta("ferramenta_nova_sem_escopo"))[0] == 403


def test_agente_desligado_nem_lista_ferramentas(porta):
    chave = credenciais.criar("mira-agente", ["mcp", "leitura"], agente=True)
    agente.desligar()
    status, corpo, _ = _chamar(porta, chave, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert status == 403 and "desligado" in corpo["error"]["message"]


def test_agente_so_grava_no_expediente(porta, monkeypatch):
    """Asking for an approval is the agent's write: outside business hours it only reads."""
    chave = credenciais.criar("mira-agente", ["mcp", "leitura", "os:sincronizar", "servico:reiniciar"], agente=True)
    monkeypatch.setattr(agente, "no_expediente", lambda agora=None: False)
    assert _chamar(porta, chave, _ferramenta("situacao_pedido", pedido=1))[0] == 200
    for nome, args in (("pedir_sincronizar_os", {"nped": 84080}), ("pedir_reiniciar_servico", {"servico": "x"})):
        status, corpo, _ = _chamar(porta, chave, _ferramenta(nome, **args))
        assert status == 403 and "7h às 19h" in corpo["error"]["message"], nome


def test_confirmar_do_modelo_nao_executa_para_o_agente(porta, monkeypatch):
    """F3, rule 1: for an agent the direct writes only preview; people keep them (rule 0)."""
    monkeypatch.setattr(agente, "no_expediente", lambda agora=None: True)
    robo = credenciais.criar("mira-agente", ["mcp", "os:sincronizar", "oportunidades:carga"], agente=True)
    status, corpo, _ = _chamar(porta, robo, _ferramenta("sincronizar_pedido_os", nped=84080, confirmar=True))
    assert status == 403 and "pedir_sincronizar_os" in corpo["error"]["message"]
    status, corpo, _ = _chamar(porta, robo, _ferramenta("forcar_carga_oportunidades", confirmar=True))
    assert status == 403 and "pedir_forcar_carga" in corpo["error"]["message"]
    assert _chamar(porta, robo, _ferramenta("sincronizar_pedido_os", nped=84080))[0] == 200   # preview
    pessoa = credenciais.criar("mcp-marcelo", ["mcp", "os:sincronizar"])
    assert _chamar(porta, pessoa, _ferramenta("sincronizar_pedido_os", nped=84080, confirmar=True))[0] == 200


def test_porta_carimba_quem_pediu_e_o_modelo_nao_finge(porta, monkeypatch):
    monkeypatch.setattr(agente, "no_expediente", lambda agora=None: True)
    chave = credenciais.criar("mira-agente", ["mcp", "pedidos_wbc"], agente=True, declara_usuario=True)
    status, _, app = _chamar(porta, chave, _ferramenta("pedir_processar_pedido", pedido=84453,
                                                       pedido_por="admin", em_nome_de="Diretor"),
                             usuario="Ana Souza")
    assert status == 200
    argumentos = json.loads(app.recebido)["params"]["arguments"]
    assert argumentos == {"pedido": 84453, "pedido_por": "mira-agente", "em_nome_de": "Ana Souza"}
    tamanho = [v for k, v in app.scope["headers"] if k == b"content-length"]
    assert tamanho == [str(len(app.recebido)).encode()]


def test_carimbo_nao_mexe_no_que_nao_e_pedido():
    corpo = json.dumps(_ferramenta("situacao_pedido", pedido=1)).encode()
    assert acesso.carimbar(corpo, credenciais.Cliente("x"), None) is corpo


def test_auditoria_registra_cada_ferramenta_com_quem_pediu(porta):
    chave = credenciais.criar("mira-agente", ["mcp", "leitura"], agente=True, declara_usuario=True)
    _chamar(porta, chave, _ferramenta("situacao_pedido", pedido=84453), usuario="Ana Souza")
    _chamar(porta, chave, _ferramenta("listar_colaboradores"))
    _chamar(porta, chave, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})      # not a call: not audited
    _chamar(porta, "inventado", _ferramenta("verificar_saude"))

    linhas = auditoria.ler("mcp")
    assert [(l["cliente"], l.get("alvo"), l["resultado"][:9]) for l in linhas] == [
        ("mira-agente", "situacao_pedido", "permitido"),
        ("mira-agente", "listar_colaboradores", "recusado:"),
        (None, None, "recusado:"),
    ]
    assert linhas[0]["usuario"] == "Ana Souza" and json.loads(linhas[0]["argumentos"]) == {"pedido": 84453}
    assert linhas[0]["ip"] == "192.168.0.90"
    assert chave not in json.dumps(linhas)


def test_token_errado_repetido_grava_uma_linha_por_minuto(porta):
    for _ in range(20):
        assert _chamar(porta, "inventado", _ferramenta("verificar_saude"))[0] == 401
    assert len(auditoria.ler("mcp")) == 1


def test_usuario_com_controle_c1_e_descartado(porta):
    """``x-sis-usuario`` decodes as latin-1: U+0085 would split the audit line."""
    chave = credenciais.criar("orcaview-90", ["mcp", "leitura"], declara_usuario=True)
    _chamar(porta, chave, _ferramenta("verificar_saude"), usuario="fulano\x85forjado")
    assert auditoria.ler("mcp")[0]["usuario"] is None


def test_toda_ferramenta_do_servidor_tem_um_escopo():
    """A tool added to mcp_server.py without a scope here stays closed (admin) — and this fails."""
    fonte = (RAIZ / "mcp" / "mcp_server.py").read_text(encoding="utf-8")
    ferramentas = set(re.findall(r"@mcp\.tool\([^)]*\)\s*\ndef (\w+)", fonte))
    assert len(ferramentas) == 32
    assert ferramentas == set(acesso.ESCOPO_DA_FERRAMENTA)
    assert set(acesso.ESCOPO_DA_FERRAMENTA.values()) <= set(credenciais.ESCOPOS)
    recursos = set(re.findall(r'@mcp\.resource\("([^"]+)"', fonte))
    assert recursos == set(acesso.ESCOPO_DO_RECURSO)
