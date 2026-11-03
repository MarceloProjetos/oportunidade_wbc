"""ETL: VW_EVOL_ORCAMENTO_ALT (SAP HANA) → Supabase ``orcamentos_espelho``.

The view was rebuilt in 09/2026 and went from 12 to 34 columns: it gained the customer's CNAE,
the assembly block (``TipoMontagem``/``ValorMontagem``/``Montador``) and the invoice block
(``NumNF``/``DataNF``/``QuitacaoNF``). This mirror takes them to Supabase so the web and the app
can read them without depending on HANA.

**Snapshot, like the oportunidades one**: inserts the new load and only then deletes the previous
executions (load-then-prune) -- if the load fails, the table keeps the previous state instead of
ending up empty.

Three decisions the SQL carries, and why (probe of 17/09/2026 over the whole view, 5.643 rows):

- ``nf_quitada`` is a **three-state boolean**: ``NULL`` when there is no invoice. ``QuitacaoNF``
  alone lies -- 4.310 rows say ``'Não'`` only because no invoice was issued, and whoever read that
  as "open invoice" would be off by a factor of three. Writing the rule here settles it once for
  every reader;
- ``AcaoContato``, ``Lead`` and ``SituacaoCliente`` come as an EMPTY STRING, never ``NULL``; they
  enter the mirror as ``NULL`` (``NULLIF``), otherwise every Postgres report would have to
  remember to test ``<> ''``;
- the window is the whole view: it **no longer has 2024** (it starts on 06/01/2025), and
  filtering by date here would only hide that from whoever reads the mirror.

In 10/2026 the view gained a 35th column, ``Incoterms`` (freight mode, already a text
label). Only order rows (``TipoDoc = '17'``) carry it; quotations are always ``NULL``.
The Supabase column must exist before this code runs (``sql/orcamentos_espelho.sql``).
The 36th, ``ObsMontagem`` (free text, up to 2000 chars, orders only), arrives with SAP's
bare CR line breaks turned into LF, the same text the ``/pedidos`` API returns.

``VW_ORCAMENTO_ALT``, the other quotation view, remains at 16 columns with none of the new
fields -- which is why the mirror is of this view and only this one.
"""

from __future__ import annotations

import logging
import sys
import time
from datetime import datetime
from typing import Any

from config import get_settings
from pipeline_core import (
    SupabaseLoader,
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

#: Tabela espelho no Supabase (DDL em ``sql/orcamentos_espelho.sql``).
TABELA = "orcamentos_espelho"

VIEW = "VW_EVOL_ORCAMENTO_ALT"

ROTINA_NOME = "ORCAMENTOS_ESPELHO"
ROTINA_ROTULO = "Espelho dos orçamentos do SAP"

_LOG = "[ORCAMENTOS_ESPELHO]"


def sql_orcamentos(schema: str) -> str:
    """The whole view, with names already in Postgres ``snake_case``."""
    validate_sql_identifier(schema)
    return f'''
        SELECT "Cotacao" AS "cotacao",
               "TipoDoc" AS "tipo_doc",
               "NumDoc" AS "num_doc",
               "NumOport" AS "num_oport",
               "StatusWBC" AS "status_wbc",
               "N_WBC" AS "n_wbc",
               NULLIF(TRIM("Versao"), '') AS "versao",
               "DataCriacaoPN" AS "data_criacao_pn",
               "CodPN" AS "cod_pn",
               "NomePN" AS "nome_pn",
               NULLIF(TRIM("Contato_Cliente"), '') AS "contato_cliente",
               NULLIF(TRIM("Email_Cliente"), '') AS "email_cliente",
               "Representante" AS "representante",
               "Valor" AS "valor",
               "DataOport" AS "data_oport",
               "DataCotacao" AS "data_cotacao",
               "UF" AS "uf",
               "Municipio" AS "municipio",
               "DataContatoCliente" AS "data_contato_cliente",
               NULLIF(TRIM("AcaoContato"), '') AS "acao_contato",
               NULLIF(TRIM("Lead"), '') AS "lead",
               NULLIF(TRIM("SituacaoCliente"), '') AS "situacao_cliente",
               NULLIF(TRIM("N_Bitrix"), '') AS "n_bitrix",
               "PctComissao" AS "pct_comissao",
               NULLIF(TRIM("Retorno"), '') AS "retorno",
               NULLIF(TRIM("Indice"), '') AS "indice",
               NULLIF(TRIM("CNAE"), '') AS "cnae",
               NULLIF(TRIM("Descricao_CNAE"), '') AS "descricao_cnae",
               NULLIF(TRIM("TipoMontagem"), '') AS "tipo_montagem",
               "ValorMontagem" AS "valor_montagem",
               NULLIF(TRIM("Montador"), '') AS "montador",
               "NumNF" AS "num_nf",
               "DataNF" AS "data_nf",
               CASE WHEN "NumNF" IS NULL THEN NULL
                    WHEN "QuitacaoNF" = 'Sim' THEN TRUE
                    ELSE FALSE END AS "nf_quitada",
               NULLIF(TRIM("Incoterms"), '') AS "incoterms",
               NULLIF(TRIM(REPLACE(REPLACE("ObsMontagem", CHAR(13) || CHAR(10), CHAR(10)),
                                   CHAR(13), CHAR(10))), '') AS "obs_montagem"
          FROM "{schema}"."{VIEW}"
    '''


def main(execution_id: str | None = None) -> bool:
    """Full load of the mirror. ``True`` when the table ended up consistent.

    Args:
        execution_id: Tracking ID; generated (UUID4) when absent.

    Raises:
        Exception: whatever ``_carga`` raises, re-raised after the failure is recorded.
    """
    inicio_dt = datetime.now().astimezone()
    inicio = time.monotonic()
    falhas: list[str] = []
    settings = get_settings()

    if not settings.sap_ready() or not settings.supabase_ready():
        logger.error("%s faltam credenciais de SAP ou Supabase", _LOG)
        return False

    loader = SupabaseLoader(settings.supabase_url, settings.supabase_write_key)
    ok = False
    linhas = 0
    try:
        ok, linhas = _carga(loader, settings, execution_id, falhas)
    except Exception as exc:  # noqa: BLE001 — registra e relança, sem engolir
        falhas.append(f"{type(exc).__name__}: {exc}")
        _registrar(loader, inicio_dt, False, falhas)
        raise
    logger.info(
        "%s %s — %d linha(s) em %.1fs", _LOG, "OK" if ok else "FALHOU",
        linhas, time.monotonic() - inicio,
    )
    _registrar(loader, inicio_dt, ok, falhas)
    return ok


def _carga(
    loader: SupabaseLoader,
    settings: Any,
    execution_id: str | None,
    falhas: list[str],
) -> tuple[bool, int]:
    """The load itself. Returns ``(ok, rows written)``."""
    ex = SAPExtractor(
        settings.sap_host, settings.sap_port, settings.sap_user,
        settings.sap_password, settings.sap_database,
    )
    if not ex.connect():
        logger.error("%s não conectou no HANA", _LOG)
        falhas.append("não conectou no HANA")
        return False, 0
    try:
        df = ex.execute_query(sql_orcamentos(settings.sap_schema))
    finally:
        ex.close()

    if df is None:
        falhas.append(f"consulta à {VIEW} falhou")
        return False, 0
    if len(df) == 0:
        # Zero linha não é snapshot válido: seria a poda apagando a tabela
        # inteira por causa de uma view vazia por acidente.
        logger.warning("%s a view voltou vazia — nada foi gravado nem podado", _LOG)
        falhas.append("view vazia")
        return False, 0

    registros, exec_id = prepare_data(df, execution_id)
    logger.info("%s %d linha(s) extraídas (execução %s)", _LOG, len(registros), exec_id)

    if not loader.insert_data(TABELA, registros):
        falhas.append(f"insert falhou em {TABELA}")
        return False, 0

    # Poda DEPOIS da escrita: no pior caso a tela lê o snapshot anterior, nunca
    # tabela vazia. Falhar aqui é ERRO, não aviso — ficam duas execuções na
    # tabela e todo leitor vê tudo em dobro até a próxima carga boa.
    if not loader.delete_other_executions(TABELA, exec_id):
        logger.error(
            "%s inserção OK mas a PODA falhou: a tabela está com registros "
            "DUPLICADOS (esta execução + a anterior). A próxima carga consolida.", _LOG,
        )
        falhas.append("poda das execuções anteriores falhou")
        return False, len(registros)

    return True, len(registros)


def _registrar(loader: SupabaseLoader, inicio: datetime, ok: bool, falhas: list[str]) -> None:
    """Outcome in ``rotinas_execucao`` -- the same place as the other routines."""
    loader.registrar_rotina(
        ROTINA_NOME, ROTINA_ROTULO,
        inicio=inicio, fim=datetime.now().astimezone(),
        sucesso=ok, erro="; ".join(falhas) if falhas else None,
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    sys.exit(0 if main() else 1)
