"""Rotas web do módulo Pedidos WBC — equivalente à tela `IntegraPedidoWBC.b1f.cs`.

A tela original tinha dois modos ("Pedidos Novos" e "Pedidos Integrados"), uma grade com
caixas de seleção e botões que agiam sobre o que estava marcado. O desenho aqui é o mesmo,
com três diferenças deliberadas, todas por causa de erros reais desta migração:

1. **Nada grava direto do botão.** Processar, reprocessar e cancelar OPs passam por um
   plano conferido em tela (`core/confirmacao.py`). Reprocessar saiu da tela em 28/09 (D8) e
   voltou em 30/09, a pedido do Marcelo, com o aviso do que ele faz de fato. No legado o botão agia sobre a seleção
   e pronto; aqui o usuário vê antes o que vai acontecer e confirma sobre isso.
2. **A execução é em segundo plano** (`core/tarefas.py`): um orçamento médio leva ~7s e um
   grande passa de minuto — tempo demais para um request segurar.
3. **A trava de produção vale aqui igual à CLI** (`core/web.py`). Mesmo `.env`, mesma regra.

O que a grade protegia e o código não (tema recorrente da migração, seção 8): o filtro da
tela é reproduzido pelas próprias queries de busca — a rota nunca aceita um identificador
digitado que não tenha passado por elas.

Since 01/10/2026 the rules live in ``acoes.py``, shared with the JSON API (``api_router.py``,
PLANO_API_PEDIDOS_WBC.md (removed 2026-10-06)); this file only reads the form and renders HTML.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from controleproducao.config import get_settings
from controleproducao.core.confirmacao import PLANOS
from controleproducao.core.hana_reader import HanaDirectReader
from controleproducao.core.recusa import Recusa
from controleproducao.core.service_layer_client import ServiceLayerClient
from controleproducao.core.tarefas import TAREFAS, Tarefa, acompanha_log
from controleproducao.core.templates import templates
from controleproducao.core.web import avisa_escrita
from controleproducao.modules.pedidos_wbc import acoes, service
from controleproducao.modules.pedidos_wbc.acoes import (
    AVISO_REPROCESSAR,
    MODULO,
    POR_PAGINA,
    TIPO_CANCELAR_OPS,
    TIPO_PROCESSAR,
    TIPO_REPROCESSAR,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pedidos-wbc", tags=["pedidos_wbc"])

# Re-exported: tests and the cancel flow below read them from here.
__all__ = ["AVISO_REPROCESSAR", "MODULO", "POR_PAGINA", "TIPO_CANCELAR_OPS", "TIPO_PROCESSAR",
           "TIPO_REPROCESSAR", "router"]

COLUNAS_PEDIDO = ["Pedido", "Oportunidade", "WBC", "Cliente", "Total"]
COLUNAS_OP = ["doc_num", "item_code", "status", "descricao"]


def _erro(request: Request, mensagem: str, **extra):
    return templates.TemplateResponse(
        request, "erro.html",
        {"mensagem": mensagem, "voltar": "/pedidos-wbc", **extra},
        status_code=400,
    )


# ---------------------------------------------------------------------------
# Consulta (somente leitura)
# ---------------------------------------------------------------------------
@router.get("", response_class=HTMLResponse)
async def pagina_inicial(
    request: Request,
    modo: str = "novos",
    buscar: int | None = None,
    pagina: int = 1,
):
    """Lista e paginação viajam na URL, de propósito.

    A busca era um POST, e com isso o F5 numa página de resultado pedia reenvio do
    formulário e "página 2" não tinha endereço. Sendo GET, cada página é um link comum:
    dá para voltar, recarregar e favoritar. É leitura pura — nada aqui grava.

    Opening the page with no parameters already searches "Pedidos novos" (asked by the
    owner, 30/09/2026): it is what the operator wants first, one click less. ``?buscar=0``
    still opens the empty page. If that automatic read fails the page opens anyway, with a
    notice to reload — a click the operator chose may fail loudly, the page itself not. There
    is no "Buscar" button since 30/09/2026: picking the mode submits the form.
    """
    integrados = modo == "integrados"
    automatica = buscar is None and not integrados
    pedidos: list = []
    erro_busca = None
    if buscar or automatica:
        try:
            # Closed on the way out: hdbcli connections are released only by close() or GC.
            with HanaDirectReader(get_settings()) as hana_reader:
                pedidos = await acoes.le_pedidos(hana_reader, integrados)
        except Exception as exc:  # noqa: BLE001 - only the automatic search is softened
            if not automatica:
                raise
            logger.warning("Pedidos WBC: busca automática ao abrir falhou: %s", exc)
            erro_busca = acoes.MSG_BUSCA_AUTOMATICA_FALHOU

    folha = acoes.pagina(pedidos, pagina)
    return templates.TemplateResponse(
        request, "pedidos_wbc.html",
        {
            "modo": "integrados" if integrados else "novos",
            "pedidos": folha.itens,
            "buscou": bool(buscar or automatica) and erro_busca is None,
            "erro_busca": erro_busca,
            "total": folha.total,
            "pagina": folha.pagina,
            "paginas": folha.paginas,
            "primeiro": folha.primeiro,
            "ultimo": folha.ultimo,
            "em_execucao": TAREFAS.em_execucao(MODULO),
        },
    )


# ---------------------------------------------------------------------------
# Etapa 1 — planos (leem, não gravam)
# ---------------------------------------------------------------------------
@router.post("/processar/conferir", response_class=HTMLResponse)
async def conferir_processar(
    request: Request,
    opp_ids: list[str] = Form(default=[]),
    force: str = Form(default=""),
):
    return await _confere_pedidos(request, opp_ids, integrados=False, force=bool(force))


@router.post("/reprocessar/conferir", response_class=HTMLResponse)
async def conferir_reprocessar(request: Request, opp_ids: list[str] = Form(default=[])):
    # Back on the screen on 30/09/2026 (D8 reversed by the owner), behind the same checked
    # plan + single-use token as Processar.
    return await _confere_pedidos(request, opp_ids, integrados=True, force=False)


async def _confere_pedidos(request: Request, opp_ids: list[str], integrados: bool, force: bool):
    if not opp_ids:
        return _erro(request, acoes.MSG_NENHUM_SELECIONADO)
    # The list is read again instead of trusting the form (see `acoes.monta_plano`).
    with HanaDirectReader(get_settings()) as hana_reader:
        elegiveis = await acoes.le_pedidos(hana_reader, integrados)
    try:
        plano = acoes.monta_plano(elegiveis, opp_ids, integrados, force=force)
    except Recusa as recusa:
        return _erro(request, recusa.mensagem)
    operacao = acoes.operacao_do_modo(integrados)
    return templates.TemplateResponse(
        request, "confirmar.html",
        {"plano": acoes.plano_visivel(plano), "colunas": COLUNAS_PEDIDO,
         "acao": f"/pedidos-wbc/{operacao.tipo}/executar",
         "aviso_destaque": operacao.destaque, "aviso_operacao": operacao.aviso,
         "voltar": "/pedidos-wbc"},
    )


@router.post("/cancelar-ops/conferir", response_class=HTMLResponse)
async def conferir_cancelar_ops(
    request: Request,
    doc_num: str = Form(default=""),
    orc_num: str = Form(default=""),
):
    # The form lives on the Manutenção de OP page since 01/10/2026: "Voltar" goes there.
    voltar = "/manutencao-op"
    # Stripped: a pasted " 84439" is text-compared in the SQL and would read as "not found".
    doc_num, orc_num = doc_num.strip(), orc_num.strip()
    if not (doc_num or orc_num):
        return _erro(request, "Informe o nº do pedido ou o nº do orçamento.", voltar=voltar)

    with HanaDirectReader(get_settings()) as hana_reader:
        levantamento = await service.levanta_ops_para_cancelamento(
            hana_reader, doc_num=doc_num or None, orc_num=orc_num or None
        )
    if not levantamento["pedido"]:
        return _erro(request, "Pedido não encontrado.", voltar=voltar)

    # Mesma regra da CLI: havendo qualquer OP fora dos status que permitem cancelar,
    # NADA é cancelado — nem as planejadas. Cancelar metade deixa o pedido num estado
    # que ninguém sabe descrever depois.
    if levantamento["bloqueantes"]:
        return _erro(
            request,
            f"{len(levantamento['bloqueantes'])} OP(s) deste pedido estão em status que "
            "não permite cancelamento. Nenhuma OP será cancelada.",
            titulo="Cancelamento bloqueado",
            detalhes=levantamento["bloqueantes"], colunas=COLUNAS_OP, voltar=voltar,
        )
    if not levantamento["a_cancelar"]:
        return _erro(request, "Não há OP planejada a cancelar neste pedido.", voltar=voltar)

    pedido = levantamento["pedido"]
    plano = PLANOS.criar(
        operacao=f"Cancelar OPs do pedido {pedido.get('DocNum')}",
        resumo={
            "OP(s) a cancelar": len(levantamento["a_cancelar"]),
            "OP(s) já canceladas (ignoradas)": len(levantamento["ja_canceladas"]),
            "OP(s) no pedido": len(levantamento["ops"]),
            # The execution reads the order again (see `service.cancela_ops_conferidas`).
            "_doc_num": pedido.get("DocNum"),
        },
        itens=levantamento["a_cancelar"],
        tipo=TIPO_CANCELAR_OPS,
    )
    return templates.TemplateResponse(
        request, "confirmar.html",
        {"plano": acoes.plano_visivel(plano), "colunas": COLUNAS_OP,
         "acao": "/pedidos-wbc/cancelar-ops/executar",
         "aviso_operacao": "Cancela as Ordens de Produção listadas e, se todas ficarem "
                           "canceladas, devolve o pedido a \"não processado\" "
                           "(U_INO_ProcessWBC='N' e U_INO_OP zerado nas linhas), como a CLI. "
                           "Na execução as OPs são relidas: se alguma tiver mudado de status, "
                           "só as que continuam planejadas são canceladas. Uma OP cancelada "
                           "não volta atrás — só recriando.",
         "voltar": voltar},
    )


# ---------------------------------------------------------------------------
# Etapa 2 — execução (grava; exige token e passa pela trava de produção)
# ---------------------------------------------------------------------------
def _consome(request: Request, token: str, tipo: str):
    """Valida o token do plano conferido. Devolve `(plano, resposta_de_erro)`.

    A conferência em duas etapas **não é** a trava de produção (removida em 22/09/2026):
    ela vale igual em homologação e existe porque a operação é irreversível — o usuário
    confirma sobre o que foi calculado e mostrado, e um F5 não reexecuta.

    The write gate comes BEFORE the token is spent (01/10/2026, as module 3 does since 29/09);
    `acoes.consome_plano` checks the busy module before spending it too.
    """
    avisa_escrita(acoes.operacao_do_token(token))
    try:
        return acoes.consome_plano(token, tipo), None
    except Recusa as recusa:
        if recusa.tipo == "ocupado":
            return None, _ocupado(request, recusa)
        return None, _erro(request, recusa.mensagem, titulo=recusa.titulo)


def _ocupado(request: Request, exc: Exception):
    # The link matters: after a double submit the operator must land on the run that DID
    # start, not on an error that invites doing it again.
    rodando = TAREFAS.em_execucao(MODULO)
    return _erro(request, str(exc), titulo="Já existe execução em andamento",
                 link=f"/tarefas/{rodando.id}" if rodando else None,
                 link_texto="Acompanhar a execução em andamento")


def _dispara(request: Request, nome: str, descricao: str, corrotina):
    try:
        tarefa = acoes.dispara(nome, descricao, corrotina,
                               ip=request.client.host if request.client else None)
    except Recusa as recusa:
        return _ocupado(request, recusa)
    return RedirectResponse(f"/tarefas/{tarefa.id}", status_code=303)


@router.post("/processar/executar")
async def executar_processar(request: Request, token: str = Form(default="")):
    plano, erro = _consome(request, token, TIPO_PROCESSAR)
    if erro:
        return erro
    return _dispara(request, plano.operacao, acoes.descricao(plano), acoes.corrotina_pedidos(plano))


@router.post("/reprocessar/executar")
async def executar_reprocessar(request: Request, token: str = Form(default="")):
    plano, erro = _consome(request, token, TIPO_REPROCESSAR)
    if erro:
        return erro
    return _dispara(request, plano.operacao, acoes.descricao(plano), acoes.corrotina_pedidos(plano))


def _relata_cancelamento(tarefa: Tarefa, doc_num, resultado: dict) -> None:
    """The cancel run, line by line, in the operator's words."""
    if tarefa.parada_pedida and not resultado["nao_iniciadas"]:
        tarefa.parada_pedida = False
        tarefa.anota("Interrupção pedida com a última OP já em andamento — nenhuma ficou de fora.")
    for op in resultado["status_mudou"]:
        tarefa.anota(f"OP {op['doc_num']}: não tocada — já não está planejada (o status mudou "
                     "depois da conferência).")
    for op in resultado["nao_conferidas"]:
        tarefa.anota(f"OP {op['doc_num']}: planejada, mas não estava na conferência — não "
                     "tocada. Confira o pedido de novo.", problema=True)
    for op in resultado["nao_iniciadas"]:
        tarefa.anota(f"OP {op['doc_num']}: NÃO INICIADA — a execução foi interrompida antes dela.")
    for erro in resultado["com_erro"]:
        tarefa.anota(f"OP {erro['doc_num']}: ERRO — {erro['motivo']}", problema=True)
    limpeza = resultado["limpeza"]
    if isinstance(limpeza, dict):
        tarefa.anota(f"Pedido {doc_num} devolvido a \"não processado\" (U_INO_ProcessWBC='N'; "
                     f"U_INO_OP zerado em {limpeza.get('linhas_limpas', 0)} linha(s)).")
    elif limpeza == "pulada":
        tarefa.anota(f"Pedido {doc_num} NÃO foi devolvido a \"não processado\": ainda há OP "
                     "planejada ou com erro. Resolva e rode o cancelamento de novo.", problema=True)


@router.post("/cancelar-ops/executar")
async def executar_cancelar_ops(request: Request, token: str = Form(default="")):
    plano, erro = _consome(request, token, TIPO_CANCELAR_OPS)
    if erro:
        return erro
    ops = plano.itens
    doc_num = plano.resumo.get("_doc_num")

    async def executa(tarefa: Tarefa):
        settings = get_settings()
        tarefa.avanca(f"Relendo as OPs do pedido {doc_num} e cancelando {len(ops)}…", 0, len(ops))
        with HanaDirectReader(settings) as hana_reader, acompanha_log(tarefa, service.__name__):
            async with ServiceLayerClient(settings) as sl:
                resultado = await service.cancela_ops_conferidas(
                    sl, hana_reader, doc_num, ops, deve_parar=lambda: tarefa.parada_pedida,
                )
        _relata_cancelamento(tarefa, doc_num, resultado)
        tarefa.avanca(
            f"{len(resultado['canceladas'])} cancelada(s), "
            f"{len(resultado['com_erro'])} com erro"
            + (f", {len(resultado['status_mudou'])} com status mudado (não tocadas)"
               if resultado["status_mudou"] else "")
            + (f", {len(resultado['nao_iniciadas'])} não iniciada(s) (interrompida)"
               if resultado["nao_iniciadas"] else "")
            + ".",
            len(resultado["canceladas"]) + len(resultado["com_erro"]),
        )
        return resultado

    return _dispara(request, plano.operacao, f"{len(ops)} OP(s)", executa)
