"""Business logic of Manutenção de OP: status changes, search and closing with stock movement.

Port of `Views/ManutencaoOp.b1f.cs`; CLI commands `buscar`, `liberar`, `replanejar`, `encerrar`
(see section 4.5 of migration_guide.md).

Mapping to the legacy C# (`Views/ManutencaoOp.b1f.cs`):

- `buscar_ops`      <- `buscar()` (line ~99)
- `muda_status`     <- `mudaStatus` + `updateOP` (lines ~241 and ~278)
- `corrige_op`      <- `corrigeOP` (line ~364)
- `saida_insumo`    <- `SaidaEnsumo` (line ~472)
- `entrada_produto` <- `EntradaProduto` (line ~547)
- `finalizar_ops`   <- `Button4_ClickAfter` (line ~399)

WARNING: do NOT port `cancelapedidoteste` (line ~321). It is the original developer's manual
test code that cancels a fixed DocEntry range (13302-13309), unrelated to the business logic.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
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
    """Build the optional filter of `OPS_MANUTENCAO`, as the legacy `WhereQuery` of `buscar()` (line ~99).

    Kept from the original: only a lower bound becomes equality (`=`), both become `BETWEEN`.
    An upper bound alone is ignored by the legacy (its outer `if` tests only the lower one); here
    it is an explicit ValueError, because a CLI user has no screen to notice the filter was not
    applied. Inverted ranges raise too (status ranges compare the codes alphabetically).

    Returns:
        ``(fragment, params)``; since F7 (28/09/2026) the values are bound parameters.
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
    """Normalise a status code and refuse anything that is not a real `OWOR."Status"` value.

    Not about injection any more (values are bound parameters): it turns a typo into a readable
    error instead of an empty result. Returns the upper-case code.
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
    """`ManutencaoOp.buscar()` (line ~99): the OPs of an order, with the screen's filters.

    The order number is required ("O número do pedido é obrigatório."); the result is ordered by
    `DocNum` descending. Read-only; it feeds the other actions of the module. Each row also
    carries "Baixada" (issued quantity; None when the row lacks it).

    Read through `hdbcli`, not the Service Layer: three joins and display aliases are the case the
    14/09 decision reserves for direct access (guide section 6.4). The old signature took a
    `ServiceLayerClient`; it was replaced for that reason.

    Raises:
        ValueError: empty or non-numeric order, or an invalid/inverted filter.
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
         "Status (descrição)": STATUS_OP.get(str(linha.get("Status") or ""), linha.get("Status")),
         # Issued quantity (the Replanejar rule); None when the row lacks it = unknown.
         "Baixada": float(linha["Baixada"] or 0) if "Baixada" in linha else None}
        for linha in linhas
    ]


def levanta_ops(
    hana_reader: HanaDirectReader,
    op_docnums: list[str] | None = None,
    doc_num_pedido: str | None = None,
) -> list[dict]:
    """Read the target OPs with their current status: by OP number, or all OPs of an order.

    Replaces the legacy grid, where the user saw the statuses before clicking Liberar/Planejar.
    Read-only. The lookup by order leaves out Cancelada OPs; the lookup by number does not.
    ``baixada`` is None when the row did not carry it (the Replanejar guard reads it as unknown).

    Raises:
        ValueError: a non-numeric number, or neither argument given.
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
            # Issued quantity (29/09/2026): None when the row did not carry it, which the
            # Replanejar guard reads as "unknown" and refuses.
            "baixada": float(row["Baixada"] or 0) if "Baixada" in row else None,
        }
        for row in rows
    ]


def saida_lancada(op: dict) -> bool:
    """True when the OP's material issue was posted — or when that is unknown (fail-closed).

    The Replanejar rule (29/09/2026, F6 of PLANO_API_MANUTENCAO_OP.md (removed 2026-10-06)): an OP whose
    components were already issued cannot go back to Planejada, or the stock movement would
    sit on a planned OP. The issue must be cancelled in the SAP first.
    """
    baixada = op.get("baixada")
    return baixada is None or float(baixada) > 0


def entrada_lancada(op: dict) -> bool:
    """True when product was already received on the OP (``apontada`` > 0) — or unknown.

    D6 of PLANO_API_MANUTENCAO_OP.md (removed 2026-10-06) (Marcelo, 29/09/2026): the same problem as the
    material issue — a receipt from production would sit on a planned OP. The receipt must be
    cancelled in the SAP first. Checked after ``saida_lancada``, so an OP with both is
    reported for its issue.
    """
    apontada = op.get("apontada")
    return apontada is None or float(apontada) > 0


#: Why a Liberada OP cannot go back to Planejada: short label (grids, CLI table) and what
#: unblocks it. Keys are also the JSON API's refusal `tipo`.
IMPEDIMENTOS_REPLANEJAR = {
    "saida_lancada": ("insumo baixado", "cancele a saída no SAP antes"),
    "entrada_lancada": ("produto apontado", "cancele a entrada no SAP antes"),
}


def impedimento_replanejar(op: dict) -> str | None:
    """``None`` when the OP may go back to Planejada, else a key of ``IMPEDIMENTOS_REPLANEJAR``.

    The one reading of the rule (30/09/2026) — the screen grid, the JSON API, the CLI and
    ``muda_status`` all ask here, so the order (issue first: an OP with both is reported for
    it) and the fail-closed reading of an unknown quantity cannot drift apart.
    """
    if saida_lancada(op):
        return "saida_lancada"
    if entrada_lancada(op):
        return "entrada_lancada"
    return None


def rotulo_impedimento(op: dict, codigo: str) -> str:
    """Short label of an impediment; an unknown quantity says so instead of guessing."""
    if codigo == "saida_lancada" and op.get("baixada") is None:
        return "não foi possível conferir o insumo"
    if codigo == "entrada_lancada" and op.get("apontada") is None:
        return "não foi possível conferir o produto apontado"
    return IMPEDIMENTOS_REPLANEJAR[codigo][0]


def classifica_encerramento(status: str, planejada: float, apontada: float) -> tuple[str, bool]:
    """What closing does to one OP: ``(label shown in the plan, whether it is processed)``.

    The one reading of "can this OP be closed", shared by the plan of the screen, the plan of
    the API and ``acoes_possiveis`` (29/09/2026) — it lived inline in the screen's route.
    """
    status = str(status or "").upper()
    if status == "L":
        return "já encerrada — ignorada", False
    if status == "C":
        # OP cancelada não pode ser liberada, e liberar é o primeiro passo — a cadeia
        # falharia no início com uma mensagem obscura do SAP. A grade do legado nunca
        # mostrava canceladas; aqui ela aparece com o motivo, porque o usuário pediu esse
        # número e merece saber por que não entra, em vez de vê-la sumir.
        return "cancelada — não pode ser encerrada", False
    if float(apontada or 0) >= float(planejada or 0):
        # `if (qtdAD < qtdPD)` do original: nothing to post. A Liberada OP is still closed, as
        # the legacy did (its final `mudaStatus("f")` covered every marked row) — otherwise an
        # OP whose close PATCH dropped after the receipt stayed open for good (2026-11-03,
        # decision of Marcelo). Planejada cannot have production posted, so it stays skipped.
        if status == "R":
            return "só encerrar (já toda apontada)", True
        return "ignorada (apontada = planejada)", False
    if status == "R":
        return "saída + entrada + encerrar", True
    # Planejada: uma OP só pode ser apontada estando Liberada (regra de 22/09), então o
    # encerramento libera antes — e o plano diz isso, porque é o estado que sobra se a
    # cadeia falhar depois desse passo.
    return "LIBERAR + saída + entrada + encerrar", True


def acoes_possiveis(
    status: str, planejada: float, apontada: float, baixada: float | None = None
) -> list[str]:
    """Actions the Manutenção de OP accepts for one OP right now, in the API's words.

    Same rules the actions apply: Liberar takes a Planejada (a Liberada is ignored, a
    terminal one refused); Replanejar takes a Liberada with nothing issued and nothing
    received (``baixada`` unknown → not offered); Encerrar takes what
    ``classifica_encerramento`` processes.
    """
    status = str(status or "").upper()
    acoes = []
    if status == "P":
        acoes.append("liberar")
    if status == "R" and impedimento_replanejar({"baixada": baixada, "apontada": apontada}) is None:
        acoes.append("replanejar")
    if classifica_encerramento(status, planejada, apontada)[1]:
        acoes.append("encerrar")
    return acoes


async def muda_status(sl: ServiceLayerClient, ops: list[dict], status: str) -> dict:
    """`ManutencaoOp.mudaStatus` + `updateOP`: change the status of the given OPs via the Service Layer.

    Args:
        status: legacy code: "l" Liberar, "p" Replanejar, "c" cancel, "f" close.

    Returns:
        ``{"alteradas", "com_erro", "ignoradas", "destino"}``. Terminal OPs, OPs already in the
        target status and, for "p", OPs with a goods issue/receipt posted (or unknown) go to
        ``ignoradas`` with a ``motivo`` and never reach the SAP.

    Raises:
        ValueError: unknown status code.

    WARNING: "f" must not be called alone. In the legacy it is the last step of the closing
    (`Button4_ClickAfter`), after `corrigeOP` + `SaidaEnsumo` + `EntradaProduto`; closing an OP
    without the stock movement leaves the report inconsistent. It is mapped because
    `finalizar_ops` calls it; the CLI exposes only liberar and replanejar.

    Deliberate difference from the C#: there each OP was updated and errors went only to
    `INO_LOG`/status bar. Here the result separates `alteradas` from `com_erro` and one failing OP
    does not stop the others (same criterion as `cancelar-ops` of module 2).
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
        # Already there: no PATCH (29/09/2026). The CLI and the 8077 route already skipped
        # it; the screen spent a write that changed nothing — and a retried API call would
        # rewrite every OP of the batch.
        if op.get("status") == transicao["owor"]:
            motivo = f"OP {op['doc_num']} já estava {transicao['nome']} — nada a fazer."
            ignoradas.append({**op, "motivo": motivo})
            logger.info("  %s", motivo)
            continue
        # Back to Planejada only with nothing issued and nothing received (F6/D6,
        # 29/09/2026). The callers refuse the whole batch before this point; this is the
        # last guard, for anyone calling the service directly — unknown also refuses.
        impedimento = impedimento_replanejar(op) if status == "p" else None
        if impedimento:
            motivo = _motivo_nao_replanejada(op, impedimento)
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


def _motivo_nao_replanejada(op: dict, impedimento: str) -> str:
    """The per-OP sentence of `muda_status` for an OP kept out of Replanejar."""
    numero = op["doc_num"]
    if impedimento == "saida_lancada":
        if op.get("baixada") is None:
            return f"OP {numero}: não foi possível saber se há saída de insumo lançada — não replanejada."
        return f"OP {numero} já tem saída de insumo lançada — cancele a saída no SAP antes de replanejar."
    if op.get("apontada") is None:
        return f"OP {numero}: não foi possível saber se há produto apontado — não replanejada."
    return (f"OP {numero} já tem produto apontado (entrada lançada) — cancele a entrada no SAP "
            "antes de replanejar.")


# Tipo de objeto da Ordem de Produção no SAP B1 (`oProductionOrders`). A DI API inferia o
# `BaseType` a partir do objeto; em REST ele precisa ser dito. Mesma classe de divergência
# já documentada nas seções 7.9 e 7.10 do guia.
TIPO_ORDEM_PRODUCAO = 202

# `ObjectCode` em `NNM1`, para descobrir a série de numeração de cada documento.
TIPO_OBJETO_SAIDA_MERCADORIA = "60"    # OIGE / InventoryGenExits
TIPO_OBJETO_ENTRADA_MERCADORIA = "59"  # OIGN / InventoryGenEntries


def _filial_do_movimento(
    hana_reader: HanaDirectReader, doc_entry: int, filial_configurada: int = 0,
    cache: dict | None = None,
) -> int:
    """Find the branch (`BPLId`) for the OP's goods issue and receipt.

    The Service Layer refuses both documents without it when the company uses branches
    (`Specify an active branch [OIGE.BPLId]`). The C# never sets `BPLId` because the DI API filled
    it from the logged user's default branch: nothing to port, behaviour to invent. Found on
    21/09/2026 in the first real `encerrar` (guide section 7.27).

    Derived from data, not configuration. The first candidate that is an ACTIVE branch wins:

    1. branch of the OP's warehouse (a stock posting happens in the warehouse's branch and the B1
       itself demands that coherence);
    2. lowest branch among the components' warehouses (OP warehouse without a branch);
    3. branch of the origin order (weakest link: an OP may serve another branch's order);
    4. `SL_BUSINESS_PLACE_ID` from `.env`: last resort on purpose, a fixed configured number
       breaks silently the day a second branch is created.

    Candidates are checked against ACTIVE branches (`OBPL."Disabled" <> 'Y'`), not just existing
    ones: the SAP message speaks of an active branch and the company has a registered but disabled
    one (branch 2). Failing with a readable reason before posting beats the SAP's refusal.

    Args:
        cache: one dict per `finalizar_ops` call (30/09/2026); the active branches are the same
            for every OP of a closing and were read again per OP.

    Raises:
        ValueError: no active branch in OBPL, or none of the candidates is active.
    """
    cache = {} if cache is None else cache
    if "filiais_ativas" not in cache:
        cache["filiais_ativas"] = {
            int(linha["BPLId"])
            for linha in hana_reader.fetch_all(q.FILIAIS_ATIVAS)
            if linha.get("BPLId") is not None
        }
    ativas = cache["filiais_ativas"]
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
    hana_reader: HanaDirectReader, object_code: str, filial: int, cache: dict | None = None,
) -> int:
    """Find the numbering series (`Series`) of a stock document for the given branch.

    Second half of the `BPLId` gap (section 7.27): with the branch alone the Service Layer keeps
    answering `Specify an active branch` until `Series` is sent too. The DI API picked the logged
    user's default series: again behaviour to invent, not to port.

    Confirmed at Altamira: series are not per branch (`NNM1."BPLId"` null) and existing `OIGE`
    documents have `Series = 20` with `BPLId = 1`. The precedence covers both layouts, because a
    company may move to per-branch series without notice: the branch's own series first, then a
    series with no branch (serves all); never a locked one (`Locked = 'Y'`); ties go to the lowest
    number so the choice is deterministic (a different series per run would scatter the numbering).

    Args:
        object_code: `NNM1."ObjectCode"` ('60' OIGE, '59' OIGN).
        cache: one dict per `finalizar_ops` call (30/09/2026); the series depends only on document
            type and branch, and was read twice per OP.

    Raises:
        ValueError: no active series for that document type and branch.
    """
    chave = ("serie", str(object_code), int(filial))
    if cache is not None and chave in cache:
        return cache[chave]
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
    if cache is not None:
        cache[chave] = int(escolhida["Series"])
    return int(escolhida["Series"])


async def corrige_op(
    sl: ServiceLayerClient, doc_entry: int, status_atual: str = ""
) -> bool:
    """`ManutencaoOp.corrigeOP` (line ~364): prepare the OP for the goods movements.

    Rule from Anderson (21/09/2026): an OP can only be reported on while LIBERADA. Closing involves
    reporting production (issue + receipt), so a Planejada OP must be released first; that is why
    the legacy `corrigeOP` releases it (without `im_Manual` the SAP tries backflush and refuses the
    manual issue).

    One partial PATCH does two things:

    1. releases the OP unless ``status_atual`` is already "R" (then the status field is not sent:
       resending it is a write with no effect);
    2. sets `ProductionOrderIssueType = im_Manual` on ALL lines, always, which allows the manual
       issue of the components.

    Only `LineNumber` + the changed field go per line (guide section 7.11): resending the whole
    collection drops fields the Service Layer returns empty but does not accept back.

    Returns:
        True when the release was included in the PATCH, so the caller reports the real transition.
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
    """`ManutencaoOp.SaidaEnsumo` (line ~472): goods issue (`InventoryGenExits`) of the OP's components.

    One line per component pointing at the OP (`BaseType`/`BaseEntry`/`BaseLine`). Item, quantity
    and warehouse are NOT sent: the SAP copies them from the base document, as in the original
    (whose assignments were commented out).

    Two deliberate fixes over the C#:

    1. `BaseLine` is the real `LineNum` of the OP line, not the row index in the recordset (see
       `queries.LINHAS_FALTANTES_OP`).
    2. Lines already fully issued (`PlannedQty - IssuedQty <= 0`) are skipped; the legacy included
       them and let the SAP refuse or post quantity zero.

    Returns:
        The created document, or ``{}`` when nothing is pending (no issue needed).
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
    """`ManutencaoOp.EntradaProduto` (line ~547): goods receipt (`InventoryGenEntries`) of the product.

    A single line pointing at the OP; the SAP takes item, remaining quantity and warehouse from the
    base document.
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
    """Item codes each OP consumes (`{doc_entry: {item_code, ...}}`), to build the hierarchy."""
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
    """Order the OPs bottom-up: child before parent.

    The reason is physical, not cosmetic: the parent OP's goods issue consumes the item the child
    produces. Closing the parent first leaves the child's product out of stock and the SAP refuses
    the issue, or worse, consumes balance from another origin.

    The hierarchy is discovered by item: if the item produced by B is a component of A, B comes
    before A (the B1 has no "parent OP" field for this cascade). An item produced by several OPs
    (`PAR000PADRA000000000` appears 13 times in order 84348) works naturally: every OP producing it
    precedes every OP consuming it. An item an OP both produces and consumes is ignored. Ties break
    by DocNum, then DocEntry, so the order is reproducible.

    Returns:
        ``(ordenadas, em_ciclo)``. OPs in a dependency cycle are left OUT of the ordered list
        instead of being put in an arbitrary order: on an irreversible stock operation, guessing is
        worse than refusing. A cycle should not exist in a BOM tree, but bad data does.
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
    """`{doc_entry: OPs that depend on it, directly or indirectly}`.

    Used to skip, when an OP fails, the OPs that consume it, instead of attempting them and
    collecting a cascade of "no stock" errors that hides the real cause.
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
    deve_parar: Callable[[], bool] | None = None,
) -> dict:
    """`ManutencaoOp.Button4_ClickAfter` (line ~399): closing with stock movement.

    Per OP, in the original order: `corrige_op` -> `saida_insumo` -> `entrada_produto` -> status
    Encerrada. Only OPs with `apontada < planejada` get the stock movement (legacy precondition); a
    Liberada OP already fully posted is only closed (``so_encerrada``, 2026-11-03), and a Planejada
    one goes to ``ignoradas``. If the chain fails after the release and before the issue is posted,
    the OP goes back to Planejada.

    IRREVERSIBLE: the goods issue and receipt are stock postings with no undo. The caller must
    confirm with the user first (the single-use `PLANOS` token).

    Deliberate differences from the C#:

    - the legacy called `mudaStatus("f")` once at the end over ALL marked rows, including those
      whose `corrigeOP`/`SaidaEnsumo` had failed, closing OPs without the movement; here each OP is
      closed only if its own chain went to the end;
    - it resolved the DocEntry from the DocNum with the `OPDE` query per row; here the DocEntry
      already comes from the survey;
    - errors went only to the status bar and `INO_LOG`; here the result says what was and was not
      done, in the shape of `muda_status`.

    Args:
        filial_configurada: `SL_BUSINESS_PLACE_ID`, last-resort branch (see `_filial_do_movimento`).
        dependentes: output of `dependentes_transitivos`; OPs depending on a failed one are skipped.
        deve_parar: (29/09/2026, D5 of PLANO_API_MANUTENCAO_OP.md (removed 2026-10-06)) asked at
            the START of each OP, never in the middle: an interruption lets the OP in progress
            finish its chain (issue -> receipt -> close, or its own error) and leaves the rest
            untouched, in ``interrompidas``. Cancelling the coroutine instead could stop between the
            issue and the receipt of the same OP: stock taken out, product never put in.

    Returns:
        ``{"finalizadas", "com_erro", "ignoradas", "puladas", "interrompidas"}``.
    """
    finalizadas: list[dict] = []
    com_erro: list[dict] = []
    ignoradas: list[dict] = []
    puladas: list[dict] = []
    interrompidas: list[dict] = []

    # OPs a pular porque uma OP da qual dependem falhou. Tentá-las produziria uma cascata
    # de "sem estoque" que esconde a causa real — a falha lá embaixo.
    dependentes = dependentes or {}
    bloqueadas: dict[int, int] = {}  # doc_entry -> doc_entry que falhou
    # Branches and series are the same for every OP of this closing: read once (30/09/2026).
    leituras: dict = {}

    async def _desfaz_liberacao(op_alvo: dict, doc_entry_alvo: int) -> str:
        """Put the OP back to Planejada when the chain failed before anything was posted.

        Called only where that is safe: `corrige_op` released the OP and the goods issue was NOT
        created. With the issue already posted, reverting the status would leave stock moved on a
        Planejada OP, worse than the current state. It exists because the 21/09 tests (five failures in
        a row: branch, series, field name, item cost) left OPs Liberada and undoing it became repeated
        manual work. Best effort: a failed revert is reported and the original error stays the main one.

        Returns:
            "desfeita", or "falhou (<error>)".
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
        if deve_parar is not None and deve_parar():
            if not interrompidas:
                logger.warning("  Interrupção pedida: nenhuma OP nova será iniciada.")
            interrompidas.append({
                **op, "motivo": "não iniciada — a execução foi interrompida antes desta OP",
            })
            continue
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
            # Nothing to post (`if (qtdAD < qtdPD)` of the legacy); a Liberada OP is only
            # closed — see `classifica_encerramento`.
            if str(op.get("status") or "").upper() != "R":
                ignoradas.append({**op, "motivo": "quantidade apontada já atingiu a planejada"})
                continue
            try:
                fechamento = await muda_status(sl, [op], "f")
                if fechamento["com_erro"]:
                    raise RuntimeError(fechamento["com_erro"][0]["motivo"])
            except Exception as exc:  # noqa: BLE001 - uma OP com erro não impede as demais
                com_erro.append({**op, "etapa": "encerrar OP", "motivo": str(exc)})
                logger.error("  OP %s falhou em 'encerrar OP': %s", op["doc_num"], exc)
                for dependente in dependentes.get(int(op["doc_entry"]), set()):
                    bloqueadas.setdefault(int(dependente), int(op["doc_entry"]))
                continue
            finalizadas.append({**op, "saida_docentry": None, "entrada_docentry": None, "so_encerrada": True})
            logger.info("  OP %s encerrada (já toda apontada, sem movimentar estoque).", op["doc_num"])
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
            filial = _filial_do_movimento(hana_reader, doc_entry, filial_configurada, cache=leituras)
            # As DUAS séries são resolvidas aqui, antes de qualquer escrita. Descobrir que
            # falta a série da ENTRADA depois de a saída já estar lançada deixaria o pior
            # estado possível: baixa de insumo sem a entrada correspondente, que só se
            # desfaz cancelando o documento no SAP à mão.
            serie_saida = _serie_do_documento(
                hana_reader, TIPO_OBJETO_SAIDA_MERCADORIA, filial, cache=leituras
            )
            serie_entrada = _serie_do_documento(
                hana_reader, TIPO_OBJETO_ENTRADA_MERCADORIA, filial, cache=leituras
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
        "interrompidas": interrompidas,
    }
