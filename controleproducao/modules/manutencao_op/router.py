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

Since 29/09/2026 (F1 of docs/PLANO_API_MANUTENCAO_OP.md) the decisions of these routes live
in `acoes.py`, shared with the JSON API (`api_router.py`): this file reads the form, calls
the gate and renders — a refusal (`acoes.Recusa`) becomes the error page, 400 as always.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from controleproducao.config import get_settings
from controleproducao.core.hana_reader import HanaDirectReader
from controleproducao.core.tarefas import TAREFAS
from controleproducao.core.templates import templates
from controleproducao.core.web import avisa_escrita
from controleproducao.modules.manutencao_op import acoes, service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/manutencao-op", tags=["manutencao_op"])

MODULO = acoes.MODULO

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

    avisa_escrita("manutencao-op liberar")

    leitor = _leitor()
    try:
        # In a thread: this route is async (it starts a task) and the read is blocking.
        ops = await asyncio.to_thread(acoes.prepara_mudanca_status, leitor, list(op_docnums))
    except acoes.Recusa as recusa:
        return _recusa(request, recusa)
    finally:
        leitor.close()

    nome = acoes.NOME_LIBERAR
    return _dispara(
        request, nome, ", ".join(str(o["doc_num"]) for o in ops),
        acoes.corrotina_mudanca_status(nome, ops, acao),
    )


# ---------------------------------------------------------------------------
# Encerrar — irreversível: plano conferido + execução em segundo plano
# ---------------------------------------------------------------------------
@router.post("/encerrar/conferir", response_class=HTMLResponse)
def conferir_encerrar(
    request: Request,
    op_docnums: list[str] = Form(default=[]),
    pedido: str = Form(default=""),
):
    leitor = _leitor()
    try:
        plano = acoes.monta_plano_encerramento(leitor, list(op_docnums), pedido)
    except acoes.Recusa as recusa:
        return _recusa(request, recusa)
    finally:
        leitor.close()

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
    # The gate first, so a refused write (no key, off the .11) does not spend the token.
    avisa_escrita(acoes.operacao_do_plano(token))
    try:
        plano = acoes.consome_plano(token)
    except acoes.Recusa as recusa:
        return _recusa(request, recusa)
    return _dispara(
        request, plano.operacao, acoes.descricao_do_plano(plano),
        acoes.corrotina_encerramento(plano),
    )


def _recusa(request: Request, recusa: acoes.Recusa):
    """The screen's rendering of a refusal: the error page, always 400 (as before)."""
    extra: dict = {}
    if recusa.titulo:
        extra["titulo"] = recusa.titulo
    if recusa.detalhes:
        extra.update(detalhes=recusa.detalhes, colunas=recusa.colunas)
    if recusa.execucao is not None:
        # Link to the run that DID start (see the same helper in pedidos_wbc/router.py).
        extra.update(link=f"/tarefas/{recusa.execucao.id}",
                     link_texto="Acompanhar a execução em andamento")
    return _erro(request, recusa.mensagem, **extra)


def _dispara(request: Request, nome: str, descricao: str, corrotina):
    try:
        tarefa = acoes.dispara(nome, descricao, corrotina, ip=request.client.host if request.client else None)
    except acoes.Recusa as recusa:
        return _recusa(request, recusa)
    return RedirectResponse(f"/tarefas/{tarefa.id}", status_code=303)
