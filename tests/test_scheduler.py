"""Scheduler tests (hour window and business days)."""

from datetime import date, datetime

import pytest

from config import parse_janela_horas
from feriados_br import eh_dia_util
from scripts.scheduled_execution import (
    _parse_dias_semana,
    esta_na_janela_comercial,
    pode_executar_carga,
)


def test_parse_janela_horas():
    assert parse_janela_horas('7-18') == (7, 18)


def test_parse_janela_horas_invalid():
    with pytest.raises(ValueError):
        parse_janela_horas('7')


def test_parse_dias_semana_range():
    assert _parse_dias_semana('mon-fri') == {0, 1, 2, 3, 4}


def test_parse_dias_semana_list():
    assert _parse_dias_semana('mon,wed,fri') == {0, 2, 4}


def test_esta_na_janela_comercial_dentro():
    agora = datetime(2026, 6, 23, 10, 30)
    assert esta_na_janela_comercial(janela_horas='7-18', agora=agora)


def test_esta_na_janela_comercial_fora_horario():
    agora = datetime(2026, 6, 23, 20, 0)
    assert not esta_na_janela_comercial(janela_horas='7-18', agora=agora)


def test_esta_na_janela_comercial_sabado():
    agora = datetime(2026, 6, 27, 10, 0)
    assert not esta_na_janela_comercial(janela_horas='7-18', agora=agora)


def test_esta_na_janela_comercial_domingo():
    agora = datetime(2026, 6, 28, 10, 0)
    assert not esta_na_janela_comercial(janela_horas='7-18', agora=agora)


def test_esta_na_janela_comercial_feriado():
    agora = datetime(2026, 1, 1, 10, 0)
    assert not esta_na_janela_comercial(janela_horas='7-18', agora=agora)


def test_pode_executar_startup_feriado_mesmo_fora_horario():
    agora = datetime(2026, 1, 1, 6, 0)
    assert not pode_executar_carga(ignorar_janela_horaria=True, agora=agora)


def test_pode_executar_startup_dia_util_fora_horario():
    agora = datetime(2026, 6, 23, 6, 0)
    assert pode_executar_carga(ignorar_janela_horaria=True, agora=agora)


def test_eh_dia_util_sabado():
    assert not eh_dia_util(date(2026, 6, 27))
