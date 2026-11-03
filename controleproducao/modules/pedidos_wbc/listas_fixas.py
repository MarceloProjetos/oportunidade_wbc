"""Fixed business lists used by `ProcessDefault.CriaItem` in the original addon.

Phase 1 (1:1 port, see "Débitos técnicos" item 4 of migration_guide.md): the lists stay
hardcoded data, exactly as in the C#. Moving them to a configurable table is phase 2
(question 6, section 9 of migration_guide.md).

Three lists, each deciding something different when a new item is created in `CriaItem`:

1. `PREFIXOS_GRUPO_333` — if `ItemCode` contains any of these prefixes, the item gets
   `ItemsGroupCode = 333` and `MaterialType = FinishedGoods`. It was a hardcoded
   `string[] stringArray` inside the C# method (not an external file).
2. `SOLDA` — read from `Resources/Solda.txt` (exact match, not "contains") by
   `carregar_solda()`. **Received 21/09/2026** and copied unchanged to `resources/Solda.txt`;
   until then the port ran with an empty list, sending to 358 items the legacy sends to 332
   (section 7.20 of migration_guide.md). Decides `ItemsGroupCode = 332` vs `358`, but is only
   consulted when the item did NOT match a prefix above: in the C# the prefix test comes first
   and wins. 1,467 of the Solda codes contain a 333 prefix, so they never reach the 332
   decision; the legacy does the same and it was preserved.

   The list is used at a second point, against a different field: `CriaItem` compares
   `ItemCode`/`PrdCode`, while the `UpdateItem` loop (force group 332) compares `PrdArv`. The
   asymmetry looks like a bug but was checked in both C# sources (`ProcessDefault.cs` line
   ~941 and `IntegraPedidoWBC.b1f.cs` line ~368) and is faithful. In the port that second
   check runs for every structure item, new or already existing
   (`service._cria_ou_atualiza_item`).
3. `EXPLOSAO_SOLDA` — `Resources/Explosao.txt` in the legacy (exact `ItemCode` match), 180
   codes, embedded below. Decides the flag `U_INO_EXPL_SOLDA = "S"`.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

# `stringArray` dentro de `ProcessDefault.CriaItem` (ProcessDefault.cs, ~linha 885-922).
# Comparação é "contém" (`ItemCode.Contains(x)`), não igualdade exata.
PREFIXOS_GRUPO_333: list[str] = [
    "ARG", "BAG", "BUC", "CAG", "CHU", "COR", "DIV", "DOB", "DOG", "ESC", "ESG",
    "ESTFET", "FEC", "FEG", "FIT", "GAV", "LIN", "LUG", "PAG", "PAIFET", "PET",
    "PIG", "POG", "PPFT", "PRG", "PUX", "RDN", "REA", "REF", "REN", "ROD", "ROL",
    "STP", "TPO", "TUG", "TPO", "TUG",
]

# `Resources/Explosao.txt` (180 códigos) — comparação é igualdade exata (`ItemCode == exp`).
EXPLOSAO_SOLDA: list[str] = [
    "ESTPTA00000000ES3000", "PPA1CPTS-2FFES200200", "PPA3CPAB-2FFDI150212",
    "PPA3CPAB-2FFES150212", "PPA3CPTI-2FFES200240", "PPA4SPAB-2FFDI175280",
    "PPA3SPTS-2FFDI160212", "PPA3SPTS-2FFES160212", "PPA1CPTI-2FFDI200240",
    "PPA1CPTI-2FFES200240", "PPA3CPAB-2FFDI150200", "PPA3CPTI-2FFES400220",
    "PPA3SPAB-2FFDI150200", "PPA3SPAB-2FFES150200", "PPA2CPTS-2FFES200300",
    "PPA4CPTI-2FFES075252", "PPA4C2TS-2FFDI300300", "PPA4C2TS-2FFES300300",
    "PPA4CPTS-2FFDI260340", "PPA4CPTS-2FFES260340", "PPA5CPTI-2FFDI200200",
    "PPA5CPTI-2FFES200200", "PPA9CPTI-2FFDI200300", "PPA9CPTI-2FFES200300",
    "PPA3CPTI-2FFDI200200", "PPA3CPTI-2FFES200200", "PPA3CPAB-2FFES150200",
    "PPA4CPTS-2FFDI200300", "PPA4CPTS-2FFES200300", "PPA6CPTI-2FFES200300",
    "PPA6CPTI-2FFDI200300", "ESTPTA00000000DI2200", "ESTPTA00000000ES2200",
    "PPA3CPTI-2FFDI200240", "PPA4SPAB-2FFES175280", "PPA3CPTI-2FFDI400220",
    "PPA6SPAB-2FFDI200300", "PPA6SPAB-2FFES200300", "PPA2CPTS-2FFDI200300",
    "ESTPTA00000000DI1480", "PPA5CPAB-2FFDI200200", "PPA4CPTI-2FFDI250300",
    "PPA4CPTI-2FFES250300", "PPA5CPAB-2FFES200200", "ESTPTA00000000DI1000",
    "ESTPTA00000000DI2000", "ESTPTA00000000DI2400", "ESTPTA00000000DI3000",
    "ESTPTA00000000ES1000", "ESTPTA00000000ES1480", "ESTPTA00000000ES2000",
    "ESTPTA00000000ES2400", "PPA3CPAB-2FFDI200220", "PPA3CPAB-2FFES200220",
    "PPA1CPTS-2FFDI200200", "PPA4CPTI-2FFDI240300", "PPA4CPTI-2FFDI075252",
    "PPA4CPTI-2FFDI100272", "PPA4CPTI-2FFES100272", "PPA4CPTI-2FFES240300",
    "PPA3CPTI-2FFDI150220", "PPA3CPTI-2FFES150220", "PAIPTAPUX00000000000",
    "PAIPTAGUISU000000000", "ETRGCMGUIIN320000000", "ETRGCMGUIIN420000000",
    "PAIPTAGUIIN104000000", "PAIPTAGUIIN112000000", "PAIPTAGUIIN120000000",
    "PAIPTAGUIIN136000000", "PAIPTAGUIIN144000000", "PAIPTAGUIIN152000000",
    "PAIPTAGUIIN160000000", "PAIPTAGUIIN176000000", "PAIPTAGUIIN184000000",
    "PAIPTAGUIIN192000000", "PAIPTAGUIIN200000000", "PAIPTAGUIIN216000000",
    "PAIPTAGUIIN224000000", "PAIPTAGUIIN232000000", "PAIPTAGUIIN240000000",
    "PAIPTAGUIIN256000000", "PAIPTAGUIIN264000000", "PAIPTAGUIIN272000000",
    "PAIPTAGUIIN280000000", "PAIPTAGUIIN296000000", "PAIPTAGUIIN304000000",
    "PAIPTAGUIIN312000000", "PAIPTAGUIIN320000000", "PAIPTAGUIIN336000000",
    "PAIPTAGUIIN344000000", "PAIPTAGUIIN352000000", "PAIPTAGUIIN360000000",
    "PAIPTAGUIIN376000000", "PAIPTAGUIIN384000000", "PAIPTAGUIIN392000000",
    "PAIPTAGUIIN400000000", "PAIPTAGUIIN416000000", "PAIPTAGUIIN424000000",
    "PAIPTAGUIIN432000000", "PAIPTATRISU150000000", "PAIPTATRISU160000000",
    "PAIPTATRISU170000000", "PAIPTATRISU180000000", "PAIPTATRISU190000000",
    "PAIPTATRISU200000000", "PAIPTATRISU210000000", "PAIPTATRISU220000000",
    "PAIPTATRISU230000000", "PAIPTATRISU240000000", "PAIPTATRISU250000000",
    "PAIPTATRISU260000000", "PAIPTATRISU270000000", "PAIPTATRISU280000000",
    "PAIPTATRISU290000000", "PAIPTATRISU300000000", "PAIPTATRISU310000000",
    "PAIPTATRISU320000000", "PAIPTATRISU330000000", "PAIPTATRISU340000000",
    "PAIPTATRISU350000000", "PAIPTATRISU360000000", "PAIPTATRISU370000000",
    "PAIPTATRISU380000000", "PAIPTATRISU390000000", "PAIPTATRISU400000000",
    "PAIPTATRISU410000000", "PAIPTATRISU420000000", "PAIPTATRISU430000000",
    "PAIPTATRISU440000000", "PAIPTATRISU450000000", "PAIPTATRISU460000000",
    "PAIPTATRISU470000000", "PAIPTATRISU480000000", "PAIPTATRISU490000000",
    "PAIPTATRISU500000000", "PAIPTATRISU510000000", "PAIPTATRISU520000000",
    "PAIPTATRISU530000000", "PAIPTATRISU540000000", "PAIPTATRISU550000000",
    "PAIPTATRISU560000000", "PAIPTATRISU570000000", "PAIPTATRISU580000000",
    "PAIPTATRISU590000000", "PAIPTATRISU600000000", "PAIPTATRISU610000000",
    "PAIPTATRISU620000000", "PAIPTATRISU630000000", "PAIPTATRISU640000000",
    "PAIPTATRISU650000000", "PAIPTATRISU660000000", "PAIPTATRISU670000000",
    "PAIPTATRISU680000000", "PAIPTATRISU690000000", "PAIPTATRISU700000000",
    "PAIPTATRISU710000000", "PAIPTATRISU720000000", "PAIPTATRISU730000000",
    "PAIPTATRISU740000000", "PAIPTATRISU750000000", "PAIPTATRISU760000000",
    "PAIPTATRISU770000000", "PAIPTATRISU780000000", "PAIPTATRISU790000000",
    "PAIPTATRISU800000000", "PAIPTATRISU810000000", "PAIPTATRISU820000000",
    "ESTPTA00000000SU1000", "ESTPTA00000000SU1480", "ESTPTA00000000SU2000",
    "ESTPTA00000000SU2200", "ESTPTA00000000SU2400", "ESTPTA00000000SU3000",
    "ESTPTATRANC000001000", "ESTPTATRANC000001480", "ESTPTATRANC000002000",
    "ESTPTATRANC000002200", "ESTPTATRANC000002400", "ESTPTATRANC000003000",
]

# Fallback caso o arquivo suma. Antes de 21/09/2026 esta constante ERA a lista (vazia),
# porque `Resources/Solda.txt` não tinha vindo junto com o código do addon.
SOLDA: frozenset[str] = frozenset()

_SOLDA_PATH = Path(__file__).resolve().parent / "resources" / "Solda.txt"


@lru_cache(maxsize=1)
def carregar_solda() -> frozenset[str]:
    """Load `Solda.txt` (faithful copy of the addon's `Resources/Solda.txt`, 21/09/2026).

    ~5,200 codes, queried once per structure item at two points. Hence two choices:

    - **`frozenset`**, not a list: the C# scans the whole array with `foreach` per item
      (O(n)). Same comparison result, only the lookup changes.
    - **`lru_cache`**: otherwise the 115 KB file would be read from disk per item, since
      `_cria_item` calls this per item. The C# also rereads it on every `CriaItem`; that is a
      cost to avoid, not behavior to preserve.

    The file has 5,229 lines and 5,015 distinct codes; the `frozenset` absorbs the repeats.
    Falls back to the empty `SOLDA` (with a warning) if the file is missing.
    """
    if _SOLDA_PATH.exists():
        linhas = (linha.strip() for linha in _SOLDA_PATH.read_text(encoding="utf-8").splitlines())
        return frozenset(linha for linha in linhas if linha)
    logger.warning(
        "Solda.txt não encontrado em %s — usando conjunto vazio. Nenhum item cairá em "
        "ItemsGroupCode=332, o que diverge do addon legado.",
        _SOLDA_PATH,
    )
    return SOLDA
