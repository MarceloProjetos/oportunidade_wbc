"""Rotas web do módulo Manutenção de OP — equivalente à tela `ManutencaoOp.b1f.cs`.

A tela original tinha uma grade com as OPs de um pedido e quatro botões: Liberar, Planejar,
Cancelar e Encerrar. O desenho aqui é o mesmo; o que muda é o que a migração aprendeu:

- **Encerrar passa por plano conferido** (`core/confirmacao.py`) e roda em segundo plano
  (`core/tarefas.py`): 68 OPs são ~270 chamadas à Service Layer, na casa de minutos.
- **A ordem é calculada, não escolhida**: filha antes da mãe, porque a saída de insumo de
  uma OP pai consome o item que a filha produz. Um ciclo recusa a operação inteira.
- **Liberar changes the status only and writes on the first click** (no token plan).
  Undoing it — Replanejar (Liberada -> Planejada) — left the screen on 28/09/2026 (D9 of the
  plan) and lives in the CLI only: next to a half-failed `encerrar` it would break the stock
  chain.
- The read-only routes (`buscar`, `encerrar/conferir`) are plain `def` (30/09/2026): FastAPI
  runs them in its threadpool, so a slow HANA query no longer freezes the event loop — and
  with it /health, the task polling and a task running in the other module.

A regra de negócio de 22/09: uma OP só pode ser apontada estando Liberada, então o
encerramento libera antes a que estiver Planejada. Isso aparece na coluna "Ação" do plano,
em vez de ficar escondido dentro do `corrige_op` — é o estado que sobra se a cadeia falhar.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from controleproducao.config import get_settings
from controleproducao.core.confirmacao import PLANOS, ConfirmacaoInvalida
from controleproducao.core.hana_reader import HanaDirectReader
from controleproducao.core.service_layer_client import ServiceLayerClient
from controleproducao.core.tarefas import TAREFAS, Tarefa, acompanha_log
from controleproducao.core.templates import templates
from controleproducao.core.web import avisa_escrita
from controleproducao.modules.manutencao_op import service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/manutencao-op", tags=["manutencao_op"])

MODULO = "manutencao_op"

COLUNAS_PLANO = ["#", "OP", "Item", "Planejada", "Apontada", "Status atual", "Ação"]


def _erro(request: Request, mensagem: str, **extra):
    return templates.TemplateResponse(
        request, "erro.html",
        {"mensagem": mensagem, "voltar": "/manutencao-op", **extra},
        status_code=400,
    )


def _leitor():
    return HanaDirectReader(get_settings())


# ---------------------------------------------------------------------------
# Consulta (somente leitura)
# ---------------------------------------------------------------------------
@router.get("", response_class=HTMLResponse)
async def pagina_inicial(request: Request):
    return templates.TemplateResponse(
        request, "manutencao_op.html",
        {"ops": [], "doc_num": "", "buscou": False,
         "em_execucao": TAREFAS.em_execucao(MODULO), "status_op": service.STATUS_OP},
    )


@router.post("/buscar", response_class=HTMLResponse)
def buscar(
    request: Request,
    doc_num: str = Form(default=""),
    op_de: str = Form(default=""),
    op_ate: str = Form(default=""),
    status_de: str = Form(default=""),
    status_ate: str = Form(default=""),
):
    leitor = _leitor()
    try:
        ops = service.buscar_ops(
            leitor, doc_num, op_de or None, op_ate or None,
            status_de or None, status_ate or None,
        )
    except ValueError as exc:
        # `_monta_filtro` recusa intervalo invertido e limite superior sozinho, com a
        # mensagem já escrita para o usuário — mostrá-la é melhor que uma tabela vazia,
        # que foi exatamente o que confundiu em 21/09.
        return _erro(request, str(exc))
    finally:
        leitor.close()

    # The filters go back to the form: a filtered list under empty fields read as "all OPs".
    filtros = {"op_de": op_de, "op_ate": op_ate, "status_de": status_de, "status_ate": status_ate}
    return templates.TemplateResponse(
        request, "manutencao_op.html",
        {"ops": ops, "doc_num": doc_num, "buscou": True, "filtros": filtros,
         "filtrado": any(filtros.values()),
         "em_execucao": TAREFAS.em_execucao(MODULO), "status_op": service.STATUS_OP},
    )


# ---------------------------------------------------------------------------
# Liberar — status only, writes on the first click (replanejar is CLI-only since 28/09/2026, D9)
# ---------------------------------------------------------------------------
@router.post("/status", response_class=HTMLResponse)
async def mudar_status(
    request: Request,
    op_docnums: list[str] = Form(default=[]),
    acao: str = Form(default=""),
):
    if acao == "p":
        # Replanejar (Liberada -> Planejada) is CLI-only since 28/09/2026 (D9 of the plan):
        # on the screen it sits one click away from an `encerrar` that half-failed, and
        # replanning there breaks the stock chain (the issue must be reversed first). The
        # CLI keeps it as the recovery path, with the operator reading the log.
        return _erro(
            request,
            "Replanejar não está disponível na tela: use a CLI na .11 — "
            "`python -m controleproducao manutencao-op replanejar` (decisão D9).",
            titulo="Replanejar é só pela CLI",
        )
    if acao != "l":
        # Only "liberar" leaves through here. Bulk cancel belongs to module 2 (it loads the
        # whole order and refuses when an OP is outside the allowed status) and "encerrar"
        # has its own path, with stock movements.
        return _erro(request, "Ação inválida nesta tela: use liberar.")
    if not op_docnums:
        return _erro(request, "Nenhuma OP selecionada.")

    nome = "Liberar OPs"
    avisa_escrita("manutencao-op liberar")

    leitor = _leitor()
    try:
        # In a thread: this route is async (it starts a task) and the read is blocking.
        ops = await asyncio.to_thread(service.levanta_ops, leitor, op_docnums=list(op_docnums))
    finally:
        leitor.close()
    if not ops:
        return _erro(request, "Nenhuma das OPs informadas foi encontrada.")

    # Barra o lote INTEIRO quando há OP em status terminal, em vez de processar as demais
    # em silêncio: a tela desabilita essas caixas, então recebê-las quer dizer que a lista
    # mudou desde que a página carregou (alguém encerrou uma OP nesse meio-tempo) ou que o
    # POST não veio da tela. Nos dois casos, o certo é o usuário reconferir — não é a hora
    # de adivinhar quais linhas ele ainda quer.
    terminais = [op for op in ops if op["status"] in service.STATUS_TERMINAIS]
    if terminais:
        return _erro(
            request,
            f"{len(terminais)} OP(s) selecionada(s) estão em status terminal (Encerrada ou "
            "Cancelada) e não admitem mudança. Nenhuma OP foi alterada — refaça a busca, "
            "porque a lista mudou desde que a tela foi carregada.",
            titulo="OP em status terminal",
            detalhes=[{"OP": o["doc_num"], "Item": o["item_code"],
                       "Status": service.STATUS_OP.get(o["status"], o["status"])}
                      for o in terminais],
            colunas=["OP", "Item", "Status"],
        )

    async def executa(tarefa: Tarefa):
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

    return _dispara(request, nome, ", ".join(str(o["doc_num"]) for o in ops), executa)


# ---------------------------------------------------------------------------
# Encerrar — irreversível: plano conferido + execução em segundo plano
# ---------------------------------------------------------------------------
@router.post("/encerrar/conferir", response_class=HTMLResponse)
def conferir_encerrar(
    request: Request,
    op_docnums: list[str] = Form(default=[]),
    pedido: str = Form(default=""),
):
    pedido = pedido.strip()
    if bool(op_docnums) == bool(pedido):
        return _erro(request, "Selecione OPs na lista OU informe um pedido — não os dois.")

    leitor = _leitor()
    try:
        ops = service.levanta_ops(
            leitor, op_docnums=list(op_docnums) or None, doc_num_pedido=pedido or None
        )
        if not ops:
            return _erro(request, "Nenhuma OP encontrada para o que foi informado.")
        componentes = service._componentes_por_op(leitor, [int(o["doc_entry"]) for o in ops])
    except ValueError as exc:
        # A non-numeric order number ("84a") used to escape as a 500; the message is
        # already written for the user ("Número do pedido: esperado um número…").
        return _erro(request, str(exc))
    finally:
        leitor.close()

    ops, em_ciclo = service.ordena_por_dependencia(ops, componentes)
    if em_ciclo:
        return _erro(
            request,
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
        completa = float(op["apontada"]) >= float(op["planejada"])
        processar = False
        if op["status"] == "L":
            acao = "já encerrada — ignorada"
        elif op["status"] == "C":
            # OP cancelada não pode ser liberada, e liberar é o primeiro passo — a cadeia
            # falharia no início com uma mensagem obscura do SAP. A grade do legado nunca
            # mostrava canceladas; aqui ela aparece com o motivo, porque o usuário pediu
            # esse número e merece saber por que não entra, em vez de vê-la sumir.
            acao = "cancelada — não pode ser encerrada"
        elif completa:
            acao = "ignorada (apontada = planejada)"
        elif op["status"] == "R":
            acao, processar = "saída + entrada + encerrar", True
        else:
            acao, processar = "LIBERAR + saída + entrada + encerrar", True
        if processar:
            a_processar.append(op)
        itens.append({
            "#": indice, "OP": op["doc_num"], "Item": op["item_code"],
            "Planejada": f"{op['planejada']:g}", "Apontada": f"{op['apontada']:g}",
            "Status atual": service.STATUS_OP.get(op["status"], op["status"]),
            "Ação": acao, "_doc_entry": op["doc_entry"], "_status": op["status"],
            # Flag explícita: a decisão de processar é tomada aqui, uma vez, e não
            # reinterpretada depois a partir de uma frase escrita para o usuário ler.
            "_processar": processar,
        })

    if not a_processar:
        return _erro(request, "Nada a encerrar: nenhuma das OPs está em condição de ser encerrada.")

    a_liberar = [o for o in a_processar if o["status"] != "R"]
    plano = PLANOS.criar(
        operacao=("Encerrar OPs do pedido " + pedido) if pedido else "Encerrar OPs",
        resumo={
            "OP(s) a encerrar com lançamento de estoque": len(a_processar),
            "OP(s) Planejadas que serão LIBERADAS antes": len(a_liberar),
            "OP(s) listadas (inclui ignoradas)": len(ops),
        },
        itens=itens,
    )
    return templates.TemplateResponse(
        request, "confirmar.html",
        {"plano": plano.para_json(), "colunas": COLUNAS_PLANO,
         "acao": "/manutencao-op/encerrar/executar",
         "aviso_operacao":
             "Gera saída de insumos e entrada do produto — lançamentos de estoque que o "
             "sistema não desfaz. A ordem abaixo (filha antes da mãe) é calculada pela "
             "estrutura e será seguida; se uma OP falhar, as que dependem dela são puladas.",
         "voltar": "/manutencao-op"},
    )


@router.post("/encerrar/executar")
async def executar_encerrar(request: Request, token: str = Form(default="")):
    try:
        plano = PLANOS.consumir(token)
    except ConfirmacaoInvalida as exc:
        return _erro(request, str(exc), titulo="Confirmação não aceita")
    avisa_escrita(plano.operacao)

    # Do plano conferido saem apenas as OPs que de fato serão processadas, na ordem em que
    # foram mostradas. Reler o banco aqui recalcularia uma ordem que o usuário não viu.
    doc_entries = [int(i["_doc_entry"]) for i in plano.itens if i.get("_processar")]

    async def executa(tarefa: Tarefa):
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

    return _dispara(request, plano.operacao, f"{len(doc_entries)} OP(s)", executa)


def _dispara(request: Request, nome: str, descricao: str, corrotina):
    try:
        tarefa = TAREFAS.criar(MODULO, nome, descricao, corrotina)
    except RuntimeError as exc:
        # Link to the run that DID start (see the same helper in pedidos_wbc/router.py).
        rodando = TAREFAS.em_execucao(MODULO)
        return _erro(request, str(exc), titulo="Já existe execução em andamento",
                     link=f"/tarefas/{rodando.id}" if rodando else None,
                     link_texto="Acompanhar a execução em andamento")
    return RedirectResponse(f"/tarefas/{tarefa.id}", status_code=303)
