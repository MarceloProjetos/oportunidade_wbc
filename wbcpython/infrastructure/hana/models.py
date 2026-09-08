"""Modelos das views nativas do SAP HANA.

Estas views existem **apenas no schema de produção** — confirmado por consulta
ao catálogo do HANA (`SYS.VIEWS`) nos dois ambientes. Ver a decisão registrada
em `ai_spec/02_data_model.md` e no `PROGRESS.md`.

Os campos abaixo foram tirados do **catálogo real** (`SYS.VIEW_COLUMNS`), não de
suposição — uma versão anterior deste módulo chutou nomes de coluna e falhou
contra o ambiente real.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


class EvolucaoOportunidade(BaseModel):
    """Linha de `VW_EVOL_OPORTUNIDADE_ALT`.

    Traz o status do orçamento no WBC já refletido dentro do HANA, junto com
    dados comerciais da oportunidade.

    Atenção aos tipos: `Retorno`, `Indice` e `Negociacao` são **NVARCHAR** na
    view, não numéricos — é o mesmo problema de separador decimal inconsistente
    documentado em `ai_spec/02_data_model.md`. Por isso são normalizados na
    leitura, em vez de convertidos direto.
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
    """Linha de `VW_CLIENTE_MUNICIPIO_ALTA`.

    Resolve o identificador interno (`AbsId`) do município, usado ao montar
    endereços de parceiro de negócios no SAP.

    A view traz o nome em duas formas: `Name` (com acentuação, ex.:
    "Acrelândia") e `Name_N` (normalizado, sem acento e em maiúsculas, ex.:
    "ACRELANDIA"). A busca usa `Name_N`, porque o município que chega do WBC
    vem sem acentuação — comparar com `Name` erraria em toda cidade acentuada.
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
