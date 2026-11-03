"""Sales opportunities (`SalesOpportunities`) over the Service Layer.

Replaces, from the legacy: `UpdateStatusDoc` (mirror the WBC SitCode into SAP),
`cancelaFechaOportunidade` (close) and `AddCotacaoOportunidade` / `AddPedidoOportunidade`
(link the generated document to the opportunity).

Relevant fields, taken from the legacy query `GetDocNumOportunidades`:

* `SequentialNo` — opportunity key (in the database: `OpprId`)
* `StartDate` — opening date. **Beware:** the database column is `OpenDate`, but the SL entity
  exposes `StartDate`; using the column name returns `HTTP 400 — Property OpenDate is invalid`.
  A real mistake, caught only by querying the real environment.
* `U_ORCNUM_WBC` — quotation number in the WBC
* `U_INO_StatusWBC` — SitCode mirrored into SAP (text)
* `U_INO_Update` / `U_INO_PN_Correc` — flag a business-partner swap
* `U_INO_IntegrouWBC` — marks the opportunity as part of the integration
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from wbcpython.infrastructure.service_layer.client import ServiceLayerClient
from wbcpython.infrastructure.service_layer.documentos import TipoDocumento

logger = logging.getLogger(__name__)

#: Único valor de `BoSoOsStatus` que representa oportunidade encerrada sem
#: venda. Os outros dois são `sos_Open` e `sos_Sold`.
STATUS_PERDIDA = "sos_Missed"
STATUS_ABERTA = "sos_Open"

#: Estados em que o SAP **recusa** acrescentar estágio à oportunidade, com
#: `-1029 [SalesOpportunitiesLines.SequenceNo] Field cannot be updated`. A
#: mensagem não diz nada sobre estar fechada — ver `vincular_documento`.
STATUS_FECHADOS = frozenset({"sos_Missed", "sos_Sold"})

#: Documentos cujo vínculo baixa a marca `U_INO_Update`. Só o pedido: no legado,
#: as duas escritas de `U_INO_Update = "N"` estão dentro de
#: `AddPedidoOportunidade` (`ServiceProcess.cs:198` e `:210`) e nenhuma em
#: `AddCotacaoOportunidade`. Ver `vincular_documento`.
BAIXA_MARCA_DE_ATUALIZACAO = frozenset({TipoDocumento.PEDIDO})

#: `PercentageRate` do estágio, por tipo de documento. Não é enfeite: são os
#: valores que o legado grava (`AddCotacaoOportunidade` usa 0,
#: `AddPedidoOportunidade` usa 100) e os que aparecem nos vínculos reais.
PERCENTUAL_DO_ESTAGIO = {TipoDocumento.COTACAO: 0, TipoDocumento.PEDIDO: 100}

ENTIDADE = "SalesOpportunities"

#: Chave da oportunidade. No banco a coluna é `OpprId`, mas a entidade do
#: Service Layer a expõe como `SequentialNo` — usar o nome da coluna devolve
#: `HTTP 400 — Property OpprId is invalid`. Vale o mesmo para `OpenDate`, que
#: aqui é `StartDate`. Os dois erros só apareceram ao consultar o ambiente real.
CAMPO_CHAVE = "SequentialNo"

UDF_ORCAMENTO = "U_ORCNUM_WBC"
UDF_STATUS_WBC = "U_INO_StatusWBC"
UDF_ATUALIZAR = "U_INO_Update"

#: Estágio/tipo de documento na coleção de linhas da oportunidade.
#: 23 = Cotação, 17 = Pedido de Venda (ObjType do SAP; o legado usa 17 na
#: consulta que junta OPR1 com ORDR).
OBJ_TYPE = {TipoDocumento.COTACAO: 23, TipoDocumento.PEDIDO: 17}


class RepositorioOportunidades(Protocol):
    def atualizar_status(self, oppr_id: int, sitcode: int) -> None: ...

    def encerrar(self, oppr_id: int, sitcode: int) -> None: ...


class RepositorioOportunidadesServiceLayer:
    """Service Layer implementation."""

    def __init__(self, cliente: ServiceLayerClient) -> None:
        self._cliente = cliente

    # -------------------------------------------------------------- leitura
    # The cycle reads the opportunities from HANA only (`infrastructure/hana`, see
    # `host/worker.py`); the Service Layer readers it once had were removed on 01/10/2026.

    def por_id(self, oppr_id: int) -> dict[str, Any]:
        return self._cliente.get_json(f"{ENTIDADE}({oppr_id})")

    # -------------------------------------------------------------- escrita

    def atualizar_status(self, oppr_id: int, sitcode: int) -> None:
        """Mirrors the WBC SitCode into the opportunity's `U_INO_StatusWBC`.

        The SitCode is written as **text**: that is how the legacy reads it back (compares with `"30"`,
        `"40"`, `"55"`), and writing a number would make the next run's comparison fail silently.
        """
        self._cliente.patch(f"{ENTIDADE}({oppr_id})", json={UDF_STATUS_WBC: str(sitcode)})
        logger.info("Oportunidade %s: status WBC atualizado para %s.", oppr_id, sitcode)

    def encerrar(self, oppr_id: int, sitcode: int) -> None:
        """Closes/cancels the opportunity (SitCode 70, 90 or 99).

        Writes the status together with the closing so the reason stays readable in SAP.

        `STATUS_PERDIDA` is not a naming choice: the SL enum `BoSoOsStatus` only accepts `sos_Open`,
        `sos_Missed` and `sos_Sold`. The plausible `sos_Lost` does **not** exist and SAP answers
        `HTTP 400 | SAP -1013`. It is the same value the legacy uses
        (`oport.Status = BoSoOsStatus.sos_Missed`, `ServiceProcess.cs:91-122`).
        """
        self._cliente.patch(
            f"{ENTIDADE}({oppr_id})",
            json={"Status": STATUS_PERDIDA, UDF_STATUS_WBC: str(sitcode)},
        )
        logger.info("Oportunidade %s encerrada (SitCode %s).", oppr_id, sitcode)

    def vincular_documento(
        self,
        oppr_id: int,
        tipo: TipoDocumento,
        doc_entry: int,
        total: float | None = None,
    ) -> None:
        """Links the generated quotation/order to the opportunity.

        Equivalent to `AddCotacaoOportunidade`/`AddPedidoOportunidade`. The link lives in the
        opportunity's stages collection (`SalesOpportunitiesLines`), and the SL replaces the whole
        collection on PATCH — so the existing collection is read and the new stage is **appended**,
        never sent alone.

        Three things SAP requires, found only in the real environment:

        1. **`DocumentNumber` takes the `DocEntry`, not the `DocNum`.** The field name says otherwise,
           but the existing links in homologation leave no doubt: opportunity `5681` points to `9486`,
           the quotation's `DocEntry` (its `DocNum` is `4948`). The legacy does the same
           (`oport.Lines.DocumentNumber = Convert.ToInt32(DocEntryCotacao)`).
        2. **`MaxLocalTotal` must be greater than zero**, otherwise SAP refuses with
           `-5002 In "Potential Amount" field, enter number greater than 0 [OOPR.MaxSumLoc]`. The
           document total goes in; without a total, `1` (the legacy workaround).
        3. **`PercentageRate` per type**: 0 on the quotation, 100 on the order — see
           `PERCENTUAL_DO_ESTAGIO`.

        **A closed opportunity is reopened and closed again.** SAP refuses a new stage on a
        `sos_Sold`/`sos_Missed` opportunity with
        `-1029 [SalesOpportunitiesLines.SequenceNo] Field cannot be updated` — a message pointing at a
        field unrelated to the cause. The legacy solves it the same way (`AddPedidoOportunidade`):
        `sos_Open` + Update, add the line + Update, `sos_Sold` + Update. The difference: the legacy
        always reclassifies as `sos_Sold`; here the previous status is **restored**, whatever it was,
        so a lost opportunity that receives a document does not become won as a side effect. The
        restore runs in a `finally`, so a failed link never leaves it open.

        **`U_INO_Update` is cleared when the order is linked.** The field flags that the operator asked
        for a PN swap on the order; once the new order is saved there is nothing left to flag. It is a
        UDF of this entity (the opportunity in SAP, not the WBC) and is filled by hand. Legacy
        behaviour, order only: both writes of `U_INO_Update = "N"` are inside `AddPedidoOportunidade`
        (`ServiceProcess.cs:198` and `:210`), none in `AddCotacaoOportunidade`.

        The clearing goes in the **same** PATCH as the stage: splitting it would open a window where the
        flag is cleared and the link does not exist, losing the information that the values changed.
        """
        atual = self.por_id(oppr_id)
        status_original = str(atual.get("Status") or "")
        precisa_reabrir = status_original in STATUS_FECHADOS

        if precisa_reabrir:
            logger.info(
                "Oportunidade %s está %s: reaberta para receber o vínculo.",
                oppr_id,
                status_original,
            )
            self._cliente.patch(f"{ENTIDADE}({oppr_id})", json={"Status": STATUS_ABERTA})

        estagios = list(atual.get("SalesOpportunitiesLines") or [])

        estagios.append(
            {
                "DocumentType": OBJ_TYPE[tipo],
                "DocumentNumber": doc_entry,
                "MaxLocalTotal": total if total else 1,
                "PercentageRate": PERCENTUAL_DO_ESTAGIO[tipo],
            }
        )

        corpo: dict[str, Any] = {"SalesOpportunitiesLines": estagios}
        if tipo in BAIXA_MARCA_DE_ATUALIZACAO:
            corpo[UDF_ATUALIZAR] = "N"

        try:
            self._cliente.patch(f"{ENTIDADE}({oppr_id})", json=corpo)
        finally:
            # `finally`: se o vínculo falhar, a oportunidade **não** pode ficar
            # aberta por acidente. O estado dela é dado de negócio; o vínculo
            # que falhou é problema nosso.
            if precisa_reabrir:
                self._cliente.patch(f"{ENTIDADE}({oppr_id})", json={"Status": status_original})
        logger.info(
            "Oportunidade %s: %s DocEntry %s vinculada (total %s).",
            oppr_id,
            tipo.rotulo,
            doc_entry,
            total,
        )
