"""Camada: infrastructure.service_layer — cliente REST do SAP Service Layer.

Ver ai_spec/03_architecture.md (mapeamento DI-API → Service Layer).
"""

from wbcpython.infrastructure.service_layer.client import ServiceLayerClient
from wbcpython.infrastructure.service_layer.documentos import (
    RepositorioDocumentosVenda,
    RepositorioDocumentosVendaServiceLayer,
    TipoDocumento,
)
from wbcpython.infrastructure.service_layer.errors import (
    ServiceLayerError,
    ServiceLayerLoginError,
)
from wbcpython.infrastructure.service_layer.oportunidades import (
    RepositorioOportunidades,
    RepositorioOportunidadesServiceLayer,
)
from wbcpython.infrastructure.service_layer.orcdetalhe import (
    RepositorioOrcDetalhe,
    RepositorioOrcDetalheServiceLayer,
)

__all__ = [
    "RepositorioDocumentosVenda",
    "RepositorioDocumentosVendaServiceLayer",
    "RepositorioOportunidades",
    "RepositorioOportunidadesServiceLayer",
    "RepositorioOrcDetalhe",
    "RepositorioOrcDetalheServiceLayer",
    "ServiceLayerClient",
    "ServiceLayerError",
    "ServiceLayerLoginError",
    "TipoDocumento",
]
