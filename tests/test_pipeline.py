"""Testes do pipeline e modos de execução."""

from extract_sap_to_supabase import EXECUTION_MODES, main


def test_main_rejects_upsert_mode():
    assert main(execution_mode='upsert') is False


def test_main_rejects_invalid_mode():
    assert main(execution_mode='invalid') is False


def test_execution_modes_tuple():
    assert set(EXECUTION_MODES) == {'snapshot', 'insert'}
