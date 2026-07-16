"""ETL: SAP HANA view → SQL Server enrichment → Supabase. See config.py and .env.example."""

import logging
import sys
import time
from typing import Any, Optional

import pandas as pd

from config import (
    EXECUTION_MODES,
    FILTRO_COLUNA_DATA,
    MESES_RETROATIVOS,
    SAP_PORT_DEFAULT,  # noqa: F401 — backward compat
    SQL_ENRICHMENT_VIEW_DEFAULT,  # noqa: F401 — backward compat
    SQL_LOGIN_TIMEOUT_S,
    SYNC_LOG_TABLE_NAME,  # noqa: F401 — backward compat
    get_settings,
)
from db_utils import read_dbapi_query
from pipeline_core import (  # núcleo compartilhado (genérico)
    FileLockTimeout,
    SupabaseLoader,
    agora_iso,
    build_view_query,
    oportunidades_sync_lock,
    prepare_data,
    validate_sql_identifier,
    with_retries,  # noqa: F401 — re-export p/ compatibilidade
)
from sap_connection import SAPExtractor

# UTF-8 console on Windows
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except AttributeError:
    pass

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    """Basic console logging. Called only by the entrypoint (CLI), never on import —
    as a lib (imported by the API/scheduler) it must not touch global logging."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    )


def get_sqlserver_connection(
    host: str,
    port: int,
    user: str,
    password: str,
    database: str,
    driver: Optional[str] = None,
) -> Optional[Any]:
    """Connect to SQL Server via pyodbc (tries ODBC 18 → 17 → legacy drivers)."""
    try:
        import pyodbc
    except ImportError:
        logger.error("pyodbc não está instalado. Execute: pip install pyodbc")
        return None

    drivers = [driver] if driver else []
    drivers.extend([
        'ODBC Driver 18 for SQL Server',
        'ODBC Driver 17 for SQL Server',
        'SQL Server Native Client 11.0',
        'SQL Server'
    ])

    for driver_name in drivers:
        if not driver_name:
            continue
        conn_str = (
            f'DRIVER={{{driver_name}}};'
            f'SERVER={host},{port};'
            f'UID={user};PWD={password};'
            f'DATABASE={database};'
            'TrustServerCertificate=Yes;'
            f'Connection Timeout={SQL_LOGIN_TIMEOUT_S}'
        )
        try:
            conn = pyodbc.connect(conn_str, timeout=SQL_LOGIN_TIMEOUT_S)
            logger.info(f"Conectado ao SQL Server ({host}:{port}) usando driver '{driver_name}'")
            return conn
        except Exception as exc:
            logger.warning(f"Falha ao conectar com driver '{driver_name}': {exc}")

    logger.error("Não foi possível conectar ao SQL Server. Verifique o driver ODBC e as credenciais.")
    return None


def query_sqlserver_view(view_name: str, connection: Any) -> Optional[pd.DataFrame]:
    """Read every record of a SQL Server view/table into a DataFrame.

    Args:
        view_name: Qualified view/table name (e.g. ``WBCCAD.dbo.INTEGRACAO_ORCSIT``).
        connection: Active pyodbc connection.

    Returns:
        DataFrame with the results, or ``None`` on error.

    Raises:
        ValueError: If ``view_name`` is not a valid SQL identifier.
    """
    validate_sql_identifier(view_name, what="nome da view (SQL Server)")
    try:
        query = f"SELECT * FROM {view_name}"
        df = read_dbapi_query(query, connection)
        logger.info(f"Query SQL Server executada com sucesso. {len(df)} linhas retornadas.")
        return df
    except Exception as e:
        logger.error(f"Erro ao executar query SQL Server: {e}")
        return None


def extract_sap_to_dataframe(view_name: Optional[str] = None) -> Optional[pd.DataFrame]:
    """Extract a SAP view into a DataFrame, limited to the last N months.

    The filter is applied in the query itself (``WHERE``) over ``FILTRO_COLUNA_DATA``,
    fetching only the records from the last ``MESES_RETROATIVOS`` months.

    Args:
        view_name: SAP view. If ``None``, uses ``SAP_VIEW_NAME`` from ``.env``.

    Returns:
        DataFrame with the filtered data, or ``None`` on error.
    """
    settings = get_settings()

    if not view_name:
        view_name = settings.sap_view_name

    if not settings.sap_ready() or not view_name:
        logger.error("Faltam variáveis de ambiente obrigatórias ou nome da view SAP não informado")
        return None

    sap = SAPExtractor(
        settings.sap_host,
        settings.sap_port,
        settings.sap_user,
        settings.sap_password,
        settings.sap_database,
    )
    if not sap.connect():
        return None

    # Filter at source: last MESES_RETROATIVOS months only
    query = (
        f'SELECT * FROM {build_view_query(view_name, settings.sap_schema)} '
        f'WHERE "{FILTRO_COLUNA_DATA}" >= ADD_MONTHS(CURRENT_DATE, -{MESES_RETROATIVOS})'
    )
    df = sap.execute_query(query)
    sap.close()

    if df is None:
        logger.error(f"Falha ao extrair dados da view '{view_name}'")
        return None

    logger.info(f"Dados extraídos do SAP: {len(df)} registros")
    return df


def extract_sqlserver_view(
    view_name: Optional[str] = None,
) -> Optional[pd.DataFrame]:
    """Fetch enrichment view from SQL Server (default from SQL_ENRICHMENT_VIEW env)."""
    if view_name is None:
        view_name = get_settings().sql_enrichment_view
    settings = get_settings()

    if not settings.sql_ready():
        logger.error("Faltam variáveis de ambiente obrigatórias para SQL Server")
        return None

    conn = get_sqlserver_connection(
        host=settings.sql_host,
        port=settings.sql_port,
        user=settings.sql_user,
        password=settings.sql_password,
        database=settings.sql_database,
        driver=settings.sql_driver,
    )
    if conn is None:
        return None

    df = query_sqlserver_view(view_name, conn)
    try:
        conn.close()
        logger.info("Conexão SQL Server fechada")
    except Exception:
        pass

    return df


def _sitcod_as_int(value: Any) -> Optional[int]:
    """Normalize SITCOD cell to int or None."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if pd.isna(value):
        return None
    try:
        f = float(value)
        return int(f)
    except (TypeError, ValueError):
        return None


def validate_sitcod_fk(
    df: pd.DataFrame,
    loader: SupabaseLoader,
    *,
    domain_table: str,
) -> pd.DataFrame:
    """Null out SITCOD values missing from situacoes_orcamento (avoids FK violation)."""
    if 'SITCOD' not in df.columns:
        return df

    valid = loader.fetch_sitcod_domain(domain_table)
    if valid is None:
        logger.warning(
            "[SITCOD] FK validation skipped — could not load domain from '%s'",
            domain_table,
        )
        return df

    normalized = df['SITCOD'].map(_sitcod_as_int)
    has_value = normalized.notna()
    invalid_mask = has_value & ~normalized.isin(valid)
    n_invalid = int(invalid_mask.sum())

    if n_invalid:
        samples = sorted({normalized.loc[i] for i in df.index[invalid_mask]})[:10]
        logger.error(
            "[SITCOD] FK violation prevented: %s row(s) have unknown SITCOD %s "
            "(not in '%s'). Setting those values to NULL.",
            n_invalid, samples, domain_table,
        )
        df = df.copy()
        df.loc[invalid_mask, 'SITCOD'] = None
    else:
        with_sitcod = int(has_value.sum())
        logger.info(
            "[SITCOD] FK validation OK — %s non-null row(s), all exist in '%s'",
            with_sitcod, domain_table,
        )

    return df


def main(
    view_name: Optional[str] = None,
    execution_mode: str = 'snapshot',
    execution_id: Optional[str] = None,
) -> bool:
    """Orchestrate the full pipeline: extract from SAP, enrich and load into Supabase.

    Args:
        view_name: SAP view to query. If ``None``, uses ``SAP_VIEW_NAME`` from ``.env``.
        execution_mode: Loading strategy:
            ``'snapshot'`` (default) — insert the new load and remove previous executions
            (load-then-prune); the table reflects the current state and never ends up
            empty if something fails;
            ``'insert'`` — insert only (accumulates history, may duplicate).
        execution_id: Custom tracking ID. Generated automatically if ``None``.

    Returns:
        ``True`` if the process completed successfully, ``False`` otherwise.
    """
    # Load configuration
    settings = get_settings()
    view_name = view_name or settings.sap_view_name

    if not settings.sap_ready() or not settings.supabase_ready() or not view_name:
        logger.error("Faltam variáveis de ambiente obrigatórias ou nome da view SAP não informado")
        return False

    if execution_mode == 'upsert':
        logger.error(
            "Modo 'upsert' não é suportado (removido): não havia chave de negócio para "
            "ON CONFLICT e o modo só duplicava registros. Use 'snapshot' (default) para "
            "manter o estado atual ou 'insert' para acumular histórico."
        )
        return False

    if execution_mode not in EXECUTION_MODES:
        logger.error(
            f"execution_mode inválido: {execution_mode!r}. "
            f"Valores aceitos: {', '.join(EXECUTION_MODES)}"
        )
        return False

    # Sync measurement (for the log): start, count and result
    inicio = time.monotonic()
    sync_log_table = settings.sync_log_table_name
    qtd_registros = 0
    resultado = False
    loader: Optional[SupabaseLoader] = None  # reused in the finally to write the log

    try:
        # 1. Extract data from SAP
        logger.info("Iniciando extração de dados do SAP...")
        df = extract_sap_to_dataframe(view_name)

        if df is None or len(df) == 0:
            logger.warning(f"Nenhum dado retornado da view '{view_name}'")
            return False

        logger.info(f"Total de registros extraídos: {len(df)}")

        # 1.1 SQL Server enrichment: join N_WBC = ORCNUM → SITCOD, ORCALTDTH
        enrich_view = settings.sql_enrichment_view
        logger.info("SQL Server enrichment from %s...", enrich_view)
        if 'N_WBC' not in df.columns:
            logger.error("SAP data missing N_WBC column; cannot merge enrichment")
            return False
        sql_df = extract_sqlserver_view(enrich_view)
        if sql_df is None or len(sql_df) == 0:
            logger.warning("SQL Server unavailable; SITCOD/ORCALTDTH will be null")
            df['SITCOD'] = None
            df['ORCALTDTH'] = None
        else:
            logger.info("SQL Server rows: %s", len(sql_df))
            sit = sql_df[['ORCNUM', 'SITCOD', 'ORCALTDTH']].copy()
            sit['ORCNUM'] = sit['ORCNUM'].astype(str).str.strip()
            sit = sit.sort_values('ORCALTDTH').drop_duplicates(subset='ORCNUM', keep='last')
            df['_key'] = df['N_WBC'].astype(str).str.strip()
            df = df.merge(sit, how='left', left_on='_key', right_on='ORCNUM')
            df = df.drop(columns=['_key', 'ORCNUM'])
            matched = int(df['SITCOD'].notna().sum())
            logger.info("Enrichment matched %s / %s rows", matched, len(df))

        # 2. Validate SITCOD FK, then prepare payload
        logger.info("Carregando dados no Supabase...")
        loader = SupabaseLoader(settings.supabase_url, settings.supabase_write_key)
        df = validate_sitcod_fk(df, loader, domain_table=settings.sitcod_domain_table)

        logger.info("Preparando dados para inserção...")
        data_to_insert, exec_id = prepare_data(df, execution_id)
        qtd_registros = len(data_to_insert)

        # 3. Insert into Supabase
        success = loader.insert_data(settings.table_name, data_to_insert)

        # Snapshot mode: load-then-prune — previous executions are only removed AFTER the
        # insert succeeds, guaranteeing the table is never left empty.
        if success and execution_mode == 'snapshot':
            if not loader.delete_other_executions(settings.table_name, exec_id):
                # NOT a success: the snapshot contract ("replaces the table") was not met
                # — TWO executions remain and readers get everything duplicated. This used
                # to be WARNING + return True: the log said 'sucesso' with a corrupted
                # table. The next successful load consolidates it.
                logger.error(
                    "Inserção OK mas a PODA das execuções anteriores falhou: a tabela está "
                    "com registros DUPLICADOS (a atual + a anterior). A próxima carga "
                    "bem-sucedida consolida."
                )
                return False

        if success:
            logger.info(f"✓ Processo concluído com sucesso (ID de execução: {exec_id})")
            resultado = True
            return True
        else:
            logger.error("✗ Erro ao carregar dados no Supabase")
            return False

    except Exception as e:
        logger.error(f"Erro no processo: {e}")
        return False
    finally:
        # Record the sync in the log (machine time + duration). Isolated in its own
        # try/except so it can never affect the main sync result.
        try:
            duracao = time.monotonic() - inicio
            data_hora_pc = agora_iso()   # with offset: the column is timestamptz (see agora_iso)
            status = 'sucesso' if resultado else 'falha'
            # Reuse the client already created in the main flow; only instantiate a new
            # one if the failure happened before it existed (e.g. the SAP extraction
            # failed before reaching the Supabase load).
            log_loader = loader or SupabaseLoader(
                settings.supabase_url, settings.supabase_write_key
            )
            log_loader.registrar_sincronizacao(
                sync_log_table, data_hora_pc, duracao, status, qtd_registros
            )
        except Exception as log_exc:
            logger.error(f"Falha ao registrar log de sincronização (ignorada): {log_exc}")


if __name__ == "__main__":
    _configure_logging()
    # Parameters (all optional):
    #   view_name      — SAP view; if omitted, uses SAP_VIEW_NAME from .env
    #   execution_mode — 'snapshot' (default) or 'insert'
    #   execution_id   — None generates a UUID automatically
    #
    # The LOCK is mandatory here too: the API (api.py) and the scheduler
    # (scripts/scheduled_execution.py) already took the lock, but this entrypoint ran
    # OUTSIDE it. Running this by hand during a scheduler load could EMPTY the table —
    # each process inserts and then prunes "everything that is not my execution",
    # deleting the other's rows. `timeout=0`: if a load is already running, warn and exit
    # without touching anything.
    try:
        with oportunidades_sync_lock(timeout=0):
            ok = main(execution_mode='snapshot')
    except FileLockTimeout:
        logger.error(
            "Já há uma carga de oportunidades em andamento (agendador ou API). "
            "Nada foi alterado — aguarde ela terminar."
        )
        raise SystemExit(1)
    raise SystemExit(0 if ok else 1)
