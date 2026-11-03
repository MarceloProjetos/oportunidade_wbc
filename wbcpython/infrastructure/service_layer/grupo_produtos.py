"""WBC group → SAP item mapping (`@INO_GRP_PRODUTOS`).

Answers "which SAP item matches this quotation line". The answer is NOT in the line text
(`ORCTXT`), as the data suggests at first glance, but in the group code (`GRPCOD`) — which is
how the legacy system always did it (`Querys.resx → GetItensSAP`).

The table is small and practically static (11 groups), so it is read once per run and kept in
memory instead of costing one network call per line of every quotation.

**SL resource name:** `U_INO_GRP_PRODUTOS`, with the `U_` prefix. The unprefixed name answers
`-1002 Service Not Found` (verified against the real environment); the prefix is the SL
convention for user tables that are not registered as UDOs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)

RECURSO = "U_INO_GRP_PRODUTOS"


@dataclass(frozen=True, slots=True)
class GrupoDeProduto:
    """One row of the mapping."""

    codigo: str
    descricao: str
    item_sap: str


class RepositorioGrupoProdutos(Protocol):
    """Read contract of the mapping — no writes, by construction."""

    def carregar(self) -> dict[str, GrupoDeProduto]: ...


class RepositorioGrupoProdutosServiceLayer:
    """Reads the mapping from the Service Layer and caches it in memory."""

    def __init__(self, cliente: Any) -> None:
        self._cliente = cliente
        self._cache: dict[str, GrupoDeProduto] | None = None

    def carregar(self) -> dict[str, GrupoDeProduto]:
        if self._cache is None:
            self._cache = self._ler()
        return self._cache

    def _ler(self) -> dict[str, GrupoDeProduto]:
        dados = self._cliente.get_json(RECURSO)
        mapa: dict[str, GrupoDeProduto] = {}
        for linha in dados.get("value", []):
            codigo = str(linha.get("Code") or "").strip()
            item = str(linha.get("U_INO_ItemSAP") or "").strip()
            if not codigo or not item:
                # Uma linha do de-para sem item não aponta para lugar nenhum.
                # Ignorá-la em silêncio faria o grupo cair no fallback como se
                # nem existisse — o aviso é o que separa "grupo desconhecido"
                # de "grupo cadastrado pela metade".
                logger.warning("Grupo de produto ignorado por cadastro incompleto: %r", linha)
                continue
            mapa[codigo] = GrupoDeProduto(
                codigo=codigo,
                descricao=str(linha.get("U_INO_Descricao") or "").strip(),
                item_sap=item,
            )
        logger.info("De-para de grupos carregado: %d grupo(s).", len(mapa))
        return mapa
