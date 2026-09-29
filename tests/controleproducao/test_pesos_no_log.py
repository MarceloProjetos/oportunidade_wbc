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
    sql, _params = leitor.fetch_all.call_args.args
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
