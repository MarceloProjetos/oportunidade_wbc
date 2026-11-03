"""Documents already resolved by the read, with no extra SAP round trip.

`montar_estado` needs, per quote, whether a quotation and an order exist, which revision is
applied to them, which partner the order sits on and whether it is closed. Asking the Service
Layer costs **six** requests per quote: each of those methods calls `buscar`, and `existe` and
`revisao_aplicada` do so for both document types.

The HANA query already returns that answer with the opportunity. This class hands it to
`montar_estado` without touching the state machine: it implements the same minimal interface as
the Service Layer repository, answering from memory.

The saving is large, but the reason to exist is keeping `montar_estado` and `decidir` ignorant
of where the data comes from: the decision cannot change because the read moved, and the
state-machine tests stay valid unchanged.
"""

from __future__ import annotations

from typing import Any

from wbcpython.infrastructure.service_layer.documentos import (
    UDF_CONGELADO,
    TipoDocumento,
    esta_congelado_pelo_campo,
    esta_fechado_pelo_status,
)

#: Chave usada pelo repositório do HANA para cada tipo de documento.
_CHAVE = {TipoDocumento.COTACAO: "cotacao", TipoDocumento.PEDIDO: "pedido"}

#: UDF que guarda a revisão do WBC aplicada no documento.
UDF_REVISAO = "U_INO_VERSAOWBC"


class DocumentosJaLidos:
    """Answers about the documents from what the query already returned."""

    def __init__(self, documentos: dict[str, Any] | None = None) -> None:
        self._documentos = documentos or {}

    @classmethod
    def da_oportunidade(cls, oportunidade: dict[str, Any]) -> DocumentosJaLidos:
        """Extracts the ``documentos`` block the HANA repository attaches."""
        return cls(oportunidade.get("documentos"))

    def buscar(self, tipo: TipoDocumento, orcamento: str) -> dict[str, Any] | None:
        del orcamento  # a consulta já filtrou pelo orçamento
        return self._documentos.get(_CHAVE[tipo])

    def existe(self, tipo: TipoDocumento, orcamento: str) -> bool:
        return self.buscar(tipo, orcamento) is not None

    def revisao_aplicada(self, tipo: TipoDocumento, orcamento: str) -> str:
        """Revision stored on the document, or ``''`` when there is no document.

        Empty string is how the state machine represents "no revision" (same contract as the
        Service Layer repository).
        """
        documento = self.buscar(tipo, orcamento)
        if not documento:
            return ""
        return str(documento.get(UDF_REVISAO) or "")

    def parceiro_aplicado(self, tipo: TipoDocumento, orcamento: str) -> str:
        """`CardCode` of the current document, empty when there is none."""
        documento = self.buscar(tipo, orcamento)
        return str(documento.get("CardCode") or "") if documento else ""

    def esta_fechado(self, tipo: TipoDocumento, orcamento: str) -> bool:
        """`DocStatus = 'C'`: the SAP accepts neither changes nor cancellation.

        `False` when there is no document: "does not exist" is not "is closed", and mixing them up
        would block **creating** the first order. An **unknown** status, however, is treated as
        closed: see `esta_fechado_pelo_status`, where the policy lives.
        """
        documento = self.buscar(tipo, orcamento)
        if not documento:
            return False
        return esta_fechado_pelo_status(
            documento.get("DocStatus"), documento=f"{tipo.rotulo} de {orcamento}"
        )

    def esta_congelado(self, tipo: TipoDocumento, orcamento: str) -> bool:
        """`U_INO_Congelado = 'Y'`: the order lines must not be rebuilt.

        Same question the Service Layer answers through `buscar`, here from what the HANA query
        already returned; the translation policy lives in `esta_congelado_pelo_campo`.
        """
        documento = self.buscar(tipo, orcamento)
        if not documento:
            return False
        return esta_congelado_pelo_campo(documento.get(UDF_CONGELADO))

    def doc_entry(self, tipo: TipoDocumento, orcamento: str) -> int | None:
        documento = self.buscar(tipo, orcamento)
        return int(documento["DocEntry"]) if documento else None

    def ops_do_pedido(self, orcamento: str) -> tuple[int, int] | None:
        """Live OPs of the current order: ``(planned, released or closed)``.

        ``(0, 0)`` when there is no order — nothing can be orphaned. ``None`` when the
        order came without the count (a read that predates it): unknown, never zero,
        because a guard that reads "no OPs" from missing data would cancel an order
        that production holds.
        """
        documento = self.buscar(TipoDocumento.PEDIDO, orcamento)
        if not documento:
            return (0, 0)
        ops = documento.get("OPs")
        if not isinstance(ops, dict):
            return None
        return (int(ops.get("planejadas") or 0), int(ops.get("em_producao") or 0))
