"""Feriados nacionais do Brasil (dias úteis para o agendador).

Inclui feriados fixos e móveis (Carnaval, Sexta-feira Santa, Corpus Christi)
conforme o calendário federal, pré-calculados até ``FERIADOS_ANO_FIM`` (2030).

Referência: Lei 9.093/1995; Lei 14.759/2023 (20/11 a partir de 2024).
"""

from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache

FERIADOS_ANO_INICIO = 2024
FERIADOS_ANO_FIM = 2030

# Feriados em data fixa (mês, dia)
_FERIADOS_FIXOS = (
    (1, 1),    # Confraternização Universal
    (4, 21),   # Tiradentes
    (5, 1),    # Dia do Trabalho
    (9, 7),    # Independência
    (10, 12),  # Nossa Senhora Aparecida
    (11, 2),   # Finados
    (11, 15),  # Proclamação da República
    (12, 25),  # Natal
)

# Dia da Consciência Negra — feriado nacional a partir de 2024 (Lei 14.759/2023)
_CONSCIENCIA_NEGRA = (11, 20)
_CONSCIENCIA_NEGRA_DESDE = 2024


def _pascoa(ano: int) -> date:
    """Domingo de Páscoa (algoritmo de Meeus/Jones/Butcher)."""
    a = ano % 19
    b = ano // 100
    c = ano % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    el = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * el) // 451
    mes = (h + el - 7 * m + 114) // 31
    dia = ((h + el - 7 * m + 114) % 31) + 1
    return date(ano, mes, dia)


def _feriados_do_ano(ano: int) -> set[date]:
    feriados = {date(ano, mes, dia) for mes, dia in _FERIADOS_FIXOS}
    if ano >= _CONSCIENCIA_NEGRA_DESDE:
        feriados.add(date(ano, *_CONSCIENCIA_NEGRA))

    pascoa = _pascoa(ano)
    feriados.add(pascoa - timedelta(days=48))  # Segunda de Carnaval
    feriados.add(pascoa - timedelta(days=47))  # Terça de Carnaval
    feriados.add(pascoa - timedelta(days=2))    # Sexta-feira Santa
    feriados.add(pascoa + timedelta(days=60))  # Corpus Christi
    return feriados


@lru_cache(maxsize=1)
def feriados_nacionais() -> frozenset[date]:
    """Conjunto imutável de feriados nacionais entre ``FERIADOS_ANO_INICIO`` e ``FERIADOS_ANO_FIM``."""
    todos: set[date] = set()
    for ano in range(FERIADOS_ANO_INICIO, FERIADOS_ANO_FIM + 1):
        todos |= _feriados_do_ano(ano)
    return frozenset(todos)


def eh_feriado_nacional(d: date) -> bool:
    """``True`` se ``d`` é feriado nacional brasileiro (no calendário pré-carregado)."""
    return d in feriados_nacionais()


def eh_dia_util(d: date) -> bool:
    """``True`` se ``d`` é dia útil: segunda a sexta e fora do calendário de feriados."""
    return d.weekday() < 5 and not eh_feriado_nacional(d)
