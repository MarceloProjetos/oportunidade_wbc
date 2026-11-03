"""Models of the native SAP HANA views.

These views exist **only in the production schema** (checked against the HANA catalog,
`SYS.VIEWS`, in both environments); see "Ler o HANA do schema de produção" in
`docs/wbc/DECISOES.md`.

The fields come from the **real catalog** (`SYS.VIEW_COLUMNS`), not guesswork: an earlier
version guessed column names and failed against the real environment.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


class EvolucaoOportunidade(BaseModel):
    """Row of `VW_EVOL_OPORTUNIDADE_ALT`: the WBC quote status as mirrored in HANA, plus the
    opportunity's commercial data.

    `Retorno`, `Indice` and `Negociacao` are **NVARCHAR** in the view, with an inconsistent
    decimal separator (see "HANA" in `docs/wbc/APRENDIZADOS.md`), so they are read through
    `normalizar_decimal_tolerante` instead of being cast directly.
    """

    model_config = {"frozen": True}

    n_wbc: str
    """Número do orçamento no WBC — corresponde ao `ORCNUM`/`U_INO_COD`."""

    status_wbc: str = ""
    """Descrição textual do status (ex.: "Entrada", "Emissao p/ Cliente",
    "Calculo Financeiro"). É texto, não o código numérico `SITCOD`."""

    num_oportunidade: int | None = None
    num_documento: int | None = None
    tipo_documento: str = ""
    cotacao: int | None = None

    codigo_pn: str = ""
    nome_pn: str = ""
    representante: str = ""
    municipio: str = ""
    uf: str = ""

    valor: Decimal | None = None
    data_oportunidade: datetime | None = None
    data_cotacao: datetime | None = None

    pct_comissao: Decimal | None = None
    retorno: Decimal | None = None
    indice: Decimal | None = None
    negociacao: Decimal | None = None


class MunicipioCliente(BaseModel):
    """Row of `VW_CLIENTE_MUNICIPIO_ALTA`: resolves the municipality's internal id (`AbsId`),
    used when building business-partner addresses in SAP.

    The view has the name twice: `Name` (accented, e.g. "Acrelândia") and `Name_N`
    (normalized: unaccented, upper case, e.g. "ACRELANDIA"). Lookup uses `Name_N` because the
    municipality coming from the WBC is unaccented; comparing with `Name` would miss every
    accented city.
    """

    model_config = {"frozen": True}

    abs_id: int
    municipio: str
    """Nome com acentuação (`Name`)."""

    municipio_normalizado: str = ""
    """Nome sem acentuação, em maiúsculas (`Name_N`) — usado na busca."""

    uf: str = ""
    codigo: str = ""
    codigo_ibge: str = ""
    pais: str = "BR"
