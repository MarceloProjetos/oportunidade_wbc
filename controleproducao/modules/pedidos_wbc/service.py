"""Lógica de negócio do módulo Pedidos WBC — porte do addon original.

Equivalente a `Form2` (Views/IntegraPedidoWBC.b1f.cs) + `Controllers/ProcessDefault.cs`.
Ver seção 4.4 do migration_guide.md para a descrição passo a passo do fluxo original, e a
seção 7.4 para o histórico desta implementação (extração do C# feita em 15/09/2026).

⚠️ Não testado contra um ambiente real (sem credenciais/rede de homologação disponíveis
nesta sessão) — antes de usar em produção, seguir o passo 4 da seção 10 do guia
(rodar em paralelo ao addon legado e comparar registro a registro, DUPLAMENTE importante
aqui: é o módulo mais complexo, com a cascata recursiva de Ordens de Produção).

⚠️ Nomes de entidade/campo da Service Layer para Ordens de Produção (`ProductionOrders`)
foram confirmados contra o padrão documentado da SAP; os de **Resources** (Recurso —
`CriaResources`) e das linhas de `SalesOpportunities` (`AddPedidoOportunidade`) usam os
nomes mais prováveis, mas **precisam ser validados contra o `$metadata` real da Service
Layer do Anderson** antes do primeiro teste (marcado com TODO no local exato).

✅ `Resources/Solda.txt` recebido em 21/09/2026 e instalado em `pedidos_wbc/resources/Solda.txt`
(5.015 códigos distintos). Antes disso o porte rodava com a lista vazia — ver seção
7.20 do migration_guide.md para o que isso significa nas OPs já geradas.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from controleproducao.core.audit_log import preenche_log
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
    """Extrai a chave do documento recém-criado da resposta da Service Layer.

    ⚠️ Corrigido em 16/09/2026. O código lia direto `resultado["DocEntry"]`, mas a entidade
    `ProductionOrders` da Service Layer expõe a chave como **`AbsoluteEntry`** (a coluna da
    tabela `OWOR` é que se chama `DocEntry`). Resultado: toda OP criada devolvia `0`, e esse
    zero era gravado em `U_INO_OP` nas linhas do pedido por `_atualiza_doc(..., "OP")` —
    as OPs nasciam certas, mas o vínculo com o pedido ficava zerado. Apareceu no log do
    orçamento 00125192 como `OP criada: DocEntry=0`, sete vezes.

    Tenta as duas grafias em vez de fixar uma só, porque entidades diferentes da Service
    Layer usam nomes diferentes (`DocEntry` em `Orders`, `AbsoluteEntry` em
    `ProductionOrders`), e avisa alto se não achar nenhuma — melhor um aviso do que
    continuar gravando zero em silêncio. Para conferir o nome real de qualquer entidade:
    `python -m controleproducao diag entidade <Entidade> --campos`.
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


def _primeiro_valor(rows: list, padrao=None):
    """Primeiro valor da primeira linha de um resultado, ou `padrao` se não houver linha.

    Existe porque várias queries transcritas do C# não têm alias (`SELECT COUNT(...)`,
    `SELECT top 1 "OpprId"`), e sem alias o driver devolve a coluna com um nome que não
    dá para escrever no código. O idioma `list(rows[0].values())[0]` aparecia nove vezes
    e some no meio da linha; nomeado, a intenção fica visível — e o `if rows` que faltava
    em alguns pontos passa a ser único.
    """
    return list(rows[0].values())[0] if rows else padrao


def _deposito_do_item(item_code: str) -> dict:
    """`{"Warehouse": "08"}` para item que começa com "I", `{}` para os demais.

    Devolve DICIONÁRIO e não string porque o campo precisa ficar **ausente** do corpo
    quando não se aplica — mandar `Warehouse: None` não é a mesma coisa para a Service
    Layer. Era um `**({...} if ... else {})` repetido em quatro pontos, com a regra do
    prefixo escrita por extenso em cada um.
    """
    if str(item_code or "")[:1].upper() == PREFIXO_ITEM_DEPOSITO_08:
        return {"Warehouse": DEPOSITO_ITEM_I}
    return {}


async def buscar_pedidos_para_integrar(
    hana_reader: HanaDirectReader, integrados: bool = False
) -> list[PedidoParaIntegrar]:
    """Equivalente ao botão 'Buscar' do `Form2` — lista os pedidos de cada um dos dois modos
    da tela original:

    - `integrados=False` (padrão) → modo **"Pedidos Novos"** (`BuscaPedidosParaIntegrar`):
      pedidos já vinculados a uma oportunidade integrada (`U_INO_IntegrouWBC='Y'`) e ainda
      **não** processados (`U_INO_ProcessWBC='N'`) — os candidatos a `processar_pedidos_novos`.
    - `integrados=True` → modo **"Pedidos Integrados"** (`BuscaPedidosIntegrados`): os que
      já têm `U_INO_ProcessWBC='Y'`. Útil durante a validação: assim que o porte processa um
      pedido (ou o addon legado processou), ele sai da primeira lista e aparece nesta — é aqui
      que se acha um pedido para reprocessar com `--force` ou com `reprocessar-integrados`.

    ⚠️ Função adicionada em 15/09/2026 (a pedido do Anderson) — antes desta reescrita não
    havia comando de listagem para o módulo 2, só `processar-novos`/`reprocessar-integrados`
    (que exigem o `orc_num` já em mãos). A query em si já vinha transcrita do C# original em
    `queries.py`, com um placeholder `{filtro}` (igual ao padrão usado em
    o módulo 1, desde removido) — sem filtro adicional nesta primeira versão, listamos todos
    os pedidos elegíveis (`filtro=""`)."""
    sql = q.BUSCA_PEDIDOS_INTEGRADOS if integrados else q.BUSCA_PEDIDOS_PARA_INTEGRAR
    rows = hana_reader.fetch_all(*ligar(sql, filtro=""))
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
        )
        for row in rows
    ]
    # Pedido mais recente primeiro (22/09/2026, a pedido do Anderson). A ordenação é feita
    # aqui e não na query porque o SQL veio transcrito verbatim do C# e é referência de
    # comparação com o legado — mudá-lo por questão de apresentação atrapalharia isso.
    # Mesma convenção já usada na busca do módulo 3 (`DocNum` decrescente).
    pedidos.sort(key=lambda p: p.doc_num, reverse=True)
    return pedidos


# ---------------------------------------------------------------------------
# Helpers de leitura (HANA — tabelas padrão do SAP B1: ORDR/OOPR/OPR1/OITM/OWOR)
# ---------------------------------------------------------------------------
async def _verifica_congelado(hana_reader: HanaDirectReader, doc_entry: str) -> str:
    """`Querys.VerificaCong` — campo `U_INO_Congelado` do pedido (`ORDR`)."""
    rows = hana_reader.fetch_all(*ligar(q.VERIFICA_CONG, doc_entry=doc_entry))
    return str(rows[0]["U_INO_Congelado"]) if rows else "N"


async def _verifica_tab_update(hana_reader: HanaDirectReader, doc_entry: str) -> str:
    """`Querys.VerificaTabUpdate` — campo `U_INO_UpdateDetalhe` do pedido (`ORDR`)."""
    rows = hana_reader.fetch_all(*ligar(q.VERIFICA_TAB_UPDATE, doc_entry=doc_entry))
    return str(rows[0]["U_INO_UpdateDetalhe"]) if rows else ""


async def _verifica_process_wbc(hana_reader: HanaDirectReader, doc_entry: str) -> str:
    """`U_INO_ProcessWBC` do pedido (`ORDR`) — "Y" quando o pedido já foi processado.

    ⚠️ Checagem adicionada em 15/09/2026 a pedido do Anderson — **não existe no C#
    original**. No addon legado a proteção contra reprocessamento vinha só da tela: a
    grade "Pedidos Novos" listava apenas pedidos com `U_INO_ProcessWBC='N'`
    (`BuscaPedidosParaIntegrar`), então um pedido já processado simplesmente não
    aparecia mais para ser selecionado de novo. Como a CLI recebe o `orc_num` direto, sem
    passar pela grade, essa proteção precisa existir em código — ver `processar_pedidos_novos`
    e a flag `--force`."""
    rows = hana_reader.fetch_all(*ligar(q.VERIFICA_PROCESS_WBC, doc_entry=doc_entry))
    return str(rows[0]["U_INO_ProcessWBC"]) if rows else "N"


def _op_previa_do_grupo(previas: list[int], ordem_do_grupo: int) -> int | None:
    """A OP que já cobre o `ordem_do_grupo`-ésimo grupo de um item, ou `None`.

    `previas` são as OPs que o item já tinha ANTES desta execução. Com 1 OP prévia e três
    grupos do mesmo item, o 1º grupo é reprocessamento (devolve a OP), o 2º e o 3º não.
    """
    if 1 <= ordem_do_grupo <= len(previas):
        return previas[ordem_do_grupo - 1]
    return None


async def _ops_existentes_para_item(hana_reader: HanaDirectReader, doc_num_ped: str, item_code: str) -> list[int]:
    """Todas as OPs não canceladas do pedido para o item (`OWOR.OriginNum` +
    `OWOR.ItemCode`), em ordem de `DocEntry`.

    Checagem adicionada em 15/09/2026 (mesma motivação de `_verifica_process_wbc`) — o C#
    original (`CriaOP`/`CriaOPSA`) nunca verificava isso antes de criar; sem ela, rodar
    `processar-novos` duas vezes para o mesmo pedido duplica as OPs.

    ⚠️ 23/09/2026 — pedido 84426. Até esta data a checagem respondia "existe ou não", e
    isso não bastava: **vários grupos do WBC podem apontar para o mesmo item SAP** (no 84426,
    três dos quatro itens do orçamento viraram `I000003` — Porta-Paletes). O primeiro grupo
    criava a OP; o segundo e o terceiro consultavam, encontravam a OP que o primeiro tinha
    acabado de criar **na mesma execução** e pulavam como se fosse reprocessamento. O
    chamador agora compara QUANTAS OPs já existiam com QUANTOS grupos daquele item já
    passaram — ver `_processa_grupo_producao`."""
    if not doc_num_ped:
        return []
    rows = hana_reader.fetch_all(*ligar(q.CHECA_OP_EXISTENTE_PEDIDO, doc_num=doc_num_ped, item_code=item_code))
    return sorted(int(_primeiro_valor([r])) for r in rows)


async def _get_id_orcamentos_pedido(hana_reader: HanaDirectReader, orc_num: str) -> dict | None:
    """`Querys.GetIdOrcamentosPedido` — (DocEntry, CardCode, DocNum) do pedido vinculado."""
    rows = hana_reader.fetch_all(*ligar(q.GET_ID_ORCAMENTOS_PEDIDO, orc_num=orc_num))
    return rows[0] if rows else None


async def _oppr_id_do_orcamento(hana_reader: HanaDirectReader, orc_num: str) -> str:
    """A chave da Oportunidade no SAP (`OOPR.OpprId`) a partir do nº do orçamento WBC.

    ⚠️ Acrescentada em 22/09/2026 porque o porte tinha **colapsado duas numerações numa
    só**. A grade do addon trazia as duas lado a lado:

    - `"Nº Oportunidade"` = `OOPR.U_ORCNUM_MASC` — o nº do orçamento WBC (ex. `00125566`),
      usado em praticamente todo o fluxo;
    - `"Num Oportunidade"` = `OPR1.OpprId` — a chave da Oportunidade (ex. `15149`), usada
      em **um único ponto**: `AddPedidoOportunidade`, que faz `GetByKey` de
      `SalesOpportunities` (`Convert.ToInt32(OpprId)` no C#, linha 848 da view).

    Como CLI e web recebem só o número do orçamento, a chave é reobtida aqui. É uma
    consulta que o legado não fazia — lá o valor já estava na linha da grade.
    """
    rows = hana_reader.fetch_all(*ligar(q.OPPR_ID_POR_ORCAMENTO, orc_num=orc_num))
    return str(_primeiro_valor(rows, ""))


async def _pega_doc_entry_ped(hana_reader: HanaDirectReader, orc_num: str) -> str:
    rows = hana_reader.fetch_all(*ligar(q.PEGA_DOC_ENTRY_PED, orc_num=orc_num))
    return str(rows[0]["DocEntry"]) if rows else ""


async def _busca_orcamentos_wbc(wbc: WbcSqlServerClient, orc_num: str) -> list[OportunidadeDoc]:
    """Equivalente a `SQLConnection.PegaOrcamentosWBC(GetOrcsWBC)`."""
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
    """Grava/atualiza o "espelho" do orçamento no UDO `OrcDetalhe` (+ filha
    `INO_ORC_LINHA`) e devolve o `DocEntry` do registro criado.

    Dois caminhos, exatamente como no C# (`ProcessDefault.preencheTabela`,
    ProcessDefault.cs ~1458):

    - **Caminho principal** (`GetTableValdixson` tem linhas — o orçamento tem estrutura
      detalhada em `INTEGRACAO_ORCPRDARV`): uma linha do UDO por item da estrutura,
      cabeçalho vindo de `NovaTabelaQuot` (usa só o último item lido, igual ao C#: o
      `foreach` original reescreve os mesmos campos de cabeçalho a cada iteração).
    - **Caminho de fallback** (sem estrutura detalhada): cabeçalho + linhas vêm de
      `NovaTabelaQuot`/`NovaTabelaQuotLinha`, cada linha de `INTEGRACAO_ORCIMP` vira uma
      linha "rasa" do UDO. ⚠️ Réplica de uma particularidade do C# original: o método lá
      tem um `return` dentro do primeiro `foreach`, então processa **só o primeiro**
      registro de `NovaTabelaQuot` mesmo que existam outros — replicado aqui de propósito
      (fidelidade fase 1, ver seção 8 do migration_guide.md).
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
            await sl.create_entity(_UDO_ORC_DETALHE, body)
            return await _get_doc_entry_table_valdixon(wbc)

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
        await sl.create_entity(_UDO_ORC_DETALHE, body)
        return await _get_doc_entry_table_valdixon(wbc)

    except Exception as exc:  # noqa: BLE001 - replica o catch externo (retorna 0) do C#
        logger.exception("Erro em preenche_tabela para orc_num=%s", orc_num)
        await preenche_log(sl, orc_num, "Pedido", "Erro", "Integração", str(exc))
        return 0


def _monta_body_orc_detalhe(orc_num: str, header, linhas_udo: list[dict]) -> dict:
    """Monta o corpo do UDO `OrcDetalhe` (+ coleção filha) para a Service Layer.

    ⚠️ **Tipos importam aqui** (descoberto em 15/09/2026 com `diag entidade OrcDetalhe`,
    lendo um registro real criado pelo addon legado): o C# original passava tudo como
    string (`oGeneralData.SetProperty("U_ORCVALVND", item.ORCVALVND.ToString())`) porque a
    DI API converte para o tipo do UDF automaticamente. A Service Layer é tipada em JSON e
    **rejeita** string onde o campo é numérico — devolvendo um genérico
    `Internal server error`, sem dizer qual campo. Por isso os campos abaixo seguem os
    tipos observados no registro real, e não o `.ToString()` do C#:
      - `float`: U_ORCVAL{VND,LST,INV,LUC,EXP,COM,TRP,MON}, U_ORCPERCOM, U_ORCBAS{1,2,3}
      - `int`:   U_CLICOD, U_CLICONCOD, U_PRZENT
      - `str`:   os demais — incluindo **U_ORCVALEMB**, que apesar do nome é um UDF de
                 texto no SAP (vem como `"0.0000"` no registro real), então continua `str`.
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


async def _busca_header_nova_tabela_quot(wbc: WbcSqlServerClient, orc_num: str) -> NewOrcPrd:
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
    """Equivalente a `SQLConnection.ExecuteSelectNew` — um `NewOrcPrd` por linha de
    `INTEGRACAO_ORCIMP`, cada um com sua própria lista de `linhas` (`NovaTabelaQuotLinha`,
    consultada uma vez por item — igual ao C#, que reabre a query dentro do loop)."""
    header = await _busca_header_nova_tabela_quot(wbc, orc_num)
    # `fetch_all_values` (não `fetch_all`/dict) pelo mesmo motivo de
    # `_busca_header_nova_tabela_quot`: `NOVA_TABELA_QUOT_LINHA` tem várias colunas `''`
    # literais e `ISNULL(...)` sem alias que colidem por nome no driver ODBC.
    linha_rows = wbc.fetch_all_values(q.NOVA_TABELA_QUOT_LINHA, (orc_num,))
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
    """`Querys.PegaLinha` — devolve o `ORCITM` associado a uma linha da árvore."""
    rows = wbc.fetch_all(q.PEGA_LINHA, (id_integracao_orc_prd,))
    if not rows:
        return ""
    return str(_primeiro_valor(rows) or "")


async def _get_linha(hana_reader: HanaDirectReader, orc_num: str, orc_itm: str) -> str:
    """`Querys.GetLinha` — número da linha (`LineNum`) do pedido de venda já criado no
    SAP para este `orc_itm`. Consulta HANA (tabelas `RDR1`/`ORDR`), não WBC."""
    rows = hana_reader.fetch_all(*ligar(q.GET_LINHA, orc_num=orc_num, orc_itm=orc_itm))
    if not rows:
        return "0"
    return str(_primeiro_valor(rows) or "0")


async def _get_doc_entry_table_valdixon(wbc: WbcSqlServerClient) -> int:
    """⚠️ ESQUELETO — devolve 0 sempre. Rastreado em 23/09/2026; ainda não corrigido.

    No C# é `Querys.getDocEntryTableValdixon` (`SELECT max("DocEntry") FROM "@INO_ORCAM"`,
    no HANA), executada logo após criar o espelho do orçamento para recuperar o DocEntry
    do registro recém-inserido. O porte deixou um `return 0` com a ideia de usar a
    resposta do `POST` da Service Layer — e isso nunca foi feito.

    **Consequência, porque um `return 0` aqui não é neutro:** o valor sobe como
    `tb_valdixson` e é o que decide dois comportamentos.

    1. `U_INO_ORCAMENTO` **nunca é gravado no pedido.** Os três `Update*` do C# fazem
       `if (tbValdixson != 0) DocCot.UserFields.Fields.Item("U_INO_ORCAMENTO").Value = ...`
       (ProcessDefault.cs 1090, 1168, 1251). Com 0, o `if` é sempre falso aqui e sempre
       verdadeiro lá. É uma DIVERGÊNCIA silenciosa em relação ao legado, em todo pedido
       processado — não um detalhe de implementação.
    2. O ramo `tb_valdixson != 0` de `_update_pedido` vira **código morto**: só o `else`
       roda. Junto com ele some a consulta de peso (`GET_PESO_PEDIDO`, que o C# faz com
       `tbValdixson` — ProcessDefault.cs 1300), e portanto o `Weight1` das linhas.

    A query está preservada em `queries.py` como `GET_DOC_ENTRY_TABLE_VALDIXON`. A
    correção precisa de uma decisão que não é minha: ler o `DocEntry` da resposta do
    `create_entity` (mais correto, e evita a corrida do `max()`) ou executar a query do
    legado (fiel, mas sujeita a devolver o registro de outro processo). Por isso ficou
    registrado em vez de ser resolvido por conta própria.
    """
    return 0


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
    """Decide entre `UpdateTabPedido` / `UpdateTabPedidoCong` / `UpdatePedido`, replicando
    exatamente a árvore de decisão de `Button0_ClickAfter` (IntegraPedidoWBC.b1f.cs,
    ~linha 258-283 e ~2000-2023): primeiro checa `U_INO_Congelado`; se "N", checa
    `U_INO_UpdateDetalhe" (`resultado`) para escolher entre os outros dois.

    ⚠️ Nomes dos parâmetros corrigidos em 23/09/2026. Eram `oppr_id` e `oppr_id_orig` —
    dois nomes quase iguais para coisas diferentes: o primeiro recebe o nº do ORÇAMENTO
    WBC e o segundo recebe o nº do PEDIDO, como se vê nas duas chamadas. Nenhum dos dois
    é a chave da Oportunidade, que é o que `oppr_id` sugeria — e confundir essas
    numerações já custou um bug em 22/09 (seção 7.35 do migration_guide.md).
    """
    # Qual dos três caminhos foi tomado é a informação mais útil desta etapa: eles
    # gravam coisas bem diferentes (um só mexe em campos de controle, outro RECRIA as
    # linhas do pedido), e a árvore de decisão depende de dois campos do próprio pedido.
    # Sem isto, o acompanhamento não distingue um update inofensivo de uma recriação.
    verif_congelado = await _verifica_congelado(hana_reader, doc_entry)
    if verif_congelado == "N":
        resultado = await _verifica_tab_update(hana_reader, doc_entry)
        if resultado != "Y":
            logger.info(
                "  Pedido não congelado e sem update de detalhe: atualizando só os campos "
                "de controle (U_INO_ProcessWBC=%s).", process,
            )
            return await _update_tab_pedido(sl, oportunidades, orc_num, doc_entry, tb_valdixson, process)
        logger.info(
            "  Pedido não congelado e COM update de detalhe: recriando as linhas do "
            "pedido a partir do orçamento WBC.",
        )
        return await _update_pedido(
            sl, wbc, hana_reader, oportunidades, orc_num, card_code, doc_num, doc_entry, tb_valdixson,
            process,
        )
    logger.info(
        "  Pedido congelado: atualizando os campos de controle%s.",
        " e zerando U_INO_OP nas linhas" if process != "Y" else "",
    )
    return await _update_tab_pedido_cong(sl, oportunidades, orc_num, doc_entry, tb_valdixson, process)


async def _update_tab_pedido(
    sl: ServiceLayerClient,
    oportunidades: list[OportunidadeDoc],
    orc_num: str,
    doc_entry: str,
    tb_valdixson: int,
    process: str,
) -> int:
    """`ProcessDefault.UpdateTabPedido` — só atualiza campos de controle do pedido, sem
    mexer nas linhas."""
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


async def _update_tab_pedido_cong(
    sl: ServiceLayerClient,
    oportunidades: list[OportunidadeDoc],
    orc_num: str,
    doc_entry: str,
    tb_valdixson: int,
    process: str,
) -> int:
    """`ProcessDefault.UpdateTabPedidoCong` — como `_update_tab_pedido`, mas quando
    `process != "Y"` também zera `U_INO_OP` em todas as linhas existentes do pedido
    (equivalente ao loop `DocCot.Lines.SetCurrentLine(i); ...U_INO_OP = 0`)."""
    campos: dict = {"U_INO_ProcessWBC": process}
    if tb_valdixson != 0:
        campos["U_INO_ORCAMENTO"] = tb_valdixson
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


def _kg(valor: float) -> str:
    """`226.43` → `226,43`, como a pessoa lê na tela do SAP."""
    return f"{valor:.2f}".replace(".", ",")


def _loga_pesos(hana_reader: HanaDirectReader, orc_num: str, doc_entry, estrutura) -> None:
    """One log line per order line: the SAP ``Weight1`` next to what it should be.

    Expected = WBC tree level 1 + 10% — the worker's rule (`wbcpython.domain.linhas.
    peso_da_linha`, imported so the two cannot drift). Read-only, and never stops the
    processing: this step does not write the weight (the worker does, when it creates the
    order). It exists because the weight went wrong on quote 00125817 (29/09/2026) with
    nothing about it in this log. More than 1% away from the expected value (hand-typed
    weights are round numbers: 248 for 249.07) comes out as a WARNING.
    """
    from decimal import Decimal

    from wbcpython.domain.linhas import peso_da_linha

    try:
        wbc: dict[int, float] = {}
        for no in estrutura:            # `EstruturaPrd`, from `busca_estrutura_produto`
            if int(no.nivel or 0) == 1:
                wbc[int(no.orc_itm)] = wbc.get(int(no.orc_itm), 0.0) + float(no.peso or 0)
        linhas = hana_reader.fetch_all(*ligar(q.PESOS_DAS_LINHAS_DO_PEDIDO, doc_entry=doc_entry))
    except Exception as exc:  # noqa: BLE001 - a log line must not break the processing
        logger.warning("Pedido %s: não foi possível comparar os pesos: %s", orc_num, exc)
        return
    for linha in linhas:
        orc_itm = str(linha.get("U_INO_ORCITM") or "").strip()
        sap = float(linha.get("Weight1") or 0)
        arvore = wbc.get(int(orc_itm)) if orc_itm.isdigit() else None
        esperado = peso_da_linha(Decimal(str(arvore))) if arvore is not None else None
        texto = (
            f"Pedido {orc_num}: peso da linha {linha.get('LineNum')} (item {linha.get('ItemCode')}, "
            f"OrcItm {orc_itm or '—'}, qtd {float(linha.get('Quantity') or 0):g}): SAP {_kg(sap)} kg"
            + (f" · esperado {_kg(float(esperado))} kg (árvore do WBC {_kg(arvore)} kg + 10%)"
               if esperado is not None else " · árvore do WBC sem peso")
        )
        if esperado is not None and abs(sap - float(esperado)) > float(esperado) * 0.01:
            logger.warning("%s — DIFERENTE.", texto)
        else:
            logger.info("%s.", texto)


_LINHA_MANUAL_VAZIA = Linha(ped_cliente="", item_cliente="0", nf="", cor="")


def _monta_linha_pedido(item, item_sap: str, manual: Linha, peso=None) -> dict:
    """Uma linha de `DocumentLines` a partir do item do orçamento WBC.

    Os dois ramos de `_update_pedido` montavam as MESMAS nove chaves, na mesma ordem,
    diferindo apenas por `Weight1` (que só o primeiro tem). Duas cópias de nove campos é
    o tipo de duplicação que sobrevive a uma correção feita só num lado.
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
    """`ProcessDefault.UpdatePedido` — recria as linhas do pedido a partir de
    `oportunidades` (`GetItensSAP` + `GetPesoPedido`). Dois sub-caminhos, como no C#:
    `tb_valdixson != 0` (reaproveita o pedido/DocEntry existente) vs. o `else` (busca
    vendedor/condições no `SalesOpportunities` original e monta um pedido do zero).

    ⚠️ A Service Layer substitui linhas de documento enviando o array `DocumentLines`
    completo num único `PATCH` (não há `Lines.Delete()` linha a linha como na DI API) —
    portado assim aqui; validar contra o ambiente real que isso recria as linhas com o
    mesmo efeito do legado.
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
        # (b) este ramo hoje é inalcançável — ver `_get_doc_entry_table_valdixon`.
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
    """`Querys.ManualLinha` — dados manuais (pedido do cliente, item, NF, cor) já
    preenchidos nas linhas existentes do pedido, na ordem em que aparecem (RDR1: HANA)."""
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
    """Busca a árvore completa de estrutura do(s) orçamento(s) (`PEGA_ESTRUTURA_PRD_WBC`).

    Mapeamento **posicional**, replicando exatamente `SQLConnection.PegaEstruturaPrd`
    (SQLConnection.cs ~487) — a query faz `SELECT tst.*, num_row, <PRDCOD calculado>` e o
    C# lê por índice, não por nome (o `PRDCOD` de `INTEGRACAO_ORCPRDARV`, coluna 4, vira
    `PrdArv`; a ÚLTIMA coluna, o `PRDCOD` recalculado/coalescido com `INTEGRACAO_ORCPRD`,
    vira `PrdCode`; a coluna `num_row` (`ROW_NUMBER()`), penúltima, vira `linhaOrc`; e
    `linha = OrcItm`). Importante não confundir com `prd_arv`/`prd_code` invertidos.

    Réplica também o `.Reverse()` do método original — a ordem da lista importa para os
    agrupamentos por nível feitos depois (a ordem final é do nível mais profundo para o
    nível 1, igual ao C#)."""
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
    """Para cada item da estrutura ainda não cadastrado no SAP, cria (`CriaItem`); se já
    existir e o `PrdArv` estiver na lista de solda, força o grupo 332 (`UpdateItem`).

    `nivel_top` — o menor `nivel` entre os itens da estrutura — é calculado uma vez no
    início do loop (equivalente a `Form2.nivelTop`, lido por `CriaItem` para decidir
    `ProcurementMethod` Make vs. Buy) e reaproveitado para todos os itens, igual ao C#
    original (que só recalcula `nivelTop` quando cria um item de nível menor que o atual —
    detalhe sem efeito prático aqui já que a estrutura inteira já está em mãos).
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
    """Passo por item do loop de `Button0_ClickAfter` (linhas ~324-374) — separado de
    `garante_itens_cadastrados` para poder ser chamado com o `HanaDirectReader` já aberto
    pelo orquestrador (`processar_pedidos_novos`)."""
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
    """Nome do item no SAP — usado só para o log. Nunca derruba a execução."""
    try:
        rows = hana_reader.fetch_all(*ligar(q.DESCRICAO_ITEM, item_code=item_code))
        return str(_primeiro_valor(rows, ""))
    except Exception:  # noqa: BLE001 - uma descrição ausente não pode parar uma gravação
        return ""


def _resumo(texto: object, limite: int = 60) -> str:
    """Descrição encurtada para caber numa linha de log.

    Descrições de produto do WBC passam de 100 caracteres ("COLUNA NORMAL 80 mm ch. 1,80
    4800MM ..."), e uma linha que quebra três vezes na tela deixa de ser acompanhamento.
    """
    limpo = " ".join(str(texto or "").split())
    return limpo if len(limpo) <= limite else limpo[: limite - 1].rstrip() + "…"


def _quantidade(valor: object) -> str:
    """Quantidade sem casas decimais inúteis: `640`, não `640.0`."""
    try:
        return f"{float(valor):g}"
    except (TypeError, ValueError):
        return str(valor or "")


def _num(valor: object, padrao: float = 0.0) -> float:
    """Converte para `float` um número vindo do HANA ou do SQL Server.

    Os dois drivers devolvem `decimal.Decimal` para colunas numéricas, e `Decimal` **não
    opera com `float`**: `2.0 / Decimal("10")` levanta
    `TypeError: unsupported operand type(s) for /: 'float' and 'decimal.Decimal'`.
    Foi exatamente esse erro que fez o recurso de rateio do item I000005 falhar no
    orçamento 00120634 (21/09/2026) — o `except` do `_processa_grupo_producao` engoliu a
    exceção, registrou no `INO_LOG` e a OP saiu sem a linha de rateio. Só apareceu na
    comparação com o legado, dias depois.

    É a mesma classe de problema do `Decimal is not JSON serializable` (seção 7.8): tipo
    de banco escapando para dentro da lógica. A resposta aqui é a mesma — converter na
    fronteira, uma vez, em vez de espalhar `float(...)` por cada operação.

    Aceita `None` e string vazia (devolve `padrao`) e string com vírgula decimal, que é
    como o WBC devolve alguns campos convertidos.
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
    """Devolve os dígitos de um número como o C# os produziria, para compor o código do
    Recurso de rateio (`GGF_<orc><item><dígitos>`).

    O legado monta esse código com `valor.ToString()` de um `double` em cultura pt-BR e
    depois remove o separador decimal. Em `double.ToString()`, um valor inteiro sai **sem
    parte decimal**: `0` vira `"0"`, e `3854.14` vira `"3854,14"` -> `"385414"`.

    O porte fazia `str(valor)` direto. Para 3854.14 o resultado coincide, mas para um
    valor inteiro o Python acrescenta o `.0` (`str(0.0) == "0.0"`, e `str(Decimal("0.00"))
    == "0.00"`), o que gerava `"00"`/`"000"` em vez de `"0"` — código diferente do que o
    legado criou. Efeito prático: o porte não reconhecia o recurso já existente
    (`GGF_00120634I0000050`) e tentava criar outro. Descoberto em 21/09/2026 na primeira
    comparação real contra produção (seção 7.24).

    Continua sendo o débito nº 8 da seção 8 (cultura de parsing decimal), aqui no lugar
    mais traiçoeiro possível: não num cálculo, mas na formação de um identificador.
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
    """Cria (se não existir) um Recurso representando rateio de transporte/embalagem/
    montagem, ou devolve o código do existente. Retorna `""` quando não há custos WBC.

    Nomes de campo confirmados em 23/09/2026 contra um recurso real
    (`diag entidade Resources`, só leitura). Até essa data o corpo usava `ResourceCode`/
    `Warehouses`/`WarehouseCode`/`DailyCapacities` "segundo a documentação" — nenhum
    existe na Service Layer — e **todo** `POST /Resources` do porte falhou com `Data
    DailyCapacities not found`. A OP saía assim mesmo, sem a linha de rateio (pedido
    84426, seção 7.42 do guia). Os nomes reais são os mesmos da DI API com prefixo nas
    coleções: `VisCode`, `ResourceWarehouses[].Warehouse`,
    `ResourceDailyCapacities[].Weekday/Factor1`.

    Fiel ao C# (`CriaResources`): só `VisCode` é informado — o `Code` interno fica com o
    B1 — e o que se devolve na criação é o `Code` retornado (`ret.Code`); no ramo do
    recurso já existente, o `VisResCode` lido do ORSC (`PegaRecursoCode`).
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
        "ResourceWarehouses": [{"Warehouse": "01"}],
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
    """`ProcessDefault.CriaOP` — cria a Ordem de Produção do item pai, com uma linha de
    componente por item nível 1 da estrutura + a linha de recurso (se houver).

    Retorna `(status, doc_entry_criado)` — 0/DocEntry em caso de sucesso, réplica do
    `int[] final` original. Quando `entrega_multipla == "Y"`, o C# **não cria a OP**
    (deixa `retorno = 0, final[1] = 0` sem chamar `Add()`) — comportamento preservado
    aqui, embora pareça uma condição invertida (ver seção 9 do migration_guide.md — vale
    confirmar com o Anderson se é intencional)."""
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
                "Warehouse": "01",
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
    """`ProcessDefault.CriaOPSA` — cria a Ordem de Produção de um semiacabado, com uma
    linha por item em `semi_acabados`. A linha de recurso fica comentada no C# original
    (dead code) — **não portada de propósito**, réplica fiel."""
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
    """Verifica (via `newBuscaItem`/`verificaSemiAcabadoPeso`) se `item_pai` tem "filhos"
    no próximo nível da estrutura; se tiver, cria uma nova OP para o semiacabado
    (`cria_ordem_producao_semi_acabado`) e **repete recursivamente** para cada filho — é
    aqui que a árvore de estrutura do orçamento vira a cascata de OPs encadeadas por
    nível (ver seção 4.4.1, passo 6, do migration_guide.md).

    Qualquer erro é logado e engolido (nunca propaga) — igual ao `catch` do C# original.
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
    """Grava `U_INO_OP = num_op` nas linhas do pedido correspondentes a `orc_itens`
    (valores de `U_INO_ORCITM`), em **uma única** chamada à Service Layer.

    ⚠️ Otimização de 16/09/2026. Antes, `_processa_grupo_producao` chamava
    `_atualiza_doc(..., "OP")` uma vez por item do grupo, e cada chamada fazia 1 consulta ao
    HANA + 1 `PATCH /Orders`. No perfil do orçamento 00125192 foram **6 PATCHes somando
    4,1s** (~680 ms cada) — a maior fatia isolada da execução. Agora: 1 consulta (resolvendo
    todas as linhas de uma vez) + 1 PATCH com todas as linhas no mesmo corpo.

    O estado final é idêntico — o C# original também fazia um `Update()` por item, mas isso
    era característica da DI API, não regra de negócio. A diferença aparece só em caso de
    falha no meio do grupo: antes, as marcações já feitas ficavam gravadas; agora, ou grava
    todas ou nenhuma. Como uma exceção aborta o pedido inteiro de qualquer forma (o `except`
    de `processar_pedidos_novos`), o estado parcial não era coerente nos dois casos.
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
    """`ProcessDefault.AtualizaDoc` restrito aos tipos usados neste módulo.

    - `"OP"`: grava `U_INO_OP` na linha do pedido correspondente a `linha`.
    - `"Sucesso"`: marca `U_INO_ProcessWBC = "Y"` no pedido.
    - `"Erro"`: marca `U_INO_ProcessWBC = "N"` no pedido.
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
    """Orquestra o fluxo completo do modo "Pedidos Novos" (IntegraPedidoWBC.b1f.cs,
    ~219-763): para cada oportunidade selecionada, preenche a tabela de orçamento,
    decide/aplica a atualização do pedido, garante os itens da estrutura cadastrados,
    e cria a cascata de Ordens de Produção (uma por grupo `(GrpCode, OrcItm)` de nível 1,
    com semiacabados recursivos).

    Verificação de reprocessamento (adicionada em 15/09/2026, ver `_verifica_process_wbc`
    e `_ops_existentes_para_item` — **não existe no C# original**, que dependia só do
    filtro da grade da tela): por padrão, um pedido com `U_INO_ProcessWBC='Y'` é pulado
    (motivo registrado em `com_erro`), e uma OP não é recriada para um item que já tem
    uma OP não cancelada vinculada ao mesmo pedido. Passe `force=True` para pular as duas
    checagens e reprocessar mesmo assim (útil depois de corrigir algo manualmente, ou em
    conjunto com `reprocessar_pedidos_integrados`, que cancela as OPs antigas primeiro).

    Retorna um resumo `{"processados": [...], "com_erro": [...]}` — igual ao padrão já
    usado no módulo 1 enquanto ele existiu (diferente do legado, que só mostrava a
    status bar).
    """
    processados: list[str] = []
    com_erro: list[dict] = []
    # Grupos que terminaram sem gerar OP (ver `_processa_grupo_producao`): o pedido foi
    # integrado, mas parte dele não produziu nada — precisa aparecer no resultado.
    sem_op: list[dict] = []
    # Grupos cuja OP saiu sem a linha de rateio porque o recurso GGF_ não pôde ser criado.
    sem_rateio: list[dict] = []

    for orc_num in orc_nums_selecionados:
        try:
            listaOport = await _busca_orcamentos_wbc(wbc, orc_num)
            id_orc = await _get_id_orcamentos_pedido(hana_reader, orc_num)

            if not id_orc:
                com_erro.append({"orc_num": orc_num, "motivo": "sem pedido vinculado (GetIdOrcamentosPedido vazio)"})
                continue

            doc_entry, card_code, doc_num = str(id_orc["DocEntry"]), str(id_orc["CardCode"]), str(id_orc["DocNum"])

            if not force:
                ja_processado = await _verifica_process_wbc(hana_reader, doc_entry)
                if ja_processado == "Y":
                    com_erro.append(
                        {
                            "orc_num": orc_num,
                            "motivo": (
                                f"pedido {doc_num} já está marcado como processado (U_INO_ProcessWBC=Y) — "
                                "use --force para reprocessar mesmo assim, ou rode "
                                "'reprocessar-integrados' primeiro para cancelar as OPs antigas."
                            ),
                        }
                    )
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

            logger.info("Pedido %s: lendo a estrutura de produto no WBC...", orc_num)
            estrutura = await busca_estrutura_produto(wbc, [orc_num])
            if not estrutura:
                await preenche_log(sl, orc_num, "Pedido", "Erro", "Integração", "Esse pedido não tem dados no WBC.")
                com_erro.append({"orc_num": orc_num, "motivo": "sem dados no WBC (estrutura vazia)"})
                continue

            _loga_pesos(hana_reader, orc_num, doc_entry, estrutura)
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

    return {"processados": processados, "com_erro": com_erro, "sem_op": sem_op, "sem_rateio": sem_rateio}


def _agrupa_por_grp_orc_itm(estrutura: list[EstruturaPrd]):
    """Equivalente a `ListaItensEst.Where(x => x.nivel == 1).GroupBy(x => new
    {x.GrpCode, x.OrcItm})` — agrupa preservando a ordem de primeira ocorrência."""
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
    """Corpo do `foreach (var final in ...GroupBy(...))` em `Button0_ClickAfter`
    (linhas ~2304-2472): resolve o item pai de produção, cria o recurso de rateio, cria a
    OP e dispara a cascata de semiacabados.

    ⚠️ Parâmetro `force` adicionado em 15/09/2026 (mesma checagem de `_verifica_process_wbc` —
    ver docstring de `_ops_existentes_para_item`): quando `force=False` (padrão), pula a criação
    de uma nova OP se já existir uma OP não cancelada para este pedido+item.

    Devolve `None` quando a OP foi criada, ou uma frase explicando por que este grupo não
    gerou OP nenhuma.

    ⚠️ 23/09/2026 — pedido 84426: saíram OPs de apenas 2 dos 4 itens e a execução terminou
    "sem erro". As três saídas sem OP abaixo gravavam só no `@INO_LOG` (`preenche_log`), sem
    nenhum `logger.*`, então não apareciam no acompanhamento em tela nem no resultado da
    tarefa: o operador via "concluída" e não tinha como saber que dois grupos não produziram
    nada. Cada uma passou a emitir também `logger.*` e a devolver o motivo, que
    `processar_pedidos_novos` acumula em `sem_op`. Mesma classe de correção do `except`
    do rateio (21/09) — é a quinta vez neste porte que uma proteção/condição silenciosa só
    aparece depois do estrago.

    `contexto` é compartilhado entre os grupos DO MESMO PEDIDO (criado por
    `processar_pedidos_novos`): guarda quantas OPs cada item já tinha antes desta execução
    e quantos grupos de cada item já passaram, e recolhe os grupos cujo rateio falhou.
    Chamadas avulsas (testes) podem omiti-lo.

    Since 30/09/2026 it also keeps the per-order reads (`leituras`): DocEntry/DocNum of the
    order, multiple delivery, the SAP item of a group code, the WBC costs and the max price
    used to be read again for EVERY group, and the DocEntry twice per group. None of them
    changes during the execution — this run only adds OPs and marks `U_INO_OP` on lines;
    the order, its lines' prices and @INO_GRP_PRODUTOS stay as they were."""
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
            "(use --force para recriar)"
        )
        logger.warning("    SEM OP: %s.", motivo)
        await preenche_log(
            sl, orc_num, "Pedido", "Aviso", "Integração",
            f"já existe uma OP não cancelada (DocEntry={op_existente}) para o pedido "
            f"{doc_num_ped}, item {orc_item_sap} — use --force para recriar mesmo assim.",
        )
        return motivo

    linha_documento = -1
    status, novo_doc_entry = await cria_ordem_producao(
        sl, hana_reader, grupo, orc_item_sap, linha_documento, quantidade_linha_base, recurso, entrega_multipla
    )

    if status == 0:
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
    else:
        motivo = f"a Service Layer recusou a OP do item {orc_item_sap} (status {status})"
        logger.error("    SEM OP: %s.", motivo)
        await preenche_log(
            sl, orc_num, "Pedido", "Erro", "Integração",
            f"Erro ao criar a OP para o item: {orc_item_sap}.",
        )
        await _atualiza_doc(sl, hana_reader, 0, novo_doc_entry, doc_entry_final, "Erro")
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
    """Modo "Pedidos Integrados" (IntegraPedidoWBC.b1f.cs, ~764-883): para pedidos já
    processados, reatualiza o vínculo pedido↔oportunidade (`AddPedidoOportunidade`) e
    cancela as OPs antigas ainda planejadas (`CancelaOP`) antes de recriar.

    Nota do C# original: o corpo roda uma vez por linha de grade selecionada, não uma vez
    por item de `listaOport` — havia um `foreach (var item in listaOport)` comentado
    (dead code) envolvendo o bloco. Comportamento replicado aqui (uma chamada por
    oportunidade selecionada, não por item do orçamento)."""
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
            logger.info("  Tabela do orçamento: DocEntry %s.", tab)

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
    """`ProcessDefault.AddPedidoOportunidade` — vincula o pedido como documento seguinte
    da oportunidade e marca status "Ganha" (`sos_Sold`).

    ⚠️ TODO: nome exato da coleção de linhas de `SalesOpportunities` na Service Layer
    (`SalesOpportunitiesLines` é o nome mais provável) precisa ser confirmado contra o
    `$metadata` real antes do primeiro teste — a DI API usa `oport.Lines.Add()` com
    `DocumentType`/`DocumentNumber`/`PercentageRate`/`MaxLocalTotal`. Use
    `diag entidade SalesOpportunities --campos`: contra a Service Layer, nome de campo se
    descobre lendo um registro real, nunca por analogia (seção 7.29 do guia).

    ⚠️ Esta função relê as linhas e devolve o array inteiro — o mesmo padrão que quebrou
    `marca_op_nas_linhas` (15/09) e `_update_tab_pedido_cong` (22/09) com
    `'' is not a valid value for property ...`. Aqui é preciso ACRESCENTAR uma linha, não
    só alterar campos de linhas existentes, então não dá para simplesmente mandar o
    parcial; se o erro reaparecer, o caminho é montar cada linha existente só com a chave
    de linha e os campos que importam."""
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
    """`ProcessDefault.CancelaOP` — sem try/catch no C# original (propaga exceção);
    comportamento preservado aqui (não engolimos o erro nesta função específica)."""
    await sl.update_entity("ProductionOrders", doc_entry, {"ProductionOrderStatus": "boposCancelled"})


# ---------------------------------------------------------------------------
# Auditoria: comparar as OPs geradas pelo Python com as geradas pelo addon legado
# (adicionado em 15/09/2026 a pedido do Anderson — sem equivalente no C#, é só leitura)
# ---------------------------------------------------------------------------
def _prefixo_schema(schema: str | None) -> str:
    """`"SBOALTAMIRAPROD".` quando um schema é informado, string vazia para usar o schema
    corrente da conexão (o de `SL_COMPANY_DB`; since 28/09/2026 the package reads its schema
    from the write company). Permite comparar produção vs homologação na
    mesma conexão HANA, desde que o usuário tenha leitura nos dois schemas."""
    return f'"{schema}".' if schema else ""


def _resumo_op(row: dict) -> dict:
    """Extrai de uma linha de `OWOR` só os campos que interessam à comparação, de forma
    defensiva (a query usa `SELECT *`; nomes de coluna variam entre versões do B1)."""
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
    """Idem para `WOR1` (componentes da OP)."""
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
    """Ordena as OPs por **conteúdo**, não pelo `DocEntry`/`DocNum`.

    A numeração das OPs é diferente em cada ambiente (produção e homologação têm suas
    próprias sequências), então ordenar por número deixaria as duas tabelas desalinhadas e
    a conferência manual seria um vai-e-vem. Ordenando pelos mesmos atributos de negócio, a
    mesma OP cai (quando existe dos dois lados) na mesma posição das duas tabelas.

    Chave: item produzido → quantidade planejada → linha de referência → nº da OP. A
    quantidade entra antes da linha porque um mesmo item pode ter várias OPs no mesmo
    pedido (ex.: `PAR000PADRA000000000`, com quantidades diferentes), e é pela quantidade
    que se distingue uma da outra; o `U_INO_LinhaRef` pode divergir entre os ambientes, por
    isso não é a chave principal."""
    return (op["item_code"], op["planejada"], op["linha_ref"], str(op["doc_num"]))


def _le_ops_do_lado(
    hana_reader: HanaDirectReader, orc_num: str, schema: str | None, com_linhas: bool
) -> dict:
    """Lê, de um schema, o pedido do orçamento e todas as OPs vinculadas a ele (incluindo
    canceladas — o Anderson avisou que algumas foram canceladas manualmente em produção, e
    elas continuam valendo como "o addon legado criou esta OP")."""
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
    """Compara as OPs de um orçamento entre o schema onde o **addon legado** roda e o
    schema onde **este porte** roda, e diagnostica as que faltaram.

    Os dois ambientes são schemas HANA distintos e permanentes (confirmado pelo Anderson em
    16/09/2026): `SBOALTAMIRAPROD` é onde o addon legado executa, `SBOALTAMIRAHOMOLOG` é
    onde este porte executa. A comparação é sempre schema a schema — não existe separação
    por data de criação (uma versão anterior deste comando tentava isso e só confundia, já
    que os dois lados nunca convivem no mesmo schema).

    `desde` (opcional, `AAAA-MM-DD`) filtra o lado NOVO para considerar só as OPs criadas a
    partir daquela data. Serve para isolar a última execução quando a homologação acumulou
    OPs de execuções anteriores do próprio porte — o que acontece facilmente ao reprocessar
    com `--force`.

    `incluir_canceladas=False` (padrão, pedido pelo Anderson em 16/09/2026): OPs canceladas
    (`OWOR."Status" = 'C'`) ficam FORA da comparação, dos dois lados — em produção várias
    foram canceladas manualmente depois, e isso é ruído operacional, não resultado da
    integração. As descartadas não somem do relatório: voltam em `canceladas_legado` /
    `canceladas_novo` para serem contabilizadas, e os itens que só têm OP cancelada no
    legado voltam em `itens_so_cancelados_legado`. Isso importa porque, ao descartar, um
    item cuja OP de produção foi cancelada passa a aparecer como "só no novo (a mais)" —
    parecendo que o porte criou algo indevido, quando na verdade os dois criaram e um
    humano cancelou depois.

    ⚠️ Só leitura — nunca escreve nada. Requer que o usuário HANA tenha SELECT nos dois
    schemas. Adicionado em 15/09/2026, não existe no C#.
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
    """Para cada item que tem OP no legado mas não no novo, tenta apontar o motivo,
    checando as causas conhecidas (todas documentadas no migration_guide.md):

    1. `U_INO_EntregaMultipla = 'Y'` no pedido → `CriaOP` não cria a OP (quirk do C#
       original que parece condição invertida, pergunta 9 da seção 9 do guia).
    2. Grupo do WBC sem mapeamento em `@INO_GRP_PRODUTOS` → o grupo inteiro é pulado com
       log "Item correspondente ... não cadastrado".
    3. OP não cancelada já existente + execução sem `--force` → pulada pela verificação de
       reprocessamento (seção 7.6 do guia).

    ⚠️ Todas as checagens olham o **lado NOVO** (o schema onde o porte roda), porque é o que
    o porte enxergava ao decidir criar ou não a OP. Olhar o lado do legado daria um
    diagnóstico errado: a verificação de reprocessamento consulta as OPs do próprio schema
    do porte, e o pedido que importa para `U_INO_EntregaMultipla` é o de lá.
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
    """Levanta as OPs de um pedido e diz se o cancelamento em lote pode começar.

    **Não escreve nada** — é a etapa de verificação, deliberadamente separada da execução
    para que a CLI possa mostrar o que vai acontecer, barrar quando houver impedimento, e
    só então pedir confirmação.

    Devolve `{pedido, ops, bloqueantes, a_cancelar, ja_canceladas}`:
    - `bloqueantes`: OPs em status fora de `STATUS_QUE_PERMITEM_CANCELAR`. Se houver
      qualquer uma, **nada deve ser cancelado** — nem as que estariam liberadas para isso.
    - `a_cancelar`: as planejadas (`P`), que é o que de fato será alterado.
    - `ja_canceladas`: as `C`, contadas para o relatório e ignoradas na execução.
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
    """Cancela as OPs recebidas, uma a uma, e relata o que aconteceu com cada uma.

    ⚠️ Chame **somente** depois de `levanta_ops_para_cancelamento` confirmar que não há
    bloqueantes — esta função não repete a verificação, ela executa o que foi decidido.

    Um erro numa OP não interrompe as demais: cancelar 8 de 10 e dizer exatamente quais
    falharam é mais útil do que parar na terceira e deixar o pedido num estado que ninguém
    sabe qual é. O resultado traz `canceladas` e `com_erro` separados.
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


async def limpa_vinculos_do_pedido(
    sl: ServiceLayerClient, hana_reader: HanaDirectReader, doc_entry: str | int
) -> dict:
    """Devolve o pedido ao estado de "não processado", desfazendo o que a integração marcou.

    Duas coisas, **numa única chamada** à Service Layer (aplicando a lição da seção 7.14 —
    agrupar atualizações da mesma entidade):
    - cabeçalho: `U_INO_ProcessWBC = 'N'`;
    - linhas: `U_INO_OP = 0` nas que têm OP vinculada.

    **Os valores não foram escolhidos por conveniência, são os do próprio sistema**: `'N'`
    é o que `_atualiza_doc(..., "Erro")` já grava e o que `BUSCA_PEDIDOS_PARA_INTEGRAR`
    filtra para listar um pedido como pendente; e `0` é como o addon legado zerava esse
    campo (`DocCot.Lines.SetCurrentLine(i); ...U_INO_OP = 0`, portado em
    `_update_tab_pedido`) e como o resto do código lê "sem OP" (`IFNULL(U_INO_OP,0) = 0`).

    Efeito prático: o pedido volta a aparecer em `pedidos-wbc buscar` e pode ser
    reprocessado sem `--force`.

    ⚠️ Chamar somente quando **todas** as OPs do pedido estiverem canceladas. Se alguma
    continuar planejada (ex.: um cancelamento falhou), limpar os vínculos deixaria o pedido
    dizendo "nunca processado" enquanto existem OPs vivas apontando para ele — pior que o
    estado anterior. Quem garante essa condição é o chamador.
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
    """Quantas linhas o pedido tinha em cada versão salva (`ADOC`/`ADO1`). Só leitura.

    Forense retroativa: o B1 guarda uma cópia das linhas a cada atualização do documento.
    Se a contagem cair de uma versão para a seguinte, alguma atualização comeu linhas — e
    dá para ver em qual. Devolve lista vazia quando o log de histórico não está ativo, o
    que não prova nada em nenhuma direção.
    """
    return hana_reader.fetch_all(*ligar(q.HISTORICO_LINHAS_PEDIDO, doc_entry=int(doc_entry)))


def foto_das_linhas(hana_reader: HanaDirectReader, doc_entry: int) -> list[dict]:
    """Fotografia das linhas do pedido, para comparar antes/depois de um PATCH."""
    return hana_reader.fetch_all(*ligar(q.FOTO_LINHAS_PEDIDO, doc_entry=int(doc_entry)))


def compara_fotos(antes: list[dict], depois: list[dict]) -> dict:
    """Diferença entre duas fotos de `RDR1`, por `LineNum`.

    Devolve `{sumiram, surgiram, alteradas}`. `alteradas` lista, por linha, quais campos
    mudaram — com o valor antes e depois, para que a diferença fale por si.
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
    """Reenvia UMA linha com o valor que ela já tem — um PATCH que não deveria mudar nada.

    É o experimento isolado: mesmo formato de corpo que `marca_op_nas_linhas` usa em
    produção (`DocumentLines` com só `LineNum` + um campo), mas escolhendo um valor igual
    ao atual. Assim, tudo o que aparecer na comparação antes/depois é efeito colateral do
    mecanismo, não da alteração pedida.
    """
    await sl.update_entity(
        "Orders",
        int(doc_entry),
        {"DocumentLines": [{"LineNum": int(line_num), "U_INO_OP": int(valor_atual)}]},
    )
