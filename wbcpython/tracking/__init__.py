"""Camada: tracking — banco próprio de acompanhamento da integração.

É o único banco em que a solução **escreve**, e é a única fonte que o dashboard
consulta. Ver ai_spec/03_architecture.md.
"""

from wbcpython.tracking.modelos import (
    Acompanhamento,
    Base,
    Evento,
    Execucao,
    StatusExecucao,
    StatusIntegracao,
    TipoEvento,
    Trava,
)
from wbcpython.tracking.repositorio import (
    TRAVA_WORKER,
    RepositorioTracking,
    TravaNaoObtida,
)

__all__ = [
    "TRAVA_WORKER",
    "Acompanhamento",
    "Base",
    "Evento",
    "Execucao",
    "RepositorioTracking",
    "StatusExecucao",
    "StatusIntegracao",
    "TipoEvento",
    "Trava",
    "TravaNaoObtida",
]
