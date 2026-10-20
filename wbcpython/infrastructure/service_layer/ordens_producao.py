"""Production orders of a sales order, over the Service Layer — the worker's side.

Only what the partner swap needs (`docs/PLANO_TROCA_PN_COM_OP.md`): list the live OPs of
an order and cancel a planned one. Before this, the swap cancelled the order and left its
OPs planned: on 20/10/2026, 110 OPs on the cancelled 84505 and 84506 while the
replacements got their own.

The link is `ProductionOrderOriginEntry` (`OWOR.OriginAbs`), the same one the window read
counts by (`infrastructure/hana/oportunidades.py`). Measured on 20/10/2026: no live OP of
another origin points at a sales order, and `OriginNum` adds none.

Cancelling is the same PATCH the Controle de Produção sends
(`controleproducao/.../service.py::_cancela_op`), the one that cancelled those 110 OPs.
The write goes through `ServiceLayerClient`, so it is blocked off the .11 like every
other write of the worker (`wbcpython/safety.py`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wbcpython.infrastructure.service_layer.client import ServiceLayerClient

PLANEJADA = "boposPlanned"
CANCELADA = "boposCancelled"


@dataclass(frozen=True, slots=True)
class OpViva:
    """A production order that is not cancelled."""

    doc_entry: int
    doc_num: int
    status: str

    @property
    def planejada(self) -> bool:
        return self.status == PLANEJADA


class RepositorioOrdensProducaoServiceLayer:
    def __init__(self, cliente: ServiceLayerClient) -> None:
        self._cliente = cliente

    def vivas(self, pedido_doc_entry: int) -> list[OpViva]:
        """Every non-cancelled OP of the order, read now (paged: the SL returns 20 a page)."""
        registros = self._cliente.listar(
            "ProductionOrders",
            filtro=(
                f"ProductionOrderOriginEntry eq {int(pedido_doc_entry)} "
                f"and ProductionOrderStatus ne '{CANCELADA}'"
            ),
            ordenar_por="AbsoluteEntry",
            selecionar="AbsoluteEntry,DocumentNumber,ProductionOrderStatus",
        )
        return [
            OpViva(
                doc_entry=int(r["AbsoluteEntry"]),
                doc_num=int(r["DocumentNumber"]),
                status=str(r.get("ProductionOrderStatus") or ""),
            )
            for r in registros
        ]

    def cancelar(self, op_doc_entry: int) -> None:
        self._cliente.patch(
            f"ProductionOrders({int(op_doc_entry)})",
            json={"ProductionOrderStatus": CANCELADA},
        )
