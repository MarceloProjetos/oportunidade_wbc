"""Repository of the `OrcDetalhe` UDO over the Service Layer.

`OrcDetalhe` (header table `INO_ORCAM`, lines table `INO_ORC_LINHA`) mirrors the WBC quotation
inside SAP.

**Essential behaviour: it is a history, not a mutable record.** Confirmed on real data — the
same `U_INO_COD` appears under several `DocEntry` (e.g. quotation `00123316` exists as 513925
*and* 513927). It matches the legacy system, which always calls `.Add()`, never `.Update()`,
and resolves "the current one" with `SELECT max("DocEntry")`.

Hence this repository **exposes no update**: each verification writes a new record and the
current state is always "the highest DocEntry for that code". An `atualizar()` here would be an
invitation to corrupt the history.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from wbcpython.infrastructure.service_layer.client import ServiceLayerClient
from wbcpython.infrastructure.service_layer.errors import ServiceLayerError

logger = logging.getLogger(__name__)

ENTIDADE = "OrcDetalhe"
COLECAO_LINHAS = "INO_ORC_LINHACollection"


class RepositorioOrcDetalhe(Protocol):
    """OrcDetalhe UDO contract. No update, by design."""

    def ultimo_snapshot(self, codigo: str) -> dict[str, Any] | None: ...

    def criar_snapshot(self, dados: dict[str, Any]) -> dict[str, Any]: ...

    def existe(self, codigo: str) -> bool: ...


def _escapar_literal_odata(valor: str) -> str:
    """Escapes single quotes in an OData literal (by doubling them).

    Without it a code with an apostrophe would break the `$filter` and, at the limit, change its
    meaning. Quotation codes are numeric today, but the repository must not depend on that.
    """
    return valor.replace("'", "''")


class RepositorioOrcDetalheServiceLayer:
    """Service Layer implementation."""

    def __init__(self, cliente: ServiceLayerClient) -> None:
        self._cliente = cliente

    # ------------------------------------------------------------- leitura

    def ultimo_snapshot(self, codigo: str) -> dict[str, Any] | None:
        """Returns the quotation's most recent snapshot, or `None`.

        "Most recent" is the highest `DocEntry` — the legacy's definition. Never assume one record per
        code.
        """
        registros = self._cliente.listar(
            ENTIDADE,
            filtro=f"U_INO_COD eq '{_escapar_literal_odata(codigo)}'",
            ordenar_por="DocEntry desc",
            top=1,
        )
        if not registros:
            return None

        # A listagem não traz a coleção de linhas aninhada; buscar o documento
        # completo por chave é o que garante os itens.
        return self.por_docentry(int(registros[0]["DocEntry"]))

    def por_docentry(self, doc_entry: int) -> dict[str, Any]:
        return self._cliente.get_json(f"{ENTIDADE}({doc_entry})")

    def existe(self, codigo: str) -> bool:
        registros = self._cliente.listar(
            ENTIDADE,
            filtro=f"U_INO_COD eq '{_escapar_literal_odata(codigo)}'",
            top=1,
            selecionar="DocEntry",
        )
        return bool(registros)

    def historico(self, codigo: str, *, limite: int = 20) -> list[dict[str, Any]]:
        """All snapshots of the quotation, newest first (at most `limite`).

        Shows how the quotation evolved across verifications, which is what the UDO history keeps.
        No caller in `wbcpython` uses it today.
        """
        return self._cliente.listar(
            ENTIDADE,
            filtro=f"U_INO_COD eq '{_escapar_literal_odata(codigo)}'",
            ordenar_por="DocEntry desc",
            top=limite,
        )

    # -------------------------------------------------------------- escrita

    def criar_snapshot(self, dados: dict[str, Any]) -> dict[str, Any]:
        """Writes a new snapshot (`POST`), the equivalent of DI-API `.Add()`.

        There is no update counterpart: see the module note. The environment safety lock is applied
        by the client, in `request()`.

        Raises:
            ValueError: `U_INO_COD` missing.
            ServiceLayerError: SAP accepted the write but the body is not JSON.
        """
        if not dados.get("U_INO_COD"):
            raise ValueError(
                "U_INO_COD é obrigatório: é a chave de negócio que liga o snapshot "
                "ao orçamento do WBC."
            )

        resposta = self._cliente.post(ENTIDADE, json=dados)
        try:
            criado = resposta.json()
        except ValueError as exc:
            raise ServiceLayerError(
                "O SAP aceitou a gravação mas devolveu um corpo que não é JSON.",
                status_code=resposta.status_code,
                metodo="POST",
                caminho=ENTIDADE,
            ) from exc

        logger.info(
            "Snapshot do OrcDetalhe criado: U_INO_COD=%s DocEntry=%s",
            dados.get("U_INO_COD"),
            criado.get("DocEntry"),
        )
        return criado
