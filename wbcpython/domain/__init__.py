"""Domain layer: pure business rules, no I/O.

The original `ai_spec/01_business_rules.md` no longer exists; `docs/wbc/ai_spec/00_index.md` says where
each of its subjects lives now.
"""

from wbcpython.domain.mapeamento import montar_payload_orcdetalhe
from wbcpython.domain.numeros import (
    NumeroInvalido,
    formatar_para_sap,
    normalizar_decimal,
    normalizar_decimal_tolerante,
)
from wbcpython.domain.revisao import (
    RevisaoInvalida,
    ordem_revisao,
    revisao_wbc_e_mais_nova,
)
from wbcpython.domain.sitcode import (
    Acao,
    Decisao,
    EstadoIntegracao,
    decidir,
)

__all__ = [
    "Acao",
    "Decisao",
    "EstadoIntegracao",
    "NumeroInvalido",
    "RevisaoInvalida",
    "decidir",
    "formatar_para_sap",
    "montar_payload_orcdetalhe",
    "normalizar_decimal",
    "normalizar_decimal_tolerante",
    "ordem_revisao",
    "revisao_wbc_e_mais_nova",
]
