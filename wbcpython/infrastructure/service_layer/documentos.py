"""Quotations (`Quotations`) and sales orders (`Orders`) over the Service Layer.

Replaces the legacy DI-API `GetBusinessObject(oQuotations/oOrders)`.

The link between the SAP document and the WBC quotation is the UDF **`U_INO_COTWBC`** — the
legacy counts quotations and orders by it (`GetIntIdWBCOrcamentos`, `GetIdOrcamentosPedido`),
and it is the key that makes the integration idempotent: check whether one exists before creating.

`U_INO_VERSAOWBC` holds the WBC revision applied to the document; the state machine compares it
to decide between updating and leaving the document frozen.

**Cancelled documents are ignored** in every search (`Cancelled eq 'tNO'`), mirroring the
legacy `CANCELED = 'N'`: a cancelled document does not count as existing, otherwise the
integration would never recreate what was voided.
"""

from __future__ import annotations

import logging
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Any, Protocol

from wbcpython.infrastructure.service_layer.client import ServiceLayerClient

logger = logging.getLogger(__name__)

UDF_ORCAMENTO = "U_INO_COTWBC"
UDF_REVISAO = "U_INO_VERSAOWBC"

#: `U_INO_Congelado = 'Y'` no pedido: **as linhas não podem ser refeitas**.
#:
#: É o campo que o legado lê em `UpdatePedido` (`ServiceProcess.cs:585`). Sem
#: ele, a atualização apaga todas as linhas do pedido e as reconstrói a partir
#: do orçamento — e leva junto qualquer edição manual (item trocado, quantidade
#: ajustada, texto adicional).
#:
#: Protege **só as linhas**: no legado o cabeçalho é gravado fora do `if`, e o
#: `Update()` acontece depois dele.
UDF_CONGELADO = "U_INO_Congelado"


class TipoDocumento(Enum):
    """The two sales documents the integration creates."""

    COTACAO = "Quotations"
    PEDIDO = "Orders"

    @property
    def rotulo(self) -> str:
        return "cotação" if self is TipoDocumento.COTACAO else "pedido"


class RepositorioDocumentosVenda(Protocol):
    def buscar(self, tipo: TipoDocumento, orcamento: str) -> dict[str, Any] | None: ...

    def existe(self, tipo: TipoDocumento, orcamento: str) -> bool: ...

    def parceiro_aplicado(self, tipo: TipoDocumento, orcamento: str) -> str: ...

    def esta_fechado(self, tipo: TipoDocumento, orcamento: str) -> bool: ...

    def esta_congelado(self, tipo: TipoDocumento, orcamento: str) -> bool: ...

    def total_das_linhas(self, tipo: TipoDocumento, doc_entry: int) -> Decimal: ...

    def criar(self, tipo: TipoDocumento, dados: dict[str, Any]) -> dict[str, Any]: ...

    def atualizar(self, tipo: TipoDocumento, doc_entry: int, dados: dict[str, Any]) -> None: ...

    def atualizar_pesos(
        self,
        tipo: TipoDocumento,
        doc_entry: int,
        pesos_por_linha: dict[int, float],
        *,
        etag: str | None = None,
    ) -> None: ...

    def estado_das_linhas(
        self, tipo: TipoDocumento, doc_entry: int
    ) -> tuple[str | None, dict[int, tuple[float, float]]]: ...

    def buscar_por_docnum(self, tipo: TipoDocumento, docnum: int) -> dict[str, Any] | None: ...

    def cancelar(self, tipo: TipoDocumento, doc_entry: int) -> None: ...


#: Status que significam "aberto", nas duas fontes que a integração lê.
#:
#: O Service Layer devolve `bost_Open`/`bost_Close` no campo `DocumentStatus`;
#: a consulta do HANA devolve `'O'`/`'C'` na coluna `DocStatus`. São vocabulários
#: diferentes para o mesmo fato, e este é o único lugar que conhece os dois.
_ABERTO = frozenset({"O", "BOST_OPEN"})
_FECHADO = frozenset({"C", "BOST_CLOSE"})


#: SHA-1 of "1": what the .11's Service Layer returns as the ETag of every order (05/10/2026),
#: so it does not change when the order is saved and `If-Match` protects nothing.
ETAG_FIXA = 'W/"356A192B7913B04C54574D18C28D46E6395428AB"'


def esta_congelado_pelo_campo(bruto: Any) -> bool:
    """Translates the order's `U_INO_Congelado`. Only `'Y'` freezes.

    Follows the legacy to the letter: `if (Congelado != "Y")` rebuilds the lines
    (`ServiceProcess.cs:597`). Empty, null or any other value does **not** freeze.

    Unlike `DocStatus`, there is no way to fail closed here without diverging: "fail frozen" on an
    unknown value would stop updating lines in a case the legacy updates. In production this is
    academic — all 2,581 current orders are `'Y'` — but the divergence would be silent, and that is
    the kind nobody finds later.
    """
    return str(bruto or "").strip().upper() == "Y"


def esta_fechado_pelo_status(bruto: Any, *, documento: str) -> bool:
    """Translates the document status, **failing closed** on unknown values.

    A value that is neither open nor closed (empty, null, a column that vanished from the query, new
    SAP vocabulary) returns `True`: the document is treated as closed and the integration does not
    write to it.

    Deliberate, and consistent with the rest of the project — `safety.py` and `PAINEL_SENHA` also
    block when configuration is incomplete. It weighs more here: quotation `00124268`'s defect was
    exactly a column that **was not read**, and a fail-open guard would evaporate silently the day
    the column vanished again — no test failing, and writes to a closed order resuming.

    Measured cost: none. Grouping by status in both environments only `'O'` and `'C'` exist
    (146+2,424 in homologation, 116+2,452 in production), so no row takes this path today. The
    warning makes visible the day one does.
    """
    valor = str(bruto or "").strip().upper()
    if valor in _FECHADO:
        return True
    if valor in _ABERTO:
        return False
    logger.warning(
        "%s: status %r não reconhecido. Tratando como FECHADO por segurança — a "
        "integração não vai alterar nem cancelar este documento. Valores esperados: "
        "%s (aberto) ou %s (fechado).",
        documento,
        str(bruto or ""),
        "/".join(sorted(_ABERTO)),
        "/".join(sorted(_FECHADO)),
    )
    return True


def _escapar(valor: str) -> str:
    return valor.replace("'", "''")


class RepositorioDocumentosVendaServiceLayer:
    """Service Layer implementation."""

    def __init__(self, cliente: ServiceLayerClient) -> None:
        self._cliente = cliente

    # -------------------------------------------------------------- leitura

    def buscar(self, tipo: TipoDocumento, orcamento: str) -> dict[str, Any] | None:
        """Most recent non-cancelled document for the quotation, or `None`."""
        registros = self._cliente.listar(
            tipo.value,
            filtro=(f"{UDF_ORCAMENTO} eq '{_escapar(orcamento)}' and Cancelled eq 'tNO'"),
            ordenar_por="DocEntry desc",
            top=1,
        )
        return registros[0] if registros else None

    def existe(self, tipo: TipoDocumento, orcamento: str) -> bool:
        """Equivalent to the legacy queries' `COUNT(*) > 0`."""
        return self.buscar(tipo, orcamento) is not None

    def revisao_aplicada(self, tipo: TipoDocumento, orcamento: str) -> str:
        """WBC revision already stored on the document (`U_INO_VERSAOWBC`).

        Returns an empty string when there is no document or the field is blank — how the state machine
        represents "no revision".
        """
        documento = self.buscar(tipo, orcamento)
        if not documento:
            return ""
        return str(documento.get(UDF_REVISAO) or "")

    def parceiro_aplicado(self, tipo: TipoDocumento, orcamento: str) -> str:
        """`CardCode` of the current document — empty when there is none.

        The legacy `ChecaPNPedido`: comparing the order's partner with `U_INO_PN_Correc` tells whether
        the swap is still pending.
        """
        documento = self.buscar(tipo, orcamento)
        return str(documento.get("CardCode") or "") if documento else ""

    def esta_fechado(self, tipo: TipoDocumento, orcamento: str) -> bool:
        """Closed document: SAP accepts neither changes nor cancellation.

        Here the field is `DocumentStatus` (`bost_Open`/`bost_Close`); the HANA query returns the same
        fact as `DocStatus` (`'O'`/`'C'`). `esta_fechado_pelo_status` knows both vocabularies.

        `False` when there is no document: "does not exist" is not "is closed", and conflating them
        would block creating the first order.
        """
        documento = self.buscar(tipo, orcamento)
        if not documento:
            return False
        return esta_fechado_pelo_status(
            documento.get("DocumentStatus"), documento=f"{tipo.rotulo} de {orcamento}"
        )

    def esta_congelado(self, tipo: TipoDocumento, orcamento: str) -> bool:
        """Order with protected lines (`U_INO_Congelado = 'Y'`).

        `False` without a document: no order, no line to protect — and creation never reads this field,
        in the legacy or here.
        """
        documento = self.buscar(tipo, orcamento)
        if not documento:
            return False
        return esta_congelado_pelo_campo(documento.get(UDF_CONGELADO))

    def doc_entry(self, tipo: TipoDocumento, orcamento: str) -> int | None:
        documento = self.buscar(tipo, orcamento)
        return int(documento["DocEntry"]) if documento else None

    # -------------------------------------------------------------- escrita

    def criar(self, tipo: TipoDocumento, dados: dict[str, Any]) -> dict[str, Any]:
        """Creates the document. The environment safety lock is applied by the client.

        Raises:
            ValueError: `U_INO_COTWBC` missing — the document would be orphaned and duplicated on the
                next run.
        """
        if not dados.get(UDF_ORCAMENTO):
            raise ValueError(
                f"{UDF_ORCAMENTO} é obrigatório ao criar {tipo.rotulo}: sem ele o "
                f"documento fica órfão e a integração não consegue reencontrá-lo, "
                f"o que levaria a duplicá-lo na próxima execução."
            )

        resposta = self._cliente.post(tipo.value, json=dados)
        criado = resposta.json()
        logger.info(
            "%s criada: DocEntry=%s DocNum=%s orçamento=%s",
            tipo.rotulo.capitalize(),
            criado.get("DocEntry"),
            criado.get("DocNum"),
            dados.get(UDF_ORCAMENTO),
        )
        return criado

    def atualizar(self, tipo: TipoDocumento, doc_entry: int, dados: dict[str, Any]) -> None:
        """Partial update (`PATCH`) that really replaces the lines.

        On its own the SL `PATCH` does **not** replace the lines collection, it merges: lines sent
        without `LineNum` are appended and existing ones stay, values included.

        Observed in homologation, not deduced: quotation 101857 (WBC quotation `00125527`) ended an
        update with **three** lines (R$ 69,656.20) while the WBC quotation has two (R$ 52,079.03). The
        extra line came from an earlier revision and nobody removed it — wrong money on a document that
        goes to the customer.

        `B1S-ReplaceCollectionsOnPatch` is the SL header for this: the sent collection replaces the
        existing one. It is not always honoured (204 with the lines unchanged, quotations `00125616` and
        `00125577` — see `total_das_linhas`), hence the post-update check.

        **Two `PATCH`es, and the order matters** (measured in homologation, 15/09/2026, quotation
        101977 — `DECISOES.md`, "Preço unitário e desconto no `PATCH`"):

        * Sending only `LineTotal` on a line that **already existed** makes SAP keep the previous
          revision's `UnitPrice` and close the sum with `DiscountPercent`. That is how quotation 78264
          (production) came out as "R$ 3,088.86 with 46.24% discount" on a R$ 1,660.66 line — right
          total, wrong unit price and discount on the customer's printout.
        * Sending `UnitPrice` **changes** the unit price, but SAP recomputes `LineTotal` as
          `UnitPrice × Quantity` and ignores the one sent: with 4 decimals on the price a cent comes
          back (`272 × 2600.0071 = 707,201.93` vs `707,201.92`).
        * Sending only `LineTotal` on a line whose `UnitPrice` **already is** right makes SAP honour
          `LineTotal`, with discount 0.0000.

        Hence step 1 writes `UnitPrice = LineTotal ÷ Quantity` (4 decimals) and step 2 rewrites the
        lines as received, with `LineTotal` only. A new line (creation, `POST`) needs none of this: it
        is born with the derived unit price and zero discount — which is why `criar` does not go through
        here.

        **The revision stamp (`U_INO_VERSAOWBC`) goes in step 2 only.** If step 1 writes and step 2
        fails (timeout, network), the document keeps totals recomputed from `UnitPrice` (the cent is
        back); with the revision already stamped, the next cycle would read "frozen revision" and never
        touch it again. Stamping last, a mid-way failure leaves the old revision in SAP and the next
        cycle redoes both steps. Without `DocumentLines` only the single PATCH is sent.
        """
        linhas = dados.get("DocumentLines")
        if linhas:
            passo1 = {k: v for k, v in dados.items() if k != UDF_REVISAO}
            passo1["DocumentLines"] = [_com_unitario(linha) for linha in linhas]
            self._cliente.patch(
                f"{tipo.value}({doc_entry})",
                json=passo1,
                headers={"B1S-ReplaceCollectionsOnPatch": "true"},
            )
        self._cliente.patch(
            f"{tipo.value}({doc_entry})",
            json=dados,
            headers={"B1S-ReplaceCollectionsOnPatch": "true"},
        )
        logger.info("%s %s atualizada.", tipo.rotulo.capitalize(), doc_entry)

    def atualizar_pesos(
        self,
        tipo: TipoDocumento,
        doc_entry: int,
        pesos_por_linha: dict[int, float],
        *,
        etag: str | None = None,
    ) -> None:
        """Writes `Weight1` on specific lines, **without** touching the rest.

        With `etag` (from `estado_das_linhas`) the PATCH goes with `If-Match`: if someone saved the
        document since it was read, the SL answers 412 instead of writing.

        The difference from `atualizar` is the header that is **not** sent. With
        `B1S-ReplaceCollectionsOnPatch` the sent collection replaces the existing one: sending only the
        lines that have a weight would delete the others, and sending all of them with just `LineNum`
        and `Weight1` would zero whatever was not repeated.

        Without the header the `PATCH` **merges** — the very behaviour that hurts when updating a whole
        document (see `atualizar`) helps here: a line identified by `LineNum` gets the sent fields
        changed and keeps the rest. Item, price, text and warehouse stay as they were.
        """
        if not pesos_por_linha:
            return
        corpo = {
            "DocumentLines": [
                {"LineNum": linha, "Weight1": peso}
                for linha, peso in sorted(pesos_por_linha.items())
            ]
        }
        self._cliente.patch(
            f"{tipo.value}({doc_entry})", json=corpo, headers={"If-Match": etag} if etag else None
        )
        logger.info(
            "%s %s: peso gravado em %d linha(s).",
            tipo.rotulo.capitalize(),
            doc_entry,
            len(pesos_por_linha),
        )

    def estado_das_linhas(
        self, tipo: TipoDocumento, doc_entry: int
    ) -> tuple[str | None, dict[int, tuple[float, float]]]:
        """The document's ETag and each line's ``(Quantity, Weight1)``, in ONE read.

        Same GET on purpose: a weight PATCH only goes for the lines still as the decision saw
        them — a weight typed in between is never overwritten. That re-read is the real guard:
        the .11's Service Layer sends the same ETag for every order (`ETAG_FIXA`, measured on
        84454 and 84457, 05/10/2026), so its `If-Match` never fails. It is still sent, in case a
        future SL version computes a real one (v1 may send it as ``odata.etag``).
        """
        resposta = self._cliente.get(
            f"{tipo.value}({doc_entry})", params={"$select": "DocEntry,DocumentLines"}
        )
        try:
            corpo = resposta.json() or {}
        except ValueError:
            corpo = {}
        etag = (
            resposta.headers.get("ETag") or corpo.get("@odata.etag") or corpo.get("odata.etag") or None
        )
        linhas = {
            int(linha["LineNum"]): (float(linha.get("Quantity") or 0), float(linha.get("Weight1") or 0))
            for linha in corpo.get("DocumentLines") or ()
            if linha.get("LineNum") is not None
        }
        return etag, linhas

    def buscar_por_docnum(self, tipo: TipoDocumento, docnum: int) -> dict[str, Any] | None:
        """Document by the number people quote (`DocNum`), not by key.

        `DocEntry` is the SL key; `DocNum` is what shows on the SAP screen and in the e-mail of whoever
        asks for the correction. Operators should not have to translate one into the other by hand.
        """
        registros = self._cliente.listar(tipo.value, filtro=f"DocNum eq {int(docnum)}", top=1)
        return registros[0] if registros else None

    def cancelar(self, tipo: TipoDocumento, doc_entry: int) -> None:
        """Cancels through the dedicated action, equivalent to `Documents.Cancel()`."""
        self._cliente.post(f"{tipo.value}({doc_entry})/Cancel", json=None)
        logger.info("%s %s cancelada.", tipo.rotulo.capitalize(), doc_entry)

    # ------------------------------------------------------------ conveniência

    def total_das_linhas(self, tipo: TipoDocumento, doc_entry: int) -> Decimal:
        """Re-reads the document from SAP and sums the lines' `LineTotal`.

        Checks what **actually got stored**: a successful `PATCH` does not guarantee the lines changed —
        the SL answered `204 No Content` on documents whose lines stayed as they were (`DECISOES.md`,
        quotations `00125616` and `00125577`).

        Sums before tax, like `total_do_payload`, so both sides of the comparison are the same quantity
        (`DocTotal` includes tax and would not do). Reads the `LineTotal` SAP returns instead of
        `Quantity * Price`: since 15/09/2026 it is the total the integration sends, and the stored
        `Price` is derived with 4 decimals — multiplying it back would reintroduce the rounding cent
        the change removed.
        """
        documento = self._cliente.get_json(f"{tipo.value}({doc_entry})")
        total = Decimal(0)
        for linha in documento.get("DocumentLines") or ():
            total += Decimal(str(linha.get("LineTotal") or 0))
        return total

    def cancelar_e_recriar(
        self, tipo: TipoDocumento, orcamento: str, dados: dict[str, Any]
    ) -> dict[str, Any]:
        """Cancels the current document (if any) and creates the new one.

        The order matters: cancelling first keeps `buscar()` from finding two non-cancelled documents
        for the same quotation — an ambiguous state that would break idempotency on later runs.
        """
        atual = self.doc_entry(tipo, orcamento)
        if atual is not None:
            self.cancelar(tipo, atual)
        return self.criar(tipo, dados)


def _com_unitario(linha: dict[str, Any]) -> dict[str, Any]:
    """The line with `UnitPrice = LineTotal ÷ Quantity`, rounded to 4 decimals.

    4 because that is what SAP stores for the price; sending more leaves SAP to round for us,
    unannounced. A line without `LineTotal` or quantity is returned as is.
    """
    total = linha.get("LineTotal")
    quantidade = linha.get("Quantity")
    if total is None or not quantidade:
        return linha
    unitario = (Decimal(str(total)) / Decimal(str(quantidade))).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP
    )
    return {**linha, "UnitPrice": float(unitario)}
