"""Weight the SAP rescaled when a person changed a line's quantity — `docs/PLANO_PESO_REESCALADO.md`.

The SAP client multiplies ``Weight1`` by new/old quantity whenever someone changes the quantity
of a line (84457, 02/10/2026: 1 → 30 turned 148,94 kg into 4.468,20). The integration writes
the weight of the WHOLE line (WBC tree level 1 + 10%), which does not change when a person
rewrites "1 lot" as "30 units", so the rescale is wrong. This rule says when to put the weight
back. Pure: the versions come from the SAP change log (ADOC/ADO1), read elsewhere.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Any

#: Decision 4 (Marcelo, 05/10/2026): 3 min after the last save — the person has usually
#: closed the order by then. Saving over an order someone still has open makes the SAP
#: refuse THEIR save (-2039), so this is the price of acting sooner.
ESPERA = timedelta(minutes=3)

#: Decision 3: a line total that moved more than this is a change in the sale, not in how it
#: is written — 12 of the 13 measured total changes were under 5%; the 13th (84438) was -55%.
TOLERANCIA_DO_TOTAL = 0.05

#: How close the new weight must be to old × new qty ÷ old qty to count as the SAP's rescale.
TOLERANCIA_DA_REESCALA = 0.005
TOLERANCIA_EM_KG = 0.01


class Acao(StrEnum):
    CORRIGIR = "corrigir"
    AVISAR = "avisar"
    NADA = "nada"


@dataclass(frozen=True, slots=True)
class Versao:
    """One saved version of an order line (ADO1 + who saved it, from ADOC)."""

    instancia: int
    quantidade: float
    peso: float
    total: float
    pela_integracao: bool
    usuario: str
    momento: datetime | None


@dataclass(frozen=True, slots=True)
class LinhaAtual:
    """The line as it is now (RDR1)."""

    quantidade: float
    peso: float
    total: float
    aberta: bool = True


@dataclass(frozen=True, slots=True)
class Mudanca:
    """One change of the line's weight: the version before and the version that changed it."""

    antes: Versao
    depois: Versao

    @property
    def mudou_quantidade(self) -> bool:
        return abs(self.antes.quantidade - self.depois.quantidade) > 1e-9

    @property
    def reescala(self) -> bool:
        """A person changed the quantity and the weight moved by the same ratio."""
        a, d = self.antes, self.depois
        if d.pela_integracao or not self.mudou_quantidade or a.quantidade <= 0:
            return False
        esperado = a.peso * d.quantidade / a.quantidade
        return abs(d.peso - esperado) <= max(TOLERANCIA_EM_KG, abs(esperado) * TOLERANCIA_DA_REESCALA)


@dataclass(frozen=True, slots=True)
class Decisao:
    acao: Acao
    motivo: str
    #: The weight to write back, only for CORRIGIR.
    peso: float | None = None
    #: The first rescale of the chain being undone (who, when, from what quantity).
    reescala: Mudanca | None = None


def _nada(motivo: str) -> Decisao:
    return Decisao(Acao.NADA, motivo)


def mudancas_de_peso(versoes: Sequence[Versao]) -> list[Mudanca]:
    """Every version in which the weight moved, oldest first."""
    return [
        Mudanca(a, d)
        for a, d in zip(versoes, versoes[1:], strict=False)
        if abs(a.peso - d.peso) >= 0.005
    ]


def decidir(
    versoes: Sequence[Versao],
    atual: LinhaAtual,
    agora: datetime,
    *,
    espera: timedelta = ESPERA,
    tolerancia_do_total: float = TOLERANCIA_DO_TOTAL,
    a_partir_de: datetime | None = None,
    historico_cortado: bool = False,
) -> Decisao:
    """Put the weight back, warn, or leave the line alone.

    CORRIGIR only when the last weight change was the SAP rescaling a person's quantity change,
    nobody typed another weight since, the weight before it was the integration's, and the line
    total stayed within 5%. Consecutive rescales (1 → 30 → 25) undo as one, back to the weight
    before the first. A weight a person typed is never overwritten.

    ``a_partir_de``: a rescale that started before it is the past and is left as it is
    (Marcelo, 05/10/2026: "pode analisar mas não alterar o passado"). ``historico_cortado``: the
    ORDER's change log no longer starts at version 1 (the SAP keeps 99), so the author of the
    line's first known weight is unknown — a line added after creation is not that case.
    """
    if not atual.aberta:
        return _nada("linha fechada")
    mudancas = mudancas_de_peso(versoes)
    if not mudancas or not mudancas[-1].reescala:
        return _nada("a última mudança de peso não foi o SAP reescalando uma troca de quantidade")
    inicio = len(mudancas) - 1
    while inicio > 0 and mudancas[inicio - 1].reescala:
        inicio -= 1
    primeira, ultima = mudancas[inicio], mudancas[-1]

    salvo_em = versoes[-1].momento
    if salvo_em is None or primeira.depois.momento is None:
        return _nada("histórico sem horário")
    if a_partir_de is not None and primeira.depois.momento < a_partir_de:
        return _nada(f"troca anterior a {a_partir_de:%d/%m/%Y %H:%M}: o passado não se altera")
    if agora - salvo_em < espera:
        return _nada(f"aguardando {int(espera.total_seconds() // 60)} min depois do último salvamento")
    if abs(atual.peso - ultima.depois.peso) > TOLERANCIA_EM_KG or abs(
        atual.quantidade - ultima.depois.quantidade
    ) > 1e-9:
        return _nada("o peso atual não é mais o que o SAP calculou")

    antes = primeira.antes
    if antes.peso <= 0:
        return _nada("sem peso antes da troca")
    # 1 → 30 → 1 cancels out; without this the line would be "fixed" to the weight it has, on
    # every cycle (review, 05/10/2026).
    if abs(atual.peso - antes.peso) <= max(TOLERANCIA_EM_KG, antes.peso * TOLERANCIA_DA_REESCALA):
        return _nada("o peso já é o de antes da troca")
    # Who set the weight that was multiplied: the version where it last changed before the chain.
    anteriores = mudancas[:inicio]
    autor = anteriores[-1].depois if anteriores else versoes[0]
    if not anteriores and historico_cortado:
        return Decisao(Acao.AVISAR, "o começo do histórico foi apagado pelo SAP", reescala=primeira)
    if not autor.pela_integracao:
        return Decisao(
            Acao.AVISAR, f"o peso de antes ({kg(antes.peso)} kg) foi digitado por {autor.usuario}",
            reescala=primeira,
        )
    if antes.total > 0:
        variacao = (atual.total - antes.total) / antes.total
    else:
        variacao = 0.0 if abs(atual.total) < 0.005 else 1.0
    if abs(variacao) > tolerancia_do_total:
        return Decisao(
            Acao.AVISAR, f"o total da linha mudou {variacao:+.1%}".replace(".", ","), reescala=primeira
        )
    return Decisao(Acao.CORRIGIR, "o SAP reescalou o peso da integração", peso=antes.peso, reescala=primeira)


def kg(valor: float) -> str:
    return f"{valor:,.2f}".replace(",", "\0").replace(".", ",").replace("\0", ".")


def descrever(decisao: Decisao) -> str:
    """The cause, as the log tells it: who changed what, when, and what the SAP did."""
    m = decisao.reescala
    if m is None:
        return decisao.motivo
    quando = f" em {m.depois.momento:%d/%m/%Y às %H:%M}" if m.depois.momento else ""
    return (
        f"{m.depois.usuario} mudou a quantidade de {m.antes.quantidade:g} para "
        f"{m.depois.quantidade:g}{quando} e o SAP refez o peso ({kg(m.antes.peso)} → "
        f"{kg(m.depois.peso)} kg)"
    )


def momento_do_sap(data: Any, hora: Any) -> datetime | None:
    """ADOC ``UpdateDate`` (a date) + ``UpdateTS`` (HHMMSS as an int) → datetime, or None.

    No time means None, never midnight: midnight would make the 3-minute wait pass at once.
    """
    if not isinstance(data, date) or hora is None:
        return None
    try:
        hhmmss = int(hora)
        return datetime(data.year, data.month, data.day, hhmmss // 10000, hhmmss // 100 % 100, hhmmss % 100)
    except (TypeError, ValueError):
        return None


def versao_da_linha(linha: Mapping[str, Any], usuarios_da_integracao: Collection[str]) -> Versao:
    """One ADO1 row (with ADOC's ``UpdateDate``/``UpdateTS`` and OUSR's ``USER_CODE``/``U_NAME``)."""
    codigo = str(linha.get("USER_CODE") or "").strip()
    return Versao(
        instancia=int(linha.get("LogInstanc") or 0),
        quantidade=float(linha.get("Quantity") or 0),
        peso=float(linha.get("Weight1") or 0),
        total=float(linha.get("LineTotal") or 0),
        pela_integracao=codigo.lower() in usuarios_da_integracao,
        usuario=str(linha.get("U_NAME") or codigo or "usuário não identificado").strip(),
        momento=momento_do_sap(linha.get("UpdateDate"), linha.get("UpdateTS")),
    )
