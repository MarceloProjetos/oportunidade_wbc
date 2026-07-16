"""On-demand ETL: CONSOLIDATED SAP view ``VW_OS_INTEGRACAO`` (per N_PED) → Supabase.

Mirrors the single HANA view ``VW_OS_INTEGRACAO`` (OS + tree/structure + quote,
54 columns) into a single Supabase table — it replaced the old separate mirrors for
engineering OS, WBC tree and print views (2026-07-14 consolidation). The view keys on
``"N_PED"`` (with underscore).

Unlike ``extract_sap_to_supabase.py`` (oportunidades), this pipeline:

* is triggered **on demand** for one or more ``N_PED`` (it is not scheduled);
* does **not** enrich from SQL Server nor validate ``SITCOD``;
* uses the **``replace_nped``** strategy (replace per pedido): load-then-prune **scoped
  to the N_PED**, so the table accumulates several pedidos and each is updated
  independently, without affecting the others.

Reuses the generic core in ``pipeline_core`` (``SupabaseLoader``, ``prepare_data``,
``build_view_query``) and the shared connection in ``sap_connection``.

Usage (CLI)::

    python extract_ordens_servico_engenharia.py 84080
    python extract_ordens_servico_engenharia.py 84080 84095 84100   # several pedidos
"""

from __future__ import annotations

import logging
import sys
import time
from typing import Iterable, List, Optional

import pandas as pd

from config import (
    OS_EXECUTION_MODE_DEFAULT,
    OS_EXECUTION_MODES,
    OS_SYNC_LOG_MAX_REGISTROS,
    get_settings,
)
from pipeline_core import (
    SupabaseLoader,
    agora_iso,
    build_view_query,
    coerce_positive_int,
    os_sync_lock,
    prepare_data,
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
    as a lib (imported by the API) it must not touch global logging."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    )
    # httpx logs every request (URL with all columns) at INFO — noisy in production.
    logging.getLogger('httpx').setLevel(logging.WARNING)


def extract_os_to_dataframe(nped: object) -> Optional[pd.DataFrame]:
    """Extract the OS view rows for a single ``NPED``.

    Args:
        nped: Pedido number (integer or numeric string).

    Returns:
        DataFrame with the pedido's rows, an empty ``DataFrame`` if the pedido does not
        exist, or ``None`` on connection/query failure.

    Raises:
        ValueError: if ``nped`` is not a valid integer.
    """
    settings = get_settings()
    nped_int = coerce_positive_int(nped, what='NPED')  # propagates ValueError to the caller

    if not settings.sap_ready():
        logger.error("Faltam variáveis de ambiente obrigatórias do SAP")
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

    base = build_view_query(settings.os_sap_view_name, settings.sap_schema)
    # nped_int is a validated integer → safe to interpolate. VW_OS_INTEGRACAO uses "N_PED".
    query = f'SELECT * FROM {base} WHERE "N_PED" = {nped_int}'
    df = sap.execute_query(query)
    sap.close()

    if df is None:
        logger.error("Falha ao extrair OS do NPED %s", nped_int)
        return None

    logger.info("OS extraídas do SAP (NPED %s): %s linhas", nped_int, len(df))
    return df


def diagnosticar_nped(nped: object) -> dict:
    """Classify an NPED by querying OWOR (production order) and ORDR (pedido) in SAP.

    Rule (SAP B1): the OS exists when there is a row in ``OWOR`` with ``OriginNum`` = the
    pedido number. No row → OS not generated yet. If **all** rows have ``Status`` =
    ``'C'`` → OS cancelled. ``ORDR`` adds the **pedido** status (best-effort): it tells
    "pedido cancelled" and "pedido not found" apart from "open pedido that has not
    generated an OS yet".

    Returns:
        ``{'tem_os': bool, 'cancelada': bool, 'status': [...],
        'pedido_existe': bool|None, 'pedido_cancelado': bool|None,
        'pedido_status': 'Aberto'|'Cancelado'|'Fechado'|None}``
        (the ``pedido_*`` keys stay ``None`` if the ORDR query fails) or
        ``{'erro': '<reason>'}`` if OWOR cannot be queried.

    Raises:
        ValueError: if ``nped`` is not a positive integer.
    """
    settings = get_settings()
    nped_int = coerce_positive_int(nped, what='NPED')

    if not settings.sap_ready():
        return {'erro': 'sap_config'}

    sap = SAPExtractor(
        settings.sap_host, settings.sap_port, settings.sap_user,
        settings.sap_password, settings.sap_database,
    )
    if not sap.connect():
        return {'erro': 'sap_conexao'}

    base = build_view_query('OWOR', settings.sap_schema)  # "SCHEMA"."OWOR"
    # GROUP BY → only the DISTINCT statuses (few rows), instead of one row per OP.
    df = sap.execute_query(
        f'SELECT "Status" FROM {base} WHERE "OriginNum" = {nped_int} GROUP BY "Status"'
    )
    if df is None:
        sap.close()
        return {'erro': 'consulta'}

    # Pedido (ORDR), on the SAME connection — best-effort: a failure here does not
    # invalidate the OS diagnosis.
    ordr = build_view_query('ORDR', settings.sap_schema)  # "SCHEMA"."ORDR"
    df_ped = sap.execute_query(
        f'SELECT "CANCELED", "DocStatus" FROM {ordr} WHERE "DocNum" = {nped_int}'
    )
    sap.close()

    statuses = [str(s).strip() for s in df['Status'].tolist()] if len(df) else []
    tem_os = len(statuses) > 0
    cancelada = tem_os and all(s == 'C' for s in statuses)

    pedido_existe = pedido_cancelado = pedido_status = None
    if df_ped is not None:
        pedido_existe = len(df_ped) > 0
        pedido_cancelado = False
        if pedido_existe:
            row = df_ped.iloc[0]
            # CANCELED: 'Y' = cancelled, 'C' = reversal document (cancellation);
            # DocStatus 'C' without cancellation = pedido closed.
            pedido_cancelado = str(row.get('CANCELED', '')).strip() in ('Y', 'C')
            if pedido_cancelado:
                pedido_status = 'Cancelado'
            elif str(row.get('DocStatus', '')).strip() == 'C':
                pedido_status = 'Fechado'
            else:
                pedido_status = 'Aberto'

    return {'tem_os': tem_os, 'cancelada': cancelada, 'status': statuses,
            'pedido_existe': pedido_existe, 'pedido_cancelado': pedido_cancelado,
            'pedido_status': pedido_status}


def listar_pedidos_com_os(limit: int = 30) -> Optional[List[dict]]:
    """List up to ``limit`` pedidos (NPED) with an OS created in SAP, newest first.

    Rule (same as ``diagnosticar_nped``): the OS exists when there is a row in ``OWOR``
    with ``OriginNum`` = the pedido number. Pedidos whose OS is **fully cancelled** (every
    row with ``Status = 'C'``) are excluded — we filter ``Status <> 'C'`` before grouping.
    LEFT JOINs ``OWOR`` with ``ORDR`` (pedido) to bring in the customer name
    (``CardName``).

    Args:
        limit: maximum number of pedidos to return.

    Returns:
        List of ``{'nped': int, 'cliente': str|None, 'os': int|None, 'data': str|None}``
        sorted newest to oldest, or ``None`` on failure.
    """
    settings = get_settings()
    limit_int = coerce_positive_int(limit, what='limit')

    if not settings.sap_ready():
        logger.error("Faltam variáveis de ambiente obrigatórias do SAP")
        return None

    sap = SAPExtractor(
        settings.sap_host, settings.sap_port, settings.sap_user,
        settings.sap_password, settings.sap_database,
    )
    if not sap.connect():
        return None

    owor = build_view_query('OWOR', settings.sap_schema)  # "SCHEMA"."OWOR"
    ordr = build_view_query('ORDR', settings.sap_schema)  # "SCHEMA"."ORDR"
    # limit_int is a validated integer → safe to interpolate. OriginNum > 0 discards
    # manual OPs (no originating pedido). MAX(DocEntry) sorts by the newest OS.
    query = (
        f'SELECT T0."OriginNum" AS "NPED", MAX(T1."CardName") AS "Cliente", '
        f'MAX(T0."DocNum") AS "OS", MAX(T0."PostDate") AS "Data" '
        f'FROM {owor} T0 LEFT JOIN {ordr} T1 ON T1."DocNum" = T0."OriginNum" '
        f"WHERE T0.\"OriginNum\" > 0 AND T0.\"Status\" <> 'C' "
        f'GROUP BY T0."OriginNum" '
        f'ORDER BY MAX(T0."DocEntry") DESC '
        f'LIMIT {limit_int}'
    )
    df = sap.execute_query(query)
    sap.close()

    if df is None:
        logger.error("Falha ao listar pedidos com OS no SAP")
        return None

    pedidos: List[dict] = []
    for _, row in df.iterrows():
        if pd.isna(row.get('NPED')):
            continue
        data = row.get('Data')
        cliente = row.get('Cliente')
        os_num = row.get('OS')
        pedidos.append({
            'nped': int(row['NPED']),
            'cliente': str(cliente).strip() if pd.notna(cliente) else None,
            'os': int(os_num) if pd.notna(os_num) else None,
            'data': data.isoformat() if hasattr(data, 'isoformat') else (
                str(data) if pd.notna(data) else None),
        })
    logger.info("Pedidos com OS listados do SAP: %s", len(pedidos))
    return pedidos


def main(
    nped: object,
    execution_mode: str = OS_EXECUTION_MODE_DEFAULT,
    execution_id: Optional[str] = None,
) -> bool:
    """Sync a single ``NPED`` into the Ordens de Serviço (Engenharia) table.

    Args:
        nped: Pedido to sync.
        execution_mode: ``'replace_nped'`` (default — replaces that NPED's rows) or
            ``'insert'`` (accumulate only, keeping history by ``id_execucao``).
        execution_id: Custom ID (UUID generated automatically if ``None``).

    Returns:
        ``True`` if it completed successfully; ``False`` otherwise.
    """
    settings = get_settings()

    if execution_mode not in OS_EXECUTION_MODES:
        logger.error(
            "execution_mode inválido: %r. Valores aceitos: %s",
            execution_mode, ', '.join(OS_EXECUTION_MODES),
        )
        return False

    if not settings.supabase_ready():
        logger.error("Faltam variáveis de ambiente obrigatórias do Supabase")
        return False

    inicio = time.monotonic()
    qtd_registros = 0
    resultado = False
    nped_int: Optional[int] = None
    loader: Optional[SupabaseLoader] = None

    try:
        nped_int = coerce_positive_int(nped, what='NPED')
    except ValueError:
        logger.error("NPED inválido (esperado inteiro): %r", nped)
        return False

    try:
        logger.info("Extraindo OS do NPED %s...", nped_int)
        df = extract_os_to_dataframe(nped_int)
        if df is None:
            logger.error("Extração falhou para o NPED %s", nped_int)
            return False

        if len(df) == 0:
            # Pedido missing/with no rows in the view: do NOT delete what is already
            # there, so a valid pedido already loaded is not removed by mistake.
            logger.warning(
                "NPED %s não retornou linhas na view; tabela mantida inalterada.",
                nped_int,
            )
            return False

        logger.info("Carregando %s linha(s) do NPED %s no Supabase...", len(df), nped_int)
        loader = SupabaseLoader(settings.supabase_url, settings.supabase_write_key)

        data_to_insert, exec_id = prepare_data(df, execution_id)
        qtd_registros = len(data_to_insert)

        # Cross-process PER-PEDIDO lock around insert+prune: the two together are what
        # must be exclusive. Without it, the API and the CLI could write the same N_PED
        # and each prune the other's rows, DELETING the pedido (api.py's `_sync_lock` is
        # a threading.Lock and cannot see another process). See `os_sync_lock`.
        with os_sync_lock(nped_int):
            success = loader.insert_data(
                settings.os_table_name, data_to_insert,
                batch_size=settings.os_insert_batch_size,
            )

            # replace_nped: load-then-prune SCOPED to the NPED — THIS pedido's old rows
            # are only removed after the insert succeeds (the pedido is never empty).
            if success and execution_mode == 'replace_nped':
                if not loader.delete_other_executions(
                    settings.os_table_name, exec_id, where_eq={'N_PED': nped_int}
                ):
                    # NOT a success: the replace_nped contract ("replaces, does not
                    # duplicate") was not met. The table holds TWO executions of the
                    # pedido and reads sum both (inflated total_orcamento). This used to
                    # be a WARNING with the function returning True — the log said
                    # 'sucesso' with a corrupted table. Re-syncing consolidates it.
                    logger.error(
                        "Inserção OK mas a PODA do NPED %s falhou: a tabela está com DUAS "
                        "execuções deste pedido e a leitura vai somar em dobro. "
                        "Re-sincronize para consolidar.", nped_int,
                    )
                    return False

        if success:
            logger.info("✓ NPED %s sincronizado (id_execucao: %s)", nped_int, exec_id)
            resultado = True
            return True

        logger.error("✗ Erro ao carregar o NPED %s no Supabase", nped_int)
        return False

    except Exception as exc:
        logger.error("Erro ao sincronizar o NPED %s: %s", nped_int, exc)
        return False
    finally:
        # Auxiliary log (never affects the main result).
        try:
            duracao = time.monotonic() - inicio
            data_hora_pc = agora_iso()   # with offset: the column is timestamptz (see agora_iso)
            status = 'sucesso' if resultado else 'falha'
            log_loader = loader or SupabaseLoader(
                settings.supabase_url, settings.supabase_write_key
            )
            log_loader.registrar_sincronizacao(
                settings.os_sync_log_table,
                data_hora_pc,
                duracao,
                status,
                qtd_registros,
                max_registros=OS_SYNC_LOG_MAX_REGISTROS,
                extra_fields={'nped': nped_int},
            )
        except Exception as log_exc:
            logger.error("Falha ao registrar log de sincronização (ignorada): %s", log_exc)


def run_npeds(npeds: Iterable[object]) -> dict:
    """Sync several NPEDs in sequence. Returns ``{nped: bool}`` with the outcome."""
    resultados: dict = {}
    for n in npeds:
        resultados[n] = main(n)
    ok = sum(1 for v in resultados.values() if v)
    logger.info("Concluído: %s/%s NPED(s) sincronizado(s) com sucesso", ok, len(resultados))
    return resultados


def _parse_args(argv: List[str]) -> List[str]:
    return [a for a in argv if a.strip()]


if __name__ == "__main__":
    _configure_logging()
    args = _parse_args(sys.argv[1:])
    if not args:
        print(
            "Uso: python extract_ordens_servico_engenharia.py <NPED> [<NPED> ...]\n"
            "Ex.: python extract_ordens_servico_engenharia.py 84080 84095"
        )
        raise SystemExit(2)
    resultados = run_npeds(args)
    # exit code 0 only if every one succeeded
    raise SystemExit(0 if all(resultados.values()) else 1)
