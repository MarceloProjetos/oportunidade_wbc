"""The weight of each order line in the Processar pedidos log (29/09/2026).

The weight of quote 00125817 went wrong with nothing about it in the execution log: the
step now prints the SAP ``Weight1`` next to the WBC tree's level 1, read-only.
"""
from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

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


def test_peso_diferente_volta_estruturado_para_o_resultado():
    """The same finding, as fields — `resultado.pesos_diferentes` of the JSON API."""
    leitor = MagicMock()
    leitor.fetch_all.side_effect = [
        [{"LineNum": 0, "ItemCode": "I000003", "Quantity": 1, "Weight1": 88.45, "U_INO_ORCITM": "1"}],
        [_versao(2, 176.9, quem="orcaview", hora=115109), _versao(1, 88.45)],
    ]
    diferentes = service._loga_pesos(leitor, "00125348", 20300, [_arvore(1, 1, 160.82)])

    assert len(diferentes) == 1
    item = diferentes[0]
    assert {k: item[k] for k in ("orc_num", "linha", "item", "quantidade", "peso_sap", "peso_esperado",
                                 "arvore_wbc")} == {
        "orc_num": "00125348", "linha": 0, "item": "I000003", "quantidade": 1.0,
        "peso_sap": 88.45, "peso_esperado": 176.9, "arvore_wbc": 160.82,
    }
    causa = item["causa"]
    assert {k: causa[k] for k in causa if k != "texto"} == {
        "tipo": "quantidade_mudada_no_sap", "usuario": "Adriano Fonseca",
        "momento": "2026-10-01T14:00:06", "quantidade_antes": 2.0, "quantidade_depois": 1.0,
        "peso_antes": 176.9, "peso_depois": 88.45, "integracao_gravou_certo": True,
    }
    assert causa["texto"].startswith("CAUSA: Adriano Fonseca mudou a quantidade")


def test_peso_certo_nao_entra_no_resultado():
    leitor = _leitor({"LineNum": 0, "ItemCode": "I000003", "Quantity": 2, "Weight1": 176.9,
                      "U_INO_ORCITM": "1"})
    assert service._loga_pesos(leitor, "00125348", 20300, [_arvore(1, 1, 160.82)]) == []


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


def _historico_84453() -> list[dict]:
    """84453 as the Processar sees it: created, rescaled by a person, and saved by the Processar
    itself seconds ago (``U_INO_ProcessWBC``) — that fresh save must not hide the forecast."""
    from datetime import datetime

    agora = datetime.now()
    return [
        {**_versao(2, 176.9, quem="orcaview", hora=115109), "LogInstanc": 1, "USER_CODE": "orcaview",
         "LineTotal": 3159.62, "PrimeiraVersao": 1},
        {**_versao(1, 88.45), "LogInstanc": 2, "USER_CODE": "vendas01", "LineTotal": 3159.62, "PrimeiraVersao": 1},
        {**_versao(1, 88.45, quem="orcaview"), "LogInstanc": 3, "USER_CODE": "orcaview", "LineTotal": 3159.62,
         "PrimeiraVersao": 1, "UpdateDate": agora, "UpdateTS": int(agora.strftime("%H%M%S"))},
    ]


def _linha_84453(**extra) -> dict:
    return {"LineNum": 0, "ItemCode": "I000003", "Quantity": 1, "Weight1": 88.45, "LineTotal": 3159.62,
            "LineStatus": "O", "U_INO_ORCITM": "1", **extra}


@pytest.fixture
def integracao_orcaview(monkeypatch):
    from types import SimpleNamespace

    import controleproducao.config as cfg

    monkeypatch.setattr(cfg, "get_settings", lambda: SimpleNamespace(sl_username="orcaview"))


def test_diz_o_que_a_integracao_faz_com_a_linha(caplog, integracao_orcaview, monkeypatch):
    """Same rule as the worker (`wbcpython.domain.peso_reescalado`); in simulation, fix by hand."""
    import wbcpython.application.pesos_reescalados as aplicacao

    monkeypatch.setattr(aplicacao, "GRAVA_A_PARTIR_DE", None)
    leitor = MagicMock()
    leitor.fetch_all.side_effect = [[_linha_84453()], _historico_84453()]
    with caplog.at_level(logging.INFO, logger=service.__name__):
        service._loga_pesos(leitor, "00125348", 20300, [_arvore(1, 1, 160.82)])
    assert caplog.records[-1].levelno == logging.WARNING
    assert caplog.records[-1].getMessage() == (
        "Pedido 00125348: a integração voltaria este peso para 176,90 kg, mas a correção automática "
        "ainda está em simulação: corrija à mão."
    )


def test_linha_fechada_nao_ganha_previsao(caplog, integracao_orcaview):
    leitor = MagicMock()
    leitor.fetch_all.side_effect = [[_linha_84453(LineStatus="C")], _historico_84453()]
    with caplog.at_level(logging.INFO, logger=service.__name__):
        service._loga_pesos(leitor, "00125348", 20300, [_arvore(1, 1, 160.82)])
    assert "CAUSA" in caplog.text and "a integração voltaria" not in caplog.text


def test_previsao_que_falha_nao_para_o_processamento(caplog, integracao_orcaview, monkeypatch):
    """It runs after ProcessWBC='Y' is saved: an exception there would leave an order without OPs."""
    import wbcpython.domain.peso_reescalado as regra

    def quebra(*a, **kw):
        raise ValueError("hora inválida")

    monkeypatch.setattr(regra, "decidir", quebra)
    leitor = MagicMock()
    leitor.fetch_all.side_effect = [[_linha_84453()], _historico_84453()]
    with caplog.at_level(logging.INFO, logger=service.__name__):
        diferentes = service._loga_pesos(leitor, "00125348", 20300, [_arvore(1, 1, 160.82)])
    assert len(diferentes) == 1 and "hora inválida" in caplog.text


def test_depois_de_ligada_troca_velha_nao_promete_correcao(caplog, integracao_orcaview, monkeypatch):
    """The worker only reads the last 3 days: an older change is not promised (review, 05/10/2026)."""
    from datetime import datetime

    import wbcpython.application.pesos_reescalados as aplicacao

    monkeypatch.setattr(aplicacao, "GRAVA_A_PARTIR_DE", datetime(2026, 9, 1))
    leitor = MagicMock()
    leitor.fetch_all.side_effect = [[_linha_84453()], _historico_84453()]
    with caplog.at_level(logging.INFO, logger=service.__name__):
        service._loga_pesos(leitor, "00125348", 20300, [_arvore(1, 1, 160.82)])
    assert "NÃO volta este peso sozinha: a troca tem mais de 3 dias" in caplog.records[-1].getMessage()


def test_troca_anterior_ao_inicio_pede_correcao_a_mao(caplog, integracao_orcaview, monkeypatch):
    """A change before the start date is the past: the worker leaves it, so the log must not go
    silent — it says to fix by hand (05/10/2026, when F4 got its date)."""
    from datetime import datetime

    import wbcpython.application.pesos_reescalados as aplicacao

    monkeypatch.setattr(aplicacao, "GRAVA_A_PARTIR_DE", datetime(2026, 10, 6))
    leitor = MagicMock()
    leitor.fetch_all.side_effect = [[_linha_84453()], _historico_84453()]
    with caplog.at_level(logging.INFO, logger=service.__name__):
        service._loga_pesos(leitor, "00125348", 20300, [_arvore(1, 1, 160.82)])
    assert caplog.records[-1].levelno == logging.WARNING
    assert caplog.records[-1].getMessage() == (
        "Pedido 00125348: a integração NÃO volta este peso sozinha: a troca é anterior a "
        "06/10/2026 00:00, quando a correção automática começa (o passado não se altera); "
        "corrija à mão."
    )
