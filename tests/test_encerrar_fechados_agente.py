"""The agent's side of PLANO_ENCERRAR_OPS_PEDIDOS_FECHADOS (F3, 28/10/2026).

The Mira may READ the list and ASK for a closing; a person approves. What is protected: the
request is validated before any call (list, cap of 20, cost), the preview comes from the CP's
own plan, and the run refuses when the orders or their open OPs changed since the approval.
"""
from __future__ import annotations

import pytest

from operacao import acoes_agente as a

PLANO = {"token": "t1", "resumo": {"pedidos": 2, "ops_abertas": 17, "custo": "0.01", "ativar_inativos": False},
         "itens": [{"pedido": 84396, "ops_abertas": 9}, {"pedido": 84397, "ops_abertas": 8}]}
CTX = a.Contexto(pessoa="Ana PCP", codigo="1234", pedido_por="mira")


def test_valida_lista_teto_e_custo():
    v = a._validar_fechados({"pedidos": [84397, "84396", 84397]})
    assert v == {"pedidos": [84396, 84397], "custo": None, "ativar_inativos": False}
    assert a._validar_fechados({"pedidos": [1], "custo": "0,05"})["custo"] == "0.05"
    for ruim in ({}, {"pedidos": []}, {"pedidos": ["84a"]}, {"pedidos": list(range(1, 22))},
                 {"pedidos": [1], "custo": "0"}, {"pedidos": [1], "custo": "abc"}):
        with pytest.raises(a.AcaoInvalida):
            a._validar_fechados(ruim)
    # Only a real True activates items: "sim" from a model is not a decision.
    assert a._validar_fechados({"pedidos": [1], "ativar_inativos": "sim"})["ativar_inativos"] is False


def test_previa_vem_do_plano_do_cp(monkeypatch):
    chamadas = []

    def chamar(base, metodo, caminho, **kw):
        chamadas.append((metodo, caminho, kw.get("corpo")))
        return 200, {"ok": True, "plano": PLANO}

    monkeypatch.setattr(a, "_chamar", chamar)
    previa = a._previa_fechados(a._validar_fechados({"pedidos": [84396, 84397]}))
    assert previa["itens"] == [[84396, 9], [84397, 8]]
    assert "IRREVERSÍVEL" in previa["texto"] and "0,01" in previa["texto"]
    assert chamadas[0][1] == "/api/manutencao-op/pedidos-fechados/encerrar/conferir"


def test_execucao_recusa_se_a_lista_mudou(monkeypatch):
    mudou = {**PLANO, "itens": [{"pedido": 84396, "ops_abertas": 9}]}
    monkeypatch.setattr(a, "_chamar", lambda *_a, **_k: (200, {"ok": True, "plano": mudou}))
    ok, corpo = a._executar_fechados({"pedidos": [84396, 84397], "custo": None, "ativar_inativos": False},
                                     {"itens": [[84396, 9], [84397, 8]]}, CTX)
    assert not ok and "mudaram" in corpo["motivo"]


def test_execucao_manda_token_e_quem_aprovou(monkeypatch):
    chamadas = []

    def chamar(base, metodo, caminho, **kw):
        chamadas.append((caminho, kw.get("corpo")))
        if caminho.endswith("/conferir"):
            return 200, {"ok": True, "plano": PLANO}
        return 202, {"ok": True, "execucao": {"id": "abc"}}

    monkeypatch.setattr(a, "_chamar", chamar)
    ok, corpo = a._executar_fechados({"pedidos": [84396, 84397], "custo": None, "ativar_inativos": False},
                                     {"itens": [[84396, 9], [84397, 8]]}, CTX)
    assert ok and corpo["http"] == 202
    caminho, enviado = chamadas[-1]
    assert caminho.endswith("/encerrar/executar") and enviado["token"] == "t1"
    assert enviado["solicitante"].startswith("Ana PCP (aprovou 1234")


def test_alvo_e_os_pedidos_ordenados():
    acao = a.CATALOGO["encerrar_ops_pedidos_fechados"]
    assert acao.alvo(a._validar_fechados({"pedidos": [84397, 84396]})) == "84396,84397"


# ---------------------------------------------------------------------------
# The 8077 read route (the MCP's door to the list)
# ---------------------------------------------------------------------------
def test_rota_8077_le_do_cp_e_exige_chave(monkeypatch):
    pytest.importorskip("flask")
    import api as apimod
    from config import reset_settings

    monkeypatch.setenv("OS_API_KEY", "chave-mestra-de-teste")
    reset_settings()
    apimod._rate_limiter.reset()
    pedidos_vistos = []

    def ler(incluir):
        pedidos_vistos.append(incluir)
        return 200, {"ok": True, "total_pedidos": 1, "pedidos": [{"pedido": 84396}]}

    monkeypatch.setattr(apimod.acoes_agente, "pedidos_fechados_com_op", ler)
    c = apimod.app.test_client()
    assert c.get("/manutencao-op/pedidos-fechados").status_code == 401
    r = c.get("/manutencao-op/pedidos-fechados?incluir_atrasados=1", headers={"X-API-Key": "chave-mestra-de-teste"})
    assert r.status_code == 200 and r.get_json()["pedidos"] == [{"pedido": 84396}]
    assert pedidos_vistos == [True]

    monkeypatch.setattr(apimod.acoes_agente, "pedidos_fechados_com_op", lambda _i: (0, {"erro": "sem resposta"}))
    assert c.get("/manutencao-op/pedidos-fechados", headers={"X-API-Key": "chave-mestra-de-teste"}).status_code == 503
