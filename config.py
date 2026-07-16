"""Centralized pipeline configuration (environment variables and defaults)."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# SAP HANA
SAP_PORT_DEFAULT = 30015
SAP_CONNECT_TIMEOUT_MS = 15_000
SAP_COMM_TIMEOUT_MS = 60_000

# SQL Server
SQL_PORT_DEFAULT = 1433
SQL_DATABASE_DEFAULT = 'WBCCAD'
SQL_ENRICHMENT_VIEW_DEFAULT = 'WBCCAD.dbo.INTEGRACAO_ORCSIT'
SQL_LOGIN_TIMEOUT_S = 10

# Supabase
SUPABASE_TIMEOUT_S = 120
TABLE_NAME_DEFAULT = 'oportunidades'
SITCOD_DOMAIN_TABLE_DEFAULT = 'situacoes_orcamento'

# Pipeline
RETRY_ATTEMPTS = 3
RETRY_BASE_DELAY_S = 2.0
INSERT_BATCH_SIZE = 500
MESES_RETROATIVOS = 6
FILTRO_COLUNA_DATA = 'CreateDate'

# Sync log
SYNC_LOG_TABLE_NAME = 'sincronizacao_log'
SYNC_LOG_MAX_REGISTROS = 6

EXECUTION_MODES = ('snapshot', 'insert')

# Ordens de Serviço — on-demand pipeline, keyed by N_PED. Mirrors the CONSOLIDATED
# HANA view VW_OS_INTEGRACAO (54 columns: OS + tree/structure + quote + process flags)
# into a SINGLE Supabase table. It replaced the separate mirrors for engineering OS,
# WBC tree and print views (2026-07-14 consolidation). The view keys on "N_PED" (with
# underscore) — unlike the older "NPED".
OS_SAP_VIEW_NAME_DEFAULT = 'VW_OS_INTEGRACAO'
OS_TABLE_NAME_DEFAULT = 'vw_os_integracao'
OS_SYNC_LOG_TABLE_DEFAULT = 'sincronizacao_log_os_integracao'
OS_EXECUTION_MODE_DEFAULT = 'replace_nped'
OS_EXECUTION_MODES = ('replace_nped', 'insert')
OS_INSERT_BATCH_SIZE_DEFAULT = 500
OS_SYNC_LOG_MAX_REGISTROS = 100

# HTTP API that triggers the OS sync (api.py)
OS_API_HOST_DEFAULT = '0.0.0.0'
OS_API_PORT_DEFAULT = 8077

# Monitor for the "Integração WBC" scheduled task (Windows Task Scheduler).
# The PowerShell script ``monitor_wbc_task.ps1`` (scheduled every 10 min) queries the
# task and writes its state to ``WBC_TASK_STATE_FILE``. The API only *reads* that file
# and exposes it on ``/status`` — it never spawns a subprocess or queries the task per
# request. If the file grows older than ``WBC_TASK_STALE_MIN``, the monitor itself may
# have died, and ``/status`` flags that as an alert (503 under ``?strict=1``).
WBC_TASK_NAME_DEFAULT = 'Integração WBC'
WBC_TASK_STATE_FILE_DEFAULT = 'state/wbc_task_state.json'
WBC_TASK_STALE_MIN_DEFAULT = 25

# Windows Update / pending reboot (``windows_update.py``; full plan in
# ``../SAP_RDP/docs/PLANO_WINDOWS_UPDATE.md``). Every number here comes from MEASUREMENT
# on the two real servers, not from an estimate.
WU_ENABLED_DEFAULT = True
# The thread sleeps this long before collecting: it gives boot time to settle (.11 comes
# up around 06:12) and the process's 1st search is the expensive one (30s cold) — better
# to pay for it with nobody waiting.
WU_DELAY_START_S_DEFAULT = 300.0
# A scan older than this makes the pending count a LIE, so it becomes "don't know". The
# .12 was 610.8 days stale and still confidently returned "0 pending" in 22.5s.
WU_VARREDURA_MAX_D_DEFAULT = 7.0
# Collection ceiling: measured 3.1s here on .11 (22.5s on .12, 30s cold). 120s leaves
# headroom without hanging.
WU_COLETA_TIMEOUT_S_DEFAULT = 120.0

_BOOL_VERDADEIRO = ('1', 'true', 'yes', 'on', 'sim')

# Scheduler
INTERVALO_MINUTOS_DEFAULT = 30
INTERVALO_PISO_MIN = 5
JANELA_HORAS_DEFAULT = '7-18'
EXECUTION_MODE_DEFAULT = 'snapshot'

# JANELA_HORAS must match start-end hour range (e.g. 7-18)
JANELA_HORAS_RE = re.compile(r'^\d{1,2}-\d{1,2}$')


def parse_janela_horas(expr: str) -> tuple[int, int]:
    """Parse inclusive hour window from JANELA_HORAS (e.g. '7-18')."""
    expr = expr.strip()
    if not JANELA_HORAS_RE.fullmatch(expr):
        msg = f"Invalid JANELA_HORAS: {expr!r} (expected e.g. '7-18')"
        logger.error('[CONFIG] %s', msg)
        raise ValueError(msg)
    h_start, h_end = (int(x) for x in expr.split('-', 1))
    if not (0 <= h_start <= 23 and 0 <= h_end <= 23 and h_start <= h_end):
        msg = f"Invalid JANELA_HORAS range: {expr!r}"
        logger.error('[CONFIG] %s', msg)
        raise ValueError(msg)
    return h_start, h_end


def _env(*keys: str) -> Optional[str]:
    """Return first non-empty env value among keys."""
    for key in keys:
        value = os.getenv(key)
        if value:
            return value
    return None


def _env_bool(key: str, default: bool) -> bool:
    """Bool from the environment; empty/missing falls back to the default."""
    bruto = os.getenv(key)
    if not bruto or not bruto.strip():
        return default
    return bruto.strip().lower() in _BOOL_VERDADEIRO


def _env_float(key: str, default: float) -> float:
    """Float from the environment; garbage falls back to the DEFAULT instead of taking
    the API down at boot.

    Unlike the ``int(os.getenv(...))`` used by the older fields — there a malformed
    ``.env`` raises ``ValueError`` inside ``get_settings()`` and the service never starts.

    This is not just robustness: ``WU_VARREDURA_MAX_D`` is **interpolated into the
    PowerShell collection script** (``windows_update._PS_COLETA``), and this function is
    what guarantees only a numeric literal reaches it. Replacing it with a raw read would
    open PowerShell injection via ``.env`` — a test pins this
    (``test_limite_malicioso_no_env_nao_injeta_powershell``).
    """
    bruto = os.getenv(key)
    if not bruto or not bruto.strip():
        return default
    try:
        return float(bruto.strip())
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """Environment snapshot for the ETL pipeline."""

    sap_host: Optional[str]
    sap_port: int
    sap_user: Optional[str]
    sap_password: Optional[str]
    sap_database: Optional[str]
    sap_schema: Optional[str]
    sap_view_name: Optional[str]

    supabase_url: Optional[str]
    supabase_key: Optional[str]
    supabase_service_role_key: Optional[str]
    table_name: str
    supabase_timeout_s: float
    sync_log_table_name: str
    sitcod_domain_table: str

    sql_host: Optional[str]
    sql_port: int
    sql_user: Optional[str]
    sql_password: Optional[str]
    sql_database: str
    sql_driver: Optional[str]
    sql_enrichment_view: str

    # Ordens de Serviço (consolidated view VW_OS_INTEGRACAO)
    os_sap_view_name: str
    os_table_name: str
    os_sync_log_table: str
    os_execution_mode: str
    os_insert_batch_size: int
    os_api_key: Optional[str]
    os_api_host: str
    os_api_port: int

    # Monitor for the "Integração WBC" scheduled task (Windows Task Scheduler;
    # do NOT confuse with the WBC tree, which now comes inside VW_OS_INTEGRACAO)
    wbc_task_name: str
    wbc_task_state_file: str
    wbc_task_stale_min: int

    # Windows Update (expensive collection, in the background — see windows_update.py)
    wu_enabled: bool           # WU_ENABLED — turns the collection thread off
    wu_delay_start_s: float    # WU_DELAY_START_S — thread wait after the API starts
    wu_varredura_max_d: float  # WU_VARREDURA_MAX_D — max scan age for publishing the count
    wu_coleta_timeout_s: float  # WU_COLETA_TIMEOUT_S — ceiling for the collection powershell

    intervalo_minutos: int
    janela_horas: str
    execution_mode: str

    @classmethod
    def from_env(cls) -> Settings:
        janela_horas = os.getenv('JANELA_HORAS', JANELA_HORAS_DEFAULT)
        parse_janela_horas(janela_horas)

        return cls(
            sap_host=os.getenv('SAP_HOST'),
            sap_port=int(os.getenv('SAP_PORT', SAP_PORT_DEFAULT)),
            sap_user=os.getenv('SAP_USER'),
            sap_password=os.getenv('SAP_PASSWORD'),
            sap_database=os.getenv('SAP_DATABASE') or None,
            sap_schema=os.getenv('SAP_SCHEMA'),
            sap_view_name=os.getenv('SAP_VIEW_NAME'),
            supabase_url=os.getenv('SUPABASE_URL'),
            supabase_key=os.getenv('SUPABASE_KEY'),
            supabase_service_role_key=os.getenv('SUPABASE_SERVICE_ROLE_KEY'),
            table_name=os.getenv('TABLE_NAME', TABLE_NAME_DEFAULT),
            supabase_timeout_s=float(os.getenv('SUPABASE_TIMEOUT_S', SUPABASE_TIMEOUT_S)),
            sync_log_table_name=os.getenv('SYNC_LOG_TABLE_NAME', SYNC_LOG_TABLE_NAME),
            sitcod_domain_table=os.getenv('SITCOD_DOMAIN_TABLE', SITCOD_DOMAIN_TABLE_DEFAULT),
            sql_host=_env('SQLSERVER_HOST', 'SQL_HOST'),
            sql_port=int(_env('SQLSERVER_PORT', 'SQL_PORT') or SQL_PORT_DEFAULT),
            sql_user=_env('SQLSERVER_USER', 'SQL_USER'),
            sql_password=_env('SQLSERVER_PASSWORD', 'SQL_PASSWORD'),
            sql_database=_env('SQLSERVER_DATABASE', 'SQL_DATABASE') or SQL_DATABASE_DEFAULT,
            sql_driver=_env('SQLSERVER_DRIVER', 'SQL_DRIVER'),
            sql_enrichment_view=os.getenv('SQL_ENRICHMENT_VIEW', SQL_ENRICHMENT_VIEW_DEFAULT),
            os_sap_view_name=os.getenv('OS_SAP_VIEW_NAME', OS_SAP_VIEW_NAME_DEFAULT),
            os_table_name=os.getenv('OS_TABLE_NAME', OS_TABLE_NAME_DEFAULT),
            os_sync_log_table=os.getenv('OS_SYNC_LOG_TABLE_NAME', OS_SYNC_LOG_TABLE_DEFAULT),
            os_execution_mode=os.getenv('OS_EXECUTION_MODE', OS_EXECUTION_MODE_DEFAULT),
            os_insert_batch_size=int(
                os.getenv('OS_INSERT_BATCH_SIZE', OS_INSERT_BATCH_SIZE_DEFAULT)
            ),
            os_api_key=os.getenv('OS_API_KEY') or None,
            os_api_host=os.getenv('OS_API_HOST', OS_API_HOST_DEFAULT),
            os_api_port=int(os.getenv('OS_API_PORT', OS_API_PORT_DEFAULT)),
            wbc_task_name=os.getenv('WBC_TASK_NAME', WBC_TASK_NAME_DEFAULT),
            wbc_task_state_file=os.getenv('WBC_TASK_STATE_FILE', WBC_TASK_STATE_FILE_DEFAULT),
            wbc_task_stale_min=max(
                1, int(os.getenv('WBC_TASK_STALE_MIN', WBC_TASK_STALE_MIN_DEFAULT))
            ),
            wu_enabled=_env_bool('WU_ENABLED', WU_ENABLED_DEFAULT),
            wu_delay_start_s=_env_float('WU_DELAY_START_S', WU_DELAY_START_S_DEFAULT),
            wu_varredura_max_d=_env_float('WU_VARREDURA_MAX_D', WU_VARREDURA_MAX_D_DEFAULT),
            wu_coleta_timeout_s=_env_float('WU_COLETA_TIMEOUT_S', WU_COLETA_TIMEOUT_S_DEFAULT),
            intervalo_minutos=max(
                INTERVALO_PISO_MIN,
                int(os.getenv('INTERVALO_MINUTOS', INTERVALO_MINUTOS_DEFAULT)),
            ),
            janela_horas=janela_horas,
            execution_mode=os.getenv('EXECUTION_MODE', EXECUTION_MODE_DEFAULT),
        )

    @property
    def supabase_write_key(self) -> Optional[str]:
        return self.supabase_service_role_key or self.supabase_key

    def sap_ready(self) -> bool:
        return bool(self.sap_host and self.sap_user and self.sap_password)

    def supabase_ready(self) -> bool:
        return bool(self.supabase_url and self.supabase_write_key)

    def sql_ready(self) -> bool:
        return bool(self.sql_host and self.sql_user and self.sql_password)


_settings: Optional[Settings] = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings.from_env()
    return _settings


def reset_settings() -> None:
    global _settings
    _settings = None
