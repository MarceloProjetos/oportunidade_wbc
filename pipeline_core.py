"""Shared pipeline core (generic, domain-free).

Functions/classes reusable by any "SAP view → Supabase table" ETL: retry with
backoff, SQL identifier validation, FROM-clause building, the Supabase loader
(batched insert, prune-by-execution, sync log) and DataFrame preparation for
insertion (control fields + JSON serialization).

Originally part of ``extract_sap_to_supabase.py``; extracted so that both the
``oportunidades`` and the ``ordens_servico_engenharia`` pipelines can use it.
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

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
from retry import with_retries as _with_retries

logger = logging.getLogger(__name__)

# Cross-process file lock serializing the oportunidades load between the scheduler
# (run_scheduler.bat) and the API's "force sync" (run_api.bat).
try:
    from filelock import FileLock
    from filelock import Timeout as FileLockTimeout
except ImportError:  # pragma: no cover - filelock is a production dependency
    FileLock = None  # type: ignore[assignment]

    class FileLockTimeout(Exception):  # type: ignore[no-redef]
        """Fallback for when 'filelock' is not installed."""

_LOCK_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.locks')
_OPORTUNIDADES_LOCK_PATH = os.path.join(_LOCK_DIR, 'oportunidades_sync.lock')


@contextmanager
def oportunidades_sync_lock(timeout: float = 0):
    """Cross-process file lock for the oportunidades load.

    Shared between the scheduler and the API's "force sync", preventing two concurrent
    snapshot loads (one could prune the other's rows).

    Args:
        timeout: seconds to wait for the lock. ``0`` = do not wait — raises
            ``FileLockTimeout`` if a load is already running.

    Note:
        Without ``filelock`` installed this becomes a **no-op** (no cross-process
        protection) — install it with ``pip install filelock``.
    """
    if FileLock is None:
        logger.warning("filelock não instalado — carga de oportunidades SEM lock cross-process")
        yield
        return
    os.makedirs(_LOCK_DIR, exist_ok=True)
    with FileLock(_OPORTUNIDADES_LOCK_PATH, timeout=timeout):
        yield


@contextmanager
def os_sync_lock(nped: object, timeout: float = 0):
    """Cross-process file lock for the OS sync of **a single pedido**.

    The ``_sync_lock`` in ``api.py`` is a ``threading.Lock`` — it serializes waitress
    threads and nothing else. But the pipeline also has a CLI
    (``python extract_ordens_servico_engenharia.py 84080``), so two PROCESSES could write
    the same pedido at once, with a destructive result::

        A inserts (exec_A)            B inserts (exec_B)
        A prunes all != exec_A   ->   deletes B's rows
        B prunes all != exec_B   ->   deletes A's rows
        => the pedido VANISHES from the table — and both log 'sucesso'

    That breaks the invariant load-then-prune promises ("a pedido is never empty"): the
    insert+prune pair is not atomic. The lock closes the window between processes.

    **Per pedido, not global**, on purpose: the invariant is *one writer per N_PED*. A
    single lock would block unrelated pedidos for no reason and would misrepresent what
    it protects. The files live in ``.locks/`` (gitignored) and are empty.

    Args:
        nped: pedido to lock (forms the file name; validated as an integer).
        timeout: seconds to wait. ``0`` = do not wait — raises ``FileLockTimeout`` if
            another process is already syncing **this** pedido.

    Note:
        Without ``filelock`` installed this becomes a **no-op** (same contract as the
        oportunidades lock).
    """
    nped_int = coerce_positive_int(nped, what='NPED')
    if FileLock is None:
        logger.warning("filelock não instalado — sync de OS SEM lock cross-process")
        yield
        return
    os.makedirs(_LOCK_DIR, exist_ok=True)
    with FileLock(os.path.join(_LOCK_DIR, f'os_sync_{nped_int}.lock'), timeout=timeout):
        yield


def with_retries(
    operation: Callable[[], Any],
    *,
    attempts: int = RETRY_ATTEMPTS,
    base_delay: float = RETRY_BASE_DELAY_S,
    what: str = "operation",
    retry_on: Optional[Callable[[Exception], bool]] = None,
) -> Any:
    """Retry with backoff — see ``retry.with_retries``.

    Thin wrapper that only applies the project defaults (``RETRY_ATTEMPTS`` /
    ``RETRY_BASE_DELAY_S``). The implementation lives in ``retry.py``, dependency-free, so
    ``sap_connection`` can reuse it without dragging in ``supabase``.
    """
    return _with_retries(
        operation, attempts=attempts, base_delay=base_delay, what=what, retry_on=retry_on,
    )


# ── Schema guard: source (view) × target (table) ──────────────────────────────────
# The mirror is a ``SELECT *`` and PostgREST matches columns by NAME. A column that
# exists in the view but NOT in the table takes down the WHOLE INSERT with PGRST204
# ("Could not find the 'X' column of 'Y' in the schema cache").
#
# This happened 3x in 2 days (welding branch block on 07-10; process flags and
# U_INO_ORCITM on 07-15) and each time cost tens of minutes, because the error:
#   * names only the FIRST missing column (fix one, the next appears);
#   * arrived buried under 3 backoff retries — and retrying a SCHEMA error is
#     pointless, it is deterministic;
#   * did not say what to do.
# The guard below checks BEFORE inserting and logs a ready-to-paste ALTER.
_PGRST_SCHEMA_CODES = ('PGRST204', 'PGRST205')
_ISO_DT_RE = re.compile(r'^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}')


def agora_iso() -> str:
    """Current instant in ISO-8601 **with offset** (e.g. ``2026-07-15T16:43:30-03:00``).

    ALWAYS use this instead of ``datetime.now().isoformat()`` when writing a timestamp to
    Supabase. Reason (measured 2026-07-15): a naive ``'16:43:30'`` in a ``timestamptz``
    column is read as **UTC** by Postgres and becomes ``16:43:30+00`` — silently 3h in the
    past. That was happening to ``data_hora_sincronizacao``, and the whole "Últimas
    sincronizações" panel showed the wrong time.

    With the offset, the instant is stored correctly in ``timestamptz`` and **nothing
    changes** for ``timestamp without time zone`` columns (e.g. ``data_hora_extracao``):
    Postgres drops the offset and keeps the wall-clock time, exactly as before. That is
    why the same function works for both cases.
    """
    return datetime.now().astimezone().isoformat()


def e_erro_de_schema(exc: object) -> bool:
    """True if the error is a PostgREST SCHEMA error (column/table missing from cache).

    Deterministic: retrying does not fix it, it only stalls and hides the message.
    """
    texto = str(exc)
    return any(codigo in texto for codigo in _PGRST_SCHEMA_CODES)


def _retry_se_transitorio(exc: Exception) -> bool:
    """``retry_on`` for ``with_retries``: retry everything except schema errors."""
    return not e_erro_de_schema(exc)


def _tipo_pg_sugerido(data: List[Dict[str, Any]], coluna: str) -> str:
    """Suggested Postgres type for the ALTER, inferred from the column's 1st non-null value.

    It is a SUGGESTION for a human to paste/review — not a truth about HANA.
    ``prepare_data`` has already normalized the types (numpy→int/float, dates→isoformat).
    All nulls ⇒ ``text``, which accepts anything.
    """
    for registro in data:
        valor = registro.get(coluna)
        if valor is None:
            continue
        if isinstance(valor, bool):      # before int: bool is a subclass of int
            return 'boolean'
        if isinstance(valor, int):
            return 'integer'
        if isinstance(valor, float):
            return 'numeric'
        if isinstance(valor, str) and _ISO_DT_RE.match(valor):
            return 'timestamp'
        return 'text'
    return 'text'


def alter_sugerido(table_name: str, faltando: List[str], data: List[Dict[str, Any]]) -> str:
    """Build the ready-to-paste ``ALTER TABLE`` that aligns the table with the source.

    Quoted names preserve the byte-exact case PostgREST requires, and the trailing
    ``notify pgrst`` keeps the next insert from failing against a stale cache.
    """
    adds = ',\n'.join(
        f'  add column if not exists "{col}" {_tipo_pg_sugerido(data, col)}'
        for col in faltando
    )
    return f"alter table public.{table_name}\n{adds};\n\nnotify pgrst, 'reload schema';"


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


# Positive integer (digits only) — used to validate numeric keys (e.g. NPED) before
# interpolating them into a query. Rejects signs, decimals and inner whitespace.
_POSITIVE_INT_RE = re.compile(r'^\d+$')


def coerce_positive_int(value: Any, *, what: str = "valor") -> int:
    """Validate and normalize ``value`` as a **positive** integer (injection defense).

    Accepts an ``int`` or a numeric string (surrounding whitespace allowed). Rejects
    non-digits, negatives and zero — guaranteeing an identifier that is safe to
    interpolate into SQL.

    Raises:
        ValueError: if ``value`` is not a positive integer.
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

    def colunas_da_tabela(self, table_name: str) -> Optional[Set[str]]:
        """Table columns in Supabase, or ``None`` if they cannot be determined.

        PostgREST does not expose the schema through a simple call, so we infer it from
        the KEYS of 1 real row. Accepted limitation: **an empty table returns ``None``**
        (no row, no keys) — in that case the schema guard is skipped and the insert's own
        PGRST204 becomes the diagnosis again. An explicit blind spot beats a false sense
        of checking.
        """
        try:
            res = self.client.table(table_name).select('*').limit(1).execute()
        except Exception as exc:
            logger.debug("[SCHEMA] não foi possível ler as colunas de '%s': %s", table_name, exc)
            return None
        linhas = res.data or []
        return set(linhas[0].keys()) if linhas else None

    def colunas_faltantes(self, table_name: str, data: List[Dict[str, Any]]) -> List[str]:
        """Record columns the table does NOT have — the cause of PGRST204.

        Returns:
            List (in source order) of the missing columns; ``[]`` if everything lines up
            **or** if it could not be determined (see ``colunas_da_tabela``).
        """
        if not data:
            return []
        colunas = self.colunas_da_tabela(table_name)
        if colunas is None:
            return []
        return [col for col in data[0] if col not in colunas]

    def insert_data(
        self, table_name: str, data: List[Dict[str, Any]], batch_size: int = INSERT_BATCH_SIZE
    ) -> bool:
        """Insert data into the Supabase table, in batches.

        Batching avoids blowing the PostgREST payload limit and adds resilience: each
        batch gets its own retry.

        Args:
            table_name: Table name.
            data: List of dictionaries holding the data.
            batch_size: Size of each batch.

        Returns:
            ``True`` if every batch was inserted; ``False`` otherwise.
        """
        total = len(data)
        if total == 0:
            logger.warning("Nenhum registro para inserir.")
            return True

        # Schema guard: fail BEFORE inserting, saying what to do (see
        # `colunas_faltantes`). Without it, PGRST204 costs 3 retries and a generic error.
        faltando = self.colunas_faltantes(table_name, data)
        if faltando:
            logger.error(
                "[SCHEMA] A origem tem %s coluna(s) que a tabela '%s' NAO tem: %s.\n"
                "O insert falharia com PGRST204. Rode no Supabase e sincronize de novo:\n\n"
                "%s\n",
                len(faltando), table_name, ', '.join(faltando),
                alter_sugerido(table_name, faltando, data),
            )
            return False

        try:
            for inicio in range(0, total, batch_size):
                lote = data[inicio:inicio + batch_size]
                num_lote = inicio // batch_size + 1
                with_retries(
                    lambda l=lote: self.client.table(table_name).insert(l).execute(),
                    what=f"insert lote {num_lote} ('{table_name}')",
                    retry_on=_retry_se_transitorio,  # schema errors fail on the 1st try
                )
                logger.info(
                    f"Lote {num_lote}: {len(lote)} registro(s) inseridos "
                    f"({min(inicio + batch_size, total)}/{total})"
                )
            logger.info(f"{total} registro(s) inseridos com sucesso na tabela '{table_name}'")
            return True
        except Exception as e:
            logger.error(f"Erro ao inserir dados no Supabase: {e}")
            self._reverter_parcial(table_name, data)
            return False

    def _reverter_parcial(self, table_name: str, data: List[Dict[str, Any]]) -> None:
        """Remove rows ALREADY inserted by this execution when the insert fails midway.

        The insert is batched and **there is no transaction across batches**: if batch 3
        of 5 blows up, batches 1-2 stay written. Since the prune only runs on success, the
        table was left with the PREVIOUS execution **+** part of the new one — and reads
        sum both, inflating ``total_orcamento`` and ``num_linhas``. Same class as the
        2026-07-06 incident (R$ 96.78 on a R$ 3M quote), by a different cause.

        Deleting by ``id_execucao`` is precise and safe: the UUID belongs to this load
        only, so there is no risk of taking good data with it. Best-effort — if the
        cleanup also fails, it logs the exact SQL for manual removal (a ready command
        beats a mystery).
        """
        exec_id = (data[0] or {}).get('id_execucao') if data else None
        if not exec_id:
            return
        try:
            res = self.client.table(table_name).delete().eq('id_execucao', exec_id).execute()
            removidas = len(res.data or [])
            if removidas:
                logger.warning(
                    "Insert falhou no meio: %s linha(s) parciais desta execução removidas de "
                    "'%s' — a tabela volta ao estado anterior (sem duplicata).",
                    removidas, table_name,
                )
        except Exception as exc:
            logger.error(
                "Insert falhou E a limpeza do parcial falhou em '%s': %s\n"
                "A tabela pode estar com linhas DUPLICADAS desta execução (a leitura vai "
                "somar em dobro). Remova com:\n"
                "    delete from public.%s where id_execucao = '%s';",
                table_name, exc, table_name, exec_id,
            )

    def delete_other_executions(
        self,
        table_name: str,
        keep_execution_id: str,
        *,
        where_eq: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Remove records from previous executions, preserving the current one.

        Used by the **load-then-prune** strategy: must be called AFTER a successful
        insert. That way, if extraction or loading fails, the old data stays intact and
        the table (or the filtered subset) is never empty.

        Args:
            table_name: Table name.
            keep_execution_id: ``id_execucao`` to preserve (the current load's).
            where_eq: Extra equality filters (``column -> value``) that **narrow** the
                prune. Without them, it deletes every previous execution in the table
                (the global ``snapshot`` mode of ``oportunidades``). With
                ``{'NPED': 84080}``, it only deletes that pedido's old rows (the
                ``replace_nped`` mode of ordens de serviço).

        Returns:
            ``True`` if the cleanup ran without error; ``False`` otherwise.
        """
        try:
            def _op():
                # `neq` ALONE does not catch rows with a NULL `id_execucao`: in SQL,
                # `NULL <> 'x'` evaluates to NULL (not TRUE) and DELETE skips the row —
                # measured. An orphan row (manual load, import, column added later) would
                # survive EVERY prune, forever, duplicating the data served. The `or_`
                # includes the nulls; where_eq's `eq` filters still AND with it.
                query = (
                    self.client.table(table_name)
                    .delete()
                    .or_(f'id_execucao.is.null,id_execucao.neq.{keep_execution_id}')
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
        """Write a sync log record and keep only the N most recent ones.

        Inserts a row with the local finish time, the duration and the status; then prunes
        the table down to the ``max_registros`` most recent records (deleting the oldest
        by ``id``, which is auto-incremented).

        This is an auxiliary operation: any failure here is only logged and does NOT
        interrupt or affect the main sync flow.

        Args:
            table_name: Log table name.
            data_hora: Machine-local time (ISO 8601) when the sync finished.
            duracao_seg: Process duration, in seconds.
            status: ``'sucesso'`` or ``'falha'``.
            qtd_registros: Number of records synced (optional).
            max_registros: Maximum number of records to keep in the table.
            extra_fields: Extra columns to write (e.g. ``{'nped': 84080}``).

        Returns:
            ``True`` if the record was written; ``False`` otherwise.
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
    """Convert the DataFrame into records ready to insert into Supabase.

    Adds the tracking fields ``id_execucao`` and ``data_hora_extracao`` and normalizes
    values that are not JSON-serializable (dates, Decimal, numpy types and NaN/NaT).

    Args:
        df: DataFrame holding the extracted data.
        execution_id: ID used to track the execution. Generated (UUID4) if not provided.

    Returns:
        A ``(records, execution_id)`` tuple, where ``records`` is the list of
        dictionaries ready for insertion and ``execution_id`` is the ID used.
    """
    if execution_id is None:
        execution_id = str(uuid.uuid4())

    extraction_datetime = agora_iso()   # with offset — see agora_iso()

    # Convert DataFrame to a list of dictionaries
    data_list = df.to_dict(orient='records')

    # Add execution and timestamp fields
    for record in data_list:
        record['id_execucao'] = execution_id
        record['data_hora_extracao'] = extraction_datetime
        # Handle NULL/NaN values and convert types that are not JSON-serializable
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
                # SAP INTEGER columns become float in pandas because of NaN
                # (e.g. 72051.0). Convert exact integers back to int so integer
                # columns are not violated; keep real decimals as float.
                f = float(value)
                record[key] = int(f) if f.is_integer() else f
            elif isinstance(value, np.bool_):
                record[key] = bool(value)

    logger.info(f"Dados preparados: {len(data_list)} registros com ID de execução '{execution_id}'")
    return data_list, execution_id
