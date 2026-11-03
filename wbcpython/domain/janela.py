"""On-demand search window: bands, write ceiling and the request state.

No I/O, like the rest of `domain/`. These rules decide *how much* a cycle may write when someone asks
for a window larger than the default, and *when* the request returns to the default on its own.

The problem it solves (`PLANO_JANELA_SOB_DEMANDA.md (removed 2026-10-06)`):

A larger window brings back many more backlogged opportunities. With the fixed ceiling of 200 writes per
cycle, "open 24 months for one cycle" would write 200 and abandon the rest, because the window would
already be back to 6: the request would be spent without doing what was asked. Removing the ceiling is
worse: one cycle would create thousands of irreversible production documents with nobody watching.

The way out is a ceiling that **grows with the window**, and a question when even that is not enough.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime
from math import ceil

#: Largura de cada banda de multiplicador, em meses.
LARGURA_DA_BANDA = 6

#: Quanto o teto de escrita cresce a cada banda acima do padrão.
#:
#: Progressão aritmética (3×, 6×, 9×), e não dobra (3×, 6×, 12×): decisão 2 do
#: plano, fechada pelo Marcelo em 11/09/2026. O passo constante mantém o pior
#: caso previsível quando alguém for mexer nos números daqui a um ano.
PASSO_DO_MULTIPLICADOR = 3

#: Teto que nenhuma banda ultrapassa, por mais larga que seja a janela.
#:
#: O escalonamento é uma regra; isto é a rede embaixo dela. Existe para o dia em
#: que alguém aumentar `JANELA_MAXIMA` ou o passo sem refazer a conta do pior
#: caso — sem este limite, um número digitado errado vira um ciclo de milhares
#: de escritas irreversíveis em produção.
TETO_ABSOLUTO_PADRAO = 2000


class EstadoDaJanela(enum.StrEnum):
    """Where the window request stands.

    The three states separate two things that look like one: "the next cycle must use the wide window"
    (`ARMADO`) and "a wide cycle already ran, hit the ceiling and awaits an answer" (`AGUARDANDO`). Without
    that split the worker would keep sweeping 24 months every cycle (`WORKER_INTERVAL_SECONDS`, 300 s by
    default) while the question waits, which is exactly the heavy cycle running by itself that this design
    exists to prevent.
    """

    OCIOSO = "ocioso"
    ARMADO = "armado"
    AGUARDANDO = "aguardando"


def multiplicador_do_teto(meses: int, *, padrao: int) -> int:
    """How many times the write ceiling grows for a window of `meses`.

    The band is counted from the **default** window, not from the number 6: the excess is what justifies the
    larger ceiling, and `MESES_DE_JANELA` stays adjustable in `.env`. With a default of 6 this gives exactly
    the plan's table:

    | Window   | Multiplier |
    |----------|------------|
    | up to 6  | 1x         |
    | 7 to 12  | 3x         |
    | 13 to 18 | 6x         |
    | 19 to 24 | 9x         |
    """
    if meses <= padrao:
        return 1
    banda = ceil((meses - padrao) / LARGURA_DA_BANDA)
    return PASSO_DO_MULTIPLICADOR * banda


def teto_de_escrita(
    meses: int,
    *,
    padrao: int,
    base: int,
    absoluto: int = TETO_ABSOLUTO_PADRAO,
) -> int:
    """Write ceiling of a cycle whose window is `meses`.

    `base` is `LIMITE_DE_ESCRITA_POR_CICLO`, the ceiling of the default window, which this plan leaves
    unchanged.
    """
    return min(base * multiplicador_do_teto(meses, padrao=padrao), absoluto)


@dataclass(frozen=True, slots=True)
class Janela:
    """The window a cycle will use, and where it came from.

    `estendida` cannot be derived from `meses > padrao` by whoever only receives the result, and it decides
    whether the end of the cycle touches the request. It therefore travels along instead of being recomputed
    at each use site.
    """

    meses: int
    teto: int
    estendida: bool = False

    def __str__(self) -> str:
        origem = "estendida, a pedido" if self.estendida else "padrão"
        return f"{self.meses} meses ({origem}); teto de {self.teto} escrita(s)"


def validar_meses(meses: int, *, padrao: int, maximo: int) -> int:
    """Return `meses`, or raise `ValueError` with the sentence that goes to the screen.

    Validation is on the server on purpose: the screen field limits what is easy to type, not what reaches
    the worker. Arming through the CLI or a hand-made POST goes through here just the same.
    """
    if meses < padrao:
        raise ValueError(
            f"A janela pedida ({meses}) é menor que o padrão ({padrao}). "
            f"Para voltar ao padrão, use a opção de limpar o pedido."
        )
    if meses > maximo:
        raise ValueError(f"A janela pedida ({meses}) passa do máximo de {maximo} meses.")
    return meses


def expirou(
    estado: EstadoDaJanela,
    expira_em: datetime | None,
    *,
    agora: datetime,
) -> bool:
    """Whether a request in `AGUARDANDO` has passed its answer deadline.

    Only `AGUARDANDO` expires. `ARMADO` is a request not yet served, and one made at 19:00 on a Friday only
    acts on Monday, because the worker does not run outside business hours. Expiring it by time would
    silently swallow exactly the request of whoever waited through the weekend.
    """
    if estado is not EstadoDaJanela.AGUARDANDO or expira_em is None:
        return False
    return agora >= expira_em
