"""pedidos_compra_hana — purchase-order lines from ``VW_ALTA_PEDIDOS_COMPRA_ITENS`` (read only).

Serves ``GET /compras/pedidos-itens`` and the MCP tool ``pedidos_compra_itens`` (scope
``compras:ler``). Same shape as :mod:`situacao_pedidos_hana`: one HANA connection per read,
a 120 s cache shared by every caller, filters applied in Python over the cached snapshot,
and :class:`situacao_pedidos_hana.SAPIndisponivel` for "HANA down" (the route turns it into 503).

The view has no price columns; it carries one row per purchase-order line (measured
22/10/2026: 1397 rows, 774 orders, all of 2026, one branch). Contract: ``API_PEDIDOS_COMPRA.md``.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import date
from typing import Any

from situacao_pedidos import ValidationError
from situacao_pedidos_hana import (
    _conectar,
    _data_iso,
    _linhas,
    _num,
    _schema,
)

logger = logging.getLogger(__name__)

__all__ = ["CACHE_TTL_SEGUNDOS", "VIEW", "fetch_itens", "filtrar", "idade_do_cache_s",
           "limpar_cache", "normalizar"]

CACHE_TTL_SEGUNDOS = 120
VIEW = "VW_ALTA_PEDIDOS_COMPRA_ITENS"
#: Fail loudly if the view ever stops being a short list (it has no date filter of its own).
_MAX_LINHAS = 50_000

#: View column → published field. Explicit on purpose: the contract is the right side.
CAMPOS = {
    "DocEntry": "doc_entry", "DocNum": "doc_num", "Filial": "filial",
    "DataPedido": "data_pedido", "DataEntrega": "data_entrega",
    "CodFornecedor": "cod_fornecedor", "Fornecedor": "fornecedor",
    "CodItem": "cod_item", "Descricao": "descricao", "Grupo": "grupo",
    "StatusPedido": "status_pedido", "StatusItem": "status_item",
    "QtdPedida": "qtd_pedida", "QtdEmAberto": "qtd_em_aberto",
}
_DATAS = ("DataPedido", "DataEntrega")
STATUS = ("aberto", "fechado", "todos")

_cache_lock = threading.Lock()
_cache: tuple[float, list[dict[str, Any]]] | None = None


def limpar_cache() -> None:
    global _cache
    with _cache_lock:
        _cache = None


def idade_do_cache_s() -> float | None:
    with _cache_lock:
        return None if not _cache else round(time.monotonic() - _cache[0], 1)


def normalizar(r: dict[str, Any]) -> dict[str, Any]:
    """Raw view row → published item (ISO dates, ``Decimal`` → float)."""
    return {pub: (_data_iso(r.get(col)) if col in _DATAS else _num(r.get(col)))
            for col, pub in CAMPOS.items()}


def _buscar_no_hana() -> list[dict[str, Any]]:
    schema = _schema()
    colunas = ", ".join(f'"{c}"' for c in CAMPOS)
    conn = _conectar()
    try:
        cont = _linhas(conn, f'SELECT COUNT(*) AS "N" FROM "{schema}"."{VIEW}"')
        n = int(cont[0]["N"]) if cont else 0
        if n > _MAX_LINHAS:
            raise ValidationError(f"{VIEW} devolveu {n} linhas (teto {_MAX_LINHAS}); avise o suporte.")
        linhas = _linhas(conn, f'SELECT {colunas} FROM "{schema}"."{VIEW}" '
                               'ORDER BY "DataEntrega", "DocNum", "CodItem"')
    finally:
        try:
            conn.close()
        except Exception as e:  # connection already dead
            logger.debug("Falha ao fechar conexao HANA (ignorada): %s", e)
    return [normalizar(r) for r in linhas]


def fetch_itens(*, recarregar: bool = False) -> list[dict[str, Any]]:
    """Every line of the view, normalized, through the cache. Callers must not mutate the dicts.

    Raises:
        SAPIndisponivel: HANA down, missing credentials or failed query.
        ValidationError: volume above ``_MAX_LINHAS``.
    """
    global _cache
    if not recarregar:
        with _cache_lock:
            if _cache and (time.monotonic() - _cache[0]) < CACHE_TTL_SEGUNDOS:
                return list(_cache[1])
    itens = _buscar_no_hana()
    with _cache_lock:
        _cache = (time.monotonic(), itens)
    return list(itens)


def _data_param(valor: str | None, nome: str) -> str | None:
    if not valor:
        return None
    try:
        return date.fromisoformat(valor.strip()).isoformat()
    except ValueError:
        raise ValidationError(f"{nome} deve ser AAAA-MM-DD") from None


def filtrar(itens: list[dict[str, Any]], *, pedido: str | None = None,
            fornecedor: str | None = None, item: str | None = None, busca: str | None = None,
            status: str | None = None, entrega_de: str | None = None,
            entrega_ate: str | None = None) -> list[dict[str, Any]]:
    """Apply the query filters; every one is optional and they combine with AND.

    ``status`` looks at the LINE (``status_item``): an open order can have closed lines
    (12 of them on 22/10/2026), and "what is still to arrive" is a per-line question.
    """
    st = (status or "todos").strip().lower()
    if st not in STATUS:
        raise ValidationError("status deve ser aberto, fechado ou todos")
    num = None
    if pedido:
        try:
            num = int(pedido)
        except ValueError:
            raise ValidationError("pedido deve ser o numero do pedido de compra (DocNum)") from None
    de = _data_param(entrega_de, "entrega_de")
    ate = _data_param(entrega_ate, "entrega_ate")
    forn = (fornecedor or "").strip().casefold()
    cod = (item or "").strip().casefold()
    termo = (busca or "").strip().casefold()

    def casa(i: dict[str, Any]) -> bool:
        if num is not None and i["doc_num"] != num:
            return False
        if forn and str(i["cod_fornecedor"] or "").casefold() != forn:
            return False
        if cod and str(i["cod_item"] or "").casefold() != cod:
            return False
        if st != "todos" and str(i["status_item"] or "").casefold() != st:
            return False
        if de and (not i["data_entrega"] or i["data_entrega"] < de):
            return False
        if ate and (not i["data_entrega"] or i["data_entrega"] > ate):
            return False
        if termo:
            texto = " ".join(str(i[k] or "") for k in ("fornecedor", "descricao", "cod_item", "grupo"))
            if termo not in texto.casefold():
                return False
        return True

    return [i for i in itens if casa(i)]
