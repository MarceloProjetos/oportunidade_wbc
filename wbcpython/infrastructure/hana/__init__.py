"""Infrastructure: native SAP HANA views and opportunity reads through hdbcli. **Read-only.**"""

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
