"""How a closed pedido was invoiced — and whether a future-delivery sale was really shipped.

SAP closes the pedido (``ORDR.DocStatus = 'C'``) as soon as ANY outgoing NF is copied from it,
including the "simples faturamento para entrega futura" NF (CFOP 5922/6922), issued before the
goods leave. The goods leave later with the "remessa" NF (CFOP 5116/6116/5117/6117), which is
created standalone (``INV1.BaseType = -1``) and linked through the referenced-documents table
``INV21``: either to the future NF (``RefObjType = '13'``) or to the pedido itself
(``RefObjType = '17'``). A future NF can also be annulled by a credit note copied from it (ORIN).

Rule measured against every 2026 case in production (F0 of ``docs/PLANO_ENTREGA_FUTURA.md``,
16/10/2026), per pedido and by value without tax (``LineTotal``) — quantity does not work
because items sold as sets ship in fractions:

* net future = live 5922/6922 NFs copied from the pedido − live credit notes copied from them;
* shipped = live remessa NFs (at least one 5116/6116/5117/6117 line; the whole NF counts)
  referencing one of those NFs or the pedido, each counted once;
* complete when what is missing is within ``max(R$ 1,00, 0,5% of net future)``.

Read-only. All queries take the pedido ``DocEntry`` list read from SAP itself.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from pipeline_core import build_view_query
from sql_seguro import sql

logger = logging.getLogger(__name__)

__all__ = [
    "CFOP_ENTREGA_FUTURA",
    "CFOP_REMESSA",
    "Faturamento",
    "ler_faturamento",
    "situacao_remessa",
]

CFOP_ENTREGA_FUTURA = ("5922", "6922")
CFOP_REMESSA = ("5116", "6116", "5117", "6117")
#: Tolerance on the missing value: rounding showed up as R$ 0,02 (84021) in closed cases.
TOLERANCIA_ABSOLUTA = 1.00
TOLERANCIA_RELATIVA = 0.005

#: Remessa states that mean "the goods have not all left yet".
REMESSA_PENDENTE = ("nenhuma", "parcial")


@dataclass(frozen=True)
class Faturamento:
    """Invoicing summary of one pedido.

    ``tipo`` is ``"entrega_futura"`` when a live future-delivery NF exists, ``"venda"`` when
    only regular sale NFs exist, ``None`` when no live NF was copied from the pedido (e.g.
    closed by hand). ``remessa`` is set only for ``"entrega_futura"``.
    """

    tipo: str | None
    remessa: str | None
    faturado_em: str | None

    @property
    def pendente(self) -> bool:
        return self.tipo == "entrega_futura" and self.remessa in REMESSA_PENDENTE


def situacao_remessa(futura: float, devolvido: float, remetido: float) -> str:
    """``'nenhuma' | 'parcial' | 'completa' | 'anulada'`` for one pedido's future-delivery sale.

    ``'anulada'``: credit notes cancelled the whole future NF, so nothing is left to ship.
    """
    liquida = float(futura or 0) - float(devolvido or 0)
    remetido = float(remetido or 0)
    tolerancia = max(TOLERANCIA_ABSOLUTA, TOLERANCIA_RELATIVA * max(liquida, 0.0))
    if liquida <= tolerancia and remetido <= tolerancia:
        return "anulada"
    if liquida - remetido <= tolerancia:
        return "completa"
    return "nenhuma" if remetido <= tolerancia else "parcial"


def _num(valor: object) -> float:
    try:
        return float(valor) if valor is not None and valor == valor else 0.0
    except (TypeError, ValueError):
        return 0.0


def ler_faturamento(sap, schema: str, pedidos: list[int]) -> dict[int, Faturamento] | None:
    """Invoicing of each pedido ``DocEntry`` in ``pedidos``, on an open ``SAPExtractor``.

    Three queries for the whole list. Pedidos with no live NF are absent from the result.
    Returns ``None`` when any query fails — the caller keeps the plain ``DocStatus`` answer.
    """
    from extract_ordens_servico_engenharia import _data_iso  # same ISO format as the ORDR fields

    pedidos = sorted({int(p) for p in pedidos})
    if not pedidos:
        return {}
    oinv = build_view_query("OINV", schema)
    inv1 = build_view_query("INV1", schema)
    inv21 = build_view_query("INV21", schema)
    orin = build_view_query("ORIN", schema)
    rin1 = build_view_query("RIN1", schema)
    try:
        notas = sap.execute_query(*sql(
            t'SELECT d."BaseEntry" AS "Pedido", MAX(p."DocDate") AS "UltimaNF", '
            t'SUM(CASE WHEN d."CFOPCode" IN (\'5922\',\'6922\') THEN d."LineTotal" ELSE 0 END) AS "Futura" '
            t'FROM {inv1:ident} d JOIN {oinv:ident} p ON p."DocEntry" = d."DocEntry" '
            t'WHERE d."BaseType" = 17 AND d."BaseEntry" IN ({pedidos:ints}) AND p."CANCELED" = \'N\' '
            t'GROUP BY d."BaseEntry"'))
        if notas is None:
            return None
        com_futura = [int(r["Pedido"]) for _, r in notas.iterrows() if _num(r["Futura"]) > 0]
        devolvido: dict[int, float] = {}
        remetido: dict[int, dict[int, float]] = {}
        if com_futura:
            futuras = (
                t'SELECT DISTINCT d."DocEntry" AS "Nf", d."BaseEntry" AS "Pedido" '
                t'FROM {inv1:ident} d JOIN {oinv:ident} p ON p."DocEntry" = d."DocEntry" '
                t'WHERE d."BaseType" = 17 AND d."BaseEntry" IN ({com_futura:ints}) '
                t'AND p."CANCELED" = \'N\' AND d."CFOPCode" IN (\'5922\',\'6922\')')
            dev = sap.execute_query(*sql(
                t'SELECT f."Pedido", SUM(rl."LineTotal") AS "Devolvido" '
                t'FROM {rin1:ident} rl JOIN {orin:ident} r ON r."DocEntry" = rl."DocEntry" '
                t'JOIN (' + futuras + t') f ON rl."BaseType" = 13 AND rl."BaseEntry" = f."Nf" '
                t'WHERE r."CANCELED" = \'N\' GROUP BY f."Pedido"'))
            # A remessa NF is one with at least one remessa-CFOP line, and it counts WHOLE: an ST
            # item ships on the same NF with CFOP 6401 (84039, remessa 5348). The EXISTS also
            # drops the future NF itself, which references the pedido (17) too.
            rem = sap.execute_query(*sql(
                t'SELECT DISTINCT x."Pedido", m."DocEntry" AS "Remessa", '
                t'(SELECT SUM(l."LineTotal") FROM {inv1:ident} l WHERE l."DocEntry" = m."DocEntry") AS "Valor" '
                t'FROM (SELECT r."DocEntry", f."Pedido" FROM {inv21:ident} r JOIN (' + futuras
                + t') f ON r."RefObjType" = \'13\' AND r."RefDocEntr" = f."Nf" '
                t'UNION SELECT r."DocEntry", r."RefDocEntr" AS "Pedido" FROM {inv21:ident} r '
                t'WHERE r."RefObjType" = \'17\' AND r."RefDocEntr" IN ({com_futura:ints})) x '
                t'JOIN {oinv:ident} m ON m."DocEntry" = x."DocEntry" WHERE m."CANCELED" = \'N\' '
                t'AND EXISTS (SELECT 1 FROM {inv1:ident} l2 WHERE l2."DocEntry" = m."DocEntry" '
                t'AND l2."CFOPCode" IN (\'5116\',\'6116\',\'5117\',\'6117\'))'))
            if dev is None or rem is None:
                return None
            for _, r in dev.iterrows():
                devolvido[int(r["Pedido"])] = _num(r["Devolvido"])
            for _, r in rem.iterrows():
                valor = _num(r["Valor"])
                remetido.setdefault(int(r["Pedido"]), {})[int(r["Remessa"])] = valor
        resultado: dict[int, Faturamento] = {}
        for _, r in notas.iterrows():
            pedido = int(r["Pedido"])
            futura = _num(r["Futura"])
            if futura > 0:
                remessa = situacao_remessa(futura, devolvido.get(pedido, 0.0),
                                           sum(remetido.get(pedido, {}).values()))
                resultado[pedido] = Faturamento("entrega_futura", remessa, _data_iso(r["UltimaNF"]))
            else:
                resultado[pedido] = Faturamento("venda", None, _data_iso(r["UltimaNF"]))
        return resultado
    except Exception:
        logger.exception("[FATURAMENTO] leitura das NFs do pedido falhou — seguindo só pelo DocStatus")
        return None
