"""Business partners (`BusinessPartners`) — only what the integration needs.

Exists for one reason: a partner swap **cancels the order before creating the new one** (see
`cancelar_e_recriar`, where the order is deliberate). If the corrected partner does not exist
in SAP, creation fails and the quotation is left with no order at all — worse than the starting
state, and destructive.

The legacy had the same guard, ahead of everything::

    if (ServiceProcess.ChecaPN(oComp, PNNew))          // SELECT count(*) FROM OCRD
        if (!ServiceProcess.ChecaPNPedido(...))        // order already on the new PN?
            CancelaPedido(...) → CriaPedido(..., PNNew, ...)
"""

from __future__ import annotations

import logging

from wbcpython.infrastructure.service_layer.client import ServiceLayerClient
from wbcpython.infrastructure.service_layer.errors import ServiceLayerError

logger = logging.getLogger(__name__)

ENTIDADE = "BusinessPartners"


class RepositorioParceirosServiceLayer:
    """Partner existence check. Read-only."""

    def __init__(self, cliente: ServiceLayerClient) -> None:
        self._cliente = cliente
        self._cache: dict[str, bool] = {}

    def existe(self, card_code: str) -> bool:
        """Does the partner exist in SAP?

        Memoized for the lifetime of the repository (one cycle): in a batch of partner corrections the
        same `CardCode` tends to repeat, and each lookup is a network round trip.
        """
        if not card_code:
            return False
        if card_code in self._cache:
            return self._cache[card_code]

        try:
            self._cliente.get_json(f"{ENTIDADE}('{card_code}')")
        except ServiceLayerError as erro:
            # 404 é resposta, não falha: significa "não existe". Qualquer outro
            # erro é problema de verdade e deve subir.
            if erro.status_code != 404:
                raise
            self._cache[card_code] = False
        else:
            self._cache[card_code] = True

        return self._cache[card_code]
