"""Screen routes of the "Pedidos fechados com OPs abertas" card (28/10/2026).

A separate card on the Manutenção de OP page (owner, 28/10/2026), in its own file so the
page's existing routes stay exactly as they are: this file only adds routes and reuses the
existing helpers (`router._erro`, `router._recusa`, `router._dispara`) and pages
(`manutencao_op.html`, `confirmar.html`). Same flow as "Encerrar todas as OPs de um
pedido": consult (read) → check the plan (read, token) → Sim/Não → background run.
"""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from controleproducao.config import get_settings
from controleproducao.core.hana_reader import HanaDirectReader
from controleproducao.core.recusa import Recusa
from controleproducao.core.tarefas import TAREFAS
from controleproducao.core.templates import templates
from controleproducao.core.web import avisa_escrita
from controleproducao.modules.manutencao_op import fechados, service
from controleproducao.modules.manutencao_op import router as tela

router = APIRouter(prefix="/manutencao-op/pedidos-fechados", tags=["manutencao_op"])

COLUNAS_PLANO = ["#", "Pedido", "Cliente", "Entrega", "OPs abertas", "Planejadas (serão liberadas)"]
VOLTAR = "/manutencao-op/pedidos-fechados#pedidos-fechados"


def _leitor() -> HanaDirectReader:
    return HanaDirectReader(get_settings())


def _marcado(valor: str) -> bool:
    return str(valor or "").strip().lower() in ("1", "on", "true", "sim")


@router.get("", response_class=HTMLResponse)
def consultar(request: Request, atrasados: str = ""):
    """The page as `/manutencao-op` renders it, plus the card's list (read-only)."""
    incluir = _marcado(atrasados)
    leitor = _leitor()
    try:
        lista = fechados.pedidos_fechados_com_op(leitor, incluir)
    finally:
        leitor.close()
    return templates.TemplateResponse(
        request, "manutencao_op.html",
        {"ops": [], "doc_num": "", "buscou": False,
         "em_execucao": TAREFAS.em_execucao(tela.MODULO), "status_op": service.STATUS_OP,
         "fechados": lista, "fechados_atrasados": incluir},
    )


@router.post("/encerrar/conferir", response_class=HTMLResponse)
def conferir(
    request: Request,
    pedidos: list[str] = Form(default=[]),
    custo: str = Form(default=""),
    ativar_inativos: str = Form(default=""),
    incluir_atrasados: str = Form(default=""),
):
    leitor = _leitor()
    try:
        plano = fechados.monta_plano(
            leitor, fechados.le_pedidos(list(pedidos)), fechados.le_custo(custo),
            _marcado(ativar_inativos), _marcado(incluir_atrasados),
        )
    except Recusa as recusa:
        return tela._recusa(request, recusa)
    finally:
        leitor.close()
    return templates.TemplateResponse(
        request, "confirmar.html",
        {"plano": plano.para_json(), "colunas": COLUNAS_PLANO,
         "acao": "/manutencao-op/pedidos-fechados/encerrar/executar",
         "aviso_operacao":
             "Encerra todas as OPs abertas dos pedidos abaixo com saída de insumos e entrada do "
             "produto — lançamentos de estoque que o sistema não desfaz. Em cada pedido a OP "
             "filha é encerrada antes da mãe. Insumo sem custo recebe o custo informado "
             "(reavaliação de estoque) e o pedido é tentado mais uma vez.",
         "voltar": VOLTAR},
    )


@router.post("/encerrar/executar")
async def executar(request: Request, token: str = Form(default="")):
    # The gate first, so a refused write (no key, off the .11) does not spend the token.
    avisa_escrita(fechados.operacao_do_plano(token))
    try:
        plano = fechados.consome_plano(token)
    except Recusa as recusa:
        return tela._recusa(request, recusa)
    return tela._dispara(
        request, plano.operacao, fechados.descricao_do_plano(plano),
        fechados.corrotina(plano), parada_combinada=True,
    )
