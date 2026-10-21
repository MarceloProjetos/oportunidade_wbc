"""controle_montagem: the order's assembly control as the PCP reads it (web PLANO_CONTROLE_MONTAGEM F6b).

Pure rules here; the HTTP edge is in ``test_api_situacao_pedidos.py``.
"""
from __future__ import annotations

from datetime import date
from typing import Any

import controle_montagem as cm

LINHA = {"doc_entry": 20672, "doc_num": 84507, "inicio_montagem": "2026-11-03", "fim_montagem": "2026-11-05",
         "obs_processo": "Equipe de 3", "atualizado_em": "2026-10-21T14:45:20+00:00"}


class _Cliente:
    """Minimal supabase-py double: records the chain and returns ``dados``."""

    def __init__(self, dados: list[dict[str, Any]]) -> None:
        self.dados = dados
        self.filtros: list[tuple] = []

    def table(self, nome: str) -> _Cliente:
        self.filtros.append(("table", nome))
        return self

    def select(self, cols: str) -> _Cliente:
        self.filtros.append(("select", cols))
        return self

    def eq(self, col: str, val: Any) -> _Cliente:
        self.filtros.append(("eq", col, val))
        return self

    def limit(self, n: int) -> _Cliente:
        return self

    def execute(self) -> Any:
        return type("R", (), {"data": self.dados})()


def test_ler_linha_e_sem_linha() -> None:
    c = _Cliente([LINHA])
    assert cm.ler(c, 20672) == LINHA
    assert ("table", "pedidos_montagem") in c.filtros and ("eq", "doc_entry", 20672) in c.filtros
    assert cm.ler(_Cliente([]), 20672) == {}


def test_conclusao_igual_a_da_web() -> None:
    obs = ("Prazo de pagamento do pedido: 30 dias (informado por x em 01/10/2026) · "
           "Montagem concluída em 20/11/2026 (informada por y em 21/11/2026)")
    assert cm.conclusao_da_obs(obs) == date(2026, 11, 20)
    assert cm.conclusao_da_obs("nada") is None and cm.conclusao_da_obs(None) is None


def test_previsao_so_da_referencia_exata() -> None:
    rows = [{"U_OrigRef": "CTR:7:OV:120672", "Remark": "Montagem concluída em 01/01/2026"},
            {"U_OrigRef": "CTR:7:OV:20672", "Remark": "Montagem concluída em 20/11/2026"}]
    assert cm.previsao_das_linhas(rows, 20672) == {"tem_previsao": True, "conclusao": "2026-11-20"}
    assert cm.previsao_das_linhas(rows[:1], 20672) == {"tem_previsao": False, "conclusao": None}


def test_bloco() -> None:
    assert cm.bloco(None) is None                                   # base down: unknown, never "none"
    assert cm.bloco({}) == {"inicio_montagem": None, "fim_montagem": None, "fim_fonte": "orcaview",
                            "obs_processo": None, "atualizado_em": None}
    b = cm.bloco(LINHA)
    assert b["fim_montagem"] == "2026-11-05" and b["fim_fonte"] == "orcaview" and "doc_entry" not in b
    b = cm.bloco(LINHA, {"tem_previsao": True, "conclusao": "2026-11-20"})
    assert b["fim_montagem"] == "2026-11-20" and b["fim_fonte"] == "previsao"
    b = cm.bloco(LINHA, {"tem_previsao": True, "conclusao": None})     # contract, not finished yet
    assert b["fim_montagem"] is None and b["fim_fonte"] == "previsao"
