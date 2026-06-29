"""Scheduler tests (hour window and business days)."""

from datetime import date, datetime

import pytest

from config import parse_janela_horas
from feriados_br import eh_dia_util
from scripts.scheduled_execution import (
    can_run_load,
    is_within_commercial_window,
)


def test_parse_janela_horas():
    assert parse_janela_horas('7-18') == (7, 18)


def test_parse_janela_horas_invalid():
    with pytest.raises(ValueError):
        parse_janela_horas('7')


def test_dentro_da_janela():
    now = datetime(2026, 6, 23, 10, 30)
    assert is_within_commercial_window(janela_horas='7-18', now=now)


def test_fora_do_horario():
    now = datetime(2026, 6, 23, 20, 0)
    assert not is_within_commercial_window(janela_horas='7-18', now=now)


def test_sabado():
    now = datetime(2026, 6, 27, 10, 0)
    assert not is_within_commercial_window(janela_horas='7-18', now=now)


def test_domingo():
    now = datetime(2026, 6, 28, 10, 0)
    assert not is_within_commercial_window(janela_horas='7-18', now=now)


def test_feriado():
    now = datetime(2026, 1, 1, 10, 0)
    assert not is_within_commercial_window(janela_horas='7-18', now=now)


def test_startup_feriado_mesmo_fora_horario():
    now = datetime(2026, 1, 1, 6, 0)
    assert not can_run_load(ignore_hour_window=True, now=now)


def test_startup_dia_util_fora_horario():
    now = datetime(2026, 6, 23, 6, 0)
    assert can_run_load(ignore_hour_window=True, now=now)


def test_eh_dia_util_sabado():
    assert not eh_dia_util(date(2026, 6, 27))
