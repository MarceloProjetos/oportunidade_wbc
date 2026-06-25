"""ETL: SAP HANA view → SQL Server enrichment → Supabase. See config.py and .env.example."""

import sys
import time
import logging
from datetime import datetime
from typing import Any, Optional

import pandas as pd

from config import (
    EXECUTION_MODES,
    FILTRO_COLUNA_DATA,
    MESES_RETROATIVOS,
    SQL_ENRICHMENT_VIEW_DEFAULT,  # noqa: F401 — backward compat
    SQL_LOGIN_TIMEOUT_S,
    SYNC_LOG_TABLE_NAME,  # noqa: F401 — backward compat
    get_settings,
)
from pipeline_core import (  # núcleo compartilhado (genérico)
    SupabaseLoader,
    build_view_query,
    prepare_data,
    validate_sql_identifier,
    with_retries,  # noqa: F401 — re-export p/ compatibilidade
)
from sap_connection import SAPExtractor, is_sap_tenant_error
from db_utils import read_dbapi_query

from config import SAP_PORT_DEFAULT  # noqa: F401 — backward compat

# UTF-8 console on Windows
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except AttributeError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


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
    """Lê todos os registros de uma view/tabela do SQL Server para um DataFrame.

    Args:
        view_name: Nome qualificado da view/tabela (ex.: ``WBCCAD.dbo.INTEGRACAO_ORCSIT``).
        connection: Conexão pyodbc ativa.

    Returns:
        DataFrame com os resultados ou ``None`` em caso de erro.

    Raises:
        ValueError: Se ``view_name`` não for um identificador SQL válido.
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
    """Extrai uma view SAP para um DataFrame, limitando aos últimos N meses.

    O filtro é aplicado na própria query (``WHERE``) sobre ``FILTRO_COLUNA_DATA``,
    trazendo apenas os registros dos últimos ``MESES_RETROATIVOS`` meses.

    Args:
        view_name: View SAP. Se ``None``, usa ``SAP_VIEW_NAME`` do ``.env``.

    Returns:
        DataFrame com os dados filtrados ou ``None`` em caso de erro.
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
    """Orquestra o pipeline completo: extrai do SAP, enriquece e carrega no Supabase.

    Args:
        view_name: View SAP a consultar. Se ``None``, usa ``SAP_VIEW_NAME`` do ``.env``.
        execution_mode: Estratégia de carga:
            ``'snapshot'`` (default) — insere a nova carga e remove as execuções
            anteriores (carrega-depois-poda); a tabela reflete o estado atual e nunca
            fica vazia se algo falhar;
            ``'insert'`` — apenas insere (acumula histórico, pode duplicar).
        execution_id: ID customizado para rastreamento. Gerado automaticamente se ``None``.

    Returns:
        ``True`` se o processo concluiu com sucesso, ``False`` caso contrário.
    """
    # Carregar configurações
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

    # Medição da sincronização (para o log): início, contagem e resultado
    inicio = time.monotonic()
    sync_log_table = settings.sync_log_table_name
    qtd_registros = 0
    resultado = False
    loader: Optional[SupabaseLoader] = None  # reaproveitado no finally para gravar o log

    try:
        # 1. Extrair dados do SAP
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

        # Modo snapshot: carrega-depois-poda — só removemos as execuções anteriores
        # APÓS a inserção dar certo, garantindo que a tabela nunca fique vazia.
        if success and execution_mode == 'snapshot':
            if not loader.delete_other_executions(settings.table_name, exec_id):
                logger.warning(
                    "Inserção OK, mas a remoção das execuções anteriores falhou. "
                    "A tabela pode conter registros duplicados de execuções passadas."
                )

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
        # Registrar a sincronização no log (hora do PC + duração). Isolado em try/except
        # próprio para nunca afetar o resultado da sincronização principal.
        try:
            duracao = time.monotonic() - inicio
            data_hora_pc = datetime.now().isoformat()
            status = 'sucesso' if resultado else 'falha'
            # Reaproveita o cliente já criado no fluxo principal; só instancia um novo se
            # a falha ocorreu antes de ele existir (ex.: extração SAP falhou antes de
            # chegar à carga no Supabase).
            log_loader = loader or SupabaseLoader(
                settings.supabase_url, settings.supabase_write_key
            )
            log_loader.registrar_sincronizacao(
                sync_log_table, data_hora_pc, duracao, status, qtd_registros
            )
        except Exception as log_exc:
            logger.error(f"Falha ao registrar log de sincronização (ignorada): {log_exc}")


if __name__ == "__main__":
    # Parâmetros (todos opcionais):
    #   view_name      — view SAP; se omitido, usa SAP_VIEW_NAME do .env
    #   execution_mode — 'snapshot' (default) ou 'insert'
    #   execution_id   — None gera um UUID automaticamente
    main(execution_mode='snapshot')
