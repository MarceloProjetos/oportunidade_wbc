"""MCP tools of the agreed delivery date (PLANO_DATA_ENTREGA_COMBINADA, F4).

The facade is thin: what has logic here is the conversation around the HTTP call — the
preview that never posts, the body and the ASCII headers of the write, its own time budget,
and the timeout hint that says "check before repeating". The 8078 door's part (scope,
stamping who asked) is tested against ``acesso_mcp`` directly.
"""
from __future__ import annotations

import importlib.util
import json
import os

import httpx
import pytest

from seguranca import credenciais

pytest.importorskip("mcp.server.fastmcp")

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _carregar(nome: str, arquivo: str):
    spec = importlib.util.spec_from_file_location(nome, os.path.join(RAIZ, "mcp", arquivo))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def fachada():
    return _carregar("_fachada_mcp_entrega", "mcp_server.py")


@pytest.fixture(scope="module")
def acesso():
    return _carregar("_acesso_mcp_entrega", "acesso_mcp.py")


def test_solicitar_sem_confirmar_nao_faz_post(fachada, monkeypatch):
    lidos = []
    monkeypatch.setattr(fachada, "_get", lambda path, params=None: lidos.append(path) or {
        "ok": True, "data_vigente": "2026-11-20", "data_sap": "2026-11-20", "pendente": None})
    monkeypatch.setattr(fachada, "_post", lambda *a, **k: pytest.fail("gravou sem confirmar"))
    r = fachada.solicitar_data_entrega(84146, "2026-11-27", "quebra_maquina", solicitante="ana")
    assert r["preview"] is True and r["data_vigente"] == "2026-11-20"
    assert r["a_solicitar"] == {"data_nova": "2026-11-27", "motivo": "quebra_maquina",
                                "observacao": None, "solicitante": "ana"}
    assert lidos == ["/pedidos/84146/data-entrega"]


def test_post_do_mcp_manda_json_e_cabecalhos_ascii(fachada, monkeypatch):
    enviado = {}

    def _post(url, params=None, json=None, headers=None, timeout=None, trust_env=None):
        enviado.update(url=url, json=json, headers=headers, timeout=timeout)
        return httpx.Response(201, json={"ok": True})

    monkeypatch.setattr(fachada.httpx, "post", _post)
    fachada.solicitar_data_entrega(84146, "2026-11-27", "quebra_maquina", observacao="Prensa 3",
                                   solicitante="ignorado", confirmar=True,
                                   pedido_por="pcp-mcp", em_nome_de="João Lima")
    assert enviado["url"].endswith("/pedidos/84146/data-entrega/solicitacoes")
    assert enviado["json"] == {"data_nova": "2026-11-27", "motivo": "quebra_maquina",
                               "observacao": "Prensa 3", "solicitante": "João Lima"}
    assert enviado["headers"]["X-SIS-Pedido-Por"] == "pcp-mcp"
    assert enviado["headers"]["X-SIS-Usuario"] == "Joao Lima"      # header must be ASCII
    assert enviado["timeout"] == 180.0


def test_timeout_manda_conferir_data_entrega_pedido(fachada, monkeypatch):
    def _lento(*_a, **_k):
        raise httpx.ReadTimeout("lento")

    monkeypatch.setattr(fachada.httpx, "post", _lento)
    r = fachada.solicitar_data_entrega(84146, "2026-11-27", "quebra_maquina", confirmar=True)
    assert r["ok"] is False and "data_entrega_pedido" in r["dica"]


def test_leituras_chamam_as_rotas_certas(fachada, monkeypatch):
    chamadas = []
    monkeypatch.setattr(fachada, "_get", lambda path, params=None: chamadas.append((path, params)) or {})
    fachada.data_entrega_pedido(84146)
    fachada.solicitacoes_data_entrega("decididas", 3)
    assert chamadas == [("/pedidos/84146/data-entrega", None),
                        ("/pedidos/data-entrega/solicitacoes", {"situacao": "decididas", "dias": 3})]


def test_porta_recusa_sem_escopo_e_carimba_quem_pediu(acesso):
    so_leitura = credenciais.Cliente("x", frozenset({"mcp", "leitura"}))
    pcp = credenciais.Cliente("pcp-mcp", frozenset({"mcp", "entrega:ler", "entrega:solicitar"}))

    def _msg(nome, **args):
        return {"method": "tools/call", "params": {"name": nome, "arguments": args}}

    for nome in ("data_entrega_pedido", "solicitacoes_data_entrega", "solicitar_data_entrega"):
        assert acesso.avaliar(so_leitura, _msg(nome))[2] is not None, nome
        assert acesso.avaliar(pcp, _msg(nome))[2] is None, nome

    corpo = json.dumps(_msg("solicitar_data_entrega", pedido=1, pedido_por="forjado",
                            em_nome_de="forjado")).encode()
    carimbado = json.loads(acesso.carimbar(corpo, pcp, "Ana Souza"))
    args = carimbado["params"]["arguments"]
    assert (args["pedido_por"], args["em_nome_de"]) == ("pcp-mcp", "Ana Souza")


def test_escrita_de_data_conta_como_escrita_para_as_regras_de_agente(acesso):
    assert "entrega:solicitar" in acesso.ESCRITAS
    assert "entrega:solicitar" in credenciais.PROIBIDOS_AO_AGENTE
