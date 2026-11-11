"""Closed orders whose future-delivery sale has not shipped yet.

SAP closes the order (``ORDR.DocStatus = 'C'``) at the "simples faturamento" NF (CFOP 5922/6922),
issued before the goods leave; they leave later with the remessa NF. Such an order is still
production work: "Pedidos novos" must offer it to Processar (84537 and 84584 in 11/2026 were
invoiced before anyone processed them), and "Pedidos fechados com OPs abertas" must never offer
its OPs to Encerrar.

The rule is the .11's, measured in ``docs/PLANO_ENTREGA_FUTURA.md`` and kept in
``faturamento_pedido`` (root): this module only runs it over the CP's own HANA reader, so the
two never disagree. Read-only.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from controleproducao.core.hana_reader import HanaDirectReader


class _ComoDataFrame:
    """``execute_query`` returning a DataFrame — the interface ``faturamento_pedido`` reads."""

    def __init__(self, leitor: HanaDirectReader) -> None:
        self._leitor = leitor

    def execute_query(self, query: str, params: Any = None) -> Any:
        import pandas as pd

        return pd.DataFrame(self._leitor.fetch_all(query, tuple(params or ())))


def pendentes(leitor: HanaDirectReader, doc_entries: Iterable[int]) -> set[int] | None:
    """The ``DocEntry``s, among ``doc_entries``, closed by a future-delivery NF whose remessa is
    still ``nenhuma``/``parcial``. ``None`` when the invoicing could not be read — the caller
    decides what "unknown" means for its action; it is never "not future delivery"."""
    entries = sorted({int(e) for e in doc_entries})
    if not entries:
        return set()
    from faturamento_pedido import ler_faturamento  # lazy: root module, only when needed

    # Empty schema: the reader already ran `SET SCHEMA` on its connection.
    faturamento = ler_faturamento(_ComoDataFrame(leitor), "", entries)
    if faturamento is None:
        return None
    return {entry for entry, f in faturamento.items() if f.pendente}
