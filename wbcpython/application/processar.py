"""Central use case: process one quote.

Where the pieces meet: reads the state (WBC + SAP), asks the domain for the decision,
executes the actions in the SAP and records everything in the tracking store.

The business decision does **not** live here: `domain.sitcode.decidir()` decides and this
layer only executes. That split lets the whole rule be tested with no external system, and is
the visible difference from the legacy, where rule and API calls were tangled in a 380-line
`Main`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from wbcpython.application.documentos_lidos import DocumentosJaLidos
from wbcpython.domain import cotacao as regras_cotacao
from wbcpython.domain import pedido as regras_pedido
from wbcpython.domain.linhas import ResultadoLinhas, peso_da_linha
from wbcpython.domain.mapeamento import montar_payload_orcdetalhe
from wbcpython.domain.sitcode import ACOES_DE_PEDIDO as _ACOES_DE_PEDIDO
from wbcpython.domain.sitcode import Acao, Decisao, EstadoIntegracao, decidir
from wbcpython.infrastructure.service_layer.documentos import (
    RepositorioDocumentosVenda,
    RepositorioDocumentosVendaServiceLayer,
    TipoDocumento,
)
from wbcpython.infrastructure.service_layer.grupo_produtos import (
    RepositorioGrupoProdutos,
)
from wbcpython.infrastructure.service_layer.oportunidades import (
    CAMPO_CHAVE,
    RepositorioOportunidadesServiceLayer,
)
from wbcpython.infrastructure.service_layer.orcdetalhe import (
    RepositorioOrcDetalheServiceLayer,
)
from wbcpython.infrastructure.service_layer.ordens_producao import (
    RepositorioOrdensProducaoServiceLayer,
)
from wbcpython.infrastructure.service_layer.parceiros import (
    RepositorioParceirosServiceLayer,
)
from wbcpython.infrastructure.wbc_sql.models import OrcamentoWbc
from wbcpython.infrastructure.wbc_sql.repository import RepositorioOrcamentosWbcSql
from wbcpython.tracking import RepositorioTracking, StatusIntegracao, TipoEvento

logger = logging.getLogger(__name__)

#: Filial (`BPL_IDAssignedToInvoice`) atribuída aos documentos.
#: `1` é `Altamira Indústria Metalúrgica Ltda.`, a única filial ativa; a de
#: código `2` ("Principal") está desabilitada. O legado fixava este mesmo valor
#: no código-fonte.
FILIAL_PADRAO = 1

#: Ações que criam ou alteram um documento de venda no SAP. Depois de
#: qualquer uma delas o `U_INO_StatusWBC` da oportunidade precisa refletir o
#: SitCode do WBC — ver `_espelhar_status_apos_documento`.
ACOES_DE_DOCUMENTO = frozenset(
    {
        Acao.CRIAR_COTACAO,
        Acao.ATUALIZAR_COTACAO,
        Acao.CANCELAR_E_RECRIAR_COTACAO,
        Acao.CRIAR_PEDIDO,
        Acao.ATUALIZAR_PEDIDO,
        Acao.CANCELAR_E_RECRIAR_PEDIDO,
    }
)

#: As ações de cada documento. A divisão não é organização de código: cotação e
#: pedido levam conjuntos **diferentes** de campos, no cabeçalho e nas linhas,
#: e cada um tem o seu procedimento (`_executar_cotacao`, `_executar_pedido`).
ACOES_DE_COTACAO = frozenset(
    {Acao.CRIAR_COTACAO, Acao.ATUALIZAR_COTACAO, Acao.CANCELAR_E_RECRIAR_COTACAO}
)
#: Importado do domínio: é lá que a regra de "fora da janela padrão não cria
#: pedido" precisa do conjunto, e duas listas iguais divergem na primeira ação
#: nova.
ACOES_DE_PEDIDO = _ACOES_DE_PEDIDO

#: Ações que escrevem no SAP. É o que o teto por ciclo limita — ler a janela
#: inteira custa centésimos de segundo no HANA; criar documentos, não.
ACOES_DE_ESCRITA = ACOES_DE_DOCUMENTO | frozenset(
    {
        Acao.MARCAR_OPORTUNIDADE_PERDIDA,
        Acao.CANCELAR_COTACAO_NO_ENCERRAMENTO,
        Acao.ATUALIZAR_STATUS_OPORTUNIDADE,
        Acao.VINCULAR_DOCUMENTO_A_OPORTUNIDADE,
        Acao.CANCELAR_OPS_DO_PEDIDO,
    }
)

#: Decision rules that need a person and change nothing by themselves: logged as ERROR on
#: every cycle (red in the log) besides the panel, so they cannot sit unnoticed.
REGRAS_QUE_PEDEM_GENTE = frozenset(
    {"troca_de_pn_bloqueada_op_em_producao", "troca_de_pn_ops_desconhecidas"}
)


class TrocaAdiada(Exception):
    """The OPs of the order could not all be cancelled in this cycle: stop the swap here.

    Not an integration error. The order stays as it is, nothing after the OP step runs,
    and the next cycle tries again with whatever OPs are still alive. ``grave`` = a person
    must look (an OP in production showed up, a cancellation failed): logged as ERROR;
    otherwise (Controle de Produção busy) as WARNING.
    """

    def __init__(self, mensagem: str, *, grave: bool = False) -> None:
        super().__init__(mensagem)
        self.grave = grave

#: Ação executada → status a exibir no dashboard.
STATUS_POR_ACAO = {
    Acao.CRIAR_COTACAO: StatusIntegracao.COTACAO_CRIADA,
    Acao.ATUALIZAR_COTACAO: StatusIntegracao.COTACAO_ATUALIZADA,
    Acao.CANCELAR_E_RECRIAR_COTACAO: StatusIntegracao.COTACAO_CRIADA,
    Acao.CRIAR_PEDIDO: StatusIntegracao.PEDIDO_CRIADO,
    Acao.ATUALIZAR_PEDIDO: StatusIntegracao.PEDIDO_ATUALIZADO,
    Acao.CANCELAR_E_RECRIAR_PEDIDO: StatusIntegracao.PEDIDO_CRIADO,
    Acao.MARCAR_OPORTUNIDADE_PERDIDA: StatusIntegracao.ENCERRADA,
}


#: Folga na conferência do total gravado, em reais.
#:
#: O SAP arredonda em duas casas por linha e a soma pode diferir do centavo
#: calculado aqui. Um centavo de folga distingue arredondamento de linha que não
#: foi gravada — as divergências reais observadas são de milhares de reais.
TOLERANCIA_DE_TOTAL = Decimal("0.01")


class DocumentoSemValor(Exception):
    """The built document has no value, so there is nothing to send to the SAP.

    Not an integration error: the quote does not yet have anything to turn into a document.
    See `_exigir_valor`.
    """


class ParceiroCorrigidoInexistente(Exception):
    """The corrected partner (`U_INO_PN_Correc`) does not exist in the SAP, so the order was not
    created. Already recorded as an ERRO event by `_parceiro_existe`.

    Raised instead of returning `(None, None)`: that return also means "done, nothing to link",
    and the action was counted as executed — the tracking said `pedido_criado` with no order,
    every cycle (2026-11-03 review).
    """


class _SnapshotSobDemanda:
    """The OrcDetalhe snapshot of one quote, written once, on first use.

    `no_payload(dados)` writes it (the first time) and stamps its `DocEntry` into
    `U_INO_ORCAMENTO` — the same field `venda_comum` used to fill when the snapshot came in
    up front. Called only by a path that is about to send the document to the SAP.
    """

    def __init__(self, gravar: Any) -> None:
        self._gravar = gravar
        self._feito = False
        self._doc_entry: int | None = None

    def no_payload(self, dados: dict[str, Any]) -> dict[str, Any]:
        if not self._feito:
            self._doc_entry = self._gravar()
            self._feito = True
        if self._doc_entry:
            return {**dados, "U_INO_ORCAMENTO": self._doc_entry}
        return dados


def total_do_payload(dados: dict[str, Any]) -> Decimal:
    """Sum of `LineTotal` over the payload lines.

    The pre-tax total computed before sending, the same quantity `total_das_linhas` re-reads from
    the SAP afterwards. `Decimal` on purpose: lines carry `float` (what the Service Layer JSON
    accepts), and summing floats to compare with zero is exactly where a legitimate total of a few
    cents could become zero.

    Until 15/09/2026 it summed `Quantity * Price`; the line now carries `LineTotal` (see
    `domain.linhas`) and both sides of the check changed together — comparing a sent total with a
    re-read product would compare different quantities.
    """
    total = Decimal(0)
    for linha in dados.get("DocumentLines") or ():
        total += _decimal(linha.get("LineTotal")) or Decimal(0)
    return total


@dataclass(frozen=True, slots=True)
class ResultadoProcessamento:
    orcnum: str
    decisao: Decisao
    acoes_executadas: tuple[Acao, ...] = ()
    erro: str = ""

    @property
    def sucesso(self) -> bool:
        return not self.erro


def _kg(peso: Decimal) -> str:
    """`226.43` → `226,43`, as people read it on the SAP screen."""
    return f"{peso:.2f}".replace(".", ",")


class ProcessadorDeOrcamento:
    """Orchestrates the processing of one quote, from the WBC to the SAP."""

    def __init__(
        self,
        *,
        wbc: RepositorioOrcamentosWbcSql,
        orcdetalhe: RepositorioOrcDetalheServiceLayer,
        documentos: RepositorioDocumentosVendaServiceLayer,
        oportunidades: RepositorioOportunidadesServiceLayer,
        tracking: RepositorioTracking,
        grupo_produtos: RepositorioGrupoProdutos | None = None,
        parceiros: RepositorioParceirosServiceLayer | None = None,
        filial: int = FILIAL_PADRAO,
        gravar_snapshot: bool = True,
        somente_leitura: bool = False,
        corte_de_pedido: date | None = None,
        ordens_producao: RepositorioOrdensProducaoServiceLayer | None = None,
        controle_producao_ocupado: Callable[[], bool | None] | None = None,
    ) -> None:
        self._wbc = wbc
        #: Both feed `Acao.CANCELAR_OPS_DO_PEDIDO`. Missing either one, the swap of an order
        #: with planned OPs waits (`TrocaAdiada`) instead of cancelling blind.
        self._ordens_producao = ordens_producao
        self._controle_producao_ocupado = controle_producao_ocupado
        self._orcdetalhe = orcdetalhe
        self._documentos = documentos
        self._oportunidades = oportunidades
        self._tracking = tracking
        self._grupo_produtos = grupo_produtos
        self._parceiros = parceiros
        self._filial = filial
        self._gravar_snapshot = gravar_snapshot
        #: Primeiro dia da janela PADRÃO. Oportunidade anterior a ele não ganha
        #: pedido — ver `EstadoIntegracao.fora_da_janela_padrao`. `None` desliga
        #: a regra, que é o que um teste ou um caso pontual espera.
        self._corte_de_pedido = corte_de_pedido
        #: Ensaio: decide e registra tudo, mas não executa nenhuma ação.
        #:
        #: A garantia não vem de filtrar ações uma a uma — vem de o único
        #: caminho que escreve (`_executar`) não ser chamado. Não há como uma
        #: ação nova nascer fora do ensaio por esquecimento.
        self._somente_leitura = somente_leitura

    # ------------------------------------------------------------------ fluxo

    def processar(
        self,
        oportunidade: dict[str, Any],
        situacao: tuple[int, str] | None = None,
    ) -> ResultadoProcessamento:
        """Process one SAP opportunity against its quote in the WBC.

        Never raises: a failure on one quote becomes a recorded error and the worker loop moves on to
        the next. Direct fix for the legacy defect where one bad record aborted the whole run.
        """
        orcnum = str(oportunidade.get("U_ORCNUM_WBC") or "").strip()
        if not orcnum:
            logger.warning(
                "Oportunidade %s sem número de orçamento — ignorada.", oportunidade.get(CAMPO_CHAVE)
            )
            return ResultadoProcessamento(
                orcnum="",
                decisao=Decisao(regra="sem_numero_de_orcamento"),
                erro="Oportunidade sem U_ORCNUM_WBC.",
            )

        try:
            return self._processar_orcamento(orcnum, oportunidade, situacao)
        except Exception as exc:
            logger.exception("Falha ao processar o orçamento %s.", orcnum)
            self._tracking.registrar_erro(orcnum, f"{type(exc).__name__}: {exc}")
            return ResultadoProcessamento(
                orcnum=orcnum, decisao=Decisao(regra="erro"), erro=str(exc)
            )

    def _processar_orcamento(
        self,
        orcnum: str,
        oportunidade: dict[str, Any],
        situacao: tuple[int, str] | None = None,
    ) -> ResultadoProcessamento:
        """Decide first; load the whole quote only if there is an action.

        `situacao` is `(sitcode, revisao)` read in batch by the worker. With it the decision comes out
        without touching the quote lines, and loading lines is expensive: 279 ms against 29 ms for the
        situation, measured in the real environment. Most quotes in the window have no action, so
        skipping that load is the difference between an eight-minute cycle and a one-minute one.

        A quote with an action loads the full quote and **redoes** state and decision on it. Not
        redundant: the document is built from the same snapshot that authorised the action, not from a
        version that changed between the two reads. In the ensaio (`somente_leitura`) it stops after
        recording.
        """
        if situacao is not None:
            decisao_previa = self._decidir_com_situacao(orcnum, oportunidade, situacao)
            if decisao_previa is not None:
                return decisao_previa

        orcamento = self._wbc.buscar_orcamento(orcnum)
        if orcamento is None:
            mensagem = f"Orçamento {orcnum} não encontrado no WBC."
            self._tracking.registrar_erro(orcnum, mensagem)
            return ResultadoProcessamento(
                orcnum=orcnum, decisao=Decisao(regra="orcamento_inexistente"), erro=mensagem
            )

        estado, decisao = decidir_pelo_orcamento(
            orcamento, oportunidade, self._documentos, corte_de_pedido=self._corte_de_pedido
        )
        _alertar_se_pede_gente(orcnum, decisao)

        self._tracking.registrar_verificacao(
            orcnum,
            oppr_id=_inteiro(oportunidade.get(CAMPO_CHAVE)),
            cliente=orcamento.cliente_nome,
            vendedor=orcamento.representante,
            municipio=orcamento.municipio,
            uf=orcamento.uf,
            sitcode_wbc=orcamento.sitcode,
            revisao_wbc=orcamento.revisao,
            sitcode_sap=estado.sitcode_sap,
            regra=decisao.regra,
            data_abertura=_data(oportunidade.get("OpenDate")),
        )
        self._tracking.registrar_evento(
            orcnum,
            regra=decisao.regra,
            mensagem=" ".join(decisao.motivos),
            detalhes={"acoes": [a.value for a in decisao.acoes]},
        )

        if not decisao.tem_acao:
            self._tracking.registrar_verificacao(
                orcnum, status=StatusIntegracao.SEM_ACAO, regra=decisao.regra
            )
            return ResultadoProcessamento(orcnum=orcnum, decisao=decisao)

        if self._somente_leitura:
            return self._registrar_ensaio(orcnum, decisao)

        executadas = self._executar(estado, orcamento, oportunidade, decisao)
        return ResultadoProcessamento(orcnum=orcnum, decisao=decisao, acoes_executadas=executadas)

    def _registrar_ensaio(self, orcnum: str, decisao: Decisao) -> ResultadoProcessamento:
        """Close the quote in the ensaio: recorded, decided, not executed.

        The status is **not** touched. A new row is born `PENDENTE`, which is the truth: evaluated,
        action identified, not done. A row already carrying the result of a real cycle must not be
        downgraded by an ensaio, which undid nothing in the SAP.

        The event says out loud that it was an ensaio; without it, weeks later, a row stuck on
        "Pendente" would be indistinguishable from one that failed silently.
        """
        acoes = ", ".join(a.value for a in decisao.acoes)
        self._tracking.registrar_evento(
            orcnum,
            regra=decisao.regra,
            mensagem=(
                f"Simulação: {len(decisao.acoes)} ação(ões) decidida(s) e "
                f"**não** executada(s) ({acoes}). Nada foi escrito no SAP."
            ),
            detalhes={"simulacao": True, "acoes_previstas": [a.value for a in decisao.acoes]},
        )
        return ResultadoProcessamento(orcnum=orcnum, decisao=decisao)

    def _decidir_com_situacao(
        self,
        orcnum: str,
        oportunidade: dict[str, Any],
        situacao: tuple[int, str],
    ) -> ResultadoProcessamento | None:
        """Decide from the batch-read situation. `None` means "has an action, carry on".

        The record written for a quote with no action is partial on purpose: client, salesperson and
        city live in the lines, which were not loaded. The tracking store keeps what it already knew
        for those fields, and writing `''` over it would be worse than not writing.
        """
        sitcode, revisao = situacao
        estado, decisao = decidir_pela_situacao(
            orcnum, situacao, oportunidade, self._documentos, corte_de_pedido=self._corte_de_pedido
        )
        if precisa_do_orcamento(decisao):
            return None
        _alertar_se_pede_gente(orcnum, decisao)

        self._tracking.registrar_verificacao(
            orcnum,
            oppr_id=_inteiro(oportunidade.get(CAMPO_CHAVE)),
            sitcode_wbc=sitcode,
            revisao_wbc=revisao,
            sitcode_sap=estado.sitcode_sap,
            regra=decisao.regra,
            status=StatusIntegracao.SEM_ACAO,
            data_abertura=_data(oportunidade.get("OpenDate")),
        )
        self._tracking.registrar_evento(
            orcnum,
            regra=decisao.regra,
            mensagem=" ".join(decisao.motivos),
            detalhes={"acoes": []},
        )
        return ResultadoProcessamento(orcnum=orcnum, decisao=decisao)

    # ------------------------------------------------------------- execução

    def _executar(
        self,
        estado: EstadoIntegracao,
        orcamento: OrcamentoWbc,
        oportunidade: dict[str, Any],
        decisao: Decisao,
    ) -> tuple[Acao, ...]:
        orcnum = orcamento.orcnum
        oppr_id = _inteiro(oportunidade.get(CAMPO_CHAVE))
        executadas: list[Acao] = []
        ultimo_documento: dict[str, Any] | None = None
        tipo_ultimo: TipoDocumento | None = None

        # The snapshot's `DocEntry` feeds the quote/order UDF `U_INO_ORCAMENTO` (see
        # `_gravar_snapshot_orcdetalhe`), so it is written lazily, once, right before the first
        # document write (01/10/2026 review): it used to be written before ANY action — a quote with
        # no items (DocumentoSemValor), a status-only change or a missing partner wrote a
        # new @INO_ORCAM row every 3-minute cycle with no document to point at it.
        snapshot = _SnapshotSobDemanda(
            lambda: self._gravar_snapshot_orcdetalhe(orcamento) if self._gravar_snapshot else None
        )
        ops_canceladas: list[int] = []
        adiada = False

        for acao in decisao.acoes:
            if acao is Acao.VINCULAR_DOCUMENTO_A_OPORTUNIDADE:
                # Depende do documento gerado nas ações anteriores desta mesma
                # decisão; sem documento novo, não há o que vincular.
                if oppr_id and ultimo_documento and tipo_ultimo:
                    # DocEntry, não DocNum — ver `vincular_documento`.
                    doc_entry = _inteiro(ultimo_documento.get("DocEntry"))
                    total = _decimal(ultimo_documento.get("DocTotal"))
                    if doc_entry and self._vincular(
                        orcnum, oppr_id, tipo_ultimo, doc_entry, float(total) if total else None
                    ):
                        executadas.append(acao)
                continue

            if acao is Acao.ATUALIZAR_STATUS_OPORTUNIDADE:
                if oppr_id:
                    self._oportunidades.atualizar_status(oppr_id, orcamento.sitcode)
                    executadas.append(acao)
                continue

            if acao is Acao.CANCELAR_COTACAO_NO_ENCERRAMENTO:
                if self._cancelar_cotacao_no_encerramento(orcamento.orcnum, oportunidade):
                    executadas.append(acao)
                continue

            if acao is Acao.MARCAR_OPORTUNIDADE_PERDIDA:
                if oppr_id:
                    self._oportunidades.encerrar(oppr_id, orcamento.sitcode)
                    executadas.append(acao)
                continue

            if acao in (Acao.CANCELAR_OPS_DO_PEDIDO, Acao.CANCELAR_E_RECRIAR_PEDIDO):
                try:
                    if acao is Acao.CANCELAR_OPS_DO_PEDIDO:
                        # The order's own guards run first: cancelling the OPs and then
                        # giving up on the order would leave it alive with no production.
                        self._conferir_pedido_novo(estado, orcamento, oportunidade)
                        ops_canceladas = self._cancelar_ops_do_pedido(orcnum)
                    else:
                        # Every swap, with or without OPs in the decision: the count came
                        # from the read at the start of the cycle.
                        self._garantir_pedido_sem_op_viva(orcnum)
                except TrocaAdiada as motivo:
                    self._registrar_adiamento(orcnum, motivo)
                    adiada = True
                    # The order must not be cancelled with OPs alive: nothing after this
                    # step runs in this cycle.
                    break
                if acao is Acao.CANCELAR_OPS_DO_PEDIDO:
                    executadas.append(acao)
                    continue

            try:
                documento, tipo = self._executar_documento(
                    acao, estado, orcamento, oportunidade, snapshot
                )
            except (DocumentoSemValor, ParceiroCorrigidoInexistente):
                # A ação **não** entra em `executadas`: nada foi escrito, e
                # contá-la faria o espelhamento de status e o teto de escrita
                # tratarem um documento que não existe como se existisse.
                continue
            except Exception as exc:
                if ops_canceladas:
                    self._avisar_ops_canceladas_sem_troca(orcnum, ops_canceladas, exc)
                raise
            if documento is not None and tipo is not None:
                ultimo_documento, tipo_ultimo = documento, tipo
                self._registrar_documento(orcnum, tipo, documento)
            executadas.append(acao)

        if adiada and not executadas:
            # Nothing was written: an empty "Ações executadas" event every cycle
            # while the swap waits would bury the history (ACAO events are not deduped).
            return ()

        self._espelhar_status_apos_documento(oppr_id, estado, orcamento, executadas)

        # Nada de "baixar a marca" **depois da troca**. Chegamos a fazer isso,
        # escrevendo `U_INO_Update = 'N'` e `U_INO_PN_Correc = ''`, e as duas
        # escritas estavam erradas: `U_INO_Update` sinaliza alteração de
        # **valores**, e não a troca de parceiro; e apagar o `PN_Correc`
        # destruiria o registro de qual correção foi pedida. A idempotência vem
        # do próprio pedido — `troca_de_parceiro_pendente` compara o parceiro
        # que ele já tem —, exatamente como o `ChecaPNPedido` do legado.
        #
        # Isto **não** quer dizer que a integração nunca toque no campo: ela
        # baixa `U_INO_Update` ao vincular o pedido, junto com o estágio, como
        # o legado (`ServiceProcess.cs:198`, `:210`). O que não existe é uma
        # escrita avulsa aqui, no fim da troca.

        status = self._status_final(executadas)
        if status is not None:
            self._tracking.registrar_verificacao(orcnum, status=status, regra=decisao.regra)
        self._tracking.registrar_evento(
            orcnum,
            tipo=TipoEvento.ACAO,
            regra=decisao.regra,
            mensagem="Ações executadas: " + ", ".join(a.value for a in executadas),
        )
        return tuple(executadas)

    def _cancelar_ops_do_pedido(self, orcnum: str) -> list[int]:
        """Cancel every planned OP of the current order, or raise `TrocaAdiada`.

        Re-reads the OPs here, at execution time: the count that made the decision may be
        minutes old. Stops before cancelling anything when the Controle de Produção is (or
        may be) running, or when a released or closed OP shows up; stops without touching
        the order when a cancellation fails or an OP survives. Returns the DocNums cancelled.
        """
        doc_entry, vivas = self._ler_ops_do_pedido(orcnum)
        em_producao = [op for op in vivas if not op.planejada]
        if em_producao:
            raise TrocaAdiada(
                f"{len(em_producao)} OP(s) liberada(s) ou encerrada(s) no pedido "
                f"({_lista_de_ops(em_producao)}); nenhuma OP cancelada, precisa de uma pessoa.",
                grave=True,
            )

        ordens = self._ordens_producao
        if ordens is None:  # `_ler_ops_do_pedido` already refused; kept for the type checker
            raise TrocaAdiada("sem acesso às OPs nesta execução; nada feito.", grave=True)
        canceladas: list[int] = []
        for op in vivas:
            try:
                ordens.cancelar(op.doc_entry)
            except Exception as exc:  # noqa: BLE001 - one failure stops the swap, never the cycle
                self._registrar_ops_canceladas(orcnum, canceladas)
                raise TrocaAdiada(
                    f"falha ao cancelar a OP {op.doc_num} ({type(exc).__name__}: {exc}); "
                    f"canceladas antes dela: {_numeros(canceladas)}. O pedido não foi "
                    f"cancelado; o próximo ciclo segue com as que sobraram.",
                    grave=True,
                ) from exc
            canceladas.append(op.doc_num)

        self._registrar_ops_canceladas(orcnum, canceladas)
        restantes = ordens.vivas(doc_entry)
        if restantes:
            raise TrocaAdiada(
                f"{len(restantes)} OP(s) ainda viva(s) depois do cancelamento "
                f"({_lista_de_ops(restantes)}); canceladas: {_numeros(canceladas)}. "
                f"O pedido não foi cancelado.",
                grave=True,
            )
        return canceladas

    def _ler_ops_do_pedido(self, orcnum: str) -> tuple[int, list[Any]]:
        """`(order DocEntry, live OPs)` read now — refusing while the Controle de Produção
        is, or may be, mid-run (it creates OPs after marking the order processed)."""
        if self._ordens_producao is None:
            raise TrocaAdiada("sem acesso às OPs nesta execução; nada feito.", grave=True)
        ocupado = (
            self._controle_producao_ocupado() if self._controle_producao_ocupado else None
        )
        if ocupado is None:
            # Same machine: a probe that keeps failing means a stuck service or a wrong
            # port, and the swap would wait forever without anything red.
            raise TrocaAdiada(
                "não foi possível saber se o Controle de Produção está processando; "
                "nada feito.",
                grave=True,
            )
        if ocupado:
            raise TrocaAdiada(
                "o Controle de Produção está processando; nada feito, tenta no próximo ciclo."
            )
        doc_entry = self._documentos.doc_entry(TipoDocumento.PEDIDO, orcnum)
        if doc_entry is None:
            raise TrocaAdiada("pedido vigente não encontrado no SAP; nada feito.", grave=True)
        return doc_entry, self._ordens_producao.vivas(doc_entry)

    def _garantir_pedido_sem_op_viva(self, orcnum: str) -> None:
        """Last check before the swap cancels the order: no OP alive on it, read now."""
        _, vivas = self._ler_ops_do_pedido(orcnum)
        if vivas:
            raise TrocaAdiada(
                f"{len(vivas)} OP(s) viva(s) no pedido ({_lista_de_ops(vivas)}) na hora de "
                f"cancelá-lo; o pedido não foi cancelado.",
                grave=True,
            )

    def _conferir_pedido_novo(
        self, estado: EstadoIntegracao, orcamento: OrcamentoWbc, oportunidade: dict[str, Any]
    ) -> None:
        """The guards `_executar_pedido` applies to the swap, run before any OP is cancelled.

        Partner missing or an order with no value make the swap give up on the order;
        found only after the OPs, they left it alive with its OPs cancelled (review of the
        F2, 20/10/2026). The SAP refusing the order's own Cancel has no cheap pre-check:
        `_avisar_ops_canceladas_sem_troca` covers that one.
        """
        dados = self._payload_pedido(orcamento, estado, oportunidade, None)
        try:
            self._exigir_valor(TipoDocumento.PEDIDO, orcamento.orcnum, dados)
        except DocumentoSemValor as exc:
            raise TrocaAdiada(
                f"o pedido novo não teria valor ({exc}); nenhuma OP cancelada.", grave=True
            ) from exc
        if not self._parceiro_existe(estado, orcamento.orcnum):
            raise TrocaAdiada(
                f"o parceiro corrigido {estado.parceiro_novo} não existe no SAP; "
                f"nenhuma OP cancelada.",
                grave=True,
            )

    def _registrar_ops_canceladas(self, orcnum: str, canceladas: list[int]) -> None:
        """The DocNums people search for — on success and on every partial failure."""
        if not canceladas:
            return
        texto = _numeros(canceladas)
        logger.warning(
            "Orçamento %s: %d OP(s) planejada(s) canceladas para a troca de PN: %s",
            orcnum, len(canceladas), texto,
        )
        self._tracking.registrar_evento(
            orcnum,
            tipo=TipoEvento.ACAO,
            regra="troca_de_pn_cancelou_ops",
            mensagem=f"OPs planejadas canceladas para a troca de PN: {texto}.",
            detalhes={"ops": canceladas},
        )

    def _registrar_adiamento(self, orcnum: str, motivo: TrocaAdiada) -> None:
        texto = f"Troca de PN adiada: {motivo}"
        if motivo.grave:
            logger.error("Orçamento %s: %s", orcnum, texto)
            # ERROR status: the panel shows it in red until a later cycle completes.
            self._tracking.registrar_erro(orcnum, texto)
        else:
            logger.warning("Orçamento %s: %s", orcnum, texto)
            self._tracking.registrar_evento(orcnum, regra="troca_de_pn_adiada", mensagem=texto)

    def _avisar_ops_canceladas_sem_troca(
        self, orcnum: str, canceladas: list[int], exc: Exception
    ) -> None:
        """OPs are gone and the order is still alive: the PCP has to know which ones."""
        texto = (
            f"OPs planejadas {_numeros(canceladas)} foram canceladas, mas o pedido não foi "
            f"cancelado nem recriado ({type(exc).__name__}: {exc}). O pedido segue vivo sem "
            f"essas OPs: avise o PCP."
        )
        logger.error("Orçamento %s: %s", orcnum, texto)
        self._tracking.registrar_evento(
            orcnum,
            tipo=TipoEvento.ERRO,
            regra="troca_de_pn_pedido_nao_cancelado",
            mensagem=texto,
            detalhes={"ops": canceladas},
        )

    def _vincular(
        self,
        orcnum: str,
        oppr_id: int,
        tipo: TipoDocumento,
        doc_entry: int,
        total: float | None,
    ) -> bool:
        """Link the new document to the opportunity; a failure is a warning, not an error.

        01/10/2026 review: the exception used to leave `_executar` before
        `_espelhar_status_apos_documento`. On `emitido_apos_revisao_no_sap` only the mirror
        (`U_INO_StatusWBC` back to 30) ends the rule, so a link failing every time cancelled
        and recreated the quotation every 3-minute cycle. The document itself is still found
        next cycle — by `U_INO_COTWBC` on the document, not by this link — so going on is safe;
        what is lost is the opportunity's stage line, recorded here as an ERRO event for the
        painel. Same treatment as `_cancelar_cotacao_no_encerramento`.
        """
        try:
            self._oportunidades.vincular_documento(oppr_id, tipo, doc_entry, total)
        except Exception as exc:  # noqa: BLE001 - see the docstring
            aviso = (
                f"{tipo.rotulo.capitalize()} {doc_entry} criada, mas o vínculo com a "
                f"oportunidade {oppr_id} falhou ({type(exc).__name__}: {exc}). O documento "
                f"vale e é achado pelo orçamento; falta só a linha de estágio na oportunidade."
            )
            logger.warning("Orçamento %s: %s", orcnum, aviso)
            self._tracking.registrar_evento(orcnum, tipo=TipoEvento.ERRO, mensagem=aviso)
            return False
        return True

    def _cancelar_cotacao_no_encerramento(
        self,
        orcnum: str,
        oportunidade: dict[str, Any],
    ) -> bool:
        """Cancel the quote of the closed budget; a failure does not break the cycle.

        The SAP refusing is an expected outcome, not a defect: the quote may have been converted,
        closed by hand or cancelled by someone between the read and the write. The warning is recorded
        and the closing of the opportunity goes on, which is the effect the legacy already had.

        Returns:
            True if the quote was cancelled; False if none was found or the SAP refused.
        """
        doc_entry = fonte_de_documentos(oportunidade, self._documentos).doc_entry(
            TipoDocumento.COTACAO, orcnum
        )
        if doc_entry is None:
            return False
        try:
            self._documentos.cancelar(TipoDocumento.COTACAO, doc_entry)
        except Exception as erro:  # noqa: BLE001
            aviso = f"Não foi possível cancelar a cotação {doc_entry} no encerramento: {erro}"
            logger.warning("Orçamento %s: %s", orcnum, aviso)
            self._tracking.registrar_evento(orcnum, tipo=TipoEvento.ERRO, mensagem=aviso)
            return False
        return True

    def _espelhar_status_apos_documento(
        self,
        oppr_id: int | None,
        estado: EstadoIntegracao,
        orcamento: OrcamentoWbc,
        executadas: list[Acao],
    ) -> None:
        """Touched a document: the opportunity's `U_INO_StatusWBC` follows.

        Without this a freshly integrated opportunity kept the old status (typically `"0"`): the rules
        that create or update a document (`sem_cotacao_cria`, `emitido_sem_cotacao`,
        `revisao_sobre_cotacao_emitida`) do not carry `atualizar_status_oportunidade` among their
        actions, because in the legacy the mirroring was built into saving the opportunity
        (`AddCotacaoOportunidade` writes `U_INO_StatusWBC` together with the link).

        Forgetting it is silent and expensive: next run the SAP still says `"0"`, the decision is
        "create" again, and the integration recreates the document, again and again.

        An explicit step after any document action. It skips (no useless or harmful call) when:
        nothing was executed or there is no `oppr_id`; `ATUALIZAR_STATUS_OPORTUNIDADE` already did it;
        the opportunity was just closed (`MARCAR_OPORTUNIDADE_PERDIDA`) — never reopen it; no document
        action ran; or the SAP already reflects the SitCode.
        """
        if oppr_id is None or not executadas:
            return
        if Acao.ATUALIZAR_STATUS_OPORTUNIDADE in executadas:
            return
        if Acao.MARCAR_OPORTUNIDADE_PERDIDA in executadas:
            return
        if not any(acao in ACOES_DE_DOCUMENTO for acao in executadas):
            return
        if estado.sitcode_sap.strip() == str(orcamento.sitcode):
            return

        self._oportunidades.atualizar_status(oppr_id, orcamento.sitcode)
        logger.info(
            "Orçamento %s: status da oportunidade espelhado para %s após ação de documento.",
            orcamento.orcnum,
            orcamento.sitcode,
        )

    def _executar_documento(
        self,
        acao: Acao,
        estado: EstadoIntegracao,
        orcamento: OrcamentoWbc,
        oportunidade: dict[str, Any],
        snapshot: _SnapshotSobDemanda | None = None,
    ) -> tuple[dict[str, Any] | None, TipoDocumento | None]:
        """Route the action to the procedure of the matching document.

        Quote and order have **separate** procedures (`_executar_cotacao`, `_executar_pedido`) because
        the two documents do not carry the same fields. While they shared one path with a type
        parameter, it was easy to send a field of one into the other: `U_INO_Composicao` once went out
        on a quote, where it shows on the customer's printout.

        Returns:
            `(document, type)`, or `(None, None)` when the action produced no document to link.
        """
        snapshot = snapshot or _SnapshotSobDemanda(lambda: None)
        if acao in ACOES_DE_COTACAO:
            return self._executar_cotacao(acao, estado, orcamento, oportunidade, snapshot)
        if acao in ACOES_DE_PEDIDO:
            return self._executar_pedido(acao, estado, orcamento, oportunidade, snapshot)
        return None, None

    def _parceiro_existe(self, estado: EstadoIntegracao, orcnum: str) -> bool:
        """Does the corrected partner exist in the SAP? (The legacy `ChecaPN`.)

        Covers both paths that write the order on `PN_Correc`: the swap (cancel and recreate) and
        creation already corrected. Creating on the opportunity's partner "meanwhile" would be worse
        than not creating: the link clears `U_INO_Update` and the order would stay on the wrong partner
        forever — see `EstadoIntegracao.nasce_no_parceiro_corrigido`.

        Returns True without a lookup when the order is not on the corrected partner, or when no
        partners repository was injected (the pre-existing behaviour, relied on by tests that do not
        exercise the swap). On False it records an ERRO event.
        """
        if not estado.pedido_no_parceiro_corrigido or self._parceiros is None:
            return True
        if self._parceiros.existe(estado.parceiro_novo):
            return True

        aviso = (
            f"Parceiro corrigido {estado.parceiro_novo} não existe no SAP: "
            + (
                "o pedido não foi criado (criá-lo no parceiro da oportunidade o "
                "deixaria no parceiro errado para sempre)."
                if estado.nasce_no_parceiro_corrigido
                else "o pedido não foi refeito (cancelá-lo o deixaria sem substituto)."
            )
        )
        logger.warning("Orçamento %s: %s", orcnum, aviso)
        self._tracking.registrar_evento(orcnum, tipo=TipoEvento.ERRO, mensagem=aviso)
        return False

    def _exigir_valor(self, tipo: TipoDocumento, orcnum: str, dados: dict[str, Any]) -> None:
        """Refuse, before sending, a document the SAP would reject.

        The SAP answers `HTTP 400 | SAP -5002 | "Document total value must be zero or greater than
        zero"` to a document with no value. Reaching that turns into a cycle error, shows on the
        dashboard as a failure and drowns in noise — when the cause is mundane: the quote has no items
        yet in the WBC (case 00125275, zero items) or its items did not get a price.

        That is not an integration failure. It goes to the log and the tracking store and the cycle
        goes on; once the quote gains items, the next run creates the document normally — the decision
        depends on a quote existing, not on having tried already.

        Raises:
            DocumentoSemValor: the payload total is not greater than zero.
        """
        total = total_do_payload(dados)
        if total > 0:
            return
        linhas = len(dados.get("DocumentLines") or ())
        motivo = "sem itens" if linhas == 0 else f"{linhas} item(ns) somando {total}"
        aviso = (
            f"{tipo.rotulo.capitalize()} não criada: o orçamento está {motivo}, "
            f"e o SAP recusa documento sem valor."
        )
        logger.info("Orçamento %s: %s", orcnum, aviso)
        self._tracking.registrar_evento(orcnum, tipo=TipoEvento.DECISAO, mensagem=aviso)
        raise DocumentoSemValor(aviso)

    # ------------------------------------------------------------- cotação

    def _executar_cotacao(
        self,
        acao: Acao,
        estado: EstadoIntegracao,
        orcamento: OrcamentoWbc,
        oportunidade: dict[str, Any],
        snapshot: _SnapshotSobDemanda,
    ) -> tuple[dict[str, Any] | None, TipoDocumento | None]:
        """Creation and maintenance of the **quote**."""
        # Built without the snapshot first: the value check needs no SAP write.
        dados = self._payload_cotacao(orcamento, estado, oportunidade, None)
        tipo = TipoDocumento.COTACAO
        self._exigir_valor(tipo, orcamento.orcnum, dados)

        if acao is Acao.CRIAR_COTACAO:
            return self._documentos.criar(tipo, snapshot.no_payload(dados)), tipo
        if acao is Acao.CANCELAR_E_RECRIAR_COTACAO:
            return (
                self._documentos.cancelar_e_recriar(
                    tipo, orcamento.orcnum, snapshot.no_payload(dados)
                ),
                tipo,
            )
        if acao is Acao.ATUALIZAR_COTACAO:
            doc_entry = self._documentos.doc_entry(tipo, orcamento.orcnum)
            if doc_entry is not None:
                dados = snapshot.no_payload(dados)
                self._documentos.atualizar(tipo, doc_entry, dados)
                return self._conferir_cotacao(orcamento.orcnum, doc_entry, dados)
            return None, None
        return None, None

    def _conferir_cotacao(
        self, orcnum: str, doc_entry: int, dados: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, TipoDocumento | None]:
        """Re-read the updated quote and, if it diverges from the budget, redo it.

        **A `204 No Content` does not prove the lines changed.** On quotes `00125616` and `00125577`
        the `PATCH` was accepted, the header saved, and the lines stayed as they were — one at price
        zero, leaving the document R$ 1.604,38 and R$ 39.802,60 below the budget. The integration
        recorded success and the error reached the user through the document, not the panel. See
        `DECISOES.md`.

        Cancel-and-recreate is the path that **provably** works: it is what the user did by hand on
        `00125616`, and the new quote came out with both values right. Quotes only: the order has its
        own guards (`U_INO_Congelado`, `DocStatus`) and recreating it weighs differently.

        Returns:
            `(document, type)` of the recreated quote; `(None, None)` when the totals match or the
            re-read failed (the update is kept as is).
        """
        esperado = total_do_payload(dados)
        try:
            gravado = self._documentos.total_das_linhas(TipoDocumento.COTACAO, doc_entry)
        except Exception as exc:  # noqa: BLE001 — a conferência não derruba o ciclo
            logger.warning(
                "Orçamento %s: não foi possível reler a cotação %s para conferir "
                "(%s: %s). A atualização foi mantida como está.",
                orcnum,
                doc_entry,
                type(exc).__name__,
                exc,
            )
            return None, None

        if abs(gravado - esperado) <= TOLERANCIA_DE_TOTAL:
            logger.info(
                "[line-total] %s: cotação %s conferida — LineTotal no SAP soma %s, "
                "orçamento soma %s.",
                orcnum,
                doc_entry,
                gravado,
                esperado,
            )
            return None, None

        logger.warning(
            "[line-total] %s: a cotação %s ficou em %s (soma de LineTotal) depois da "
            "atualização, e o orçamento soma %s. O SAP aceitou o PATCH sem trocar as "
            "linhas — cancelando e recriando a cotação.",
            orcnum,
            doc_entry,
            gravado,
            esperado,
        )
        self._tracking.registrar_evento(
            orcnum,
            regra="cotacao_refeita_apos_divergencia",
            mensagem=(
                f"Atualização aceita pelo SAP mas sem efeito nas linhas: documento "
                f"ficou em {gravado} contra {esperado} do orçamento. Cotação "
                f"cancelada e recriada."
            ),
        )
        return (
            self._documentos.cancelar_e_recriar(TipoDocumento.COTACAO, orcnum, dados),
            TipoDocumento.COTACAO,
        )

    def _payload_cotacao(
        self,
        orcamento: OrcamentoWbc,
        estado: EstadoIntegracao,
        oportunidade: dict[str, Any] | None = None,
        snapshot_id: int | None = None,
    ) -> dict[str, Any]:
        """Quote body: `domain.cotacao` header plus the lines."""
        payload = regras_cotacao.montar_payload(
            orcamento,
            # A cotação nunca muda de parceiro: no legado, `CriaCotacao` recebe
            # o `CardCode` da oportunidade, e `PNNew` só aparece no pedido.
            parceiro=estado.parceiro_atual,
            filial=self._filial,
            snapshot_id=snapshot_id,
            oportunidade=oportunidade,
        )
        linhas = self._montar_linhas(orcamento, regras_cotacao.linhas)
        if linhas:
            payload["DocumentLines"] = linhas
        return payload

    # -------------------------------------------------------------- pedido

    def _executar_pedido(
        self,
        acao: Acao,
        estado: EstadoIntegracao,
        orcamento: OrcamentoWbc,
        oportunidade: dict[str, Any],
        snapshot: _SnapshotSobDemanda,
    ) -> tuple[dict[str, Any] | None, TipoDocumento | None]:
        """Creation and maintenance of the **sales order**."""
        # Built without the snapshot first: the value and partner checks need no SAP write.
        dados = self._payload_pedido(orcamento, estado, oportunidade, None)
        tipo = TipoDocumento.PEDIDO
        self._exigir_valor(tipo, orcamento.orcnum, dados)

        if acao is Acao.CRIAR_PEDIDO:
            # Pedido nascendo já no `PN_Correc` (`U_INO_Update = 'Y'` antes de
            # existir pedido): a mesma guarda `ChecaPN` da troca. Fora desse
            # caso `_parceiro_existe` devolve True sem ir à rede.
            if not self._parceiro_existe(estado, orcamento.orcnum):
                raise ParceiroCorrigidoInexistente(orcamento.orcnum)
            return (
                self._documentos.criar(tipo, regras_pedido.com_uso_principal(snapshot.no_payload(dados))),
                tipo,
            )
        if acao is Acao.CANCELAR_E_RECRIAR_PEDIDO:
            # `cancelar_e_recriar` cancela **antes** de criar. Se o parceiro
            # corrigido não existir no SAP, o orçamento fica sem pedido nenhum
            # — pior que o estado inicial, e irreversível. O legado protegia
            # isso com `ChecaPN`, e a proteção vinha antes de tudo.
            if not self._parceiro_existe(estado, orcamento.orcnum):
                raise ParceiroCorrigidoInexistente(orcamento.orcnum)
            # O payload já prioriza o PN novo quando há troca de parceiro.
            return (
                self._documentos.cancelar_e_recriar(
                    tipo, orcamento.orcnum,
                    regras_pedido.com_uso_principal(snapshot.no_payload(dados)),
                ),
                tipo,
            )
        if acao is Acao.ATUALIZAR_PEDIDO:
            doc_entry = self._documentos.doc_entry(tipo, orcamento.orcnum)
            if doc_entry is not None:
                corpo = self._respeitar_congelamento(orcamento.orcnum, snapshot.no_payload(dados))
                self._documentos.atualizar(
                    tipo, doc_entry, self._manter_detalhe_das_ops(estado, orcamento, corpo)
                )
            return None, None
        return None, None

    def _manter_detalhe_das_ops(
        self, estado: EstadoIntegracao, orcamento: OrcamentoWbc, dados: dict[str, Any]
    ) -> dict[str, Any]:
        """An order that has, or may have, live OPs keeps its `U_INO_ORCAMENTO`.

        OP lines point at quote lines by position: `WOR1.U_INO_DESC` is the `LineId` in the detail
        the OPs were built from, and `VW_OS_INTEGRACAO` joins that position to the detail in
        `ORDR.U_INO_ORCAMENTO`. Moving the order to a newer detail makes every position name another
        part: on 03/11/2026 order 84550 went from 1094 to 61 matching lines out of 1094, and the
        tree of the OS screens came out wrong. A new detail only goes in with its own OPs, through
        Processar. Unknown count = may have OPs, same as the partner swap. The worst case of keeping
        it on an order with no OPs is a stale link, which the next Processar rewrites.
        """
        planejadas, em_producao = estado.ops_planejadas_pedido, estado.ops_em_producao_pedido
        if "U_INO_ORCAMENTO" not in dados or (planejadas == 0 and em_producao == 0):
            return dados
        ops = (
            "OPs desconhecidas"
            if planejadas is None or em_producao is None
            else f"{planejadas + em_producao} OP(s) viva(s)"
        )
        texto = (
            f"Pedido atualizado para a revisão {orcamento.revisao} do WBC com {ops}: Detalhe do "
            f"Orçamento mantido. As OPs continuam na árvore de antes; se a revisão mudou o projeto, "
            f"precisa de uma pessoa para refazê-las."
        )
        logger.warning("Orçamento %s: %s", orcamento.orcnum, texto)
        self._tracking.registrar_evento(
            orcamento.orcnum, regra="detalhe_mantido_pelas_ops", mensagem=texto
        )
        return {k: v for k, v in dados.items() if k != "U_INO_ORCAMENTO"}

    def _respeitar_congelamento(self, orcnum: str, dados: dict[str, Any]) -> dict[str, Any]:
        """`U_INO_Congelado = 'Y'` on the order: send the header, not the lines.

        Updating an order, here as in the legacy, **rebuilds the lines**: they are deleted and rebuilt
        from the WBC quote (`ServiceProcess.cs:600-672`). Any manual edit — swapped item, adjusted
        quantity, extra text — goes with them. The field exists to prevent exactly that.

        Protects **only the lines**, and that matters: in the legacy the header is assigned before the
        `if (Congelado != "Y")` and `Update()` comes after it, so a frozen order still receives the new
        `U_INO_VERSAOWBC`. That stamp is what stops the quote from being picked again every cycle:
        without it the WBC revision would stay forever newer than the order's and the cycle would try
        to update forever, burning the write cap.

        In production **all 2,581 current orders were `'Y'`** — 2,461 closed and 120 open — so in
        practice an order update never rebuilds lines. Without this guard the integration would destroy
        the manual edits on the 120 open ones in the first cycle that reached them.
        """
        if not self._documentos.esta_congelado(TipoDocumento.PEDIDO, orcnum):
            return dados
        preservado = {k: v for k, v in dados.items() if k != "DocumentLines"}
        logger.info(
            "Pedido do orçamento %s está congelado (U_INO_Congelado = 'Y'): "
            "cabeçalho atualizado, %d linha(s) preservadas como estão.",
            orcnum,
            len(dados.get("DocumentLines") or ()),
        )
        return preservado

    def _payload_pedido(
        self,
        orcamento: OrcamentoWbc,
        estado: EstadoIntegracao,
        oportunidade: dict[str, Any] | None = None,
        snapshot_id: int | None = None,
    ) -> dict[str, Any]:
        """Order body: `domain.pedido` header plus the lines."""
        payload = regras_pedido.montar_payload(
            orcamento,
            parceiro=estado.parceiro_do_pedido,
            filial=self._filial,
            snapshot_id=snapshot_id,
            oportunidade=oportunidade,
            # Vale também para o pedido que nasce no `PN_Correc`: o contato da
            # oportunidade pertence ao parceiro antigo, e o SAP recusa contato
            # de outro parceiro (`-5002 Invalid contact person code`).
            trocando_de_parceiro=estado.pedido_no_parceiro_corrigido,
        )
        # O peso vai só no pedido — ver `domain.linhas`. A leitura é uma
        # consulta extra ao WBC, feita apenas nas ações que montam um pedido:
        # não é a cada avaliação da janela, é a cada pedido de verdade.
        pesos = self._pesos_do_orcamento(orcamento)
        linhas = self._montar_linhas(
            orcamento,
            lambda orc, de_para: regras_pedido.linhas(orc, de_para, pesos=pesos),
        )
        if linhas:
            payload["DocumentLines"] = linhas
        return payload

    def _pesos_do_orcamento(self, orcamento: OrcamentoWbc) -> dict[int, Decimal]:
        """Weight per `ORCITM`, plus a warning for each item left without one.

        A missing weight does not block the order — the SAP keeps the item master's — but it must be
        visible. Among the quotes that become orders the tree covers 314 of 316 lines; when an item
        falls in the other two, better the log says so than the shipping dock finds out.
        """
        pesos = self._wbc.pesos_por_item(orcamento.orcnum)
        # The weight is the one field that went wrong without a trace (quote 00125817,
        # 29/09/2026): every line says what it will carry.
        for item in orcamento.itens:
            peso = peso_da_linha(pesos.get(item.orcitm))
            if peso is not None:
                logger.info(
                    "%s: peso do item %s: árvore do WBC %s kg + 10%% = %s kg no Weight1 da linha.",
                    orcamento.orcnum, item.orcitm, _kg(pesos[item.orcitm]), _kg(peso),
                )
        sem_peso = [item.orcitm for item in orcamento.itens if item.orcitm not in pesos]
        if sem_peso:
            logger.warning(
                "%s: sem peso na árvore para o(s) item(ns) %s — o SAP manterá o peso "
                "do cadastro nessas linhas.",
                orcamento.orcnum,
                ", ".join(str(i) for i in sem_peso),
            )
        return pesos

    def _montar_linhas(
        self,
        orcamento: OrcamentoWbc,
        resolver: Callable[[OrcamentoWbc, dict[str, Any]], ResultadoLinhas],
    ) -> list[dict[str, Any]]:
        """Resolve the document lines and record what had to be supplied.

        Warnings are **not** left only in the log: they go to the tracking store, because a group
        outside the mapping (de-para) means the document was created with an item nobody consciously
        chose, and whoever looks at the dashboard must see it without opening the server log. Returns
        `[]` (so no `DocumentLines`) when the group mapping is unavailable.
        """
        if self._grupo_produtos is None:
            logger.warning(
                "De-para de grupos indisponível: %s seguirá sem DocumentLines.",
                orcamento.orcnum,
            )
            return []

        resultado = resolver(orcamento, self._grupo_produtos.carregar())

        for aviso in resultado.avisos:
            logger.warning("%s: %s", orcamento.orcnum, aviso)
            self._tracking.registrar_evento(orcamento.orcnum, tipo=TipoEvento.ERRO, mensagem=aviso)

        # Notas ficam só no log, com o tema na frente do número do orçamento
        # ("[porta-paletes] 00125442: ..."): é o tema que o painel usa para
        # colorir a linha, e ele precisa abrir a mensagem para ser reconhecido.
        for nota in resultado.notas:
            logger.log(
                logging.WARNING if nota.atencao else logging.INFO,
                "%s",
                _tema_na_frente(orcamento.orcnum, nota.texto),
            )

        if resultado.vazio and orcamento.itens:
            # Itens existem no WBC mas nenhuma linha sobreviveu. Deixar seguir
            # produziria um documento com total zero — recusado pelo SAP com uma
            # mensagem que não diz nada sobre a causa real.
            logger.error(
                "%s: %d item(ns) no WBC e nenhuma linha montada para o SAP.",
                orcamento.orcnum,
                len(orcamento.itens),
            )

        return list(resultado.linhas)

    def _gravar_snapshot_orcdetalhe(self, orcamento: OrcamentoWbc) -> int | None:
        """Write the OrcDetalhe snapshot and return the `DocEntry` created.

        Called lazily through `_SnapshotSobDemanda`, once per quote, right before the first document
        write (not before every action). The order relative to the document matters: this snapshot's
        `DocEntry` goes into the `U_INO_ORCAMENTO` UDF of the quote/order, linking the document back to
        the picture of the quote that originated it.

        The legacy does the same, in the same order (`ServiceProcess.cs:~967`), but recovers the
        `DocEntry` with `SELECT max("DocEntry") FROM "@INO_ORCAM"`, which can return another concurrent
        run's record. Here the `DocEntry` comes from the POST response itself, the only value that
        certainly matches what was just created.

        Still **auxiliary**: on failure it returns `None` and the document is created anyway with
        `U_INO_ORCAMENTO` empty — exactly what the legacy does without the value
        (`DocEntryValdixonTable == "" ? "" : ...`). Losing the history is bad; losing the quote is worse.
        """
        try:
            criado = self._orcdetalhe.criar_snapshot(montar_payload_orcdetalhe(orcamento))
            return _inteiro((criado or {}).get("DocEntry"))
        except Exception as exc:  # noqa: BLE001 - o snapshot é auxiliar
            # O snapshot é registro histórico: falhar nele não pode desfazer os
            # documentos já criados no SAP, então vira aviso, não erro fatal.
            logger.warning(
                "Não foi possível gravar o snapshot do OrcDetalhe para %s: %s",
                orcamento.orcnum,
                exc,
            )
            self._tracking.registrar_evento(
                orcamento.orcnum,
                tipo=TipoEvento.ERRO,
                mensagem=f"Snapshot do OrcDetalhe não gravado: {exc}",
            )
            return None

    def _registrar_documento(
        self, orcnum: str, tipo: TipoDocumento, documento: dict[str, Any]
    ) -> None:
        self._tracking.registrar_documento(
            orcnum,
            tipo="cotacao" if tipo is TipoDocumento.COTACAO else "pedido",
            doc_entry=_inteiro(documento.get("DocEntry")),
            doc_num=_inteiro(documento.get("DocNum")),
            valor=_decimal(documento.get("DocTotal")),
        )

    @staticmethod
    def _status_final(executadas: list[Acao]) -> StatusIntegracao | None:
        """Status to show, given the most significant action executed.

        Table order matters: closing beats order, which beats quote — otherwise a closed quote would
        show on the dashboard as "quote updated", which happened last but is not what matters.
        """
        for acao in (
            Acao.MARCAR_OPORTUNIDADE_PERDIDA,
            Acao.CANCELAR_E_RECRIAR_PEDIDO,
            Acao.CRIAR_PEDIDO,
            Acao.ATUALIZAR_PEDIDO,
            Acao.CANCELAR_E_RECRIAR_COTACAO,
            Acao.CRIAR_COTACAO,
            Acao.ATUALIZAR_COTACAO,
        ):
            if acao in executadas:
                return STATUS_POR_ACAO[acao]
        return None


def _lista_de_ops(ops: Any) -> str:
    """`158845, 158846 … (+40)` — the DocNums people search for, capped for the log."""
    numeros = [str(op.doc_num) for op in ops]
    if len(numeros) <= 12:
        return ", ".join(numeros)
    return ", ".join(numeros[:12]) + f" … (+{len(numeros) - 12})"


def _numeros(doc_nums: list[int]) -> str:
    """Every DocNum, uncapped: these are the OPs already cancelled, the list the PCP needs."""
    return ", ".join(str(n) for n in doc_nums) or "nenhuma"


def _alertar_se_pede_gente(orcnum: str, decisao: Decisao) -> None:
    """ERROR in the log for a decision that only a person can unblock (see the set)."""
    if REGRAS_QUE_PEDEM_GENTE.intersection(decisao.regra.split("+")):
        logger.error("Orçamento %s: %s", orcnum, " ".join(decisao.motivos))


def _data(valor: Any) -> date | None:
    """`OOPR.OpenDate` comes from HANA as `datetime`; the tracking store keeps a date.

    It is the date that defines the worker's window — see `Acompanhamento.data_abertura`.
    """
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    return None


def _inteiro(valor: Any) -> int | None:
    try:
        return int(valor) if valor is not None else None
    except (TypeError, ValueError):
        return None


def _tema_na_frente(orcnum: str, texto: str) -> str:
    """`[theme] rest` + quote → `[theme] quote: rest`.

    The panel recognises the theme by the bracket that **opens** the message
    (`logs.LinhaDeLog.tema`); the quote number follows right after so the line stays readable per
    quote. Text without a theme becomes `quote: text`.
    """
    if texto.startswith("[") and "] " in texto:
        tema, resto = texto.split("] ", 1)
        return f"{tema}] {orcnum}: {resto}"
    return f"{orcnum}: {texto}"


def _decimal(valor: Any) -> Decimal | None:
    try:
        return Decimal(str(valor)) if valor is not None else None
    except Exception:  # noqa: BLE001
        return None


@dataclass(frozen=True, slots=True)
class _OrcamentoResumido:
    """The minimum `montar_estado` needs from the WBC: number, situation and revision.

    Exists so the decision can be taken without loading the quote lines. Not an incomplete
    `OrcamentoWbc` in disguise: whoever needs the lines loads the real object, and the distinct
    type keeps this one from being passed along by mistake.
    """

    orcnum: str
    sitcode: int
    revisao: str


def fonte_de_documentos(oportunidade: dict[str, Any], repositorio: Any) -> Any:
    """Where quote and order come from: the opportunity's own read, or the Service Layer.

    When the opportunity arrives from HANA it already carries the resolved documents, and asking
    the SAP again would be four requests per quote for the same answer. When it comes from the
    Service Layer (or a test) the block is absent and the usual repository applies.

    The choice is by **presence of data**, not configuration: no switch can sit in the wrong state.
    """
    if oportunidade.get("documentos") is not None:
        return DocumentosJaLidos.da_oportunidade(oportunidade)
    return repositorio


def _ops_do_pedido(documentos: Any, orcnum: str) -> tuple[int | None, int | None]:
    """OP counts when the source knows them; ``(None, None)`` otherwise.

    Only the HANA read carries them (`DocumentosJaLidos.ops_do_pedido`). The Service
    Layer repository and the test doubles do not, and "unknown" must reach the state
    as ``None`` rather than as zero.
    """
    ler = getattr(documentos, "ops_do_pedido", None)
    contagem = ler(orcnum) if callable(ler) else None
    return contagem if contagem is not None else (None, None)


def montar_estado(
    orcamento: OrcamentoWbc,
    oportunidade: dict[str, Any],
    documentos: RepositorioDocumentosVenda,
    *,
    corte_de_pedido: date | None = None,
) -> EstadoIntegracao:
    """Gather, in one snapshot, what the WBC says and what the SAP already has.

    A module function, not a method, on purpose: building this snapshot is **read-only**, and the
    preview (`wbcpython pendentes`) needs it without instantiating the processor, which requires
    tracking and can write. That way the preview and the real cycle see exactly the same state and
    cannot drift apart.
    """
    orcnum = orcamento.orcnum
    ops_planejadas, ops_em_producao = _ops_do_pedido(documentos, orcnum)
    return EstadoIntegracao(
        orcamento=orcnum,
        sitcode_wbc=orcamento.sitcode,
        revisao_wbc=orcamento.revisao,
        sitcode_sap=str(oportunidade.get("U_INO_StatusWBC") or ""),
        status_oportunidade=str(oportunidade.get("Status") or ""),
        tem_cotacao=documentos.existe(TipoDocumento.COTACAO, orcnum),
        tem_pedido=documentos.existe(TipoDocumento.PEDIDO, orcnum),
        pedido_fechado=documentos.esta_fechado(TipoDocumento.PEDIDO, orcnum),
        revisao_cotacao_sap=documentos.revisao_aplicada(TipoDocumento.COTACAO, orcnum),
        revisao_pedido_sap=documentos.revisao_aplicada(TipoDocumento.PEDIDO, orcnum),
        parceiro_pedido_sap=documentos.parceiro_aplicado(TipoDocumento.PEDIDO, orcnum),
        ops_planejadas_pedido=ops_planejadas,
        ops_em_producao_pedido=ops_em_producao,
        alterado=str(oportunidade.get("U_INO_Update") or "N").upper() == "Y",
        parceiro_atual=str(oportunidade.get("CardCode") or ""),
        parceiro_novo=str(oportunidade.get("U_INO_PN_Correc") or ""),
        # `corte_de_pedido` é o primeiro dia da janela PADRÃO. Num ciclo normal
        # nenhuma oportunidade lida é anterior a ele — o corte só morde quando a
        # janela foi estendida. Ver `EstadoIntegracao.fora_da_janela_padrao`.
        fora_da_janela_padrao=_fora_da_janela(oportunidade, corte_de_pedido),
        # Zero item no WBC = documento sem linha, que o SAP recusa. A decisão
        # sai sem as ações de documento; ver `EstadoIntegracao.orcamento_sem_itens`.
        #
        # ⚠️ `getattr` porque o PRÉ-FILTRO decide com `_OrcamentoResumido`, que
        # não carregou as linhas: ali "não sei" não pode virar "não tem", senão
        # o ciclo deixaria de carregar o orçamento — e nunca descobriria que ele
        # tem itens. Quem responde é a passada com o `OrcamentoWbc` inteiro.
        orcamento_sem_itens=(itens := getattr(orcamento, "itens", None)) is not None
        and not itens,
    )


def decidir_pela_situacao(
    orcnum: str,
    situacao: tuple[int, str],
    oportunidade: dict[str, Any],
    documentos: Any,
    *,
    corte_de_pedido: date | None = None,
) -> tuple[EstadoIntegracao, Decisao]:
    """Step 1 of the decision: only from `(sitcode, revisao)` read in batch, without the lines.

    The worker (`ProcessadorDeOrcamento`) and the preview (`wbcpython pendentes`) both call THIS
    function — each used to build its own `_OrcamentoResumido`, and the preview imported the private
    one from here. A diverging copy would make the preview lie about what the cycle writes, with no
    error.
    """
    sitcode, revisao = situacao
    estado = montar_estado(
        _OrcamentoResumido(orcnum=orcnum, sitcode=sitcode, revisao=revisao),
        oportunidade,
        fonte_de_documentos(oportunidade, documentos),
        corte_de_pedido=corte_de_pedido,
    )
    return estado, decidir(estado)


def precisa_do_orcamento(decisao: Decisao) -> bool:
    """The gate between the two steps: with an action, load the whole quote and decide again.

    One place for the rule. The preview used `ACOES_QUE_ESCREVEM`, the worker `tem_acao`: today they
    agree, but a new action outside that set would split them silently.
    """
    return decisao.tem_acao


def decidir_pelo_orcamento(
    orcamento: OrcamentoWbc,
    oportunidade: dict[str, Any],
    documentos: Any,
    *,
    corte_de_pedido: date | None = None,
) -> tuple[EstadoIntegracao, Decisao]:
    """2nd step: the decision on the whole quote (the one that authorises writing)."""
    estado = montar_estado(
        orcamento,
        oportunidade,
        fonte_de_documentos(oportunidade, documentos),
        corte_de_pedido=corte_de_pedido,
    )
    return estado, decidir(estado)


def _fora_da_janela(oportunidade: dict[str, Any], corte: date | None) -> bool:
    """The opportunity opened before the cutoff of the standard window.

    With no `OpenDate` the answer is **no**: when in doubt, behave as usual. A missing date must not
    turn into a silent order block in a normal cycle.
    """
    if corte is None:
        return False
    abertura = _data(oportunidade.get("OpenDate"))
    return abertura is not None and abertura < corte
