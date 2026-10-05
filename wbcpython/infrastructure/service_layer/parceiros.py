"""Parceiros de negócio (`BusinessPartners`) — só o que a integração precisa.

Existe por um motivo específico: a troca de parceiro **cancela o pedido antes
de criar o novo** (ver `cancelar_e_recriar`, onde a ordem é deliberada). Se o
parceiro corrigido não existir no SAP, a criação falha e o orçamento fica sem
pedido nenhum — um estado pior que o inicial, e destrutivo.

O legado tinha a mesma proteção, e vinha antes de tudo:

    if (ServiceProcess.ChecaPN(oComp, PNNew))          // SELECT count(*) FROM OCRD
        if (!ServiceProcess.ChecaPNPedido(...))        // pedido já está no PN novo?
            CancelaPedido(...) → CriaPedido(..., PNNew, ...)
"""

from __future__ import annotations

import logging

from wbcpython.infrastructure.service_layer.client import ServiceLayerClient
from wbcpython.infrastructure.service_layer.errors import ServiceLayerError

logger = logging.getLogger(__name__)

ENTIDADE = "BusinessPartners"


class RepositorioParceirosServiceLayer:
    """Consulta de existência de parceiro. Somente leitura."""

    def __init__(self, cliente: ServiceLayerClient) -> None:
        self._cliente = cliente
        self._cache: dict[str, bool] = {}

    def existe(self, card_code: str) -> bool:
        """O parceiro existe no SAP?

        O resultado é memorizado no ciclo: numa leva de correções de parceiro,
        o mesmo `CardCode` costuma repetir, e cada consulta é uma ida à rede.
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
