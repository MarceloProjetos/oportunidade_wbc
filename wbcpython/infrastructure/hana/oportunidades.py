"""Reads the opportunities straight from HANA in **one** query.

Why: the Service Layer returns 20 records per response and ignores `$top` above that.
Scanning the window cost ~90 paginated requests, plus four per quote just to learn whether a
quotation and an order already existed: about 900 SAP round trips for a 200-quote cycle. Here
it is **one** query: 1,785 opportunities with quotation and order already resolved, in 0.04 s.

**What the query reproduces.** The exact snapshot `montar_estado` used to get from the
Service Layer:

| Field | Source | What the Service Layer did |
|---|---|---|
| `U_INO_StatusWBC`, `U_INO_Update`, `U_INO_PN_Correc`, `CardCode` | `OOPR` | paginated listing |
| quotation: exists? which revision? | `OQUT` | `buscar(COTACAO)`, twice |
| order: exists? which revision? | `ORDR` | `buscar(PEDIDO)`, twice |

Checked against the Service Layer on 40 homologation quotes (no document, with quotation,
with order, with a revision applied): **zero divergences**.

The read also carries what the Service Layer path never had: the live production
orders of the current sales order (`OWOR` by `OriginAbs`, status P/R/L), so the
partner swap stops cancelling an order that production already holds
(`docs/PLANO_TROCA_PN_COM_OP.md`). On 20/10/2026 the swap had left 110 planned
OPs on two cancelled orders while their replacements got their own.

**The schema is the one of the company that receives the writes, not `HANA_SCHEMA`.** This
is the dangerous part of the module. `HANA_SCHEMA` points at the reporting views
(`SBOALTAMIRAPROD`, even in homologation); the company the integration writes to is
`SL_COMPANY_DB`. Reading the state of one and writing to the other would create documents in
homologation based on what production already has, silently and looking normal. Hence the
schema comes from `service_layer.company_db`, and
`tests/wbc/infrastructure/test_hana_oportunidades.py` pins that choice.

Read-only: every write still goes through the Service Layer, which applies SAP's business
rules.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from wbcpython.config import HanaSettings
from wbcpython.infrastructure.hana.identificadores import citar_identificador
from wbcpython.infrastructure.hana.repository import _abrir_conexao_hdbcli

logger = logging.getLogger(__name__)

#: Campos devolvidos com os mesmos nomes do Service Layer. Manter a nomenclatura
#: significa que `montar_estado` e o processador não precisam saber de onde o
#: dado veio — e que trocar a fonte não pode mudar a decisão.
CAMPOS = (
    "SequentialNo",
    "U_ORCNUM_WBC",
    "CardCode",
    "SalesPerson",
    "ContactPerson",
    "U_INO_StatusWBC",
    "U_INO_Update",
    "U_INO_PN_Correc",
)


def _sql(schema_citado: str) -> str:
    """Build the query; `ROW_NUMBER` picks the latest document per quote.

    Latest = highest `DocEntry` among the non-cancelled ones, exactly what the Service Layer
    did with `$orderby=DocEntry desc&$top=1` and `Cancelled eq 'tNO'`.
    """
    return f"""
SELECT O."OpprId", O."U_ORCNUM_WBC", O."CardCode", C."CardName", O."SlpCode", O."CprCode",
       O."OpenDate", O."Status", O."U_INO_StatusWBC", O."U_INO_Update", O."U_INO_PN_Correc",
       Q."DocEntry" AS "COT_DOCENTRY", Q."DocNum" AS "COT_DOCNUM",
       Q."U_INO_VERSAOWBC" AS "COT_REVISAO", Q."DocStatus" AS "COT_STATUS",
       R."DocEntry" AS "PED_DOCENTRY", R."DocNum" AS "PED_DOCNUM",
       R."U_INO_VERSAOWBC" AS "PED_REVISAO",
       R."CardCode" AS "PED_CARDCODE", R."DocStatus" AS "PED_STATUS",
       R."U_INO_Congelado" AS "PED_CONGELADO",
       IFNULL(W."PLANEJADAS", 0) AS "PED_OPS_PLANEJADAS",
       IFNULL(W."EM_PRODUCAO", 0) AS "PED_OPS_EM_PRODUCAO"
FROM {schema_citado}."OOPR" O
LEFT JOIN {schema_citado}."OCRD" C ON C."CardCode" = O."CardCode"
LEFT JOIN (
    SELECT "U_INO_COTWBC", "DocEntry", "DocNum", "U_INO_VERSAOWBC", "DocStatus",
           ROW_NUMBER() OVER (
               PARTITION BY "U_INO_COTWBC" ORDER BY "DocEntry" DESC
           ) AS "RN"
    FROM {schema_citado}."OQUT"
    WHERE "CANCELED" = 'N' AND LENGTH("U_INO_COTWBC") > 0
) Q ON Q."U_INO_COTWBC" = O."U_ORCNUM_WBC" AND Q."RN" = 1
LEFT JOIN (
    SELECT "U_INO_COTWBC", "DocEntry", "DocNum", "U_INO_VERSAOWBC", "CardCode", "DocStatus",
           "U_INO_Congelado",
           ROW_NUMBER() OVER (
               PARTITION BY "U_INO_COTWBC" ORDER BY "DocEntry" DESC
           ) AS "RN"
    FROM {schema_citado}."ORDR"
    WHERE "CANCELED" = 'N' AND LENGTH("U_INO_COTWBC") > 0
) R ON R."U_INO_COTWBC" = O."U_ORCNUM_WBC" AND R."RN" = 1
LEFT JOIN (
    SELECT "OriginAbs",
           SUM(CASE WHEN "Status" = 'P' THEN 1 ELSE 0 END) AS "PLANEJADAS",
           SUM(CASE WHEN "Status" IN ('R', 'L') THEN 1 ELSE 0 END) AS "EM_PRODUCAO"
    FROM {schema_citado}."OWOR"
    WHERE "Status" IN ('P', 'R', 'L')
    GROUP BY "OriginAbs"
) W ON W."OriginAbs" = R."DocEntry"
WHERE O."U_INO_IntegrouWBC" = 'Y'
  AND LENGTH(O."U_ORCNUM_WBC") > 0
  AND O."OpenDate" >= ?
  AND (? IS NULL OR O."U_ORCNUM_WBC" = ?)
ORDER BY O."OpprId" DESC
"""


class RepositorioOportunidadesHana:
    """Window opportunities with their quotation/order already resolved."""

    def __init__(
        self,
        settings: HanaSettings,
        *,
        company_db: str,
        fabrica_de_conexao: Any = None,
    ) -> None:
        self._settings = settings
        # Ver a nota do módulo: o schema é o da company que recebe a escrita.
        self._schema = citar_identificador(company_db, descricao="SL_COMPANY_DB")
        self._fabrica = fabrica_de_conexao or (lambda: _abrir_conexao_hdbcli(settings))
        self._conexao: Any = None

    def _obter_conexao(self) -> Any:
        if self._conexao is None:
            self._conexao = self._fabrica()
        return self._conexao

    def close(self) -> None:
        if self._conexao is not None:
            try:
                self._conexao.close()
            except Exception as exc:  # noqa: BLE001 - encerramento não pode explodir
                logger.warning("Falha ao encerrar a conexão com o HANA: %s", exc)
            finally:
                self._conexao = None

    def pendentes_de_integracao(
        self, *, desde: date, orcamento: str | None = None
    ) -> list[dict[str, Any]]:
        """The whole window in one query; ``orcamento`` narrows it to one quote number.

        There is no read cap: reading everything costs hundredths of a second. The cap that
        matters is the **write** one, and it lives in the worker.
        """
        cursor = self._obter_conexao().cursor()
        try:
            cursor.execute(_sql(self._schema), (desde, orcamento, orcamento))
            colunas = [descricao[0] for descricao in cursor.description]
            linhas = cursor.fetchall()
        finally:
            cursor.close()

        return [self._para_dicionario(dict(zip(colunas, linha, strict=False))) for linha in linhas]

    def datas_de_abertura(self, orcnums: list[str]) -> dict[str, Any]:
        """`OpenDate` per quote, **without** the window filter.

        Backfills the tracking history: rows saved before the column existed have no date, and
        rows that already left the window are never checked again, so they would stay null
        forever, and a null date passes any window filter. Without the backfill the dashboard's
        cutoff would never apply to them.
        """
        if not orcnums:
            return {}
        marcadores = ", ".join("?" for _ in orcnums)
        sql = (
            f'SELECT "U_ORCNUM_WBC", "OpenDate" FROM {self._schema}."OOPR" '
            f'WHERE "U_ORCNUM_WBC" IN ({marcadores})'
        )
        cursor = self._obter_conexao().cursor()
        try:
            cursor.execute(sql, tuple(orcnums))
            linhas = cursor.fetchall()
        finally:
            cursor.close()
        return {str(linha[0]).strip(): linha[1] for linha in linhas if linha[0]}

    @staticmethod
    def _para_dicionario(campos: dict[str, Any]) -> dict[str, Any]:
        """Translate the row into the Service Layer shape.

        `documentos` travels pre-resolved; that is what saves the four SAP round trips per
        quote that existed only to answer "is there a quotation already?".

        Columns are read **by name**, never by position. The earlier version used `linha[0]`,
        `linha[1]`... and it bit the first time a column was added in the middle of the
        `SELECT`: every following field shifted by one, silently. `U_INO_StatusWBC` received
        `Status`, `Update` received the SitCode, and the cycle kept running with no error,
        deciding on a swapped snapshot.
        """
        return {
            "SequentialNo": campos["OpprId"],
            "U_ORCNUM_WBC": str(campos["U_ORCNUM_WBC"] or "").strip(),
            "CardCode": str(campos["CardCode"] or ""),
            # Apresentação apenas: nenhuma decisão olha o nome do parceiro. Ele
            # viaja junto porque o painel e a prévia precisam identificar o
            # cliente, e buscá-lo depois custaria uma ida por orçamento.
            "CardName": str(campos["CardName"] or ""),
            "SalesPerson": campos["SlpCode"],
            # A data que define a janela. Viaja junto para que o acompanhamento
            # possa guardá-la: sem ela, o painel não tem como aplicar o mesmo
            # corte que o worker aplica, e passa a mostrar oportunidades que o
            # ciclo já não olha mais — foi exatamente o que aconteceu quando a
            # janela encolheu de 6 para 3 meses.
            "OpenDate": campos["OpenDate"],
            "ContactPerson": campos["CprCode"],
            "Status": str(campos["Status"] or ""),
            "U_INO_StatusWBC": str(campos["U_INO_StatusWBC"] or ""),
            "U_INO_Update": str(campos["U_INO_Update"] or ""),
            "U_INO_PN_Correc": str(campos["U_INO_PN_Correc"] or ""),
            "documentos": {
                "cotacao": _documento(
                    campos["COT_DOCENTRY"],
                    campos["COT_REVISAO"],
                    docnum=campos["COT_DOCNUM"],
                    status=campos["COT_STATUS"],
                ),
                "pedido": _documento(
                    campos["PED_DOCENTRY"],
                    campos["PED_REVISAO"],
                    campos["PED_CARDCODE"],
                    docnum=campos["PED_DOCNUM"],
                    status=campos["PED_STATUS"],
                    # Campo só do pedido: `U_INO_Congelado` é UDF do `ORDR`, e
                    # a cotação não o tem. Fica no chamador, e não em
                    # `_documento`, para a cotação não ganhar uma chave vazia
                    # que sugere um campo que não existe nela.
                    extras={
                        "U_INO_Congelado": str(campos["PED_CONGELADO"] or ""),
                        # Cancelled OPs are left out by the query: they hold nothing.
                        # Released and closed travel together because neither can be
                        # cancelled without a person (issued material, posted receipts).
                        "OPs": {
                            "planejadas": int(campos["PED_OPS_PLANEJADAS"] or 0),
                            "em_producao": int(campos["PED_OPS_EM_PRODUCAO"] or 0),
                        },
                    },
                ),
            },
        }


def _documento(
    doc_entry: Any,
    revisao: Any,
    parceiro: Any = None,
    *,
    docnum: Any = None,
    status: Any = None,
    extras: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Document summary, or `None` when there is no document (same as the Service Layer).

    `CardCode` is the equivalent of the legacy `ChecaPNPedido`: comparing the partner the
    order **already has** with `U_INO_PN_Correc` tells whether the swap is still pending; see
    `EstadoIntegracao.troca_de_parceiro_pendente`.

    `DocStatus` is `'O'` (open) or `'C'` (closed). SAP accepts neither changes nor
    cancellation on a closed document and the integration must know **before** trying; see
    `DocumentosJaLidos.esta_fechado`.

    The column is kept for both types because `esta_fechado(tipo, ...)` answers for both: it
    is not dead data, just data today's rules do not ask about. They only query the order;
    there are 3 closed quotations in each environment and handling them later needs no query
    change; see `DECISOES.md`.

    `extras` carries what belongs to one type only. Today that is `U_INO_Congelado`, a UDF of
    `ORDR` the quotation lacks: `'Y'` protects the order's lines from being redone; see
    `DocumentosJaLidos.esta_congelado`.
    """
    if doc_entry is None:
        return None
    return {
        "DocEntry": int(doc_entry),
        # `DocNum` é o número que as pessoas citam; `DocEntry` é a chave que o
        # Service Layer usa. Os dois viajam: um para a tela, outro para a API.
        "DocNum": None if docnum is None else int(docnum),
        "U_INO_VERSAOWBC": str(revisao or ""),
        "CardCode": str(parceiro or ""),
        "DocStatus": str(status or ""),
        **(extras or {}),
    }
