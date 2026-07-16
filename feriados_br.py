"""Brazilian national holidays for scheduler business-day checks (2024-2030).

⚠️ A tabela é FINITA (ver ``HOLIDAY_YEAR_END``). Fora do intervalo, ``is_national_holiday``
devolveria ``False`` em silêncio e o agendador rodaria carga no feriado achando que é dia
útil — por isso ``is_business_day`` **avisa** quando a data sai da cobertura (ver lá).
Estender = subir ``HOLIDAY_YEAR_END`` (as datas são calculadas, inclusive as móveis).
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from functools import lru_cache

logger = logging.getLogger(__name__)

HOLIDAY_YEAR_START = 2024
HOLIDAY_YEAR_END = 2030

_FIXED_HOLIDAYS = (
    (1, 1), (4, 21), (5, 1), (9, 7), (10, 12), (11, 2), (11, 15), (12, 25),
    (11, 20),   # Consciência Negra — feriado nacional desde 2024 (Lei 14.759/2023),
                # e a tabela começa em 2024, então vale para todos os anos cobertos.
)


def _easter_sunday(year: int) -> date:
    """Easter Sunday (Meeus/Jones/Butcher algorithm)."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    el = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * el) // 451
    month = (h + el - 7 * m + 114) // 31
    day = ((h + el - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _holidays_for_year(year: int) -> set[date]:
    holidays = {date(year, month, day) for month, day in _FIXED_HOLIDAYS}
    easter = _easter_sunday(year)
    holidays.add(easter - timedelta(days=48))  # Carnival Monday
    holidays.add(easter - timedelta(days=47))  # Carnival Tuesday
    holidays.add(easter - timedelta(days=2))   # Good Friday
    holidays.add(easter + timedelta(days=60))  # Corpus Christi
    return holidays


@lru_cache(maxsize=1)
def national_holidays() -> frozenset[date]:
    all_dates: set[date] = set()
    for year in range(HOLIDAY_YEAR_START, HOLIDAY_YEAR_END + 1):
        all_dates |= _holidays_for_year(year)
    return frozenset(all_dates)


def is_national_holiday(d: date) -> bool:
    """True se ``d`` é feriado nacional. Fora de 2024-2030, sempre False — ver ``covers``."""
    return d in national_holidays()


def covers(d: date) -> bool:
    """True se ``d`` está dentro do intervalo que a tabela conhece."""
    return HOLIDAY_YEAR_START <= d.year <= HOLIDAY_YEAR_END


def is_business_day(d: date) -> bool:
    """Seg-sex, exceto feriados nacionais.

    Fora da cobertura da tabela, **loga um aviso** e trata como dia útil comum (só o
    fim de semana é excluído). Sem o aviso, 01/01/2031 seria "dia útil" em silêncio e o
    agendador rodaria a carga no Ano-Novo — o tipo de bug que só aparece anos depois, no
    pior dia possível. O warning aparece no log do agendador e no ``/status``.
    """
    if not covers(d):
        logger.warning(
            "feriados_br não cobre %s (tabela vai de %s a %s): tratando como dia útil "
            "comum — feriados desse ano NÃO serão respeitados. Atualize HOLIDAY_YEAR_END.",
            d.isoformat(), HOLIDAY_YEAR_START, HOLIDAY_YEAR_END,
        )
    return d.weekday() < 5 and not is_national_holiday(d)


# Backward-compatible aliases
FERIADOS_ANO_INICIO = HOLIDAY_YEAR_START
FERIADOS_ANO_FIM = HOLIDAY_YEAR_END
feriados_nacionais = national_holidays
eh_feriado_nacional = is_national_holiday
eh_dia_util = is_business_day
