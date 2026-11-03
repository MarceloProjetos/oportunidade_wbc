"""pedidos_bloqueados -- the hardcoded blocklist of sales orders that do not count.

An order listed here is treated as if it did not exist: it does not show up on a
screen, it is not added to any total, and it does not propagate to the Supabase
aggregates or to the app. The rule is deliberately **hardcoded** (Marcelo's call,
2026-09-21) and not a flag in the ``.env``: it is a correction of specific
documents in production, not a feature that gets switched on and off.

Why ``84337`` (NAVARRO, DocEntry 19696, R$ 93.531,74, 2026-09-02) is here: the order
is CLOSED in SAP (``DocStatus='C'``) with **no delivery and no invoice**
(``RDR1.TargetType = -1``, nothing in ``DLN1``/``INV1``) -- it was closed by hand. It
still weighed in the month's Orders total, in the "Mês atual" card and in the
customer ranking on the phone, where it read as a sale that never happened.

**Where this is applied on this machine:**

* ``extract_vendas_bi.py`` -- both ``VW_PEDIDO_ALTA`` queries (monthly series and
  recent detail), which feed ``bi_vendas_kpi``, ``bi_vendas_ranking`` and
  ``bi_vendas_serie_mensal``. Since the load **prunes** whatever it did not rewrite
  itself, the old ranking row disappears on the next run;
* ``maintenance/conferir_vendas_bi.py`` -- the checker reads the SAME view; without
  the same cut it would report a divergence against a Supabase that is right;
* ``situacao_pedidos.normalizar`` -- the twin of the web (see the warning there).

⚠️ **There is a copy of this list in the web repo**
(``web_orcaview_V118/backend/services/pedidos_bloqueados.py``) and another in the app
(``mobile_orcaview_V4/lib/pedidos/bloqueados.ts``). Different repos reading different
sources (HANA directly here, Supabase on the phone); adding an order means touching
all three.
"""
from __future__ import annotations

from typing import Any, Iterable

__all__ = [
    "PEDIDOS_BLOQUEADOS_DOCNUM",
    "docnum_bloqueado",
    "sql_nao_bloqueado",
]

#: Os pedidos bloqueados, pelo número que aparece na tela (``ORDR.DocNum``, que
#: na ``VW_PEDIDO_ALTA`` se chama ``DOC``).
PEDIDOS_BLOQUEADOS_DOCNUM: frozenset[int] = frozenset({84337})


def _como_int(valor: Any) -> int | None:
    """``'84337'``/``84337`` -> ``84337``; anything else -> ``None``.

    The comparison tolerates the int and the text spelling (HANA hands a UDF declared
    as text back as a number, and the mirror turns ``doc_num`` into TEXT). A float is
    NOT accepted: ``84337.0`` -> ``None``, because ``int('84337.0')`` raises.
    """
    if valor is None or isinstance(valor, bool):
        return None
    try:
        return int(str(valor).strip())
    except (TypeError, ValueError):
        return None


def docnum_bloqueado(valor: Any) -> bool:
    """``True`` when this ``DocNum`` is on the blocklist."""
    return _como_int(valor) in PEDIDOS_BLOQUEADOS_DOCNUM


def _lista(valores: Iterable[int]) -> str:
    return ", ".join(str(int(v)) for v in sorted(valores))


def sql_nao_bloqueado(coluna: str, *, prefixo: str = " AND ") -> str:
    """SQL fragment that drops the blocked orders, with ``prefixo`` (default ``AND``) in front.

    Interpolating is safe here and only here: the values are ints from a module-level
    constant, never user input. ``coluna`` arrives already quoted from the caller
    (``"DOC"`` on the view, ``p."DOC"`` with an alias). Returns ``''`` with an empty
    list, so emptying the blocklist does not leave a broken ``WHERE`` behind.
    """
    if not PEDIDOS_BLOQUEADOS_DOCNUM:
        return ""
    return f'{prefixo}{coluna} NOT IN ({_lista(PEDIDOS_BLOQUEADOS_DOCNUM)})'
