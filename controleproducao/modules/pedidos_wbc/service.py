"""Business logic of the Pedidos WBC module — port of the legacy C# addon.

Equivalent of `Form2` (Views/IntegraPedidoWBC.b1f.cs) + `Controllers/ProcessDefault.cs`. See
migration_guide.md section 4.4 for the original step-by-step flow and section 7.4 for the history of
this implementation (C# extraction done 15/09/2026).

Writes to PRODUCTION: since 28/09/2026 the writes are unlocked only on the .11, by machine IP
(`wbcpython/safety.py`). Rules protected by tests:
- `processar_pedidos_novos` has no rollback: it marks `U_INO_ProcessWBC='Y'` before the first OP.
  Resume = `manutencao-op buscar` -> `cancelar-ops` -> `processar-novos` WITHOUT `--force`
  (force duplicates OPs).
- `reprocessar_pedidos_integrados` cancels every planned OP and does not recreate.
- Processing refuses (also with `force`) an order whose quote has a cancelled order with a live
  P/R OP (`ops_orfas`) and an order with `U_U_INO_NotaEspelho='Y'` (`nota_espelho`); reprocessing
  passes.
- `_update_pedido` (recreates the order lines) is CLOSED with an error until Anderson validates it.

Service Layer names: the `ProductionOrders` ones follow the documented SAP standard; the
`Resources` ones were confirmed against a real record on 23/09/2026 (`diag entidade Resources`);
`SalesOpportunitiesLines` is the real collection (docs/wbc/APRENDIZADOS.md).

`Resources/Solda.txt` received 21/09/2026 and installed at `pedidos_wbc/resources/Solda.txt` (5,015
distinct codes). Before that the port ran with an empty list — see migration_guide.md section 7.20
for what that means for the OPs already generated.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from controleproducao.core.audit_log import preenche_log
from controleproducao.core.exceptions import ServiceLayerAuthError, ServiceLayerError
from controleproducao.core.formato import numero_br
from controleproducao.core.hana_reader import HanaDirectReader
from controleproducao.core.service_layer_client import ServiceLayerClient
from controleproducao.core.sql_ligado import ligar
from controleproducao.core.sqlserver_client import WbcSqlServerClient
from controleproducao.modules.pedidos_wbc import listas_fixas
from controleproducao.modules.pedidos_wbc import queries as q
from controleproducao.modules.pedidos_wbc.schemas import (
    EstruturaPrd,
    Linha,
    NewOrcPrd,
    OportunidadeDoc,
    OrcPrdLinha,
    PedidoParaIntegrar,
    TabelaValdixsonLinha,
)

logger = logging.getLogger(__name__)

_UDO_ORC_DETALHE = "OrcDetalhe"


def _chave_do_documento_criado(resultado: dict, entidade: str) -> int:
    """Extract the key of the just-created document from the Service Layer response.

    Fixed 16/09/2026: the code read `resultado["DocEntry"]`, but the `ProductionOrders` entity exposes
    the key as **`AbsoluteEntry`** (`DocEntry` is only the `OWOR` column name). Every OP came back as
    `0`, and that zero was written to `U_INO_OP` on the order lines by `_atualiza_doc(..., "OP")`: the
    OPs were right but the link to the order was zeroed (quote 00125192 logged `OP criada: DocEntry=0`
    seven times).

    Tries `AbsoluteEntry`, `DocEntry` and `DocNum` (entities differ: `Orders` uses `DocEntry`,
    `ProductionOrders` uses `AbsoluteEntry`) and warns loudly, returning 0, when none is found. To
    check an entity's real field names: `python -m controleproducao diag entidade <Entidade> --campos`.
    """
    for chave in ("AbsoluteEntry", "DocEntry", "DocNum"):
        valor = resultado.get(chave)
        if valor not in (None, "", 0):
            return int(valor)

    logger.warning(
        "Não foi possível identificar a chave do %s criado — resposta da Service Layer sem "
        "AbsoluteEntry/DocEntry (campos recebidos: %s). O vínculo com o pedido ficará zerado.",
        entidade, sorted(resultado)[:15],
    )
    return 0

# Nome da coleção filha do UDO na Service Layer. No C# original a tabela filha é acessada
# como `oGeneralData.Child("INO_ORC_LINHA")` (ProcessDefault.cs ~1587/1698); a Service
# Layer expõe tabelas filhas de UDO com o sufixo `Collection` (`<TabelaFilha>Collection`).
# ⚠️ A CONFIRMAR contra o ambiente real — rode `python -m controleproducao diag entidade OrcDetalhe`,
# que lê um registro já existente (criado pelo addon legado) e mostra os nomes reais dos
# campos e da coleção filha. Ver seção 7.9 do migration_guide.md.
_UDO_ORC_DETALHE_CHILD = "INO_ORC_LINHACollection"


# ---------------------------------------------------------------------------
# Constantes de negócio herdadas do addon (nomeadas em 23/09/2026)
# ---------------------------------------------------------------------------
# Estavam como literais espalhados pelo arquivo — `332` em quatro pontos, `"08"` em
# quatro, `"I"` em quatro. Nomear não muda nada na execução; muda quem consegue ler o
# código sem abrir o C# ao lado, e é o que permite achar TODOS os usos de uma regra
# quando ela mudar. Os valores continuam idênticos ao legado.

# Grupos de item (`OITM.ItemsGroupCode`) atribuídos por `CriaItem`/`UpdateItem`.
GRUPO_ITEM_NIVEL_TOP = 333       # item do nível mais alto da estrutura
GRUPO_ITEM_SOLDA = 332           # código presente em Resources/Solda.txt
GRUPO_ITEM_PADRAO = 358          # todos os demais

# Depósitos. O addon decide o depósito da linha pelo PREFIXO do código do item: os que
# começam com "I" (itens importados/intermediários, na nomenclatura da casa) vão para o
# 08; o recurso de rateio vai sempre para o 01.
PREFIXO_ITEM_DEPOSITO_08 = "I"
DEPOSITO_ITEM_I = "08"
DEPOSITO_RECURSO = "01"

# Prazo somado a `DueDate`/`PostingDate` das Ordens de Produção criadas.
PRAZO_OP_DIAS = 20


def _primeiro_valor(rows: list, padrao: Any = None) -> Any:
    """First value of the first row, or `padrao` when there is no row.

    Many queries transcribed from the C# have no alias (`SELECT COUNT(...)`, `SELECT top 1
    "OpprId"`), so the driver returns a column name that cannot be written in code. Naming the
    `list(rows[0].values())[0]` idiom (nine copies) makes the intent visible and keeps the `if rows`
    guard in one place.
    """
    return list(rows[0].values())[0] if rows else padrao


def _deposito_do_item(item_code: str) -> dict:
    """`{"Warehouse": "08"}` for an item code starting with "I", `{}` otherwise.

    Returns a dict, not a string, because the field must be ABSENT from the body when it does not
    apply — sending `Warehouse: None` is not the same for the Service Layer. It replaces a
    `**({...} if ... else {})` repeated in four places.
    """
    if str(item_code or "")[:1].upper() == PREFIXO_ITEM_DEPOSITO_08:
        return {"Warehouse": DEPOSITO_ITEM_I}
    return {}


async def buscar_pedidos_para_integrar(
    hana_reader: HanaDirectReader, integrados: bool = False
) -> list[PedidoParaIntegrar]:
    """Equivalent of the `Form2` "Buscar" button — lists the orders of each of the two original modes.

    - `integrados=False` (default): mode **"Pedidos Novos"** (`BuscaPedidosParaIntegrar`): orders
      linked to an integrated opportunity (`U_INO_IntegrouWBC='Y'`) and not yet processed
      (`U_INO_ProcessWBC='N'`) — the candidates for `processar_pedidos_novos`.
    - `integrados=True`: mode **"Pedidos Integrados"** (`BuscaPedidosIntegrados`): orders with
      `U_INO_ProcessWBC='Y'` — where to find an order to reprocess with `reprocessar-integrados`.

    Added 15/09/2026 (Anderson's request); before it only `processar-novos`/`reprocessar-integrados`
    existed, both needing the `orc_num` in hand. The query is transcribed from the C# in `queries.py`
    with a `{filtro}` placeholder, unused here (`filtro=""`: every eligible order is listed). Each
    `PedidoParaIntegrar` also carries `nota_espelho` and, in "novos" mode only, `ops_orfas`
    (`ops_vivas_de_pedidos_cancelados`), so the screen can show the Processar refusals.

    Sorted by `doc_num` descending (22/09/2026, Anderson): done here and not in the SQL because the SQL
    is verbatim from the C# and serves as the comparison reference with the legacy.
    """
    sql = q.BUSCA_PEDIDOS_INTEGRADOS if integrados else q.BUSCA_PEDIDOS_PARA_INTEGRAR
    rows = hana_reader.fetch_all(*ligar(sql, filtro=""))
    # "Pedidos novos" only: that is where Processar would plan the items a second time.
    orfas = (
        {} if integrados
        else await ops_vivas_de_pedidos_cancelados(
            hana_reader, [str(row["Nº Oportunidade"]) for row in rows]
        )
    )
    pedidos = [
        PedidoParaIntegrar(
            selecionar=row.get("Selecionar", "N"),
            # `opp_id` está correto AQUI: é a chave da Oportunidade no SAP
            # (`OPR1.OpprId`, coluna "Num Oportunidade"), usada pela tela para marcar a
            # linha. O que foi renomeado para `orc_num` em 23/09 é o parâmetro que
            # atravessa o módulo carregando o nº do ORÇAMENTO — outra numeração.
            opp_id=int(row["Num Oportunidade"]),
            doc_num=int(row["Nº Pedido"]),
            cod_cliente=str(row["Cod.Cliente"]),
            nome_cliente=str(row["Nome Cliente"]),
            total_pedido=float(row["Total Pedido"] or 0),
            data_lancamento=str(row["Data Lancamento"]),
            orc_num_masc=str(row["Nº Oportunidade"]),
            nota_espelho=str(row.get("Nota Espelho") or "").strip().upper() == "Y",
            ops_orfas=orfas.get(str(row["Nº Oportunidade"]), []),
        )
        for row in rows
    ]
    # Pedido mais recente primeiro (22/09/2026, a pedido do Anderson). A ordenação é feita
    # aqui e não na query porque o SQL veio transcrito verbatim do C# e é referência de
    # comparação com o legado — mudá-lo por questão de apresentação atrapalharia isso.
    # Mesma convenção já usada na busca do módulo 3 (`DocNum` decrescente).
    pedidos.sort(key=lambda p: p.doc_num, reverse=True)
    return pedidos


async def ops_vivas_de_pedidos_cancelados(
    hana_reader: HanaDirectReader, orc_nums: list[str]
) -> dict[str, list[dict]]:
    """Live OPs (planned or released) on CANCELLED orders of these WBC quotes, by quote.

    The partner swap used to cancel an order and leave its OPs planned; the replacement
    then showed up in "Pedidos novos" and Processar planned the same items again (84505 →
    84507 and 84506 → 84510, 20/10/2026). Also catches an order cancelled by hand in the SAP.
    """
    if not orc_nums:
        return {}
    rows = hana_reader.fetch_all(
        *ligar(q.OPS_VIVAS_DE_PEDIDOS_CANCELADOS,
               orc_nums=list(dict.fromkeys(str(n) for n in orc_nums)))
    )
    por_orcamento: dict[str, list[dict]] = {}
    for row in rows:
        por_orcamento.setdefault(str(row["Orcamento"]), []).append(
            {"pedido": int(row["PedidoCancelado"]), "op": int(row["OP"]),
             "status": str(row["Status"])}
        )
    return por_orcamento


def mensagem_nota_espelho(doc_num: object, orc_num: str) -> str:
    """The Nota Espelho refusal of Processar — one text for the plan (screen and API) and the run."""
    return (
        f"O pedido {doc_num} (WBC {orc_num}) está marcado como Nota Espelho no SAP e não pode "
        "ser processado: ainda não foi confirmado e não vai para a produção."
    )


def mensagem_ops_orfas(doc_num: object, orc_num: str, ops: list[dict]) -> str:
    """The refusal of Processar — one text for the plan (screen and API) and the run."""
    partes = []
    for pedido in sorted({o["pedido"] for o in ops}):
        do_pedido = [o for o in ops if o["pedido"] == pedido]
        planejadas = sum(1 for o in do_pedido if o["status"] == "P")
        liberadas = len(do_pedido) - planejadas
        contagem = " e ".join(
            t for t in (f"{planejadas} planejada(s)" if planejadas else "",
                        f"{liberadas} liberada(s)" if liberadas else "") if t
        )
        numeros = [str(o["op"]) for o in do_pedido]
        lista = ", ".join(numeros[:8]) + (f" … (+{len(numeros) - 8})" if len(numeros) > 8 else "")
        partes.append(f"o pedido cancelado {pedido} ainda tem {contagem} ({lista})")
    comandos = "; ".join(
        f"python -m controleproducao pedidos-wbc cancelar-ops {p} --manter-vinculos"
        for p in sorted({o["pedido"] for o in ops})
    )
    liberadas = (
        " As liberadas: Replanejar na Manutenção de OP e depois cancelar; a que já teve "
        "apontamento, Encerrar."
        if any(o["status"] != "P" for o in ops) else ""
    )
    return (
        f"O pedido {doc_num} (WBC {orc_num}) não pode ser processado: "
        + "; ".join(partes)
        + ". Processar agora planejaria os mesmos itens de novo. Cancele antes as OPs do "
        f"pedido cancelado (na .11: {comandos})." + liberadas
    )


# ---------------------------------------------------------------------------
# Helpers de leitura (HANA — tabelas padrão do SAP B1: ORDR/OOPR/OPR1/OITM/OWOR)
# ---------------------------------------------------------------------------
async def _verifica_congelado(hana_reader: HanaDirectReader, doc_entry: str) -> str:
    """`Querys.VerificaCong` — `U_INO_Congelado` of the order (`ORDR`); "N" when there is no row."""
    rows = hana_reader.fetch_all(*ligar(q.VERIFICA_CONG, doc_entry=doc_entry))
    return str(rows[0]["U_INO_Congelado"]) if rows else "N"


async def _verifica_tab_update(hana_reader: HanaDirectReader, doc_entry: str) -> str:
    """`Querys.VerificaTabUpdate` — `U_INO_UpdateDetalhe` of the order (`ORDR`); "" when there is no row."""
    rows = hana_reader.fetch_all(*ligar(q.VERIFICA_TAB_UPDATE, doc_entry=doc_entry))
    return str(rows[0]["U_INO_UpdateDetalhe"]) if rows else ""


async def _verifica_process_wbc(hana_reader: HanaDirectReader, doc_entry: str) -> str:
    """`U_INO_ProcessWBC` of the order (`ORDR`) — "Y" when it was already processed ("N" without a row).

    Check added 15/09/2026 at Anderson's request — **not in the original C#**. In the legacy addon the
    only guard against reprocessing was the screen: the "Pedidos Novos" grid listed just
    `U_INO_ProcessWBC='N'` (`BuscaPedidosParaIntegrar`), so a processed order never showed up to be
    selected again. The CLI receives `orc_num` directly, bypassing the grid, so the guard has to exist
    in code — see `processar_pedidos_novos` and the `--force` flag.
    """
    rows = hana_reader.fetch_all(*ligar(q.VERIFICA_PROCESS_WBC, doc_entry=doc_entry))
    return str(rows[0]["U_INO_ProcessWBC"]) if rows else "N"


def _op_previa_do_grupo(previas: list[int], ordem_do_grupo: int) -> int | None:
    """The OP that already covers the `ordem_do_grupo`-th group of an item, or `None`.

    `previas` are the OPs the item already had BEFORE this run. With 1 previous OP and three groups of
    the same item, the 1st group is a reprocess (returns that OP) and the 2nd and 3rd are not.
    """
    if 1 <= ordem_do_grupo <= len(previas):
        return previas[ordem_do_grupo - 1]
    return None


async def _ops_existentes_para_item(hana_reader: HanaDirectReader, doc_num_ped: str, item_code: str) -> list[int]:
    """All non-cancelled OPs (`OWOR.Status <> 'C'`) of the order for the item (`OWOR.OriginNum` +
    `OWOR.ItemCode`), ordered by `DocEntry`; `[]` without `doc_num_ped`.

    Check added 15/09/2026 (same motivation as `_verifica_process_wbc`): the original C#
    (`CriaOP`/`CriaOPSA`) never checked this before creating, so running `processar-novos` twice for
    the same order duplicates the OPs.

    23/09/2026, order 84426: answering only "exists or not" was not enough, because **several WBC
    groups can point to the same SAP item** (in 84426 three of the four quote items became `I000003` —
    Porta-Paletes). The 1st group created the OP; the 2nd and 3rd found the OP the 1st had just
    created **in the same run** and skipped as if it were a reprocess. The caller now compares HOW MANY
    OPs already existed with HOW MANY groups of that item have gone by — see `_processa_grupo_producao`.
    """
    if not doc_num_ped:
        return []
    rows = hana_reader.fetch_all(*ligar(q.CHECA_OP_EXISTENTE_PEDIDO, doc_num=doc_num_ped, item_code=item_code))
    return sorted(int(_primeiro_valor([r])) for r in rows)


async def _get_id_orcamentos_pedido(hana_reader: HanaDirectReader, orc_num: str) -> dict | None:
    """`Querys.GetIdOrcamentosPedido` — the newest non-cancelled order linked to the quote
    (`U_INO_COTWBC`), as a row with `DocEntry`, `CardCode`, `DocNum` and `NotaEspelho`
    (`U_U_INO_NotaEspelho`, "N" when empty); `None` when there is none.
    """
    rows = hana_reader.fetch_all(*ligar(q.GET_ID_ORCAMENTOS_PEDIDO, orc_num=orc_num))
    return rows[0] if rows else None


async def _oppr_id_do_orcamento(hana_reader: HanaDirectReader, orc_num: str) -> str:
    """The SAP Opportunity key (`OOPR.OpprId`) from the WBC quote number ("" when there is none).

    Added 22/09/2026 because the port had **collapsed two numberings into one**. The addon's grid
    showed both side by side:

    - `"Nº Oportunidade"` = `OOPR.U_ORCNUM_MASC` — the WBC quote number (e.g. `00125566`), used in
      practically the whole flow;
    - `"Num Oportunidade"` = `OPR1.OpprId` — the Opportunity key (e.g. `15149`), used in **one single
      place**: `AddPedidoOportunidade`, which does `GetByKey` on `SalesOpportunities`
      (`Convert.ToInt32(OpprId)` in the C#, line 848 of the view).

    CLI and web receive only the quote number, so the key is re-read here — a query the legacy did not
    need, since the value was already in the grid row.
    """
    rows = hana_reader.fetch_all(*ligar(q.OPPR_ID_POR_ORCAMENTO, orc_num=orc_num))
    return str(_primeiro_valor(rows, ""))


async def _pega_doc_entry_ped(hana_reader: HanaDirectReader, orc_num: str) -> str:
    rows = hana_reader.fetch_all(*ligar(q.PEGA_DOC_ENTRY_PED, orc_num=orc_num))
    return str(rows[0]["DocEntry"]) if rows else ""


async def _busca_orcamentos_wbc(wbc: WbcSqlServerClient, orc_num: str) -> list[OportunidadeDoc]:
    """Equivalent of `SQLConnection.PegaOrcamentosWBC(GetOrcsWBC)`."""
    rows = wbc.fetch_all(q.GET_ORCS_WBC, (orc_num,))
    return [
        OportunidadeDoc(
            orc_num=row["ORCNUM"],
            versao=row["VERSAO"],
            sit_code=row["SITCOD"],
            cli_nom=row["CLINOM"],
            recp_cod=row["REPCOD"],
            cli_mun=row["CLIMUN"],
            est_cod=row["ESTCOD"],
            orc_date=row["ORCDAT"],
            orc_alt_dth=row["ORCALTDTH"],
            grp_code=row["GRPCOD"],
            sub_grup_code=row["SUBGRPCOD"],
            orc_item=row["ORCITM"],
            orc_prod_code=row["ORCPRDCOD"],
            orc_prod_quantidade=row["ORCPRDQTD"],
            orc_txt=row["ORCTXT"],
            orc_val=row["ORCVAL"],
            orc_ipi=row["ORCIPI"],
            orc_icm=row["ORCICM"],
            id_integracao=row["idIntegracao_OrcItm"],
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# 1. preenche_tabela  <- ProcessDefault.preencheTabela
# ---------------------------------------------------------------------------
async def preenche_tabela(
    sl: ServiceLayerClient, wbc: WbcSqlServerClient, hana_reader: HanaDirectReader, orc_num: str
) -> int:
    """Write the quote "mirror" to the `OrcDetalhe` UDO (+ child `INO_ORC_LINHA`) and return the
    `DocEntry` of the record created.

    Always creates a new record (never updates one). Any failure is logged (including to `INO_LOG`)
    and swallowed: it returns 0, as does a quote with no header in the WBC.

    Two paths, exactly as in the C# (`ProcessDefault.preencheTabela`, ProcessDefault.cs ~1458):

    - **Main path** (`GetTableValdixson` has rows — the quote has a detailed structure in
      `INTEGRACAO_ORCPRDARV`): one UDO line per structure item; header from `NovaTabelaQuot` (only the
      last item read is used, as in the C#, whose `foreach` rewrites the same header fields on each
      iteration).
    - **Fallback path** (no detailed structure): header + lines come from
      `NovaTabelaQuot`/`NovaTabelaQuotLinha`; each `INTEGRACAO_ORCIMP` row becomes a "shallow" UDO
      line. Replicates a C# quirk: the method has a `return` inside the first `foreach`, so it
      processes **only the first** `NovaTabelaQuot` record even if there are more (phase-1 fidelity,
      migration_guide.md section 8).
    """
    try:
        estrutura_rows = wbc.fetch_all(q.GET_TABLE_VALDIXSON, (orc_num, orc_num, orc_num))
        estrutura = [
            TabelaValdixsonLinha(
                orc_num=row["ORCNUM"],
                grp_code=row["GRPCOD"],
                sub_grup_code=row["SUBGRPCOD"],
                orc_item=row[list(row.keys())[3]],  # coluna sem alias (CASE ... ORCITM)
                orc_prod_code=row["PRDCOD"],
                orc_prd_arv_nivel=row["ORCPRDARV_NIVEL"],
                cor_code=row["CORCOD"],
                prd_desc=row["PRDDSC"],
                orc_qtd=row["ORCQTD"],
                orc_tot=row["ORCTOT"],
                orc_pes=row["ORCPES"],
                id_integracao_orc_prd=row["idIntegracao_OrcPrdArv"],
            )
            for row in estrutura_rows
        ]

        if estrutura:
            # Qual caminho e quantas linhas: o de fallback processa SÓ o primeiro
            # registro (particularidade do C# replicada de propósito), então saber por
            # qual dos dois passou é o que explica um resultado menor que o esperado.
            logger.info(
                "    Estrutura detalhada com %d item(ns) — montando o espelho do orçamento.",
                len(estrutura),
            )
            header = await _busca_header_nova_tabela_quot(wbc, orc_num)
            linhas_udo = []
            for item in estrutura:
                linha_ref = await _pega_linha_manual(wbc, item.id_integracao_orc_prd)
                valor = await _get_linha(hana_reader, orc_num, linha_ref) if linha_ref else "0"
                preco_unitario = (item.orc_tot / item.orc_qtd) if item.orc_tot else 0.0
                linhas_udo.append(
                    {
                        "U_INO_PESO": item.orc_pes,
                        "U_INO_CODIGO": item.orc_prod_code.upper(),
                        "U_INO_Qtde": item.orc_qtd,
                        "U_INO_PROD": item.prd_desc,
                        "U_INO_COR": item.cor_code,
                        "U_INO_PRECO": preco_unitario,
                        "U_INO_TOTAL": item.orc_tot,
                        "U_INO_NIVEL": str(item.orc_prd_arv_nivel),
                        **({"U_INO_LINHA": valor} if valor and int(valor) > 0 else {}),
                        "U_INO_ORCITM": str(item.orc_item),
                    }
                )
            body = _monta_body_orc_detalhe(orc_num, header, linhas_udo)
            return _doc_entry_do_detalhe(await sl.create_entity(_UDO_ORC_DETALHE, body))

        # Caminho de fallback — sem estrutura detalhada.
        logger.info("    Sem estrutura detalhada: usando o caminho de fallback.")
        cabecalhos = await _busca_nova_tabela_quot_com_linhas(wbc, orc_num)
        if not cabecalhos:
            logger.warning("    Nenhum cabeçalho de orçamento no WBC — espelho não criado.")
            return 0
        primeiro = cabecalhos[0]  # réplica do `return` dentro do foreach original
        linhas_udo = [
            {
                "U_INO_PESO": linha.peso,
                "U_INO_CODIGO": str(linha.codigo_item),
                "U_INO_Qtde": linha.quantidade,
                "U_INO_PROD": linha.produto,
                "U_INO_COR": linha.cor,
                "U_INO_PRECO": linha.preco,
                "U_INO_TOTAL": linha.orc_val,
                "U_INO_NIVEL": linha.nivel,
                "U_INO_ORCITM": str(linha.orc_itm),
                "U_INO_ORCTXT": linha.orc_txt,
            }
            for linha in primeiro.linhas
        ]
        logger.info(
            "    %d linha(s) no espelho (fallback processa só o primeiro cabeçalho, "
            "de %d — igual ao legado).", len(linhas_udo), len(cabecalhos),
        )
        body = _monta_body_orc_detalhe(orc_num, primeiro, linhas_udo)
        return _doc_entry_do_detalhe(await sl.create_entity(_UDO_ORC_DETALHE, body))

    except Exception as exc:  # noqa: BLE001 - replica o catch externo (retorna 0) do C#
        logger.exception("Erro em preenche_tabela para orc_num=%s", orc_num)
        await preenche_log(sl, orc_num, "Pedido", "Erro", "Integração", str(exc))
        return 0


def _monta_body_orc_detalhe(orc_num: str, header: NewOrcPrd, linhas_udo: list[dict]) -> dict:
    """Build the `OrcDetalhe` UDO body (+ child collection) for the Service Layer.

    ⚠️ **Types matter here** (found 15/09/2026 with `diag entidade OrcDetalhe`, reading a real record
    created by the legacy addon): the C# passed everything as strings
    (`oGeneralData.SetProperty("U_ORCVALVND", item.ORCVALVND.ToString())`) because the DI API converts
    to the UDF type. The Service Layer is typed JSON and **rejects** a string where the field is
    numeric, answering a generic `Internal server error` without naming the field. So the fields
    follow the types observed in the real record, not the C# `.ToString()`:

    - `float`: U_ORCVAL{VND,LST,INV,LUC,EXP,COM,TRP,MON}, U_ORCPERCOM, U_ORCBAS{1,2,3}
    - `int`:   U_CLICOD, U_CLICONCOD, U_PRZENT
    - `str`:   the rest — including **U_ORCVALEMB**, which despite the name is a text UDF in the SAP
      (`"0.0000"` in the real record), so it stays `str`.
    """
    campos_header = {
        "U_INO_COD": orc_num,
        # Formato de data: `%Y-%m-%d` (não `isoformat()` com microssegundos, que a Service
        # Layer pode recusar) — confirmado no registro real (`"2024-12-01"`).
        "U_INO_DATA": datetime.now().strftime("%Y-%m-%d"),
        "U_ORCVALVND": float(header.orc_val_vnd),
        "U_ORCVALLST": float(header.orc_val_lst),
        "U_ORCVALINV": float(header.orc_val_inv),
        "U_ORCVALLUC": float(header.orc_val_luc),
        "U_ORCVALEXP": float(header.orc_val_exp),
        "U_ORCVALCOM": float(header.orc_val_com),
        "U_ORCPERCOM": float(header.orc_per_com),
        "U_REPCOD": header.rep_cod,
        "U_CLICOD": int(header.cli_cod),
        "U_CLINOM": header.cli_nom,
        "U_CLICONCOD": int(header.cli_con_cod),
        "U_CLICON": header.cli_con,
        "U_ORCVALTRP": float(header.orc_val_trp),
        # `U_ORCVALEMB` é UDF de TEXTO no SAP (registro real traz "0.0000") — mantém str.
        "U_ORCVALEMB": str(header.orc_val_emb),
        "U_ORCVALMON": float(header.orc_val_mon),
        "U_PGTCOD": header.pgt_cod,
        "U_TIPMONCOD": header.tip_mon_cod,
        "U_PRZENT": int(header.przent),
        "U_ORCBAS1": float(header.orc_bas1),
        "U_ORCBAS2": float(header.orc_bas2),
        "U_ORCBAS3": float(header.orc_bas3),
        "U_ORCPGT": header.orc_pgt,
        "U_ORCIMP_REVISAO": header.orc_imp_revisao,
        "U_ORCIMP_EMAIL": header.orc_imp_email,
        "U_ORCIMP_FONE": header.orc_imp_fone,
        "U_ORCIMP_CIDADE": header.orc_imp_cidade,
        "U_ORCIMP_UF": header.orc_imp_uf,
        "U_ORCIMP_TIPO_VENDA": header.orc_imp_tipo_venda,
        "U_ORCIMP_TRANSPORTE": header.orc_imp_transporte,
        "U_ORCIMP_MONTAGEM": header.orc_imp_montagem,
        "U_TABELA_PRECO": header.tabela_preco,
        "U_ORCIMP_RETORNO": header.orc_imp_retorno,
        "U_ORCIMP_INDICE_VENDAS": header.orc_imp_indice_vendas,
        "U_ORCIMP_NEGOCIACAO": header.orc_imp_negociacao,
    }
    # Réplica do corte em 254 chars + campo de continuação do C# original.
    acabamento = header.orc_imp_acabamento or ""
    if len(acabamento) < 255:
        campos_header["U_ORCIMP_ACABAMENTO"] = acabamento
    else:
        campos_header["U_ORCIMP_ACABAMENTO"] = acabamento[:254]
        campos_header["U_ORCIMP_ACABAMENTO2"] = acabamento[254:]

    return {**campos_header, _UDO_ORC_DETALHE_CHILD: linhas_udo}


_CAMPOS_DO_RESUMO = {
    "orc_val_vnd": "valor de venda", "orc_imp_acabamento": "acabamento/cor", "orc_imp_montagem": "montagem",
    "orc_imp_transporte": "transporte", "orc_pgt": "pagamento",
}


def _revisao_nova_do_orcamento(wbc: WbcSqlServerClient, orc_num: str, header: NewOrcPrd) -> str | None:
    """The quote's revision when the proposal row is on another one; `None` when they agree."""
    rows = wbc.fetch_all(q.REVISAO_DO_ORCAMENTO, (orc_num,))
    if not rows:
        return None
    revisao = str(rows[0]["REVISAO"] or "").strip()
    # `ISNULL(ORCIMP_REVISAO,0)`: no revision on the row comes as "0".
    proposta = header.orc_imp_revisao.strip().removeprefix("0")
    return None if revisao == proposta else revisao


def revisoes_dos_orcamentos(wbc: WbcSqlServerClient, orc_nums: list[str]) -> dict[str, str]:
    """The current revision of each quote, by quote number (the "Versão" column of the list)."""
    if not orc_nums:
        return {}
    rows = wbc.fetch_all(*ligar(q.REVISOES_DOS_ORCAMENTOS, orc_nums=list(dict.fromkeys(orc_nums))))
    return {str(r["ORCNUM"]).strip(): str(r["REVISAO"] or "").strip() for r in rows}


def _texto_wbc(valor: object) -> str:
    """The line breaks of a WBC variable, as the print writes them in `INTEGRACAO_ORCIMP`."""
    return str(valor or "").replace("\r\n", "[CR]").replace("\n", "[CR]").replace("\r", "[CR]")


def _numero_de_calculo(calculo: dict[str, object], nome: str, sem_variavel: str) -> str:
    """A number from `OrcVar`, which stores both "1.5" and "1,5". A quote may not have the variable
    at all (23 of 300): no revision holds it then, so `sem_variavel` is the only value there is."""
    if nome not in calculo:
        return sem_variavel
    return f"{float(str(calculo[nome] or 0).replace(',', '.')):.4f}"


def _cabecalho_da_revisao(wbc: WbcSqlServerClient, orc_num: str, revisao: str, proposta: NewOrcPrd) -> NewOrcPrd:
    """The order-detail header on the quote's own revision, without depending on a print.

    `proposta` only names what changed in the log. Raises when the revision cannot be read: the
    pedido fails before any write, because going on would send an old revision to the SAP.
    """
    chave = f"{orc_num}{revisao}"
    cadastro = wbc.fetch_all(q.CADASTRO_DA_REVISAO, (chave,))
    valores = wbc.fetch_all(q.VALORES_DO_ORCAMENTO, (orc_num,))
    if not cadastro or not valores:
        raise ValueError(f"Orçamento {chave} não encontrado no WBC: a última revisão não pôde ser lida.")
    cad, val = cadastro[0], valores[0]
    textos = {str(r["varusrcodigo"]).strip(): _texto_wbc(r["varusrvalor"])
              for r in wbc.fetch_all(q.TEXTOS_DA_REVISAO, (chave,))}
    # `OrcVar` has both "[RETORNO]" and "[retorno]".
    calculo = {str(r["Variavel"]).strip().upper(): r["Valor"] for r in wbc.fetch_all(q.CALCULO_DA_REVISAO, (chave,))}
    total, lista = float(cad["orccab_total"] or 0), float(cad["OrcCabPrecoLista"] or 0)
    t = textos.get
    novo = proposta.model_copy(update={
        "orc_val_vnd": total, "orc_val_lst": lista,
        "orc_val_inv": float(val["ORCVALINV"] or 0), "orc_val_luc": float(val["ORCVALLUC"] or 0),
        "orc_val_exp": float(val["ORCVALEXP"] or 0), "orc_val_com": float(val["ORCVALCOM"] or 0),
        "orc_per_com": float(val["ORCPERCOM"] or 0), "rep_cod": str(val["REPCOD"] or ""),
        "cli_cod": int(val["CLICOD"] or 0), "cli_nom": str(val["CLINOM"] or ""),
        "cli_con_cod": int(val["CLICONCOD"] or 0), "cli_con": str(val["CLICON"] or ""),
        "orc_val_trp": float(val["ORCVALTRP"] or 0), "orc_val_emb": float(val["ORCVALEMB"] or 0),
        "orc_val_mon": float(val["ORCVALMON"] or 0), "pgt_cod": str(val["PGTCOD"] or ""),
        "tip_mon_cod": str(val["TIPMONCOD"] or ""), "przent": int(val["PRZENT"] or 0),
        "orc_bas1": float(val["ORCBAS1"] or 0), "orc_bas2": float(val["ORCBAS2"] or 0),
        "orc_bas3": float(val["ORCBAS3"] or 0), "orc_pgt": str(val["ORCPGT"] or ""),
        "orc_imp_revisao": revisao, "orc_imp_email": str(cad["ORCCAB_CONTATO_EMAIL"] or ""),
        "orc_imp_fone": str(cad["orccab_fone"] or ""), "orc_imp_cidade": str(cad["orccab_municipio"] or ""),
        "orc_imp_uf": str(cad["orccab_UF"] or ""), "orc_imp_tipo_venda": str(cad["TIPOVENDA"] or ""),
        "tabela_preco": str(cad["lista_precos"] or ""),
        # The print layout, measured on 300 current proposals.
        "orc_imp_acabamento": "[CR]".join([t("ADIC_001", ""), t("ADIC_002", ""), t("ADIC_003", ""),
                                           t("ADIC_004", ""), t("ACABAMENTO", ""), ""]),
        "orc_imp_transporte": "[CR]".join([t("TXT_002", ""), t("TRANSPORTE", ""), ""]),
        "orc_imp_montagem": "[CR]".join([t("TXT_001", ""), t("MONTAGEM", ""), ""]).replace("      ", " "),
        "orc_imp_retorno": _numero_de_calculo(calculo, "[RETORNO]", proposta.orc_imp_retorno),
        "orc_imp_negociacao": _numero_de_calculo(calculo, "[NEGOCIACAO]", proposta.orc_imp_negociacao),
        "orc_imp_indice_vendas": f"{total / lista:.4f}" if lista else "1.0000",
    })
    mudou = [rotulo for campo, rotulo in _CAMPOS_DO_RESUMO.items()
             if getattr(novo, campo) != getattr(proposta, campo)]
    logger.info(
        "    Proposta do WBC na revisão %s, orçamento na %s: dados lidos direto da %s. %s",
        proposta.orc_imp_revisao or "—", revisao or "—", revisao or "—",
        f"Mudou: {', '.join(mudou)}." if mudou else "Valor e textos iguais.",
    )
    return novo


async def _busca_header_nova_tabela_quot(wbc: WbcSqlServerClient, orc_num: str) -> NewOrcPrd:
    """The order-detail header, always on the quote's current revision."""
    header = _le_header_da_proposta(wbc, orc_num)
    revisao = _revisao_nova_do_orcamento(wbc, orc_num, header)
    return header if revisao is None else _cabecalho_da_revisao(wbc, orc_num, revisao, header)


def _le_header_da_proposta(wbc: WbcSqlServerClient, orc_num: str) -> NewOrcPrd:
    rows = wbc.fetch_all_values(q.NOVA_TABELA_QUOT, (orc_num,))
    # `NOVA_TABELA_QUOT` devolve colunas sem alias (ISNULL(campo,0)) — mapear por posição,
    # na mesma ordem de `SQLConnection.ExecuteSelectNew` (ver service.py, cross-check com
    # o C# original em ProcessDefault.cs/SQLConnection.cs). Usa `fetch_all_values` (não
    # `fetch_all`/dict) porque as 35 colunas sem alias colidem por nome no driver ODBC e
    # perderiam valores num dict — ver docstring de `fetch_all_values` em sqlserver_client.py
    # (bug real encontrado em 15/09/2026 testando contra homologação, sem equivalente no C#,
    # que lia por índice direto no `SqlDataReader`).
    valores = rows[0] if rows else [0] * 35
    return NewOrcPrd(
        orc_val_vnd=valores[0] or 0, orc_val_lst=valores[1] or 0, orc_val_inv=valores[2] or 0,
        orc_val_luc=valores[3] or 0, orc_val_exp=valores[4] or 0, orc_val_com=valores[5] or 0,
        orc_per_com=valores[6] or 0, rep_cod=str(valores[7] or ""), cli_cod=valores[8] or 0,
        cli_nom=str(valores[9] or ""), cli_con_cod=valores[10] or 0, cli_con=str(valores[11] or ""),
        orc_val_trp=valores[12] or 0, orc_val_emb=valores[13] or 0, orc_val_mon=valores[14] or 0,
        pgt_cod=str(valores[15] or ""), tip_mon_cod=str(valores[16] or ""), przent=valores[17] or 0,
        orc_bas1=valores[18] or 0, orc_bas2=valores[19] or 0, orc_bas3=valores[20] or 0,
        orc_pgt=str(valores[21] or ""), orc_imp_revisao=str(valores[22] or ""),
        orc_imp_email=str(valores[23] or ""), orc_imp_fone=str(valores[24] or ""),
        orc_imp_cidade=str(valores[25] or ""), orc_imp_uf=str(valores[26] or ""),
        orc_imp_tipo_venda=str(valores[27] or ""), orc_imp_transporte=str(valores[28] or ""),
        orc_imp_acabamento=str(valores[29] or ""), orc_imp_montagem=str(valores[30] or ""),
        tabela_preco=str(valores[31] or ""), orc_imp_retorno=str(valores[32] or ""),
        orc_imp_indice_vendas=str(valores[33] or ""), orc_imp_negociacao=str(valores[34] or ""),
    )


async def _busca_nova_tabela_quot_com_linhas(wbc: WbcSqlServerClient, orc_num: str) -> list[NewOrcPrd]:
    """Equivalent of `SQLConnection.ExecuteSelectNew`, returning a one-element list: the
    `NovaTabelaQuot` header with all `NovaTabelaQuotLinha` rows in `linhas`.

    The C# returned one `NewOrcPrd` per `INTEGRACAO_ORCIMP` row, re-running the line query inside the
    loop; the port builds a single header, which is all `preenche_tabela` uses (it processes only the
    first, as in the C#).
    """
    header = _le_header_da_proposta(wbc, orc_num)
    revisao = _revisao_nova_do_orcamento(wbc, orc_num, header)
    sql_linhas = q.NOVA_TABELA_QUOT_LINHA
    if revisao is not None:
        header = _cabecalho_da_revisao(wbc, orc_num, revisao, header)
        sql_linhas = q.NOVA_TABELA_QUOT_LINHA_ATUAL
    # `fetch_all_values` (não `fetch_all`/dict) pelo mesmo motivo de
    # `_le_header_da_proposta`: `NOVA_TABELA_QUOT_LINHA` tem várias colunas `''`
    # literais e `ISNULL(...)` sem alias que colidem por nome no driver ODBC.
    linha_rows = wbc.fetch_all_values(sql_linhas, (orc_num,))
    linhas = [
        OrcPrdLinha(
            peso=str(row[0] or ""),
            codigo_item=row[1] or 0,
            quantidade=str(row[2] or ""),
            produto=str(row[3] or ""),
            cor=str(row[4] or ""),
            preco=str(row[5] or ""),
            orc_val=row[6] or 0,
            nivel=str(row[7] or ""),
            linha="0",
            orc_itm=row[9] or 0,
            orc_txt=str(row[10] or ""),
        )
        for row in linha_rows
    ]
    header.linhas = linhas
    return [header]


async def _pega_linha_manual(wbc: WbcSqlServerClient, id_integracao_orc_prd: int) -> str:
    """`Querys.PegaLinha` — the `ORCITM` tied to a tree line (`idIntegracao_OrcPrdArv`); "" when none."""
    rows = wbc.fetch_all(q.PEGA_LINHA, (id_integracao_orc_prd,))
    if not rows:
        return ""
    return str(_primeiro_valor(rows) or "")


async def _get_linha(hana_reader: HanaDirectReader, orc_num: str, orc_itm: str) -> str:
    """`Querys.GetLinha` — `LineNum` of the sales-order line already created in the SAP for this
    `orc_itm` (newest non-cancelled order of the quote; "0" when none). HANA (`RDR1`/`ORDR`), not WBC.
    """
    rows = hana_reader.fetch_all(*ligar(q.GET_LINHA, orc_num=orc_num, orc_itm=orc_itm))
    if not rows:
        return "0"
    return str(_primeiro_valor(rows) or "0")


def _doc_entry_do_detalhe(resposta: dict | None) -> int:
    """DocEntry of the OrcDetalhe just created, from the Service Layer's own answer (0 = unknown).

    It becomes the order's `U_INO_ORCAMENTO` ("Detalhe do Orçamento"). Until 05/10/2026 this
    was a stub returning 0, so every Processar/Reprocessar wrote a new snapshot and left the
    order on the old one (84454: 546783 while 546875 existed). Never the legacy
    `max("DocEntry") FROM "@INO_ORCAM"`: two snapshots written at once make it return another
    quote's — 5 open orders pointed at quote 00124945's on 05/10/2026.
    """
    try:
        doc_entry = int((resposta or {}).get("DocEntry") or 0)
    except (TypeError, ValueError):
        doc_entry = 0
    if doc_entry:
        logger.info("    Detalhe do Orçamento %s criado.", doc_entry)
    return doc_entry


async def _detalhe_do_pedido(hana_reader: HanaDirectReader, doc_entry: str) -> int | None:
    """The order's `U_INO_ORCAMENTO` as the SAP has it now (None = empty)."""
    rows = hana_reader.fetch_all(*ligar(q.DETALHE_DO_PEDIDO, doc_entry=doc_entry))
    valor = rows[0].get("U_INO_ORCAMENTO") if rows else None
    if valor is None:
        return None
    try:
        return int(valor) or None
    except (TypeError, ValueError):
        return None


async def _confere_detalhe(
    hana_reader: HanaDirectReader, doc_entry: str, antes: int | None, novo: int
) -> None:
    """Says in the log where the order's "Detalhe do Orçamento" points, read back from the SAP.

    Read back, not assumed: the Service Layer has answered 204 to a field it did not keep
    (`DocumentLines.Text`). Never raises — `U_INO_ProcessWBC` is already saved in the same PATCH,
    and aborting here would leave the order "processed" without OPs.
    """
    def onde(valor: int | None) -> str:
        return f"no {valor}" if valor else "sem Detalhe"

    try:
        if not novo:
            logger.error(
                "  Sem Detalhe do Orçamento novo (não criado, ou a resposta veio sem DocEntry): o "
                "pedido continua %s (U_INO_ORCAMENTO NÃO atualizado).", onde(antes),
            )
            return
        depois = await _detalhe_do_pedido(hana_reader, doc_entry)
        if depois == novo:
            logger.info(
                "  Detalhe do Orçamento do pedido: %s → %s (conferido no SAP).", antes or "nenhum", novo
            )
        else:
            logger.error(
                "  Detalhe do Orçamento NÃO atualizado: o pedido devia apontar para o %s e está %s "
                "(U_INO_ORCAMENTO).", novo, onde(depois),
            )
    except Exception as exc:  # noqa: BLE001 - a failed check must not stop the processing
        logger.error("  Não foi possível conferir o Detalhe do Orçamento do pedido: %s", exc)


# ---------------------------------------------------------------------------
# 2. Decisão UpdateTabPedido / UpdateTabPedidoCong / UpdatePedido (pergunta 1 da
#    seção 9, resolvida em 15/09/2026 lendo o C# original — ver seção 7.4)
# ---------------------------------------------------------------------------
async def atualiza_pedido_tabela(
    sl: ServiceLayerClient,
    wbc: WbcSqlServerClient,
    hana_reader: HanaDirectReader,
    oportunidades: list[OportunidadeDoc],
    orc_num: str,
    card_code: str,
    doc_num: str,
    doc_entry: str,
    tb_valdixson: int,
    process: str,
) -> int:
    """Decide between `UpdateTabPedido` / `UpdateTabPedidoCong` / `UpdatePedido`, replicating the
    decision tree of `Button0_ClickAfter` (IntegraPedidoWBC.b1f.cs, ~258-283 and ~2000-2023): first
    `U_INO_Congelado`; if "N", `U_INO_UpdateDetalhe` (`resultado`) picks between the other two:

    - frozen (`!= "N"`): `_update_tab_pedido_cong` (control fields; with `process != "Y"` it also
      zeroes `U_INO_OP` on the lines);
    - not frozen, `U_INO_UpdateDetalhe != "Y"`: `_update_tab_pedido` (control fields only);
    - not frozen, `U_INO_UpdateDetalhe == "Y"`: `_update_pedido`, which is CLOSED and raises
      `ValueError` until Anderson validates it (F7, 29/09/2026).

    `process` is the `U_INO_ProcessWBC` value to write ("Y" when processing, "N" when reprocessing);
    `tb_valdixson` is the DocEntry of the new `OrcDetalhe`. Afterwards `_confere_detalhe` reads
    `U_INO_ORCAMENTO` back from the SAP and logs where it points (never raises). Returns the result of
    the chosen path (0).

    ⚠️ Parameter names fixed 23/09/2026. They were `oppr_id` and `oppr_id_orig` — two near-identical
    names for different things: the first received the WBC QUOTE number and the second the ORDER
    number. Neither is the Opportunity key that `oppr_id` suggested, and mixing those numberings
    already cost a bug on 22/09 (section 7.35 of migration_guide.md).
    """
    # Qual dos três caminhos foi tomado é a informação mais útil desta etapa: eles
    # gravam coisas bem diferentes (um só mexe em campos de controle, outro RECRIA as
    # linhas do pedido), e a árvore de decisão depende de dois campos do próprio pedido.
    # Sem isto, o acompanhamento não distingue um update inofensivo de uma recriação.
    antes = await _detalhe_do_pedido(hana_reader, doc_entry)
    verif_congelado = await _verifica_congelado(hana_reader, doc_entry)
    if verif_congelado == "N":
        resultado = await _verifica_tab_update(hana_reader, doc_entry)
        if resultado != "Y":
            logger.info(
                "  Pedido não congelado e sem update de detalhe: atualizando só os campos "
                "de controle (U_INO_ProcessWBC=%s).", process,
            )
            gravou = await _update_tab_pedido(sl, oportunidades, orc_num, doc_entry, tb_valdixson, process)
        else:
            logger.info(
                "  Pedido não congelado e COM update de detalhe: recriando as linhas do "
                "pedido a partir do orçamento WBC.",
            )
            gravou = await _update_pedido(
                sl, wbc, hana_reader, oportunidades, orc_num, card_code, doc_num, doc_entry, tb_valdixson,
                process,
            )
    else:
        logger.info(
            "  Pedido congelado: atualizando os campos de controle%s.",
            " e zerando U_INO_OP nas linhas" if process != "Y" else "",
        )
        versao = await _revisao_que_o_worker_nao_carimba(hana_reader, oportunidades, doc_entry, process)
        gravou = await _update_tab_pedido_cong(sl, oportunidades, orc_num, doc_entry, tb_valdixson, process, versao)
    await _confere_detalhe(hana_reader, doc_entry, antes, tb_valdixson)
    return gravou


async def _update_tab_pedido(
    sl: ServiceLayerClient,
    oportunidades: list[OportunidadeDoc],
    orc_num: str,
    doc_entry: str,
    tb_valdixson: int,
    process: str,
) -> int:
    """`ProcessDefault.UpdateTabPedido` — updates only the order's control fields
    (`U_INO_ProcessWBC`, `U_INO_Congelado='Y'`, `U_INO_ORCAMENTO` when non-zero, `U_INO_VERSAOWBC`),
    without touching the lines. Logs the error to `INO_LOG` and re-raises.
    """
    campos: dict = {"U_INO_ProcessWBC": process, "U_INO_Congelado": "Y"}
    if tb_valdixson != 0:
        campos["U_INO_ORCAMENTO"] = tb_valdixson
    if oportunidades:
        campos["U_INO_VERSAOWBC"] = oportunidades[0].versao
    try:
        await sl.update_entity("Orders", doc_entry, campos)
        return 0
    except Exception as exc:  # noqa: BLE001
        await preenche_log(sl, orc_num, "Pedido", "Erro", "Integração", str(exc))
        raise


async def _revisao_que_o_worker_nao_carimba(
    hana_reader: HanaDirectReader, oportunidades: list[OportunidadeDoc], doc_entry: str, process: str
) -> str | None:
    """The revision for Processar to stamp on a frozen order the worker leaves alone, else `None`.

    On any other frozen order the stamp stays with the worker: it is what tells the worker a new
    revision still has to reach the order's header.
    """
    if process != "Y" or not oportunidades:
        return None
    rows = hana_reader.fetch_all(*ligar(q.PEDIDO_CORRIGIDO_A_MAO, doc_entry=doc_entry))
    return oportunidades[0].versao if rows and rows[0]["N"] else None


async def _update_tab_pedido_cong(
    sl: ServiceLayerClient,
    oportunidades: list[OportunidadeDoc],
    orc_num: str,
    doc_entry: str,
    tb_valdixson: int,
    process: str,
    versao: str | None = None,
) -> int:
    """`ProcessDefault.UpdateTabPedidoCong` — control-field update for a frozen order
    (`U_INO_ProcessWBC`, `U_INO_ORCAMENTO` when non-zero); when `process != "Y"` it also zeroes
    `U_INO_OP` on every existing line (the C# loop `DocCot.Lines.SetCurrentLine(i); ...U_INO_OP = 0`).
    Logs the error to `INO_LOG` and re-raises.
    """
    campos: dict = {"U_INO_ProcessWBC": process}
    if tb_valdixson != 0:
        campos["U_INO_ORCAMENTO"] = tb_valdixson
    if versao is not None:
        campos["U_INO_VERSAOWBC"] = versao
    if process != "Y":
        # Atualização PARCIAL: só `LineNum` + o campo que muda.
        #
        # ⚠️ Terceira vez que este erro aparece no projeto, e a segunda vez com a MESMA
        # mensagem do SAP: reler o pedido e devolver `DocumentLines` inteiro reenvia todos
        # os campos de todas as linhas, inclusive UDFs de outros add-ons que voltam vazias
        # no GET e que o SAP recusa na escrita —
        # `'' is not a valid value for property 'U_B1SYS_RevenueInd2'`.
        #
        # `marca_op_nas_linhas` foi corrigida assim em 15/09/2026; esta função ficou para
        # trás porque o caminho que passa por ela (`reprocessar-integrados`) só foi
        # executado em 22/09. O `diag patch-parcial` provou que as linhas não enviadas
        # sobrevivem e que um objeto de linha parcial é aceito — é o que sustenta isto.
        pedido_atual = await sl.get_by_key("Orders", doc_entry)
        linhas_do_corpo = [
            {"LineNum": linha["LineNum"], "U_INO_OP": 0}
            for linha in pedido_atual.get("DocumentLines", [])
            if linha.get("LineNum") is not None
        ]
        if linhas_do_corpo:
            campos["DocumentLines"] = linhas_do_corpo
    try:
        await sl.update_entity("Orders", doc_entry, campos)
        return 0
    except Exception as exc:  # noqa: BLE001
        await preenche_log(sl, orc_num, "Pedido", "Erro", "Integração", str(exc))
        raise


def _loga_pesos(
    hana_reader: HanaDirectReader, orc_num: str, doc_entry: str | int, estrutura: list[EstruturaPrd]
) -> list[dict]:
    """One log line per order line: the SAP ``Weight1`` next to what it should be.

    Expected = WBC tree level 1 + 10% — the worker's rule (`wbcpython.domain.linhas.
    peso_da_linha`, imported so the two cannot drift). Read-only, and never stops the
    processing: this step does not write the weight (the worker does, when it creates the
    order). It exists because the weight went wrong on quote 00125817 (29/09/2026) with
    nothing about it in this log. More than 1% away from the expected value (hand-typed
    weights are round numbers: 248 for 249.07) comes out as a WARNING — followed, since
    01/10/2026, by WHO changed the line in the SAP (`_mudanca_do_peso`).

    Returns the lines that are off, structured (``pesos_diferentes`` of the execution result,
    the JSON API's view of the same warning — API_PEDIDOS_WBC.md).
    """
    from decimal import Decimal

    from wbcpython.domain.linhas import peso_da_linha

    diferentes: list[dict] = []
    try:
        wbc: dict[int, float] = {}
        for no in estrutura:            # `EstruturaPrd`, from `busca_estrutura_produto`
            if int(no.nivel or 0) == 1:
                wbc[int(no.orc_itm)] = wbc.get(int(no.orc_itm), 0.0) + float(no.peso or 0)
        linhas = hana_reader.fetch_all(*ligar(q.PESOS_DAS_LINHAS_DO_PEDIDO, doc_entry=doc_entry))
    except Exception as exc:  # noqa: BLE001 - a log line must not break the processing
        logger.warning("Pedido %s: não foi possível comparar os pesos: %s", orc_num, exc)
        return diferentes
    for linha in linhas:
        orc_itm = str(linha.get("U_INO_ORCITM") or "").strip()
        sap = float(linha.get("Weight1") or 0)
        quantidade = float(linha.get("Quantity") or 0)
        arvore = wbc.get(int(orc_itm)) if orc_itm.isdigit() else None
        esperado = peso_da_linha(Decimal(str(arvore))) if arvore is not None else None
        texto = (
            f"Pedido {orc_num}: peso da linha {linha.get('LineNum')} (item {linha.get('ItemCode')}, "
            f"OrcItm {orc_itm or '—'}, qtd {quantidade:g}): SAP {numero_br(sap)} kg"
            + (f" · esperado {numero_br(esperado)} kg (árvore do WBC {numero_br(arvore)} kg + 10%)"
               if esperado is not None else " · árvore do WBC sem peso")
        )
        if esperado is not None and abs(sap - float(esperado)) > float(esperado) * 0.01:
            logger.warning("%s — DIFERENTE.", texto)
            versoes = _versoes_da_linha(hana_reader, doc_entry, linha.get("LineNum"))
            mudanca = _mudanca_do_peso(versoes, linha.get("LineNum"), float(esperado))
            if mudanca:
                logger.warning("Pedido %s: %s", orc_num, mudanca["texto"])
                try:
                    veredito = _veredito_do_worker(versoes, linha)
                except Exception as exc:  # noqa: BLE001 - runs after ProcessWBC='Y': must never abort
                    logger.info("  (não foi possível dizer o que a integração faz com o peso: %s)", exc)
                    veredito = None
                if veredito:
                    logger.log(veredito[0], "Pedido %s: %s", orc_num, veredito[1])
            diferentes.append({
                "orc_num": orc_num,
                "linha": linha.get("LineNum"),
                "item": linha.get("ItemCode"),
                "quantidade": quantidade,
                "peso_sap": round(sap, 2),
                "peso_esperado": float(esperado),
                "arvore_wbc": round(float(arvore), 2),
                "causa": mudanca,
            })
        else:
            logger.info("%s.", texto)
    return diferentes


def _versoes_da_linha(
    hana_reader: HanaDirectReader, doc_entry: str | int, line_num: int | None
) -> list[dict]:
    """The order line's change log (ADOC/ADO1), oldest first — empty when it cannot be read."""
    if line_num is None:
        return []
    try:
        return hana_reader.fetch_all(
            *ligar(q.HISTORICO_DA_LINHA_DO_PEDIDO, doc_entry=int(doc_entry), line_num=int(line_num))
        )
    except Exception as exc:  # noqa: BLE001 - the cause is a bonus, never a failure
        logger.info("  (histórico de alterações do pedido indisponível: %s)", exc)
        return []


def _veredito_do_worker(versoes: list[dict], linha: dict) -> tuple[int, str] | None:
    """What the worker does with this line — the same rule (`wbcpython.domain.peso_reescalado`),
    so the Processar log and the worker never give the same case two answers (log level, text).

    No 3-minute wait here: this is a forecast, and the Processar itself has just saved the order
    (``U_INO_ProcessWBC``), which would always look like a fresh save."""
    from controleproducao.config import get_settings
    from wbcpython.application.pesos_reescalados import DIAS_OLHADOS, GRAVA_A_PARTIR_DE
    from wbcpython.domain.peso_reescalado import Acao, LinhaAtual, decidir, kg, versao_da_linha

    usuarios = {get_settings().sl_username.strip().lower()} - {""}
    if not usuarios or not versoes:
        return None
    atual = LinhaAtual(
        float(linha.get("Quantity") or 0), float(linha.get("Weight1") or 0), float(linha.get("LineTotal") or 0),
        aberta=str(linha.get("LineStatus") or "O") != "C",
    )
    convertidas = [versao_da_linha(v, usuarios) for v in versoes]
    primeira = int(versoes[0].get("PrimeiraVersao") or 1)
    agora = datetime.now()
    cortado = primeira > 1 and convertidas[0].instancia == primeira
    # Decided without the start date first: a change before it is still a wrong weight the
    # log must call out ("fix by hand"), not a silent NADA.
    decisao = decidir(convertidas, atual, agora, espera=timedelta(0), historico_cortado=cortado)
    if decisao.acao is Acao.AVISAR:
        return logging.WARNING, f"a integração NÃO volta este peso sozinha: {decisao.motivo}."
    if decisao.acao is not Acao.CORRIGIR:
        return None
    if GRAVA_A_PARTIR_DE is not None:
        depois_do_inicio = decidir(
            convertidas, atual, agora, espera=timedelta(0), a_partir_de=GRAVA_A_PARTIR_DE,
            historico_cortado=cortado,
        )
        if depois_do_inicio.acao is not Acao.CORRIGIR:
            return logging.WARNING, (
                f"a integração NÃO volta este peso sozinha: a troca é anterior a "
                f"{GRAVA_A_PARTIR_DE:%d/%m/%Y %H:%M}, quando a correção automática começa "
                f"(o passado não se altera); corrija à mão."
            )
        # The worker only reads orders a person saved in the last DIAS_OLHADOS days.
        ultima = max((v.momento for v in convertidas if not v.pela_integracao and v.momento), default=None)
        if ultima is None or ultima.date() < agora.date() - timedelta(days=DIAS_OLHADOS):
            return logging.WARNING, (
                f"a integração NÃO volta este peso sozinha: a troca tem mais de {DIAS_OLHADOS} dias "
                f"(o worker só olha os últimos {DIAS_OLHADOS}); corrija à mão."
            )
        return logging.INFO, f"a integração volta este peso para {kg(decisao.peso)} kg sozinha."
    return logging.WARNING, (
        f"a integração voltaria este peso para {kg(decisao.peso)} kg, mas a correção automática "
        f"ainda está em simulação: corrija à mão."
    )


def _mudanca_do_peso(versoes: list[dict], line_num: int | None, esperado: float) -> dict | None:
    """Who changed the order line in the SAP, from its change log (ADOC/ADO1) — or ``None``.

    Orders 84444 (29/09/2026) and 84453 (01/10/2026) were created by the worker with the right
    weight; then a person changed the line quantity from 2 to 1 in the SAP, and the SAP rescales
    ``Weight1`` with the quantity (176,90 → 88,45 kg). The log said "DIFERENTE" and nothing
    else, so it read as an integration bug. Now it names the change: who, when, from what to
    what — as a sentence for the log (``texto``) and as fields for the API.
    """
    for antes, depois in reversed(list(zip(versoes, versoes[1:], strict=False))):
        q0, q1 = float(antes.get("Quantity") or 0), float(depois.get("Quantity") or 0)
        w0, w1 = float(antes.get("Weight1") or 0), float(depois.get("Weight1") or 0)
        if abs(w0 - w1) < 0.005:
            continue
        quem = str(depois.get("U_NAME") or depois.get("USER_CODE") or "usuário não identificado").strip()
        quando = _momento_do_sap(depois.get("UpdateDate"), depois.get("UpdateTS"))
        peso_criado = float(versoes[0].get("Weight1") or 0)
        criado_certo = abs(peso_criado - esperado) <= esperado * 0.01
        fecho = (
            f" A integração tinha gravado o peso certo ({numero_br(peso_criado)} kg) ao criar o pedido."
            if criado_certo else ""
        )
        mudou_quantidade = abs(q0 - q1) > 1e-9
        if mudou_quantidade:
            texto = (
                f"CAUSA: {quem} mudou a quantidade da linha {line_num} de {q0:g} para {q1:g} no SAP"
                f"{quando}, e o SAP refez o peso na mesma proporção ({numero_br(w0)} → "
                f"{numero_br(w1)} kg).{fecho}"
            )
        else:
            texto = (
                f"CAUSA: {quem} mudou o peso da linha {line_num} no SAP{quando} "
                f"({numero_br(w0)} → {numero_br(w1)} kg).{fecho}"
            )
        return {
            "tipo": "quantidade_mudada_no_sap" if mudou_quantidade else "peso_mudado_no_sap",
            "usuario": quem,
            "momento": _momento_iso(depois.get("UpdateDate"), depois.get("UpdateTS")),
            "quantidade_antes": q0,
            "quantidade_depois": q1,
            "peso_antes": round(w0, 2),
            "peso_depois": round(w1, 2),
            "integracao_gravou_certo": criado_certo,
            "texto": texto,
        }
    return None


def _momento_iso(data: object, hora: object) -> str | None:
    """`UpdateDate` + `UpdateTS` (HHMMSS) → "2026-10-01T14:00:06", or the date alone, or None."""
    if data is None or not hasattr(data, "strftime"):
        return None
    try:
        hhmmss = int(hora)
    except (TypeError, ValueError):
        return f"{data:%Y-%m-%d}"
    return f"{data:%Y-%m-%d}T{hhmmss // 10000:02d}:{hhmmss // 100 % 100:02d}:{hhmmss % 100:02d}"


def _momento_do_sap(data: object, hora: object) -> str:
    """`UpdateDate` (date) + `UpdateTS` (HHMMSS as an int) → " em 01/10/2026 às 14:00"."""
    texto = ""
    if data is not None and hasattr(data, "strftime"):
        texto = f" em {data:%d/%m/%Y}"
    try:
        hhmmss = int(hora)
    except (TypeError, ValueError):
        return texto
    return f"{texto} às {hhmmss // 10000:02d}:{hhmmss // 100 % 100:02d}"


_LINHA_MANUAL_VAZIA = Linha(ped_cliente="", item_cliente="0", nf="", cor="")


def _monta_linha_pedido(
    item: OportunidadeDoc, item_sap: str, manual: Linha, peso: float | None = None
) -> dict:
    """One `DocumentLines` entry built from a WBC quote item.

    The two branches of `_update_pedido` built the SAME nine keys in the same order, differing only by
    `Weight1` (only the first has it). Two copies of nine fields is the kind of duplication that
    survives a fix made on one side only. `U_nItem` is added only when the manual line has a customer
    item.
    """
    linha = {
        "ItemCode": item_sap,
        "Quantity": item.orc_prod_quantidade,
        "Price": item.orc_val,
        "U_INO_Id_IntWBC": str(item.id_integracao),
        "U_INO_ORCITM": str(item.orc_item),
        "U_INO_D_Adicionais": item.orc_txt,
        "U_xPed": manual.ped_cliente,
        "U_INO_Composicao": manual.nf,
        "U_INO_COR": manual.cor,
    }
    if peso is not None:
        linha["Weight1"] = peso
    # `"0"` é o sentinela do legado para "sem item do cliente" — a chave não vai no corpo.
    if manual.item_cliente != "0":
        linha["U_nItem"] = manual.item_cliente
    return linha


async def _update_pedido(
    sl: ServiceLayerClient,
    wbc: WbcSqlServerClient,
    hana_reader: HanaDirectReader,
    oportunidades: list[OportunidadeDoc],
    orc_num: str,
    card_code: str,
    doc_num: str,
    doc_entry: str,
    tb_valdixson: int,
    process: str,
) -> int:
    """`ProcessDefault.UpdatePedido` — recreate the order lines from `oportunidades`
    (`GetItensSAP` + `GetPesoPedido`).

    CLOSED (F7, 29/09/2026): it raises `ValueError` at its first statement until Anderson validates it
    in homologation, so everything after the `raise` is unreachable (kept as the port of the C#). Only
    a non-frozen order with `U_INO_UpdateDetalhe='Y'` gets here. Two sub-paths, as in the C#:
    `tb_valdixson != 0` (reuse the existing order/DocEntry, line weights from `GetPesoPedido`) vs the
    `else` (read salesperson/terms from the original `SalesOpportunities` and build the order from
    scratch).

    ⚠️ The Service Layer replaces document lines by sending the whole `DocumentLines` array in a single
    `PATCH` (there is no line-by-line `Lines.Delete()` as in the DI API); ported that way — still to be
    validated against the real environment that this recreates the lines with the same effect as the
    legacy.
    """
    # F7 (29/09/2026): closed until Anderson validates it in homologation. This path
    # rewrites every order line with a full DocumentLines PATCH and was never exercised by
    # the port; until the F7 fix it also sent SAP tables to the WBC SQL Server, so it would
    # have failed before writing anyway. Refusing here keeps that "nothing written" outcome,
    # with a message that says why. Today it is unreachable: open orders are Congelado='Y'.
    raise ValueError(
        "Caminho 'UpdatePedido' (recria as linhas do pedido) fechado: nunca foi validado no "
        "porte. Pedido não congelado com U_INO_UpdateDetalhe='Y' — chame o Anderson "
        "(plano, F7)."
    )
    linhas_manuais = await _busca_linhas_manuais(hana_reader, doc_entry)
    novas_linhas: list[dict] = []

    if tb_valdixson != 0:
        for idx, item in enumerate(oportunidades):
            item_sap_rows = hana_reader.fetch_all(*ligar(q.GET_ITENS_SAP, grp_code=item.grp_code))
            if not item_sap_rows:
                continue
            peso_rows = hana_reader.fetch_all(
                *ligar(q.GET_PESO_PEDIDO, doc_entry=tb_valdixson, orc_item=item.orc_item)
            )
            peso = peso_rows[0].get("U_INO_PESO") if peso_rows else None
            manual = linhas_manuais[idx] if idx < len(linhas_manuais) else _LINHA_MANUAL_VAZIA
            novas_linhas.append(
                _monta_linha_pedido(item, _primeiro_valor(item_sap_rows), manual, peso)
            )
        campos = {
            "U_INO_ORCAMENTO": tb_valdixson,
            "U_INO_ProcessWBC": process,
            "U_INO_VERSAOWBC": oportunidades[0].versao if oportunidades else "",
            "U_INO_Congelado": "Y",
            "DocumentLines": novas_linhas,
        }
    else:
        # ⚠️ `doc_num` (nº do PEDIDO) como chave de `SalesOpportunities`, que espera o
        # `OpprId`. Parece errado — e é —, mas é RÉPLICA FIEL do C#: a chamada viva passa
        # `oRs.Fields.Item(2)` = DocNum (IntegraPedidoWBC.b1f.cs 276 e 831), enquanto a
        # linha COMENTADA logo acima (801) passa `"Num Oportunidade"`, que é o OpprId de
        # verdade. Ou seja: o autor do addon sabia o valor certo e a versão que ficou no
        # ar usa o errado.
        #
        # Não corrigido por conta própria por dois motivos: (a) é o mesmo erro de
        # numeração que custou o bug de 22/09 (seção 7.35) e merece decisão explícita;
        # (b) este ramo hoje é inalcançável — a função inteira está fechada (F7) e, desde
        # 05/10/2026, só cairia aqui com o Detalhe do Orçamento sem número.
        # Rastreado em 23/09/2026.
        oport_orig = await sl.get_by_key("SalesOpportunities", doc_num)
        vendedor = oport_orig.get("SalesPersonCode")
        orc_cab_rows = wbc.fetch_all(q.GET_ORCCAB, (orc_num,))
        tipo_montagem = valor_montagem = percentual_comiss = valor_comiss = 0
        dias = 0
        cdpag = ""
        for row in orc_cab_rows:
            tipo_montagem = row.get("TIPMONCOD", "")
            valor_montagem = row.get("ORCBAS3", 0)
            percentual_comiss = row.get("ORCPERCOM", 0)
            dias = row.get("PRZENT", 0)
            cdpag = row.get("PGTCOD", "") or ""
            valor_comiss = row.get("ORCVALCOM", 0)

        for idx, item in enumerate(oportunidades):
            item_sap_rows = hana_reader.fetch_all(*ligar(q.GET_ITENS_SAP, grp_code=item.grp_code))
            if not item_sap_rows:
                continue
            manual = linhas_manuais[idx] if idx < len(linhas_manuais) else _LINHA_MANUAL_VAZIA
            # Sem `peso`: este ramo monta o pedido do zero e o legado não busca
            # `GET_PESO_PEDIDO` aqui. Diferença preservada de propósito.
            novas_linhas.append(
                _monta_linha_pedido(item, _primeiro_valor(item_sap_rows), manual)
            )

        campos = {
            "CardCode": card_code,
            "DocDueDate": (datetime.now() + timedelta(days=dias)).strftime("%Y-%m-%d"),
            "Comments": cdpag[:100],
            "SalesPersonCode": vendedor,
            "U_INO_COTWBC": oportunidades[0].orc_num if oportunidades else "",
            "U_INO_TIPO_MT": tipo_montagem,
            "U_INO_VL_MT": valor_montagem,
            "U_INO_COM": str(percentual_comiss),
            "U_INO_VL_COM": valor_comiss,
            "U_INO_ORCAMENTO": 0,
            "DocumentLines": novas_linhas,
        }

    try:
        await sl.update_entity("Orders", doc_entry, campos)
        return 0
    except Exception as exc:  # noqa: BLE001
        await preenche_log(sl, orc_num, "Pedido", "Erro", "Integração", str(exc))
        raise


async def _busca_linhas_manuais(hana_reader: HanaDirectReader, doc_entry: str) -> list[Linha]:
    """`Querys.ManualLinha` — manual data (customer order, item, NF, colour) already filled in the
    order's existing lines, in the order they appear (`RDR1`: HANA).
    """
    rows = hana_reader.fetch_all(*ligar(q.MANUAL_LINHA, doc_entry=doc_entry))
    return [
        Linha(
            ped_cliente=str(row.get("U_xPed", "")),
            item_cliente=str(row.get("U_nItem", "0")),
            nf=str(row.get("U_INO_Composicao", "")),
            cor=str(row.get("U_INO_COR", "")),
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# 3. busca_estrutura_produto  <- SQLConnection.PegaEstruturaPrd
# ---------------------------------------------------------------------------
async def busca_estrutura_produto(wbc: WbcSqlServerClient, orc_nums: list[str]) -> list[EstruturaPrd]:
    """Fetch the full structure tree of the quote(s) (`PEGA_ESTRUTURA_PRD_WBC`).

    **Positional** mapping, replicating `SQLConnection.PegaEstruturaPrd` (SQLConnection.cs ~487): the
    query does `SELECT tst.*, num_row, <computed PRDCOD>` and the C# reads by index, not by name — the
    `PRDCOD` of `INTEGRACAO_ORCPRDARV` (column 4) becomes `PrdArv`; the LAST column, the `PRDCOD`
    recomputed/coalesced with `INTEGRACAO_ORCPRD`, becomes `PrdCode`; `num_row` (`ROW_NUMBER()`, second
    to last) becomes `linhaOrc`; and `linha = OrcItm`. Do not swap `prd_arv`/`prd_code`.

    Also replicates the original `.Reverse()`: list order matters for the per-level groupings done
    later (the final order is deepest level first, down to level 1, as in the C#).
    """
    if not orc_nums:
        return []
    rows = wbc.fetch_all(*ligar(q.PEGA_ESTRUTURA_PRD_WBC, orc_nums=[str(n) for n in orc_nums]))
    estrutura = []
    for row in rows:
        v = list(row.values())
        # Índices de INTEGRACAO_ORCPRDARV (tst.*): 0 ORCNUM, 1 GRPCOD, 2 SUBGRPCOD,
        # 3 ORCITM, 4 PRDCOD, 5 ORCPRDARV_NIVEL, 6 CORCOD, 7 PRDDSC, 8 ORCQTD, 9 ORCTOT,
        # 10 ORCPES, 11 idIntegracao_OrcPrdArv, 12 orcprdarv_dth; depois: 13 num_row,
        # 14 PRDCOD recalculado (o `SELECT tst.*, ...` final da query).
        orc_itm = int(v[3])
        estrutura.append(
            EstruturaPrd(
                orc_num=str(v[0]),
                grp_code=int(v[1]),
                sub_group_cod=int(v[2]),
                orc_itm=orc_itm,
                prd_code=str(v[14]).upper(),
                nivel=int(v[5]),
                cor_cod=str(v[6] or ""),
                prd_desc=str(v[7] or ""),
                quantidade=float(v[8] or 0),
                total=float(v[9] or 0),
                peso=float(v[10] or 0),
                id_integracao_orc=int(v[11]),
                linha_orc=str(v[13]),
                prd_arv=str(v[4] or ""),
                linha=orc_itm,
            )
        )
    estrutura.reverse()
    return estrutura


# ---------------------------------------------------------------------------
# 4. garante_itens_cadastrados  <- ProcessDefault.CriaItem / UpdateItem
# ---------------------------------------------------------------------------
async def garante_itens_cadastrados(
    sl: ServiceLayerClient, hana_reader: HanaDirectReader, estrutura: list[EstruturaPrd]
) -> None:
    """For each structure item not yet registered in the SAP, create it (`CriaItem`); if it already
    exists and its `PrdArv` is in the solda list, force group 332 (`UpdateItem`).

    `nivel_top` — the lowest `nivel` in the structure — is computed once and reused for every item
    (equivalent of `Form2.nivelTop`, read by `CriaItem` to choose `ProcurementMethod` Make vs Buy). The
    C# only recomputed it when creating an item of a lower level; no practical effect here since the
    whole structure is already in hand.
    """
    nivel_top = min((item.nivel for item in estrutura), default=0)
    solda = listas_fixas.carregar_solda()
    for item in estrutura:
        await _cria_ou_atualiza_item(sl, hana_reader, item, nivel_top, solda)


async def _cria_ou_atualiza_item(
    sl: ServiceLayerClient,
    hana_reader: HanaDirectReader,
    item: EstruturaPrd,
    nivel_top: int,
    solda: frozenset[str],
) -> None:
    """Per-item step of the `Button0_ClickAfter` loop (~324-374), split from
    `garante_itens_cadastrados` so it can run with the `HanaDirectReader` already opened by the
    orchestrator (`processar_pedidos_novos`).

    A failure to create the item is logged (error + `INO_LOG`) and swallowed;
    `item.processado_itens` becomes "Y" only when the item exists or was created.
    """
    contagem_rows = hana_reader.fetch_all(*ligar(q.SELECT_CODIGO_ITEM, item_code=item.prd_code))
    ja_existe = contagem_rows and int(_primeiro_valor(contagem_rows)) != 0

    if not ja_existe:
        try:
            await _cria_item(sl, item, nivel_top)
            logger.info("  Item criado: %s (%s).", item.prd_code, item.prd_desc)
            item.processado_itens = "Y"
        except Exception as exc:  # noqa: BLE001
            # O código do item entra na mensagem: o `prd_desc` sozinho não diz qual URL
            # foi chamada, e esta etapa já falhou por causa do formato do código
            # (`#` em 15/09, `/` em 22/09).
            logger.error("  Falha ao criar o item %s (%s): %s", item.prd_code, item.prd_desc, exc)
            await preenche_log(
                sl, "", "Pedido", "Erro", "Integração", f"Erro ao criar o item: {item.prd_desc}. Erro: {exc}"
            )
    else:
        item.processado_itens = "Y"

    if item.prd_arv in solda:
        logger.info("  Item %s está na lista de solda: forçando grupo %s.", item.prd_code, GRUPO_ITEM_SOLDA)
        await _update_item_grupo_332(sl, item.prd_code)


async def _cria_item(sl: ServiceLayerClient, item: EstruturaPrd, nivel_top: int) -> None:
    """`ProcessDefault.CriaItem` — `POST /Items`."""
    contem_prefixo_333 = any(prefixo in item.prd_code for prefixo in listas_fixas.PREFIXOS_GRUPO_333)

    body: dict = {
        "ItemCode": item.prd_code,
        "ItemName": item.prd_desc,
        "ForeignName": item.prd_desc,
        "ItemType": "itItems",
        "PurchaseItem": "tYES",
        "InventoryItem": "tYES",
        "SalesItem": "tNO",
        "IssueMethod": "im_Manual",
        "PlanningSystem": "bop_MRP",
        "U_INO_CODARV": item.prd_arv,
    }

    if contem_prefixo_333:
        body["ItemsGroupCode"] = GRUPO_ITEM_NIVEL_TOP
        body["MaterialType"] = "mt_FinishedGoods"
    else:
        body["MaterialType"] = "mt_Package"
        solda = listas_fixas.carregar_solda()
        body["ItemsGroupCode"] = GRUPO_ITEM_SOLDA if item.prd_code in solda else GRUPO_ITEM_PADRAO

    if item.nivel < nivel_top:
        body["ProcurementMethod"] = "bom_Make"
    else:
        body["ProcurementMethod"] = "bom_Buy"
        body["PlanningSystem"] = "bop_None"

    if item.prd_code in listas_fixas.EXPLOSAO_SOLDA:
        body["U_INO_EXPL_SOLDA"] = "S"

    await sl.create_entity("Items", body)


async def _update_item_grupo_332(sl: ServiceLayerClient, item_code: str) -> None:
    """`ProcessDefault.UpdateItem` — `PATCH /Items('{ItemCode}')` forçando grupo 332."""
    # Chave crua + `chave_texto=True`: quem monta/encoda a chave da URL é o
    # `ServiceLayerClient._formata_chave`. Códigos de item contêm `#` (precisa virar `%23`)
    # e podem ser só dígitos — daí o `chave_texto`, que força as aspas do literal.
    await sl.update_entity("Items", item_code, {"ItemsGroupCode": GRUPO_ITEM_SOLDA}, chave_texto=True)


# ---------------------------------------------------------------------------
# 5. cria_recurso_rateio  <- ProcessDefault.CriaResources
# ---------------------------------------------------------------------------
def _descricao_item(hana_reader: HanaDirectReader, item_code: str) -> str:
    """Item name in the SAP — used only for the log. Never aborts the run."""
    try:
        rows = hana_reader.fetch_all(*ligar(q.DESCRICAO_ITEM, item_code=item_code))
        return str(_primeiro_valor(rows, ""))
    except Exception:  # noqa: BLE001 - uma descrição ausente não pode parar uma gravação
        return ""


def _resumo(texto: object, limite: int = 60) -> str:
    """Description shortened to fit one log line.

    WBC product descriptions exceed 100 characters ("COLUNA NORMAL 80 mm ch. 1,80 4800MM ..."), and a
    line that wraps three times on screen stops being a progress trace.
    """
    limpo = " ".join(str(texto or "").split())
    return limpo if len(limpo) <= limite else limpo[: limite - 1].rstrip() + "…"


def _quantidade(valor: object) -> str:
    """Quantity without useless decimals: `640`, not `640.0`."""
    try:
        return f"{float(valor):g}"
    except (TypeError, ValueError):
        return str(valor or "")


def _num(valor: object, padrao: float = 0.0) -> float:
    """Convert a number coming from HANA or SQL Server to `float`.

    Both drivers return `decimal.Decimal` for numeric columns, and `Decimal` **does not mix with
    `float`**: `2.0 / Decimal("10")` raises `TypeError: unsupported operand type(s) for /: 'float' and
    'decimal.Decimal'`. That exact error made the rateio resource of item I000005 fail on quote
    00120634 (21/09/2026) — the `except` of `_processa_grupo_producao` swallowed it, logged to
    `INO_LOG`, and the OP went out without the rateio line; it only showed up days later, comparing
    with the legacy.

    Same class of problem as `Decimal is not JSON serializable` (section 7.8): a database type leaking
    into the logic. The answer is the same — convert once at the boundary instead of scattering
    `float(...)` around every operation.

    Accepts `None` and "" (returns `padrao`) and strings with a decimal comma, which is how the WBC
    returns some converted fields.
    """
    if valor is None or valor == "":
        return padrao
    if isinstance(valor, str):
        valor = valor.replace(",", ".")
    try:
        return float(valor)
    except (TypeError, ValueError):
        return padrao


def _digitos_do_valor_como_no_legado(valor: object) -> str:
    """Digits of a number as the C# would produce them, to compose the rateio Resource code
    (`GGF_<orc><item><digits>`).

    The legacy builds that code from `valor.ToString()` of a `double` in the pt-BR culture and then
    strips the decimal separator. In `double.ToString()` a whole value comes out **without a decimal
    part**: `0` becomes `"0"`, and `3854.14` becomes `"3854,14"` -> `"385414"`.

    The port used a plain `str(valor)`. For 3854.14 the result matches, but for a whole value Python
    appends `.0` (`str(0.0) == "0.0"`, `str(Decimal("0.00")) == "0.00"`), producing `"00"`/`"000"`
    instead of `"0"` — a different code from the one the legacy created. Practical effect: the port did
    not recognise the existing resource (`GGF_00120634I0000050`) and tried to create another. Found
    21/09/2026 in the first real comparison against production (section 7.24).

    Still debt no. 8 of section 8 (decimal parsing culture), here in the trickiest place: not in a
    calculation but in the formation of an identifier.
    """
    numero = _num(valor)
    if numero == int(numero):
        return str(int(numero))
    # `repr` dá a representação mais curta que volta ao mesmo float ("3854.14"), evitando
    # o "3854.1400000000003" que uma formatação ingênua produziria.
    return repr(numero).replace(".", "")


async def cria_recurso_rateio(
    sl: ServiceLayerClient,
    hana_reader: HanaDirectReader,
    custos_wbc: list[float],
    nome_recurso: str,
    quantidade_linha: float,
    valor_total: float,
    valor_linha: float,
    nome: str,
) -> str:
    """Create (if it does not exist) a Resource representing the transport/packing/assembly rateio,
    or return the code of the existing one.

    Returns the `Code` of the created resource; for an existing one, the `VisResCode` read from ORSC
    (`PegaRecursoCode`); or `""` when there is no rateio base (`quantidade_linha == -1`,
    `valor_total == -1`, or `valor_total == 0`, which also warns). Existence is checked first, so an
    existing resource is returned even then. With empty `custos_wbc` the resource is still created, with
    zero costs.

    Field names confirmed 23/09/2026 against a real resource (`diag entidade Resources`, read-only).
    Until then the body used `ResourceCode`/`Warehouses`/`WarehouseCode`/`DailyCapacities` "per the
    documentation" — none exists in the Service Layer — and **every** `POST /Resources` of the port
    failed with `Data DailyCapacities not found`. The OP went out anyway, without the rateio line
    (order 84426, guide section 7.42). The real names are the DI API's, prefixed in the collections:
    `VisCode`, `ResourceWarehouses[].Warehouse`, `ResourceDailyCapacities[].Weekday/Factor1`.

    Faithful to the C# (`CriaResources`): only `VisCode` is sent — the internal `Code` is left to B1.
    """
    contagem_rows = hana_reader.fetch_all(*ligar(q.COUNT_RECURSO, nome_recurso=nome_recurso))
    ja_existe = contagem_rows and str(list(contagem_rows[0].values())[0]) != "0"

    if ja_existe:
        rows = hana_reader.fetch_all(*ligar(q.PEGA_RECURSO_CODE, nome_recurso=nome_recurso))
        return str(_primeiro_valor(rows, ""))

    # Normaliza ANTES de qualquer conta: os três podem chegar como `Decimal` do HANA e
    # `Decimal` não opera com `float` (ver `_num`).
    quantidade_linha = _num(quantidade_linha)
    valor_total = _num(valor_total)
    valor_linha = _num(valor_linha)

    if quantidade_linha == -1 or valor_total == -1:
        return ""
    if valor_total == 0:
        # Divisão por zero daria ZeroDivisionError, que o `except` de quem chama engoliria
        # do mesmo jeito que engoliu o TypeError. Sem valor total não há rateio a fazer.
        logger.warning(
            "Recurso %s não criado: valor total do pedido é zero, não há base para ratear.",
            nome_recurso,
        )
        return ""

    custo_transporte = custo_embalagem = custo_montagem = 0.0
    if custos_wbc:
        custo_transporte = ((quantidade_linha * _num(custos_wbc[0])) / valor_total) * valor_linha
        custo_embalagem = ((quantidade_linha * _num(custos_wbc[1])) / valor_total) * valor_linha
        custo_montagem = ((quantidade_linha * _num(custos_wbc[2])) / valor_total) * valor_linha

    body = {
        "VisCode": nome_recurso,
        "Name": f"OrcNum / idOrçamento: {nome}",
        "ForeignName": f"OrcNum / idOrçamento: {nome}",
        "ResourceWarehouses": [{"Warehouse": DEPOSITO_RECURSO}],
        "Cost1": custo_transporte,
        "Cost2": custo_embalagem,
        "Cost3": custo_montagem,
        "ResourceDailyCapacities": [{"Weekday": "rdcwFirst", "Factor1": 1}],
    }
    resultado = await sl.create_entity("Resources", body)
    codigo = str(resultado.get("Code") or resultado.get("VisCode") or nome_recurso)
    logger.info("    Recurso de rateio %s criado (Code=%s).", nome_recurso, codigo)
    return codigo


# ---------------------------------------------------------------------------
# 6/7. cria_ordem_producao / cria_ordem_producao_semi_acabado <- CriaOP / CriaOPSA
# ---------------------------------------------------------------------------
async def cria_ordem_producao(
    sl: ServiceLayerClient,
    hana_reader: HanaDirectReader,
    lista_op: list[EstruturaPrd],
    item_pai: str,
    linha_base: int,
    quantidade_linha_base: float,
    recurso: str,
    entrega_multipla: str,
) -> tuple[int, int]:
    """`ProcessDefault.CriaOP` — create the parent item's OP, with one component line per level-1
    item of the structure plus the resource line (if any). A `TPO00000000000000000` line with colour
    `S/ COR` or `S/ PINT` is skipped.

    Returns `(status, doc_entry_created)` — `0`/DocEntry on success, replicating the original
    `int[] final`. When `entrega_multipla == "Y"` the C# **does not create the OP** (leaves
    `retorno = 0, final[1] = 0` without calling `Add()`) — preserved here (returns `(0, 0)`), although
    it looks like an inverted condition (migration_guide.md section 9; worth confirming with Anderson
    whether it is intentional). A Service Layer error is logged to `INO_LOG` and re-raised.
    """
    if entrega_multipla == "Y":
        return 0, 0

    codigo_rows = hana_reader.fetch_all(*ligar(q.SELECT_CODIGO_ITEM_OP, item_code=item_pai))
    if codigo_rows:
        item_pai = str(list(codigo_rows[0].values())[0])

    primeiro_nivel_1 = next((i for i in lista_op if i.nivel == 1), lista_op[0] if lista_op else None)
    doc_entry = ""
    if primeiro_nivel_1:
        doc_entry_rows = hana_reader.fetch_all(*ligar(q.PEGA_DOC_ENTRY_PED, orc_num=primeiro_nivel_1.orc_num))
        if doc_entry_rows:
            doc_entry = str(doc_entry_rows[0]["DocEntry"])

    linhas: list[dict] = []
    # `U_INO_LinhaRef` do CABEÇALHO: o C# original (ProcessDefault.cs ~683) reatribui
    # `OrdemPrducao.UserFields.Fields.Item("U_INO_LinhaRef").Value = item.linha` DENTRO do
    # foreach das linhas, então o valor que efetivamente vai para a OP é o do ÚLTIMO item
    # processado (não o `linhaBase` setado antes do loop, linha ~637, que só sobra quando
    # nenhum item é processado). Réplica fiel — descoberto em 15/09/2026 relendo o C# ao
    # investigar o erro de `ProductionOrderIssueType`.
    linha_ref_header = str(linha_base)
    for item in lista_op:
        if item.prd_code == "TPO00000000000000000" and item.cor_cod in ("S/ COR", "S/ PINT"):
            continue
        linha_ref_header = str(item.linha)
        codigo_op_rows = hana_reader.fetch_all(*ligar(q.SELECT_CODIGO_ITEM_OP, item_code=item.prd_code))
        item_code = str(list(codigo_op_rows[0].values())[0]) if codigo_op_rows else item.prd_code
        linhas.append(
            {
                "ItemNo": item_code,
                "ItemType": "pit_Item",
                "PlannedQuantity": round(item.quantidade, 2),
                "U_INO_PESO": item.peso,
                "U_INO_DESC": item.linha_orc,
                "ProductionOrderIssueType": "im_Backflush",
                **(_deposito_do_item(item_code)),
            }
        )

    if recurso:
        linhas.append(
            {
                "ItemNo": recurso,
                "ItemType": "pit_Resource",
                "Warehouse": DEPOSITO_RECURSO,
                "PlannedQuantity": 1,
                "ProductionOrderIssueType": "im_Backflush",
            }
        )

    body = {
        "ProductionOrderType": "bopotSpecial",
        "ProductionOrderStatus": "boposPlanned",
        "ItemNo": item_pai,
        "PlannedQuantity": quantidade_linha_base,
        "DueDate": (datetime.now() + timedelta(days=PRAZO_OP_DIAS)).strftime("%Y-%m-%d"),
        "PostingDate": datetime.now().strftime("%Y-%m-%d"),
        "ClosingDate": (datetime.now() + timedelta(days=PRAZO_OP_DIAS)).strftime("%Y-%m-%d"),
        "ProductionOrderOrigin": "bopooSalesOrder",
        "U_INO_LinhaRef": linha_ref_header,
        "ProductionOrderLines": linhas,
        **(_deposito_do_item(item_pai)),
        **({"ProductionOrderOriginEntry": int(doc_entry)} if doc_entry else {}),
    }

    try:
        resultado = await sl.create_entity("ProductionOrders", body)
        doc_entry_criado = _chave_do_documento_criado(resultado, "ProductionOrders")
        logger.info(
            "  OP criada: DocEntry=%s, item %s — %s (%s un), %d linha(s).",
            doc_entry_criado, item_pai,
            _resumo(next((i.prd_desc for i in lista_op if i.prd_code == item_pai), "")
                    or (lista_op[0].prd_desc if lista_op else "")),
            _quantidade(quantidade_linha_base), len(linhas),
        )
        return 0, doc_entry_criado
    except Exception as exc:  # noqa: BLE001
        orc_num = lista_op[0].orc_num if lista_op else ""
        await preenche_log(sl, orc_num, "Pedido", "Erro", "Integração", f"Erro ao criar op: {exc}")
        raise


async def cria_ordem_producao_semi_acabado(
    sl: ServiceLayerClient,
    hana_reader: HanaDirectReader,
    item_pai: str,
    linha_base: int,
    quantidade_linha_base: float,
    recurso: str,
    quantidade_anterior: float,
    semi_acabados: list,
    oopr: str,
    quantidade_item_pai: float,
) -> int:
    """`ProcessDefault.CriaOPSA` — create the OP of a semi-finished item, one line per item in
    `semi_acabados`. Returns the created DocEntry, or `-1` when the Service Layer call fails (logged,
    not raised). The resource line is commented out in the original C# (dead code) — **deliberately
    not ported**, so `recurso` and `quantidade_anterior` are accepted and ignored.
    """
    doc_entry_rows = hana_reader.fetch_all(*ligar(q.PEGA_DOC_ENTRY_PED, orc_num=oopr))
    doc_entry = int(doc_entry_rows[0]["DocEntry"]) if doc_entry_rows else None

    linhas = []
    for item in semi_acabados:
        peso = round(item.peso, 2)
        linha = {
            "ItemNo": item.item_code,
            "ItemType": "pit_Item",
            "PlannedQuantity": round(item.quantidade, 2),
            "U_INO_PESO": peso,
            "U_INO_DESC": item.linha_orc,
            "ProductionOrderIssueType": "im_Backflush",
        }
        linha.update(_deposito_do_item(item.item_code))
        linhas.append(linha)

    body = {
        "ProductionOrderType": "bopotSpecial",
        "ProductionOrderStatus": "boposPlanned",
        "ItemNo": item_pai,
        "PlannedQuantity": quantidade_item_pai,
        "DueDate": (datetime.now() + timedelta(days=PRAZO_OP_DIAS)).strftime("%Y-%m-%d"),
        "PostingDate": datetime.now().strftime("%Y-%m-%d"),
        "ClosingDate": (datetime.now() + timedelta(days=PRAZO_OP_DIAS)).strftime("%Y-%m-%d"),
        "ProductionOrderOrigin": "bopooSalesOrder",
        "U_INO_LinhaRef": str(linha_base),
        "ProductionOrderLines": linhas,
        **(_deposito_do_item(item_pai)),
        **({"ProductionOrderOriginEntry": doc_entry} if doc_entry else {}),
    }
    # Nota: `recurso` chega aqui mas nunca é anexado à OP — igual ao C# (bloco comentado).
    del recurso, quantidade_anterior

    try:
        resultado = await sl.create_entity("ProductionOrders", body)
        doc_entry_criado = _chave_do_documento_criado(resultado, "ProductionOrders")
        logger.info(
            "  OP de semiacabado criada: DocEntry=%s, item %s — %s (%s un), %d componente(s).",
            doc_entry_criado, item_pai, _resumo(_descricao_item(hana_reader, item_pai)),
            _quantidade(quantidade_item_pai), len(linhas),
        )
        return doc_entry_criado
    except Exception as exc:  # noqa: BLE001
        logger.error("Erro ao realizar a criação do semi-acabado: %s", exc)
        return -1


# ---------------------------------------------------------------------------
# 8. checa_semi_acabado  <- ProcessDefault.ChecaSemiAcabado (RECURSIVO) + CriaOPSA
# ---------------------------------------------------------------------------
async def checa_semi_acabado(
    sl: ServiceLayerClient,
    wbc: WbcSqlServerClient,
    hana_reader: HanaDirectReader,
    item_pai: str,
    linha_base: int,
    quantidade_linha_base: float,
    recurso: str,
    quantidade_anterior: float,
    oopr: str,
    quantidade_item_pai: float,
    id_integracao: int,
    numero_orc: str,
    estrutura_completa: list[EstruturaPrd],
) -> None:
    """Check (via `newBuscaItem`/`verificaSemiAcabadoPeso`) whether `item_pai` has "children" at the
    next structure level; if so, create a new OP for the semi-finished item
    (`cria_ordem_producao_semi_acabado`) and **recurse** for each child — this is where the quote's
    structure tree becomes the cascade of OPs chained by level (migration_guide.md section 4.4.1,
    step 6).

    Any error is logged (`INO_LOG` + logger) and swallowed, never propagated — like the original C#
    `catch`.
    """
    from controleproducao.modules.pedidos_wbc.schemas import SemiAcabado  # import local p/ evitar ciclo

    try:
        codigos = wbc.fetch_all(
            q.NEW_BUSCA_ITEM, (oopr, id_integracao, id_integracao, oopr, id_integracao, id_integracao)
        )
        codigos_planos = [_primeiro_valor([row]) for row in codigos]
        if not codigos_planos:
            return

        semi_acabados: list[SemiAcabado] = []
        for codigo in codigos_planos:
            # `fetch_all_values`: os 3 campos vêm de `REPLACE(CONVERT(...))`/`CONVERT(...)`
            # sem alias, que colidem por nome no driver (mesmo bug de
            # `_busca_header_nova_tabela_quot`).
            peso_qtd_rows = wbc.fetch_all_values(q.VERIFICA_SEMI_ACABADO_PESO, (codigo, id_integracao, oopr))
            if not peso_qtd_rows:
                continue
            valores = peso_qtd_rows[0]
            peso = float(str(valores[0]).replace(",", "."))
            quantidade = float(str(valores[1]).replace(",", "."))
            id_arv = int(valores[2])

            linha_orc = "0"
            for est in estrutura_completa:
                if est.prd_code == codigo and int(est.id_integracao_orc) == id_arv:
                    linha_orc = est.linha_orc
                    break

            semi_acabados.append(
                SemiAcabado(item_code=codigo, quantidade=quantidade, peso=peso, id_int=id_arv, linha_orc=linha_orc)
            )

        await cria_ordem_producao_semi_acabado(
            sl, hana_reader, item_pai, linha_base, quantidade_linha_base, recurso,
            quantidade_anterior, semi_acabados, oopr, quantidade_item_pai,
        )

        for item in semi_acabados:
            await checa_semi_acabado(
                sl, wbc, hana_reader, item.item_code, linha_base, quantidade_linha_base,
                recurso, quantidade_item_pai, oopr, item.quantidade, item.id_int,
                item.linha_orc, estrutura_completa,
            )

    except Exception as exc:  # noqa: BLE001 - réplica do catch do C# (loga e segue)
        await preenche_log(
            sl, oopr, "Pedido", "Erro", "Integração", f"Erro ao realizar o processamento do semi-acabado: {exc}"
        )
        logger.error("Erro ao realizar o processamento do semi-acabado: %s", exc)


# ---------------------------------------------------------------------------
# AtualizaDoc (Tipo="OP"/"Erro"/"Sucesso" — o "UpdateOrc" do C# pertencia ao módulo 1,
# removido do porte em 22/09/2026)
# ---------------------------------------------------------------------------
async def marca_op_nas_linhas(
    sl: ServiceLayerClient,
    hana_reader: HanaDirectReader,
    orc_itens: list,
    num_op: int,
    doc_entry: str,
) -> None:
    """Write `U_INO_OP = num_op` on the order lines matching `orc_itens` (`U_INO_ORCITM` values), in
    **a single** Service Layer call.

    ⚠️ Optimisation of 16/09/2026. Before, `_processa_grupo_producao` called `_atualiza_doc(..., "OP")`
    once per group item, each doing 1 HANA query + 1 `PATCH /Orders`. In the profile of quote 00125192
    that was **6 PATCHes totalling 4.1 s** (~680 ms each) — the largest single slice of the run. Now: 1
    query (resolving all lines at once) + 1 PATCH with all lines in the same body.

    The final state is identical — the C# also did one `Update()` per item, but that was a DI API trait,
    not a business rule. The difference shows only when something fails mid-group: before, the marks
    already made stayed saved; now it is all or nothing. Since an exception aborts the whole order
    anyway (the `except` of `processar_pedidos_novos`), the partial state was not coherent either way.
    """
    if not doc_entry:
        return

    # Rede de segurança: gravar `U_INO_OP = 0` é pior do que não gravar — deixaria o pedido
    # parecendo vinculado a uma OP inexistente. Ver `_chave_do_documento_criado`.
    if not num_op:
        logger.warning(
            "OP sem número identificado (num_op=%s) — `U_INO_OP` NÃO gravado no pedido %s. "
            "A OP pode ter sido criada mesmo assim; conferir em OWOR.",
            num_op, doc_entry,
        )
        return

    # `orc_itens` são valores de `U_INO_ORCITM` (item do orçamento), não números de linha —
    # igual ao C#, que passa `item.linha` (= OrcItm) e resolve a linha real via
    # `Querys.Linha` antes do `Lines.SetCurrentLine(...)`. Preserva a ordem e tira repetidos.
    valores = list(dict.fromkeys(str(item) for item in orc_itens if str(item) != ""))
    if not valores:
        return

    linhas_rows = hana_reader.fetch_all(
        *ligar(q.LINHAS_PEDIDO_POR_ORCITM, doc_entry=doc_entry, orc_itens=valores)
    )
    if not linhas_rows:
        logger.warning(
            "Nenhuma linha do pedido %s corresponde a U_INO_ORCITM em %s — U_INO_OP=%s não gravado.",
            doc_entry, valores, num_op,
        )
        return

    # Dedup por LineNum: dois itens de orçamento podem cair na mesma linha do pedido, e
    # repetir o mesmo `LineNum` no corpo confundiria a Service Layer.
    line_nums = list(dict.fromkeys(int(row["LineNum"]) for row in linhas_rows))
    linhas_do_corpo = [{"LineNum": line_num, "U_INO_OP": num_op} for line_num in line_nums]

    # Atualização PARCIAL das linhas: manda só `LineNum` + o campo que muda.
    #
    # ⚠️ NÃO reler o pedido e devolver `DocumentLines` inteiro (era o que esta função fazia
    # até 15/09/2026): isso reenvia todos os campos de todas as linhas, incluindo UDFs de
    # outros add-ons que voltam vazios no GET e que o SAP recusa na escrita — foi assim que
    # quebrou em homologação, com `'' is not a valid value for property 'U_B1SYS_RevenueInd2'`.
    logger.info("  Gravando U_INO_OP=%s em %d linha(s) do pedido %s.", num_op, len(linhas_do_corpo), doc_entry)
    await sl.update_entity("Orders", doc_entry, {"DocumentLines": linhas_do_corpo})


async def _atualiza_doc(
    sl: ServiceLayerClient,
    hana_reader: HanaDirectReader,
    linha: int,
    num_op: int,
    doc_entry: str,
    tipo: str,
) -> None:
    """`ProcessDefault.AtualizaDoc` restricted to the types used in this module (no-op without
    `doc_entry`):

    - `"OP"`: write `U_INO_OP` on the order line matching `linha`.
    - `"Sucesso"`: set `U_INO_ProcessWBC = "Y"` on the order.
    - `"Erro"`: set `U_INO_ProcessWBC = "N"` on the order.
    """
    if not doc_entry:
        return

    if tipo == "OP":
        await marca_op_nas_linhas(sl, hana_reader, [linha], num_op, doc_entry)
    elif tipo == "Sucesso":
        await sl.update_entity("Orders", doc_entry, {"U_INO_ProcessWBC": "Y"})
    elif tipo == "Erro":
        await sl.update_entity("Orders", doc_entry, {"U_INO_ProcessWBC": "N"})


# ---------------------------------------------------------------------------
# 9. processar_pedidos_novos  <- Form2.Button0_ClickAfter, ramo "Pedidos Novos:"
# ---------------------------------------------------------------------------
async def processar_pedidos_novos(
    sl: ServiceLayerClient,
    wbc: WbcSqlServerClient,
    hana_reader: HanaDirectReader,
    orc_nums_selecionados: list[str],
    force: bool = False,
) -> dict:
    """Orchestrate the full "Pedidos Novos" flow (IntegraPedidoWBC.b1f.cs, ~219-763): for each selected
    quote, fill the quote table, decide/apply the order update, ensure the structure items are
    registered, and create the cascade of Production Orders (one per level-1 `(GrpCode, OrcItm)`
    group, with recursive semi-finished items).

    ⚠️ NO ROLLBACK: `atualiza_pedido_tabela(..., "Y")` marks `U_INO_ProcessWBC='Y'` before the first
    OP, so a failure midway leaves the order "processed" with partial OPs. Resume =
    `manutencao-op buscar` -> `cancelar-ops` -> `processar-novos` WITHOUT `--force`.

    Refusals (recorded in `com_erro`): no linked order; order with `U_U_INO_NotaEspelho='Y'`
    (`nota_espelho`, 26/10/2026); quote whose cancelled order still has a live P/R OP (`ops_orfas`,
    20/10/2026). Those two are re-checked at run time and apply even with `force`.

    Reprocess guard (added 15/09/2026, see `_verifica_process_wbc` and `_ops_existentes_para_item` —
    **not in the original C#**, which relied only on the screen's grid filter): by default an order
    with `U_INO_ProcessWBC='Y'` is skipped, and an OP is not recreated for an item that already has a
    non-cancelled OP on the same order. `force=True` skips both checks and therefore DUPLICATES the
    OPs on top of the existing ones — it is not a way to resume.

    Returns `{"processados", "com_erro", "sem_op", "sem_rateio", "pesos_diferentes"}`: `sem_op` = groups
    that produced no OP; `sem_rateio` = OPs that went out
    without the rateio line; `pesos_diferentes` = order-line weights off the WBC tree
    (`_loga_pesos`). The legacy only showed the status bar.
    """
    processados: list[str] = []
    com_erro: list[dict] = []
    # Grupos que terminaram sem gerar OP (ver `_processa_grupo_producao`): o pedido foi
    # integrado, mas parte dele não produziu nada — precisa aparecer no resultado.
    sem_op: list[dict] = []
    # Grupos cuja OP saiu sem a linha de rateio porque o recurso GGF_ não pôde ser criado.
    sem_rateio: list[dict] = []
    # Order lines whose SAP weight is off the WBC tree, with who changed them (01/10/2026).
    pesos_diferentes: list[dict] = []

    for orc_num in orc_nums_selecionados:
        try:
            listaOport = await _busca_orcamentos_wbc(wbc, orc_num)
            id_orc = await _get_id_orcamentos_pedido(hana_reader, orc_num)

            if not id_orc:
                com_erro.append({"orc_num": orc_num, "motivo": "sem pedido vinculado (GetIdOrcamentosPedido vazio)"})
                continue

            doc_entry, card_code, doc_num = str(id_orc["DocEntry"]), str(id_orc["CardCode"]), str(id_orc["DocNum"])

            # Same run-time recheck as the orphan OPs below, `force` included: a mirror order
            # is not confirmed and never gets OPs (26/10/2026).
            if str(id_orc.get("NotaEspelho") or "").strip().upper() == "Y":
                com_erro.append({"orc_num": orc_num, "motivo": mensagem_nota_espelho(doc_num, orc_num),
                                 "nota_espelho": True})
                continue

            # Checked again here, at run time and even with `force`: the plan may be minutes
            # old, and the CLI never builds one (PLANO_TROCA_PN_COM_OP, F3).
            orfas = (await ops_vivas_de_pedidos_cancelados(hana_reader, [orc_num])).get(orc_num, [])
            if orfas:
                com_erro.append(
                    {"orc_num": orc_num, "motivo": mensagem_ops_orfas(doc_num, orc_num, orfas),
                     "ops_orfas": orfas}
                )
                continue

            if not force:
                ja_processado = await _verifica_process_wbc(hana_reader, doc_entry)
                if ja_processado == "Y":
                    com_erro.append(
                        {
                            "orc_num": orc_num,
                            "motivo": (
                                f"pedido {doc_num} já está marcado como processado (U_INO_ProcessWBC=Y) — "
                                "para refazer, rode 'reprocessar-integrados' (cancela as OPs "
                                "planejadas) e depois processe de novo, sem forçar: forçar "
                                "cria OPs duplicadas."
                            ),
                        }
                    )
                    continue

            # The structure is read BEFORE the first write: `atualiza_pedido_tabela` marks the
            # order `ProcessWBC='Y'`, and an empty structure found after that left it
            # "processed" with no OP, gone from "Pedidos novos" (2026-11-03 review).
            logger.info("Pedido %s: lendo a estrutura de produto no WBC...", orc_num)
            estrutura = await busca_estrutura_produto(wbc, [orc_num])
            if not estrutura:
                await preenche_log(sl, orc_num, "Pedido", "Erro", "Integração", "Esse pedido não tem dados no WBC.")
                com_erro.append({"orc_num": orc_num, "motivo": "sem dados no WBC (estrutura vazia)"})
                continue

            # Progresso em `logger.info` (e não `print`): a CLI liga o log no console com
            # `--perfil`/`--verbose`, e a web pode consumir o mesmo log sem alteração.
            # Adicionado em 16/09/2026 porque a execução pode levar minutos sem dar sinal,
            # e era impossível saber em que etapa ela estava.
            logger.info("Pedido %s (DocNum %s): montando a tabela do orçamento...", orc_num, doc_num)
            tab = await preenche_tabela(sl, wbc, hana_reader, orc_num)
            await atualiza_pedido_tabela(
                sl, wbc, hana_reader, listaOport, orc_num, card_code, doc_num, doc_entry, tab, "Y"
            )

            pesos_diferentes.extend(_loga_pesos(hana_reader, orc_num, doc_entry, estrutura))
            logger.info(
                "Pedido %s: estrutura com %d item(ns); garantindo que estejam cadastrados "
                "(uma consulta por item)...", orc_num, len(estrutura),
            )
            await garante_itens_cadastrados(sl, hana_reader, estrutura)

            grupos = list(_agrupa_por_grp_orc_itm(estrutura))
            contexto_pedido: dict = {}
            logger.info("Pedido %s: %d grupo(s) de produção a processar.", orc_num, len(grupos))
            for indice, ((grp_code, orc_itm), grupo) in enumerate(grupos, start=1):
                logger.info(
                    "Pedido %s: grupo %d/%d (GrpCode=%s, OrcItm=%s, %d item(ns))...",
                    orc_num, indice, len(grupos), grp_code, orc_itm, len(grupo),
                )
                motivo = await _processa_grupo_producao(
                    sl, wbc, hana_reader, orc_num, grp_code, orc_itm, grupo, estrutura, force,
                    contexto_pedido,
                )
                if motivo:
                    sem_op.append({"orc_num": orc_num, "grp_code": grp_code, "orc_itm": orc_itm, "motivo": motivo})

            sem_rateio.extend(contexto_pedido.get("sem_rateio", []))
            if contexto_pedido.get("sem_rateio"):
                logger.error(
                    "Pedido %s: %d OP(s) saíram SEM a linha de rateio (recurso GGF_ não criado).",
                    orc_num, len(contexto_pedido["sem_rateio"]),
                )
            faltantes = [f for f in sem_op if f["orc_num"] == orc_num]
            if faltantes:
                logger.error(
                    "Pedido %s: %d de %d grupo(s) NÃO geraram OP. Confira os avisos 'SEM OP' acima.",
                    orc_num, len(faltantes), len(grupos),
                )
                await preenche_log(
                    sl, orc_num, "Pedido", "Erro", "Integração",
                    f"Pedido integrado, mas {len(faltantes)} de {len(grupos)} grupo(s) não geraram OP.",
                )
            else:
                await preenche_log(sl, orc_num, "Pedido", "Sucesso", "Integração", "Pedido integrado com Sucesso.")
            processados.append(orc_num)

        except Exception as exc:  # noqa: BLE001 - não aborta o lote, segue para o próximo
            logger.exception("Erro ao processar pedido novo orc_num=%s", orc_num)
            await preenche_log(sl, orc_num, "Pedido", "Erro", "Integração", str(exc))
            com_erro.append({"orc_num": orc_num, "motivo": str(exc)})

    return {"processados": processados, "com_erro": com_erro, "sem_op": sem_op, "sem_rateio": sem_rateio,
            "pesos_diferentes": pesos_diferentes}


def _agrupa_por_grp_orc_itm(
    estrutura: list[EstruturaPrd],
) -> list[tuple[tuple[int, int], list[EstruturaPrd]]]:
    """Equivalent of `ListaItensEst.Where(x => x.nivel == 1).GroupBy(x => new {x.GrpCode, x.OrcItm})`
    — groups the level-1 items preserving first-occurrence order. Returns
    `[((grp_code, orc_itm), items), ...]`.
    """
    grupos: dict[tuple[int, int], list[EstruturaPrd]] = {}
    for item in estrutura:
        if item.nivel != 1:
            continue
        chave = (item.grp_code, item.orc_itm)
        grupos.setdefault(chave, []).append(item)
    return list(grupos.items())


async def _processa_grupo_producao(
    sl: ServiceLayerClient,
    wbc: WbcSqlServerClient,
    hana_reader: HanaDirectReader,
    orc_num: str,
    grp_code: int,
    orc_itm: int,
    grupo: list[EstruturaPrd],
    estrutura_completa: list[EstruturaPrd],
    force: bool = False,
    contexto: dict | None = None,
) -> str | None:
    """Body of the `foreach (var final in ...GroupBy(...))` in `Button0_ClickAfter` (~2304-2472):
    resolve the production parent item, create the rateio resource, create the OP and trigger the
    semi-finished cascade.

    Returns `None` when the OP was created, or a sentence saying why this group produced no OP
    (`GrpCode` missing from `@INO_GRP_PRODUTOS`; item already had a non-cancelled OP; the Service Layer
    refused the OP; `EntregaMultipla='Y'`, where the legacy creates no parent OP). A refusal does not
    stop the other groups; `ProcessWBC` stays `'Y'` (no rollback, see `processar_pedidos_novos`).

    `force` (added 15/09/2026; same check as `_verifica_process_wbc`, see `_ops_existentes_para_item`):
    with `force=False` the creation is skipped when the item already had an OP on this order before
    this run; `force=True` creates it anyway (duplicating).

    ⚠️ 23/09/2026, order 84426: OPs came out for only 2 of the 4 items and the run ended "without
    error". The three no-OP exits wrote only to `@INO_LOG` (`preenche_log`), with no `logger.*`, so they
    showed neither in the on-screen trace nor in the task result: the operator saw "done" with no way
    to know two groups produced nothing. Each now also emits `logger.*` and returns the reason, which
    `processar_pedidos_novos` accumulates in `sem_op`. Same class of fix as the rateio `except`
    (21/09) — the fifth time in this port that a silent protection/condition only showed after the
    damage.

    `contexto` is shared across the groups OF THE SAME ORDER (created by `processar_pedidos_novos`):
    it keeps how many OPs each item already had before this run and how many groups of each item have
    gone by, and collects the groups whose rateio failed (`sem_rateio`). Standalone calls (tests) may
    omit it.

    Since 30/09/2026 it also keeps the per-order reads (`leituras`): the order's DocEntry/DocNum,
    multiple delivery, the SAP item of a group code, the WBC costs and the max price used to be read
    again for EVERY group, and the DocEntry twice per group. None of them changes during the run — it
    only adds OPs and marks `U_INO_OP` on lines; the order, its lines' prices and
    `@INO_GRP_PRODUTOS` stay as they were.
    """
    if contexto is None:
        contexto = {}
    ops_previas: dict[str, list[int]] = contexto.setdefault("ops_previas", {})
    grupos_vistos: dict[str, int] = contexto.setdefault("grupos_vistos", {})
    sem_rateio: list[dict] = contexto.setdefault("sem_rateio", [])
    leituras: dict = contexto.setdefault("leituras", {})
    orc_num_max = max(item.orc_num for item in grupo)
    if ("doc_entry", orc_num_max) not in leituras:
        leituras[("doc_entry", orc_num_max)] = await _pega_doc_entry_ped(hana_reader, orc_num_max)
    doc_entry_ped = leituras[("doc_entry", orc_num_max)]
    # O DocNum sai do DocEntry já resolvido, e não de uma segunda busca pela Oportunidade:
    # a busca antiga (`pegaDocNumPed`, igual à do C#) podia devolver um pedido CANCELADO da
    # mesma Oportunidade. No 84425 devolveu o 84424 — a checagem de OP existente olhou o
    # pedido errado, e a quantidade da OP e a base do rateio foram lidas dele.
    if ("doc_num", doc_entry_ped) not in leituras:
        doc_num_rows = (
            hana_reader.fetch_all(*ligar(q.DOC_NUM_POR_DOC_ENTRY, doc_entry=int(doc_entry_ped)))
            if str(doc_entry_ped).strip() not in ("", "0") else []
        )
        leituras[("doc_num", doc_entry_ped)] = (
            str(doc_num_rows[0].get("DocNum", "")) if doc_num_rows else ""
        )
    doc_num_ped = leituras[("doc_num", doc_entry_ped)]

    if ("entrega_multipla", doc_entry_ped) not in leituras:
        entrega_multipla_rows = hana_reader.fetch_all(*ligar(q.ENTREGA_MULTIPLA, doc_entry=doc_entry_ped))
        leituras[("entrega_multipla", doc_entry_ped)] = str(_primeiro_valor(entrega_multipla_rows, ""))
    entrega_multipla = leituras[("entrega_multipla", doc_entry_ped)]

    grp_max = max(item.grp_code for item in grupo)
    if ("item_sap", grp_max) not in leituras:
        leituras[("item_sap", grp_max)] = hana_reader.fetch_all(*ligar(q.SELECT_ORC_ITEM_SAP, grp_code=grp_max))
    item_sap_rows = leituras[("item_sap", grp_max)]
    if not item_sap_rows:
        descricao = max(i.prd_desc for i in grupo)
        motivo = (
            f"GrpCode {grp_max} ({descricao}) não está cadastrado em @INO_GRP_PRODUTOS — "
            "nenhuma OP foi criada para este item"
        )
        logger.error("    SEM OP: %s.", motivo)
        await preenche_log(
            sl, orc_num, "Pedido", "Erro", "Integração",
            f"Item correspondente ao item: {descricao} não cadastrado na "
            "tabela de Grupo de Itens, por favor verificar.",
        )
        return motivo

    orc_item_sap = str(list(item_sap_rows[0].values())[0])
    if ("recursos", grupo[0].orc_num) not in leituras:
        leituras[("recursos", grupo[0].orc_num)] = wbc.fetch_all(q.PEGA_VALORES_RECURSOS, (grupo[0].orc_num,))
    valores_recursos = leituras[("recursos", grupo[0].orc_num)]
    custos_wbc = list(valores_recursos[0].values()) if valores_recursos else []

    # `GET_VERSAO_PEDIDO` is no longer read (30/09/2026): the C# read the order version here
    # and never used it (the port kept it as `_ = ...`) — a query per group for nothing.

    quantidade_linha_base = 0.0
    valor_linha = 0.0
    # `fetch_all_values`: os 3 `max(...)` da query vêm sem alias e colidem por nome
    # (mesmo bug de `_busca_header_nova_tabela_quot`, aqui no lado HANA).
    #
    # A quantidade da OP e o valor da linha (base do rateio) vêm da linha DO GRUPO — a que
    # tem `U_INO_ORCITM` igual ao item do orçamento deste grupo (mesma correspondência que
    # `marca_op_nas_linhas` usa para gravar `U_INO_OP`). Ver `BUSCA_MAX_ITEM_LINHA`.
    orc_itens_do_grupo = list(
        dict.fromkeys(str(i.linha) for i in grupo if str(i.linha) != "")
    )
    max_item_linha_rows = (
        hana_reader.fetch_all_values(
            *ligar(
                q.BUSCA_MAX_ITEM_LINHA,
                doc_entry=int(doc_entry_ped), item_code=orc_item_sap, orc_itens=orc_itens_do_grupo,
            )
        )
        if orc_itens_do_grupo and str(doc_entry_ped).strip() not in ("", "0")
        else []
    )
    if max_item_linha_rows:
        vals = max_item_linha_rows[0]
        quantidade_linha_base = _num(vals[1])
        valor_linha = _num(vals[2])
    else:
        # Mesmo desfecho do legado quando a busca não acha nada (a quantidade cai para 1
        # mais abaixo) — mas agora dito na tela, porque com a regra de quantidade por
        # módulos uma OP com 1 é quase sempre errada.
        logger.warning(
            "    Linha do pedido %s para o item %s (U_INO_ORCITM em %s) não encontrada sem OP "
            "— a OP sairá com quantidade 1.",
            doc_num_ped, orc_item_sap, ",".join(orc_itens_do_grupo) or "—",
        )

    quantidade_max_pedido = -1.0
    if ("max_preco", doc_num_ped) not in leituras:
        leituras[("max_preco", doc_num_ped)] = hana_reader.fetch_all(*ligar(q.MAX_PRECO_PEDIDO, doc_num=doc_num_ped))
    max_preco_rows = leituras[("max_preco", doc_num_ped)]
    if max_preco_rows:
        quantidade_max_pedido = _num(list(max_preco_rows[0].values())[0], padrao=-1.0) or -1.0

    # The C# resolved the order's DocEntry a second time here; only reads happened since the
    # first one, and a DocEntry does not change — the same value is reused.
    doc_entry_final = doc_entry_ped

    recurso = ""
    if custos_wbc:
        try:
            nome_recurso = (
                f"GGF_{orc_num_max}{orc_item_sap}{_digitos_do_valor_como_no_legado(valor_linha)}"
            )
            recurso = await cria_recurso_rateio(
                sl, hana_reader, custos_wbc, nome_recurso, quantidade_linha_base,
                quantidade_max_pedido, valor_linha, orc_num_max,
            )
        except Exception as exc:  # noqa: BLE001
            # O C# só registrava no INO_LOG. Mantido — abortar o pedido por causa do
            # rateio seria pior. Mas `logger.exception` foi acrescentado: o TypeError de
            # 21/09 ficou invisível no console e a OP saiu sem a linha de rateio sem que
            # ninguém percebesse até a comparação com o legado.
            logger.exception("Erro ao preencher o recurso de rateio %s", nome_recurso)
            await preenche_log(sl, orc_num, "Pedido", "Erro", "Integração", f"Erro ao preencher recurso: {exc}")
            # A OP sai assim mesmo, mas SEM a linha de rateio (custo de transporte/
            # embalagem/montagem). Precisa constar do resultado, não só do @INO_LOG.
            sem_rateio.append({
                "orc_num": orc_num, "grp_code": grp_code, "orc_itm": orc_itm,
                "item": orc_item_sap, "recurso": nome_recurso, "motivo": str(exc),
            })

    if quantidade_linha_base in (0, -1):
        quantidade_linha_base = 1

    # Conta por item: o N-ésimo grupo que resolve para o item X só é "reprocessamento"
    # se X já tinha pelo menos N OPs ANTES desta execução. A foto das OPs prévias é tirada
    # no primeiro grupo de cada item — antes, portanto, de esta execução criar qualquer OP
    # para ele. Assim: rodar de novo um pedido completo pula tudo; um pedido que parou no
    # meio completa só o que faltou; e grupos distintos do mesmo item deixam de se
    # bloquear entre si (pedido 84426).
    ordem_do_grupo = grupos_vistos.get(orc_item_sap, 0) + 1
    grupos_vistos[orc_item_sap] = ordem_do_grupo
    if orc_item_sap not in ops_previas:
        ops_previas[orc_item_sap] = await _ops_existentes_para_item(hana_reader, doc_num_ped, orc_item_sap)
    previas = ops_previas[orc_item_sap]

    op_existente = None if force else _op_previa_do_grupo(previas, ordem_do_grupo)
    if op_existente is not None:
        motivo = (
            f"item {orc_item_sap} já tinha a OP não cancelada DocEntry={op_existente} no "
            f"pedido {doc_num_ped} antes desta execução — nenhuma OP nova foi criada "
            "(forçar criaria uma OP duplicada)"
        )
        logger.warning("    SEM OP: %s.", motivo)
        await preenche_log(
            sl, orc_num, "Pedido", "Aviso", "Integração",
            f"já existe uma OP não cancelada (DocEntry={op_existente}) para o pedido "
            f"{doc_num_ped}, item {orc_item_sap} — nenhuma OP nova criada.",
        )
        return motivo

    linha_documento = -1
    try:
        _, novo_doc_entry = await cria_ordem_producao(
            sl, hana_reader, grupo, orc_item_sap, linha_documento, quantidade_linha_base, recurso,
            entrega_multipla,
        )
    except ServiceLayerAuthError:
        raise
    except ServiceLayerError as exc:
        # The SL refused this OP (already in `@INO_LOG` by `cria_ordem_producao`). The other
        # groups are independent OPs: aborting the order here left them without OP and out of
        # the result (2026-11-03 review). Transport errors still propagate: the OP may exist.
        motivo = f"a Service Layer recusou a OP do item {orc_item_sap}: {exc}"
        logger.error("    SEM OP: %s.", motivo)
        return motivo

    # Acumula os itens a marcar em vez de chamar a Service Layer a cada um: uma chamada
    # por item eram 6 PATCHes / 4,1s no perfil do orçamento 00125192 (ver
    # `marca_op_nas_linhas`). A cascata de semiacabados segue item a item — ela cria
    # documentos diferentes, não dá para agrupar.
    orc_itens_do_grupo: list = []
    for item in grupo:
        await checa_semi_acabado(
            sl, wbc, hana_reader, item.prd_code, item.linha, item.quantidade, recurso,
            quantidade_linha_base, item.orc_num, item.quantidade, item.id_integracao_orc,
            item.linha_orc, estrutura_completa,
        )
        linha_documento = item.linha
        orc_itens_do_grupo.append(item.linha)

    await marca_op_nas_linhas(sl, hana_reader, orc_itens_do_grupo, novo_doc_entry, doc_entry_final)
    await _atualiza_doc(sl, hana_reader, linha_documento, novo_doc_entry, doc_entry_final, "Sucesso")

    if not novo_doc_entry:
        # `EntregaMultipla='Y'`: like the legacy, no parent OP is created (only the
        # semi-finished cascade). Reported, so the screen does not say "concluído".
        motivo = (
            f"pedido com entrega múltipla: a OP do item {orc_item_sap} não é criada "
            "(mesmo comportamento do addon antigo)"
        )
        logger.warning("    SEM OP: %s.", motivo)
        return motivo

    return None


# ---------------------------------------------------------------------------
# 10. reprocessar_pedidos_integrados <- Form2.Button0_ClickAfter, ramo "Pedidos Integrados:"
# ---------------------------------------------------------------------------
async def reprocessar_pedidos_integrados(
    sl: ServiceLayerClient,
    wbc: WbcSqlServerClient,
    hana_reader: HanaDirectReader,
    orc_nums_selecionados: list[str],
) -> dict:
    """Mode "Pedidos Integrados" (IntegraPedidoWBC.b1f.cs, ~764-883): for already-processed orders,
    rewrite the quote table, update the order with `U_INO_ProcessWBC='N'` (`atualiza_pedido_tabela`),
    re-link order and opportunity (`AddPedidoOportunidade`) and cancel every PLANNED OP of the order
    (`CancelaOP`, query `BUSCA_OPS`: `Status = 'P'`).

    It does **not** recreate the OPs — run `processar-novos` (without `--force`) afterwards. Released
    or closed OPs are not touched. Returns `{"atualizados", "com_erro"}`.

    Note from the original C#: the body runs once per selected grid row, not once per `listaOport`
    item — a commented-out `foreach (var item in listaOport)` (dead code) wrapped the block. Replicated
    here (one call per selected opportunity, not per quote item).
    """
    atualizados: list[str] = []
    com_erro: list[dict] = []

    for orc_num in orc_nums_selecionados:
        try:
            # Reprocessar era a única etapa muda do módulo: quatro gravações no SAP sem
            # uma linha de log entre elas. Na web isso vira minutos de tela parada, e em
            # caso de falha ninguém sabe em qual das quatro parou.
            logger.info("Orçamento %s: lendo os itens no WBC...", orc_num)
            listaOport = await _busca_orcamentos_wbc(wbc, orc_num)
            logger.info("  %d item(ns) de orçamento no WBC.", len(listaOport))

            id_orc = await _get_id_orcamentos_pedido(hana_reader, orc_num)
            if not id_orc:
                logger.error("  Nenhum pedido vinculado ao orçamento %s (U_INO_COTWBC).", orc_num)
                com_erro.append({"orc_num": orc_num, "motivo": "sem pedido vinculado (GetIdOrcamentosPedido vazio)"})
                continue

            doc_entry, card_code, doc_num = str(id_orc["DocEntry"]), str(id_orc["CardCode"]), str(id_orc["DocNum"])
            logger.info("  Pedido %s (DocEntry %s), cliente %s.", doc_num, doc_entry, card_code)

            logger.info("  Montando a tabela do orçamento (UDO)...")
            tab = await preenche_tabela(sl, wbc, hana_reader, orc_num)

            logger.info("  Atualizando o pedido %s...", doc_num)
            await atualiza_pedido_tabela(
                sl, wbc, hana_reader, listaOport, orc_num, card_code, doc_num, doc_entry, tab, "N"
            )

            status_wbc = str(listaOport[0].sit_code) if listaOport else ""
            # A chave da Oportunidade, não o nº do orçamento: é a única chamada do fluxo
            # que usa a coluna "Num Oportunidade" da grade original (ver
            # `_oppr_id_do_orcamento`). Passar o orçamento aqui fazia o `GetByKey` de
            # SalesOpportunities procurar uma chave que não existe.
            oppr_id = await _oppr_id_do_orcamento(hana_reader, orc_num)
            if not oppr_id:
                logger.error("  Nenhuma Oportunidade com U_ORCNUM_MASC='%s' em OOPR.", orc_num)
                com_erro.append({
                    "orc_num": orc_num,
                    "motivo": f"nenhuma Oportunidade com U_ORCNUM_MASC='{orc_num}' (OOPR)",
                })
                continue
            logger.info(
                "  Vinculando o pedido %s à Oportunidade %s (status WBC %s)...",
                doc_num, oppr_id, status_wbc,
            )
            resultado = await _add_pedido_oportunidade(sl, oppr_id, status_wbc, int(doc_num), int(doc_entry))

            if resultado == 0:
                ops_planejadas = hana_reader.fetch_all(*ligar(q.BUSCA_OPS, origin_num=doc_num))
                logger.info(
                    "  %d OP(s) planejada(s) do pedido %s a cancelar antes de recriar.",
                    len(ops_planejadas), doc_num,
                )
                for indice, op_row in enumerate(ops_planejadas, start=1):
                    doc_entry_op = int(op_row["DocEntry"])
                    await _cancela_op(sl, doc_entry_op)
                    logger.info(
                        "    OP %s cancelada (%d/%d): %s — %s (%s un).",
                        op_row.get("DocNum", doc_entry_op), indice, len(ops_planejadas),
                        op_row.get("ItemCode", ""), _resumo(op_row.get("ItemName", "")),
                        _quantidade(op_row.get("PlannedQty")),
                    )
                # O log de sucesso do reprocessamento usa a chave da Oportunidade — como no
                # C# (linha 869), que ali troca de coluna junto com o AddPedidoOportunidade.
                await preenche_log(sl, oppr_id, "Pedido", "Sucesso", "Integração", "Pedido reintegrado com sucesso.")
                logger.info("  Pedido %s reintegrado.", doc_num)
                atualizados.append(orc_num)
            else:
                logger.error("  AddPedidoOportunidade devolveu status %s no pedido %s.", resultado, doc_num)
                com_erro.append({"orc_num": orc_num, "motivo": "AddPedidoOportunidade retornou status != 0"})

        except Exception as exc:  # noqa: BLE001
            logger.exception("Erro ao reprocessar pedido integrado orc_num=%s", orc_num)
            await preenche_log(sl, orc_num, "Pedido", "Erro", "Integração", str(exc))
            com_erro.append({"orc_num": orc_num, "motivo": str(exc)})

    return {"atualizados": atualizados, "com_erro": com_erro}


async def _add_pedido_oportunidade(
    sl: ServiceLayerClient, oppr_id: str, status_wbc: str, doc_num_pedido: int, doc_entry: int
) -> int:
    """`ProcessDefault.AddPedidoOportunidade` — link the order as a follow-up document of the
    opportunity and mark it "Won" (`sos_Sold`).

    Sequence: set `U_INO_StatusWBC` + `sos_Open`; re-read the opportunity and APPEND a
    `SalesOpportunitiesLines` entry (`DocumentType='bodt_Order'`, `DocumentNumber=DocEntry` — not the
    DocNum, despite the field name —, `PercentageRate=100`, `MaxLocalTotal=DocTotal`); then `sos_Sold`.
    Returns 0 (the status is a constant); errors are logged to `INO_LOG` and re-raised.

    The collection name `SalesOpportunitiesLines` is the real one (confirmed in
    docs/wbc/APRENDIZADOS.md). Against the Service Layer a field name is found by reading a real record
    (`diag entidade SalesOpportunities --campos`), never by analogy (section 7.29 of the guide).

    ⚠️ This re-reads the lines and sends the whole array back — the same pattern that broke
    `marca_op_nas_linhas` (15/09) and `_update_tab_pedido_cong` (22/09) with `'' is not a valid value
    for property ...`. Here a line must be APPENDED, not just fields changed on existing lines, so a
    partial body is not possible; if the error reappears, build each existing line with only its line
    key and the fields that matter.
    """
    try:
        pedido = await sl.get_by_key("Orders", doc_entry)
        doc_total = pedido.get("DocTotal", 0)

        await sl.update_entity("SalesOpportunities", oppr_id, {"U_INO_StatusWBC": status_wbc, "Status": "sos_Open"})

        oport = await sl.get_by_key("SalesOpportunities", oppr_id)
        linhas = oport.get("SalesOpportunitiesLines", [])
        logger.info(
            "    Oportunidade %s tem %d linha(s); acrescentando o pedido %s (total %s).",
            oppr_id, len(linhas), doc_num_pedido, doc_total,
        )
        linhas.append(
            {
                "DocumentType": "bodt_Order",
                "DocumentNumber": doc_entry,
                "PercentageRate": 100,
                "MaxLocalTotal": doc_total,
            }
        )
        status = 0
        await sl.update_entity("SalesOpportunities", oppr_id, {"SalesOpportunitiesLines": linhas})

        if status == 0:
            await sl.update_entity("SalesOpportunities", oppr_id, {"Status": "sos_Sold"})
            logger.info("    Oportunidade %s marcada como Ganha.", oppr_id)
        return status
    except Exception as exc:  # noqa: BLE001
        await preenche_log(sl, oppr_id, "Pedido", "Erro", "Integração", str(exc))
        raise


async def _cancela_op(sl: ServiceLayerClient, doc_entry: int) -> None:
    """`ProcessDefault.CancelaOP` — no try/catch in the original C# (the exception propagates);
    preserved here: the error is not swallowed in this function.
    """
    await sl.update_entity("ProductionOrders", doc_entry, {"ProductionOrderStatus": "boposCancelled"})


# ---------------------------------------------------------------------------
# Auditoria: comparar as OPs geradas pelo Python com as geradas pelo addon legado
# (adicionado em 15/09/2026 a pedido do Anderson — sem equivalente no C#, é só leitura)
# ---------------------------------------------------------------------------
def _prefixo_schema(schema: str | None) -> str:
    """`"SBOALTAMIRAPROD".` when a schema is given, an empty string to use the connection's current
    schema (the one of `SL_COMPANY_DB`; since 28/09/2026 the package reads its schema from the write
    company). Allows comparing production vs homologation on the same HANA connection, provided the
    user can read both schemas.
    """
    return f'"{schema}".' if schema else ""


def _resumo_op(row: dict) -> dict:
    """Extract from an `OWOR` row only the fields relevant to the comparison, defensively (the query is
    `SELECT *`; column names vary between B1 versions).
    """
    return {
        "doc_entry": row.get("DocEntry"),
        "doc_num": row.get("DocNum"),
        "item_code": str(row.get("ItemCode") or ""),
        "planejada": float(row.get("PlannedQty") or 0),
        "concluida": float(row.get("CmpltQty") or 0),
        "status": str(row.get("Status") or ""),
        "tipo": str(row.get("Type") or ""),
        "linha_ref": str(row.get("U_INO_LinhaRef") or ""),
        "deposito": str(row.get("Warehouse") or ""),
        "criada_em": str(row.get("CreateDate") or ""),
        "origem_abs": row.get("OriginAbs"),
        "origem_num": row.get("OriginNum"),
    }


def _resumo_linha_op(row: dict) -> dict:
    """Same for `WOR1` (OP components)."""
    return {
        "item_code": str(row.get("ItemCode") or ""),
        "planejada": float(row.get("PlannedQty") or 0),
        "base": float(row.get("BaseQty") or 0),
        "tipo": str(row.get("ItemType") or ""),
        "deposito": str(row.get("Warehouse") or row.get("wareHouse") or ""),
        "peso": str(row.get("U_INO_PESO") or ""),
        "desc": str(row.get("U_INO_DESC") or ""),
    }


def _chave_ordenacao_op(op: dict) -> tuple:
    """Sort OPs by **content**, not by `DocEntry`/`DocNum`.

    OP numbering differs per environment (production and homologation have their own sequences), so
    sorting by number would misalign the two tables and make the manual check a back-and-forth. Sorting
    by the same business attributes puts the same OP (when it exists on both sides) at the same position
    in both tables.

    Key: produced item -> planned quantity -> reference line -> OP number. Quantity comes before the
    line because one item can have several OPs on the same order (e.g. `PAR000PADRA000000000`, with
    different quantities), and quantity is what tells them apart; `U_INO_LinhaRef` may diverge between
    environments, so it is not the primary key.
    """
    return (op["item_code"], op["planejada"], op["linha_ref"], str(op["doc_num"]))


def _le_ops_do_lado(
    hana_reader: HanaDirectReader, orc_num: str, schema: str | None, com_linhas: bool
) -> dict:
    """Read, from one schema, the quote's order and all OPs linked to it (cancelled ones included —
    Anderson noted some were cancelled by hand in production, and they still count as "the legacy addon
    created this OP"). Returns `{"schema", "pedido", "ops"}`, OPs sorted by `_chave_ordenacao_op` (each
    with its `linhas` when `com_linhas`).
    """
    prefixo = _prefixo_schema(schema)
    pedidos = hana_reader.fetch_all(
        *ligar(q.PEDIDO_POR_ORCAMENTO, schema=prefixo, orc_num=orc_num)
    )
    if not pedidos:
        return {"schema": schema, "pedido": None, "ops": []}

    pedido = pedidos[0]
    doc_entry, doc_num = pedido["DocEntry"], pedido["DocNum"]

    ops = [
        _resumo_op(row)
        for row in hana_reader.fetch_all(
            *ligar(q.OPS_DO_PEDIDO, schema=prefixo, doc_entry=doc_entry, doc_num=doc_num)
        )
    ]

    if com_linhas:
        for op in ops:
            op["linhas"] = [
                _resumo_linha_op(row)
                for row in hana_reader.fetch_all(
                    *ligar(q.LINHAS_DA_OP, schema=prefixo, doc_entry=op["doc_entry"])
                )
            ]

    ops.sort(key=_chave_ordenacao_op)
    return {"schema": schema, "pedido": dict(pedido), "ops": ops}


async def comparar_ops(
    hana_reader: HanaDirectReader,
    wbc: WbcSqlServerClient,
    orc_num: str,
    schema_legado: str,
    schema_novo: str | None = None,
    desde: str | None = None,
    incluir_canceladas: bool = False,
) -> dict:
    """Compare a quote's OPs between the schema where the **legacy addon** runs and the schema where
    **this port** runs, and diagnose the missing ones.

    The two environments are distinct, permanent HANA schemas (confirmed by Anderson on 16/09/2026):
    `SBOALTAMIRAPROD` is where the legacy addon runs and `SBOALTAMIRAHOMOLOG` was where this port ran;
    since 28/09/2026 the package writes where `SL_COMPANY_DB` points, and `schema_novo=None` means that
    current schema. The comparison is always schema to schema — there is no split by creation date (an
    earlier version tried that and only confused, since the two sides never share a schema).

    `desde` (optional, `AAAA-MM-DD`) filters the NEW side to OPs created from that date on. It isolates
    the last run when homologation accumulated OPs from earlier runs of the port itself — easy to
    happen when reprocessing with `--force`.

    `incluir_canceladas=False` (default, Anderson's request of 16/09/2026): cancelled OPs
    (`OWOR."Status" = 'C'`) stay OUT of the comparison on both sides — in production several were
    cancelled by hand afterwards, which is operational noise, not an integration result. The discarded
    ones do not vanish from the report: they come back in `canceladas_legado` / `canceladas_novo`, and
    items that only have a cancelled OP in the legacy come back in `itens_so_cancelados_legado`. That
    matters because, once discarded, an item whose production OP was cancelled would show as "only in
    the new (extra)", looking like the port created something undue when in fact both created it and a
    human cancelled one later.

    ⚠️ Read-only — never writes. Requires the HANA user to have SELECT on both schemas. Added
    15/09/2026, no C# equivalent.
    """
    lado_legado = _le_ops_do_lado(hana_reader, orc_num, schema_legado, com_linhas=True)
    lado_novo = _le_ops_do_lado(hana_reader, orc_num, schema_novo, com_linhas=True)

    if desde:
        lado_novo["ops"] = [op for op in lado_novo["ops"] if str(op["criada_em"])[:10] >= desde]

    canceladas_legado = [op for op in lado_legado["ops"] if op["status"] == "C"]
    canceladas_novo = [op for op in lado_novo["ops"] if op["status"] == "C"]

    if not incluir_canceladas:
        lado_legado["ops"] = [op for op in lado_legado["ops"] if op["status"] != "C"]
        lado_novo["ops"] = [op for op in lado_novo["ops"] if op["status"] != "C"]

    # Itens que, no legado, SÓ têm OP cancelada — se aparecerem "a mais" do lado novo, não é
    # invenção do porte: o legado criou também, e a OP foi cancelada depois.
    itens_vivos_legado = {op["item_code"] for op in lado_legado["ops"] if op["status"] != "C"}
    itens_so_cancelados_legado = {op["item_code"] for op in canceladas_legado} - itens_vivos_legado

    nome_novo = schema_novo or "schema corrente (SL_COMPANY_DB)"
    criterio = f"'{schema_legado}' (legado) vs '{nome_novo}' (porte Python)"
    if desde:
        criterio += f", considerando no lado novo só OPs criadas a partir de {desde}"
    criterio += ", incluindo OPs canceladas" if incluir_canceladas else ", descartando OPs canceladas"

    diagnosticos = await _diagnostica_ops_faltantes(hana_reader, wbc, orc_num, lado_legado, lado_novo)

    return {
        "orc_num": orc_num,
        "criterio": criterio,
        "legado": lado_legado,
        "novo": lado_novo,
        "diagnosticos": diagnosticos,
        "canceladas_legado": canceladas_legado,
        "canceladas_novo": canceladas_novo,
        "itens_so_cancelados_legado": sorted(itens_so_cancelados_legado),
        "incluir_canceladas": incluir_canceladas,
    }


async def _diagnostica_ops_faltantes(
    hana_reader: HanaDirectReader,
    wbc: WbcSqlServerClient,
    orc_num: str,
    lado_legado: dict,
    lado_novo: dict,
) -> list[dict]:
    """For each item that has an OP in the legacy but not in the new side, try to point out the
    reason by checking the known causes (all documented in migration_guide.md):

    1. `U_INO_EntregaMultipla = 'Y'` on the order -> `CriaOP` does not create the OP (a quirk of the
       original C# that looks like an inverted condition, question 9 of section 9 of the guide).
    2. WBC group with no mapping in `@INO_GRP_PRODUTOS` -> the whole group is skipped with the log
       "Item correspondente ... não cadastrado".
    3. Non-cancelled OP already existing + run without `--force` -> skipped by the reprocess check
       (section 7.6 of the guide).

    ⚠️ Every check looks at the **NEW side** (the schema where the port runs), because that is what the
    port saw when deciding whether to create the OP. Looking at the legacy side would give a wrong
    diagnosis: the reprocess check queries the OPs of the port's own schema, and the order that matters
    for `U_INO_EntregaMultipla` is the one there.
    """
    itens_legado = {op["item_code"] for op in lado_legado["ops"]}
    itens_novo = {op["item_code"] for op in lado_novo["ops"]}
    faltantes = sorted(itens_legado - itens_novo)
    if not faltantes:
        return []

    pedido_novo = lado_novo.get("pedido") or {}
    entrega_multipla = str(pedido_novo.get("EntregaMultipla") or "")
    doc_num_novo = str(pedido_novo.get("DocNum") or "")
    schema_novo = _prefixo_schema(lado_novo.get("schema"))

    # Grupos do orçamento no WBC e seus mapeamentos em @INO_GRP_PRODUTOS — lidos no schema
    # do porte, que é de onde ele lê esse mapeamento em `_processa_grupo_producao`.
    grupos_sem_mapeamento: set[int] = set()
    item_sap_por_grupo: dict[str, int] = {}
    try:
        estrutura = await busca_estrutura_produto(wbc, [orc_num])
        for grp_code in {item.grp_code for item in estrutura}:
            rows = hana_reader.fetch_all(
                *ligar(q.ITEM_SAP_DO_GRUPO, schema=schema_novo, grp_code=grp_code)
            )
            if not rows:
                grupos_sem_mapeamento.add(grp_code)
            else:
                item_sap_por_grupo[str(_primeiro_valor(rows))] = grp_code
    except Exception as exc:  # noqa: BLE001 - diagnóstico nunca deve derrubar o relatório
        logger.warning("Não foi possível checar @INO_GRP_PRODUTOS/estrutura do WBC: %s", exc)

    diagnosticos = []
    for item_code in faltantes:
        motivos = []
        if entrega_multipla == "Y":
            motivos.append(
                "pedido está com U_INO_EntregaMultipla='Y' — `CriaOP` NÃO cria a OP nesse caso "
                "(quirk do C# original, preservado; ver pergunta 9 da seção 9 do guia)"
            )
        grp = item_sap_por_grupo.get(item_code)
        if grp is not None and grp in grupos_sem_mapeamento:
            motivos.append(f"grupo {grp} sem linha em @INO_GRP_PRODUTOS")
        elif grp is None and grupos_sem_mapeamento:
            motivos.append(
                f"pode ser um dos grupos sem mapeamento em @INO_GRP_PRODUTOS: "
                f"{sorted(grupos_sem_mapeamento)}"
            )
        # Pergunta à realidade em vez de inferir: existe, NO SCHEMA DO PORTE, uma OP não
        # cancelada para este pedido+item? É exatamente a consulta que
        # `_ops_existentes_para_item` faz durante a execução. Se existe, a OP "faltante" na
        # verdade já estava lá (de uma execução anterior, fora da janela do `--desde`) e a
        # verificação de reprocessamento pulou a criação.
        if doc_num_novo:
            try:
                rows = hana_reader.fetch_all(
                    *ligar(q.CHECA_OP_EXISTENTE_PEDIDO, doc_num=doc_num_novo, item_code=item_code)
                )
                if rows:
                    motivos.append(
                        f"já existe OP não cancelada para este item no schema do porte "
                        f"(DocEntry={_primeiro_valor(rows)}) — provavelmente de uma execução "
                        "anterior; sem `--force` a verificação de reprocessamento pula a criação "
                        "(seção 7.6 do guia). Se estiver usando `--desde`, essa OP pode estar "
                        "fora da janela e por isso não aparece na tabela acima."
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Não foi possível checar OP existente para %s: %s", item_code, exc)
        if not motivos:
            motivos.append(
                "motivo não identificado automaticamente — checar o INO_LOG do orçamento e se a "
                "execução abortou no meio (uma exceção em qualquer grupo interrompe os grupos "
                "seguintes e a cascata de semiacabados)"
            )
        diagnosticos.append({"item_code": item_code, "motivos": motivos})

    return diagnosticos


# ---------------------------------------------------------------------------
# Limpeza: cancelar em lote as OPs de um pedido de vendas
# (adicionado em 16/09/2026 a pedido do Anderson — sem equivalente no C#, que só cancelava
# OP a OP dentro do reprocessamento, e sem verificação prévia de status)
# ---------------------------------------------------------------------------
# Status de Ordem de Produção em `OWOR."Status"` (SAP B1).
STATUS_OP = {"P": "Planejada", "R": "Liberada", "L": "Encerrada", "C": "Cancelada"}

# Só estes podem existir para o cancelamento em lote começar.
#
# A regra dada pelo Anderson foi "todas em planejadas ou canceladas; nenhuma liberada". É
# uma LISTA BRANCA, então `L` (Encerrada) também bloqueia — e é bom que bloqueie: uma OP
# encerrada já teve apontamento de produção, baixa de insumo e entrada de produto acabado.
# Cancelá-la seria bem pior do que cancelar uma liberada.
STATUS_QUE_PERMITEM_CANCELAR = {"P", "C"}


async def levanta_ops_para_cancelamento(
    hana_reader: HanaDirectReader, doc_num: str | None = None, orc_num: str | None = None
) -> dict:
    """List an order's OPs and say whether the batch cancellation may start.

    **Writes nothing** — it is the checking step, deliberately separate from the execution so the CLI
    can show what will happen, stop when something blocks, and only then ask for confirmation.
    Raises `ValueError` when neither `doc_num` nor `orc_num` is given.

    Returns `{pedido, ops, bloqueantes, a_cancelar, ja_canceladas}`:
    - `bloqueantes`: OPs in a status outside `STATUS_QUE_PERMITEM_CANCELAR`. If there is any, **nothing
      should be cancelled** — not even the ones that would be allowed.
    - `a_cancelar`: the planned ones (`P`), which is what will actually change.
    - `ja_canceladas`: the `C` ones, counted for the report and ignored in the execution.
    """
    if doc_num:
        pedidos = hana_reader.fetch_all(*ligar(q.PEDIDO_POR_DOCNUM, doc_num=doc_num))
    elif orc_num:
        pedidos = hana_reader.fetch_all(*ligar(q.PEDIDO_POR_ORCAMENTO, schema="", orc_num=orc_num))
    else:
        raise ValueError("Informe o pedido (doc_num) ou o orçamento (orc_num).")

    if not pedidos:
        return {"pedido": None, "ops": [], "bloqueantes": [], "a_cancelar": [], "ja_canceladas": []}

    pedido = dict(pedidos[0])
    ops = [
        _resumo_op(row)
        for row in hana_reader.fetch_all(
            *ligar(q.OPS_DO_PEDIDO, schema="", doc_entry=pedido["DocEntry"], doc_num=pedido["DocNum"])
        )
    ]
    ops.sort(key=_chave_ordenacao_op)

    return {
        "pedido": pedido,
        "ops": ops,
        "bloqueantes": [op for op in ops if op["status"] not in STATUS_QUE_PERMITEM_CANCELAR],
        "a_cancelar": [op for op in ops if op["status"] == "P"],
        "ja_canceladas": [op for op in ops if op["status"] == "C"],
    }


async def cancela_ops_do_pedido(sl: ServiceLayerClient, ops_a_cancelar: list[dict]) -> dict:
    """Cancel the given OPs one by one and report what happened to each.

    ⚠️ Call **only** after `levanta_ops_para_cancelamento` confirmed there are no blockers — this
    function does not repeat the check, it executes what was decided.

    An error on one OP does not stop the others: cancelling 8 of 10 and saying exactly which failed is
    more useful than stopping at the third and leaving the order in an unknown state. The result has
    `canceladas` and `com_erro` separately.
    """
    canceladas: list[dict] = []
    com_erro: list[dict] = []

    for op in ops_a_cancelar:
        try:
            await _cancela_op(sl, int(op["doc_entry"]))
            canceladas.append(op)
            logger.info("  OP %s (item %s) cancelada.", op["doc_num"], op["item_code"])
        except Exception as exc:  # noqa: BLE001 - segue para as demais, mas registra
            com_erro.append({**op, "motivo": str(exc)})
            logger.error("  Falha ao cancelar a OP %s (item %s): %s", op["doc_num"], op["item_code"], exc)

    return {"canceladas": canceladas, "com_erro": com_erro}


async def cancela_ops_conferidas(
    sl: ServiceLayerClient,
    hana_reader: HanaDirectReader,
    doc_num: str | int,
    conferidas: list[dict],
    *,
    deve_parar: Callable[[], bool] | None = None,
) -> dict:
    """The screen's "Cancelar OPs": re-read, cancel what is still as checked, then clean.

    01/10/2026 review, two gaps against the CLI:

    - The plan was checked up to 10 min earlier and the execution trusted it: an OP released
      meanwhile (PCP, the Liberar screen, the API, an Encerrar running in the OTHER module)
      was cancelled anyway, against ``STATUS_QUE_PERMITEM_CANCELAR``. Now the order's OPs are
      read again: any blocker refuses everything, as at checking time, and only the checked
      OPs that are STILL planned are cancelled.
    - The screen never ran ``limpa_vinculos_do_pedido``: the order stayed
      ``ProcessWBC='Y'`` with lines pointing at cancelled OPs, so the documented recovery
      ("cancelar-ops → processar-novos without --force") did not work from the screen.
      Same rule as the CLI: clean only when every OP of the order ended cancelled.

    ``deve_parar`` is checked before each OP, never inside one (the screen's "Interromper").
    Returns ``canceladas``, ``com_erro``, ``status_mudou`` (checked, no longer planned — not
    touched), ``nao_conferidas`` (planned now but not in the checked plan — not touched),
    ``nao_iniciadas`` (left out by an interruption) and ``limpeza``.
    """
    resultado: dict = {
        "canceladas": [], "com_erro": [], "status_mudou": [], "nao_conferidas": [],
        "nao_iniciadas": [], "limpeza": None,
    }
    atual = await levanta_ops_para_cancelamento(hana_reader, doc_num=str(doc_num))
    pedido = atual["pedido"]
    if not pedido:
        resultado["com_erro"].append({"doc_num": doc_num, "item_code": "—",
                                      "motivo": "Pedido não encontrado na releitura."})
        resultado["limpeza"] = "pulada"
        return resultado
    if atual["bloqueantes"]:
        resultado["com_erro"] = [
            {**op, "motivo": f"Status mudou para {op['status']} — "
                             f"{STATUS_OP.get(op['status'], op['status'])} depois da "
                             "conferência; nenhuma OP foi cancelada."}
            for op in atual["bloqueantes"]
        ]
        resultado["limpeza"] = "pulada"
        return resultado

    planejadas_agora = {int(op["doc_entry"]) for op in atual["a_cancelar"]}
    conferidas_ids = {int(op["doc_entry"]) for op in conferidas}
    a_cancelar = [op for op in conferidas if int(op["doc_entry"]) in planejadas_agora]
    resultado["status_mudou"] = [op for op in conferidas if int(op["doc_entry"]) not in planejadas_agora]
    resultado["nao_conferidas"] = [
        op for op in atual["a_cancelar"] if int(op["doc_entry"]) not in conferidas_ids
    ]

    for posicao, op in enumerate(a_cancelar):
        if deve_parar is not None and deve_parar():
            resultado["nao_iniciadas"] = a_cancelar[posicao:]
            break
        parcial = await cancela_ops_do_pedido(sl, [op])
        resultado["canceladas"].extend(parcial["canceladas"])
        resultado["com_erro"].extend(parcial["com_erro"])

    # Cleaning with an OP still planned would leave the order saying "never processed" while
    # a live OP points at it — worse than before (see `limpa_vinculos_do_pedido`).
    if resultado["com_erro"] or resultado["nao_iniciadas"] or resultado["nao_conferidas"]:
        resultado["limpeza"] = "pulada"
    else:
        resultado["limpeza"] = await limpa_vinculos_do_pedido(sl, hana_reader, pedido["DocEntry"])
    return resultado


async def limpa_vinculos_do_pedido(
    sl: ServiceLayerClient, hana_reader: HanaDirectReader, doc_entry: str | int
) -> dict:
    """Return the order to the "not processed" state, undoing what the integration marked.

    Two things, in **a single** Service Layer call (applying the lesson of section 7.14 — group
    updates of the same entity):
    - header: `U_INO_ProcessWBC = 'N'`;
    - lines: `U_INO_OP = 0` on those with a linked OP.

    **The values were not chosen for convenience, they are the system's own**: `'N'` is what
    `_atualiza_doc(..., "Erro")` already writes and what `BUSCA_PEDIDOS_PARA_INTEGRAR` filters on to
    list an order as pending; and `0` is how the legacy addon zeroed that field
    (`DocCot.Lines.SetCurrentLine(i); ...U_INO_OP = 0`, ported in `_update_tab_pedido_cong`) and how
    the rest of the code reads "no OP" (`IFNULL(U_INO_OP,0) = 0`).

    Practical effect: the order shows up again in `pedidos-wbc buscar` and can be reprocessed without
    `--force`.

    ⚠️ Call only when **all** the order's OPs are cancelled. If any stays planned (e.g. a cancellation
    failed), cleaning the links would leave the order saying "never processed" while live OPs point at
    it — worse than before. The caller guarantees that condition. Returns `{"linhas_limpas": n}`.
    """
    linhas = hana_reader.fetch_all(*ligar(q.LINHAS_COM_OP, doc_entry=doc_entry))

    corpo: dict = {"U_INO_ProcessWBC": "N"}
    if linhas:
        corpo["DocumentLines"] = [{"LineNum": int(row["LineNum"]), "U_INO_OP": 0} for row in linhas]

    await sl.update_entity("Orders", doc_entry, corpo)
    logger.info(
        "  Pedido %s marcado como não processado (U_INO_ProcessWBC='N'); "
        "U_INO_OP zerado em %d linha(s).", doc_entry, len(linhas),
    )
    return {"linhas_limpas": len(linhas)}


# ---------------------------------------------------------------------------
# Verificação do PATCH parcial de DocumentLines (21/09/2026)
# ---------------------------------------------------------------------------
def historico_de_linhas(hana_reader: HanaDirectReader, doc_entry: int) -> list[dict]:
    """How many lines the order had in each saved version (`ADOC`/`ADO1`). Read-only.

    Retroactive forensics: B1 keeps a copy of the lines on every document update. If the count drops
    from one version to the next, some update ate lines — and you can see which. Returns an empty list
    when the history log is not active, which proves nothing either way.
    """
    return hana_reader.fetch_all(*ligar(q.HISTORICO_LINHAS_PEDIDO, doc_entry=int(doc_entry)))


def foto_das_linhas(hana_reader: HanaDirectReader, doc_entry: int) -> list[dict]:
    """Snapshot of the order's lines, to compare before/after a PATCH."""
    return hana_reader.fetch_all(*ligar(q.FOTO_LINHAS_PEDIDO, doc_entry=int(doc_entry)))


def compara_fotos(antes: list[dict], depois: list[dict]) -> dict:
    """Difference between two `RDR1` snapshots, by `LineNum`.

    Returns `{sumiram, surgiram, alteradas}`. `alteradas` lists, per line, which fields changed — with
    the value before and after, so the difference speaks for itself.
    """
    por_linha_antes = {int(linha["LineNum"]): linha for linha in antes}
    por_linha_depois = {int(linha["LineNum"]): linha for linha in depois}

    alteradas = []
    for line_num in sorted(set(por_linha_antes) & set(por_linha_depois)):
        a, d = por_linha_antes[line_num], por_linha_depois[line_num]
        campos = {
            campo: (a.get(campo), d.get(campo))
            for campo in a
            if str(a.get(campo)) != str(d.get(campo))
        }
        if campos:
            alteradas.append({"LineNum": line_num, "campos": campos})

    return {
        "sumiram": sorted(set(por_linha_antes) - set(por_linha_depois)),
        "surgiram": sorted(set(por_linha_depois) - set(por_linha_antes)),
        "alteradas": alteradas,
    }


async def patch_parcial_de_prova(
    sl: ServiceLayerClient, doc_entry: int, line_num: int, valor_atual: int
) -> None:
    """Resend ONE line with the value it already has — a PATCH that should change nothing.

    It is the isolated experiment: the same body shape `marca_op_nas_linhas` uses in production
    (`DocumentLines` with only `LineNum` + one field), but with a value equal to the current one. So
    everything that shows up in the before/after comparison is a side effect of the mechanism, not of
    the requested change.
    """
    await sl.update_entity(
        "Orders",
        int(doc_entry),
        {"DocumentLines": [{"LineNum": int(line_num), "U_INO_OP": int(valor_atual)}]},
    )
