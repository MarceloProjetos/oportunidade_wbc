"""Lógica de negócio do módulo Manutenção de OP — **completo** (17/09/2026).

- 16/09/2026: mudança de status (`muda_status`) — CLI `liberar` / `replanejar`.
- 17/09/2026: busca com filtros (`buscar_ops`) e encerramento com movimentação de estoque
  (`corrige_op` + `saida_insumo` + `entrada_produto` + `finalizar_ops`) — CLI `buscar` e
  `encerrar`.

Referência: `Views/ManutencaoOp.b1f.cs` (ver seção 4.5 do migration_guide.md).
Prioridade 3 na ordem de migração acordada com o usuário.

Referência linha a linha do C# original (`Views/ManutencaoOp.b1f.cs`):

- `buscar_ops`      <- `buscar()` (linha ~99)
- `muda_status`     <- `mudaStatus` + `updateOP` (linhas ~241 e ~278)
- `corrige_op`      <- `corrigeOP` (linha ~364)
- `saida_insumo`    <- `SaidaEnsumo` (linha ~472)
- `entrada_produto` <- `EntradaProduto` (linha ~547)
- `finalizar_ops`   <- `Button4_ClickAfter` (linha ~399)

⚠️ NÃO portar `cancelapedidoteste` (linha ~321) — é código de teste manual do
desenvolvedor original que cancela um range fixo de DocEntry (13302-13309),
sem relação com a lógica de negócio real.
"""
from __future__ import annotations

import logging
from datetime import datetime

from controleproducao.core.hana_reader import HanaDirectReader
from controleproducao.core.service_layer_client import ServiceLayerClient
from controleproducao.modules.manutencao_op import queries as q

logger = logging.getLogger(__name__)

# Códigos de status em `OWOR."Status"`.
STATUS_OP = {"P": "Planejada", "R": "Liberada", "L": "Encerrada", "C": "Cancelada"}

# Status dos quais uma OP não sai mais (22/09/2026). Encerrada já teve a movimentação de
# estoque lançada; cancelada foi descartada. O SAP recusa as duas, mas com mensagem
# obscura — e a tela do legado nunca chegava a oferecê-las, porque a grade filtrava
# (`OPS_MANUTENCAO` exclui `Status = 'C'`).
#
# É a quarta vez no projeto que a proteção que morava na grade precisa virar regra em
# código: reprocessamento (15/09), OP cancelada no encerrar (21/09), pedido fora da lista
# elegível (22/09) e agora esta. Toda camada que aceita um identificador direto perde a
# checagem que a tela fazia por filtragem.
STATUS_TERMINAIS = {"L", "C"}

# Tradução dos códigos do legado (`mudaStatus("l"/"p"/"c"/"f")`) para o valor esperado pela
# Service Layer e para o código correspondente em `OWOR."Status"`. Transcrito de
# `ManutencaoOp.updateOP` (linha ~278), incluindo o `ClosingDate = DateTime.Now` do "f".
TRANSICOES = {
    "l": {"sl": "boposReleased", "owor": "R", "nome": "Liberada"},
    "p": {"sl": "boposPlanned", "owor": "P", "nome": "Planejada"},
    "c": {"sl": "boposCancelled", "owor": "C", "nome": "Cancelada"},
    "f": {"sl": "boposClosed", "owor": "L", "nome": "Encerrada", "fecha": True},
}


# Ordem em que o `between` do filtro de status realmente compara — alfabética nos
# códigos, não a ordem do ciclo de vida (que seria P -> R -> L, com C à parte).
_ORDEM_STATUS = tuple(sorted(STATUS_OP))

# Colunas do `OPS_MANUTENCAO` que a busca devolve, na ordem da grade original.
COLUNAS_BUSCA = [
    "Número OP", "Status", "Cód. Produto", "Produto", "Qtde. Planejada", "Qtde. Apontada",
    "Qtde. Restante", "Data Pedido", "Data inicio", "Data Vencimento", "Cod. Cliente", "Cliente",
]


def _marcadores(quantidade: int) -> str:
    """`?, ?, ...` for an `IN` list — the only text ever formatted into these queries."""
    return ", ".join("?" * quantidade)


def _inteiro(valor: object, rotulo: str) -> int:
    """A SAP number (DocNum/DocEntry) from the CLI or a form, refused unless it is one.

    The original pasted the text into the SQL; with bound parameters that can no longer be
    injected, but a typo would still reach HANA as a conversion error. Refusing here gives
    the message in Portuguese, at the border.
    """
    texto = str(valor if valor is not None else "").strip()
    if not texto.isdigit():
        raise ValueError(f"{rotulo} inválido: {valor!r} (esperado um número).")
    return int(texto)


def _monta_filtro(
    op_de: str | None, op_ate: str | None, status_de: str | None, status_ate: str | None
) -> tuple[str, list]:
    """Reproduz a montagem do `WhereQuery` de `ManutencaoOp.buscar()` (linha ~99).

    Regra do original, preservada: informar só o limite inferior vira igualdade (`=`),
    e informar os dois vira `between`. O limite superior sozinho é ignorado — no legado
    porque o `if` externo testa só o inferior; aqui isso vira um erro explícito, já que
    numa CLI o usuário não tem a tela para perceber que o filtro não foi aplicado.

    Returns ``(fragment, params)``: since F7 (28/09/2026) the values are bound parameters.
    """
    if op_ate and not op_de:
        raise ValueError("--op-ate exige --op-de (o legado ignora silenciosamente esse caso).")
    if status_ate and not status_de:
        raise ValueError("--status-ate exige --status-de.")

    partes: list[str] = []
    params: list = []
    if op_de:
        de_op = _inteiro(op_de, "--op-de")
        ate_op = _inteiro(op_ate, "--op-ate") if op_ate else None
        if ate_op is not None and ate_op < de_op:
            raise ValueError(
                f"Faixa de OP invertida: --op-de {op_de} é maior que --op-ate {op_ate}. "
                "O `between` devolveria zero linhas."
            )
        if ate_op is not None:
            partes.append('(T1."DocNum" BETWEEN ? AND ?)')
            params += [de_op, ate_op]
        else:
            partes.append('(T1."DocNum" = ?)')
            params.append(de_op)
    if status_de:
        de = _valida_status(status_de)
        if status_ate:
            ate = _valida_status(status_ate)
            # O `between` do original compara os códigos como TEXTO, então a faixa é
            # alfabética (C < L < P < R) e não tem relação com o ciclo de vida da OP.
            # Pedir "de Planejada até Cancelada" parece natural e devolve ZERO linhas,
            # indistinguível de "este pedido não tem OP". Aconteceu no primeiro uso real
            # (21/09/2026) — daí o erro explícito em vez do vazio silencioso.
            if ate < de:
                raise ValueError(
                    f"Faixa de status invertida: '{de}' vem depois de '{ate}' na ordem "
                    f"alfabética usada pelo filtro ({' < '.join(_ORDEM_STATUS)}), então o "
                    "`between` devolveria zero linhas.\n"
                    f"Para essa faixa, inverta: --status-de {ate} --status-ate {de}. "
                    "Para todos os status, omita os dois filtros."
                )
            partes.append('(T1."Status" BETWEEN ? AND ?)')
            params += [de, ate]
        else:
            partes.append('(T1."Status" = ?)')
            params.append(de)
    return "".join(f" AND {parte}" for parte in partes), params


def _valida_status(codigo: str) -> str:
    """Impede injeção no literal de status e erros de digitação silenciosos.

    O legado interpolava o conteúdo da caixa de texto direto na query. Como aqui a origem
    é a linha de comando, o valor é restrito aos códigos reais de `OWOR."Status"`.
    """
    codigo = (codigo or "").strip().upper()
    if codigo not in STATUS_OP:
        raise ValueError(
            f"Status inválido: {codigo!r}. Use um de {', '.join(f'{k} ({v})' for k, v in STATUS_OP.items())}."
        )
    return codigo


def buscar_ops(
    hana_reader: HanaDirectReader,
    doc_num: str,
    op_de: str | None = None,
    op_ate: str | None = None,
    status_de: str | None = None,
    status_ate: str | None = None,
) -> list[dict]:
    """`ManutencaoOp.buscar()` (linha ~99) — OPs de um pedido, com os filtros da tela.

    O pedido é obrigatório, como no original ("O número do pedido é obrigatório."), e a
    ordenação final é `DocNum` decrescente. Somente leitura: é a consulta que alimenta as
    demais ações do módulo.

    Leitura por `hdbcli` e não pela Service Layer: a query tem três joins e aliases de
    exibição — exatamente o caso que a decisão de 14/09 reserva ao acesso direto (seção 6.4
    do guia). A assinatura antiga recebia um `ServiceLayerClient`; foi trocada por isso.
    """
    if not str(doc_num or "").strip():
        raise ValueError("O número do pedido é obrigatório.")

    pedido = _inteiro(doc_num, "Número do pedido")
    filtro, params_filtro = _monta_filtro(op_de, op_ate, status_de, status_ate)
    sql = q.OPS_MANUTENCAO + filtro + ' ORDER BY T1."DocNum" DESC'

    linhas = hana_reader.fetch_all(sql, (pedido, *params_filtro))
    # A coluna "Selecionar" existia só para a caixa de seleção da grade — não faz sentido
    # na CLI, onde a seleção é o próprio argumento do comando.
    return [
        {**{coluna: linha.get(coluna) for coluna in COLUNAS_BUSCA},
         "Status (descrição)": STATUS_OP.get(str(linha.get("Status") or ""), linha.get("Status"))}
        for linha in linhas
    ]


def levanta_ops(
    hana_reader: HanaDirectReader,
    op_docnums: list[str] | None = None,
    doc_num_pedido: str | None = None,
) -> list[dict]:
    """Lê as OPs alvo com o status atual — por número de OP, ou todas de um pedido.

    Substitui a grade da tela original: no legado o usuário via os status antes de clicar
    em Liberar/Planejar, e é essa informação que permite decidir. Só leitura.
    """
    if op_docnums:
        numeros = list(dict.fromkeys(_inteiro(n, "Número da OP") for n in op_docnums))
        rows = hana_reader.fetch_all(
            q.OPS_POR_DOCNUM.format(marcadores=_marcadores(len(numeros))), tuple(numeros)
        )
    elif doc_num_pedido:
        rows = hana_reader.fetch_all(q.OPS_POR_PEDIDO, (_inteiro(doc_num_pedido, "Número do pedido"),))
    else:
        raise ValueError("Informe os números das OPs ou o pedido.")

    return [
        {
            "doc_entry": int(row["DocEntry"]),
            "doc_num": row["DocNum"],
            "status": str(row["Status"] or ""),
            "item_code": str(row["ItemCode"] or ""),
            "planejada": float(row["PlannedQty"] or 0),
            "apontada": float(row["CmpltQty"] or 0),
            "pedido": row.get("OriginNum"),
        }
        for row in rows
    ]


async def muda_status(sl: ServiceLayerClient, ops: list[dict], status: str) -> dict:
    """`ManutencaoOp.mudaStatus` + `updateOP` — muda o status das OPs informadas.

    `status` usa os códigos do legado: `"l"` liberar, `"p"` planejar, `"c"` cancelar,
    `"f"` encerrar.

    ⚠️ **`"f"` não deve ser chamado isoladamente.** No legado ele é o último passo do
    encerramento (`Button4_ClickAfter`), depois de `corrigeOP` + `SaidaEnsumo` +
    `EntradaProduto` — fechar a OP sem a movimentação de estoque deixa o apontamento
    inconsistente. Está no mapa porque `finalizar_ops` vai precisar dele, não para uso
    avulso; a CLI expõe apenas liberar e replanejar.

    Diferença consciente em relação ao C#: lá cada OP era atualizada e os erros iam só para
    o `INO_LOG`/status bar, sem o usuário saber quais deram certo. Aqui o resultado separa
    `alteradas` de `com_erro`, e um erro numa OP não interrompe as demais — mesmo critério
    adotado no `cancelar-ops` do módulo 2.
    """
    transicao = TRANSICOES.get(status)
    if not transicao:
        raise ValueError(f"Status inválido: {status!r}. Use um de {sorted(TRANSICOES)}.")

    campos = {"ProductionOrderStatus": transicao["sl"]}
    if transicao.get("fecha"):
        # `OrdemPrducao.ClosingDate = DateTime.Now` do `updateOP` original.
        campos["ClosingDate"] = datetime.now().strftime("%Y-%m-%d")

    alteradas: list[dict] = []
    com_erro: list[dict] = []
    ignoradas: list[dict] = []

    for op in ops:
        # Terminal não vira nada: recusa aqui, ANTES da Service Layer. A tela desabilita
        # a caixa dessas linhas, mas a tela é sugestão — o número chega por POST e pode
        # ser qualquer um. Vai para `ignoradas`, não `com_erro`: não é falha, é recusa.
        if op.get("status") in STATUS_TERMINAIS:
            motivo = (
                f"OP {op['doc_num']} está {STATUS_OP.get(op['status'], op['status'])} — "
                "status terminal, não admite mudança."
            )
            ignoradas.append({**op, "motivo": motivo})
            logger.info("  %s", motivo)
            continue
        try:
            await sl.update_entity("ProductionOrders", int(op["doc_entry"]), campos)
            alteradas.append(op)
            logger.info(
                "  OP %s (item %s): %s -> %s.",
                op["doc_num"], op["item_code"],
                STATUS_OP.get(op["status"], op["status"]), transicao["nome"],
            )
        except Exception as exc:  # noqa: BLE001 - segue para as demais, mas registra
            com_erro.append({**op, "motivo": str(exc)})
            logger.error("  Falha ao alterar a OP %s: %s", op["doc_num"], exc)

    return {"alteradas": alteradas, "com_erro": com_erro, "ignoradas": ignoradas,
            "destino": transicao}


# Tipo de objeto da Ordem de Produção no SAP B1 (`oProductionOrders`). A DI API inferia o
# `BaseType` a partir do objeto; em REST ele precisa ser dito. Mesma classe de divergência
# já documentada nas seções 7.9 e 7.10 do guia.
TIPO_ORDEM_PRODUCAO = 202

# `ObjectCode` em `NNM1`, para descobrir a série de numeração de cada documento.
TIPO_OBJETO_SAIDA_MERCADORIA = "60"    # OIGE / InventoryGenExits
TIPO_OBJETO_ENTRADA_MERCADORIA = "59"  # OIGN / InventoryGenEntries


def _filial_do_movimento(
    hana_reader: HanaDirectReader, doc_entry: int, filial_configurada: int = 0
) -> int:
    """Descobre a filial (`BPLId`) a usar na saída e na entrada de mercadoria da OP.

    A Service Layer recusa os dois documentos sem esse campo quando a empresa usa filiais
    (`Specify an active branch [OIGE.BPLId]`). O C# não informa `BPLId` em lugar nenhum
    porque a DI API o preenchia a partir da filial padrão do usuário logado — não há
    equivalente a portar, é comportamento a inventar. Descoberto em 21/09/2026, no
    primeiro `encerrar` real (seção 7.27 do guia).

    **Deriva do dado, não de configuração.** A ordem de preferência:

    1. **Filial do depósito da OP** — um lançamento de estoque acontece na filial do
       depósito, e o próprio B1 exige essa coerência. É o candidato mais correto.
    2. **Filial dos depósitos dos componentes** — cobre a OP cujo depósito de produto não
       tem filial definida. Usa o menor `BPLId` entre eles.
    3. **Filial do pedido de origem** — vínculo mais fraco (uma OP pode atender pedido de
       outra filial), por isso vem depois.
    4. `SL_BUSINESS_PLACE_ID` do `.env` — último recurso, e de propósito: um número fixo
       em configuração quebra em silêncio no dia em que a segunda filial for criada.

    Cada candidato é conferido contra as filiais **ativas** (`OBPL."Disabled" <> 'Y'`), e
    não só contra as existentes: a mensagem do SAP fala em filial ativa, e uma empresa pode
    ter filial cadastrada e desabilitada (é o caso aqui — a filial 2 está desabilitada).
    Melhor falhar com um motivo legível antes de postar do que receber a recusa do SAP.
    """
    ativas = {
        int(linha["BPLId"])
        for linha in hana_reader.fetch_all(q.FILIAIS_ATIVAS)
        if linha.get("BPLId") is not None
    }
    if not ativas:
        raise ValueError(
            "Nenhuma filial ativa em OBPL — a Service Layer vai recusar qualquer "
            "lançamento de estoque. Verifique o cadastro de filiais."
        )

    linhas = hana_reader.fetch_all(q.FILIAL_CANDIDATA_DA_OP, (int(doc_entry),))
    origem = linhas[0] if linhas else {}

    candidatos = [
        ("depósito da OP", int(origem.get("filial_deposito_op") or 0)),
        ("depósitos dos componentes", int(origem.get("filial_componentes") or 0)),
        ("pedido de origem", int(origem.get("filial_pedido") or 0)),
        ("SL_BUSINESS_PLACE_ID do .env", int(filial_configurada or 0)),
    ]

    for rotulo, candidato in candidatos:
        if candidato and candidato in ativas:
            # "DocEntry" explícito: em toda a CLI "OP <n>" significa DocNum, e imprimir o
            # DocEntry com o mesmo rótulo fez a mensagem parecer estar falando de outra OP.
            logger.info(
                "  OP DocEntry=%s: filial %s (origem: %s).", doc_entry, candidato, rotulo
            )
            return candidato

    encontrados = [f"{rotulo}={valor}" for rotulo, valor in candidatos if valor]
    raise ValueError(
        f"Não foi possível determinar uma filial ATIVA para a OP {doc_entry}.\n"
        f"Candidatos encontrados: {', '.join(encontrados) or 'nenhum'}.\n"
        f"Filiais ativas: {sorted(ativas)}.\n"
        "Defina SL_BUSINESS_PLACE_ID no .env ou corrija a filial do depósito."
    )


def _serie_do_documento(
    hana_reader: HanaDirectReader, object_code: str, filial: int
) -> int:
    """Descobre a série de numeração (`Series`) do documento, para a filial informada.

    Segunda metade da lacuna do `BPLId` (seção 7.27). Informar só a filial **não basta**:
    a Service Layer continua respondendo `Specify an active branch` até receber também a
    `Series`. A DI API escolhia a série padrão do usuário logado — de novo, não há o que
    portar do C#, é comportamento a inventar.

    Confirmado no ambiente da Altamira: as séries não são por filial (`NNM1."BPLId"` nulo)
    e os documentos existentes em `OIGE` têm `Series = 20` com `BPLId = 1`. A precedência
    abaixo cobre os dois arranjos, porque uma empresa pode passar a ter séries por filial
    sem avisar ninguém:

    1. Série cadastrada **para aquela filial**;
    2. Série **sem filial**, que serve todas;

    nunca uma série bloqueada (`Locked = 'Y'`), e o empate é resolvido pelo menor número
    para a escolha ser determinística — uma série diferente a cada execução espalharia a
    numeração dos documentos sem que ninguém entendesse por quê.
    """
    linhas = hana_reader.fetch_all(q.SERIE_DO_DOCUMENTO, (str(object_code), int(filial)))
    if not linhas:
        raise ValueError(
            f"Nenhuma série de numeração ativa para o tipo de documento {object_code} "
            f"na filial {filial} (NNM1). A Service Layer recusa o lançamento sem série."
        )

    escolhida = linhas[0]
    logger.info(
        "  Documento tipo %s: série %s (%s), filial %s.",
        object_code, escolhida["Series"], escolhida.get("SeriesName") or "", filial,
    )
    return int(escolhida["Series"])


async def corrige_op(
    sl: ServiceLayerClient, doc_entry: int, status_atual: str = ""
) -> bool:
    """`ManutencaoOp.corrigeOP` (linha ~364) — prepara a OP para o apontamento.

    **Regra de negócio (informada pelo Anderson em 21/09/2026): uma OP só pode ser
    apontada estando LIBERADA.** Encerrar uma OP envolve apontar produção — saída de
    insumo e entrada de produto —, então uma OP Planejada precisa ser liberada antes. É
    essa a razão de o `corrigeOP` do legado liberar a OP; até aqui o guia registrava só a
    consequência técnica (sem `im_Manual` o SAP tenta backflush e recusa a saída manual),
    sem dizer a regra que a motiva.

    Faz duas coisas num único PATCH:

    1. **Libera a OP**, se ela ainda não estiver liberada. Com `status_atual == "R"` o
       campo de status não é enviado: a OP já está onde precisa, e reenviar o mesmo status
       é uma escrita sem efeito.
    2. Força `ProductionOrderIssueType = Manual` em **todas** as linhas — isso vai sempre,
       independente do status, porque é o que permite a baixa manual dos insumos.

    Devolve `True` quando a liberação foi incluída no PATCH, para quem chama poder relatar
    a transição de verdade em vez de supor.

    Usa PATCH parcial das linhas (só `LineNumber` + o campo alterado), pelo motivo
    registrado na seção 7.11 do guia: reenviar a coleção inteira derruba campos que a
    Service Layer devolve vazios mas não aceita de volta.
    """
    op = await sl.get_by_key("ProductionOrders", doc_entry)
    linhas = op.get("ProductionOrderLines") or []

    precisa_liberar = str(status_atual or "").upper() != TRANSICOES["l"]["owor"]

    campos: dict = {}
    if precisa_liberar:
        campos["ProductionOrderStatus"] = TRANSICOES["l"]["sl"]
        logger.info(
            "  OP DocEntry=%s: liberando antes do apontamento (uma OP só pode ser "
            "apontada estando liberada).", doc_entry,
        )
    if linhas:
        campos["ProductionOrderLines"] = [
            {"LineNumber": linha["LineNumber"], "ProductionOrderIssueType": "im_Manual"}
            for linha in linhas
        ]

    if campos:
        await sl.update_entity("ProductionOrders", doc_entry, campos)
    return precisa_liberar


async def saida_insumo(
    sl: ServiceLayerClient, hana_reader: HanaDirectReader, doc_entry: int,
    filial: int, serie: int,
) -> dict:
    """`ManutencaoOp.SaidaEnsumo` (linha ~472) — Saída de Mercadoria dos componentes.

    Cria um `InventoryGenExits` com uma linha por componente, apontando para a OP
    (`BaseType`/`BaseEntry`/`BaseLine`). Item, quantidade e depósito NÃO são informados: o
    SAP os copia do documento base, exatamente como no original (que tinha essas atribuições
    comentadas).

    Duas correções conscientes em relação ao C#:

    1. `BaseLine` usa o `LineNum` real da linha da OP, e não o índice da linha no recordset
       (ver o comentário em `queries.LINHAS_FALTANTES_OP`).
    2. Linhas já totalmente baixadas (`PlannedQty - IssuedQty <= 0`) são puladas. O legado as
       incluía e deixava o SAP recusar ou lançar quantidade zero.
    """
    linhas_op = hana_reader.fetch_all(q.LINHAS_FALTANTES_OP, (int(doc_entry),))
    pendentes = [linha for linha in linhas_op if float(linha["Faltante"] or 0) > 0]

    if not pendentes:
        logger.info("  OP %s: nenhum insumo pendente de baixa — saída não é necessária.", doc_entry)
        return {}

    corpo = {
        # `BPL_IDAssignedToInvoice` + `Series`: a Service Layer exige os DOIS quando a
        # empresa usa filiais.
        #
        # ⚠️ O nome do campo da filial é `BPL_IDAssignedToInvoice`, NÃO `BPLId` — a
        # entidade `Documents` não tem `BPLId` nenhum. Descoberto em 21/09/2026 lendo um
        # documento real com `diag entidade InventoryGenExits --campos`, depois de duas
        # tentativas erradas com `BPLId`. A armadilha: a Service Layer **ignora em
        # silêncio** propriedade desconhecida no POST, então enviar `BPLId` não dá erro de
        # propriedade inválida — dá o erro de filial ausente, que aponta para outro lugar.
        # Ver seção 7.29 do guia.
        "BPL_IDAssignedToInvoice": int(filial),
        "Series": int(serie),
        "DocumentLines": [
            {
                "BaseType": TIPO_ORDEM_PRODUCAO,
                "BaseEntry": int(doc_entry),
                "BaseLine": int(linha["LineNum"]),
            }
            for linha in pendentes
        ],
    }
    # O corpo enviado fica no log de propósito, e não só durante a investigação: este é
    # um lançamento de estoque irreversível, e registrar exatamente o que foi mandado é o
    # mínimo para auditar depois. Foi também o que separou "valor calculado" de "valor
    # enviado" quando o campo da filial estava com o nome errado (21/09/2026).
    logger.info("  Corpo da saída de insumo: %s", corpo)
    return await sl.create_entity("InventoryGenExits", corpo)


async def entrada_produto(
    sl: ServiceLayerClient, doc_entry: int, filial: int, serie: int
) -> dict:
    """`ManutencaoOp.EntradaProduto` (linha ~547) — Entrada de Mercadoria do produto.

    Uma única linha apontando para a OP; o SAP traz o item, a quantidade restante e o
    depósito do documento base.
    """
    corpo = {
        # Mesmo campo da saída (`InventoryGenEntries` é a mesma entidade `Documents`).
        "BPL_IDAssignedToInvoice": int(filial),
        "Series": int(serie),
        "DocumentLines": [
            {"BaseType": TIPO_ORDEM_PRODUCAO, "BaseEntry": int(doc_entry)}
        ],
    }
    logger.info("  Corpo da entrada de produto: %s", corpo)
    return await sl.create_entity("InventoryGenEntries", corpo)


def _componentes_por_op(
    hana_reader: HanaDirectReader, doc_entries: list[int]
) -> dict[int, set[str]]:
    """Itens que cada OP consome, para montar a hierarquia."""
    if not doc_entries:
        return {}
    unicos = list(dict.fromkeys(int(d) for d in doc_entries))
    por_op: dict[int, set[str]] = {int(d): set() for d in doc_entries}
    sql = q.COMPONENTES_DAS_OPS.format(marcadores=_marcadores(len(unicos)))
    for linha in hana_reader.fetch_all(sql, tuple(unicos)):
        por_op.setdefault(int(linha["DocEntry"]), set()).add(str(linha["ItemCode"] or ""))
    return por_op


def ordena_por_dependencia(
    ops: list[dict], componentes: dict[int, set[str]]
) -> tuple[list[dict], list[dict]]:
    """Ordena as OPs de baixo para cima: filha antes da mãe.

    **A razão é física, não estética.** A saída de insumo de uma OP pai consome o item que
    a OP filha produz. Encerrada a mãe primeiro, o produto da filha ainda não existe em
    estoque e o SAP recusa a saída — ou, pior, consome saldo de outra origem.

    A hierarquia é descoberta **pelo item**: se o item produzido por B aparece como
    componente de A, B vem antes de A. O B1 não tem campo de "OP pai" para esta cascata.
    Itens produzidos por mais de uma OP (o `PAR000PADRA000000000` aparece 13 vezes no
    pedido 84348) funcionam naturalmente: todas as OPs que produzem o item precedem todas
    as que o consomem.

    Devolve `(ordenadas, em_ciclo)`. OPs num ciclo de dependência ficam **fora** da lista
    ordenada em vez de entrarem numa ordem arbitrária: numa operação irreversível de
    estoque, chutar a ordem é pior que recusar. Um ciclo não deveria existir numa árvore de
    estrutura, mas dado ruim existe.
    """
    por_entry = {int(op["doc_entry"]): op for op in ops}

    # Quem produz cada item, entre as OPs em jogo.
    produtoras: dict[str, set[int]] = {}
    for entry, op in por_entry.items():
        produtoras.setdefault(str(op["item_code"] or ""), set()).add(entry)

    # `precisa_de[A]` = OPs que precisam ser encerradas antes de A.
    precisa_de: dict[int, set[int]] = {entry: set() for entry in por_entry}
    for entry in por_entry:
        for item in componentes.get(entry, set()):
            for produtora in produtoras.get(item, set()):
                if produtora != entry:  # item que a própria OP produz e consome: ignorado
                    precisa_de[entry].add(produtora)

    # Kahn, com desempate pelo DocNum para a ordem ser reproduzível entre execuções.
    pendentes = dict(precisa_de)
    ordenadas: list[dict] = []
    while True:
        prontas = sorted(
            (entry for entry, faltam in pendentes.items() if not faltam),
            key=lambda entry: (str(por_entry[entry]["doc_num"]), entry),
        )
        if not prontas:
            break
        for entry in prontas:
            ordenadas.append(por_entry[entry])
            del pendentes[entry]
        for faltam in pendentes.values():
            faltam.difference_update(prontas)

    em_ciclo = [por_entry[entry] for entry in sorted(pendentes)]
    return ordenadas, em_ciclo


def dependentes_transitivos(
    ops: list[dict], componentes: dict[int, set[str]]
) -> dict[int, set[int]]:
    """`{doc_entry: OPs que dependem dela, direta ou indiretamente}`.

    Serve para, quando uma OP falha, pular as que a consomem em vez de tentá-las e colher
    uma cascata de "sem estoque" que esconde a causa real.
    """
    por_entry = {int(op["doc_entry"]): op for op in ops}
    produtoras: dict[str, set[int]] = {}
    for entry, op in por_entry.items():
        produtoras.setdefault(str(op["item_code"] or ""), set()).add(entry)

    # Aresta filha -> mãe.
    consumidoras: dict[int, set[int]] = {entry: set() for entry in por_entry}
    for entry in por_entry:
        for item in componentes.get(entry, set()):
            for produtora in produtoras.get(item, set()):
                if produtora != entry:
                    consumidoras[produtora].add(entry)

    def alcancaveis(inicio: int) -> set[int]:
        vistos: set[int] = set()
        pilha = list(consumidoras.get(inicio, set()))
        while pilha:
            atual = pilha.pop()
            if atual in vistos:
                continue
            vistos.add(atual)
            pilha.extend(consumidoras.get(atual, set()))
        return vistos

    return {entry: alcancaveis(entry) for entry in por_entry}


async def finalizar_ops(
    sl: ServiceLayerClient, hana_reader: HanaDirectReader, ops: list[dict],
    filial_configurada: int = 0, dependentes: dict[int, set[int]] | None = None,
) -> dict:
    """`ManutencaoOp.Button4_ClickAfter` (linha ~399) — encerramento com movimentação.

    Para cada OP, na ordem do original: `corrige_op` -> `saida_insumo` -> `entrada_produto`
    -> status Encerrada. Só entram OPs com `apontada < planejada`, precondição do legado.

    ⚠️ **Irreversível.** A saída e a entrada de mercadoria são lançamentos de estoque; não
    há desfazer. Quem chama é responsável por confirmar com o usuário antes.

    Divergências conscientes em relação ao C#:

    - O legado chamava `mudaStatus("f")` uma vez no fim, sobre TODAS as linhas marcadas,
      inclusive as cujo `corrigeOP`/`SaidaEnsumo` tinha falhado — fechando OPs sem a
      movimentação correspondente. Aqui cada OP só é encerrada se a sua própria cadeia
      tiver ido até o fim.
    - O legado resolvia o `DocEntry` a partir do `DocNum` com a query `OPDE` a cada linha;
      aqui o `DocEntry` já vem do levantamento, então essa ida ao banco desaparece.
    - Erros iam apenas para a status bar e o `INO_LOG`; aqui o resultado diz o que foi e o
      que não foi feito, no mesmo formato de `muda_status`.
    """
    finalizadas: list[dict] = []
    com_erro: list[dict] = []
    ignoradas: list[dict] = []
    puladas: list[dict] = []

    # OPs a pular porque uma OP da qual dependem falhou. Tentá-las produziria uma cascata
    # de "sem estoque" que esconde a causa real — a falha lá embaixo.
    dependentes = dependentes or {}
    bloqueadas: dict[int, int] = {}  # doc_entry -> doc_entry que falhou

    async def _desfaz_liberacao(op_alvo: dict, doc_entry_alvo: int) -> str:
        """Devolve a OP para Planejada quando a cadeia falhou sem lançar nada.

        Só é chamada no caso em que isso é seguro: a OP foi liberada por `corrige_op` e a
        saída de insumo **não** foi criada. Se a saída já tivesse sido lançada, reverter o
        status deixaria estoque movimentado numa OP Planejada — pior que o estado atual.

        Existe porque nos testes de 21/09 cinco falhas seguidas (filial, série, nome de
        campo, custo de item) deixaram a OP Liberada, e desfazer isso virou trabalho
        manual repetido. Melhor esforço: se a reversão falhar, ela é relatada e o erro
        original continua sendo o principal.
        """
        try:
            await sl.update_entity(
                "ProductionOrders", doc_entry_alvo,
                {"ProductionOrderStatus": TRANSICOES["p"]["sl"]},
            )
            logger.info(
                "  OP %s devolvida para Planejada (nada foi lançado).", op_alvo["doc_num"]
            )
            return "desfeita"
        except Exception as exc:  # noqa: BLE001 - não pode esconder o erro original
            logger.error(
                "  OP %s: falha ao devolver para Planejada — ela ficou Liberada: %s",
                op_alvo["doc_num"], exc,
            )
            return f"falhou ({exc})"

    for op in ops:
        doc_entry_atual = int(op["doc_entry"])
        if doc_entry_atual in bloqueadas:
            culpada = bloqueadas[doc_entry_atual]
            puladas.append({
                **op,
                "motivo": f"depende da OP DocEntry={culpada}, que falhou — encerrá-la "
                          "agora falharia por falta do insumo que ela produz",
            })
            logger.warning(
                "  OP %s pulada: depende da OP DocEntry=%s, que falhou.",
                op["doc_num"], culpada,
            )
            continue

        if float(op["apontada"]) >= float(op["planejada"]):
            # `if (qtdAD < qtdPD)` do original: nada a apontar, nada a movimentar.
            ignoradas.append({**op, "motivo": "quantidade apontada já atingiu a planejada"})
            continue

        doc_entry = int(op["doc_entry"])
        # A filial é resolvida ANTES de qualquer escrita: se não houver filial ativa
        # determinável, a OP nem é liberada — falhar antes de mexer em nada é melhor do que
        # deixá-la Liberada esperando um conserto de configuração. E é resolvida uma única
        # vez, para a saída e a entrada saírem na MESMA filial.
        etapa = "determinar filial e séries"
        liberou = False
        saida_lancada = False
        try:
            filial = _filial_do_movimento(hana_reader, doc_entry, filial_configurada)
            # As DUAS séries são resolvidas aqui, antes de qualquer escrita. Descobrir que
            # falta a série da ENTRADA depois de a saída já estar lançada deixaria o pior
            # estado possível: baixa de insumo sem a entrada correspondente, que só se
            # desfaz cancelando o documento no SAP à mão.
            serie_saida = _serie_do_documento(
                hana_reader, TIPO_OBJETO_SAIDA_MERCADORIA, filial
            )
            serie_entrada = _serie_do_documento(
                hana_reader, TIPO_OBJETO_ENTRADA_MERCADORIA, filial
            )

            etapa = "liberar a OP para apontamento"
            liberou = await corrige_op(sl, doc_entry, op["status"])
            # Depois daqui a OP está liberada, então o status lido no levantamento ficou
            # velho. Sem atualizar, o log fecha com "Planejada -> Encerrada" e esconde a
            # passagem por Liberada — que é justamente o estado que sobra quando a cadeia
            # falha depois deste ponto.
            op = {**op, "status": TRANSICOES["l"]["owor"], "foi_liberada": liberou}

            etapa = "saída de insumo"
            saida = await saida_insumo(sl, hana_reader, doc_entry, filial, serie_saida)
            saida_lancada = True

            etapa = "entrada de produto"
            entrada = await entrada_produto(sl, doc_entry, filial, serie_entrada)

            etapa = "encerrar OP"
            fechamento = await muda_status(sl, [op], "f")
            if fechamento["com_erro"]:
                raise RuntimeError(fechamento["com_erro"][0]["motivo"])

            finalizadas.append({
                **op,
                "saida_docentry": saida.get("DocEntry") if saida else None,
                "entrada_docentry": entrada.get("DocEntry"),
            })
            logger.info("  OP %s encerrada com movimentação de estoque.", op["doc_num"])
        except Exception as exc:  # noqa: BLE001 - uma OP com erro não impede as demais
            falha = {**op, "etapa": etapa, "motivo": str(exc)}
            # Reverter a liberação só é seguro se nada foi lançado. Com a saída já criada,
            # devolver a OP para Planejada deixaria estoque movimentado numa OP planejada.
            if liberou and not saida_lancada:
                falha["liberacao"] = await _desfaz_liberacao(op, doc_entry)
            elif liberou:
                falha["liberacao"] = "mantida (saída já lançada)"
            com_erro.append(falha)
            logger.error("  OP %s falhou em '%s': %s", op["doc_num"], etapa, exc)
            # Tudo que consome o que esta OP produz fica inalcançável.
            for dependente in dependentes.get(doc_entry, set()):
                bloqueadas.setdefault(int(dependente), doc_entry)

    return {
        "finalizadas": finalizadas,
        "com_erro": com_erro,
        "ignoradas": ignoradas,
        "puladas": puladas,
    }
