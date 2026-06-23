"""Centralized pipeline configuration (environment variables and defaults)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

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

# Scheduler
INTERVALO_MINUTOS_DEFAULT = 30
INTERVALO_PISO_MIN = 5
JANELA_HORAS_DEFAULT = '7-18'
DIAS_SEMANA_DEFAULT = 'mon-fri'
EXECUTION_MODE_DEFAULT = 'snapshot'

# JANELA_HORAS must match start-end hour range (e.g. 7-18)
JANELA_HORAS_RE = re.compile(r'^\d{1,2}-\d{1,2}$')


def parse_janela_horas(expr: str) -> tuple[int, int]:
    """Parse inclusive hour window from JANELA_HORAS (e.g. '7-18')."""
    expr = expr.strip()
    if not JANELA_HORAS_RE.fullmatch(expr):
        raise ValueError(f"Invalid JANELA_HORAS: {expr!r} (expected e.g. '7-18')")
    h_start, h_end = (int(x) for x in expr.split('-', 1))
    if not (0 <= h_start <= 23 and 0 <= h_end <= 23 and h_start <= h_end):
        raise ValueError(f"Invalid JANELA_HORAS range: {expr!r}")
    return h_start, h_end


def _env(*keys: str) -> Optional[str]:
    """Return first non-empty env value among keys."""
    for key in keys:
        value = os.getenv(key)
        if value:
            return value
    return None


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

    sql_host: Optional[str]
    sql_port: int
    sql_user: Optional[str]
    sql_password: Optional[str]
    sql_database: str
    sql_driver: Optional[str]
    sql_enrichment_view: str

    intervalo_minutos: int
    janela_horas: str
    dias_semana: str
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
            sql_host=_env('SQLSERVER_HOST', 'SQL_HOST'),
            sql_port=int(_env('SQLSERVER_PORT', 'SQL_PORT') or SQL_PORT_DEFAULT),
            sql_user=_env('SQLSERVER_USER', 'SQL_USER'),
            sql_password=_env('SQLSERVER_PASSWORD', 'SQL_PASSWORD'),
            sql_database=_env('SQLSERVER_DATABASE', 'SQL_DATABASE') or SQL_DATABASE_DEFAULT,
            sql_driver=_env('SQLSERVER_DRIVER', 'SQL_DRIVER'),
            sql_enrichment_view=os.getenv('SQL_ENRICHMENT_VIEW', SQL_ENRICHMENT_VIEW_DEFAULT),
            intervalo_minutos=max(
                INTERVALO_PISO_MIN,
                int(os.getenv('INTERVALO_MINUTOS', INTERVALO_MINUTOS_DEFAULT)),
            ),
            janela_horas=janela_horas,
            dias_semana=os.getenv('DIAS_SEMANA', DIAS_SEMANA_DEFAULT),
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
