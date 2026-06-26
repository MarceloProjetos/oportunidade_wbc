"""ETL sob demanda: view SAP ``VW_EXPORT_ORDENS_SERVICO_1`` (por NPED) → Supabase.

Diferente de ``extract_sap_to_supabase.py`` (oportunidades), este pipeline:

* é acionado **sob demanda** para um ou mais ``NPED`` (não é agendado);
* **não** faz enriquecimento com SQL Server nem validação de ``SITCOD``;
* usa a estratégia **``replace_nped``** (substituição por pedido): carrega-depois-poda
  **escopado ao NPED**, de modo que a tabela acumula vários pedidos e cada um é
  atualizado de forma independente, sem afetar os demais.

Reaproveita o núcleo genérico em ``pipeline_core`` (``SupabaseLoader``, ``prepare_data``,
``build_view_query``) e a conexão compartilhada em ``sap_connection``.

Uso (CLI)::

    python extract_ordens_servico_engenharia.py 84080
    python extract_ordens_servico_engenharia.py 84080 84095 84100   # vários pedidos
"""

from __future__ import annotations

import sys
import time
import logging
from datetime import datetime
from typing import Iterable, List, Optional

import pandas as pd

from config import (
    OS_EXECUTION_MODE_DEFAULT,
    OS_EXECUTION_MODES,
    OS_SYNC_LOG_MAX_REGISTROS,
    get_settings,
)
from pipeline_core import SupabaseLoader, build_view_query, coerce_positive_int, prepare_data
from sap_connection import SAPExtractor

# UTF-8 console on Windows
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except AttributeError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)
# httpx loga cada requisição (URL com todas as colunas) em INFO — ruidoso em produção.
logging.getLogger('httpx').setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


def extract_os_to_dataframe(nped: object) -> Optional[pd.DataFrame]:
    """Extrai as linhas da view de OS para um único ``NPED``.

    Args:
        nped: Número do pedido (inteiro ou string numérica).

    Returns:
        DataFrame com as linhas do pedido, ``DataFrame`` vazio se o pedido não existir,
        ou ``None`` em caso de falha de conexão/consulta.

    Raises:
        ValueError: se ``nped`` não for um inteiro válido.
    """
    settings = get_settings()
    nped_int = coerce_positive_int(nped, what='NPED')  # propaga ValueError p/ o chamador tratar

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
    # nped_int é inteiro validado → seguro interpolar
    query = f'SELECT * FROM {base} WHERE "NPED" = {nped_int}'
    df = sap.execute_query(query)
    sap.close()

    if df is None:
        logger.error("Falha ao extrair OS do NPED %s", nped_int)
        return None

    logger.info("OS extraídas do SAP (NPED %s): %s linhas", nped_int, len(df))
    return df


def main(
    nped: object,
    execution_mode: str = OS_EXECUTION_MODE_DEFAULT,
    execution_id: Optional[str] = None,
) -> bool:
    """Sincroniza um único ``NPED`` para a tabela de Ordens de Serviço (Engenharia).

    Args:
        nped: Pedido a sincronizar.
        execution_mode: ``'replace_nped'`` (default — substitui as linhas daquele NPED)
            ou ``'insert'`` (apenas acumula, mantendo histórico por ``id_execucao``).
        execution_id: ID customizado (UUID gerado automaticamente se ``None``).

    Returns:
        ``True`` se concluiu com sucesso; ``False`` caso contrário.
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
            # Pedido inexistente/sem linhas na view: NÃO apaga o que já existe,
            # para não remover por engano um pedido válido já carregado.
            logger.warning(
                "NPED %s não retornou linhas na view; tabela mantida inalterada.",
                nped_int,
            )
            return False

        logger.info("Carregando %s linha(s) do NPED %s no Supabase...", len(df), nped_int)
        loader = SupabaseLoader(settings.supabase_url, settings.supabase_write_key)

        data_to_insert, exec_id = prepare_data(df, execution_id)
        qtd_registros = len(data_to_insert)

        success = loader.insert_data(
            settings.os_table_name, data_to_insert, batch_size=settings.os_insert_batch_size
        )

        # replace_nped: carrega-depois-poda ESCOPADO ao NPED — só removemos as linhas
        # antigas DESTE pedido após a inserção dar certo (a tabela nunca fica sem o pedido).
        if success and execution_mode == 'replace_nped':
            if not loader.delete_other_executions(
                settings.os_table_name, exec_id, where_eq={'NPED': nped_int}
            ):
                logger.warning(
                    "Inserção OK, mas a poda das linhas antigas do NPED %s falhou. "
                    "Pode haver linhas duplicadas de cargas anteriores deste pedido.",
                    nped_int,
                )

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
        # Log auxiliar (nunca afeta o resultado principal).
        try:
            duracao = time.monotonic() - inicio
            data_hora_pc = datetime.now().isoformat()
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
    """Sincroniza vários NPEDs em sequência. Retorna ``{nped: bool}`` com o resultado."""
    resultados: dict = {}
    for n in npeds:
        resultados[n] = main(n)
    ok = sum(1 for v in resultados.values() if v)
    logger.info("Concluído: %s/%s NPED(s) sincronizado(s) com sucesso", ok, len(resultados))
    return resultados


def _parse_args(argv: List[str]) -> List[str]:
    return [a for a in argv if a.strip()]


if __name__ == "__main__":
    args = _parse_args(sys.argv[1:])
    if not args:
        print(
            "Uso: python extract_ordens_servico_engenharia.py <NPED> [<NPED> ...]\n"
            "Ex.: python extract_ordens_servico_engenharia.py 84080 84095"
        )
        raise SystemExit(2)
    resultados = run_npeds(args)
    # código de saída 0 só se todos deram certo
    raise SystemExit(0 if all(resultados.values()) else 1)
