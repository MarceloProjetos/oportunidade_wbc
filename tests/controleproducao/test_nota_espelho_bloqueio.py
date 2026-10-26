"""Processar refused for a Nota Espelho order (Marcelo, 26/10/2026).

`ORDR.U_U_INO_NotaEspelho = 'Y'` marks a mirror order that is not confirmed yet: it must never
get OPs. The barrier sits in the plan (screen and API, one text) and again at run time (covers
the CLI and a plan that got old). "Forçar" does not skip it; Reprocessar stays open.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from controleproducao.core.recusa import HTTP_DA_RECUSA, Recusa
from controleproducao.modules.pedidos_wbc import acoes, queries, service
from tests.controleproducao.test_api_pedidos_wbc import (  # noqa: F401 - pytest fixtures
    _conferir,
    _limpa_registros,
    _texto,
    ambiente,
    c,
    lista,
)


def _espelho(pedido):
    return pedido.model_copy(update={"nota_espelho": True})


class TestPlano:
    def test_processar_recusa_pedido_espelho(self, lista) -> None:  # noqa: F811
        with pytest.raises(Recusa) as recusa:
            acoes.monta_plano([_espelho(lista[0]), *lista[1:]], [str(lista[0].opp_id)], False)

        assert recusa.value.tipo == "nota_espelho" and HTTP_DA_RECUSA["nota_espelho"] == 409
        assert f"O pedido {lista[0].doc_num}" in recusa.value.mensagem
        assert "Nota Espelho" in recusa.value.mensagem
        assert recusa.value.dados == {"nota_espelho": [lista[0].doc_num]}

    def test_forcar_nao_pula_a_barreira(self, lista) -> None:  # noqa: F811
        with pytest.raises(Recusa):
            acoes.monta_plano([_espelho(lista[0])], [str(lista[0].opp_id)], False, force=True)

    def test_lote_misto_e_recusado_inteiro(self, lista) -> None:  # noqa: F811
        with pytest.raises(Recusa) as recusa:
            acoes.monta_plano([_espelho(lista[0]), *lista[1:]],
                              [str(lista[0].opp_id), str(lista[1].opp_id)], False)
        assert str(lista[1].doc_num) not in recusa.value.mensagem

    def test_reprocessar_nao_e_barrado(self, lista) -> None:  # noqa: F811
        """Reprocessar only cancels planned OPs; it never creates one."""
        plano = acoes.monta_plano([_espelho(lista[0])], [str(lista[0].opp_id)], integrados=True)
        assert len(plano.itens) == 1

    def test_pedido_sem_espelho_segue(self, lista) -> None:  # noqa: F811
        plano = acoes.monta_plano([_espelho(lista[0]), *lista[1:]], [str(lista[1].opp_id)], False)
        assert [i["Pedido"] for i in plano.itens] == [lista[1].doc_num]


def test_api_e_tela_recusam_com_o_mesmo_texto(c, ambiente) -> None:  # noqa: F811
    ambiente[0] = _espelho(ambiente[0])

    resposta = _conferir(c, [ambiente[0].opp_id])
    tela = _texto(
        c.post("/pedidos-wbc/processar/conferir", data={"opp_ids": [str(ambiente[0].opp_id)]}).text
    )

    assert resposta.status_code == 409
    corpo = resposta.json()
    assert corpo["tipo"] == "nota_espelho"
    assert corpo["motivo"] in tela
    assert corpo["nota_espelho"] == [ambiente[0].doc_num]
    assert "Pedido com Nota Espelho não vai para a produção" in tela


class TestNaExecucao:
    def test_recusa_na_hora_mesmo_forcado_e_nao_toca_no_sap(self, monkeypatch) -> None:
        monkeypatch.setattr(service, "_busca_orcamentos_wbc", AsyncMock(return_value=[]))
        monkeypatch.setattr(
            service, "_get_id_orcamentos_pedido",
            AsyncMock(return_value={"DocEntry": 20700, "CardCode": "C007583", "DocNum": 84534,
                                    "NotaEspelho": "Y"}),
        )
        nao_grava = AsyncMock(side_effect=AssertionError("não devia gravar"))
        monkeypatch.setattr(service, "preenche_tabela", nao_grava)
        monkeypatch.setattr(service, "preenche_log", nao_grava)
        leitor = MagicMock()
        leitor.fetch_all = MagicMock(return_value=[])

        resultado = asyncio.run(
            service.processar_pedidos_novos(None, None, leitor, ["00125960"], force=True)
        )

        assert resultado["processados"] == []
        erro = resultado["com_erro"][0]
        assert erro["nota_espelho"] is True
        assert "O pedido 84534 (WBC 00125960) está marcado como Nota Espelho" in erro["motivo"]

    def test_consulta_do_pedido_traz_o_campo(self) -> None:
        assert '"U_U_INO_NotaEspelho"' in queries.GET_ID_ORCAMENTOS_PEDIDO
        assert '"NotaEspelho"' in queries.GET_ID_ORCAMENTOS_PEDIDO
