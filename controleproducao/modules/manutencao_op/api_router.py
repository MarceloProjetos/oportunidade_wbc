"""JSON API of the Manutenção de OP (29/09/2026, F3 of docs/PLANO_API_MANUTENCAO_OP.md).

The contract for consumers is ``API_MANUTENCAO_OP.md`` at the repository root.

It lives in THIS process, next to the screen, on purpose: the one-execution-per-module lock
(`core.tarefas.TAREFAS`) and the single-use tokens of the closing plan (`core.confirmacao.
PLANOS`) are process memory. An API anywhere else would see neither. What that buys for
free: an execution started here blocks the screen's (and vice versa), shows on the
Execuções screen and in its Supabase history, and makes ``deploy_update.bat`` wait.

Every decision comes from `acoes.py`/`service.py` — the same functions the screen calls —
so a refusal carries the screen's own text. This file only translates: JSON in, JSON out,
``{"ok": false, "tipo", "motivo"}`` on every error (the shape of the SIS OP route). Rules
that belong to the API alone:

- ``X-API-Key`` only (``core/acesso.py``); no cookie, no ``?key=``.
- ``solicitante`` is required on every write and on cancel: it goes to the log and to the
  history. It is what the caller declares, not a verified identity (the key is shared).
- Replanejar is not here yet: it is the last phase of the plan (F6), after the rest has
  been deployed and tested for real.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from controleproducao.config import get_settings
from controleproducao.core import tarefas_router
from controleproducao.core.confirmacao import Plano
from controleproducao.core.hana_reader import HanaDirectReader
from controleproducao.core.tarefas import ORIGEM_API, TAREFAS, Tarefa
from controleproducao.core.web import avisa_escrita
from controleproducao.modules.manutencao_op import acoes, service

logger = logging.getLogger(__name__)

PREFIXO = "/api/manutencao-op"

router = APIRouter(prefix=PREFIXO, tags=["api_manutencao_op"])

LIMITE_SOLICITANTE = 80

MSG_SAP_INDISPONIVEL = "Não foi possível ler o SAP agora — tente de novo em instantes."
MSG_SOLICITANTE = (
    f"Informe 'solicitante': quem pediu a operação (nome ou login), até {LIMITE_SOLICITANTE} "
    "caracteres. Ele vai para o log e para o histórico de Execuções."
)


# ---------------------------------------------------------------------------
# Plumbing
# ---------------------------------------------------------------------------
def _leitor() -> HanaDirectReader:
    return HanaDirectReader(get_settings())


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _falha(tipo: str, motivo: str, http: int, **extra: Any) -> JSONResponse:
    return JSONResponse({"ok": False, "tipo": tipo, "motivo": motivo, **extra}, status_code=http)


def _chave(rotulo: str) -> str:
    """Screen column label → JSON key ("Status atual" → "status_atual")."""
    return rotulo.lower().replace(" ", "_")


def _de_recusa(recusa: acoes.Recusa) -> JSONResponse:
    extra: dict[str, Any] = {}
    if recusa.detalhes:
        extra["detalhes"] = [{_chave(k): v for k, v in d.items()} for d in recusa.detalhes]
    if "itens" in recusa.dados:
        extra["itens"] = [_item_do_plano(i) for i in recusa.dados["itens"]]
    if recusa.execucao is not None:
        extra["execucao_em_andamento"] = _resumo_da_execucao(recusa.execucao)
    return _falha(recusa.tipo, recusa.mensagem, recusa.http, **extra)


def _sap_fora(o_que: str, exc: Exception) -> JSONResponse:
    logger.error("API manutencao-op: %s falhou: %s", o_que, exc)
    return _falha("sap_indisponivel", MSG_SAP_INDISPONIVEL, 502)


def _url_do_estado(tarefa_id: str) -> str:
    return f"{PREFIXO}/execucoes/{tarefa_id}"


def _execucao(tarefa: Tarefa) -> dict:
    return {**tarefa.para_json(), "estado": _url_do_estado(tarefa.id)}


def _resumo_da_execucao(tarefa: Tarefa) -> dict:
    return {
        "id": tarefa.id, "nome": tarefa.nome, "descricao": tarefa.descricao,
        "origem": tarefa.origem, "solicitante": tarefa.solicitante,
        "criada_em": tarefa.criada_em.isoformat(timespec="seconds"),
        "estado": _url_do_estado(tarefa.id),
    }


# ---------------------------------------------------------------------------
# Reading the body — refusals are `Recusa("invalido")`, answered before the write gate
# ---------------------------------------------------------------------------
def _objeto(corpo: Any) -> dict:
    if corpo is None:
        return {}
    if not isinstance(corpo, dict):
        raise acoes.Recusa("invalido", "O corpo deve ser um objeto JSON.")
    return corpo


def _solicitante(dados: dict) -> str:
    valor = dados.get("solicitante")
    texto = valor.strip() if isinstance(valor, str) else ""
    # Control characters would forge extra lines in the log this value is written to.
    if not texto or len(texto) > LIMITE_SOLICITANTE or any(ord(c) < 32 or ord(c) == 127 for c in texto):
        raise acoes.Recusa("invalido", MSG_SOLICITANTE)
    return texto


def _numero_em_texto(valor: Any, campo: str) -> str:
    """A DocNum as text for the service (which validates the digits with its own message).

    ``bool`` is refused explicitly: ``True`` is an ``int`` in Python and would become "1".
    """
    if isinstance(valor, bool) or not isinstance(valor, (int, str)):
        raise acoes.Recusa("invalido", f"'{campo}' deve conter números (DocNum): recebido {valor!r}.")
    return str(valor).strip()


def _lista_de_ops(dados: dict, *, obrigatoria: bool = True) -> list[str]:
    valor = dados.get("ops")
    if valor is None and not obrigatoria:
        return []
    if not isinstance(valor, list):
        if obrigatoria and valor is None:
            raise acoes.Recusa("invalido", "Nenhuma OP selecionada.")
        raise acoes.Recusa("invalido", "'ops' deve ser uma lista de números de OP (DocNum).")
    if not valor and obrigatoria:
        raise acoes.Recusa("invalido", "Nenhuma OP selecionada.")
    return [_numero_em_texto(v, "ops") for v in valor]


def _texto_obrigatorio(dados: dict, campo: str, mensagem: str) -> str:
    valor = dados.get(campo)
    if not isinstance(valor, str) or not valor.strip():
        raise acoes.Recusa("invalido", mensagem)
    return valor.strip()


# ---------------------------------------------------------------------------
# Writing the answer
# ---------------------------------------------------------------------------
def _numero(valor: Any) -> float | int | None:
    if valor is None:
        return None
    if isinstance(valor, Decimal):
        valor = float(valor)
    if isinstance(valor, float) and valor.is_integer():
        return int(valor)
    return valor


def _data(valor: Any) -> str | None:
    if valor is None or valor == "":
        return None
    if isinstance(valor, datetime):
        return valor.date().isoformat()
    if isinstance(valor, date):
        return valor.isoformat()
    return str(valor)[:10]


def _op_da_busca(linha: dict) -> dict:
    """One row of `service.buscar_ops` (the screen's grid) in the API's words."""
    status = str(linha.get("Status") or "")
    planejada = float(linha.get("Qtde. Planejada") or 0)
    apontada = float(linha.get("Qtde. Apontada") or 0)
    return {
        "op": _numero(linha.get("Número OP")),
        "status": status,
        "status_desc": service.STATUS_OP.get(status, status),
        "item": linha.get("Cód. Produto"),
        "produto": linha.get("Produto"),
        "planejada": _numero(planejada),
        "apontada": _numero(apontada),
        "restante": _numero(linha.get("Qtde. Restante")),
        "data_pedido": _data(linha.get("Data Pedido")),
        "data_inicio": _data(linha.get("Data inicio")),
        "data_vencimento": _data(linha.get("Data Vencimento")),
        "cliente_codigo": linha.get("Cod. Cliente"),
        "cliente": linha.get("Cliente"),
        "acoes_possiveis": service.acoes_possiveis(status, planejada, apontada),
    }


def _item_do_plano(item: dict) -> dict:
    return {
        "ordem": item["#"],
        "op": _numero(item["OP"]),
        "doc_entry": item["_doc_entry"],
        "item": item["Item"],
        "planejada": _numero(item["_planejada"]),
        "apontada": _numero(item["_apontada"]),
        "status": item["_status"],
        "status_atual": item["Status atual"],
        "acao": item["Ação"],
        "processar": item["_processar"],
    }


def _plano(plano: Plano) -> dict:
    itens = [_item_do_plano(i) for i in plano.itens]
    processar = [i for i in itens if i["processar"]]
    return {
        "token": plano.token,
        "operacao": plano.operacao,
        "valido_ate": plano.para_json()["valido_ate"],
        "resumo": {
            "a_encerrar": len(processar),
            "a_liberar_antes": sum(1 for i in processar if i["status"] != "R"),
            "listadas": len(itens),
        },
        "itens": itens,
    }


def _dispara(request: Request, nome: str, descricao: str, corrotina, solicitante: str) -> JSONResponse:
    try:
        tarefa = acoes.dispara(
            nome, descricao, corrotina, solicitante=solicitante, origem=ORIGEM_API, ip=_ip(request)
        )
    except acoes.Recusa as recusa:
        return _de_recusa(recusa)
    return JSONResponse({"ok": True, "execucao": _execucao(tarefa)}, status_code=202)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.get("/pedidos/{pedido}/ops")
def buscar_ops(
    pedido: str,
    op_de: str | None = None,
    op_ate: str | None = None,
    status_de: str | None = None,
    status_ate: str | None = None,
):
    """The OPs of one sales order (DocNum), with the screen's optional OP and status ranges.

    Plain ``def``: FastAPI runs it in its threadpool, so a slow HANA does not stall the
    event loop (the task polling, a running execution) — same as the screen's search.
    """
    leitor = _leitor()
    try:
        linhas = service.buscar_ops(
            leitor, pedido, op_de or None, op_ate or None, status_de or None, status_ate or None
        )
    except ValueError as exc:
        return _falha("invalido", str(exc), 400)
    except Exception as exc:  # noqa: BLE001 - a read that failed is a 502, never a 500
        return _sap_fora(f"busca das OPs do pedido {pedido!r}", exc)
    finally:
        leitor.close()

    ops = [_op_da_busca(linha) for linha in linhas]
    return {"ok": True, "pedido": int(pedido.strip()), "total": len(ops), "ops": ops}


@router.post("/liberar")
async def liberar(request: Request, corpo: Any = Body(default=None)):
    """Planejada → Liberada, written as soon as the execution starts (no plan, as on the screen)."""
    try:
        dados = _objeto(corpo)
        solicitante = _solicitante(dados)
        numeros = _lista_de_ops(dados)
    except acoes.Recusa as recusa:
        return _de_recusa(recusa)

    avisa_escrita("manutencao-op liberar (API)")

    leitor = _leitor()
    try:
        ops = await asyncio.to_thread(acoes.prepara_mudanca_status, leitor, numeros)
    except acoes.Recusa as recusa:
        return _de_recusa(recusa)
    except Exception as exc:  # noqa: BLE001 - see buscar_ops
        return _sap_fora("leitura das OPs a liberar", exc)
    finally:
        leitor.close()

    nome = acoes.NOME_LIBERAR
    return _dispara(
        request, nome, ", ".join(str(o["doc_num"]) for o in ops),
        acoes.corrotina_mudanca_status(nome, ops, "l"), solicitante,
    )


@router.post("/encerrar/conferir")
def conferir_encerrar(corpo: Any = Body(default=None)):
    """The checked plan of a closing (OPs or a whole order) and its 10-minute token. Reads only."""
    try:
        dados = _objeto(corpo)
        numeros = _lista_de_ops(dados, obrigatoria=False)
        pedido = dados.get("pedido")
        pedido = _numero_em_texto(pedido, "pedido") if pedido is not None else ""
    except acoes.Recusa as recusa:
        return _de_recusa(recusa)

    leitor = _leitor()
    try:
        plano = acoes.monta_plano_encerramento(leitor, numeros, pedido)
    except acoes.Recusa as recusa:
        return _de_recusa(recusa)
    except Exception as exc:  # noqa: BLE001 - see buscar_ops
        return _sap_fora("conferência do encerramento", exc)
    finally:
        leitor.close()
    return {"ok": True, "plano": _plano(plano)}


@router.post("/encerrar/executar")
async def executar_encerrar(request: Request, corpo: Any = Body(default=None)):
    """Runs a checked plan. IRREVERSIBLE: material issue + product receipt per OP."""
    try:
        dados = _objeto(corpo)
        solicitante = _solicitante(dados)
        token = _texto_obrigatorio(
            dados, "token", "Informe o 'token' devolvido por /encerrar/conferir."
        )
    except acoes.Recusa as recusa:
        return _de_recusa(recusa)

    # The gate before the token: a refused write must not spend it.
    avisa_escrita(f"{acoes.operacao_do_plano(token)} (API)")
    try:
        plano = acoes.consome_plano(token)
    except acoes.Recusa as recusa:
        return _de_recusa(recusa)
    return _dispara(
        request, plano.operacao, acoes.descricao_do_plano(plano),
        acoes.corrotina_encerramento(plano), solicitante,
    )


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
    """The same state the Execuções screen polls every 2 s, plus who asked."""
    tarefa, recusa = await _execucao_do_modulo(tarefa_id)
    if recusa is not None:
        return recusa
    return {"ok": True, "execucao": _execucao(tarefa)}


@router.post("/execucoes/{tarefa_id}/cancelar")
async def cancelar(request: Request, tarefa_id: str, corpo: Any = Body(default=None)):
    """Stops a running execution between steps. Does NOT undo what was already written."""
    try:
        solicitante = _solicitante(_objeto(corpo))
    except acoes.Recusa as recusa:
        return _de_recusa(recusa)

    tarefa, recusa = await _execucao_do_modulo(tarefa_id)
    if recusa is not None:
        return recusa
    cancelou = await TAREFAS.cancelar(tarefa.id)
    logger.warning(
        "Execução %s: interrupção pedida pela API · solicitante %s · ip %s · %s",
        tarefa.id, solicitante, _ip(request) or "—",
        "interrompida" if cancelou else "já tinha terminado",
    )
    return {"ok": True, "cancelada": cancelou}
