"""Layer: infrastructure.service_layer — REST client for the SAP Service Layer.

See `docs/wbc/ai_spec/00_index.md` (architecture and DI-API → Service Layer mapping).
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
