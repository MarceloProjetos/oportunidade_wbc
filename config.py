"""Configuração centralizada do pipeline (variáveis de ambiente e defaults)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

# ── SAP HANA ──
SAP_PORT_DEFAULT = 30015
SAP_CONNECT_TIMEOUT_MS = 15_000
SAP_COMM_TIMEOUT_MS = 60_000

# ── SQL Server ──
SQL_PORT_DEFAULT = 1433
SQL_DATABASE_DEFAULT = 'WBCCAD'
SQL_LOGIN_TIMEOUT_S = 10

# ── Supabase ──
SUPABASE_TIMEOUT_S = 120
TABLE_NAME_DEFAULT = 'oportunidades'

# ── Pipeline ──
RETRY_ATTEMPTS = 3
RETRY_BASE_DELAY_S = 2.0
INSERT_BATCH_SIZE = 500
MESES_RETROATIVOS = 6
FILTRO_COLUNA_DATA = 'CreateDate'

# ── Log de sincronização ──
SYNC_LOG_TABLE_NAME = 'sincronizacao_log'
SYNC_LOG_MAX_REGISTROS = 6

# Modos de carga suportados (``upsert`` removido — ver README).
EXECUTION_MODES = ('snapshot', 'insert')

# ── Agendador ──
INTERVALO_MINUTOS_DEFAULT = 30
INTERVALO_PISO_MIN = 5
JANELA_HORAS_DEFAULT = '7-18'
DIAS_SEMANA_DEFAULT = 'mon-fri'
EXECUTION_MODE_DEFAULT = 'snapshot'


def _env(*keys: str) -> Optional[str]:
    """Retorna o primeiro valor não vazio entre várias chaves de ambiente."""
    for key in keys:
        value = os.getenv(key)
        if value:
            return value
    return None


@dataclass(frozen=True)
class Settings:
    """Snapshot das variáveis de ambiente usadas pelo pipeline."""

    # SAP HANA
    sap_host: Optional[str]
    sap_port: int
    sap_user: Optional[str]
    sap_password: Optional[str]
    sap_database: Optional[str]
    sap_schema: Optional[str]
    sap_view_name: Optional[str]

    # Supabase
    supabase_url: Optional[str]
    supabase_key: Optional[str]
    supabase_service_role_key: Optional[str]
    table_name: str
    supabase_timeout_s: float
    sync_log_table_name: str

    # SQL Server (aceita SQL_* e SQLSERVER_*)
    sql_host: Optional[str]
    sql_port: int
    sql_user: Optional[str]
    sql_password: Optional[str]
    sql_database: str
    sql_driver: Optional[str]

    # Agendador
    intervalo_minutos: int
    janela_horas: str
    dias_semana: str
    execution_mode: str

    @classmethod
    def from_env(cls) -> Settings:
        """Carrega configuração a partir do ambiente (``.env`` já aplicado)."""
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
            intervalo_minutos=max(
                INTERVALO_PISO_MIN,
                int(os.getenv('INTERVALO_MINUTOS', INTERVALO_MINUTOS_DEFAULT)),
            ),
            janela_horas=os.getenv('JANELA_HORAS', JANELA_HORAS_DEFAULT),
            dias_semana=os.getenv('DIAS_SEMANA', DIAS_SEMANA_DEFAULT),
            execution_mode=os.getenv('EXECUTION_MODE', EXECUTION_MODE_DEFAULT),
        )

    @property
    def supabase_write_key(self) -> Optional[str]:
        """Chave para escrita no Supabase (service_role com fallback para anon)."""
        return self.supabase_service_role_key or self.supabase_key

    def sap_ready(self) -> bool:
        """``True`` se host, usuário e senha SAP estão definidos."""
        return bool(self.sap_host and self.sap_user and self.sap_password)

    def supabase_ready(self) -> bool:
        """``True`` se URL e alguma chave Supabase estão definidas."""
        return bool(self.supabase_url and self.supabase_write_key)

    def sql_ready(self) -> bool:
        """``True`` se credenciais mínimas do SQL Server estão definidas."""
        return bool(self.sql_host and self.sql_user and self.sql_password)


_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """Retorna instância cacheada de :class:`Settings`."""
    global _settings
    if _settings is None:
        _settings = Settings.from_env()
    return _settings


def reset_settings() -> None:
    """Limpa o cache de configuração (útil em testes)."""
    global _settings
    _settings = None
