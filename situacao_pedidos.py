"""situacao_pedidos -- Situacao dos Pedidos (view DDP) para a API 8077 e o MCP.

**PORTE do nucleo puro de** ``web_orcaview_V117/backend/services/situacao_pedidos_service.py``.
Decisao D1 do plano ``docs/PLANO_SITUACAO_PEDIDOS_MCP.md`` (2026-08-24): as duas maquinas
leem o MESMO HANA e rodam a MESMA logica; a copia e' mantida honesta por
``tests/test_situacao_pedidos_diffavel.py``, que compara funcao por funcao com o original
quando o repo do V117 esta ao lado (maquina de dev) e faz ``skip`` na .11.

**Nao "melhore" nada do trecho portado.** Cada esquisitice dele e' decisao de negocio com
teste atras no V117 -- o genero do status por coluna, o ``Atrasado`` historico, o ano
ausente no ``Prazo_Entrega``. Refatorar aqui reabre bug fechado e faz o teste falhar.

Pipeline (tudo puro, sem I/O -- testavel sem HANA)::

    linhas cruas -> normalizar() -> montar_dashboard()  (5 KPIs + lista + conferencia)
                                 -> filtrar()           (card/chip/busca/montador)

O que este modulo NAO faz: nao fala com HANA (F2), nao serve HTTP (F3), nao e' tool MCP
(F4). Ele so transforma linha crua em contrato.

**O que e' exclusivo da .11** (secao no fim do arquivo, fora do trecho portado de
proposito -- e' o que mantem o nucleo diffavel):

- ``alerta_liberacao`` / :func:`com_alerta` -- o texto legivel da regra dos 10 dias (D2);
- :func:`filtrar_bloqueio` -- o corte "bloqueado em QUALQUER etapa", que o ``filtrar``
  do V117 nao tem (a tela filtra uma etapa por vez, pelo card clicado);
- :func:`resumir` -- o perfil ``campos=resumo``, para a resposta caber no contexto de um
  cliente MCP (D4).
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "CAMPOS_RESUMO",
    "FIN_LIBERACAO_LIMITE_DIAS",
    "KPI_FILTROS",
    "MONTADOR_SEM",
    "STATUS_CHIPS",
    "ValidationError",
    "alerta_liberacao",
    "com_alerta",
    "filtrar",
    "filtrar_bloqueio",
    "montadores_do_recorte",
    "montar_dashboard",
    "normalizar",
    "prazo_fim",
    "resumir",
]


class ValidationError(ValueError):
    """Parametro fora do dominio (``kpi``, ``status``, ``bloqueio``).

    No V117 esta classe vem de ``exceptions`` e o handler do FastAPI a transforma em
    422. Aqui e' local -- a `api.py` traduz para 422 na F3. Herda de ``ValueError``
    para que quem esquecer de tratar ainda pegue o erro certo.
    """


def now_br() -> datetime:
    """Agora em America/Sao_Paulo, tz-aware -- equivalente ao ``utils.now_br`` do V117.

    O ``zoneinfo`` no Windows depende do pacote ``tzdata`` (chega por transitividade do
    pandas). Se faltar, cai no fuso da propria maquina, que na .11 e' o de Sao Paulo --
    e o valor so alimenta ``gerado_em`` e a data de referencia do aging.
    """
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("America/Sao_Paulo"))
    except Exception:  # tzdata ausente
        return datetime.now().astimezone()


# ---------------------------------------------------------------------------
# INICIO DO TRECHO PORTADO -- byte-identico ao V117 (fora as linhas de import).
# Alterar qualquer coisa daqui para baixo faz test_situacao_pedidos_diffavel falhar.
# ---------------------------------------------------------------------------

#: Accepted values for the ``kpi`` param (clicked card). ``None``/absent = all.
KPI_FILTROS = ("atrasados", "financeiro", "producao", "entrega")

#: Accepted values for the StatusPedido chip (owner's decision 2026-07-24, §8.5
#: of the plan): default ``todos`` mirrors Power BI; ``aberto`` isolates live delay.
STATUS_CHIPS = ("todos", "aberto", "fechado")

#: Montador selector sentinel for "orders with NO montador". Deliberately not a
#: CNPJ nor the empty string: ``""`` already means "all" in the param, and without
#: it the most useful cut of the filter (no montador defined yet) is unreachable.
MONTADOR_SEM = "__sem__"

#: Days the finance release may take before it counts as LATE (owner's rule,
#: 2026-08-19): an OPEN order with ``Financeiro = Bloqueado`` more than this many
#: days after ``Data_Pedido`` gets ``fin_liberacao_atrasada`` — the screen turns
#: the order number red and floats the row to the top.
FIN_LIBERACAO_LIMITE_DIAS = 10

#: Volume guard. The view returns the whole cut (189 rows at F0); the cap only
#: trips if it changes nature and starts returning history.
_MAX_LINHAS = 20_000


def _flag(v: Any) -> bool:
    """``'S'``/``'N'`` from the view → bool (tolerant of case/space/'Sim')."""
    return str(v or "").strip().lower().startswith("s")


def _status(v: Any) -> str:
    """Canonize by prefix: Liberado/Liberada → ``Liberado``; Bloquead* →
    ``Bloqueado``. An out-of-domain value passes through trimmed, nothing invented
    — F0 only saw the two, but if the view changes the odd value stays VISIBLE."""
    s = str(v or "").strip()
    low = s.lower()
    if low.startswith("liberad"):
        return "Liberado"
    if low.startswith("bloquead"):
        return "Bloqueado"
    return s


_PRAZO_RE = re.compile(r"^\s*(\d{2})/(\d{2})\s+A\s+(\d{2})/(\d{2})\s*$", re.IGNORECASE)


def prazo_fim(prazo_texto: str | None, data_entrega: str | None) -> str | None:
    """Date on which the delivery window **ends**, in ISO — or ``None``.

    ``Prazo_Entrega`` is TEXT and **has no year** (``"10/08 A 14/08"``), so a date
    cannot be subtracted from it directly. The year comes from ``Data_Entrega``,
    which is a TIMESTAMP and falls inside the window (measured 2026-07-27: 182 of
    189; the other 7 land 1-2 days past the end, same year).

    Year-rollover guard: a ``28/12 A 01/01`` window delivered in December would
    render 01/01 of the WRONG year. If the computed end lands more than 180 days
    from the delivery, it is corrected by ±1 year. (No window in the base crosses
    the year today — the guard is for December.)

    Returns:
        ``'YYYY-MM-DD'`` or ``None`` when the text does not match the format or the
        date is invalid (e.g. ``31/02``). ``None`` means "cannot assert" — better
        than an invented number on screen.
    """
    m = _PRAZO_RE.match(str(prazo_texto or ""))
    if not m or not data_entrega:
        return None
    _, _, dia_fim, mes_fim = (int(g) for g in m.groups())
    try:
        entrega = date.fromisoformat(str(data_entrega)[:10])
        fim = date(entrega.year, mes_fim, dia_fim)
    except ValueError:
        return None
    if (fim - entrega).days > 180:
        fim = fim.replace(year=fim.year - 1)
    elif (entrega - fim).days > 180:
        fim = fim.replace(year=fim.year + 1)
    return fim.isoformat()


def _dias_atraso(fim_iso: str | None, hoje: date) -> int | None:
    """Days since the deadline: **positive = late**, negative = days still left."""
    if not fim_iso:
        return None
    return (hoje - date.fromisoformat(fim_iso)).days


def _dias_desde(data_iso: str | None, hoje: date) -> int | None:
    """Whole days elapsed since an ISO date — ``None`` when there is no date."""
    if not data_iso:
        return None
    try:
        return (hoje - date.fromisoformat(str(data_iso)[:10])).days
    except ValueError:
        return None


def _montagem(r: dict[str, Any]) -> dict[str, Any]:
    """Order assembly block (F4.1) — comes from ``ORDR``, as in Pedidos.

    The type label comes from the single source :mod:`services.sap_montagem_labels`
    (valid UDF values from SAP itself): **no local map here** — a test blocks it.
    ``montador`` prefers the UDT name and falls back to the raw CNPJ when the
    registry does not resolve (a CNPJ beats an empty field).
    """
    import sap_montagem_labels

    cnpj = (r.get("MontadorCnpj") or "").strip()
    nome = (r.get("MontadorNome") or "").strip()
    return {
        "tipo": sap_montagem_labels.rotulo(r.get("MontagemCod"), r.get("MontagemTexto")),
        "tipo_cod": (r.get("MontagemCod") or "").strip(),
        "valor": float(r.get("MontagemValor") or 0.0),
        "montador": nome or cnpj,
        "montador_cnpj": cnpj,
    }


def normalizar(rows: list[dict[str, Any]], *, hoje: date | None = None) -> list[dict[str, Any]]:
    """Raw view rows (PascalCase) → API contract (§4.2 of the plan).

    The raw originals do NOT travel: canonical statuses, boolean flags, dates
    already ISO from the client (``_sl_date``), and ``prazo_entrega`` keeps the
    view's ready "dd/mm A dd/mm" text (no year — real sorting uses
    ``data_entrega``). Each order carries the ``montagem`` block
    (:func:`_montagem`) from ``ORDR`` in the same snapshot — the modal makes no
    extra request.

    Args:
        rows: Raw view rows.
        hoje: Aging reference date (``dias_atraso``). Defaults to today on the BR
            clock; explicit in tests, so the number does not depend on the day the
            suite runs.
    """

    hoje = hoje or now_br().date()
    return [_pedido(r, hoje) for r in rows]


def _pedido(r: dict[str, Any], hoje: date) -> dict[str, Any]:
    """One raw view row → the contract dict (§4.2 of the plan)."""
    fim = prazo_fim(r.get("Prazo_Entrega"), r.get("Data_Entrega"))
    status = (r.get("StatusPedido") or "").strip()
    # The view's "Atrasado" is HISTORICAL: still 'S' on a closed order that was
    # delivered past the deadline. For the screen, the Excel and the PDF, delay is a
    # thing of OPEN orders — a closed order is not late (owner, 2026-07-31, revising
    # the 2026-07-24 one that mirrored Power BI). The raw value stays in
    # ``atrasado_sap``: it is what lets us say "it was delivered late".
    atrasado_sap = _flag(r.get("Atrasado"))
    fechado = status.casefold() == "fechado"
    financeiro = _status(r.get("Financeiro"))
    # Finance-release aging: counted from the ORDER date (not the delivery
    # window). A closed order never alarms — same rationale as ``atrasado``.
    dias_desde_pedido = _dias_desde(r.get("Data_Pedido"), hoje)
    fin_liberacao_atrasada = (
        financeiro == "Bloqueado"
        and not fechado
        and dias_desde_pedido is not None
        and dias_desde_pedido > FIN_LIBERACAO_LIMITE_DIAS
    )
    return (
        {
            "doc_entry": r.get("DocEntry"),
            "doc_num": r.get("DocNum"),
            "data_pedido": r.get("Data_Pedido"),
            "card_code": (r.get("CardCode") or "").strip(),
            "card_name": (r.get("CardName") or "").strip(),
            "group_num": r.get("GroupNum"),
            # WBC quotation that originated the order + revision (ORDR; the revision
            # is a letter and comes empty on old orders).
            "cotacao_wbc": (r.get("CotacaoWbc") or "").strip(),
            "versao_wbc": (r.get("VersaoWbc") or "").strip(),
            "pymnt_group": (r.get("PymntGroup") or "").strip(),
            # Value and salesperson (ORDR + OSLP): the view carries neither. They exist
            # for the finance alert, which has to say how much is stuck and with whom;
            # the screen, the Excel and the PDF list their columns explicitly, so the
            # two extra keys are invisible to them.
            "valor_total": float(r.get("DocTotal") or 0.0),
            "moeda": (r.get("DocCur") or "").strip(),
            "vendedor": (r.get("Vendedor") or "").strip(),
            "integrar": _flag(r.get("Integrar")),
            "financeiro": financeiro,
            "dias_desde_pedido": dias_desde_pedido,
            "fin_liberacao_atrasada": fin_liberacao_atrasada,
            "sinal": _flag(r.get("Sinal")),
            "producao": _status(r.get("Producao")),
            "entrega": _status(r.get("Entrega")),
            "data_entrega": r.get("Data_Entrega"),
            "prazo_entrega": (r.get("Prazo_Entrega") or "").strip(),
            # Aging: window end as a real date + days since then (positive = past
            # the deadline). Says HOW MUCH the delay is; what says WHETHER it is
            # late is ``atrasado`` (view + closed-order rule).
            "prazo_fim": fim,
            "dias_atraso": _dias_atraso(fim, hoje),
            "atrasado": atrasado_sap and not fechado,
            "atrasado_sap": atrasado_sap,
            "ddo": _flag(r.get("DDO")),
            "peso": float(r.get("Peso") or 0.0),
            "status_pedido": status,
            "data_lib_fin": r.get("Data_Lib_Fin"),
            "data_lib_prod": r.get("Data_Lib_Prod"),
            "data_pagto": r.get("Data_Pagto"),
            "total_os": int(r.get("Total_OS") or 0),
            "total_os_fechadas": int(r.get("Total_OS_Fechadas") or 0),
            "montagem": _montagem(r),
        }
    )


def montar_dashboard(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Full dashboard payload from the raw view rows.

    The 5 KPIs and the table come from the SAME normalized list — 1 query, 1
    snapshot. The ``conferencia`` block exposes the invariant in the payload
    (screen footer) and is locked by a test.
    """

    pedidos = normalizar(rows)
    kpis = {
        "total": len(pedidos),
        "atrasados": sum(1 for p in pedidos if p["atrasado"]),
        "financeiro_bloqueado": sum(1 for p in pedidos if p["financeiro"] == "Bloqueado"),
        "producao_bloqueada": sum(1 for p in pedidos if p["producao"] == "Bloqueado"),
        "entrega_bloqueada": sum(1 for p in pedidos if p["entrega"] == "Bloqueado"),
    }
    return {
        "success": True,
        "gerado_em": now_br().isoformat(timespec="seconds"),
        "kpis": kpis,
        "pedidos": pedidos,
        "montadores": montadores_do_recorte(pedidos),
        "conferencia": {
            "total_kpi": kpis["total"],
            "total_tabela": len(pedidos),
            "nota": "KPIs e tabela derivam da mesma lista; divergência = bug.",
        },
    }


def montadores_do_recorte(pedidos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Montadores in the snapshot, to fill the selector without an extra request.

    Only those with an order in the cut get in — a dropdown with the whole UDT
    would offer dozens of filters that return an empty list. Sorted by name (the
    CNPJ is the key, but whoever reads it looks for the name).

    Returns:
        ``[{"cnpj": ..., "nome": ..., "qtd": N}, ...]``; ``nome`` falls back to the
        CNPJ when the UDT does not resolve (same rule as :func:`_montagem`).
    """
    por_cnpj: dict[str, dict[str, Any]] = {}
    for p in pedidos:
        m = p.get("montagem") or {}
        cnpj = m.get("montador_cnpj") or ""
        if not cnpj:
            continue
        item = por_cnpj.setdefault(cnpj, {"cnpj": cnpj, "nome": m.get("montador") or cnpj, "qtd": 0})
        item["qtd"] += 1
    return sorted(por_cnpj.values(), key=lambda x: x["nome"])


def filtrar(
    pedidos: list[dict[str, Any]],
    *,
    kpi: str | None = None,
    busca: str | None = None,
    status: str | None = None,
    montador: str | None = None,
) -> list[dict[str, Any]]:
    """Table cut: active card + search + StatusPedido chip + montador.

    Lives in the service (and not only in the JS) because the F3 Excel export
    redoes the SAME filter server-side — the front never sends rows, only params.

    Args:
        pedidos: Already normalized list (output of :func:`normalizar`).
        kpi: Active card (:data:`KPI_FILTROS`) or ``None`` = all.
        busca: Text matched (casefold) against client, code, order number and
            **WBC quotation** (the user often has only the WBC number at hand).
        status: :data:`STATUS_CHIPS` chip (``None`` = ``todos``).
        montador: Montador **CNPJ** (SAP's key — the name is a label and repeats),
            or :data:`MONTADOR_SEM` for "orders with no montador". ``None``/``""``
            = all. A nonexistent CNPJ returns an empty list on purpose: it is data,
            not a closed domain — raising would hide a filter with no effect.

    Raises:
        ValidationError: ``kpi`` or ``status`` out of domain.
    """
    if kpi is not None and kpi not in KPI_FILTROS:
        raise ValidationError(f"Filtro inválido: '{kpi}' (use {' | '.join(KPI_FILTROS)})")
    if status is not None and status not in STATUS_CHIPS:
        raise ValidationError(f"Status inválido: '{status}' (use {' | '.join(STATUS_CHIPS)})")

    def passa(p: dict[str, Any]) -> bool:
        if kpi == "atrasados" and not p["atrasado"]:
            return False
        if kpi == "financeiro" and p["financeiro"] != "Bloqueado":
            return False
        if kpi == "producao" and p["producao"] != "Bloqueado":
            return False
        if kpi == "entrega" and p["entrega"] != "Bloqueado":
            return False
        if status in ("aberto", "fechado") and p["status_pedido"].lower() != status:
            return False
        if montador:
            cnpj = (p.get("montagem") or {}).get("montador_cnpj") or ""
            if montador == MONTADOR_SEM:
                if cnpj:
                    return False
            elif cnpj != montador:
                return False
        if busca:
            alvo = (
                f"{p['card_name']} {p['card_code']} {p['doc_num']} "
                f"{p['cotacao_wbc']}"
            ).casefold()
            if busca.strip().casefold() not in alvo:
                return False
        return True

    return [p for p in pedidos if passa(p)]


# ---------------------------------------------------------------------------
# FIM DO TRECHO PORTADO. Daqui para baixo e' exclusivo da .11.
# ---------------------------------------------------------------------------

#: Aceito em ``bloqueio``. ``qualquer`` = travado em pelo menos UMA das tres etapas --
#: e' a consulta 2 do plano, e o ``filtrar`` do V117 nao a tem (a tela filtra uma etapa
#: por vez, pelo card clicado). ``nenhum`` = as tres liberadas, util para conferencia.
BLOQUEIO_FILTROS = ("qualquer", "financeiro", "producao", "entrega", "nenhum")

#: Perfil ``campos=resumo`` (D4): as 10 colunas da tela + o alerta dos 10 dias. 236
#: pedidos x ~40 campos nao cabe no contexto de um cliente MCP.
CAMPOS_RESUMO = (
    "data_pedido",
    "card_name",
    "doc_num",
    "sinal",
    "financeiro",
    "producao",
    "entrega",
    "prazo_entrega",
    "atrasado",
    "pymnt_group",
    "alerta_liberacao",
)


def alerta_liberacao(pedido: dict[str, Any]) -> str | None:
    """Texto legivel da regra dos 10 dias -- ou ``None`` quando nao ha alerta.

    A REGRA nao e' desta camada: quem decide e' o ``fin_liberacao_atrasada`` do nucleo
    portado (``Financeiro = Bloqueado``, pedido em aberto, mais de
    :data:`FIN_LIBERACAO_LIMITE_DIAS` dias desde a ``Data_Pedido``). Aqui so se escreve
    a frase -- uma regra, uma implementacao (D2 do plano).

    ``None`` em vez de ``False``: para o modelo que le isto, ausencia de texto e' sinal
    mais claro que um booleano negativo no meio de 10 campos.
    """
    if not pedido.get("fin_liberacao_atrasada"):
        return None
    dias = pedido.get("dias_desde_pedido")
    if dias is None:
        # `fin_liberacao_atrasada` nao fica True sem `dias_desde_pedido`; se ficar, a
        # frase sai sem o numero em vez de estourar.
        return f"Mais de {FIN_LIBERACAO_LIMITE_DIAS} dias preso no financeiro"
    return f"Mais de {FIN_LIBERACAO_LIMITE_DIAS} dias preso no financeiro ({dias} dias)"


def com_alerta(pedidos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Acrescenta ``alerta_liberacao`` a cada pedido, **no lugar**.

    Passada separada, e nao um campo dentro do ``_pedido`` portado, de proposito: e' o
    que mantem o nucleo byte-identico ao V117.
    """
    for p in pedidos:
        p["alerta_liberacao"] = alerta_liberacao(p)
    return pedidos


def filtrar_bloqueio(
    pedidos: list[dict[str, Any]], bloqueio: str | None = None,
) -> list[dict[str, Any]]:
    """Corte por etapa travada -- a consulta 2 do plano.

    Args:
        pedidos: lista ja normalizada.
        bloqueio: :data:`BLOQUEIO_FILTROS` ou ``None``/``""`` = sem filtro.

    Raises:
        ValidationError: valor fora do dominio.
    """
    if not bloqueio:
        return list(pedidos)
    if bloqueio not in BLOQUEIO_FILTROS:
        raise ValidationError(
            f"Bloqueio inválido: '{bloqueio}' (use {' | '.join(BLOQUEIO_FILTROS)})")

    def travado(p: dict[str, Any], etapa: str) -> bool:
        return p.get(etapa) == "Bloqueado"

    if bloqueio == "qualquer":
        return [p for p in pedidos
                if any(travado(p, e) for e in ("financeiro", "producao", "entrega"))]
    if bloqueio == "nenhum":
        return [p for p in pedidos
                if not any(travado(p, e) for e in ("financeiro", "producao", "entrega"))]
    return [p for p in pedidos if travado(p, bloqueio)]


def resumir(pedidos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Recorta cada pedido em :data:`CAMPOS_RESUMO` (D4).

    Campo ausente vira ``None`` em vez de desaparecer: quem consome uma lista de dicts
    heterogeneos nao consegue distinguir "nao tem" de "nao veio".
    """
    return [{c: p.get(c) for c in CAMPOS_RESUMO} for p in pedidos]
