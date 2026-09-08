"""De-para grupo do WBC → item do SAP (`@INO_GRP_PRODUTOS`).

Esta é a tabela que responde à pergunta "qual item do SAP corresponde a esta
linha do orçamento". A resposta **não** está no texto da linha (`ORCTXT`), como
a leitura dos dados sugere à primeira vista, e sim no código de grupo
(`GRPCOD`) — foi assim que o sistema legado sempre fez
(`Querys.resx → GetItensSAP`).

A tabela é pequena e praticamente estática (11 grupos), então é lida uma vez por
execução e mantida em memória: relê-la a cada linha de cada orçamento seria uma
chamada de rede por linha, sem nada em troca.

**Nome do recurso no Service Layer:** `U_INO_GRP_PRODUTOS`, com o prefixo `U_`.
O nome sem prefixo responde `-1002 Service Not Found` — verificado contra o
ambiente real. É a convenção do Service Layer para tabelas de usuário que não
estão registradas como UDO.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)

RECURSO = "U_INO_GRP_PRODUTOS"


@dataclass(frozen=True, slots=True)
class GrupoDeProduto:
    """Uma linha do de-para."""

    codigo: str
    descricao: str
    item_sap: str


class RepositorioGrupoProdutos(Protocol):
    """Contrato de leitura do de-para — sem escrita, por construção."""

    def carregar(self) -> dict[str, GrupoDeProduto]: ...


class RepositorioGrupoProdutosServiceLayer:
    """Lê o de-para do Service Layer e o guarda em memória."""

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
