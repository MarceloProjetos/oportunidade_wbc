"""Sub-sync sob demanda: árvore de produto WBC (SQL Server) → Supabase, por pedido.

Disparada **após** a sincronização da OS de um NPED (quando a OS está OK). Fluxo:

1. resolve o ``ORCNUM`` (código WBC) do pedido — é o ``NºOrçament`` na view de OS do SAP;
2. extrai ``SELECT * FROM WBCCAD.dbo.INTEGRACAO_ORCPRDARV WHERE ORCNUM = <orcnum>``;
3. grava no Supabase em ``wbc_arvore_produto`` com estratégia **replace por ORCNUM**
   (carrega-depois-poda escopado ao orçamento — re-sync troca só aquela árvore).

Reaproveita ``pipeline_core`` (``SupabaseLoader``, ``prepare_data``), ``sap_connection``
(resolver o ORCNUM) e ``extract_sap_to_supabase.get_sqlserver_connection`` (extrair a árvore).

Uso (CLI)::

    python extract_wbc_arvore.py 83913        # sincroniza a árvore do pedido 83913
"""

from __future__ import annotations

import logging
import sys
import time
from datetime import datetime
from typing import Optional

import pandas as pd

from config import (
    WBC_ARVORE_SYNC_LOG_MAX_REGISTROS,
    get_settings,
)
from db_utils import read_dbapi_query
from pipeline_core import (
    SupabaseLoader,
    build_view_query,
    coerce_positive_int,
    prepare_data,
    validate_sql_identifier,
)
from sap_connection import SAPExtractor

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except AttributeError:
    pass

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    """Log básico no console. Chamado só pelo entrypoint (CLI), não no import."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    )
    logging.getLogger('httpx').setLevel(logging.WARNING)


def _normaliza_orcnum(raw: object) -> Optional[str]:
    """Normaliza o NºOrçament para o formato do ORCNUM (nvarchar(8), zero-à-esquerda).

    ``'123822'`` → ``'00123822'``; ``'00123822'`` → ``'00123822'``; vazio/None → ``None``.
    Valores não numéricos são apenas aparados (mantidos como vieram).
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s.lower() == 'none':
        return None
    return s.zfill(8) if s.isdigit() else s


def resolver_orcnum(nped: object) -> Optional[str]:
    """Descobre o ORCNUM de um pedido na view de OS do SAP.

    O ORCNUM vem de ``CodigoOrcam`` (preenchido), com fallback em ``NºOrçament`` — na
    prática essa view traz o código em ``CodigoOrcam`` (o ``NºOrçament`` costuma vir nulo).

    Returns:
        ORCNUM normalizado (8 dígitos) ou ``None`` se o pedido não tiver orçamento
        ou em caso de falha de conexão/consulta.
    """
    settings = get_settings()
    nped_int = coerce_positive_int(nped, what='NPED')

    if not settings.sap_ready():
        logger.error("Faltam variáveis de ambiente obrigatórias do SAP")
        return None

    sap = SAPExtractor(
        settings.sap_host, settings.sap_port, settings.sap_user,
        settings.sap_password, settings.sap_database,
    )
    if not sap.connect():
        return None

    base = build_view_query(settings.os_sap_view_name, settings.sap_schema)
    # nped_int é inteiro validado → seguro interpolar.
    df = sap.execute_query(
        f'SELECT DISTINCT COALESCE("CodigoOrcam", "NºOrçament") AS "ORCNUM" FROM {base} '
        f'WHERE "NPED" = {nped_int} AND COALESCE("CodigoOrcam", "NºOrçament") IS NOT NULL'
    )
    sap.close()

    if df is None or len(df) == 0:
        logger.warning("NPED %s sem ORCNUM (CodigoOrcam/NºOrçament) na view de OS.", nped_int)
        return None
    if len(df) > 1:
        logger.warning("NPED %s tem %s ORCNUM distintos; usando o primeiro.", nped_int, len(df))
    return _normaliza_orcnum(df.iloc[0, 0])


def extract_arvore_to_dataframe(orcnum: str) -> Optional[pd.DataFrame]:
    """Extrai a árvore de produto do ORCNUM da INTEGRACAO_ORCPRDARV (SQL Server).

    Returns:
        DataFrame com as linhas (vazio se o ORCNUM não tiver árvore) ou ``None`` em
        caso de falha de conexão/consulta.
    """
    from extract_sap_to_supabase import get_sqlserver_connection  # lazy: evita ciclo

    settings = get_settings()
    if not settings.sql_ready():
        logger.error("SQL Server (WBC) não configurado (.env)")
        return None

    view = validate_sql_identifier(settings.wbc_arvore_view, what='view WBC árvore')
    conn = get_sqlserver_connection(
        settings.sql_host, settings.sql_port, settings.sql_user,
        settings.sql_password, settings.sql_database, settings.sql_driver,
    )
    if conn is None:
        return None
    try:
        # ORCNUM via parâmetro (?) — consulta parametrizada, sem injeção.
        df = read_dbapi_query(f'SELECT * FROM {view} WHERE ORCNUM = ?', conn, params=(orcnum,))
    except Exception as exc:
        logger.error("Falha ao extrair a árvore WBC (ORCNUM %s): %s", orcnum, exc)
        return None
    finally:
        conn.close()
    logger.info("Árvore WBC extraída (ORCNUM %s): %s linhas", orcnum, len(df))
    return df


def main(nped: object) -> bool:
    """Sincroniza a árvore de produto WBC do pedido ``nped`` (replace por ORCNUM).

    Returns:
        ``True`` se sincronizou linhas; ``False`` se não havia orçamento/árvore ou em erro.
    """
    settings = get_settings()
    if not settings.supabase_ready():
        logger.error("Faltam variáveis de ambiente obrigatórias do Supabase")
        return False

    try:
        nped_int = coerce_positive_int(nped, what='NPED')
    except ValueError:
        logger.error("NPED inválido (esperado inteiro): %r", nped)
        return False

    inicio = time.monotonic()
    qtd_registros = 0
    resultado = False
    orcnum: Optional[str] = None
    loader: Optional[SupabaseLoader] = None

    try:
        orcnum = resolver_orcnum(nped_int)
        if not orcnum:
            logger.warning("NPED %s sem ORCNUM; árvore WBC não sincronizada.", nped_int)
            return False
        logger.info("WBC árvore: NPED %s → ORCNUM %s", nped_int, orcnum)

        df = extract_arvore_to_dataframe(orcnum)
        if df is None:
            logger.error("Extração da árvore WBC falhou (ORCNUM %s)", orcnum)
            return False
        if len(df) == 0:
            logger.warning("ORCNUM %s não retornou linhas em %s; tabela mantida inalterada.",
                           orcnum, settings.wbc_arvore_view)
            return False

        loader = SupabaseLoader(settings.supabase_url, settings.supabase_write_key)
        data_to_insert, exec_id = prepare_data(df)
        qtd_registros = len(data_to_insert)

        success = loader.insert_data(
            settings.wbc_arvore_table, data_to_insert,
            batch_size=settings.wbc_arvore_insert_batch_size,
        )
        # replace por ORCNUM: poda escopada SÓ após a inserção dar certo.
        if success and not loader.delete_other_executions(
            settings.wbc_arvore_table, exec_id, where_eq={'ORCNUM': orcnum}
        ):
            logger.warning(
                "Inserção OK, mas a poda das linhas antigas do ORCNUM %s falhou "
                "(pode haver duplicação de cargas anteriores).", orcnum,
            )

        if success:
            logger.info("✓ Árvore WBC do ORCNUM %s sincronizada (%s linha(s))", orcnum, qtd_registros)
            resultado = True
            return True

        logger.error("✗ Erro ao carregar a árvore WBC do ORCNUM %s no Supabase", orcnum)
        return False

    except Exception as exc:
        logger.error("Erro ao sincronizar a árvore WBC (NPED %s): %s", nped_int, exc)
        return False
    finally:
        # Log auxiliar (nunca afeta o resultado principal).
        try:
            duracao = time.monotonic() - inicio
            data_hora_pc = datetime.now().isoformat()
            status = 'sucesso' if resultado else 'falha'
            log_loader = loader or SupabaseLoader(
                settings.supabase_url, settings.supabase_write_key
            )
            log_loader.registrar_sincronizacao(
                settings.wbc_arvore_sync_log,
                data_hora_pc,
                duracao,
                status,
                qtd_registros,
                max_registros=WBC_ARVORE_SYNC_LOG_MAX_REGISTROS,
                extra_fields={'nped': nped_int, 'orcnum': orcnum},
            )
        except Exception as log_exc:
            logger.error("Falha ao registrar log da árvore WBC (ignorada): %s", log_exc)


if __name__ == "__main__":
    _configure_logging()
    args = [a for a in sys.argv[1:] if a.strip()]
    if not args:
        print("Uso: python extract_wbc_arvore.py <NPED> [<NPED> ...]")
        raise SystemExit(2)
    ok_all = all(main(a) for a in args)
    raise SystemExit(0 if ok_all else 1)
