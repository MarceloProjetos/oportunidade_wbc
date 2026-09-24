"""ETL: VW_EVOL_ORCAMENTO_ALT (SAP HANA) → Supabase ``orcamentos_espelho``.

A view foi refeita em 09/2026 e passou de 12 para 34 colunas: ganhou o CNAE do
cliente, o bloco de montagem (``TipoMontagem``/``ValorMontagem``/``Montador``) e
o bloco de nota fiscal (``NumNF``/``DataNF``/``QuitacaoNF``). Este espelho leva
isso ao Supabase para o web e o app lerem sem depender do HANA.

**Snapshot, como o de oportunidades**: insere a carga nova e só depois apaga as
execuções anteriores (carrega-depois-poda) — se a carga falhar, a tabela
continua com o estado anterior em vez de ficar vazia.

Três decisões que o SQL carrega, e o porquê (sonda de 17/09/2026 na view
inteira, 5.643 linhas):

- ``nf_quitada`` é **booleano de três estados**: ``NULL`` quando não há nota.
  ``QuitacaoNF`` sozinho mente — 4.310 linhas dizem ``'Não'`` apenas porque nota
  nenhuma foi emitida, e quem lesse isso como "nota em aberto" erraria por um
  fator de três. Gravar a regra aqui resolve uma vez para todos os leitores;
- ``AcaoContato``, ``Lead`` e ``SituacaoCliente`` vêm como STRING VAZIA, nunca
  ``NULL``; entram no espelho como ``NULL`` (``NULLIF``), senão todo relatório
  do Postgres precisaria lembrar de testar ``<> ''``;
- a janela é a view inteira: ela **não tem mais 2024** (começa em 06/01/2025), e
  filtrar por data aqui só esconderia isso de quem lê o espelho.

A ``VW_ORCAMENTO_ALT``, a outra view de cotação, continua com 16 colunas e
nenhum dos campos novos — por isso o espelho é desta view e só dela.
"""

from __future__ import annotations

import logging
import sys
import time
from datetime import datetime
from typing import Any, List, Optional

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
    """A view inteira, com os nomes já em ``snake_case`` do Postgres."""
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
                    ELSE FALSE END AS "nf_quitada"
          FROM "{schema}"."{VIEW}"
    '''


def main(execution_id: Optional[str] = None) -> bool:
    """Carga completa do espelho. ``True`` quando a tabela ficou consistente.

    Args:
        execution_id: ID de rastreio; gerado (UUID4) quando ausente.
    """
    inicio_dt = datetime.now().astimezone()
    inicio = time.monotonic()
    falhas: List[str] = []
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
    execution_id: Optional[str],
    falhas: List[str],
) -> tuple[bool, int]:
    """A carga em si. Devolve ``(ok, linhas gravadas)``."""
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


def _registrar(loader: SupabaseLoader, inicio: datetime, ok: bool, falhas: List[str]) -> None:
    """Desfecho em ``rotinas_execucao`` — o mesmo lugar das outras rotinas."""
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
