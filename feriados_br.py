"""Brazilian national holidays for scheduler business-day checks (2024-2030).

⚠️ The table is FINITE (see ``HOLIDAY_YEAR_END``). Outside that range
``is_national_holiday`` would silently return ``False`` and the scheduler would run a
load on a holiday believing it to be a business day — which is why ``is_business_day``
**warns** when a date falls outside coverage (see there). To extend: raise
``HOLIDAY_YEAR_END`` (dates are computed, moveable feasts included).
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
    (11, 20),   # Consciência Negra — national holiday since 2024 (Lei 14.759/2023);
                # the table also starts in 2024, so it applies to every year covered.
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
    """True if ``d`` is a national holiday. Outside 2024-2030 always False — see ``covers``."""
    return d in national_holidays()


def covers(d: date) -> bool:
    """True if ``d`` falls within the range the table knows about."""
    return HOLIDAY_YEAR_START <= d.year <= HOLIDAY_YEAR_END


def is_business_day(d: date) -> bool:
    """Mon-Fri, excluding national holidays.

    Outside the table's coverage it **logs a warning** and treats the date as an
    ordinary business day (only weekends are excluded). Without the warning,
    2031-01-01 would silently be a "business day" and the scheduler would run the load
    on New Year's Day — the kind of bug that only surfaces years later, on the worst
    possible day. The warning shows up in the scheduler log and in ``/status``.
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
