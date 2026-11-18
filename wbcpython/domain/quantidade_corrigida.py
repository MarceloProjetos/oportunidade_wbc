"""Which lines of an already created order get the quantity the text says — pure, no I/O.

Until 18/11/2026 only porta-paletes lines read their quantity from the text; every other line was born in the
SAP with 1 ("88 Protetores" -> 1, order 84603). `wbcpython quantidades` fixes the open orders that were born
like that, with the same reading the creation uses now (`domain.linhas.quantidade_no_texto`), so running it on an
order created after the fix finds nothing to change.

Only a line that is open, still at quantity 1, not delivered in part and created by the integration
(`U_INO_ORCITM`) is touched. The line total and the weight go back unchanged: the price absorbs the new
quantity, and the SAP rescales `Weight1` when the quantity changes (see `domain.peso_reescalado`).
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from wbcpython.domain.linhas import quantidade_no_texto

LINHA_ABERTA = "bost_Open"


@dataclass(frozen=True, slots=True)
class QuantidadeCorrigida:
    line_num: int
    orcitm: str
    nova: int
    total: float
    peso: float
    texto: str


def quantidades_a_corrigir(
    linhas: Iterable[Mapping[str, Any]],
    ja_maiores: Collection[int] = (),
) -> tuple[list[QuantidadeCorrigida], list[str]]:
    """``(changes, lines left alone on purpose)`` for the Service Layer's `DocumentLines`.

    The second list only names lines that WOULD change but must not: delivered in part, or in ``ja_maiores`` (the
    `LineNum`s that had a quantity above 1 in an earlier version — a person brought them back to 1). A line whose
    text has no other quantity is simply not mentioned.
    """
    mudancas: list[QuantidadeCorrigida] = []
    fora: list[str] = []
    for linha in linhas:
        orcitm = str(linha.get("U_INO_ORCITM") or "").strip()
        if not orcitm or linha.get("LineStatus") != LINHA_ABERTA:
            continue
        quantidade = float(linha.get("Quantity") or 0)
        if quantidade != 1:
            continue
        texto = str(linha.get("U_INO_D_Adicionais") or "")
        nova = quantidade_no_texto(texto)
        if not nova or nova == 1:
            continue
        line_num = int(linha["LineNum"])
        if line_num in ja_maiores:
            fora.append(f"linha {line_num} (ORCITM {orcitm}) já teve quantidade maior e alguém voltou para 1: fica")
            continue
        aberta = linha.get("RemainingOpenQuantity")
        if aberta is not None and float(aberta) < quantidade:
            fora.append(f"linha {line_num} (ORCITM {orcitm}) já entregue em parte: fica com 1, o texto diz {nova}")
            continue
        mudancas.append(
            QuantidadeCorrigida(
                line_num=line_num,
                orcitm=orcitm,
                nova=nova,
                total=float(linha.get("LineTotal") or 0),
                peso=float(linha.get("Weight1") or 0),
                texto=texto.strip()[:80],
            )
        )
    return mudancas, fora


def retrato(linhas: Iterable[Mapping[str, Any]]) -> dict[int, tuple[float, float, float]]:
    """``LineNum -> (Quantity, LineTotal, Weight1)``: what every line must still be after the write."""
    return {
        int(linha["LineNum"]): (
            float(linha.get("Quantity") or 0),
            round(float(linha.get("LineTotal") or 0), 2),
            round(float(linha.get("Weight1") or 0), 3),
        )
        for linha in linhas
        if linha.get("LineNum") is not None
    }


def divergencias(
    mudancas: Iterable[QuantidadeCorrigida],
    gravadas: Iterable[Mapping[str, Any]],
    antes: Mapping[int, tuple[float, float, float]] | None = None,
) -> list[str]:
    """What the SAP stored differently from what was sent — empty = the write is proven.

    A 204 does not prove the write (`DocumentLines.Text`, `VATFirst`): the command re-reads the order and stops at
    the first divergence, before touching the next one. With ``antes`` (the `retrato` of the first read) every
    OTHER line must be exactly as it was: a PATCH that matched the array by position instead of `LineNum` would
    damage a line nobody asked to change.
    """
    gravadas = list(gravadas)
    por_linha = {int(linha["LineNum"]): linha for linha in gravadas if linha.get("LineNum") is not None}
    erros: list[str] = []
    mudancas = list(mudancas)
    if antes is not None:
        alvos = {m.line_num for m in mudancas}
        depois = retrato(gravadas)
        if set(depois) != set(antes):
            erros.append(f"linhas do pedido mudaram: antes {sorted(antes)}, agora {sorted(depois)}")
        for line_num, valores in sorted(antes.items()):
            if line_num not in alvos and line_num in depois and depois[line_num] != valores:
                erros.append(f"linha {line_num} (não pedida) mudou: {valores} -> {depois[line_num]}")
    for m in mudancas:
        linha = por_linha.get(m.line_num)
        if linha is None:
            erros.append(f"linha {m.line_num} sumiu do pedido")
            continue
        quantidade = float(linha.get("Quantity") or 0)
        total = float(linha.get("LineTotal") or 0)
        peso = float(linha.get("Weight1") or 0)
        if quantidade != m.nova:
            erros.append(f"linha {m.line_num}: quantidade {quantidade:g}, esperado {m.nova}")
        if abs(total - m.total) >= 0.005:
            erros.append(f"linha {m.line_num}: total {total:.2f}, esperado {m.total:.2f}")
        if abs(peso - m.peso) >= 0.0005:
            erros.append(f"linha {m.line_num}: peso {peso:g}, esperado {m.peso:g}")
    return erros
