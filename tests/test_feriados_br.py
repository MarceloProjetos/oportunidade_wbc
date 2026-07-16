"""Testes do calendário de feriados nacionais."""

from datetime import date

import feriados_br
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


# ============ Cobertura finita da tabela (2026-07-16) ============
# `is_business_day(date(2031,1,1))` devolvia True em SILÊNCIO: 2031 está fora da tabela,
# então o Ano-Novo virava "dia útil" e o agendador rodaria a carga. Bug que só apareceria
# anos depois, no pior dia possível. Agora avisa.

def test_covers_delimita_a_tabela():
    assert feriados_br.covers(date(2024, 1, 1)) is True
    assert feriados_br.covers(date(2030, 12, 31)) is True
    assert feriados_br.covers(date(2031, 1, 1)) is False
    assert feriados_br.covers(date(2023, 12, 31)) is False


def test_fora_da_cobertura_avisa(caplog):
    """O ponto todo: falhar BARULHENTO. Sem o aviso, ninguém descobre até o incidente."""
    import logging
    with caplog.at_level(logging.WARNING):
        feriados_br.is_business_day(date(2031, 1, 1))   # Ano-Novo de 2031: quarta
    assert 'não cobre' in caplog.text
    assert '2031-01-01' in caplog.text
    assert 'HOLIDAY_YEAR_END' in caplog.text            # diz como consertar


def test_dentro_da_cobertura_nao_polui_o_log(caplog):
    """O caminho normal (99,99% das chamadas) não pode logar nada."""
    import logging
    with caplog.at_level(logging.WARNING):
        feriados_br.is_business_day(date(2026, 6, 23))
        feriados_br.is_business_day(date(2026, 1, 1))
    assert caplog.text == ''


def test_fim_de_semana_ainda_vale_fora_da_cobertura():
    """Fora da tabela ainda dá p/ confiar no fim de semana — só os feriados se perdem."""
    assert feriados_br.is_business_day(date(2031, 1, 4)) is False   # sábado
    assert feriados_br.is_business_day(date(2031, 1, 6)) is True    # segunda (sem feriado)


def test_consciencia_negra_em_todos_os_anos_cobertos():
    """Virou feriado fixo (Lei 14.759/2023) e a tabela começa em 2024 — o `if
    year >= 2024` era sempre verdadeiro. A refatoração não pode ter perdido a data."""
    for ano in range(feriados_br.HOLIDAY_YEAR_START, feriados_br.HOLIDAY_YEAR_END + 1):
        assert feriados_br.is_national_holiday(date(ano, 11, 20)), ano
