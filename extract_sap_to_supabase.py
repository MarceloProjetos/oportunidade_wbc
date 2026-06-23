"""Extrai a view de oportunidades do SAP B1 (HANA), enriquece com dados do SQL Server
(WBCcad) e carrega o resultado numa tabela do Supabase.

Requisitos:
    pip install hdbcli supabase pandas python-dotenv pyodbc

Variáveis de ambiente (.env):
    SAP_HOST: Host do servidor SAP HANA.
    SAP_PORT: Porta do servidor SAP HANA (default: 30015).
    SAP_USER: Usuário do SAP HANA.
    SAP_PASSWORD: Senha do SAP HANA.
    SAP_DATABASE: Database (tenant) do SAP HANA — opcional.
    SAP_SCHEMA: Schema onde a view está (ex.: SBOALTAMIRAPROD).
    SAP_VIEW_NAME: Nome da view de origem.
    SUPABASE_URL: URL do projeto Supabase.
    SUPABASE_KEY: Chave anon do Supabase (leitura).
    SUPABASE_SERVICE_ROLE_KEY: Chave service_role do Supabase (escrita — ignora RLS).
    TABLE_NAME: Nome da tabela de destino (default: oportunidades).
    SUPABASE_TIMEOUT_S: Timeout das chamadas REST PostgREST, em segundos (opcional).
    SQL_HOST, SQL_PORT, SQL_USER, SQL_PASSWORD, SQL_DATABASE: Conexão SQL Server (opcional).
"""

import re
import sys
import time
import logging
from datetime import datetime, date
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Tuple
import uuid

import numpy as np
import pandas as pd
from supabase import create_client, Client
from supabase.client import ClientOptions

from config import (
    EXECUTION_MODES,
    FILTRO_COLUNA_DATA,
    INSERT_BATCH_SIZE,
    MESES_RETROATIVOS,
    RETRY_ATTEMPTS,
    RETRY_BASE_DELAY_S,
    SQL_LOGIN_TIMEOUT_S,
    SYNC_LOG_MAX_REGISTROS,
    SYNC_LOG_TABLE_NAME,
    get_settings,
)
from sap_connection import SAPExtractor, is_sap_tenant_error

# Re-export para compatibilidade com imports existentes
from config import SAP_PORT_DEFAULT  # noqa: F401

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


# Identificador SQL permitido: letra/underscore seguido de alfanuméricos/underscore.
# Aceita nome qualificado por pontos (SCHEMA.VIEW, DB.dbo.TABELA).
_SQL_IDENTIFIER_PART = r'[A-Za-z_][A-Za-z0-9_]*'
_SQL_QUALIFIED_NAME_RE = re.compile(
    rf'^{_SQL_IDENTIFIER_PART}(\.{_SQL_IDENTIFIER_PART})*$'
)


def validate_sql_identifier(name: str, *, what: str = "identificador") -> str:
    """Valida que ``name`` é um identificador SQL seguro (abordagem allow-list).

    Aceita nomes simples (``VIEW``) ou qualificados por pontos (``SCHEMA.VIEW``,
    ``DB.dbo.TABELA``). Rejeita qualquer coisa fora do padrão — espaços, aspas,
    ponto-e-vírgula, hífens, comentários, etc. — impedindo injeção de SQL na
    montagem das queries por concatenação de string.

    Os nomes de view/schema vêm do ``.env`` (origem confiável), mas validamos
    mesmo assim: defesa em profundidade. Nenhum identificador usado para montar
    SQL deve vir de entrada não confiável sem passar por aqui.

    Args:
        name: Nome a validar.
        what: Rótulo usado na mensagem de erro.

    Returns:
        O próprio ``name`` quando válido.

    Raises:
        ValueError: Se ``name`` for vazio ou não casar com o padrão permitido.
    """
    if not name or not _SQL_QUALIFIED_NAME_RE.match(name):
        raise ValueError(
            f"{what} inválido (esperado identificador SQL simples ou qualificado): {name!r}"
        )
    return name


def build_view_query(view_name: str, schema: Optional[str] = None) -> str:
    """Monta a referência qualificada da view SAP HANA.

    Args:
        view_name: Nome da view. Se já contiver schema (`SCHEMA.VIEW`), é usado como está.
        schema: Schema opcional a prefixar quando ``view_name`` não o contém.

    Returns:
        A referência pronta para uso em ``FROM`` (ex.: ``"SCHEMA"."VIEW"``).

    Raises:
        ValueError: Se ``view_name`` ou ``schema`` não forem identificadores SQL válidos.
    """
    validate_sql_identifier(view_name, what="nome da view SAP")
    if '.' in view_name:
        return view_name
    if schema:
        validate_sql_identifier(schema, what="schema SAP")
        return f'"{schema}"."{view_name}"'
    return view_name


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

    Raises:
        ValueError: Se ``view_name`` não for um identificador SQL válido.
    """
    validate_sql_identifier(view_name, what="nome da view (SQL Server)")
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

    # Filtra na origem: só os últimos MESES_RETROATIVOS meses (mais eficiente)
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
    view_name: str = 'WBCCAD.dbo.INTEGRACAO_ORCSIT'
) -> Optional[pd.DataFrame]:
    """Conecta ao SQL Server e retorna os dados da view solicitada."""
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


class SupabaseLoader:
    """Classe para carregar dados no Supabase."""
    
    def __init__(
        self,
        supabase_url: str,
        supabase_key: str,
        timeout_s: Optional[float] = None,
    ):
        """Inicializa o cliente Supabase com timeout explícito nas chamadas REST.

        Args:
            supabase_url: URL do projeto Supabase.
            supabase_key: Chave de API (preferir a service_role para escrita).
            timeout_s: Timeout PostgREST em segundos. Se ``None``, usa
                ``SUPABASE_TIMEOUT_S`` do ambiente ou o default do módulo.
        """
        if timeout_s is None:
            timeout_s = get_settings().supabase_timeout_s

        options = ClientOptions(postgrest_client_timeout=timeout_s)
        self.client: Client = create_client(supabase_url, supabase_key, options)
        logger.info(f"Cliente Supabase inicializado (timeout REST: {timeout_s}s)")
    
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

    def registrar_sincronizacao(
        self,
        table_name: str,
        data_hora: str,
        duracao_seg: float,
        status: str,
        qtd_registros: Optional[int] = None,
        max_registros: int = SYNC_LOG_MAX_REGISTROS,
    ) -> bool:
        """Grava um registro de log da sincronização e mantém só os N mais recentes.

        Insere uma linha com a hora local de término, a duração e o status; em seguida
        poda a tabela para conservar apenas os ``max_registros`` registros mais recentes
        (apaga os mais antigos pelo ``id``, que é auto-incremental).

        Esta operação é auxiliar: qualquer falha aqui é apenas logada e NÃO interrompe
        nem afeta o fluxo principal de sincronização.

        Args:
            table_name: Nome da tabela de log.
            data_hora: Hora local do PC (ISO 8601) ao terminar a sincronização.
            duracao_seg: Duração do processo, em segundos.
            status: ``'sucesso'`` ou ``'falha'``.
            qtd_registros: Nº de registros sincronizados (opcional).
            max_registros: Quantidade máxima de registros a manter na tabela.

        Returns:
            ``True`` se o registro foi gravado; ``False`` caso contrário.
        """
        try:
            registro = {
                'data_hora_sincronizacao': data_hora,
                'duracao_segundos': round(float(duracao_seg), 2),
                'status': status,
                'qtd_registros': qtd_registros,
            }
            with_retries(
                lambda: self.client.table(table_name).insert(registro).execute(),
                what=f"insert log de sincronização ('{table_name}')",
            )

            # Poda: manter apenas os max_registros mais recentes (apaga os mais antigos)
            res = self.client.table(table_name).select('id').order('id', desc=True).execute()
            ids = [r['id'] for r in (res.data or [])]
            if len(ids) > max_registros:
                excedentes = ids[max_registros:]
                self.client.table(table_name).delete().in_('id', excedentes).execute()
                logger.info(
                    f"Log de sincronização podado: {len(excedentes)} registro(s) antigo(s) "
                    f"removido(s) (mantidos os {max_registros} mais recentes)"
                )

            logger.info(
                f"Sincronização registrada no log ('{table_name}'): "
                f"{status}, {duracao_seg:.2f}s, {qtd_registros} registro(s)"
            )
            return True
        except Exception as e:
            logger.error(f"Falha ao registrar log de sincronização (ignorada): {e}")
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
        qtd_registros = len(data_to_insert)

        # 3. Carregar no Supabase
        logger.info("Carregando dados no Supabase...")
        loader = SupabaseLoader(settings.supabase_url, settings.supabase_write_key)
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
