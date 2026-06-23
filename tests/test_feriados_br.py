"""Testes do calendário de feriados nacionais."""

from datetime import date

from feriados_br import (
    FERIADOS_ANO_FIM,
    eh_dia_util,
    eh_feriado_nacional,
    feriados_nacionais,
)


def test_ano_novo_2026():
    assert eh_feriado_nacional(date(2026, 1, 1))


def test_consciencia_negra_2024():
    assert eh_feriado_nacional(date(2024, 11, 20))


def test_carnaval_2025():
    assert eh_feriado_nacional(date(2025, 3, 4))  # terça de carnaval


def test_sexta_santa_2025():
    assert eh_feriado_nacional(date(2025, 4, 18))


def test_dia_util_terca_comum():
    assert eh_dia_util(date(2026, 6, 23))


def test_sabado_nao_util():
    assert not eh_dia_util(date(2026, 6, 27))


def test_domingo_nao_util():
    assert not eh_dia_util(date(2026, 6, 28))


def test_feriado_em_semana_nao_util():
    assert not eh_dia_util(date(2026, 1, 1))  # ano novo cai em quinta


def test_calendario_ate_2030():
    assert any(d.year == FERIADOS_ANO_FIM for d in feriados_nacionais())
    assert not eh_feriado_nacional(date(2031, 1, 1))
