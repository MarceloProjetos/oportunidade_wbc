"""`controleproducao.config` on the single SIS `.env` (2026-09-28).

The suite's conftest already turns the `.env` off and clears the WBC/SIS names; every test
here sets what it needs with `monkeypatch.setenv` and builds `Settings(_env_file=None)`.
"""

from __future__ import annotations

import pytest

from controleproducao.config import Settings


@pytest.mark.parametrize(
    ("campo", "nome_wbc", "nome_sis"),
    [
        ("sl_username", "SL_USERNAME", "OP_SL_USERNAME"),
        ("sl_password", "SL_PASSWORD", "OP_SL_PASSWORD"),
        ("wbc_sql_host", "WBC_SQL_HOST", "SQL_HOST"),
        ("wbc_sql_host", "WBC_SQL_HOST", "SQLSERVER_HOST"),
        ("wbc_sql_database", "WBC_SQL_DATABASE", "SQL_DATABASE"),
        ("wbc_sql_username", "WBC_SQL_USERNAME", "SQL_USER"),
        ("wbc_sql_password", "WBC_SQL_PASSWORD", "SQL_PASSWORD"),
        ("hana_host", "HANA_HOST", "SAP_HOST"),
        ("hana_username", "HANA_USERNAME", "SAP_USER"),
        ("hana_password", "HANA_PASSWORD", "SAP_PASSWORD"),
    ],
)
def test_credencial_cai_no_nome_do_sis_quando_o_do_wbc_esta_ausente(monkeypatch, campo, nome_wbc, nome_sis):
    monkeypatch.delenv(nome_wbc, raising=False)
    monkeypatch.setenv(nome_sis, "do-sis")
    assert getattr(Settings(_env_file=None), campo) == "do-sis"


def test_nome_do_wbc_vence_o_do_sis_quando_os_dois_existem(monkeypatch):
    monkeypatch.setenv("SL_USERNAME", "do-wbc")
    monkeypatch.setenv("OP_SL_USERNAME", "do-sis")
    assert Settings(_env_file=None).sl_username == "do-wbc"


def test_credencial_vazia_no_wbc_cai_no_sis(monkeypatch):
    """`SL_USERNAME=` (empty line in the shared .env) must not win as "" over `OP_SL_USERNAME`."""
    monkeypatch.setenv("SL_USERNAME", "")
    monkeypatch.setenv("OP_SL_USERNAME", "do-sis")
    assert Settings(_env_file=None).sl_username == "do-sis"


@pytest.mark.parametrize(
    ("campo", "nome_sis", "valor", "esperado"),
    [("wbc_sql_port", "SQL_PORT", "1444", 1444), ("hana_port", "SAP_PORT", "30041", 30041)],
)
def test_porta_tambem_cai_no_nome_do_sis(monkeypatch, campo, nome_sis, valor, esperado):
    monkeypatch.setenv(nome_sis, valor)
    assert getattr(Settings(_env_file=None), campo) == esperado


def test_hana_schema_e_a_company_de_escrita(monkeypatch):
    """Reads (ORDR/OWOR) decide writes, so they must hit the Service Layer company — bug of 21/09."""
    monkeypatch.setenv("SL_COMPANY_DB", "SBOTESTE")
    monkeypatch.setenv("HANA_SCHEMA", "OUTRO")  # the worker's read schema: ignored here
    s = Settings(_env_file=None)
    assert s.hana_schema == "SBOTESTE"
    assert s.hana_schema == s.sl_company_db
    assert "hana_schema" not in Settings.model_fields


def test_campos_reservados_do_wbcpython_nao_existem_mais():
    for nome in (
        "painel_senha", "painel_host", "tracking_db_url", "worker_interval_seconds",
        "meses_de_janela", "meses_de_janela_dirigida", "worker_horario_inicio",
        "worker_horario_fim", "worker_dias_de_trabalho", "wbc_homolog_company_db",
        "wbc_homolog_tracking_db_url", "wbc_prod_tracking_db_url",
    ):
        assert nome not in Settings.model_fields, nome


def test_defaults_do_servico_web():
    s = Settings(_env_file=None)
    assert s.cp_host == "127.0.0.1"
    assert s.cp_porta == 8080
    assert s.cp_log_file == "logs/controleproducao.log"
    assert s.painel_porta == 8079
    assert s.wbc_painel_url == ""
    assert s.os_api_key.get_secret_value() == ""


def test_servico_web_le_as_variaveis_cp(monkeypatch):
    monkeypatch.setenv("CP_HOST", "0.0.0.0")
    monkeypatch.setenv("CP_PORTA", "8090")
    monkeypatch.setenv("CP_LOG_FILE", "logs/x.log")
    monkeypatch.setenv("WBC_PAINEL_URL", "http://painel/")
    s = Settings(_env_file=None)
    assert (s.cp_host, s.cp_porta, s.cp_log_file, s.wbc_painel_url) == ("0.0.0.0", 8090, "logs/x.log", "http://painel/")


def test_os_api_key_vem_do_ambiente_e_nao_vaza_no_repr(monkeypatch):
    monkeypatch.setenv("OS_API_KEY", "chave-secreta")
    s = Settings(_env_file=None)
    assert s.os_api_key.get_secret_value() == "chave-secreta"
    assert "chave-secreta" not in repr(s)


def test_defaults_das_credenciais_ficam_como_antes():
    s = Settings(_env_file=None)
    assert s.sl_username == "" and s.sl_password == ""
    assert s.wbc_sql_port == 1433 and s.wbc_sql_database == "WBCCAD"
    assert s.hana_port == 30015 and s.hana_schema_legado == "SBOALTAMIRAPROD"
    assert s.sl_company_db == "SBOALTAMIRAHOMOLOG" and s.wbc_production_company_db == "SBOALTAMIRAPROD"


def test_construtor_por_nome_do_campo_continua_valendo():
    """`populate_by_name`: tests and the CLI build Settings by attribute, not by env alias."""
    s = Settings(_env_file=None, sl_username="u", hana_host="h", wbc_sql_host="w")
    assert (s.sl_username, s.hana_host, s.wbc_sql_host) == ("u", "h", "w")
