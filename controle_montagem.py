"""controle_montagem — the order's "Controle de montagem" for the PCP (web PLANO_CONTROLE_MONTAGEM F6b).

Comercial types the assembly start, end and process notes on the OrçaView order screen (.90); they
live only in the Supabase table ``pedidos_montagem`` (one row per order, PK ``doc_entry``, service_role
only). This module reads that row for the order-status routes (``GET /pedidos/<n>/situacao``), so the
PCP's app sees the same values as the screen. Read only: nothing here writes Supabase or SAP.

D3 of the plan: on a type-2 order (assembly paid by Altamira) with an assembly forecast in Previsão
Financeira (``@ALTA_PREV``, ref ``CTR:<contrato>:OV:<DocEntry>``), the end date IS the forecast's
"Montagem concluída em" (it decides when the assembler is paid). Same rule as the web
(``services/previsoes/contrato/montagem.ler_conclusao``), ported here; ``fim_fonte`` says which one won.
"""
from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["COLUNAS", "TABELA", "TIPO_PREVISAO", "bloco", "conclusao_da_obs", "ler", "previsao_das_linhas"]

TABELA = "pedidos_montagem"
COLUNAS = "doc_entry,doc_num,inicio_montagem,fim_montagem,obs_processo,atualizado_em"
TIPO_PREVISAO = "2"
#: Separator and marker of the Previsão remark -- byte-identical to the web's montagem.py.
_SEP = " · "
_CONCL = re.compile(r"Montagem concluída em (\d{2})/(\d{2})/(\d{4})")
_REF = re.compile(r"CTR:(\d+):OV:(\d+)")


def ler(cliente: Any, doc_entry: int) -> dict[str, Any]:
    """The order's row, ``{}`` when there is none. Raises on any base failure (the caller → ``null``)."""
    res = cliente.table(TABELA).select(COLUNAS).eq("doc_entry", int(doc_entry)).limit(1).execute()
    linhas = list(res.data or [])
    return linhas[0] if linhas else {}


def conclusao_da_obs(obs: str | None) -> date | None:
    """Last "Montagem concluída em dd/mm/aaaa" of a Previsão remark (port of the web's ler_conclusao)."""
    for seg in reversed((obs or "").split(_SEP)):
        m = _CONCL.match(seg.strip())
        if m:
            return date(int(m[3]), int(m[2]), int(m[1]))
    return None


def previsao_das_linhas(rows: list[dict[str, Any]], doc_entry: int) -> dict[str, Any]:
    """``@ALTA_PREV`` rows (``U_OrigRef``, ``Remark``) -> ``{"tem_previsao", "conclusao"}`` for one order.

    The SQL's LIKE is only a prefilter; the exact reference is checked here.
    """
    proprias = [r for r in rows
                if (m := _REF.fullmatch(r.get("U_OrigRef") or "")) and int(m[2]) == int(doc_entry)]
    conc = next((c for c in (conclusao_da_obs(r.get("Remark")) for r in proprias) if c), None)
    return {"tem_previsao": bool(proprias), "conclusao": conc.isoformat() if conc else None}


def bloco(linha: dict[str, Any] | None, prev: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """What the API publishes. ``None`` = the base could not be read (never "no assembly").

    ``fim_fonte``: ``"previsao"`` when D3 applies (the end comes from Previsão Financeira),
    ``"orcaview"`` otherwise.
    """
    if linha is None:
        return None
    da_previsao = bool(prev and prev.get("tem_previsao"))
    return {
        "inicio_montagem": linha.get("inicio_montagem"),
        "fim_montagem": prev.get("conclusao") if da_previsao else linha.get("fim_montagem"),
        "fim_fonte": "previsao" if da_previsao else "orcaview",
        "obs_processo": linha.get("obs_processo"),
        "atualizado_em": linha.get("atualizado_em"),
    }
