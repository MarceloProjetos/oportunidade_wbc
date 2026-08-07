"""Centralized configuration tests."""


import pytest

from config import EXECUTION_MODES, SAP_PORT_DEFAULT, get_settings, reset_settings


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


def test_janela_horas_validated_at_load(monkeypatch):
    monkeypatch.setenv('JANELA_HORAS', 'bad')
    reset_settings()
    with pytest.raises(ValueError):
        get_settings()


def test_sql_enrichment_view_default(monkeypatch):
    monkeypatch.delenv('SQL_ENRICHMENT_VIEW', raising=False)
    reset_settings()
    assert get_settings().sql_enrichment_view == 'WBCCAD.dbo.INTEGRACAO_ORCSIT'


def test_sql_enrichment_view_from_env(monkeypatch):
    monkeypatch.setenv('SQL_ENRICHMENT_VIEW', 'MYDB.dbo.MY_VIEW')
    reset_settings()
    assert get_settings().sql_enrichment_view == 'MYDB.dbo.MY_VIEW'


# ───────────────────────── Windows Update (windows_update.py) ─────────────────────────

def test_wu_defaults(monkeypatch):
    for var in ('WU_ENABLED', 'WU_DELAY_START_S', 'WU_VARREDURA_MAX_D', 'WU_COLETA_TIMEOUT_S'):
        monkeypatch.delenv(var, raising=False)
    reset_settings()
    s = get_settings()
    assert s.wu_enabled is True
    assert s.wu_delay_start_s == 300.0
    assert s.wu_varredura_max_d == 7.0
    assert s.wu_coleta_timeout_s == 120.0


def test_wu_varredura_max_d_e_float_nao_int(monkeypatch):
    """7.9 tem de continuar 7.9: truncar faria o PowerShell da coleta usar 7 e divergir
    em silêncio do limite reaplicado no Python."""
    monkeypatch.setenv('WU_VARREDURA_MAX_D', '7.9')
    reset_settings()
    assert get_settings().wu_varredura_max_d == 7.9


def test_wu_env_invalido_cai_no_default_e_nao_derruba_a_api(monkeypatch):
    """`.env` torto não pode impedir a API de subir — e é o que barra injeção de
    PowerShell pelo WU_VARREDURA_MAX_D (ver windows_update._PS_COLETA)."""
    monkeypatch.setenv('WU_VARREDURA_MAX_D', 'lixo; Remove-Item C:\\')
    monkeypatch.setenv('WU_DELAY_START_S', 'nao-e-numero')
    reset_settings()
    s = get_settings()   # não levanta
    assert s.wu_varredura_max_d == 7.0
    assert s.wu_delay_start_s == 300.0


@pytest.mark.parametrize(('valor', 'esperado'), [
    ('false', False), ('0', False), ('no', False), ('', True),   # vazio = default
    ('true', True), ('1', True), ('sim', True),
])
def test_wu_enabled_bool(valor, esperado, monkeypatch):
    monkeypatch.setenv('WU_ENABLED', valor)
    reset_settings()
    assert get_settings().wu_enabled is esperado


# ============ Ordem de Produção: escrita no SAP (2026-08-07) ============

def test_op_sl_nasce_desligado_e_apontando_para_producao(monkeypatch):
    """O default TEM de ser desligado: a base alvo é a de PRODUÇÃO."""
    for chave in ('OP_SL_ENABLED', 'OP_SL_COMPANY_DB', 'OP_SL_SERVER', 'OP_SL_PORT'):
        monkeypatch.delenv(chave, raising=False)
    reset_settings()
    s = get_settings()
    assert s.op_sl_enabled is False
    assert s.op_sl_company_db == 'SBOALTAMIRAPROD'
    assert s.op_sl_base_url == 'https://sapbusinessonehana-vm:50000/b1s/v1'


def test_op_sl_ready_exige_switch_e_credencial(monkeypatch):
    monkeypatch.setenv('OP_SL_ENABLED', 'true')
    monkeypatch.setenv('OP_SL_USERNAME', 'u')
    monkeypatch.delenv('OP_SL_PASSWORD', raising=False)
    reset_settings()
    assert get_settings().op_sl_ready() is False

    monkeypatch.setenv('OP_SL_PASSWORD', 'p')
    reset_settings()
    assert get_settings().op_sl_ready() is True

    monkeypatch.setenv('OP_SL_ENABLED', 'false')
    reset_settings()
    assert get_settings().op_sl_ready() is False


def test_credencial_ausente_nao_derruba_o_get_settings(monkeypatch):
    """Diferente do config do repo web, que levanta no import: aqui a feature é
    opcional e um ValueError mataria a API inteira, /health incluído."""
    monkeypatch.setenv('OP_SL_ENABLED', 'true')
    monkeypatch.delenv('OP_SL_PASSWORD', raising=False)
    reset_settings()
    assert get_settings().op_sl_password is None   # não levanta


def test_op_status_permitidos_default():
    reset_settings()
    assert get_settings().op_status_permitidos == ('boposReleased', 'boposClosed')


@pytest.mark.parametrize(('bruto', 'esperado'), [
    ('boposClosed', ('boposClosed',)),
    ('boposclosed, boposreleased', ('boposClosed', 'boposReleased')),   # case-insensitive
    ('boposClosed,boposClosed', ('boposClosed',)),                      # sem duplicata
    ('boposClosed,lixo', ('boposClosed',)),                             # descarta o inválido
    ('lixo,boposInventado', ()),                                        # nada sobra
    ('', ()),
])
def test_op_status_permitidos_so_deixa_passar_codigo_conhecido(monkeypatch, bruto, esperado):
    """Essa tupla vira corpo de PATCH na base de PRODUÇÃO — typo no `.env` não pode
    chegar ao SAP. Sobrar nada é resposta válida: bloqueia toda escrita."""
    monkeypatch.setenv('OP_STATUS_PERMITIDOS', bruto)
    reset_settings()
    assert get_settings().op_status_permitidos == esperado


def test_op_status_permitidos_nao_aceita_cancelar_por_default(monkeypatch):
    monkeypatch.delenv('OP_STATUS_PERMITIDOS', raising=False)
    reset_settings()
    assert 'boposCancelled' not in get_settings().op_status_permitidos


def test_op_porta_torta_no_env_cai_no_default(monkeypatch):
    monkeypatch.setenv('OP_SL_PORT', 'nao-e-numero')
    reset_settings()
    assert get_settings().op_sl_port == 50000


def test_op_timeout_default_tem_connect_curto_e_read_longo():
    reset_settings()
    assert get_settings().op_sl_timeout == (5.0, 30.0)
