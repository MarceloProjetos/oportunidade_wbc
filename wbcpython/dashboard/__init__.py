"""Layer: dashboard — the tracking panel (HTML + HTMX).

The pages **only read** the tracking database and, on the "Próximo ciclo" tab, a snapshot on disk
written by `wbcpython pendentes --exportar`. They never query SAP or WBC: a read-only screen must not
become load on the source systems, and opening the panel must not cost a HANA scan on every page
reload. Two things sit beside that rule: the operator-name list comes from the API 8077 on localhost
(`dashboard/usuarios.py`), and the "Executar" tab starts CLI commands in a subprocess
(`dashboard/comandos.py`) — those, not the page, may touch SAP.

Layer map: `docs/wbc/README.md` ("Estrutura"); the old `ai_spec/03_architecture.md` is gone (see
`docs/wbc/ai_spec/00_index.md`).
"""

from wbcpython.dashboard.dados import (
    RECORTE_PADRAO,
    RECORTES,
    Kpis,
    Recorte,
    aplicar,
    calcular_kpis,
    linha_para_tabela,
    recorte,
    resumo_de_execucao,
)
from wbcpython.dashboard.previsao import (
    calcular_kpis as calcular_kpis_da_previsao,
)
from wbcpython.dashboard.previsao import (
    carregar as carregar_previsao,
)

__all__ = [
    "RECORTES",
    "RECORTE_PADRAO",
    "Kpis",
    "Recorte",
    "aplicar",
    "calcular_kpis",
    "calcular_kpis_da_previsao",
    "carregar_previsao",
    "linha_para_tabela",
    "recorte",
    "resumo_de_execucao",
]
