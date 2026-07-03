"""Núcleo de pipeline compartilhado (genérico, sem domínio).

Funções/Classes reutilizáveis por qualquer ETL "view SAP → tabela Supabase":
retry com backoff, validação de identificadores SQL, montagem de FROM, o loader
do Supabase (insert em lotes, poda por execução, log de sincronização) e o
preparo do DataFrame para inserção (campos de controle + serialização JSON).

Originalmente parte de ``extract_sap_to_supabase.py``; extraído para que tanto o
pipeline de ``oportunidades`` quanto o de ``ordens_servico_engenharia`` o usem.
"""

from __future__ import annotations

import logging
import os
import re
import time
import uuid
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from supabase import Client, create_client
from supabase.client import ClientOptions

from config import (
    INSERT_BATCH_SIZE,
    RETRY_ATTEMPTS,
    RETRY_BASE_DELAY_S,
    SYNC_LOG_MAX_REGISTROS,
    get_settings,
)

logger = logging.getLogger(__name__)

# Lock de arquivo (cross-process) p/ serializar a carga de oportunidades entre o
# agendador (run_scheduler.bat) e o "forçar sincronismo" da API (run_api.bat).
try:
    from filelock import FileLock
    from filelock import Timeout as FileLockTimeout
except ImportError:  # pragma: no cover - filelock é dependência de produção
    FileLock = None  # type: ignore[assignment]

    class FileLockTimeout(Exception):  # type: ignore[no-redef]
        """Fallback quando 'filelock' não está instalado."""

_LOCK_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.locks')
_OPORTUNIDADES_LOCK_PATH = os.path.join(_LOCK_DIR, 'oportunidades_sync.lock')


@contextmanager
def oportunidades_sync_lock(timeout: float = 0):
    """Lock de arquivo (cross-process) para a carga de oportunidades.

    Compartilhado entre o agendador e o "forçar sincronismo" da API, evitando duas
    cargas snapshot simultâneas (uma poderia podar as linhas da outra).

    Args:
        timeout: segundos a esperar pelo lock. ``0`` = não espera — levanta
            ``FileLockTimeout`` se já houver carga em andamento.

    Note:
        Se ``filelock`` não estiver instalado, vira **no-op** (sem proteção
        cross-process) — instale com ``pip install filelock``.
    """
    if FileLock is None:
        logger.warning("filelock não instalado — carga de oportunidades SEM lock cross-process")
        yield
        return
    os.makedirs(_LOCK_DIR, exist_ok=True)
    with FileLock(_OPORTUNIDADES_LOCK_PATH, timeout=timeout):
        yield


def with_retries(
    operation: Callable[[], Any],
    *,
    attempts: int = RETRY_ATTEMPTS,
    base_delay: float = RETRY_BASE_DELAY_S,
    what: str = "operation",
    retry_on: Optional[Callable[[Exception], bool]] = None,
) -> Any:
    """Run operation with exponential backoff retries."""
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


# Allow-list for qualified SQL identifiers (schema.view, db.dbo.table)
_SQL_IDENTIFIER_PART = r'[A-Za-z_][A-Za-z0-9_]*'
_SQL_QUALIFIED_NAME_RE = re.compile(
    rf'^{_SQL_IDENTIFIER_PART}(\.{_SQL_IDENTIFIER_PART})*$'
)


def validate_sql_identifier(name: str, *, what: str = "identifier") -> str:
    """Reject names outside the allow-list to prevent SQL injection."""
    if not name or not _SQL_QUALIFIED_NAME_RE.match(name):
        raise ValueError(
            f"{what} inválido (esperado identificador SQL simples ou qualificado): {name!r}"
        )
    return name


def build_view_query(view_name: str, schema: Optional[str] = None) -> str:
    """Build quoted SAP HANA FROM clause (optional schema prefix)."""
    validate_sql_identifier(view_name, what="nome da view SAP")
    if '.' in view_name:
        return view_name
    if schema:
        validate_sql_identifier(schema, what="schema SAP")
        return f'"{schema}"."{view_name}"'
    return view_name


# Inteiro positivo (só dígitos) — usado p/ validar chaves numéricas (ex.: NPED)
# antes de interpolá-las numa query. Rejeita sinais, decimais e espaços internos.
_POSITIVE_INT_RE = re.compile(r'^\d+$')


def coerce_positive_int(value: Any, *, what: str = "valor") -> int:
    """Valida e normaliza ``value`` como inteiro **positivo** (defesa contra injeção).

    Aceita ``int`` ou string numérica (com espaços nas pontas). Rejeita não-dígitos,
    negativos e zero — garantindo um identificador seguro para interpolar em SQL.

    Raises:
        ValueError: se ``value`` não for um inteiro positivo.
    """
    s = str(value).strip()
    if not _POSITIVE_INT_RE.match(s):
        raise ValueError(f"{what} invalido (esperado inteiro positivo): {value!r}")
    n = int(s)
    if n <= 0:
        raise ValueError(f"{what} invalido (deve ser > 0): {value!r}")
    return n


class SupabaseLoader:
    """Batch insert/delete and sync log for Supabase PostgREST."""

    def __init__(
        self,
        supabase_url: str,
        supabase_key: str,
        timeout_s: Optional[float] = None,
    ):
        """Create Supabase client with explicit REST timeout."""
        if timeout_s is None:
            timeout_s = get_settings().supabase_timeout_s

        options = ClientOptions(postgrest_client_timeout=timeout_s)
        self.client: Client = create_client(supabase_url, supabase_key, options)
        logger.info(f"Cliente Supabase inicializado (timeout REST: {timeout_s}s)")

    def fetch_sitcod_domain(self, table_name: str) -> Optional[set[int]]:
        """Load valid sitcod values from Supabase domain table (FK reference)."""
        try:
            res = with_retries(
                lambda: self.client.table(table_name).select('sitcod').execute(),
                what=f"[SITCOD] fetch domain ('{table_name}')",
            )
            codes: set[int] = set()
            for row in res.data or []:
                raw = row.get('sitcod')
                if raw is None:
                    continue
                try:
                    f = float(raw)
                    codes.add(int(f))
                except (TypeError, ValueError):
                    logger.warning("[SITCOD] Skipping non-integer domain value: %r", raw)
            logger.info("[SITCOD] Loaded %s valid code(s) from '%s'", len(codes), table_name)
            return codes
        except Exception as exc:
            logger.error(
                "[SITCOD] Failed to load domain table '%s' — FK validation skipped: %s",
                table_name, exc,
            )
            return None

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

    def delete_other_executions(
        self,
        table_name: str,
        keep_execution_id: str,
        *,
        where_eq: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Remove os registros de execuções anteriores, preservando a execução atual.

        Usado na estratégia **carrega-depois-poda**: deve ser chamado APÓS uma inserção
        bem-sucedida. Assim, se a extração ou a carga falhar, os dados antigos permanecem
        intactos e a tabela (ou o subconjunto filtrado) nunca fica vazia.

        Args:
            table_name: Nome da tabela.
            keep_execution_id: ``id_execucao`` a ser preservado (o da carga atual).
            where_eq: Filtros de igualdade adicionais (``coluna -> valor``) que **restringem**
                a poda. Sem isso, apaga toda execução anterior da tabela (modo ``snapshot``
                global de ``oportunidades``). Com ``{'NPED': 84080}``, apaga só as linhas
                antigas daquele pedido (modo ``replace_nped`` de ordens de serviço).

        Returns:
            ``True`` se a limpeza ocorreu sem erro; ``False`` caso contrário.
        """
        try:
            def _op():
                query = (
                    self.client.table(table_name)
                    .delete()
                    .neq('id_execucao', keep_execution_id)
                )
                for col, val in (where_eq or {}).items():
                    query = query.eq(col, val)
                return query.execute()

            with_retries(_op, what=f"poda de execuções anteriores ('{table_name}')")
            scope = f" (filtro {where_eq})" if where_eq else ""
            logger.info(
                "Execuções anteriores removidas da tabela '%s'%s", table_name, scope
            )
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
        extra_fields: Optional[Dict[str, Any]] = None,
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
            extra_fields: Colunas adicionais a gravar (ex.: ``{'nped': 84080}``).

        Returns:
            ``True`` se o registro foi gravado; ``False`` caso contrário.
        """
        try:
            registro: Dict[str, Any] = {
                'data_hora_sincronizacao': data_hora,
                'duracao_segundos': round(float(duracao_seg), 2),
                'status': status,
                'qtd_registros': qtd_registros,
            }
            if extra_fields:
                registro.update(extra_fields)
            with_retries(
                lambda: self.client.table(table_name).insert(registro).execute(),
                what=f"[SYNC_LOG] insert ('{table_name}')",
            )

            res = with_retries(
                lambda: self.client.table(table_name).select('id').order('id', desc=True).execute(),
                what=f"[SYNC_LOG] list ids ('{table_name}')",
            )
            ids = [r['id'] for r in (res.data or [])]
            if len(ids) > max_registros:
                excedentes = ids[max_registros:]
                with_retries(
                    lambda e=excedentes: self.client.table(table_name).delete().in_('id', e).execute(),
                    what=f"[SYNC_LOG] prune old rows ('{table_name}')",
                )
                logger.info(
                    "[SYNC_LOG] Pruned %s old row(s) from '%s' (keeping %s most recent)",
                    len(excedentes), table_name, max_registros,
                )

            logger.info(
                "[SYNC_LOG] Recorded sync on '%s': status=%s, duration=%.2fs, rows=%s",
                table_name, status, duracao_seg, qtd_registros,
            )
            return True
        except Exception as e:
            logger.error(
                "[SYNC_LOG] Failed to write/prune sync log on '%s' (ignored, pipeline unaffected): %s",
                table_name, e,
            )
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
