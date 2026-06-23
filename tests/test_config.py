"""Testes de configuração centralizada."""

import os

from config import EXECUTION_MODES, SAP_PORT_DEFAULT, Settings, get_settings, reset_settings


def test_sap_port_default():
    assert SAP_PORT_DEFAULT == 30015


def test_execution_modes_exclude_upsert():
    assert 'upsert' not in EXECUTION_MODES
    assert EXECUTION_MODES == ('snapshot', 'insert')


def test_settings_sql_aliases(monkeypatch):
    monkeypatch.setenv('SQLSERVER_HOST', 'sql-alias')
    monkeypatch.setenv('SQLSERVER_PORT', '1444')
    monkeypatch.setenv('SQLSERVER_USER', 'u')
    monkeypatch.setenv('SQLSERVER_PASSWORD', 'p')
    monkeypatch.setenv('SQLSERVER_DATABASE', 'DB')
    reset_settings()

    s = get_settings()
    assert s.sql_host == 'sql-alias'
    assert s.sql_port == 1444
    assert s.sql_database == 'DB'
    assert s.sql_ready()


def test_supabase_write_key_prefers_service_role(monkeypatch):
    monkeypatch.setenv('SUPABASE_URL', 'https://x.supabase.co')
    monkeypatch.setenv('SUPABASE_KEY', 'anon-key')
    monkeypatch.setenv('SUPABASE_SERVICE_ROLE_KEY', 'service-key')
    reset_settings()

    assert get_settings().supabase_write_key == 'service-key'


def test_from_env_intervalo_piso(monkeypatch):
    monkeypatch.setenv('INTERVALO_MINUTOS', '2')
    reset_settings()
    assert get_settings().intervalo_minutos == 5

    monkeypatch.setenv('INTERVALO_MINUTOS', '90')
    reset_settings()
    assert get_settings().intervalo_minutos == 90


def test_sap_database_optional_empty_string(monkeypatch):
    monkeypatch.setenv('SAP_DATABASE', '')
    reset_settings()
    assert get_settings().sap_database is None
