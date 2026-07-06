"""ETL sob demanda: views de impressão de OS (SAP HANA) → Supabase, por NPED.

Espelha **1:1** três views do SAP HANA (schema ``SBOALTAMIRAPROD``) para tabelas
Supabase de **mesmo nome** (minúsculo), disparado **após** a sync da OS de um NPED:

    VW_OS_EXPED_IMPRESSAO_V2  → vw_os_exped_impressao_v2   (55 colunas)
    VW_OS_PINTURA_V0          → vw_os_pintura_v0            (55 colunas)
    VW_OS_ALMOX_IMPRESSAO     → vw_os_almox_impressao       (34 colunas)

Todas filtram por ``"NPED"`` (inteiro) e usam a estratégia **``replace_nped``**
(carrega-depois-poda ESCOPADO ao NPED), idêntica à de
``extract_ordens_servico_engenharia``: a tabela acumula vários pedidos e cada um é
re-sincronizado de forma independente, sem afetar os demais. As três views são lidas
numa **única conexão HANA** (uma query por view) e carregadas em seguida.

Reaproveita ``pipeline_core`` (``SupabaseLoader``, ``prepare_data``, ``build_view_query``)
e ``sap_connection`` (``SAPExtractor``). O mapa view→tabela vem de
``config.OS_IMPRESSAO_VIEWS``.

Uso (CLI)::

    python extract_os_impressao_views.py 84080
    python extract_os_impressao_views.py 84080 84095   # vários pedidos
"""

from __future__ import annotations

import logging
import sys
import time
from datetime import datetime
from typing import Dict, Optional

import pandas as pd

from config import (
    OS_IMPRESSAO_SYNC_LOG_MAX_REGISTROS,
    OS_IMPRESSAO_VIEWS,
    get_settings,
)
from pipeline_core import (
    SupabaseLoader,
    build_view_query,
    coerce_positive_int,
    prepare_data,
)
from sap_connection import SAPExtractor

# UTF-8 console no Windows
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except AttributeError:
    pass

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    """Log básico no console. Chamado só pelo entrypoint (CLI), não no import —
    como lib (importado pela API), não deve mexer no logging global."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    )
    logging.getLogger('httpx').setLevel(logging.WARNING)


def _extract_views_by_nped(nped_int: int) -> Optional[Dict[str, Optional[pd.DataFrame]]]:
    """Extrai as 3 views de impressão de um NPED numa única conexão HANA.

    Returns:
        Dict ``{nome_tabela: DataFrame|None}`` (``None`` = a query daquela view falhou;
        DataFrame vazio = view sem linhas para o pedido), ou ``None`` se nem a conexão
        HANA foi possível.
    """
    settings = get_settings()
    sap = SAPExtractor(
        settings.sap_host, settings.sap_port, settings.sap_user,
        settings.sap_password, settings.sap_database,
    )
    if not sap.connect():
        return None

    frames: Dict[str, Optional[pd.DataFrame]] = {}
    try:
        for view_name, table_name in OS_IMPRESSAO_VIEWS:
            base = build_view_query(view_name, settings.sap_schema)
            # nped_int é inteiro validado → seguro interpolar.
            df = sap.execute_query(f'SELECT * FROM {base} WHERE "NPED" = {nped_int}')
            if df is None:
                logger.error("Falha ao extrair %s (NPED %s)", view_name, nped_int)
            else:
                logger.info("%s extraída (NPED %s): %s linhas", view_name, nped_int, len(df))
            frames[table_name] = df
    finally:
        sap.close()
    return frames


def _load_one(
    loader: SupabaseLoader,
    view_name: str,
    table_name: str,
    df: Optional[pd.DataFrame],
    nped_int: int,
) -> bool:
    """Carrega uma view no Supabase (replace_nped por NPED) e registra o log.

    Returns:
        ``True`` se inseriu linhas com sucesso; ``False`` se a extração falhou, a view
        não tinha linhas (tabela mantida intacta) ou a carga falhou.
    """
    settings = get_settings()
    inicio = time.monotonic()
    qtd_registros = 0
    resultado = False
    try:
        if df is None:
            logger.error("Extração de %s falhou (NPED %s); tabela %s inalterada.",
                         view_name, nped_int, table_name)
            return False
        if len(df) == 0:
            # Pedido sem linhas nesta view: NÃO poda — mantém o pedido já carregado
            # intacto (mesma regra defensiva do pipeline de OS por NPED).
            logger.warning("%s sem linhas p/ NPED %s; tabela %s mantida inalterada.",
                           view_name, nped_int, table_name)
            return False

        data_to_insert, exec_id = prepare_data(df)
        qtd_registros = len(data_to_insert)

        success = loader.insert_data(
            table_name, data_to_insert,
            batch_size=settings.os_impressao_insert_batch_size,
        )
        # replace_nped: poda escopada ao NPED SÓ após a inserção dar certo.
        if success and not loader.delete_other_executions(
            table_name, exec_id, where_eq={'NPED': nped_int}
        ):
            logger.warning(
                "Inserção OK, mas a poda das linhas antigas do NPED %s em %s falhou "
                "(pode haver duplicação de cargas anteriores).", nped_int, table_name,
            )

        if success:
            logger.info("✓ %s → %s: NPED %s sincronizado (%s linha(s))",
                        view_name, table_name, nped_int, qtd_registros)
            resultado = True
            return True

        logger.error("✗ Erro ao carregar %s no Supabase (NPED %s)", table_name, nped_int)
        return False
    except Exception as exc:
        logger.error("Erro ao carregar %s (NPED %s): %s", table_name, nped_int, exc)
        return False
    finally:
        # Log auxiliar (nunca afeta o resultado principal): uma linha por view.
        try:
            duracao = time.monotonic() - inicio
            loader.registrar_sincronizacao(
                settings.os_impressao_sync_log_table,
                datetime.now().isoformat(),
                duracao,
                'sucesso' if resultado else 'falha',
                qtd_registros,
                max_registros=OS_IMPRESSAO_SYNC_LOG_MAX_REGISTROS,
                extra_fields={'nped': nped_int, 'origem_view': table_name},
            )
        except Exception as log_exc:
            logger.error("Falha ao registrar log de %s (ignorada): %s", table_name, log_exc)


def sync_impressao_views(nped: object) -> Dict[str, bool]:
    """Sincroniza as 3 views de impressão de um NPED. Best-effort e independente por view.

    Returns:
        Dict ``{nome_tabela: bool}`` com o resultado de cada view. Tabelas cuja view não
        tinha linhas para o pedido saem ``False`` (não é erro — a tabela fica intacta).
        Dict todo ``False`` se NPED inválido, ambiente incompleto ou HANA inacessível.
    """
    settings = get_settings()
    results: Dict[str, bool] = {table: False for _, table in OS_IMPRESSAO_VIEWS}

    try:
        nped_int = coerce_positive_int(nped, what='NPED')
    except ValueError:
        logger.error("NPED inválido (esperado inteiro): %r", nped)
        return results

    if not settings.sap_ready():
        logger.error("Faltam variáveis de ambiente obrigatórias do SAP")
        return results
    if not settings.supabase_ready():
        logger.error("Faltam variáveis de ambiente obrigatórias do Supabase")
        return results

    frames = _extract_views_by_nped(nped_int)
    if frames is None:
        logger.error("Views de impressão: conexão HANA falhou (NPED %s)", nped_int)
        return results

    loader = SupabaseLoader(settings.supabase_url, settings.supabase_write_key)
    for view_name, table_name in OS_IMPRESSAO_VIEWS:
        results[table_name] = _load_one(
            loader, view_name, table_name, frames.get(table_name), nped_int
        )
    return results


def main(nped: object) -> bool:
    """Sincroniza as views de impressão de um NPED (entrypoint CLI/lib).

    Returns:
        ``True`` se **pelo menos uma** view sincronizou linhas; ``False`` caso contrário.
        (Views sem linhas para o pedido não contam como falha real.)
    """
    return any(sync_impressao_views(nped).values())


if __name__ == "__main__":
    _configure_logging()
    args = [a for a in sys.argv[1:] if a.strip()]
    if not args:
        print(
            "Uso: python extract_os_impressao_views.py <NPED> [<NPED> ...]\n"
            "Ex.: python extract_os_impressao_views.py 84080 84095"
        )
        raise SystemExit(2)
    ok_all = all(main(a) for a in args)
    raise SystemExit(0 if ok_all else 1)
