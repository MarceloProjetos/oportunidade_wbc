"""Vendas, Produção, Entrega and Sinal in the Pedidos WBC API list (PLANO_SINAL_E_ETAPAS_NA_API, F3).

The stages come from the API 8077 of the same machine (the Situação dos Pedidos' reading and
rule): one call for the view's cut, one per listed order missing from it (a closed order).
"""
from __future__ import annotations

import httpx

from controleproducao.modules.pedidos_wbc import acoes

CORTE = {
    "ok": True, "gerado_em": "2026-11-11T12:04:00-03:00",
    "pedidos": [
        {"doc_num": 84536, "financeiro": "Liberado", "producao": "Bloqueado", "entrega": "Bloqueado", "sinal": True,
         "card_name": "X", "prazo_entrega": "07/12 A 11/12"},
        {"doc_num": 84600, "financeiro": "Liberado", "producao": "Liberado", "entrega": "Liberado", "sinal": False},
    ],
}
FECHADO = {"ok": True, "gerado_em": "2026-11-11T12:05:00-03:00",
           "pedido": {"doc_num": 84537, "financeiro": "Entrega futura", "producao": "Entrega futura",
                      "entrega": "Entrega futura", "sinal": False, "valor_total": 1.0}}


def _api(monkeypatch, responder):
    chamadas: list[str] = []
    real = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        chamadas.append(request.url.path)
        assert request.headers.get("X-API-Key") is not None
        return responder(request)

    monkeypatch.setattr(acoes.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    return chamadas


def test_etapas_vem_do_corte_e_o_fechado_pela_rota_do_pedido(monkeypatch):
    def responder(request):
        if request.url.path == "/pedidos/situacao":
            assert request.url.params["status"] == "todos"
            return httpx.Response(200, json=CORTE)
        if request.url.path == "/pedidos/84537/situacao":
            return httpx.Response(200, json=FECHADO)
        return httpx.Response(404, json={"ok": False, "motivo": "pedido_nao_encontrado"})

    chamadas = _api(monkeypatch, responder)
    etapas = acoes.etapas_dos_pedidos([84536, 84537, 99999])
    assert etapas.por_pedido == {
        84536: {"financeiro": "Liberado", "producao": "Bloqueado", "entrega": "Bloqueado", "sinal": True},
        84537: {"financeiro": "Entrega futura", "producao": "Entrega futura", "entrega": "Entrega futura",
                "sinal": False},
    }
    assert etapas.gerado_em == "2026-11-11T12:04:00-03:00"
    # One call for the cut, one per order missing from it; 84600 (not on the page) is not asked.
    assert chamadas == ["/pedidos/situacao", "/pedidos/84537/situacao", "/pedidos/99999/situacao"]


def test_8077_fora_custa_as_colunas_e_nao_a_lista(monkeypatch):
    def responder(_request):
        raise httpx.ConnectError("recusada")

    _api(monkeypatch, responder)
    etapas = acoes.etapas_dos_pedidos([84536])
    assert etapas.por_pedido == {} and etapas.gerado_em is None


def test_lista_da_api_traz_as_etapas_de_cada_pedido():
    from controleproducao.modules.pedidos_wbc import api_router
    from controleproducao.modules.pedidos_wbc.schemas import PedidoParaIntegrar

    p = PedidoParaIntegrar(opp_id=15875, doc_num=84584, cod_cliente="C006321", nome_cliente="X", total_pedido=1.0,
                           data_lancamento="2026-11-09", orc_num_masc="00126063", entrega_futura=True)
    etapas = {"financeiro": "Entrega futura", "producao": "Entrega futura", "entrega": "Entrega futura", "sinal": False}
    linha = api_router._pedido(p, etapas)
    assert {c: linha[c] for c in acoes.CAMPOS_ETAPA} == etapas
    assert {c: api_router._pedido(p)[c] for c in acoes.CAMPOS_ETAPA} == dict.fromkeys(acoes.CAMPOS_ETAPA)
