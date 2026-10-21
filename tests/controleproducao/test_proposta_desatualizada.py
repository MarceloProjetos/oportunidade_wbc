"""Processar warns when the printed proposal is an older revision than the quote.

A real case on 21/10/2026: the seller changed the colour to white (quote revision C, tree
re-exported) without printing the proposal again — `INTEGRACAO_ORCIMP` stayed on B saying
"Cinza", and that is what goes to the order-detail header. A warning, never a refusal: in the
plan (screen and API, one text) and again at run time (log, result, ATENÇÃO line).
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from controleproducao.modules.pedidos_wbc import acoes, service
from tests.controleproducao.test_api_pedidos_wbc import (  # noqa: F401 - pytest fixtures
    API,
    CABECALHO,
    SVC,
    _conferir,
    _espera,
    _limpa_registros,
    _texto,
    _token,
    ambiente,
    c,
    execucao_falsa,
    lista,
)

INFO = {"revisao": "C", "revisao_proposta": "B"}


def _wbc(linhas):
    wbc = MagicMock()
    wbc.fetch_all = MagicMock(return_value=list(linhas))
    return wbc


def _linha(orc, revisao, proposta):
    return {"ORCNUM": orc, "REVISAO": revisao, "REVISAO_PROPOSTA": proposta}


class TestLeitura:
    def test_so_entra_proposta_mais_antiga(self) -> None:
        wbc = _wbc([
            _linha("00120000", "C", "B"),
            _linha("00120002", "B", "B"),   # same revision
            _linha("00120003", "A", "B"),   # proposal newer: not this warning
            _linha("00120004", "C", ""),    # no revision on the proposal: nothing to compare
        ])

        resultado = asyncio.run(
            service.propostas_desatualizadas(wbc, ["00120000", "00120002", "00120003", "00120004"])
        )

        assert resultado == {"00120000": INFO}

    def test_varias_linhas_da_proposta_fica_a_mais_antiga(self) -> None:
        wbc = _wbc([_linha("00120000", "C", "B"), _linha("00120000", "C", "A")])
        resultado = asyncio.run(service.propostas_desatualizadas(wbc, ["00120000"]))
        assert resultado["00120000"]["revisao_proposta"] == "A"

    def test_consulta_so_le_sem_repetir_linhas_e_passa_a_lista_por_parametro(self) -> None:
        wbc = _wbc([])
        asyncio.run(service.propostas_desatualizadas(wbc, ["00120000", "00120000", "00120002"]))

        sql, params = wbc.fetch_all.call_args.args
        assert sql.lstrip().upper().startswith("SELECT")
        assert "INTEGRACAO_ORCIMP" in sql and "ORCIMP_REVISAO" in sql
        assert "SELECT DISTINCT" in sql   # ORCIMP has one row per proposal item
        assert list(params) == ["00120000", "00120002"]

    def test_lista_vazia_nao_consulta(self) -> None:
        wbc = _wbc([])
        assert asyncio.run(service.propostas_desatualizadas(wbc, [])) == {}
        wbc.fetch_all.assert_not_called()


class TestPlano:
    def test_aviso_nao_recusa_e_diz_o_que_fazer(self, lista) -> None:  # noqa: F811
        orc = lista[0].orc_num_masc
        plano = acoes.monta_plano(lista, [str(lista[0].opp_id)], False, propostas={orc: INFO})

        assert len(plano.itens) == 1
        (aviso,) = acoes.avisos_do_plano(plano)
        assert aviso["codigo"] == "proposta_desatualizada"
        assert (aviso["revisao"], aviso["revisao_proposta"]) == ("C", "B")
        assert f"Pedido {lista[0].doc_num} (WBC {orc})" in aviso["mensagem"]
        assert "gerar a proposta da revisão C no WBC" in aviso["mensagem"]

    def test_sem_proposta_antiga_sem_aviso(self, lista) -> None:  # noqa: F811
        plano = acoes.monta_plano(lista, [str(lista[0].opp_id)], False)
        assert acoes.avisos_do_plano(plano) == []

    def test_reprocessar_nao_avisa(self, lista) -> None:  # noqa: F811
        orc = lista[0].orc_num_masc
        plano = acoes.monta_plano(lista, [str(lista[0].opp_id)], True, propostas={orc: INFO})
        assert acoes.avisos_do_plano(plano) == []

    def test_leitura_que_falha_nao_derruba_o_plano(self, lista) -> None:  # noqa: F811
        with patch(f"{acoes.__name__}.WbcSqlServerClient", side_effect=OSError("WBC fora")):
            assert asyncio.run(
                acoes.le_propostas_desatualizadas(lista, [str(lista[0].opp_id)], False)
            ) == {}

    def test_le_so_os_selecionados_e_nada_no_reprocessar(self, lista) -> None:  # noqa: F811
        lido = AsyncMock(return_value={})
        with patch(f"{acoes.__name__}.WbcSqlServerClient", MagicMock()), \
             patch.object(service, "propostas_desatualizadas", lido):
            asyncio.run(acoes.le_propostas_desatualizadas(lista, [str(lista[1].opp_id)], False))
            asyncio.run(acoes.le_propostas_desatualizadas(lista, [str(lista[1].opp_id)], True))

        lido.assert_awaited_once()
        assert lido.await_args.args[1] == [lista[1].orc_num_masc]


def test_api_e_tela_avisam_com_o_mesmo_texto(c, ambiente) -> None:  # noqa: F811
    orc = ambiente[0].orc_num_masc
    with patch(f"{acoes.__name__}.le_propostas_desatualizadas", AsyncMock(return_value={orc: INFO})):
        resposta = _conferir(c, [ambiente[0].opp_id])
        tela = _texto(
            c.post("/pedidos-wbc/processar/conferir", data={"opp_ids": [str(ambiente[0].opp_id)]}).text
        )

    assert resposta.status_code == 200, resposta.text
    (aviso,) = resposta.json()["plano"]["avisos"]
    assert aviso["codigo"] == "proposta_desatualizada" and aviso["mensagem"] in tela
    assert "Confirmar e executar" in tela


def test_api_sem_aviso_traz_lista_vazia(c, ambiente) -> None:  # noqa: F811
    with patch(f"{acoes.__name__}.le_propostas_desatualizadas", AsyncMock(return_value={})):
        resposta = _conferir(c, [ambiente[0].opp_id])
    assert resposta.json()["plano"]["avisos"] == []


class TestNaExecucao:
    def _prepara(self, monkeypatch, propostas, processado="N"):
        monkeypatch.setattr(service, "_busca_orcamentos_wbc", AsyncMock(return_value=[]))
        monkeypatch.setattr(
            service, "_get_id_orcamentos_pedido",
            AsyncMock(return_value={"DocEntry": 20000, "CardCode": "C000001", "DocNum": 84000}),
        )
        monkeypatch.setattr(service, "ops_vivas_de_pedidos_cancelados", AsyncMock(return_value={}))
        monkeypatch.setattr(service, "propostas_desatualizadas", propostas)
        monkeypatch.setattr(service, "_verifica_process_wbc", AsyncMock(return_value=processado))
        # Stop right after the checks: the warning must be recorded before any SAP write.
        monkeypatch.setattr(service, "preenche_tabela", AsyncMock(side_effect=RuntimeError("parou")))
        monkeypatch.setattr(service, "preenche_log", AsyncMock())

    def test_avisa_e_segue(self, monkeypatch) -> None:
        self._prepara(monkeypatch, AsyncMock(return_value={"00120000": INFO}))

        resultado = asyncio.run(
            service.processar_pedidos_novos(None, None, MagicMock(), ["00120000"])
        )

        (aviso,) = resultado["proposta_desatualizada"]
        assert aviso["orc_num"] == "00120000" and aviso["revisao_proposta"] == "B"
        assert "Pedido 84000 (WBC 00120000)" in aviso["motivo"]
        # At run time the pedido is already going through: what to do NOW, not "before".
        assert "reprocesse o pedido" in aviso["motivo"] and "antes de processar" not in aviso["motivo"]
        # Not a refusal: the pedido went on to the next check (which the test made fail).
        assert resultado["com_erro"][0]["motivo"] == "parou"

    def test_pedido_ja_processado_nao_ganha_aviso(self, monkeypatch) -> None:
        """Skipped as "já processado": it was not processed, so no ATENÇÃO about it."""
        lido = AsyncMock(return_value={"00120000": INFO})
        self._prepara(monkeypatch, lido, processado="Y")

        resultado = asyncio.run(
            service.processar_pedidos_novos(None, None, MagicMock(), ["00120000"])
        )

        assert resultado["proposta_desatualizada"] == []
        assert "já está marcado como processado" in resultado["com_erro"][0]["motivo"]
        lido.assert_not_awaited()

    def test_leitura_que_falha_nao_para_o_pedido(self, monkeypatch) -> None:
        self._prepara(monkeypatch, AsyncMock(side_effect=OSError("WBC fora")))

        resultado = asyncio.run(
            service.processar_pedidos_novos(None, None, MagicMock(), ["00120000"])
        )

        assert resultado["proposta_desatualizada"] == []
        assert resultado["com_erro"][0]["motivo"] == "parou"



def test_execucao_traz_o_aviso_no_resultado_e_na_linha_de_atencao(c, ambiente, execucao_falsa):  # noqa: F811
    servico = AsyncMock(return_value={
        "processados": ["00120001"], "com_erro": [], "sem_op": [], "sem_rateio": [], "pesos_diferentes": [],
        "proposta_desatualizada": [{"orc_num": "00120001", **INFO, "motivo": "texto"}],
    })
    token = _token(c, (4301,))
    with patch(f"{SVC}.processar_pedidos_novos", servico):
        resposta = c.post(f"{API}/processar/executar", json={"token": token, "solicitante": "Ana"},
                          headers=CABECALHO)
        estado = _espera(c, resposta.json()["execucao"]["id"])

    assert estado["resultado"]["proposta_desatualizada"][0]["doc_num"] == "84201"
    assert estado["passo"] == (
        "Pedido 84201 (WBC 00120001): ATENÇÃO — proposta do WBC na revisão B, orçamento na C "
        "(acabamento/cor da proposta antiga — veja acima)"
    )
    assert estado["desfecho"] == "ok"       # a warning, not a failed pedido
