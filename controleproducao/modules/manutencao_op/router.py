"""Rotas web do módulo Manutenção de OP — equivalente à tela `ManutencaoOp.b1f.cs`.

A tela original tinha uma grade com as OPs de um pedido e quatro botões: Liberar, Planejar,
Cancelar e Encerrar. O desenho aqui é o mesmo; o que muda é o que a migração aprendeu:

- **Encerrar passa por plano conferido** (`core/confirmacao.py`) e roda em segundo plano
  (`core/tarefas.py`): 68 OPs são ~270 chamadas à Service Layer, na casa de minutos.
- **A ordem é calculada, não escolhida**: filha antes da mãe, porque a saída de insumo de
  uma OP pai consome o item que a filha produz. Um ciclo recusa a operação inteira.
- **Liberar and Replanejar change the status only and write on the first click** (no token
  plan). Replanejar left the screen on 28/09/2026 (D9: next to a half-failed `encerrar` it
  would leave stock on a planned OP) and came back on 29/09/2026 (D4 of
  docs/PLANO_API_MANUTENCAO_OP.md) once it refuses any OP with material issued or product
  received — the reason D9 existed. The grid says which OPs can go back, and why not.
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
        replanejar = _replanejar_por_op(leitor, doc_num, ops)
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
         "filtrado": any(filtros.values()), "replanejar": replanejar,
         "em_execucao": TAREFAS.em_execucao(MODULO), "status_op": service.STATUS_OP},
    )


def _replanejar_por_op(leitor, doc_num: str, ops: list[dict]) -> dict[str, str | None]:
    """``{OP number: None}`` for a Liberada that can go back to Planejada, or the reason it
    cannot ("insumo baixado", "produto apontado"). Planejada and terminal OPs are absent.

    Same rule as the action (`service.saida_lancada`/`entrada_lancada`), so the grid never
    offers what the POST refuses. The issued quantity comes from its own pedido-wide query
    (the grid's query feeds the table columns); if it fails the grid still shows, and every
    Liberada reads "não foi possível conferir o insumo" — the action refuses those too.
    """
    liberadas = [op for op in ops if op.get("Status") == "R"]
    if not liberadas:
        return {}
    try:
        baixadas: dict[int, float] | None = service.baixada_das_ops_do_pedido(leitor, doc_num)
    except Exception as exc:  # noqa: BLE001 - the grid must show even without this detail
        logger.warning("Manutenção de OP: insumo baixado do pedido %s não lido: %s", doc_num, exc)
        baixadas = None
    motivos: dict[str, str | None] = {}
    for op in liberadas:
        numero = int(op["Número OP"])
        baixada = baixadas.get(numero) if baixadas is not None else None
        if baixada is None:
            motivos[str(numero)] = "não foi possível conferir o insumo"
        elif service.saida_lancada({"baixada": baixada}):
            motivos[str(numero)] = "insumo baixado"
        elif service.entrada_lancada({"apontada": op.get("Qtde. Apontada")}):
            motivos[str(numero)] = "produto apontado"
        else:
            motivos[str(numero)] = None
    return motivos


# ---------------------------------------------------------------------------
# Liberar and Replanejar — status only, write on the first click
# ---------------------------------------------------------------------------
@router.post("/status", response_class=HTMLResponse)
async def mudar_status(
    request: Request,
    op_docnums: list[str] = Form(default=[]),
    acao: str = Form(default=""),
):
    if acao not in ("l", "p"):
        # Only Liberar ("l") and Replanejar ("p") leave through here. Bulk cancel belongs to
        # module 2 (it loads the whole order and refuses when an OP is outside the allowed
        # status) and "encerrar" has its own path, with stock movements.
        return _erro(request, "Ação inválida nesta tela: use liberar ou replanejar.")
    if not op_docnums:
        return _erro(request, "Nenhuma OP selecionada.")

    avisa_escrita("manutencao-op liberar" if acao == "l" else "manutencao-op replanejar")

    leitor = _leitor()
    try:
        # In a thread: this route is async (it starts a task) and the read is blocking.
        # Replanejar refuses the whole batch when an OP has material issued or product
        # received (`acoes.prepara_mudanca_status`) — the same refusal as the JSON API.
        ops = await asyncio.to_thread(acoes.prepara_mudanca_status, leitor, list(op_docnums), acao)
    except acoes.Recusa as recusa:
        return _recusa(request, recusa)
    finally:
        leitor.close()

    nome = acoes.NOME_LIBERAR if acao == "l" else acoes.NOME_REPLANEJAR
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
        acoes.corrotina_encerramento(plano), parada_combinada=True,
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


def _dispara(request: Request, nome: str, descricao: str, corrotina, *, parada_combinada: bool = False):
    try:
        tarefa = acoes.dispara(
            nome, descricao, corrotina, ip=request.client.host if request.client else None,
            parada_combinada=parada_combinada,
        )
    except acoes.Recusa as recusa:
        return _recusa(request, recusa)
    return RedirectResponse(f"/tarefas/{tarefa.id}", status_code=303)
