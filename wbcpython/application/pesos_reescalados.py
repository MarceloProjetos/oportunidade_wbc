"""Puts back the weight the SAP rescaled — the worker step; `wbcpython pesos-reescalados` only reads.

The rule is `domain.peso_reescalado.decidir`; this module only applies it: one PATCH per order
(only `Weight1` of the lines being fixed, sent only if the Service Layer still shows the lines
the decision saw, with `If-Match` when it sends an ETag), a log line per decision and an event
in the tracking. Without ``gravar`` it is the simulation.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime

from wbcpython.domain.peso_reescalado import (
    TOLERANCIA_EM_KG,
    Acao,
    Decisao,
    LinhaAtual,
    decidir,
    descrever,
    kg,
)
from wbcpython.infrastructure.hana.pesos_reescalados import LinhaDoPedido
from wbcpython.infrastructure.service_layer.documentos import (
    RepositorioDocumentosVenda,
    TipoDocumento,
)
from wbcpython.safety import SafetyViolation
from wbcpython.tracking import RepositorioTracking, TipoEvento

logger = logging.getLogger(__name__)

REGRA = "peso_reescalado"

#: When writing starts (F4 of PLANO_PESO_REESCALADO.md (removed 2026-10-06), Marcelo's yes on 05/10/2026): only
#: quantity changes saved from then on are undone — the past is never altered. ``None`` = F3,
#: the worker only logs the weights it would put back (the rollback). A constant on purpose,
#: never a `.env` key; the Processar log reads it too.
GRAVA_A_PARTIR_DE: datetime | None = datetime(2026, 10, 6)

#: The worker only looks at orders a person saved in the last days; older ones had eyes on them.
DIAS_OLHADOS = 3


@dataclass(slots=True)
class Memoria:
    """What the worker already said and wrote, kept across its 3-minute cycles.

    ``relatadas``: log lines already given (line version, or order + failure text), so a cycle
    does not repeat them. ``gravadas``: rescales already undone (order, line, first rescale's
    version) — never written twice: if the SAP did not keep the weight, a second PATCH would
    loop every cycle.
    """

    relatadas: set[tuple] = field(default_factory=set)
    gravadas: set[tuple[int, int, int]] = field(default_factory=set)

    def primeira_vez(self, chave: tuple) -> bool:
        novo = chave not in self.relatadas
        self.relatadas.add(chave)
        return novo


@dataclass(slots=True)
class Resumo:
    corrigidas: int = 0
    simuladas: int = 0
    avisos: int = 0
    falhas: int = 0

    @property
    def texto(self) -> str:
        return (
            f"Pesos reescalados: {self.corrigidas} restaurada(s); {self.simuladas} restauraria "
            f"(simulação); {self.avisos} só com aviso; {self.falhas} falha(s)."
        )


def mesma_linha(no_sap: tuple[float, float] | None, atual: LinhaAtual) -> bool:
    """The Service Layer's ``(Quantity, Weight1)`` is still the line the decision read in HANA."""
    return (
        no_sap is not None
        and abs(no_sap[0] - atual.quantidade) <= 1e-9
        and abs(no_sap[1] - atual.peso) <= TOLERANCIA_EM_KG
    )


def conferir(
    linhas: Iterable[LinhaDoPedido],
    *,
    agora: datetime,
    gravar: Callable[[], AbstractContextManager[RepositorioDocumentosVenda]] | None = None,
    tracking: RepositorioTracking | None = None,
    memoria: Memoria | None = None,
    detalhar: bool = False,
    a_partir_de: datetime | None = None,
    parar: Callable[[], bool] | None = None,
) -> Resumo:
    """Decides every line and acts on it.

    ``gravar`` opens what writing needs (the worker: execution lock + Service Layer session) and
    is called only when some line is to be fixed. Without ``memoria`` every line is logged (a
    one-off command). ``detalhar`` also logs why a line is left alone. ``a_partir_de``: see
    `decidir`. ``parar`` is checked before each order's write. A `SafetyViolation` is never
    swallowed.
    """
    memoria = memoria or Memoria()
    resumo = Resumo()
    por_pedido: dict[int, list[tuple[LinhaDoPedido, Decisao]]] = defaultdict(list)
    for linha in linhas:
        decisao = decidir(
            linha.versoes, linha.atual, agora,
            a_partir_de=a_partir_de, historico_cortado=linha.historico_cortado,
        )
        if decisao.acao is Acao.NADA:
            if detalhar:
                logger.info("Pedido %s linha %d: nada a fazer — %s.", linha.doc_num, linha.line_num, decisao.motivo)
            continue
        versao = (linha.doc_entry, linha.line_num, linha.versoes[-1].instancia)
        ja_gravada = (linha.doc_entry, linha.line_num, decisao.reescala.depois.instancia) in memoria.gravadas
        if decisao.acao is Acao.CORRIGIR and gravar is not None and not ja_gravada:
            por_pedido[linha.doc_entry].append((linha, decisao))
            continue
        if decisao.acao is Acao.CORRIGIR and gravar is None:
            resumo.simuladas += 1
        else:
            resumo.avisos += 1
        if memoria.primeira_vez(("não guardou", *versao) if ja_gravada else versao):
            _relatar(tracking, linha, *_aviso(decisao, ja_gravada=ja_gravada))

    if not por_pedido:
        return resumo
    with gravar() as documentos:
        for doc_entry, itens in por_pedido.items():
            if parar is not None and parar():
                logger.info("Parada solicitada — pesos reescalados interrompidos.")
                break
            _gravar_pedido(documentos, doc_entry, itens, resumo, memoria, tracking)
    return resumo


def _aviso(decisao: Decisao, *, ja_gravada: bool) -> tuple[int, str]:
    """Log level and sentence for a line that is not written now."""
    if ja_gravada:
        return logging.WARNING, "peso restaurado antes e o SAP não guardou; não grava de novo."
    if decisao.acao is Acao.AVISAR:
        return logging.WARNING, f"peso NÃO restaurado: {decisao.motivo}. {descrever(decisao)}."
    return logging.INFO, f"SIMULAÇÃO: voltaria a {kg(decisao.peso)} kg. {descrever(decisao)}."


def _gravar_pedido(
    documentos: RepositorioDocumentosVenda,
    doc_entry: int,
    itens: list[tuple[LinhaDoPedido, Decisao]],
    resumo: Resumo,
    memoria: Memoria,
    tracking: RepositorioTracking | None,
) -> None:
    """One order: read the lines and ETag, write only the lines still as decided, record."""
    doc_num = itens[0][0].doc_num
    try:
        etag, no_sap = documentos.estado_das_linhas(TipoDocumento.PEDIDO, doc_entry)
        prontas = [(l, d) for l, d in itens if mesma_linha(no_sap.get(l.line_num), l.atual)]
        if len(prontas) < len(itens) and memoria.primeira_vez(("mudou", doc_entry, len(prontas))):
            logger.info("Pedido %s: linha mudou desde a leitura; fica para o próximo ciclo.", doc_num)
        if not prontas:
            return
        documentos.atualizar_pesos(
            TipoDocumento.PEDIDO, doc_entry, {l.line_num: d.peso for l, d in prontas}, etag=etag
        )
    except SafetyViolation:
        raise
    except Exception as exc:  # noqa: BLE001 - one order must not stop the others
        resumo.falhas += 1
        # Retried every cycle (412 = someone saved meanwhile), told once per failure text.
        if memoria.primeira_vez((doc_entry, str(exc))):
            logger.warning("Pedido %s: não consegui restaurar o peso (%s); fica para o próximo ciclo.", doc_num, exc)
        return
    resumo.corrigidas += len(prontas)
    for linha, decisao in prontas:
        memoria.gravadas.add((linha.doc_entry, linha.line_num, decisao.reescala.depois.instancia))
        _relatar(
            tracking, linha, logging.INFO,
            f"peso restaurado para {kg(decisao.peso)} kg. {descrever(decisao)}.", tipo=TipoEvento.ACAO,
        )


def _relatar(
    tracking: RepositorioTracking | None,
    linha: LinhaDoPedido,
    nivel: int,
    texto: str,
    *,
    tipo: TipoEvento = TipoEvento.DECISAO,
) -> None:
    """The log line, and the same sentence as an event in the tracking (painel)."""
    mensagem = f"Pedido {linha.doc_num} linha {linha.line_num}: {texto}"
    logger.log(nivel, "%s", mensagem)
    if tracking is None or not linha.orcamento:
        return
    try:
        tracking.registrar_evento(linha.orcamento, tipo=tipo, regra=REGRA, mensagem=mensagem)
    except Exception as exc:  # noqa: BLE001 - the tracking is a record, never a reason to stop
        logger.warning("Acompanhamento não gravou o evento do pedido %s: %s", linha.doc_num, exc)
