"""Open integration orders that still have a line at quantity 1 — read only, for `wbcpython quantidades`.

Only the candidates: the decision (does the text say another quantity?) is made per order on the Service Layer
document, the same one that gets written. Same schema rule as `pesos_reescalados`: the company that receives the
writes (`SL_COMPANY_DB`).
"""

from __future__ import annotations

from typing import Any

from wbcpython.config import Settings
from wbcpython.infrastructure.hana.identificadores import citar_identificador
from wbcpython.infrastructure.hana.repository import _abrir_conexao_hdbcli

#: The text rule reached every line on 18/11/2026 (bf05f12). An order created from then on was born with the
#: quantity of its text, so a 1 there is the text's (or a person's), never the old rule's.
CRIADOS_ANTES_DE = "2026-11-18"


def pedidos_com_linha_em_um(settings: Settings, *, fabrica_de_conexao: Any = None) -> list[int]:
    """`DocNum` of open, non-cancelled WBC orders created before the rule, with an open integration line at 1."""
    s = citar_identificador(settings.service_layer.company_db, descricao="SL_COMPANY_DB")
    conexao = (fabrica_de_conexao or (lambda: _abrir_conexao_hdbcli(settings.hana)))()
    try:
        cursor = conexao.cursor()
        try:
            cursor.execute(
                f"""
SELECT DISTINCT R."DocNum"
FROM {s}."ORDR" R
INNER JOIN {s}."RDR1" L ON L."DocEntry" = R."DocEntry"
WHERE R."CANCELED" = 'N' AND R."DocStatus" = 'O' AND LENGTH(R."U_INO_COTWBC") > 0
  AND R."CreateDate" < ?
  AND L."LineStatus" = 'O' AND L."Quantity" = 1 AND LENGTH(L."U_INO_ORCITM") > 0
ORDER BY R."DocNum"
""",
                (CRIADOS_ANTES_DE,),
            )
            return [int(linha[0]) for linha in cursor.fetchall()]
        finally:
            cursor.close()
    finally:
        conexao.close()


def linhas_ja_maiores_que_um(settings: Settings, *, fabrica_de_conexao: Any = None) -> dict[int, set[int]]:
    """`DocNum -> LineNum`s of open lines at 1 that had a quantity above 1 in an earlier version (`ADO1`).

    Someone lowered them on purpose: 14 of the 15 found on 18/11/2026 were `vendas01` turning porta-paletes
    "82 Módulos" into 1 after the integration created them right. The command must never undo a person's choice.
    """
    s = citar_identificador(settings.service_layer.company_db, descricao="SL_COMPANY_DB")
    conexao = (fabrica_de_conexao or (lambda: _abrir_conexao_hdbcli(settings.hana)))()
    try:
        cursor = conexao.cursor()
        try:
            cursor.execute(
                f"""
SELECT DISTINCT R."DocNum", L."LineNum"
FROM {s}."ORDR" R
INNER JOIN {s}."RDR1" L ON L."DocEntry" = R."DocEntry"
INNER JOIN {s}."ADO1" A ON A."ObjType" = '17' AND A."DocEntry" = L."DocEntry" AND A."LineNum" = L."LineNum"
WHERE R."CANCELED" = 'N' AND R."DocStatus" = 'O' AND L."LineStatus" = 'O' AND L."Quantity" = 1
  AND A."Quantity" > 1
"""
            )
            mudadas: dict[int, set[int]] = {}
            for docnum, line_num in cursor.fetchall():
                mudadas.setdefault(int(docnum), set()).add(int(line_num))
            return mudadas
        finally:
            cursor.close()
    finally:
        conexao.close()
