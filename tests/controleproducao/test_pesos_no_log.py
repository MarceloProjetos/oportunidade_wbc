"""The weight of each order line in the Processar pedidos log (29/09/2026).

The weight of quote 00125817 went wrong with nothing about it in the execution log: the
step now prints the SAP ``Weight1`` next to the WBC tree's level 1, read-only.
"""
from __future__ import annotations

import logging
from unittest.mock import MagicMock

from controleproducao.modules.pedidos_wbc import service
from controleproducao.modules.pedidos_wbc.schemas import EstruturaPrd


def _arvore(orc_itm: int, nivel: int, peso: float) -> EstruturaPrd:
    """The real node `busca_estrutura_produto` returns — a stand-in with guessed attribute
    names let the first version pass here and fail on the .11 (29/09/2026)."""
    return EstruturaPrd(
        orc_num="00125817", grp_code=2, sub_group_cod=0, orc_itm=orc_itm, prd_code="X",
        nivel=nivel, cor_cod="", prd_desc="", quantidade=1, total=0, peso=peso,
        id_integracao_orc=0, linha_orc="", prd_arv="",
    )


def _leitor(*linhas: dict) -> MagicMock:
    leitor = MagicMock()
    leitor.fetch_all.return_value = list(linhas)
    return leitor


def test_peso_diferente_sai_como_aviso(caplog):
    """Pedido 84444: o SAP ficou com 124,5 kg; a árvore soma 226,43 no nível 1."""
    estrutura = [_arvore(1, 1, 87.83), _arvore(1, 1, 138.6), _arvore(1, 2, 110.876)]
    leitor = _leitor({"LineNum": 0, "ItemCode": "I000003", "Quantity": 1, "Weight1": 124.5,
                      "U_INO_ORCITM": "1"})
    with caplog.at_level(logging.INFO, logger=service.__name__):
        service._loga_pesos(leitor, "00125817", 20243, estrutura)

    registro = caplog.records[-1]
    assert registro.levelno == logging.WARNING
    assert registro.getMessage() == (
        "Pedido 00125817: peso da linha 0 (item I000003, OrcItm 1, qtd 1): SAP 124,50 kg · "
        "esperado 249,07 kg (árvore do WBC 226,43 kg + 10%) — DIFERENTE."
    )
    sql, _params = leitor.fetch_all.call_args_list[0].args   # [1] is the change log (no change here)
    assert 'FROM RDR1 T0' in sql and "?" in sql       # bound, never pasted into the text


def test_peso_dentro_de_1_por_cento_sai_como_info(caplog):
    """O 248 digitado à mão no 84444 fica a 0,4% dos 249,07 esperados: é o certo."""
    for sap in (249.07, 248.0):
        leitor = _leitor({"LineNum": 0, "ItemCode": "I000003", "Quantity": 1, "Weight1": sap,
                          "U_INO_ORCITM": "1"})
        with caplog.at_level(logging.INFO, logger=service.__name__):
            service._loga_pesos(leitor, "00125817", 20243, [_arvore(1, 1, 226.42999999999998)])
        assert caplog.records[-1].levelno == logging.INFO
    assert "SAP 248,00 kg · esperado 249,07 kg (árvore do WBC 226,43 kg + 10%)." in caplog.text


def test_linha_sem_arvore_e_leitura_que_falha_nao_param_o_processamento(caplog):
    sem_arvore = _leitor({"LineNum": 1, "ItemCode": "I000009", "Quantity": 2, "Weight1": 1,
                          "U_INO_ORCITM": "7"})
    quebrado = MagicMock()
    quebrado.fetch_all.side_effect = RuntimeError("HANA fora")
    with caplog.at_level(logging.INFO, logger=service.__name__):
        service._loga_pesos(sem_arvore, "00125817", 20243, [_arvore(1, 1, 10.0)])
        service._loga_pesos(quebrado, "00125817", 20243, [])
    assert "árvore do WBC sem peso." in caplog.text
    assert "não foi possível comparar os pesos: HANA fora" in caplog.text


def _versao(quantidade, peso, quem="Adriano Fonseca", hora=140006) -> dict:
    """One row of `HISTORICO_DA_LINHA_DO_PEDIDO` (ADO1 + ADOC + OUSR)."""
    from datetime import datetime

    return {"Quantity": quantidade, "Weight1": peso, "UpdateDate": datetime(2026, 10, 1),
            "UpdateTS": hora, "U_NAME": quem, "USER_CODE": "x"}


def test_peso_diferente_diz_quem_mudou_a_quantidade_no_sap(caplog):
    """Pedido 84453 (01/10/2026): created with 2 × 176,90 kg; a person set quantity 1 in the
    SAP and it rescaled the weight to 88,45. The log must say so — it read as a bug."""
    leitor = MagicMock()
    leitor.fetch_all.side_effect = [
        [{"LineNum": 0, "ItemCode": "I000003", "Quantity": 1, "Weight1": 88.45, "U_INO_ORCITM": "1"}],
        [_versao(2, 176.9, quem="orcaview", hora=115109), _versao(1, 88.45), _versao(1, 88.45, hora=143657)],
    ]
    with caplog.at_level(logging.INFO, logger=service.__name__):
        service._loga_pesos(leitor, "00125348", 20300, [_arvore(1, 1, 160.82)])

    assert caplog.records[-1].levelno == logging.WARNING
    assert caplog.records[-1].getMessage() == (
        "Pedido 00125348: CAUSA: Adriano Fonseca mudou a quantidade da linha 0 de 2 para 1 no SAP "
        "em 01/10/2026 às 14:00, e o SAP refez o peso na mesma proporção (176,90 → 88,45 kg). "
        "A integração tinha gravado o peso certo (176,90 kg) ao criar o pedido."
    )
    sql, params = leitor.fetch_all.call_args.args
    assert "FROM ADO1 T0" in sql and "?" in sql and list(params) == [20300, 0]


def test_peso_mudado_a_mao_sem_mudar_a_quantidade(caplog):
    leitor = MagicMock()
    leitor.fetch_all.side_effect = [
        [{"LineNum": 0, "ItemCode": "I000003", "Quantity": 2, "Weight1": 100, "U_INO_ORCITM": "1"}],
        [_versao(2, 176.9, quem="orcaview"), _versao(2, 100, quem="vendas01", hora=90512)],
    ]
    with caplog.at_level(logging.INFO, logger=service.__name__):
        service._loga_pesos(leitor, "00125348", 20300, [_arvore(1, 1, 160.82)])
    assert caplog.records[-1].getMessage().startswith(
        "Pedido 00125348: CAUSA: vendas01 mudou o peso da linha 0 no SAP em 01/10/2026 às 09:05 "
        "(176,90 → 100,00 kg)."
    )


def test_sem_historico_fica_so_o_diferente(caplog):
    """No change in the SAP log (or the log cannot be read): no cause is invented."""
    for historico in ([_versao(1, 88.45)], RuntimeError("ADOC fora")):
        leitor = MagicMock()
        leitor.fetch_all.side_effect = [
            [{"LineNum": 0, "ItemCode": "I000003", "Quantity": 1, "Weight1": 88.45, "U_INO_ORCITM": "1"}],
            historico,
        ]
        caplog.clear()
        with caplog.at_level(logging.INFO, logger=service.__name__):
            service._loga_pesos(leitor, "00125348", 20300, [_arvore(1, 1, 160.82)])
        assert "CAUSA" not in caplog.text
        assert "DIFERENTE" in caplog.text


def test_peso_acima_de_mil_sai_com_separador_de_milhar(caplog):
    """The log uses the screen's own number format (`core.formato.numero_br`, 30/09/2026)."""
    leitor = _leitor({"LineNum": 0, "ItemCode": "I000003", "Quantity": 167, "Weight1": 22913.87,
                      "U_INO_ORCITM": "1"})
    with caplog.at_level(logging.INFO, logger=service.__name__):
        service._loga_pesos(leitor, "00124853", 1, [_arvore(1, 1, 20830.79)])
    assert "SAP 22.913,87 kg · esperado 22.913,87 kg (árvore do WBC 20.830,79 kg + 10%)." in caplog.text
