"""Camada: infrastructure.hana — views nativas do SAP HANA via hdbcli.

**Somente leitura.** Ver ai_spec/02_data_model.md §3 e ai_spec/03_architecture.md.
"""

from wbcpython.infrastructure.hana.identificadores import (
    IdentificadorInvalido,
    citar_identificador,
    validar_identificador,
)
from wbcpython.infrastructure.hana.models import EvolucaoOportunidade, MunicipioCliente
from wbcpython.infrastructure.hana.repository import (
    VIEW_EVOLUCAO,
    VIEW_MUNICIPIO,
    ConexaoHanaIndisponivel,
    RepositorioViewsHana,
    RepositorioViewsHanaSql,
)

__all__ = [
    "VIEW_EVOLUCAO",
    "VIEW_MUNICIPIO",
    "ConexaoHanaIndisponivel",
    "EvolucaoOportunidade",
    "IdentificadorInvalido",
    "MunicipioCliente",
    "RepositorioViewsHana",
    "RepositorioViewsHanaSql",
    "citar_identificador",
    "validar_identificador",
]
