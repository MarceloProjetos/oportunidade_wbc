"""Processar refused while a cancelled order of the same quote has live OPs (F3).

docs/PLANO_TROCA_PN_COM_OP.md: on 20/10/2026 the partner swap cancelled 84505 and 84506 with
their OPs still planned, and Processar on the replacements 84507 and 84510 planned the same
items again. The barrier sits in the plan (screen and API, one text) and again at run time
(covers the CLI and a plan that got old). "Forçar" does not skip it.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from controleproducao.core.recusa import HTTP_DA_RECUSA, Recusa
from controleproducao.modules.pedidos_wbc import acoes, service
from tests.controleproducao.test_api_pedidos_wbc import (  # noqa: F401 - pytest fixtures
    _conferir,
    _limpa_registros,
    _texto,
    ambiente,
    c,
    lista,
)

ORFAS = [
    {"pedido": 84506, "op": 159019, "status": "P"},
    {"pedido": 84506, "op": 159020, "status": "P"},
    {"pedido": 84506, "op": 159099, "status": "R"},
]


def _com_orfas(pedido, ops=ORFAS):
    return pedido.model_copy(update={"ops_orfas": list(ops)})


class TestPlano:
    def test_processar_recusa_e_diz_o_que_fazer(self, lista) -> None:  # noqa: F811
        elegiveis = [_com_orfas(lista[0]), *lista[1:]]

        with pytest.raises(Recusa) as recusa:
            acoes.monta_plano(elegiveis, [str(lista[0].opp_id)], integrados=False)

        assert recusa.value.tipo == "ops_orfas" and HTTP_DA_RECUSA["ops_orfas"] == 409
        texto = recusa.value.mensagem
        assert f"O pedido {lista[0].doc_num}" in texto
        assert "2 planejada(s) e 1 liberada(s) (159019, 159020, 159099)" in texto
        assert "cancelar-ops 84506 --manter-vinculos" in texto
        assert [d["OP"] for d in recusa.value.detalhes] == [159019, 159020, 159099]

    def test_forcar_nao_pula_a_barreira(self, lista) -> None:  # noqa: F811
        with pytest.raises(Recusa):
            acoes.monta_plano([_com_orfas(lista[0])], [str(lista[0].opp_id)], False, force=True)

    def test_reprocessar_nao_e_barrado(self, lista) -> None:  # noqa: F811
        """Reprocessar cancels OPs; it does not plan new ones."""
        plano = acoes.monta_plano([_com_orfas(lista[0])], [str(lista[0].opp_id)], integrados=True)
        assert len(plano.itens) == 1

    def test_lote_misto_e_recusado_inteiro(self, lista) -> None:  # noqa: F811
        """Same as `fora_da_lista`: the plan is all or nothing (API_PEDIDOS_WBC.md says so)."""
        elegiveis = [_com_orfas(lista[0]), *lista[1:]]
        with pytest.raises(Recusa) as recusa:
            acoes.monta_plano(
                elegiveis, [str(lista[0].opp_id), str(lista[1].opp_id)], integrados=False
            )
        assert recusa.value.tipo == "ops_orfas"
        assert str(lista[1].doc_num) not in recusa.value.mensagem

    def test_so_planejada_nao_manda_replanejar(self, lista) -> None:  # noqa: F811
        so_planejadas = [o for o in ORFAS if o["status"] == "P"]
        with pytest.raises(Recusa) as recusa:
            acoes.monta_plano(
                [_com_orfas(lista[0], so_planejadas)], [str(lista[0].opp_id)], integrados=False
            )
        assert "Replanejar" not in recusa.value.mensagem

    def test_so_o_pedido_selecionado_conta(self, lista) -> None:  # noqa: F811
        elegiveis = [_com_orfas(lista[0]), *lista[1:]]
        plano = acoes.monta_plano(elegiveis, [str(lista[1].opp_id)], integrados=False)
        assert [i["Pedido"] for i in plano.itens] == [lista[1].doc_num]


def test_api_e_tela_recusam_com_o_mesmo_texto(c, ambiente) -> None:  # noqa: F811
    ambiente[0] = _com_orfas(ambiente[0])

    resposta = _conferir(c, [ambiente[0].opp_id])
    tela = _texto(
        c.post("/pedidos-wbc/processar/conferir", data={"opp_ids": [str(ambiente[0].opp_id)]}).text
    )

    assert resposta.status_code == 409
    corpo = resposta.json()
    assert corpo["tipo"] == "ops_orfas"
    assert corpo["motivo"] in tela
    assert corpo["ops_orfas"][str(ambiente[0].doc_num)][0]["op"] == 159019
    # The screen also shows the title and the full table of OPs.
    assert "OP viva em pedido cancelado do mesmo orçamento" in tela
    assert "159099 Liberada" in tela


class TestNaExecucao:
    def _leitor(self, linhas):
        leitor = MagicMock()
        leitor.fetch_all = MagicMock(side_effect=lambda sql, *a: list(linhas) if "OWOR" in sql else [])
        return leitor

    def test_recusa_na_hora_mesmo_forcado_e_nao_toca_no_sap(self, monkeypatch) -> None:
        monkeypatch.setattr(service, "_busca_orcamentos_wbc", AsyncMock(return_value=[]))
        monkeypatch.setattr(
            service, "_get_id_orcamentos_pedido",
            AsyncMock(return_value={"DocEntry": 20677, "CardCode": "C012060", "DocNum": 84510}),
        )
        nao_grava = AsyncMock(side_effect=AssertionError("não devia gravar"))
        monkeypatch.setattr(service, "preenche_tabela", nao_grava)
        monkeypatch.setattr(service, "preenche_log", nao_grava)  # @INO_LOG is a write too
        leitor = self._leitor(
            [{"Orcamento": "00124837", "PedidoCancelado": 84506, "OP": 159019, "Status": "P"}]
        )

        resultado = asyncio.run(
            service.processar_pedidos_novos(None, None, leitor, ["00124837"], force=True)
        )

        assert resultado["processados"] == []
        erro = resultado["com_erro"][0]
        assert "O pedido 84510 (WBC 00124837) não pode ser processado" in erro["motivo"]
        assert erro["ops_orfas"] == [{"pedido": 84506, "op": 159019, "status": "P"}]

    def test_consulta_so_le_e_filtra_cancelado_e_status(self) -> None:
        leitor = self._leitor([])
        asyncio.run(service.ops_vivas_de_pedidos_cancelados(leitor, ["00124837", "00125690"]))

        sql, params = leitor.fetch_all.call_args.args
        assert "\"CANCELED\" = 'Y'" in sql and "\"Status\" IN ('P', 'R')" in sql
        assert list(params) == ["00124837", "00125690"]

    def test_integrados_nem_consulta_a_owor(self) -> None:
        linha = {
            "Num Oportunidade": 15158, "Nº Pedido": 84510, "Cod.Cliente": "C012060",
            "Nome Cliente": "X", "Total Pedido": 1.0, "Data Lancamento": "2026-10-20",
            "Nº Oportunidade": "00124837",
        }
        leitor = MagicMock()
        leitor.fetch_all = MagicMock(return_value=[linha])

        asyncio.run(service.buscar_pedidos_para_integrar(leitor, integrados=True))

        assert all("OWOR" not in chamada.args[0] for chamada in leitor.fetch_all.call_args_list)

    def test_lista_vazia_nao_consulta(self) -> None:
        leitor = self._leitor([])
        assert asyncio.run(service.ops_vivas_de_pedidos_cancelados(leitor, [])) == {}
        leitor.fetch_all.assert_not_called()


def test_mensagem_corta_listas_longas_e_cobre_dois_pedidos() -> None:
    ops = [{"pedido": 84505, "op": 158845 + i, "status": "P"} for i in range(10)]
    ops.append({"pedido": 84506, "op": 159019, "status": "R"})

    texto = service.mensagem_ops_orfas(84507, "00125690", ops)

    assert "158852 … (+2)" in texto
    assert "o pedido cancelado 84506 ainda tem 1 liberada(s) (159019)" in texto
    assert "cancelar-ops 84505 --manter-vinculos; python -m controleproducao" in texto
