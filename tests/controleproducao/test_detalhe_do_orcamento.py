"""The order's "Detalhe do Orçamento" (`ORDR.U_INO_ORCAMENTO`) follows each Processar/Reprocessar.

Until 05/10/2026 the DocEntry of the new OrcDetalhe snapshot was a stub returning 0, so the
order kept pointing at the old one (84454: left on 546783 while 546875 existed). Now it comes
from the Service Layer's answer, is written in the same PATCH and is read back into the log.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from controleproducao.core.tarefas import Tarefa, acompanha_log
from controleproducao.modules.pedidos_wbc import service as svc


def _hana(*detalhes, congelado: str = "Y", falha: Exception | None = None) -> MagicMock:
    """HANA answering the order's U_INO_ORCAMENTO in turn: before the PATCH, then after it."""
    leituras = iter(detalhes)

    def fetch_all(sql, *args, **kwargs):
        if "U_INO_ORCAMENTO" in sql:
            valor = next(leituras)
            if isinstance(valor, Exception):
                raise valor
            return [{"U_INO_ORCAMENTO": valor}]
        if "U_INO_Congelado" in sql:
            return [{"U_INO_Congelado": congelado}]
        return []

    hana = MagicMock()
    hana.fetch_all = fetch_all
    return hana


def _atualiza(hana, novo: int, process: str = "Y") -> AsyncMock:
    sl = MagicMock()
    sl.update_entity = AsyncMock(return_value={})
    asyncio.run(svc.atualiza_pedido_tabela(
        sl, MagicMock(), hana, [], "00125793", "C011984", "84454", "20302", novo, process,
    ))
    return sl.update_entity


@pytest.mark.parametrize(
    ("resposta", "esperado"),
    [({"DocEntry": 546875, "DocNum": 7}, 546875), ({"DocEntry": "546875"}, 546875),
     ({"DocNum": 7}, 0), ({}, 0), (None, 0), ({"DocEntry": "x"}, 0)],
)
def test_numero_do_detalhe_vem_da_resposta_da_service_layer(resposta, esperado):
    """Only `DocEntry`: the UDO's DocNum is another numbering and would link the wrong record."""
    assert svc._doc_entry_do_detalhe(resposta) == esperado


def test_preenche_tabela_devolve_o_detalhe_que_acabou_de_criar():
    linha = {
        "ORCNUM": "00125793", "GRPCOD": 7, "SUBGRPCOD": 0, "ORCITM": 1, "PRDCOD": "i000003",
        "ORCPRDARV_NIVEL": 1, "CORCOD": "", "PRDDSC": "Porta-Paletes", "ORCQTD": 1, "ORCTOT": 10.0,
        "ORCPES": 5.0, "idIntegracao_OrcPrdArv": 1,
    }
    wbc = MagicMock()
    wbc.fetch_all.return_value = [linha]
    sl = MagicMock()
    sl.create_entity = AsyncMock(return_value={"DocEntry": 546875, "DocNum": 1234})
    with patch.object(svc, "_busca_header_nova_tabela_quot", AsyncMock(return_value=MagicMock())), \
         patch.object(svc, "_pega_linha_manual", AsyncMock(return_value=None)), \
         patch.object(svc, "_monta_body_orc_detalhe", return_value={}):
        assert asyncio.run(svc.preenche_tabela(sl, wbc, MagicMock(), "00125793")) == 546875


def test_preenche_tabela_sem_estrutura_tambem_devolve_o_detalhe():
    """The fallback path (no detailed structure in the WBC) creates a snapshot too."""
    wbc = MagicMock()
    wbc.fetch_all.return_value = []
    sl = MagicMock()
    sl.create_entity = AsyncMock(return_value={"DocEntry": 546876, "DocNum": 1235})
    with patch.object(svc, "_busca_nova_tabela_quot_com_linhas", AsyncMock(return_value=[MagicMock(linhas=[])])), \
         patch.object(svc, "_monta_body_orc_detalhe", return_value={}):
        assert asyncio.run(svc.preenche_tabela(sl, wbc, MagicMock(), "00125793")) == 546876


def test_pedido_nao_congelado_tambem_grava_o_detalhe(caplog):
    with caplog.at_level(logging.INFO, logger=svc.__name__):
        update = _atualiza(_hana(546783, 546875, congelado="N"), 546875)
    assert update.await_args.args[2]["U_INO_ORCAMENTO"] == 546875
    assert update.await_args.args[2]["U_INO_Congelado"] == "Y"
    assert "546783 → 546875 (conferido no SAP)" in caplog.text


def test_leitura_de_antes_que_falha_nao_grava_nada():
    """Before the PATCH nothing is saved yet: failing there leaves the order as it was."""
    sl = MagicMock()
    sl.update_entity = AsyncMock(return_value={})
    with pytest.raises(RuntimeError, match="HANA caiu"):
        asyncio.run(svc.atualiza_pedido_tabela(
            sl, MagicMock(), _hana(RuntimeError("HANA caiu")), [], "00125793", "C011984", "84454", "20302",
            546875, "Y",
        ))
    sl.update_entity.assert_not_awaited()


def test_pedido_passa_a_apontar_para_o_detalhe_novo_e_o_log_confere(caplog):
    with caplog.at_level(logging.INFO, logger=svc.__name__):
        update = _atualiza(_hana(546783, 546875), 546875)
    assert update.await_args.args[2]["U_INO_ORCAMENTO"] == 546875
    linha = caplog.records[-1]
    assert linha.levelno == logging.INFO
    assert linha.getMessage() == "  Detalhe do Orçamento do pedido: 546783 → 546875 (conferido no SAP)."


def test_reprocessar_tambem_grava_o_detalhe_novo(caplog):
    """Reprocessar (process='N', frozen order) goes through `_update_tab_pedido_cong`."""
    sl = MagicMock()
    sl.get_by_key = AsyncMock(return_value={"DocumentLines": []})
    sl.update_entity = AsyncMock(return_value={})
    with caplog.at_level(logging.INFO, logger=svc.__name__):
        asyncio.run(svc.atualiza_pedido_tabela(
            sl, MagicMock(), _hana(546783, 546874), [], "00125793", "C011984", "84454", "20302", 546874, "N",
        ))
    assert sl.update_entity.await_args.args[2]["U_INO_ORCAMENTO"] == 546874
    assert "546783 → 546874 (conferido no SAP)" in caplog.text


def test_sap_sem_o_valor_novo_e_erro_no_log(caplog):
    with caplog.at_level(logging.INFO, logger=svc.__name__):
        _atualiza(_hana(546783, 546783), 546875)
    linha = caplog.records[-1]
    assert linha.levelno == logging.ERROR
    assert linha.getMessage() == (
        "  Detalhe do Orçamento NÃO atualizado: o pedido devia apontar para o 546875 e está "
        "no 546783 (U_INO_ORCAMENTO)."
    )


def test_detalhe_sem_numero_nao_grava_e_avisa(caplog):
    with caplog.at_level(logging.INFO, logger=svc.__name__):
        update = _atualiza(_hana(546783), 0)
    assert "U_INO_ORCAMENTO" not in update.await_args.args[2]
    assert caplog.records[-1].levelno == logging.ERROR
    assert caplog.records[-1].getMessage() == (
        "  Sem Detalhe do Orçamento novo (não criado, ou a resposta veio sem DocEntry): o pedido "
        "continua no 546783 (U_INO_ORCAMENTO NÃO atualizado)."
    )


def test_pedido_sem_detalhe_nenhum_diz_isso(caplog):
    with caplog.at_level(logging.INFO, logger=svc.__name__):
        _atualiza(_hana(None, None), 546875)
    assert "devia apontar para o 546875 e está sem Detalhe" in caplog.records[-1].getMessage()


def test_conferencia_que_falha_nao_para_o_processamento(caplog):
    """`U_INO_ProcessWBC` is already saved: raising here would leave an order without OPs."""
    with caplog.at_level(logging.INFO, logger=svc.__name__):
        _atualiza(_hana(546783, RuntimeError("HANA caiu")), 546875)
    assert caplog.records[-1].levelno == logging.ERROR
    assert "Não foi possível conferir o Detalhe do Orçamento do pedido: HANA caiu" in caplog.text


def test_erro_do_detalhe_sai_marcado_em_vermelho_na_tela():
    tarefa = Tarefa(id="t", nome="n", descricao="d", criada_em=datetime.now())
    with acompanha_log(tarefa, svc.__name__):
        _atualiza(_hana(546783, 546783), 546875)
    assert any("⚠" in linha and "Detalhe do Orçamento NÃO atualizado" in linha for linha in tarefa.linhas)
