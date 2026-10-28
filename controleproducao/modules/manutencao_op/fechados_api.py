"""JSON API of the "Pedidos fechados com OPs abertas" card (28/10/2026).

Contract: ``API_MANUTENCAO_OP.md`` (section "Pedidos fechados com OPs abertas"). Same rules as
the module's API (``api_router.py``), whose helpers this file reuses without changing them:
``X-API-Key`` (scope ``manutencao_op``), ``solicitante`` required on the write, every error as
``{"ok": false, "tipo", "motivo"}``, the execution followed at ``/api/manutencao-op/execucoes/{id}``.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Request

from controleproducao.core.confirmacao import Plano
from controleproducao.core.recusa import Recusa
from controleproducao.core.web import avisa_escrita
from controleproducao.modules.manutencao_op import api_router as api
from controleproducao.modules.manutencao_op import fechados

router = APIRouter(prefix=f"{api.PREFIXO}/pedidos-fechados", tags=["api_manutencao_op"])


def _verdadeiro(valor: Any) -> bool:
    if isinstance(valor, bool):
        return valor
    return str(valor or "").strip().lower() in ("1", "true", "sim", "on")


def _plano(plano: Plano) -> dict:
    return {
        "token": plano.token,
        "operacao": plano.operacao,
        "valido_ate": plano.para_json()["valido_ate"],
        "resumo": {
            "pedidos": len(plano.itens),
            "ops_abertas": sum(int(i["OPs abertas"]) for i in plano.itens),
            "custo": plano.itens[0]["_custo"] if plano.itens else None,
            "ativar_inativos": bool(plano.itens[0]["_ativar_inativos"]) if plano.itens else False,
        },
        "itens": [
            {"ordem": i["#"], "pedido": i["_pedido"], "cliente": i["Cliente"],
             "entrega": None if i["Entrega"] == "—" else i["Entrega"],
             "ops_abertas": i["OPs abertas"], "planejadas": i["Planejadas (serão liberadas)"]}
            for i in plano.itens
        ],
    }


@router.get("")
def listar(incluir_atrasados: str = ""):
    """The closed orders that still have Planejada/Liberada OPs. Reads only."""
    incluir = _verdadeiro(incluir_atrasados)
    leitor = api._leitor()
    try:
        pedidos = fechados.pedidos_fechados_com_op(leitor, incluir)
    except Exception as exc:  # noqa: BLE001 - the SAP being down is an answer, not a 500
        return api._sap_fora("consulta de pedidos fechados", exc)
    finally:
        leitor.close()
    return {"ok": True, "incluir_atrasados": incluir, "total_pedidos": len(pedidos),
            "total_ops_abertas": sum(p["ops_abertas"] for p in pedidos), "pedidos": pedidos}


@router.post("/encerrar/conferir")
def conferir(corpo: Any = Body(default=None)):
    """The checked plan of the marked orders and its single-use token. Reads only.

    Body: ``{"pedidos": [84104, ...], "custo": 0.01 (optional), "ativar_inativos": false,
    "incluir_atrasados": false}``.
    """
    try:
        dados = api._objeto(corpo)
        pedidos = fechados.le_pedidos(dados.get("pedidos"))
        custo = fechados.le_custo(dados.get("custo"))
    except Recusa as recusa:
        return api._de_recusa(recusa)

    leitor = api._leitor()
    try:
        plano = fechados.monta_plano(
            leitor, pedidos, custo, _verdadeiro(dados.get("ativar_inativos")),
            _verdadeiro(dados.get("incluir_atrasados")),
        )
    except Recusa as recusa:
        return api._de_recusa(recusa)
    except Exception as exc:  # noqa: BLE001 - see listar
        return api._sap_fora("conferência dos pedidos fechados", exc)
    finally:
        leitor.close()
    return {"ok": True, "plano": _plano(plano)}


@router.post("/encerrar/executar")
async def executar(request: Request, corpo: Any = Body(default=None)):
    """Runs a checked plan. IRREVERSIBLE: material issue + product receipt per OP."""
    try:
        dados = api._objeto(corpo)
        solicitante = api._solicitante(dados)
        token = api._texto_obrigatorio(
            dados, "token", "Informe o 'token' devolvido por /pedidos-fechados/encerrar/conferir."
        )
    except Recusa as recusa:
        return api._de_recusa(recusa)

    # The gate before the token: a refused write must not spend it.
    avisa_escrita(f"{fechados.operacao_do_plano(token)} (API)")
    try:
        plano = fechados.consome_plano(token)
    except Recusa as recusa:
        return api._de_recusa(recusa)
    return api._dispara(
        request, plano.operacao, fechados.descricao_do_plano(plano),
        fechados.corrotina(plano), solicitante, parada_combinada=True,
    )
