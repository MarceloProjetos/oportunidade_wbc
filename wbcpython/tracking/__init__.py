"""Layer `tracking`: the integration's own tracking database.

The only database the solution **writes** to, and the only source the dashboard queries
(see `docs/wbc/ai_spec/00_index.md`).
"""

from wbcpython.tracking.modelos import (
    Acompanhamento,
    Base,
    Evento,
    Execucao,
    PedidoDeJanela,
    StatusExecucao,
    StatusIntegracao,
    TipoEvento,
    Trava,
)
from wbcpython.tracking.repositorio import (
    LINHA_UNICA,
    TRAVA_WORKER,
    RepositorioTracking,
    TravaNaoObtida,
)

__all__ = [
    "LINHA_UNICA",
    "TRAVA_WORKER",
    "Acompanhamento",
    "Base",
    "Evento",
    "Execucao",
    "PedidoDeJanela",
    "RepositorioTracking",
    "StatusExecucao",
    "StatusIntegracao",
    "TipoEvento",
    "Trava",
    "TravaNaoObtida",
]
