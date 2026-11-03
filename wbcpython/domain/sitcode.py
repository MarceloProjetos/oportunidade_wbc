"""Integration state machine: decision from the SitCode.

This module is the heart of the business rule. It is **pure**: it takes a snapshot of the state (what WBC
says, what the SAP already has) and returns the list of actions to run. No I/O, no Service Layer, no
database, so it is fully testable without an environment.

All the logic was extracted from the `if/else` chain in the `Main` of the legacy `WBCServConsole`
(`Program.cs`), with the support queries (`Querys.resx`) used to discover the real meaning of each field.

--------------------------------------------------------------------------
Legacy fields and their equivalents here
--------------------------------------------------------------------------
| Legacy                                   | Here                          |
|------------------------------------------|-------------------------------|
| `oRs.Fields.Item(0)` = `U_ORCNUM_WBC`     | `EstadoIntegracao.orcamento`  |
| `oRs.Fields.Item(1)` = `CardCode`         | `parceiro_atual`              |
| `oRs.Fields.Item(2)` = `OpprId`           | not in the state (the application carries it) |
| `oRs.Fields.Item(3)` = `U_INO_StatusWBC`  | `sitcode_sap`                 |
| `oRs.Fields.Item(4)` = `U_INO_Update`     | `alterado` (swap detour)      |
| `oRs.Fields.Item(5)` = `U_INO_PN_Correc`  | `parceiro_novo`               |
| `GetIntIdWBCOrcamentos` (COUNT on OQUT)   | `tem_cotacao`                 |
| `GetIdOrcamentosPedido` (COUNT on ORDR)   | `tem_pedido`                  |
| `GetStatusAtualCot` (`U_INO_VERSAOWBC`)   | `revisao_cotacao_sap`         |
| `item.SitCode` (WBC `SITCOD`)             | `sitcode_wbc`                 |
| `item.versao` (WBC `REVISAO`)             | `revisao_wbc`                 |

Two readings that were easy to get wrong: `GetIntIdWBCOrcamentos` and `GetIdOrcamentosPedido` are
`SELECT COUNT(*)`, so the `"0"` compared in the legacy means "no quotation / no non-cancelled order
exists", and **not** a DocEntry.

--------------------------------------------------------------------------
Legacy defects handled here (see DEFEITOS_LEGADO.md)
--------------------------------------------------------------------------
1. Two legacy conditions are tautologies: `(x != 30 || x != 60 || ...)` is always true, because a number
   cannot differ from all of them and equal one of them at the same time. The **effective** behavior (what
   the tautology actually produces) is implemented here, with the branch named and documented.
2. Exclusions of specific opportunities by number hardcoded in the code (`OportunidadeId != "00118376"`)
   were **not ported**: they are one-off data exceptions, not business rules.
3. In the legacy, the actions inside the final branch accumulate (sequential `if`s, not `else if`). That is
   why the decision here returns an **ordered list** of actions, not a single action.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from wbcpython.domain.revisao import revisao_wbc_e_mais_nova

# SitCode abaixo ou igual a este valor não é processado pela integração.
# No legado: `if (oRsOther.RecordCount > 0 && item.SitCode > 10)`.
SITCODE_MINIMO = 10

SITCODE_ORCAMENTO_EMITIDO = 30
SITCODES_REVISAO = frozenset({40, 55})
SITCODE_PEDIDO_FECHADO = 60
SITCODES_ENCERRAMENTO = frozenset({70, 90, 99})

#: Valores de `OOPR."Status"` que significam oportunidade fechada — perdida
#: ('L') ou vendida ('W'). Aberta é 'O'.
STATUS_OPORTUNIDADE_FECHADA = frozenset({"L", "W"})


class Acao(Enum):
    """Integration action to run in the SAP."""

    CRIAR_COTACAO = "criar_cotacao"
    ATUALIZAR_COTACAO = "atualizar_cotacao"
    CANCELAR_E_RECRIAR_COTACAO = "cancelar_e_recriar_cotacao"
    CRIAR_PEDIDO = "criar_pedido"
    ATUALIZAR_PEDIDO = "atualizar_pedido"
    CANCELAR_E_RECRIAR_PEDIDO = "cancelar_e_recriar_pedido"
    #: Cancels the PLANNED production orders of the current order, before the partner
    #: swap cancels the order itself. Never touches a released or closed OP; when any
    #: OP survives, the swap waits for the next cycle (`docs/PLANO_TROCA_PN_COM_OP.md`).
    CANCELAR_OPS_DO_PEDIDO = "cancelar_ops_do_pedido"
    #: Grava `Status = 'sos_Missed'` e o SitCode na oportunidade. **Não
    #: cancela** a oportunidade: no SAP não há cancelamento de oportunidade,
    #: só a mudança de status.
    #:
    #: Chamava-se `FECHAR_CANCELAR_OPORTUNIDADE`, herdado do
    #: `cancelaFechaOportunidade` do legado — que também só mudava o status. O
    #: nome dizia "cancelar" e o código não cancelava, e isso aparecia no painel
    #: e no histórico de cada oportunidade. Renomeado na confirmação da regra
    #: com o usuário, sem mudança de comportamento.
    MARCAR_OPORTUNIDADE_PERDIDA = "marcar_oportunidade_perdida"
    CANCELAR_COTACAO_NO_ENCERRAMENTO = "cancelar_cotacao_no_encerramento"
    ATUALIZAR_STATUS_OPORTUNIDADE = "atualizar_status_oportunidade"
    VINCULAR_DOCUMENTO_A_OPORTUNIDADE = "vincular_documento_a_oportunidade"


#: As acoes que criam, alteram ou refazem um PEDIDO no SAP.
#:
#: Mora no dominio, e nao so em `application/processar.py`, porque desde
#: 14/09/2026 e o dominio que decide quando elas nao valem — ver
#: `EstadoIntegracao.fora_da_janela_padrao`.
ACOES_DE_PEDIDO = frozenset(
    {
        Acao.CRIAR_PEDIDO,
        Acao.ATUALIZAR_PEDIDO,
        Acao.CANCELAR_E_RECRIAR_PEDIDO,
        # Goes wherever the swap goes: a filter that drops the order actions must not
        # leave the OP cancellation behind on its own.
        Acao.CANCELAR_OPS_DO_PEDIDO,
    }
)

#: As ações que produzem ou refazem a cotação — o que `VINCULAR_DOCUMENTO_A_OPORTUNIDADE`
#: precisa ter antes de si para ter o que vincular quando o pedido sai da decisão.
_ACOES_DE_COTACAO = frozenset(
    {Acao.CRIAR_COTACAO, Acao.ATUALIZAR_COTACAO, Acao.CANCELAR_E_RECRIAR_COTACAO}
)

#: Toda ação que monta um documento com LINHAS e o manda ao SAP. É o conjunto
#: que não faz sentido para um orçamento sem item — ver `_sem_itens`. Cancelar
#: no encerramento, marcar a oportunidade perdida e espelhar status ficam de
#: fora de propósito: nenhuma delas precisa de linha nem de valor.
_ACOES_DE_DOCUMENTO = _ACOES_DE_COTACAO | ACOES_DE_PEDIDO


@dataclass(frozen=True, slots=True)
class EstadoIntegracao:
    """Snapshot of what WBC says and what the SAP already has, for one quote."""

    orcamento: str
    sitcode_wbc: int

    # Lado SAP
    sitcode_sap: str = ""
    #: `Status` da OOPR: 'O' aberta, 'L' perdida, 'W' vendida. É a **única**
    #: prova de que o encerramento aconteceu. O `sitcode_sap` não serve para
    #: isso: `atualizar_status` o espelha a cada ciclo, muito antes de qualquer
    #: encerramento — e enquanto o `encerrar()` mandava um `sos_Lost`
    #: inexistente, todas as oportunidades tinham o SitCode espelhado e
    #: continuavam abertas.
    status_oportunidade: str = ""
    tem_cotacao: bool = False
    tem_pedido: bool = False
    #: `DocStatus = 'C'` no pedido vigente: o SAP não aceita alteração nem
    #: cancelamento. Ver `pedido_fechado_no_sap`.
    pedido_fechado: bool = False
    revisao_cotacao_sap: str = ""
    revisao_pedido_sap: str = ""
    #: `CardCode` do pedido vigente no SAP. É o equivalente do `ChecaPNPedido`
    #: legado: comparado com `parceiro_novo`, diz se a troca já foi aplicada.
    parceiro_pedido_sap: str = ""
    #: Live production orders of the current order (`OWOR.OriginAbs`): planned, and
    #: released or closed. `None` = unknown (the Service Layer read does not carry
    #: them), which a guard must treat as "may have OPs", never as zero.
    #: `docs/PLANO_TROCA_PN_COM_OP.md`.
    ops_planejadas_pedido: int | None = None
    ops_em_producao_pedido: int | None = None

    #: A oportunidade e mais antiga que a janela PADRAO do ciclo.
    #:
    #: So acontece num ciclo de janela estendida (`PLANO_JANELA_SOB_DEMANDA.md (removed 2026-10-06)`):
    #: num ciclo normal, tudo que e lido esta dentro da janela por construcao.
    #:
    #: Quando verdadeiro, a decisao sai **sem as acoes de pedido**. Regra de
    #: negocio do Marcelo (14/09/2026): alcancar para tras serve para acertar
    #: cotacao e oportunidade de negocios antigos; **criar pedido** para um
    #: negocio de mais de seis meses e outra coisa — o ensaio de 13 meses
    #: mostrou pedidos de mais de R$ 1 milhao nascendo de oportunidades de 2025,
    #: que alguem pode ja ter resolvido a mao no SAP nesse tempo.
    #:
    #: Cotacao continua valendo, inclusive o cancelamento no encerramento: ela
    #: e proposta, nao compromisso.
    fora_da_janela_padrao: bool = False

    #: O orçamento não tem **nenhum item** no WBC.
    #:
    #: Documento sem linha não tem valor, e o SAP recusa (`HTTP 400 | SAP -5002
    #: | "Document total value must be zero or greater than zero"`). O executor
    #: já barrava isso em `_exigir_valor`, mas a decisão continuava dizendo
    #: "cria a cotação" **a cada ciclo**: o orçamento 00125188 (SitCode 20, zero
    #: item) acumulou 100 eventos de "não criada" em 17/09/2026, um a cada 3
    #: minutos, e a janela de 24 meses trouxe todos os orçamentos velhos assim
    #: para dentro do ciclo. Com o corte aqui, o ciclo não monta payload nem
    #: escreve no acompanhamento — e volta a criar sozinho no ciclo seguinte
    #: ao primeiro item, porque a decisão só olha o estado de agora.
    orcamento_sem_itens: bool = False

    # Revisão corrente no WBC
    revisao_wbc: str = ""

    #: `U_INO_Update = 'Y'`: o operador pediu a troca de PN do pedido.
    #:
    #: **Onde o campo mora:** é um UDF da *oportunidade no SAP*
    #: (`SalesOpportunities` no Service Layer, `OOPR` no HANA) — não um campo do
    #: WBC. A documentação daqui já disse o contrário, e o usuário corrigiu.
    #: A integração o lê junto com o resto da oportunidade, na mesma consulta.
    #:
    #: **É um desvio entre dois caminhos, e não uma autorização para mexer.**
    #: No legado, dentro do SitCode 60 com pedido existente: `!= 'Y'` compara a
    #: revisão carimbada no pedido e atualiza (`Program.cs:249`); `== 'Y'`
    #: desvia para cancelar e recriar no PN novo (`Program.cs:280`). Congelar o
    #: pedido em `'N'` — que esta integração chegou a fazer — inverte isso e
    #: deixa a atualização por revisão sem caminho nenhum.
    #:
    #: Quem congela o pedido é a revisão carimbada nele (`U_INO_VERSAOWBC` no
    #: `ORDR`, aqui `revisao_pedido_sap`) e o `DocStatus` fechado.
    #:
    #: A troca em si é decidida por `troca_de_parceiro_pendente`, que olha o
    #: pedido; a exigência de `'Y'` vem de `pedido_corrigido_a_mao`, que roda
    #: antes. Exigir `alterado` *dentro* da troca já foi um erro, e fez a troca
    #: deixar de acontecer.
    #:
    #: **A integração escreve neste campo, num caso só:** baixa para `'N'` ao
    #: vincular o **pedido**, no mesmo PATCH do estágio — ver
    #: `service_layer/oportunidades.py`. É o que o legado faz
    #: (`ServiceProcess.cs:198` e `:210`, ambas em `AddPedidoOportunidade`), e
    #: fecha o ciclo pedido pelo negócio: o operador marca `'Y'`, o pedido novo
    #: é criado e vinculado, a marca volta a `'N'`. O que a integração **não**
    #: faz é baixar a marca como passo avulso depois da troca — ver
    #: `processar.py`.
    alterado: bool = False

    # Troca de parceiro de negócios (PN)
    parceiro_atual: str = ""
    parceiro_novo: str = ""

    @property
    def pedido_fechado_no_sap(self) -> bool:
        """Closed order: nothing to write to it, nothing to cancel.

        A closed order (`DocStatus = 'C'`) was already delivered or invoiced. The SAP refuses both changes and
        cancellation, so trying is not just useless but one error per cycle, forever, in the history of every quote
        in that situation.

        Reported by the user on quote `00124268`: order `83988`, closed, with revision `'A'` in the SAP and a newer
        revision in WBC. The rule fell into `atualiza_pedido` and the SAP refused the write.

        The scale is surprising: in production, **2,452 of the 2,568** current orders are closed. It is the normal
        state of an order that ran its course, not the exception, and the integration could not know because
        `DocStatus` was not read.

        `tem_pedido` still holds: an order exists, so another is not created. "Closed" is not "nonexistent".
        """
        return self.tem_pedido and self.pedido_fechado

    @property
    def pedido_corrigido_a_mao(self) -> bool:
        """`U_INO_PN_Correc` filled with `U_INO_Update = 'N'`: hands off.

        Per the business, that combination means **the order is already correct, under another PN**, and nobody
        asked for value changes. The integration has nothing to do there: neither swap the partner nor update the
        order.

        The second half is what really bit. Quote `00124045` has the current order `84022` on the `PN_Correc`
        (`C011608`), with the old order `83949` already cancelled on the opportunity's PN: the correction **is
        done**. As `troca_de_parceiro_pendente` is false at that point, `parceiro_do_pedido` returned the
        *opportunity's* `CardCode` (`C008483`), and `campos_comuns` sends `CardCode` **always**, so
        `atualizar_pedido` undid the correction, returning the order to the wrong partner.

        Measured in production: 592 opportunities have this combination. Of those the old rule would still have
        considered pending a swap, **all 4** are old cases settled by hand, with the live order on a third
        partner; none is legitimate work. See `DECISOES.md`.

        A consequence that must be stated: with this guard, the PN swap only happens with `U_INO_Update = 'Y'`. If
        `PN_Correc` is filled on the opportunity without `Update` going to `'Y'`, no swap happens: both are UDFs of
        the opportunity in the SAP, and who fills them is outside this integration. Open question (`RETOMADA.md`).

        `tem_pedido` is part of the question, as in the two sibling properties: with no order there is nothing
        corrected by hand to preserve. The first version did not check it and returned `True` with no order. It
        caused no defect because `_decidir_pedido` had already returned earlier, on creation, but that is
        statement order, not a property invariant, and anyone reusing the property elsewhere would get the wrong
        answer.
        """
        return self.tem_pedido and bool(self.parceiro_novo) and not self.alterado

    @property
    def troca_de_parceiro_pendente(self) -> bool:
        """`U_INO_PN_Correc` filled and the order is not on it yet.

        **The guard is the order, not a flag.** It is the legacy `ChecaPNPedido` (`SELECT count(*) FROM ORDR WHERE
        CardCode = PNNew AND U_INO_COTWBC = quote AND CANCELED = 'N'`): with a non-cancelled order already on the
        new partner, there is nothing to do.

        That makes the rule idempotent by construction, and is what allows acting **whenever** `PN_Correc` is
        filled, without anyone clearing a flag afterwards. The field stays filled forever (578 opportunities in
        homologation have it) and that is harmless: their order is already on the new partner.

        `U_INO_Update` does **not** enter this property: the question here is only "is the order already on the
        new partner?". The field is used by `pedido_corrigido_a_mao`, a guard that runs **before** this one in
        `_decidir_pedido`, and the difference between the two matters. Requiring `alterado` *to swap* was a
        mistake, corrected after rereading `Program.cs:280-300`; what the business clarified later is the
        opposite: `Update = 'N'` with `PN_Correc` filled is a positive sign that someone **already settled** the
        case by hand.
        """
        if not self.parceiro_novo or not self.tem_pedido:
            return False
        return self.parceiro_pedido_sap != self.parceiro_novo

    @property
    def nasce_no_parceiro_corrigido(self) -> bool:
        """The order does not exist yet and the operator already asked for the new partner.

        `U_INO_Update = 'Y'` with `U_INO_PN_Correc` filled **before** the order exists. The legacy created the
        order on the opportunity's `CardCode` (`CriaPedido(oRs.Fields.Item(1))`) and only looked at `PNNew` in the
        swap branch, which requires an existing order, and linking the new order resets `Update` to `'N'`. Result:
        on the next pass the `PN_Correc` + `'N'` combination is read as "already corrected by hand" and the order
        stays on the wrong partner forever, with no error.

        The rule asked by the business (Marcelo, 09/09/2026) is the same on both paths: **`Update = 'Y'` sends the
        order to `PN_Correc`.** With an order, it is the swap (cancel and recreate); without one, it is here: the
        order is born on the right partner, cancelling nothing. Same partner-existence guard (`ChecaPN`) in both
        cases, in `processar.py`.
        """
        return not self.tem_pedido and self.alterado and bool(self.parceiro_novo)

    @property
    def pedido_no_parceiro_corrigido(self) -> bool:
        """The order to write (new or redone) carries `PN_Correc`, not the opportunity's partner."""
        return self.troca_de_parceiro_pendente or self.nasce_no_parceiro_corrigido

    @property
    def parceiro_do_pedido(self) -> str:
        """The partner the **order** must carry.

        `PN_Correc` when the swap is pending **or** when the order will be born already corrected
        (`nasce_no_parceiro_corrigido`); otherwise the opportunity's, as in the legacy, where `CriaPedido`
        receives `oRs.Fields.Item(1)` (the opportunity's `CardCode`) on the normal path and `PNNew` only in the
        swap branch.
        """
        return self.parceiro_novo if self.pedido_no_parceiro_corrigido else self.parceiro_atual

    @property
    def status_ja_espelhado(self) -> bool:
        """The SAP already records the WBC's current SitCode.

        `U_INO_StatusWBC` is a copy of the SitCode. When both ends already agree, rewriting the same value is a
        write with no effect, and not a cheap one: it consumes the cycle's write ceiling, creates an event in the
        history of every opportunity and makes the summary announce work that did not happen. In homologation
        they were 154 of the 160 mirroring actions.

        The comparison is textual because the field is text in the SAP; see `atualizar_status`, which writes
        `str(sitcode)` on purpose.
        """
        return self.sitcode_sap.strip() == str(self.sitcode_wbc)

    @property
    def oportunidade_encerrada(self) -> bool:
        """The opportunity is already closed in the SAP (lost or won)."""
        return self.status_oportunidade.strip().upper() in STATUS_OPORTUNIDADE_FECHADA

    @property
    def processavel(self) -> bool:
        """The integration only considers quotes above the minimum SitCode."""
        return self.sitcode_wbc > SITCODE_MINIMO


@dataclass(frozen=True, slots=True)
class Decisao:
    """Result of the state machine.

    `motivos` accompanies `acoes` so the dashboard and the log explain *why* each action was decided, which
    the legacy system did not record. `regra` names the branch taken (joined with `+` when several apply).
    """

    acoes: tuple[Acao, ...] = ()
    motivos: tuple[str, ...] = ()
    regra: str = ""

    @property
    def tem_acao(self) -> bool:
        return bool(self.acoes)


@dataclass
class _Acumulador:
    acoes: list[Acao] = field(default_factory=list)
    motivos: list[str] = field(default_factory=list)

    def registrar(self, motivo: str, *acoes: Acao) -> None:
        self.acoes.extend(acoes)
        self.motivos.append(motivo)


def decidir(estado: EstadoIntegracao) -> Decisao:
    """Decide the integration actions for a quote.

    Reproduces the evaluation order of the legacy `Main`, which is significant: the branches are mutually
    exclusive up to the last one, and inside it the actions accumulate.

    Outside the default window the decision comes out without the **order** actions (see `_sem_pedido`). The
    cut is applied here, not in the processor, so the preview (`wbcpython pendentes`) shows exactly what the
    cycle would do: both call this function, and a filter at execution time would leave the rehearsal
    promising orders the cycle would not create.

    A quote with no items comes out without the document actions (see `_sem_itens`). That filter comes last
    because it holds for any branch: there is no branch in which sending a document without lines to the SAP
    makes sense.
    """
    return _sem_itens(estado, _sem_pedido(estado, _decidir(estado)))


def _sem_itens(estado: EstadoIntegracao, decisao: Decisao) -> Decisao:
    """Drop the document actions when the quote has no item in WBC.

    The SAP refuses a document with no value, so the action would never take effect: the executor already
    blocked it in `_exigir_valor`, but only after building the payload and writing an event in the tracking,
    **every cycle**, forever. With the 24-month window (`PLANO_JANELA_SOB_DEMANDA`) every old empty quote
    entered that loop: 00125188 (SitCode 20, zero items) accumulated 100 events on 17/09/2026, one every 3
    minutes.

    Cutting here, not in the executor, is what makes the cycle **stop trying**: the preview (`wbcpython
    pendentes`) now says the same thing the cycle does, and nothing is written to the tracking while the quote
    is empty.

    What does NOT go: cancelling the quotation on closure, marking the opportunity lost and mirroring the
    status, since none needs a line. And `_exigir_valor` stays in the executor, because a zero-price item also
    vanishes in the SAP and that check depends on the built payload.
    """
    if not estado.orcamento_sem_itens:
        return decisao
    restantes = [a for a in decisao.acoes if a not in _ACOES_DE_DOCUMENTO]
    if len(restantes) == len(decisao.acoes):
        return decisao

    # Sem documento não há o que vincular — mesma regra do `_sem_pedido`.
    if Acao.VINCULAR_DOCUMENTO_A_OPORTUNIDADE in restantes:
        restantes.remove(Acao.VINCULAR_DOCUMENTO_A_OPORTUNIDADE)

    return Decisao(
        acoes=tuple(restantes),
        regra=f"{decisao.regra}+sem_itens_no_wbc",
        motivos=(
            *decisao.motivos,
            (
                "O orçamento não tem item no WBC: o SAP recusa documento sem "
                "valor, então nada é enviado. Quando o primeiro item entrar, o "
                "ciclo seguinte cria o documento."
            ),
        ),
    )


def _sem_pedido(estado: EstadoIntegracao, decisao: Decisao) -> Decisao:
    """Drop the order actions when the opportunity is outside the default window.

    Filtering afterwards, instead of spreading the condition through the branches, is deliberate: the state
    machine stays a branch-by-branch reading of the legacy, and the new rule sits in one place where it can be
    read whole.

    `VINCULAR_DOCUMENTO_A_OPORTUNIDADE` stays while a quotation action remains: it only acts if an earlier
    action produced a document (see `processar.py`), so without the order it links the quotation, which is what
    a quotation cycle does anyway. With no quotation action left it goes too; otherwise the decision "has an
    action" with nothing to do, and the cycle loads the whole quote and writes an empty "Ações executadas:" on
    every pass.

    **The `ATUALIZAR_COTACAO` of `cria_pedido` stays only if the WBC revision is newer.** In the original rule
    it is unconditional, and idempotent because the order is born right after and the branch never repeats.
    Without the order the stop condition vanishes: every extended cycle (and every directed `ciclo --orcamento`)
    rewrote the quotation lines of every SitCode 60 without an order, counted a write against the ceiling and
    logged "quotation updated" with nothing changed in WBC (17/09/2026).
    """
    if not estado.fora_da_janela_padrao:
        return decisao
    restantes = [a for a in decisao.acoes if a not in ACOES_DE_PEDIDO]
    if len(restantes) == len(decisao.acoes):
        return decisao
    motivos = [
        *decisao.motivos,
        (
            "Oportunidade mais antiga que a janela padrão: a janela estendida "
            "acerta cotação e oportunidade, e não cria nem altera pedido."
        ),
    ]

    if (
        Acao.CRIAR_PEDIDO in decisao.acoes
        and Acao.ATUALIZAR_COTACAO in restantes
        and not revisao_wbc_e_mais_nova(estado.revisao_wbc, estado.revisao_cotacao_sap)
    ):
        restantes.remove(Acao.ATUALIZAR_COTACAO)
        motivos.append(
            f"A cotação já está na revisão do WBC ({estado.revisao_wbc or '-'}): "
            f"sem o pedido, não há o que atualizar nela."
        )

    if Acao.VINCULAR_DOCUMENTO_A_OPORTUNIDADE in restantes and not any(
        a in _ACOES_DE_COTACAO for a in restantes
    ):
        restantes.remove(Acao.VINCULAR_DOCUMENTO_A_OPORTUNIDADE)

    return Decisao(
        acoes=tuple(restantes),
        regra=f"{decisao.regra}+sem_pedido_fora_da_janela",
        motivos=tuple(motivos),
    )


def _decidir(estado: EstadoIntegracao) -> Decisao:
    """The state machine proper; see `decidir`."""
    if not estado.processavel:
        return Decisao(
            regra="sitcode_abaixo_do_minimo",
            motivos=(
                (
                    f"SitCode {estado.sitcode_wbc} não é processado "
                    f"(mínimo: acima de {SITCODE_MINIMO})."
                ),
            ),
        )

    # --- Ramo 1: orçamento emitido para o cliente -------------------------
    if estado.sitcode_wbc == SITCODE_ORCAMENTO_EMITIDO:
        return _ramo_orcamento_emitido(estado)

    # --- Ramos 2 e 3: revisão de orçamento (SitCode 40/55) ----------------
    if estado.tem_cotacao and estado.sitcode_wbc in SITCODES_REVISAO:
        if estado.sitcode_sap == "30":
            return Decisao(
                regra="revisao_sobre_cotacao_emitida",
                acoes=(Acao.ATUALIZAR_COTACAO, Acao.VINCULAR_DOCUMENTO_A_OPORTUNIDADE),
                motivos=(
                    (
                        f"WBC em revisão (SitCode {estado.sitcode_wbc}) e SAP ainda em 30: "
                        f"atualiza a cotação existente."
                    ),
                ),
            )
        if estado.sitcode_sap in ("40", "55"):
            # "55" entra junto com "40" — como já entra no ramo do SitCode 30.
            # Até 17/09/2026 só o "40" chegava aqui, e o "55" caía no ramo
            # final, que não compara revisão: bastava um ciclo espelhar 55 na
            # oportunidade (é o que `_espelhar_status_apos_documento` faz
            # depois de mexer na cotação) para toda revisão seguinte do WBC
            # em 55 sair como `sem_acao`, com a cotação parada na anterior.
            return _ramo_revisao_ja_registrada(estado)
        # Demais valores de sitcode_sap caem no ramo final.

    # --- Ramo 3.5: orçamento encerrado no WBC -----------------------------
    # Precisa vir **antes** do ramo "não há cotação", e a ordem é consequência
    # direta de passarmos a cancelar a cotação no encerramento.
    #
    # No legado a ordem era indiferente: a cotação nunca era cancelada, então um
    # orçamento encerrado sempre tinha `tem_cotacao=True` e jamais alcançava o
    # ramo de criação. Cancelando, `tem_cotacao` vira False na execução seguinte
    # — e o ramo 4 recriaria a cotação de um orçamento perdido, para cancelá-la
    # no ciclo seguinte, indefinidamente.
    if estado.sitcode_wbc in SITCODES_ENCERRAMENTO:
        return _ramo_pedido_e_encerramento(estado)

    # --- Ramo 4: não há cotação ------------------------------------------
    # No legado esta condição vinha acompanhada de uma tautologia
    # `(SitCode != 30 || != 60 || ...)`, que é sempre verdadeira; o efeito real
    # é simplesmente "não existe cotação".
    if not estado.tem_cotacao:
        return Decisao(
            regra="sem_cotacao_cria",
            acoes=(Acao.CRIAR_COTACAO, Acao.VINCULAR_DOCUMENTO_A_OPORTUNIDADE),
            motivos=(
                (
                    f"Não existe cotação para o orçamento e o SitCode é "
                    f"{estado.sitcode_wbc}: cria a cotação."
                ),
            ),
        )

    # --- Ramo 5: cotação existe; trata pedido e encerramento --------------
    return _ramo_pedido_e_encerramento(estado)


def _ramo_orcamento_emitido(estado: EstadoIntegracao) -> Decisao:
    """SitCode 30: quote issued to the customer."""
    if not estado.tem_cotacao:
        return Decisao(
            regra="emitido_sem_cotacao",
            acoes=(Acao.CRIAR_COTACAO, Acao.VINCULAR_DOCUMENTO_A_OPORTUNIDADE),
            motivos=("SitCode 30 e nenhuma cotação no SAP: cria a cotação.",),
        )

    if estado.sitcode_sap in ("40", "55"):
        # O WBC voltou para 30 enquanto o SAP já registrava revisão: o
        # documento no SAP está desatualizado e precisa ser refeito.
        return Decisao(
            regra="emitido_apos_revisao_no_sap",
            acoes=(Acao.CANCELAR_E_RECRIAR_COTACAO, Acao.VINCULAR_DOCUMENTO_A_OPORTUNIDADE),
            motivos=(
                (
                    f"WBC voltou ao SitCode 30 mas o SAP registra {estado.sitcode_sap}: "
                    f"cancela a cotação e recria."
                ),
            ),
        )

    return Decisao(
        regra="emitido_ja_sincronizado",
        motivos=("SitCode 30 com cotação já existente e SAP sincronizado: nada a fazer.",),
    )


def _ramo_revisao_ja_registrada(estado: EstadoIntegracao) -> Decisao:
    """SitCode 40/55 with the SAP already at 40 or 55: decides by comparing revisions."""
    if revisao_wbc_e_mais_nova(estado.revisao_wbc, estado.revisao_cotacao_sap):
        return Decisao(
            regra="revisao_mais_nova_recria_cotacao",
            acoes=(Acao.CANCELAR_E_RECRIAR_COTACAO, Acao.VINCULAR_DOCUMENTO_A_OPORTUNIDADE),
            motivos=(
                (
                    f"Revisão do WBC ({estado.revisao_wbc or '-'}) é mais nova que a do SAP "
                    f"({estado.revisao_cotacao_sap or '-'}): cancela e recria a cotação."
                ),
            ),
        )
    return Decisao(
        regra="revisao_congelada",
        motivos=(
            (
                f"Revisão do WBC ({estado.revisao_wbc or '-'}) não é mais nova que a do SAP "
                f"({estado.revisao_cotacao_sap or '-'}): documento congelado, nada a fazer."
            ),
        ),
    )


def _ramo_pedido_e_encerramento(estado: EstadoIntegracao) -> Decisao:
    """Final branch of the legacy, where the actions accumulate."""
    acumulador = _Acumulador()
    regras: list[str] = []

    if estado.sitcode_wbc == SITCODE_PEDIDO_FECHADO:
        _decidir_pedido(estado, acumulador, regras)

    if estado.sitcode_wbc in SITCODES_ENCERRAMENTO:
        _decidir_encerramento(estado, acumulador, regras)

    # No legado, esta condição vinha com uma tautologia; o efeito real era
    # "existe cotação", e o status era reespelhado a cada passagem — inclusive
    # quando o SAP já tinha o valor. **Divergência deliberada:** aqui só espelha
    # quando as duas pontas discordam de fato.
    #
    # O legado podia se dar a esse luxo; nós não. Cada PATCH desses consome o
    # teto de escrita do ciclo, deixa um evento no histórico da oportunidade e
    # entra no resumo como trabalho feito. Na janela de homologação eram 154 de
    # 160 espelhamentos regravando o valor que já estava lá.
    #
    # O encerramento é excluído porque `encerrar()` grava `U_INO_StatusWBC`
    # junto com o `Status` — espelhar também seria o mesmo PATCH duas vezes.
    if (
        estado.tem_cotacao
        and not estado.status_ja_espelhado
        and Acao.MARCAR_OPORTUNIDADE_PERDIDA not in acumulador.acoes
    ):
        acumulador.registrar(
            f"Reflete o SitCode {estado.sitcode_wbc} no status da oportunidade no SAP "
            f"(o SAP registra '{estado.sitcode_sap or '-'}').",
            Acao.ATUALIZAR_STATUS_OPORTUNIDADE,
        )
        regras.append("espelha_status")

    return Decisao(
        acoes=tuple(acumulador.acoes),
        motivos=tuple(acumulador.motivos),
        regra="+".join(regras) if regras else "sem_acao",
    )


def _decidir_encerramento(
    estado: EstadoIntegracao,
    acumulador: _Acumulador,
    regras: list[str],
) -> None:
    """SitCode 70/90/99: the quote was lost/cancelled in WBC.

    What happens to the **opportunity**: it is marked lost (`Status = 'sos_Missed'`) and receives the SitCode
    in `U_INO_StatusWBC`, unless it is already closed in the SAP (`oportunidade_encerrada`), in which case
    marking is skipped. It is **not** cancelled: the SAP has no opportunity cancellation, only a status
    change, and this equals the legacy (`cancelaFechaOportunidade`, `ServiceProcess.cs:91-122`). Confirmed
    with the user.

    What happens to the **quotation** is the divergence, below.

    **Deliberate divergence from the legacy.** The `WBCServConsole` `cancelaFechaOportunidade`
    (`ServiceProcess.cs:91-122`) only wrote `U_INO_StatusWBC` and set the opportunity to `sos_Missed`; it never
    touched the documents. The production effect is measurable: of 714 closed opportunities, **686 (96%) still
    have an open quotation**, quotations nobody will honour, polluting the SAP sales pipeline.

    Here the quotation is cancelled together with the closure, and **before** it: if the cancellation fails,
    the opportunity is not left closed with an open document hanging off it, and the next cycle tries again.

    Only the quotation. The order is not cancelled for two reasons: closure with an existing order is a state
    WBC should not produce (the order is born at SitCode 60), and an order may already have moved stock or
    billing, so cancelling it automatically is irreversible and costly. With an order, the quotation is also
    preserved, because in that case it was converted and the SAP would refuse the cancellation.
    """
    if estado.tem_cotacao and not estado.tem_pedido:
        acumulador.registrar(
            f"SitCode {estado.sitcode_wbc}: cancela a cotação vinculada ao orçamento.",
            Acao.CANCELAR_COTACAO_NO_ENCERRAMENTO,
        )
        regras.append("cancela_cotacao_no_encerramento")

    if estado.oportunidade_encerrada:
        # A oportunidade já está fechada no SAP: marcar de novo é gravar o
        # mesmo valor. Sem esta guarda, toda execução repete o PATCH — ruído
        # permanente no histórico de cada oportunidade perdida da janela, e
        # teto de escrita consumido por trabalho que não existe.
        #
        # A condição **não** olha a cotação, e essa foi a correção: exigir
        # `not tem_cotacao` deixava passar todo encerramento que ainda tivesse
        # cotação vinculada. No `00123425` (Status `'L'`, SitCode espelhado,
        # cotação e pedido existentes) a oportunidade era remarcada como
        # perdida a cada ciclo — e a cotação nem chegava a ser cancelada,
        # porque há pedido. Nada acontecia, para sempre.
        #
        # O cancelamento da cotação, quando cabe, já foi registrado acima: a
        # guarda pula só a marcação. E se o `U_INO_StatusWBC` estiver
        # desatualizado, o espelhamento no fim de `_ramo_pedido_e_encerramento`
        # cuida disso — ele só é suprimido quando a marcação acontece.
        regras.append("encerramento_ja_aplicado")
        return

    acumulador.registrar(
        f"SitCode {estado.sitcode_wbc}: marca a oportunidade como perdida "
        f"(Status = 'sos_Missed'). A oportunidade **não** é cancelada.",
        Acao.MARCAR_OPORTUNIDADE_PERDIDA,
    )
    regras.append("marca_perdida")


def _decidir_troca_de_parceiro(
    estado: EstadoIntegracao,
    acumulador: _Acumulador,
    regras: list[str],
) -> None:
    """Partner swap on an open order: cancel and recreate, never leaving OPs behind.

    The swap used to cancel the order without looking at production. On 20/10/2026 that left 110 planned OPs on
    the cancelled 84505 and 84506 while their replacements got their own, the same items planned twice
    (`docs/PLANO_TROCA_PN_COM_OP.md`). Decision of the owner (option B): planned OPs are cancelled first, by the
    worker; a released or closed OP stops the swap until a person deals with it; unknown counts stop it too.
    """
    de_para = f"{estado.parceiro_pedido_sap or '-'} → {estado.parceiro_novo}"
    planejadas, em_producao = estado.ops_planejadas_pedido, estado.ops_em_producao_pedido

    if planejadas is None or em_producao is None:
        acumulador.registrar(
            f"Troca de parceiro pedida na oportunidade do SAP ({de_para}), mas as OPs do "
            f"pedido não foram lidas: nada feito, para não cancelar pedido em produção."
        )
        regras.append("troca_de_pn_ops_desconhecidas")
        return

    if em_producao:
        acumulador.registrar(
            f"Troca de parceiro pedida na oportunidade do SAP ({de_para}), mas o pedido tem "
            f"{em_producao} OP(s) liberada(s) ou encerrada(s): não dá para cancelar sem uma "
            f"pessoa. Trate as OPs na Manutenção de OP; a troca segue sozinha quando não "
            f"houver mais OP em produção."
        )
        regras.append("troca_de_pn_bloqueada_op_em_producao")
        return

    if planejadas:
        acumulador.registrar(
            f"Parceiro corrigido na oportunidade do SAP ({de_para}) e o pedido tem "
            f"{planejadas} OP(s) planejada(s): cancela as OPs, depois cancela o pedido e "
            f"recria com o novo PN.",
            Acao.CANCELAR_OPS_DO_PEDIDO,
            Acao.CANCELAR_E_RECRIAR_PEDIDO,
            Acao.VINCULAR_DOCUMENTO_A_OPORTUNIDADE,
        )
        regras.append("troca_de_pn_cancela_ops")
        return

    acumulador.registrar(
        f"Parceiro corrigido na oportunidade do SAP ({de_para}): cancela o pedido e recria "
        f"com o novo PN.",
        Acao.CANCELAR_E_RECRIAR_PEDIDO,
        Acao.VINCULAR_DOCUMENTO_A_OPORTUNIDADE,
    )
    regras.append("troca_de_pn")


def _decidir_pedido(
    estado: EstadoIntegracao,
    acumulador: _Acumulador,
    regras: list[str],
) -> None:
    """SitCode 60 (sale won in WBC; not to be confused with a closed order in the SAP).

    Checks, in order: no order yet (create, possibly already on the corrected partner); order closed in the SAP
    (`pedido_fechado_no_sap`, nothing is written); order corrected by hand (`pedido_corrigido_a_mao`); pending
    partner swap (`_decidir_troca_de_parceiro`); finally the revision stamped on the order, which updates it
    if WBC is newer and otherwise freezes it.
    """
    if not estado.tem_pedido:
        if estado.nasce_no_parceiro_corrigido:
            acumulador.registrar(
                f"SitCode 60 e nenhum pedido no SAP; parceiro corrigido na oportunidade "
                f"({estado.parceiro_atual or '-'} → {estado.parceiro_novo}, U_INO_Update = 'Y'): "
                f"atualiza a cotação e cria o pedido já no parceiro novo.",
                Acao.ATUALIZAR_COTACAO,
                Acao.CRIAR_PEDIDO,
                Acao.VINCULAR_DOCUMENTO_A_OPORTUNIDADE,
            )
            regras.append("cria_pedido_no_pn_corrigido")
            return
        acumulador.registrar(
            "SitCode 60 e nenhum pedido no SAP: atualiza a cotação e cria o pedido.",
            Acao.ATUALIZAR_COTACAO,
            Acao.CRIAR_PEDIDO,
            Acao.VINCULAR_DOCUMENTO_A_OPORTUNIDADE,
        )
        regras.append("cria_pedido")
        return

    # Pedido fechado no SAP: não aceita alteração nem cancelamento. Vem antes
    # de tudo porque é o fato mais duro — as outras guardas são regra de
    # negócio, esta é o SAP recusando a escrita.
    if estado.pedido_fechado_no_sap:
        # Uma troca de parceiro pedida sobre pedido fechado é pedido de negócio
        # que **nunca** poderá ser atendido: não dá para cancelar o pedido nem
        # refazê-lo. Congelar em silêncio deixaria esse caso indistinguível dos
        # 243 congelamentos benignos da janela; ele precisa de gente.
        if estado.troca_de_parceiro_pendente:
            acumulador.registrar(
                f"Troca de parceiro pedida ({estado.parceiro_pedido_sap or '-'} → "
                f"{estado.parceiro_novo}), mas o pedido está fechado no SAP: não é "
                f"possível cancelar nem refazer. Precisa de tratamento manual."
            )
            regras.append("troca_de_pn_impossivel_pedido_fechado")
            return
        acumulador.registrar("Pedido fechado no SAP: não aceita alteração nem cancelamento.")
        regras.append("pedido_fechado_no_sap")
        return

    # Antes de qualquer coisa sobre o pedido: `PN_Correc` preenchido com
    # `U_INO_Update = 'N'` significa que o pedido já está certo em nome de outro
    # parceiro, e que a troca pedida já foi concluída (a marca volta a `'N'` no
    # vínculo do pedido novo). Nada a fazer.
    #
    # A guarda precisa vir aqui, e não só no ramo da troca, porque o dano do
    # `00124045` veio pelo **outro** caminho: a correção já estava aplicada, a
    # troca não estava pendente, e era `atualizar_pedido` que reenviava o
    # `CardCode` da oportunidade e desfazia a correção.
    if estado.pedido_corrigido_a_mao:
        acumulador.registrar(
            f"Pedido já corrigido em nome de outro parceiro ({estado.parceiro_novo}) "
            f"e sem alteração pedida no WBC: nada a fazer no pedido."
        )
        regras.append("pedido_corrigido_a_mao")
        return

    # A troca de parceiro vem **antes** da comparação de revisão: refazer o
    # pedido já traz a revisão corrente junto, e atualizar um pedido que será
    # cancelado em seguida é trabalho jogado fora.
    if estado.troca_de_parceiro_pendente:
        _decidir_troca_de_parceiro(estado, acumulador, regras)
        return

    # Quem congela o pedido é a **revisão carimbada nele**, `U_INO_VERSAOWBC`
    # no `ORDR` (`GetStatusAtualPedido` no legado) — e, desde a guarda acima, o
    # `DocStatus` fechado. Não o `U_INO_Update`.
    #
    # Chegamos a congelar por `U_INO_Update = 'N'`, e estava invertido: no
    # legado o campo é o **desvio entre dois caminhos**, não uma autorização.
    # `'N'` é justamente a condição do caminho da revisão
    # (`Program.cs:249`: `count != "0" && SitCode == 60 && alterado != "Y"`),
    # e `'Y'` desvia para a troca de PN (`Program.cs:280`). Congelar em `'N'`
    # deixava a atualização por revisão sem caminho nenhum: com `'Y'` ela cai
    # no outro ramo e retorna antes de chegar aqui.
    if revisao_wbc_e_mais_nova(estado.revisao_wbc, estado.revisao_pedido_sap):
        acumulador.registrar(
            f"Pedido existente e revisão do WBC ({estado.revisao_wbc or '-'}) mais nova "
            f"que a carimbada no pedido ({estado.revisao_pedido_sap or '-'}): "
            f"atualiza o pedido.",
            Acao.ATUALIZAR_PEDIDO,
            Acao.VINCULAR_DOCUMENTO_A_OPORTUNIDADE,
        )
        regras.append("atualiza_pedido")
    else:
        regras.append("pedido_congelado")
