"""Extrai a view de oportunidades do SAP B1 (HANA), enriquece com dados do SQL Server
(WBCcad) e carrega o resultado numa tabela do Supabase.

Requisitos:
    pip install hdbcli supabase pandas python-dotenv pyodbc

Variáveis de ambiente (.env):
    SAP_HOST: Host do servidor SAP HANA.
    SAP_PORT: Porta do servidor SAP HANA (default: 30013).
    SAP_USER: Usuário do SAP HANA.
    SAP_PASSWORD: Senha do SAP HANA.
    SAP_DATABASE: Database (tenant) do SAP HANA — opcional.
    SAP_SCHEMA: Schema onde a view está (ex.: SBOALTAMIRAPROD).
    SAP_VIEW_NAME: Nome da view de origem.
    SUPABASE_URL: URL do projeto Supabase.
    SUPABASE_KEY: Chave anon do Supabase (leitura).
    SUPABASE_SERVICE_ROLE_KEY: Chave service_role do Supabase (escrita — ignora RLS).
    TABLE_NAME: Nome da tabela de destino (default: oportunidades).
    SQL_HOST, SQL_PORT, SQL_USER, SQL_PASSWORD, SQL_DATABASE: Conexão SQL Server (opcional).
"""

import os
import sys
import time
import logging
from datetime import datetime, date
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Tuple
import uuid

import numpy as np
import pandas as pd
from hdbcli import dbapi
from supabase import create_client, Client
from supabase.client import ClientOptions
from dotenv import load_dotenv

# ── Parâmetros de robustez (timeouts e retries) ──
SAP_CONNECT_TIMEOUT_MS = 15000       # timeout de conexão ao SAP HANA (ms)
SAP_COMM_TIMEOUT_MS = 60000          # timeout de comunicação/query ao SAP HANA (ms)
SQL_LOGIN_TIMEOUT_S = 10             # timeout de login ao SQL Server (s)
SUPABASE_TIMEOUT_S = 30             # timeout das chamadas REST ao Supabase (s)
RETRY_ATTEMPTS = 3                   # tentativas em falhas transitórias
RETRY_BASE_DELAY_S = 2.0             # atraso base do backoff exponencial (s)
INSERT_BATCH_SIZE = 500              # registros por lote no insert ao Supabase
MESES_RETROATIVOS = 6                # janela de dados a carregar (em meses)
FILTRO_COLUNA_DATA = 'CreateDate'    # coluna usada no filtro de N meses

# Garantir saída UTF-8 no console (Windows usa cp1252 e quebra com ✓/✗)
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except AttributeError:
    pass

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Carregar variáveis de ambiente
load_dotenv()


def with_retries(
    operation: Callable[[], Any],
    *,
    attempts: int = RETRY_ATTEMPTS,
    base_delay: float = RETRY_BASE_DELAY_S,
    what: str = "operação",
    retry_on: Optional[Callable[[Exception], bool]] = None,
) -> Any:
    """Executa ``operation`` com novas tentativas e backoff exponencial.

    Args:
        operation: Função sem argumentos a executar.
        attempts: Número máximo de tentativas.
        base_delay: Atraso base em segundos; dobra a cada tentativa (2, 4, 8...).
        what: Rótulo da operação, usado nas mensagens de log.
        retry_on: Predicado ``(exc) -> bool``. Se retornar ``False``, a exceção é
            propagada imediatamente, sem retry. Default: retenta em qualquer exceção.

    Returns:
        O valor retornado por ``operation``.

    Raises:
        Exception: A última exceção capturada, se todas as tentativas falharem
            (ou imediatamente, se ``retry_on`` indicar que não se deve retentar).
    """
    last_exc: Optional[Exception] = None
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except Exception as exc:
            if retry_on is not None and not retry_on(exc):
                raise
            last_exc = exc
            if attempt < attempts:
                delay = base_delay * (2 ** (attempt - 1))
                logger.warning(
                    f"{what}: tentativa {attempt}/{attempts} falhou ({exc}). "
                    f"Retentando em {delay:.0f}s..."
                )
                time.sleep(delay)
            else:
                logger.error(f"{what}: todas as {attempts} tentativas falharam.")
    raise last_exc  # type: ignore[misc]


def build_view_query(view_name: str, schema: Optional[str] = None) -> str:
    """Monta a referência qualificada da view SAP HANA.

    Args:
        view_name: Nome da view. Se já contiver schema (`SCHEMA.VIEW`), é usado como está.
        schema: Schema opcional a prefixar quando ``view_name`` não o contém.

    Returns:
        A referência pronta para uso em ``FROM`` (ex.: ``"SCHEMA"."VIEW"``).
    """
    if '.' in view_name:
        return view_name
    if schema:
        return f'"{schema}"."{view_name}"'
    return view_name


class SAPExtractor:
    """Classe para extrair dados do SAP B1 (HANA)."""
    
    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        database: Optional[str] = None,
    ) -> None:
        """Guarda os parâmetros de conexão com o SAP HANA.

        Args:
            host: Host do servidor SAP HANA.
            port: Porta do servidor.
            user: Usuário do SAP HANA.
            password: Senha do SAP HANA.
            database: Database (tenant) do SAP HANA — opcional.
        """
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database
        self.connection: Optional[Any] = None
    
    def connect(self) -> bool:
        """Conecta ao SAP HANA, com timeout e novas tentativas em falhas transitórias.

        Aplica timeouts de conexão/comunicação e retenta erros transitórios (rede) com
        backoff. O erro determinístico ``not connected`` (database tenant) não é
        retentado: dispara o fallback de conexão sem ``databaseName``.

        Returns:
            ``True`` se conectado com sucesso; ``False`` caso contrário.
        """
        connect_args = {
            'address': self.host,
            'port': self.port,
            'user': self.user,
            'password': self.password,
            'CHARSET': 'UTF8',
            'connectTimeout': SAP_CONNECT_TIMEOUT_MS,
            'communicationTimeout': SAP_COMM_TIMEOUT_MS,
        }

        if self.database:
            connect_args['databaseName'] = self.database

        # Não retentar o erro determinístico 'not connected' — ele leva ao fallback.
        not_transient = lambda exc: 'not connected' not in str(exc).lower()

        def _connect(args: dict):
            return with_retries(
                lambda: dbapi.connect(**args),
                what=f"conexão SAP HANA ({self.host}:{self.port})",
                retry_on=not_transient,
            )

        try:
            self.connection = _connect(connect_args)
            logger.info(f"Conectado ao SAP HANA ({self.host}:{self.port})")
            return True
        except Exception as e:
            error_message = str(e)
            logger.warning(f"Falha ao conectar com databaseName='{self.database}': {error_message}")

            if self.database and 'not connected' in error_message.lower():
                try:
                    connect_args.pop('databaseName', None)
                    self.connection = _connect(connect_args)
                    logger.info(f"Conectado ao SAP HANA ({self.host}:{self.port}) sem databaseName")
                    return True
                except Exception as fallback_error:
                    logger.error(f"Erro ao conectar sem databaseName: {fallback_error}")
                    return False

            logger.error(f"Erro ao conectar ao SAP HANA: {error_message}")
            return False
    
    def execute_query(self, query: str) -> Optional[pd.DataFrame]:
        """Executa uma query no SAP HANA e retorna os resultados em DataFrame.

        Args:
            query: Query SQL a executar.

        Returns:
            DataFrame com os resultados ou ``None`` em caso de erro.
        """
        try:
            if not self.connection:
                raise Exception("Não conectado ao SAP HANA")
            
            df = pd.read_sql(query, self.connection)
            logger.info(f"Query executada com sucesso. {len(df)} linhas retornadas.")
            return df
        except Exception as e:
            logger.error(f"Erro ao executar query: {e}")
            return None
    
    def close(self) -> None:
        """Fecha a conexão com SAP HANA, se aberta."""
        if self.connection:
            self.connection.close()
            logger.info("Conexão com SAP HANA fechada")


def get_sqlserver_connection(
    host: str,
    port: int,
    user: str,
    password: str,
    database: str,
    driver: Optional[str] = None,
) -> Optional[Any]:
    """Estabelece uma conexão com o SQL Server usando pyodbc.

    Tenta os drivers ODBC em ordem de preferência (18 → 17 → Native Client → SQL Server)
    e usa o primeiro que conectar.

    Args:
        host: Host/IP do servidor SQL Server.
        port: Porta do servidor.
        user: Usuário do banco.
        password: Senha do usuário.
        database: Nome do database.
        driver: Driver ODBC específico a tentar primeiro (opcional).

    Returns:
        Um objeto de conexão ``pyodbc.Connection`` ou ``None`` se nenhum driver conectar.
    """
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
    """
    try:
        query = f"SELECT * FROM {view_name}"
        df = pd.read_sql(query, connection)
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
    sap_host = os.getenv('SAP_HOST')
    sap_port = int(os.getenv('SAP_PORT', 30013))
    sap_user = os.getenv('SAP_USER')
    sap_password = os.getenv('SAP_PASSWORD')
    sap_database = os.getenv('SAP_DATABASE')
    sap_schema = os.getenv('SAP_SCHEMA')
    env_view_name = os.getenv('SAP_VIEW_NAME')

    if not view_name:
        view_name = env_view_name

    if not all([sap_host, sap_user, sap_password]) or not view_name:
        logger.error("Faltam variáveis de ambiente obrigatórias ou nome da view SAP não informado")
        return None

    sap = SAPExtractor(sap_host, sap_port, sap_user, sap_password, sap_database)
    if not sap.connect():
        return None

    # Filtra na origem: só os últimos MESES_RETROATIVOS meses (mais eficiente)
    query = (
        f'SELECT * FROM {build_view_query(view_name, sap_schema)} '
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
    view_name: str = 'WBCCAD.dbo.INTEGRACAO_ORCSIT'
) -> Optional[pd.DataFrame]:
    """Conecta ao SQL Server e retorna os dados da view solicitada."""
    # Aceita tanto SQLSERVER_* quanto SQL_* (nomes usados no .env)
    sql_host = os.getenv('SQLSERVER_HOST') or os.getenv('SQL_HOST')
    sql_port = int(os.getenv('SQLSERVER_PORT') or os.getenv('SQL_PORT') or 1433)
    sql_user = os.getenv('SQLSERVER_USER') or os.getenv('SQL_USER')
    sql_password = os.getenv('SQLSERVER_PASSWORD') or os.getenv('SQL_PASSWORD')
    sql_database = os.getenv('SQLSERVER_DATABASE') or os.getenv('SQL_DATABASE') or 'WBCCAD'
    sql_driver = os.getenv('SQLSERVER_DRIVER') or os.getenv('SQL_DRIVER')

    if not all([sql_host, sql_user, sql_password]):
        logger.error("Faltam variáveis de ambiente obrigatórias para SQL Server")
        return None

    conn = get_sqlserver_connection(
        host=sql_host,
        port=sql_port,
        user=sql_user,
        password=sql_password,
        database=sql_database,
        driver=sql_driver
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


class SupabaseLoader:
    """Classe para carregar dados no Supabase."""
    
    def __init__(self, supabase_url: str, supabase_key: str):
        """Inicializa o cliente Supabase com timeout configurado nas chamadas REST.

        Args:
            supabase_url: URL do projeto Supabase.
            supabase_key: Chave de API (preferir a service_role para escrita).
        """

        self.client: Client = create_client(supabase_url, supabase_key)
        logger.info("Cliente Supabase inicializado")
    
    def insert_data(
        self, table_name: str, data: List[Dict[str, Any]], batch_size: int = INSERT_BATCH_SIZE
    ) -> bool:
        """Insere dados na tabela do Supabase, em lotes (batches).

        Inserir em lotes evita estourar o limite de payload do PostgREST e dá mais
        resiliência: cada lote tem seu próprio retry.

        Args:
            table_name: Nome da tabela.
            data: Lista de dicionários com os dados.
            batch_size: Tamanho de cada lote.

        Returns:
            ``True`` se todos os lotes foram inseridos; ``False`` caso contrário.
        """
        total = len(data)
        if total == 0:
            logger.warning("Nenhum registro para inserir.")
            return True
        try:
            for inicio in range(0, total, batch_size):
                lote = data[inicio:inicio + batch_size]
                num_lote = inicio // batch_size + 1
                with_retries(
                    lambda l=lote: self.client.table(table_name).insert(l).execute(),
                    what=f"insert lote {num_lote} ('{table_name}')",
                )
                logger.info(
                    f"Lote {num_lote}: {len(lote)} registro(s) inseridos "
                    f"({min(inicio + batch_size, total)}/{total})"
                )
            logger.info(f"{total} registro(s) inseridos com sucesso na tabela '{table_name}'")
            return True
        except Exception as e:
            logger.error(f"Erro ao inserir dados no Supabase: {e}")
            return False
    
    def upsert_data(self, table_name: str, data: List[Dict[str, Any]]) -> bool:
        """Faz upsert (atualiza se existe, insere se não) dos dados.

        Usa a chave primária da própria tabela para resolver conflitos.

        Args:
            table_name: Nome da tabela.
            data: Lista de dicionários com os dados.

        Returns:
            ``True`` se executado com sucesso; ``False`` caso contrário.
        """
        try:
            with_retries(
                lambda: self.client.table(table_name).upsert(data).execute(),
                what=f"upsert no Supabase ('{table_name}')",
            )
            logger.info(f"{len(data)} registro(s) processado(s) com sucesso na tabela '{table_name}'")
            return True
        except Exception as e:
            logger.error(f"Erro ao fazer upsert no Supabase: {e}")
            return False

    def delete_other_executions(self, table_name: str, keep_execution_id: str) -> bool:
        """Remove os registros de execuções anteriores, preservando a execução atual.

        Usado no modo *snapshot* com a estratégia **carrega-depois-poda**: deve ser
        chamado APÓS uma inserção bem-sucedida. Assim, se a extração ou a carga falhar,
        os dados antigos permanecem intactos e a tabela nunca fica vazia.

        Args:
            table_name: Nome da tabela.
            keep_execution_id: ``id_execucao`` a ser preservado (o da carga atual).

        Returns:
            ``True`` se a limpeza ocorreu sem erro; ``False`` caso contrário.
        """
        try:
            with_retries(
                lambda: self.client.table(table_name)
                .delete().neq('id_execucao', keep_execution_id).execute(),
                what=f"poda de execuções anteriores ('{table_name}')",
            )
            logger.info(f"Execuções anteriores removidas da tabela '{table_name}' (snapshot)")
            return True
        except Exception as e:
            logger.error(f"Erro ao remover execuções anteriores: {e}")
            return False


def prepare_data(
    df: pd.DataFrame, execution_id: Optional[str] = None
) -> Tuple[List[Dict[str, Any]], str]:
    """Converte o DataFrame em registros prontos para inserir no Supabase.

    Adiciona os campos de rastreio ``id_execucao`` e ``data_hora_extracao`` e normaliza
    valores não serializáveis em JSON (datas, Decimal, tipos numpy e NaN/NaT).

    Args:
        df: DataFrame com os dados extraídos.
        execution_id: ID para rastrear a execução. Gerado (UUID4) se não fornecido.

    Returns:
        Uma tupla ``(registros, execution_id)``, onde ``registros`` é a lista de
        dicionários pronta para inserção e ``execution_id`` é o ID usado.
    """
    if execution_id is None:
        execution_id = str(uuid.uuid4())
    
    extraction_datetime = datetime.now().isoformat()
    
    # Converter DataFrame para lista de dicionários
    data_list = df.to_dict(orient='records')
    
    # Adicionar campos de execução e timestamp
    for record in data_list:
        record['id_execucao'] = execution_id
        record['data_hora_extracao'] = extraction_datetime
        # Tratar valores NULL/NaN e converter tipos não serializáveis em JSON
        for key, value in record.items():
            if pd.isna(value):
                record[key] = None
            elif isinstance(value, (pd.Timestamp, datetime, date)):
                record[key] = value.isoformat()
            elif isinstance(value, Decimal):
                record[key] = float(value)
            elif isinstance(value, np.integer):
                record[key] = int(value)
            elif isinstance(value, (np.floating, float)):
                # Colunas INTEGER do SAP viram float no pandas por causa de NaN
                # (ex.: 72051.0). Converter inteiros-exatos de volta para int para
                # não violar colunas integer; manter decimais reais como float.
                f = float(value)
                record[key] = int(f) if f.is_integer() else f
            elif isinstance(value, np.bool_):
                record[key] = bool(value)
    
    logger.info(f"Dados preparados: {len(data_list)} registros com ID de execução '{execution_id}'")
    return data_list, execution_id


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
            ``'insert'`` — apenas insere (acumula histórico, pode duplicar);
            ``'upsert'`` — atualiza existentes ou insere novos.
        execution_id: ID customizado para rastreamento. Gerado automaticamente se ``None``.

    Returns:
        ``True`` se o processo concluiu com sucesso, ``False`` caso contrário.
    """
    # Carregar configurações
    sap_host = os.getenv('SAP_HOST')
    sap_port = int(os.getenv('SAP_PORT', 30013))
    sap_user = os.getenv('SAP_USER')
    sap_password = os.getenv('SAP_PASSWORD')
    sap_database = os.getenv('SAP_DATABASE')
    sap_schema = os.getenv('SAP_SCHEMA')
    supabase_url = os.getenv('SUPABASE_URL')
    # Preferir a service_role para escrita (ignora RLS); cai para a anon se não houver
    supabase_key = os.getenv('SUPABASE_SERVICE_ROLE_KEY') or os.getenv('SUPABASE_KEY')
    table_name = os.getenv('TABLE_NAME', 'oportunidades')
    env_view_name = os.getenv('SAP_VIEW_NAME')
    
    if not view_name:
        view_name = env_view_name
    
    # Validar configurações
    if not all([sap_host, sap_user, sap_password, supabase_url, supabase_key]) or not view_name:
        logger.error("Faltam variáveis de ambiente obrigatórias ou nome da view SAP não informado")
        return False
    
    try:
        # 1. Extrair dados do SAP
        logger.info("Iniciando extração de dados do SAP...")
        df = extract_sap_to_dataframe(view_name)
        
        if df is None or len(df) == 0:
            logger.warning(f"Nenhum dado retornado da view '{view_name}'")
            return False
        
        logger.info(f"Total de registros extraídos: {len(df)}")

        # 1.1. Enriquecer com SITCOD e ORCALTDTH do SQL Server (join N_WBC = ORCNUM)
        logger.info("Conectando ao SQL Server para buscar SITCOD/ORCALTDTH (INTEGRACAO_ORCSIT)...")
        sql_df = extract_sqlserver_view('WBCCAD.dbo.INTEGRACAO_ORCSIT')
        if sql_df is None or len(sql_df) == 0:
            logger.warning("SQL Server indisponível; SITCOD/ORCALTDTH ficarão nulos")
            df['SITCOD'] = None
            df['ORCALTDTH'] = None
        else:
            logger.info(f"Dados SQL Server extraídos: {len(sql_df)} registros")
            sit = sql_df[['ORCNUM', 'SITCOD', 'ORCALTDTH']].copy()
            sit['ORCNUM'] = sit['ORCNUM'].astype(str).str.strip()
            # Se houver histórico para o mesmo ORCNUM, manter o registro mais recente
            sit = sit.sort_values('ORCALTDTH').drop_duplicates(subset='ORCNUM', keep='last')
            df['_key'] = df['N_WBC'].astype(str).str.strip()
            df = df.merge(sit, how='left', left_on='_key', right_on='ORCNUM')
            df = df.drop(columns=['_key', 'ORCNUM'])
            matched = int(df['SITCOD'].notna().sum())
            logger.info(f"SITCOD/ORCALTDTH preenchidos em {matched} de {len(df)} registros")
        
        # 2. Preparar dados
        logger.info("Preparando dados para inserção...")
        data_to_insert, exec_id = prepare_data(df, execution_id)
        
        # 3. Carregar no Supabase
        logger.info("Carregando dados no Supabase...")
        loader = SupabaseLoader(supabase_url, supabase_key)
        
        if execution_mode == 'upsert':
            success = loader.upsert_data(table_name, data_to_insert)
        else:
            # 'insert' e 'snapshot' inserem os novos registros primeiro
            success = loader.insert_data(table_name, data_to_insert)

        # Modo snapshot: carrega-depois-poda — só removemos as execuções anteriores
        # APÓS a inserção dar certo, garantindo que a tabela nunca fique vazia.
        if success and execution_mode == 'snapshot':
            if not loader.delete_other_executions(table_name, exec_id):
                logger.warning(
                    "Inserção OK, mas a remoção das execuções anteriores falhou. "
                    "A tabela pode conter registros duplicados de execuções passadas."
                )

        if success:
            logger.info(f"✓ Processo concluído com sucesso (ID de execução: {exec_id})")
            return True
        else:
            logger.error("✗ Erro ao carregar dados no Supabase")
            return False
            
    except Exception as e:
        logger.error(f"Erro no processo: {e}")
        return False


if __name__ == "__main__":
    # Parâmetros (todos opcionais):
    #   view_name      — view SAP; se omitido, usa SAP_VIEW_NAME do .env
    #   execution_mode — 'snapshot' (default), 'insert' ou 'upsert'
    #   execution_id   — None gera um UUID automaticamente
    main(execution_mode='snapshot')
