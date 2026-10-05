"""JSON API of the Pedidos WBC (01/10/2026, F2 of docs/PLANO_API_PEDIDOS_WBC.md).

The contract for consumers is ``API_PEDIDOS_WBC.md`` at the repository root — written so
another team can clone the screen *Integração de Pedidos (WBC)* with the same behaviour.

Designed FROM the screen: every route is one action of the page, and every text the server
decides (the list's labels, the confirmation's notice, refusals, the execution's lines) comes
ready in the answer — a clone draws what it receives and re-implements no rule.

It lives in THIS process, next to the screen, for the same reason as the Manutenção de OP
API: the one-execution-per-module lock (`core.tarefas.TAREFAS`) and the single-use tokens
(`core.confirmacao.PLANOS`) are process memory. An execution started here blocks the
screen's (and vice versa), shows on Execuções and in its Supabase history, and makes
``deploy_update.bat`` wait.

Rules of the API alone:

- ``X-API-Key`` only (``core/acesso.py``); CORS for any origin, ``charset=utf-8``.
- ``solicitante`` is required on every write and on cancel (log + history).
- No "forçar" (owner, 01/10/2026): ``force``/``forcar`` in a body is refused with 400.
- Every error is ``{"ok": false, "tipo", "motivo"}``.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from controleproducao.config import get_settings
from controleproducao.core import tarefas_router
from controleproducao.core.confirmacao import Plano
from controleproducao.core.hana_reader import HanaDirectReader
from controleproducao.core.recusa import Recusa
from controleproducao.core.tarefas import ORIGEM_API, TAREFAS, Tarefa
from controleproducao.core.web import avisa_escrita
from controleproducao.modules.pedidos_wbc import acoes

logger = logging.getLogger(__name__)

PREFIXO = "/api/pedidos-wbc"

router = APIRouter(prefix=PREFIXO, tags=["api_pedidos_wbc"])

LIMITE_SOLICITANTE = 80
MODOS = ("novos", "integrados")

MSG_SAP_INDISPONIVEL = "Não foi possível ler o SAP agora — tente de novo em instantes."
MSG_SOLICITANTE = (
    f"Informe 'solicitante': quem pediu a operação (nome ou login), até {LIMITE_SOLICITANTE} "
    "caracteres. Ele vai para o log e para o histórico de Execuções."
)
MSG_SEM_FORCAR = (
    "'forçar' não existe nesta API (decisão de 01/10/2026): ele duplica OP. Use a tela do "
    "Controle de Produção se for mesmo preciso."
)
MSG_MODO = "'modo' deve ser 'novos' ou 'integrados'."
MSG_PAGINA = "'pagina' deve ser um número inteiro a partir de 1."


# ---------------------------------------------------------------------------
# Plumbing
# ---------------------------------------------------------------------------
def _leitor() -> HanaDirectReader:
    return HanaDirectReader(get_settings())


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _falha(tipo: str, motivo: str, http: int, **extra: Any) -> JSONResponse:
    return JSONResponse({"ok": False, "tipo": tipo, "motivo": motivo, **extra}, status_code=http)


def _de_recusa(recusa: Recusa) -> JSONResponse:
    extra: dict[str, Any] = dict(recusa.dados)
    if recusa.execucao is not None:
        extra["execucao_em_andamento"] = _resumo_da_execucao(recusa.execucao)
    return _falha(recusa.tipo, recusa.mensagem, recusa.http, **extra)


def _sap_fora(o_que: str, exc: Exception) -> JSONResponse:
    logger.error("API pedidos-wbc: %s falhou: %s", o_que, exc)
    return _falha("sap_indisponivel", MSG_SAP_INDISPONIVEL, 502)


def _url_do_estado(tarefa_id: str) -> str:
    return f"{PREFIXO}/execucoes/{tarefa_id}"


def _execucao(tarefa: Tarefa) -> dict:
    return {**tarefa.para_json(), "parada_combinada": tarefa.parada_combinada,
            "estado": _url_do_estado(tarefa.id)}


def _resumo_da_execucao(tarefa: Tarefa) -> dict:
    return {
        "id": tarefa.id, "nome": tarefa.nome, "descricao": tarefa.descricao,
        "origem": tarefa.origem, "solicitante": tarefa.solicitante,
        "criada_em": tarefa.criada_em.isoformat(timespec="seconds"),
        "estado": _url_do_estado(tarefa.id),
    }


def _numero(valor: Any) -> float | int | None:
    if valor is None:
        return None
    valor = float(valor)
    return int(valor) if valor.is_integer() else valor


# ---------------------------------------------------------------------------
# Reading the body — refusals are `Recusa("invalido")`, answered before the write gate
# ---------------------------------------------------------------------------
def _objeto(corpo: Any) -> dict:
    if corpo is None:
        return {}
    if not isinstance(corpo, dict):
        raise Recusa("invalido", "O corpo deve ser um objeto JSON.")
    if "force" in corpo or "forcar" in corpo or "forçar" in corpo:
        raise Recusa("invalido", MSG_SEM_FORCAR)
    return corpo


def _solicitante(dados: dict) -> str:
    valor = dados.get("solicitante")
    texto = valor.strip() if isinstance(valor, str) else ""
    # Control characters would forge extra lines in the log this value is written to.
    if not texto or len(texto) > LIMITE_SOLICITANTE or any(ord(c) < 32 or ord(c) == 127 for c in texto):
        raise Recusa("invalido", MSG_SOLICITANTE)
    return texto


def _oportunidades(dados: dict) -> list[str]:
    """The selection: Oportunidade numbers (the screen's checkbox value), as text."""
    valor = dados.get("oportunidades")
    if valor is None or valor == []:
        raise Recusa("invalido", acoes.MSG_NENHUM_SELECIONADO)
    if not isinstance(valor, list):
        raise Recusa("invalido", "'oportunidades' deve ser uma lista de números de Oportunidade.")
    numeros = []
    for v in valor:
        # `True` is an int in Python and would become "1".
        texto = str(v).strip() if isinstance(v, (int, str)) and not isinstance(v, bool) else ""
        if not texto.isdigit():
            raise Recusa(
                "invalido",
                f"'oportunidades' deve conter só números de Oportunidade: recebido {v!r}.",
            )
        numeros.append(str(int(texto)))
    return list(dict.fromkeys(numeros))


def _texto_obrigatorio(dados: dict, campo: str, mensagem: str) -> str:
    valor = dados.get(campo)
    if not isinstance(valor, str) or not valor.strip():
        raise Recusa("invalido", mensagem)
    return valor.strip()


# ---------------------------------------------------------------------------
# Writing the answer
# ---------------------------------------------------------------------------
def _pedido(p) -> dict:
    """One row of the list, with the screen's columns."""
    return {
        "pedido": p.doc_num,
        "oportunidade": p.opp_id,
        "wbc": p.orc_num_masc,
        "cliente_codigo": p.cod_cliente,
        "cliente_nome": p.nome_cliente,
        "cliente": f"{p.cod_cliente} — {p.nome_cliente}",
        "total": _numero(p.total_pedido),
        "criado": str(p.data_lancamento)[:10],
        "nota_espelho": p.nota_espelho,
    }


def _acao(integrados: bool) -> dict:
    """What the selection of this mode does — the screen's button, under the table."""
    op = acoes.operacao_do_modo(integrados)
    return {
        "tipo": op.tipo,
        "verbo": op.verbo,
        "botao": f"{op.verbo} selecionados…",
        "conferir": f"{PREFIXO}/{op.tipo}/conferir",
        "aviso": acoes.AVISO_LISTA_REPROCESSAR if integrados else None,
    }


def _plano(plano: Plano) -> dict:
    integrados = plano.tipo == acoes.TIPO_REPROCESSAR
    op = acoes.operacao_do_modo(integrados)
    itens = [
        {"pedido": i["Pedido"], "oportunidade": i["Oportunidade"], "wbc": i["WBC"],
         "cliente_codigo": i["_cliente_codigo"], "cliente_nome": i["_cliente_nome"],
         "cliente": i["Cliente"], "total": _numero(i["_total"]), "total_texto": i["Total"]}
        for i in plano.itens
    ]
    return {
        "token": plano.token,
        "tipo": plano.tipo,
        "operacao": plano.operacao,
        "valido_ate": plano.para_json()["valido_ate"],
        "resumo": {"pedidos": len(itens)},
        # The screen's summary line, word for word: "<b>1</b> pedido(s) selecionado(s)".
        "resumo_tela": [{"valor": len(itens), "rotulo": acoes.RESUMO_SELECIONADOS}],
        "aviso": {"destaque": op.destaque, "texto": op.aviso},
        "executar": f"{PREFIXO}/{op.tipo}/executar",
        "itens": itens,
    }


def _dispara(request: Request, plano: Plano, solicitante: str) -> JSONResponse:
    try:
        tarefa = acoes.dispara(
            plano.operacao, acoes.descricao(plano), acoes.corrotina_pedidos(plano),
            solicitante=solicitante, origem=ORIGEM_API, ip=_ip(request),
        )
    except Recusa as recusa:
        return _de_recusa(recusa)
    return JSONResponse({"ok": True, "execucao": _execucao(tarefa)}, status_code=202)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.get("/pedidos")
def listar(modo: str = "novos", pagina: str = "1"):
    """One page of "Pedidos novos" or "Pedidos integrados" — the screen's table, KPI and paging.

    Plain ``def``: FastAPI runs it in its threadpool (no event loop there), so the HANA read
    neither stalls the loop (the task polling, a running execution) nor needs one of its own
    beyond the ``asyncio.run`` the async service call asks for.
    """
    if modo not in MODOS:
        return _falha("invalido", MSG_MODO, 400)
    if not pagina.strip().isdigit() or int(pagina) < 1:
        return _falha("invalido", MSG_PAGINA, 400)
    integrados = modo == "integrados"
    try:
        with _leitor() as leitor:
            pedidos = asyncio.run(acoes.le_pedidos(leitor, integrados))
    except Exception as exc:  # noqa: BLE001 - a read that failed is a 502, never a 500
        return _sap_fora(f"lista de pedidos ({modo})", exc)

    folha = acoes.pagina(pedidos, int(pagina))
    rodando = TAREFAS.em_execucao(acoes.MODULO)
    return {
        "ok": True,
        "modo": modo,
        "kpi": {"valor": folha.total, "rotulo": "Integrados" if integrados else "Novos"},
        "total": folha.total,
        "pagina": folha.pagina,
        "paginas": folha.paginas,
        "por_pagina": acoes.POR_PAGINA,
        "primeiro": folha.primeiro,
        "ultimo": folha.ultimo,
        "pedidos": [_pedido(p) for p in folha.itens],
        "vazio": acoes.MSG_LISTA_VAZIA if not folha.total else None,
        "acao": _acao(integrados),
        "execucao_em_andamento": _resumo_da_execucao(rodando) if rodando else None,
    }


async def _conferir(corpo: Any, integrados: bool):
    try:
        selecao = _oportunidades(_objeto(corpo))
    except Recusa as recusa:
        return _de_recusa(recusa)
    try:
        with _leitor() as leitor:
            elegiveis = await asyncio.to_thread(asyncio.run, acoes.le_pedidos(leitor, integrados))
    except Exception as exc:  # noqa: BLE001 - see listar
        return _sap_fora("releitura da lista elegível", exc)
    try:
        plano = acoes.monta_plano(elegiveis, selecao, integrados)
    except Recusa as recusa:
        return _de_recusa(recusa)
    return {"ok": True, "plano": _plano(plano)}


@router.post("/processar/conferir")
async def conferir_processar(corpo: Any = Body(default=None)):
    """The checked plan of Processar and its 10-minute token. Reads only."""
    return await _conferir(corpo, integrados=False)


@router.post("/reprocessar/conferir")
async def conferir_reprocessar(corpo: Any = Body(default=None)):
    """The checked plan of Reprocessar and its 10-minute token. Reads only."""
    return await _conferir(corpo, integrados=True)


async def _consome_e_executa(request: Request, corpo: Any, tipo: str):
    """Write gate, then the token (`acoes.consome_plano`), then the background run."""
    try:
        dados = _objeto(corpo)
        solicitante = _solicitante(dados)
        token = _texto_obrigatorio(
            dados, "token", f"Informe o 'token' devolvido por {PREFIXO}/{tipo}/conferir."
        )
    except Recusa as recusa:
        return _de_recusa(recusa)

    # The gate before the token: a refused write must not spend it.
    avisa_escrita(f"{acoes.operacao_do_token(token)} (API)")
    try:
        plano = acoes.consome_plano(token, tipo)
    except Recusa as recusa:
        return _de_recusa(recusa)
    return _dispara(request, plano, solicitante)


@router.post("/processar/executar")
async def executar_processar(request: Request, corpo: Any = Body(default=None)):
    """Runs a checked Processar plan. IRREVERSIBLE: creates OPs, items and resources."""
    return await _consome_e_executa(request, corpo, acoes.TIPO_PROCESSAR)


@router.post("/reprocessar/executar")
async def executar_reprocessar(request: Request, corpo: Any = Body(default=None)):
    """Runs a checked Reprocessar plan. IRREVERSIBLE: cancels the planned OPs, recreates none."""
    return await _consome_e_executa(request, corpo, acoes.TIPO_REPROCESSAR)


async def _execucao_do_modulo(tarefa_id: str) -> tuple[Tarefa | None, JSONResponse | None]:
    """The execution by id — only this module's: this API does not show or stop the others."""
    tarefa, status, mensagem = await tarefas_router.procura(tarefa_id)
    if tarefa is None:
        tipo = "historico_indisponivel" if status == 503 else "nao_encontrada"
        return None, _falha(tipo, mensagem, status)
    if tarefa.modulo != acoes.MODULO:
        return None, _falha("nao_encontrada", tarefas_router.NAO_ENCONTRADA, 404)
    return tarefa, None


@router.get("/execucoes/{tarefa_id}")
async def estado(tarefa_id: str):
    """The same state the screen's execution page polls every 2 s, plus who asked."""
    tarefa, recusa = await _execucao_do_modulo(tarefa_id)
    if recusa is not None:
        return recusa
    return {"ok": True, "execucao": _execucao(tarefa)}


@router.post("/execucoes/{tarefa_id}/cancelar")
async def cancelar(request: Request, tarefa_id: str, corpo: Any = Body(default=None)):
    """Stops a running execution after the pedido in progress. Does NOT undo what was written."""
    try:
        solicitante = _solicitante(_objeto(corpo))
    except Recusa as recusa:
        return _de_recusa(recusa)

    tarefa, recusa = await _execucao_do_modulo(tarefa_id)
    if recusa is not None:
        return recusa
    cancelou = await TAREFAS.cancelar(tarefa.id)
    entre_etapas = bool(tarefa.parada_combinada)
    logger.warning(
        "Execução %s: interrupção pedida pela API · solicitante %s · ip %s · %s",
        tarefa.id, solicitante, _ip(request) or "—",
        ("para depois do pedido em curso" if entre_etapas else "interrompida") if cancelou
        else "já tinha terminado",
    )
    return {"ok": True, "cancelada": cancelou, "entre_etapas": entre_etapas}
