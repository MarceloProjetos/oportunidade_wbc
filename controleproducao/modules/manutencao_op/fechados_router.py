"""Screen routes of the "Pedidos fechados com OPs abertas" card (28/10/2026).

A separate card on the Manutenção de OP page (owner, 28/10/2026), in its own file so the
page's existing routes stay exactly as they are: this file only adds routes and reuses the
existing helpers (`router._recusa`, `router._dispara`) and pages
(`manutencao_op.html`, `confirmar.html`). Same flow as "Encerrar todas as OPs de um
pedido": consult (read) → check the plan (read, token) → Sim/Não → background run.
"""
from __future__ import annotations

import logging

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

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/manutencao-op/pedidos-fechados", tags=["manutencao_op"])

COLUNAS_PLANO = ["#", "Pedido", "Cliente", "Entrega", "OPs abertas", "Planejadas (serão liberadas)"]
# A fixed sentence: the exception text may carry the driver's own (logged instead).
MSG_LISTA_NAO_CONFERIDA = ("Não foi possível conferir a lista no SAP (o SAP ou o faturamento de entrega futura "
                           "não respondeu); nenhuma OP foi oferecida para encerrar.")


def _voltar(incluir_atrasados: bool) -> str:
    """Back to the list as it was consulted: without ``atrasados=1`` it could come back empty."""
    return "/manutencao-op/pedidos-fechados" + ("?atrasados=1" if incluir_atrasados else "") + "#pedidos-fechados"


def _leitor() -> HanaDirectReader:
    return HanaDirectReader(get_settings())


def _marcado(valor: str) -> bool:
    return str(valor or "").strip().lower() in ("1", "on", "true", "sim")


@router.get("", response_class=HTMLResponse)
def consultar(request: Request, atrasados: str = ""):
    """The page as `/manutencao-op` renders it, plus the card's list (read-only)."""
    incluir = _marcado(atrasados)
    leitor = _leitor()
    erro = None
    try:
        lista = fechados.pedidos_fechados_com_op(leitor, incluir)
    except Exception as exc:  # noqa: BLE001 - said in the card, not a bare 500
        logger.error("Pedidos fechados com OPs abertas: consulta falhou: %s", exc)
        lista, erro = [], MSG_LISTA_NAO_CONFERIDA
    finally:
        leitor.close()
    return templates.TemplateResponse(
        request, "manutencao_op.html",
        {"ops": [], "doc_num": "", "buscou": False,
         "em_execucao": TAREFAS.em_execucao(tela.MODULO), "status_op": service.STATUS_OP,
         "fechados": lista, "fechados_atrasados": incluir, "fechados_erro": erro},
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
    except Exception as exc:  # noqa: BLE001 - the plan re-reads the list: same answer as the card
        logger.error("Pedidos fechados com OPs abertas: conferência falhou: %s", exc)
        return tela._erro(request, MSG_LISTA_NAO_CONFERIDA, voltar=_voltar(_marcado(incluir_atrasados)))
    finally:
        leitor.close()
    return templates.TemplateResponse(
        request, "confirmar.html",
        {"plano": plano.para_json(), "colunas": COLUNAS_PLANO,
         "acao": "/manutencao-op/pedidos-fechados/encerrar/executar",
         "aviso_operacao":
             "Encerra todas as OPs abertas dos pedidos abaixo com saída de insumos e entrada do "
             "produto — lançamentos de estoque que o sistema não desfaz. Em cada pedido a OP "
             "filha é encerrada antes da mãe, e as Planejadas são liberadas antes. Se faltar custo "
             "de algum insumo, ele recebe o custo informado (reavaliação de estoque) e o "
             "encerramento do pedido é repetido uma vez.",
         "voltar": _voltar(_marcado(incluir_atrasados))},
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
