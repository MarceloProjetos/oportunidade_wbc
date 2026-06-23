"""Testes do agendador (janela comercial)."""

from datetime import datetime

import pytest

from scheduled_execution import (
    _parse_dias_semana,
    _parse_janela_horas,
    esta_na_janela_comercial,
)


def test_parse_janela_horas():
    assert _parse_janela_horas('7-18') == (7, 18)


def test_parse_janela_horas_invalid():
    with pytest.raises(ValueError):
        _parse_janela_horas('7')


def test_parse_dias_semana_range():
    assert _parse_dias_semana('mon-sat') == {0, 1, 2, 3, 4, 5}


def test_parse_dias_semana_list():
    assert _parse_dias_semana('mon,wed,fri') == {0, 2, 4}


def test_esta_na_janela_comercial_dentro():
    agora = datetime(2026, 6, 23, 10, 30)  # terça
    assert esta_na_janela_comercial(janela_horas='7-18', dias_semana='mon-sat', agora=agora)


def test_esta_na_janela_comercial_fora_horario():
    agora = datetime(2026, 6, 23, 20, 0)
    assert not esta_na_janela_comercial(janela_horas='7-18', dias_semana='mon-sat', agora=agora)


def test_esta_na_janela_comercial_domingo():
    agora = datetime(2026, 6, 21, 10, 0)  # domingo
    assert not esta_na_janela_comercial(janela_horas='7-18', dias_semana='mon-sat', agora=agora)
