"""Camada: dashboard — painel de acompanhamento (HTML + HTMX).

Lê **apenas** o banco de tracking e, na aba "Próximo ciclo", um retrato em
disco gerado por `wbcpython pendentes --exportar`. Nunca o SAP nem o WBC: uma
tela de leitura não pode virar carga nos sistemas de origem, e abrir o painel
não pode custar uma varredura no HANA a cada recarga de página.
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
    registrar_reprocessamento,
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
    "registrar_reprocessamento",
    "resumo_de_execucao",
]
