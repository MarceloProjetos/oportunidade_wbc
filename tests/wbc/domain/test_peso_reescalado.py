"""The weight the SAP rescales when a person changes a line's quantity (PLANO_PESO_REESCALADO).

The cases are the measured ones (05/10/2026): the version history of real order lines.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from wbcpython.domain.peso_reescalado import (
    Acao,
    LinhaAtual,
    Versao,
    decidir,
    descrever,
    momento_do_sap,
    versao_da_linha,
)

AGORA = datetime(2026, 10, 5, 10, 0)


def _v(instancia, qtd, peso, total, *, quem="Pessoa de Vendas", minutos=60):
    return Versao(
        instancia=instancia, quantidade=qtd, peso=peso, total=total,
        pela_integracao=quem == "orcaview", usuario=quem,
        momento=AGORA - timedelta(minutes=minutos),
    )


def _atual(versoes, **kw):
    ultima = versoes[-1]
    return LinhaAtual(
        kw.get("quantidade", ultima.quantidade), kw.get("peso", ultima.peso),
        kw.get("total", ultima.total), kw.get("aberta", True),
    )


def _decidir(versoes, **kw):
    return decidir(versoes, _atual(versoes, **kw), AGORA)


def test_84454_volta_ao_peso_da_integracao():
    versoes = [_v(1, 2, 970.79, 21454.18, quem="orcaview"), _v(2, 1, 485.395, 21454.18)]
    d = _decidir(versoes)
    assert (d.acao, d.peso) == (Acao.CORRIGIR, 970.79)
    assert descrever(d) == (
        "Pessoa de Vendas mudou a quantidade de 2 para 1 em 05/10/2026 às 09:00 e o SAP refez "
        "o peso (970,79 → 485,39 kg)"
    )


def test_total_ate_5_por_cento_corrige_e_acima_so_avisa():
    """84368 L1 (-4,73%: arredondamento do unitário) × 84438 (-54,7%: a venda mudou)."""
    l1 = [_v(1, 1, 76, 1662.64, quem="orcaview"), _v(2, 6, 456, 1584.0)]
    assert _decidir(l1).acao == Acao.CORRIGIR
    venda = [_v(1, 10, 575, 7476.0, quem="orcaview"), _v(2, 1, 57.5, 3389.83)]
    d = _decidir(venda)
    assert (d.acao, d.motivo) == (Acao.AVISAR, "o total da linha mudou -54,7%")


def test_trocas_seguidas_voltam_ao_peso_de_antes_da_primeira():
    versoes = [
        _v(1, 1, 148.94, 4127.4, quem="orcaview"),
        _v(2, 30, 4468.2, 4127.4, minutos=30),
        _v(3, 30, 4468.2, 4127.4, quem="Pessoa do Fiscal", minutos=20),  # header edit only
        _v(4, 25, 3723.5, 4127.4, minutos=10),
    ]
    assert _decidir(versoes).peso == 148.94


@pytest.mark.parametrize(
    ("versoes", "kw", "motivo"),
    [
        # 84457: Pessoa de Projetos typed 148 after the rescale — the typed number stays.
        ([_v(1, 1, 148.94, 4127.4, quem="orcaview"), _v(2, 30, 4468.2, 4127.4),
          _v(3, 30, 148.0, 4127.4, quem="Pessoa de Projetos")], {}, "a última mudança"),
        # 84453: the integration already put it back.
        ([_v(1, 2, 176.9, 3159.62, quem="orcaview"), _v(2, 1, 88.45, 3159.62),
          _v(3, 1, 176.9, 3159.62, quem="orcaview")], {}, "a última mudança"),
        # 84441: quantity changed, weight did not.
        ([_v(1, 2, 408, 6774.43, quem="orcaview"), _v(2, 1, 408, 6774.43)], {}, "a última mudança"),
        # 84406: the weight moved again with no version in ADOC (another writer).
        ([_v(1, 1, 311, 5939.2, quem="orcaview"), _v(2, 10, 3110, 5937.5)], {"peso": 310.0},
         "não é mais o que o SAP calculou"),
        ([_v(1, 2, 970.79, 100, quem="orcaview"), _v(2, 1, 485.395, 100)], {"aberta": False},
         "linha fechada"),
        ([_v(1, 2, 970.79, 100, quem="orcaview"), _v(2, 1, 485.395, 100, minutos=2)], {},
         "aguardando 3 min"),
    ],
)
def test_deixa_a_linha_como_esta(versoes, kw, motivo):
    d = _decidir(versoes, **kw)
    assert d.acao == Acao.NADA and motivo in d.motivo


def test_o_passado_nao_se_altera():
    """Marcelo, 05/10/2026: a rescale that started before writing was switched on stays."""
    versoes = [_v(1, 2, 970.79, 100, quem="orcaview"), _v(2, 1, 485.395, 100, minutos=60)]
    atual = _atual(versoes)
    assert decidir(versoes, atual, AGORA, a_partir_de=AGORA - timedelta(minutes=30)).acao == Acao.NADA
    assert decidir(versoes, atual, AGORA, a_partir_de=AGORA - timedelta(minutes=90)).acao == Acao.CORRIGIR


def test_espera_de_3_minutos_conta_do_ultimo_salvamento():
    versoes = [_v(1, 2, 970.79, 100, quem="orcaview"), _v(2, 1, 485.395, 100, minutos=3)]
    assert _decidir(versoes).acao == Acao.CORRIGIR


def test_peso_digitado_por_pessoa_nao_e_restaurado_so_avisa():
    versoes = [
        _v(1, 1, 148.94, 4127.4, quem="orcaview"),
        _v(2, 30, 4468.2, 4127.4),
        _v(3, 30, 148.0, 4127.4, quem="Pessoa de Projetos"),
        _v(4, 25, 123.333333, 4127.4),
    ]
    d = _decidir(versoes)
    assert (d.acao, d.motivo) == (Acao.AVISAR, "o peso de antes (148,00 kg) foi digitado por Pessoa de Projetos")


def test_historico_sem_o_comeco_so_avisa_e_linha_incluida_depois_corrige():
    """The SAP keeps 99 versions per ORDER; a line added after creation starts later and is fine."""
    versoes = [_v(40, 2, 970.79, 100, quem="orcaview"), _v(41, 1, 485.395, 100)]
    cortado = decidir(versoes, _atual(versoes), AGORA, historico_cortado=True)
    assert (cortado.acao, cortado.motivo) == (Acao.AVISAR, "o começo do histórico foi apagado pelo SAP")
    assert _decidir(versoes).acao == Acao.CORRIGIR


@pytest.mark.parametrize(
    "versoes",
    [
        # 1 → 30 → 1: the second rescale already put the weight back.
        [_v(1, 1, 148.94, 4127.4, quem="orcaview"), _v(2, 30, 4468.2, 4127.4), _v(3, 1, 148.94, 4127.4)],
        # Same, with the SAP's rounding on the way back.
        [_v(1, 3, 100.0, 10, quem="orcaview"), _v(2, 7, 233.333, 10), _v(3, 3, 99.9999, 10)],
    ],
)
def test_peso_que_ja_voltou_nao_e_corrigido(versoes):
    """Review, 05/10/2026: "corrigir" a weight that is already right would PATCH every cycle."""
    d = _decidir(versoes)
    assert (d.acao, d.motivo) == (Acao.NADA, "o peso já é o de antes da troca")


def test_linha_do_sap_vira_versao():
    linha = {"LogInstanc": 2, "Quantity": 30, "Weight1": 4468.2, "LineTotal": 4127.4,
             "UpdateDate": date(2026, 10, 2), "UpdateTS": 93412, "USER_CODE": "ORCAVIEW ",
             "U_NAME": "orcaview"}
    v = versao_da_linha(linha, {"orcaview"})
    assert v.pela_integracao and v.momento == datetime(2026, 10, 2, 9, 34, 12)
    assert momento_do_sap(None, 93412) is None
    # No time is no moment — midnight would let the 3-minute wait pass at once.
    assert momento_do_sap(date(2026, 10, 2), None) is None
    assert momento_do_sap(date(2026, 10, 2), 999999) is None
