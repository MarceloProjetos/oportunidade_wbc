"""Flows of the Manutenção de OP shared by the screen and the JSON API (29/09/2026).

Until F1 of docs/PLANO_API_MANUTENCAO_OP.md these lived inside the screen's routes, mixed
with the HTML: validating the selection, refusing a terminal OP, building the closing plan,
starting the background execution. The JSON API needs the same decisions with the same
words, so they moved here and both routers became thin adapters. A refusal is raised as
`Recusa`; the screen renders it with `erro.html` (400, as before) and the API as JSON with
the HTTP status of its `tipo`. One text, written once — the messages cannot drift apart.

What stays in the adapters: reading the request, creating the HANA reader (injected here,
so the tests keep swapping it at the router) and the write gate `core.web.avisa_escrita`,
called by every write endpoint where the coverage test in `test_web_modulos.py` sees it.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from controleproducao.config import get_settings
from controleproducao.core.confirmacao import PLANOS, ConfirmacaoInvalida, Plano
from controleproducao.core.hana_reader import HanaDirectReader
from controleproducao.core.service_layer_client import ServiceLayerClient
from controleproducao.core.tarefas import ORIGEM_TELA, TAREFAS, Tarefa, acompanha_log
from controleproducao.modules.manutencao_op import service

logger = logging.getLogger(__name__)

MODULO = "manutencao_op"

NOME_LIBERAR = "Liberar OPs"
NOME_ENCERRAR = "Encerrar OPs"

# HTTP status of each refusal in the JSON API. The screen answers 400 for all of them (as it
# always did); the API tells "fix the request" (400) from "no such thing" (404) from "the
# state of the SAP or of the module says no" (409).
HTTP_DA_RECUSA = {
    "invalido": 400,
    "nao_encontrada": 404,
    "status_terminal": 409,
    "ciclo": 409,
    "nada_a_encerrar": 409,
    "confirmacao_invalida": 409,
    "ocupado": 409,
}


class Recusa(Exception):
    """An action refused before anything was written, with the screen's own message.

    ``detalhes``/``colunas`` are the table the screen shows under the message (the OPs that
    caused it); ``dados`` is extra JSON only the API returns; ``execucao`` is the task that
    keeps the module busy (the screen links to it, the API returns its id).
    """

    def __init__(
        self,
        tipo: str,
        mensagem: str,
        *,
        titulo: str | None = None,
        detalhes: list[dict] | None = None,
        colunas: list[str] | None = None,
        dados: dict | None = None,
        execucao: Tarefa | None = None,
    ) -> None:
        if tipo not in HTTP_DA_RECUSA:
            raise ValueError(f"tipo de recusa desconhecido: {tipo!r}")
        super().__init__(mensagem)
        self.tipo = tipo
        self.mensagem = mensagem
        self.titulo = titulo
        self.detalhes = detalhes or []
        self.colunas = colunas or []
        self.dados = dados or {}
        self.execucao = execucao

    @property
    def http(self) -> int:
        return HTTP_DA_RECUSA[self.tipo]


def _ocupado(exc: RuntimeError) -> Recusa:
    return Recusa(
        "ocupado", str(exc), titulo="Já existe execução em andamento",
        execucao=TAREFAS.em_execucao(MODULO),
    )


# ---------------------------------------------------------------------------
# Liberar (and, in F6, Replanejar) — status only, written on the first request
# ---------------------------------------------------------------------------
def prepara_mudanca_status(leitor: HanaDirectReader, numeros: list[str]) -> list[dict]:
    """The OPs a status change will touch, read again from the SAP.

    Blocking (HANA): async callers run it in a thread. Refuses the WHOLE batch when an OP is
    terminal instead of changing the others in silence: the screen disables those boxes, so
    receiving one means the list changed since the page loaded (someone closed the OP in the
    meantime) or the request did not come from the screen. Either way the caller must look
    again — this is not the moment to guess which rows are still wanted.
    """
    if not numeros:
        raise Recusa("invalido", "Nenhuma OP selecionada.")
    try:
        ops = service.levanta_ops(leitor, op_docnums=list(numeros))
    except ValueError as exc:
        # "OP 12a": the message is already written for the user (it was a 500 on the screen).
        raise Recusa("invalido", str(exc)) from exc
    if not ops:
        raise Recusa("nao_encontrada", "Nenhuma das OPs informadas foi encontrada.")

    terminais = [op for op in ops if op["status"] in service.STATUS_TERMINAIS]
    if terminais:
        raise Recusa(
            "status_terminal",
            f"{len(terminais)} OP(s) selecionada(s) estão em status terminal (Encerrada ou "
            "Cancelada) e não admitem mudança. Nenhuma OP foi alterada — refaça a busca, "
            "porque a lista mudou desde que a tela foi carregada.",
            titulo="OP em status terminal",
            detalhes=[{"OP": o["doc_num"], "Item": o["item_code"],
                       "Status": service.STATUS_OP.get(o["status"], o["status"])}
                      for o in terminais],
            colunas=["OP", "Item", "Status"],
        )
    return ops


def corrotina_mudanca_status(
    nome: str, ops: list[dict], acao: str
) -> Callable[[Tarefa], Awaitable[dict]]:
    """The background body of a status change (legacy code in ``acao``: "l", "p")."""

    async def executa(tarefa: Tarefa) -> dict:
        tarefa.avanca(f"{nome}: {len(ops)} OP(s)…", 0, len(ops))
        with acompanha_log(tarefa, service.__name__):
            async with ServiceLayerClient(get_settings()) as sl:
                resultado = await service.muda_status(sl, ops, acao)
        tarefa.avanca(
            f"{len(resultado.get('alteradas', []))} alterada(s), "
            f"{len(resultado.get('com_erro', []))} com erro"
            + (f", {len(resultado['ignoradas'])} ignorada(s)" if resultado.get("ignoradas") else "")
            + ".",
            len(ops),
        )
        return resultado

    return executa


# ---------------------------------------------------------------------------
# Encerrar — irreversible: checked plan + single-use token, then the background run
# ---------------------------------------------------------------------------
def monta_plano_encerramento(
    leitor: HanaDirectReader, op_docnums: list[str] | None, pedido: str | None
) -> Plano:
    """The checked plan of a closing: every OP asked, in execution order, with its action.

    Blocking (HANA). The order is computed, not chosen — child before parent, because the
    material issue of a parent OP consumes what the child produces; a cycle refuses the
    whole operation. Returns the stored `Plano` (its token is what the execution accepts).
    """
    op_docnums = list(op_docnums or [])
    pedido = (pedido or "").strip()
    if bool(op_docnums) == bool(pedido):
        raise Recusa("invalido", "Selecione OPs na lista OU informe um pedido — não os dois.")

    try:
        ops = service.levanta_ops(leitor, op_docnums=op_docnums or None, doc_num_pedido=pedido or None)
        if not ops:
            raise Recusa("nao_encontrada", "Nenhuma OP encontrada para o que foi informado.")
        componentes = service._componentes_por_op(leitor, [int(o["doc_entry"]) for o in ops])
    except ValueError as exc:
        # A non-numeric order number ("84a") used to escape as a 500; the message is
        # already written for the user ("Número do pedido: esperado um número…").
        raise Recusa("invalido", str(exc)) from exc

    ops, em_ciclo = service.ordena_por_dependencia(ops, componentes)
    if em_ciclo:
        raise Recusa(
            "ciclo",
            f"{len(em_ciclo)} OP(s) formam um ciclo de dependência entre si; nenhuma foi "
            "alterada. Numa operação irreversível de estoque, encerrar em ordem arbitrária "
            "é pior que recusar — uma delas consumiria o produto da outra antes de ele "
            "existir. Resolva a estrutura ou encerre-as uma a uma, por número.",
            titulo="Ciclo de dependência",
            detalhes=[{"OP": o["doc_num"], "Item": o["item_code"]} for o in em_ciclo],
            colunas=["OP", "Item"],
        )

    itens, a_processar = [], []
    for indice, op in enumerate(ops, start=1):
        acao, processar = service.classifica_encerramento(op["status"], op["planejada"], op["apontada"])
        if processar:
            a_processar.append(op)
        itens.append({
            "#": indice, "OP": op["doc_num"], "Item": op["item_code"],
            "Planejada": f"{op['planejada']:g}", "Apontada": f"{op['apontada']:g}",
            "Status atual": service.STATUS_OP.get(op["status"], op["status"]),
            "Ação": acao,
            # Underscored keys are not shown by the screen: the execution reads them (the
            # decision to process is taken here, once — never re-derived from a sentence
            # written for people) and the API returns the raw numbers.
            "_doc_entry": op["doc_entry"], "_status": op["status"],
            "_planejada": op["planejada"], "_apontada": op["apontada"],
            "_processar": processar,
        })

    if not a_processar:
        raise Recusa(
            "nada_a_encerrar",
            "Nada a encerrar: nenhuma das OPs está em condição de ser encerrada.",
            dados={"itens": itens},
        )

    a_liberar = [o for o in a_processar if o["status"] != "R"]
    return PLANOS.criar(
        operacao=(f"{NOME_ENCERRAR} do pedido {pedido}") if pedido else NOME_ENCERRAR,
        resumo={
            "OP(s) a encerrar com lançamento de estoque": len(a_processar),
            "OP(s) Planejadas que serão LIBERADAS antes": len(a_liberar),
            "OP(s) listadas (inclui ignoradas)": len(ops),
        },
        itens=itens,
    )


def operacao_do_plano(token: str) -> str:
    """The operation a token stands for, without spending it — the label of the write gate."""
    plano = PLANOS.obter(token)
    return plano.operacao if plano else NOME_ENCERRAR


def consome_plano(token: str) -> Plano:
    """Spend the token — only after checking the module is free (29/09/2026).

    Before, a busy module was found only when the task was created, after the token had been
    spent: the operator had to check the plan again. The check and the spend run with no
    `await` in between, so nothing can start in the module between them.
    """
    try:
        TAREFAS.confere_livre(MODULO)
    except RuntimeError as exc:
        raise _ocupado(exc) from exc
    try:
        return PLANOS.consumir(token)
    except ConfirmacaoInvalida as exc:
        raise Recusa("confirmacao_invalida", str(exc), titulo="Confirmação não aceita") from exc


def corrotina_encerramento(plano: Plano) -> Callable[[Tarefa], Awaitable[dict]]:
    """The background body of a checked closing plan."""
    # Only the OPs the plan marked to process, in the order the person saw. Reading the
    # database again here would compute an order nobody looked at.
    doc_entries = [int(i["_doc_entry"]) for i in plano.itens if i.get("_processar")]

    async def executa(tarefa: Tarefa) -> dict:
        settings = get_settings()
        leitor = HanaDirectReader(settings)
        try:
            ops = service.levanta_ops(
                leitor, op_docnums=[str(i["OP"]) for i in plano.itens
                                    if int(i["_doc_entry"]) in set(doc_entries)]
            )
            ordem = {de: pos for pos, de in enumerate(doc_entries)}
            ops = sorted(
                [o for o in ops if int(o["doc_entry"]) in ordem],
                key=lambda o: ordem[int(o["doc_entry"])],
            )
            componentes = service._componentes_por_op(leitor, [int(o["doc_entry"]) for o in ops])
            dependentes = service.dependentes_transitivos(ops, componentes)

            tarefa.avanca(f"Encerrando {len(ops)} OP(s), filha antes da mãe…", 0, len(ops))
            with acompanha_log(tarefa, service.__name__):
                async with ServiceLayerClient(settings) as sl:
                    resultado = await service.finalizar_ops(
                        sl, leitor, ops, settings.sl_business_place_id, dependentes=dependentes
                    )
        finally:
            leitor.close()

        for op in resultado["finalizadas"]:
            tarefa.anota(
                f"OP {op['doc_num']}: saída={op.get('saida_docentry') or '—'}, "
                f"entrada={op.get('entrada_docentry') or '—'}"
                + (" (liberada antes)" if op.get("foi_liberada") else "")
            )
        for op in resultado.get("puladas", []):
            tarefa.anota(f"OP {op['doc_num']}: PULADA — dependia de uma OP que falhou.")
        for erro in resultado["com_erro"]:
            tarefa.anota(f"OP {erro['doc_num']}: ERRO em '{erro['etapa']}' — {erro['motivo']}")
            if erro.get("liberacao") == "mantida (saída já lançada)":
                tarefa.anota(
                    f"  ATENÇÃO na OP {erro['doc_num']}: a saída de insumo JÁ foi lançada e "
                    "a OP continua Liberada. Não use replanejar — a saída precisa ser "
                    "cancelada no SAP primeiro."
                )
        tarefa.avanca(
            f"{len(resultado['finalizadas'])} encerrada(s), "
            f"{len(resultado['com_erro'])} com erro, "
            f"{len(resultado.get('puladas', []))} pulada(s).",
            len(ops),
        )
        return resultado

    return executa


def descricao_do_plano(plano: Plano) -> str:
    return f"{sum(1 for i in plano.itens if i.get('_processar'))} OP(s)"


# ---------------------------------------------------------------------------
# Starting the execution
# ---------------------------------------------------------------------------
def dispara(
    nome: str,
    descricao: str,
    corrotina: Callable[[Tarefa], Awaitable[Any]],
    *,
    solicitante: str | None = None,
    origem: str = ORIGEM_TELA,
    ip: str | None = None,
) -> Tarefa:
    """Create the background execution of this module, or refuse because it is busy.

    One WARNING line per execution started, with who asked, from where and through what
    (screen or API) — the audit trail next to the write notice of ``avisa_escrita``.
    """
    try:
        tarefa = TAREFAS.criar(MODULO, nome, descricao, corrotina, solicitante=solicitante, origem=origem)
    except RuntimeError as exc:
        raise _ocupado(exc) from exc
    logger.warning(
        "Execução %s iniciada: %s (%s) · origem %s · solicitante %s · ip %s",
        tarefa.id, nome, descricao, origem, solicitante or "—", ip or "—",
    )
    return tarefa
