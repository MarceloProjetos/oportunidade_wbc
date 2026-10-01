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
NOME_REPLANEJAR = "Replanejar OPs"
NOME_ENCERRAR = "Encerrar OPs"

# HTTP status of each refusal in the JSON API. The screen answers 400 for all of them (as it
# always did); the API tells "fix the request" (400) from "no such thing" (404) from "the
# state of the SAP or of the module says no" (409).
HTTP_DA_RECUSA = {
    "invalido": 400,
    "nao_encontrada": 404,
    "status_terminal": 409,
    "saida_lancada": 409,
    "entrada_lancada": 409,
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


def _exige_todas(numeros: list[str], ops: list[dict]) -> None:
    """Every OP number asked must exist, or nothing is done (29/09/2026).

    `levanta_ops` returns what it finds and drops the rest in silence. The screen never
    noticed — its numbers come from its own search — but the API takes any number, and a
    typo in a batch would have released the others and reported success. Same rule as a
    terminal OP: refuse the whole batch and say which numbers are missing. Call it after
    `levanta_ops`, which already refused anything that is not digits.
    """
    pedidos = list(dict.fromkeys(int(str(n).strip()) for n in numeros))
    achados = {int(op["doc_num"]) for op in ops}
    faltando = [n for n in pedidos if n not in achados]
    if faltando:
        raise Recusa(
            "nao_encontrada",
            f"{len(faltando)} OP(s) informada(s) não existem no SAP: "
            f"{', '.join(str(n) for n in faltando)}. Nada foi feito — confira os números (DocNum).",
            titulo="OP não encontrada",
            detalhes=[{"OP": n} for n in faltando],
            colunas=["OP"],
        )


# ---------------------------------------------------------------------------
# Liberar and Replanejar — status only, written on the first request
# ---------------------------------------------------------------------------
def prepara_mudanca_status(leitor: HanaDirectReader, numeros: list[str], acao: str = "l") -> list[dict]:
    """The OPs a status change will touch, read again from the SAP.

    ``acao`` is the legacy code: "l" Liberar, "p" Replanejar. Replanejar also refuses the
    whole batch when a Liberada OP already has material issued (F6, 29/09/2026) or product
    received (D6, same day): the movement must be cancelled in the SAP first, or it would sit
    on a planned OP. The issue is checked first, so an OP with both is reported for it.

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
    _exige_todas(numeros, ops)

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

    if acao == "p":
        impedimentos = {
            op["doc_num"]: service.impedimento_replanejar(op) for op in ops if op["status"] == "R"
        }
        com_saida = [op for op in ops if impedimentos.get(op["doc_num"]) == "saida_lancada"]
        if com_saida:
            raise Recusa(
                "saida_lancada",
                f"{len(com_saida)} OP(s) já têm saída de insumo lançada e não podem voltar para "
                "Planejada — a saída precisa ser cancelada no SAP antes. Nenhuma OP foi alterada.",
                titulo="OP com saída de insumo lançada",
                detalhes=[{"OP": o["doc_num"], "Item": o["item_code"],
                           "Baixado": o["baixada"] if o.get("baixada") is not None else "desconhecido"}
                          for o in com_saida],
                colunas=["OP", "Item", "Baixado"],
            )
        com_entrada = [op for op in ops if impedimentos.get(op["doc_num"]) == "entrada_lancada"]
        if com_entrada:
            raise Recusa(
                "entrada_lancada",
                f"{len(com_entrada)} OP(s) já têm produto apontado (entrada lançada) e não podem "
                "voltar para Planejada — a entrada precisa ser cancelada no SAP antes. Nenhuma OP "
                "foi alterada.",
                titulo="OP com produto apontado",
                detalhes=[{"OP": o["doc_num"], "Item": o["item_code"],
                           "Apontado": o["apontada"] if o.get("apontada") is not None else "desconhecido"}
                          for o in com_entrada],
                colunas=["OP", "Item", "Apontado"],
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
        if op_docnums:
            _exige_todas(op_docnums, ops)
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
        tipo=TIPO_ENCERRAR,
    )


#: The confirmation tokens of this module are only spent by the closing (see `Plano.tipo`).
TIPO_ENCERRAR = "encerrar"


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
        return PLANOS.consumir(token, TIPO_ENCERRAR)
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
                        sl, leitor, ops, settings.sl_business_place_id, dependentes=dependentes,
                        # D5: "Interromper" is checked before each OP, never inside one.
                        deve_parar=lambda: tarefa.parada_pedida,
                    )
        finally:
            leitor.close()

        interrompidas = resultado.get("interrompidas", [])
        if tarefa.parada_pedida and not interrompidas:
            # Asked while the last OP was already running: nothing was left out, so this
            # is a completed run, not an interrupted one.
            tarefa.parada_pedida = False
            tarefa.anota("Interrupção pedida com a última OP já em andamento — nenhuma ficou de fora.")

        for op in resultado["finalizadas"]:
            tarefa.anota(
                f"OP {op['doc_num']}: saída={op.get('saida_docentry') or '—'}, "
                f"entrada={op.get('entrada_docentry') or '—'}"
                + (" (liberada antes)" if op.get("foi_liberada") else "")
            )
        for op in resultado.get("puladas", []):
            tarefa.anota(f"OP {op['doc_num']}: PULADA — dependia de uma OP que falhou.", problema=True)
        for op in interrompidas:
            tarefa.anota(f"OP {op['doc_num']}: NÃO INICIADA — a execução foi interrompida antes dela.")
        for erro in resultado["com_erro"]:
            tarefa.anota(f"OP {erro['doc_num']}: ERRO em '{erro['etapa']}' — {erro['motivo']}", problema=True)
            if erro.get("liberacao") == "mantida (saída já lançada)":
                tarefa.anota(
                    f"  ATENÇÃO na OP {erro['doc_num']}: a saída de insumo JÁ foi lançada e "
                    "a OP continua Liberada. Não use replanejar — a saída precisa ser "
                    "cancelada no SAP primeiro.",
                    problema=True,
                )
        tarefa.avanca(
            f"{len(resultado['finalizadas'])} encerrada(s), "
            f"{len(resultado['com_erro'])} com erro, "
            f"{len(resultado.get('puladas', []))} pulada(s)"
            + (f", {len(interrompidas)} não iniciada(s) (interrompida)" if interrompidas else "")
            + ".",
            len(ops) - len(interrompidas),
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
    parada_combinada: bool = False,
) -> Tarefa:
    """Create the background execution of this module, or refuse because it is busy.

    One WARNING line per execution started, with who asked, from where and through what
    (screen or API) — the audit trail next to the write notice of ``avisa_escrita``.
    """
    try:
        tarefa = TAREFAS.criar(
            MODULO, nome, descricao, corrotina,
            solicitante=solicitante, origem=origem, parada_combinada=parada_combinada,
        )
    except RuntimeError as exc:
        raise _ocupado(exc) from exc
    logger.warning(
        "Execução %s iniciada: %s (%s) · origem %s · solicitante %s · ip %s",
        tarefa.id, nome, descricao, origem, solicitante or "—", ip or "—",
    )
    return tarefa
