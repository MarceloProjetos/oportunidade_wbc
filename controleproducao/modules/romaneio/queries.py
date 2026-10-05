"""Queries do módulo Romaneio, transcritas de `Controllers/Querys.resx`. Todas em HANA."""

OPS_PEDIDO_TST = """
SELECT 'N' "Selecionar", T1."ItemCode", T2."ItemName" "Semiacabado", R1."ItemCode" "Item de Origem",
       R1."U_INO_ORCITM" "Código de Origem",
       sum(T1."CmpltQty") - IFNULL(
           (SELECT sum(Q1."Quantity") FROM OITT Q0 INNER JOIN ITT1 Q1 ON Q0."Code" = Q1."Father"
            WHERE SUBSTR_AFTER(SUBSTR_AFTER(SUBSTR_AFTER(Q0."Code",'_'),'_'),'_') = '{doc_num}'
              AND Q1."Code" = T1."ItemCode"), 0) "Qtde. Disponível"
FROM "ORDR" T0
  INNER JOIN "RDR1" R1 ON T0."DocEntry" = R1."DocEntry"
  LEFT OUTER JOIN OWOR T1 ON T0."CardCode" = T1."CardCode" AND T0."DocNum" = T1."OriginNum"
  LEFT OUTER JOIN "OITM" T2 ON T1."ItemCode" = T2."ItemCode"
WHERE T0."DocNum" = '{doc_num}' AND T0."U_INO_EntregaMultipla" != 'N'
  AND T1."Status" != 'C' AND T1."CmpltQty" > 0 AND T1."U_INO_LinhaRef" = R1."U_INO_ORCITM"
GROUP BY T1."ItemCode", T2."ItemName", R1."ItemCode", R1."U_INO_ORCITM"
ORDER BY R1."U_INO_ORCITM"
""".strip()

DOC_ENTRY_PED = 'SELECT top 1 T0."DocEntry" FROM "ORDR" T0 WHERE T0."DocNum" = \'{doc_num}\''

LISTA_PED = 'SELECT T1."WhsCode" FROM ORDR T0 INNER JOIN RDR1 T1 ON T0."DocEntry" = T1."DocEntry" WHERE T0."DocNum" = \'{doc_num}\''

LINHAS_PED = 'SELECT top 1 T1."ItemCode", T1."Dscription", T2."NCMCode" FROM "ORDR" T0 LEFT JOIN "RDR1" T1 ON T0."DocEntry" = T1."DocEntry" INNER JOIN OITM T2 ON T1."ItemCode" = T2."ItemCode" WHERE T0."DocNum" = \'{doc_num}\' AND T1."ItemCode" = \'{item_code}\''

LINHAS_PED2 = 'SELECT Count(*) FROM "ORDR" T0 LEFT JOIN "RDR1" T1 ON T0."DocEntry" = T1."DocEntry" WHERE T0."DocNum" = \'{doc_num}\''

QTD_TOTAL = """
SELECT (((SELECT sum(T1."Quantity") FROM "ITT1" T1 WHERE T1."Father" = '{item_code}') / sum(T0."CmpltQty")))
       * (SELECT top 1 T1."Weight1" FROM ORDR T0 INNER JOIN RDR1 T1 ON T0."DocEntry" = T1."DocEntry"
          WHERE T0."DocNum" = '{doc_num}' AND T1."U_INO_ORCITM" = '{linha_origem}' ORDER BY T1."LineNum")
FROM OWOR T0
WHERE T0."OriginNum" = '{doc_num}' AND T0."U_INO_LinhaRef" = '{linha_origem}' AND T0."Status" != 'C'
""".strip()

LINHA_ORIG = 'SELECT top 1 T1."VisOrder" FROM ORDR T0 INNER JOIN RDR1 T1 ON T0."DocEntry" = T1."DocEntry" WHERE T0."DocNum" = \'{doc_num}\' AND T1."U_INO_ORCITM" = \'{origem}\' ORDER BY T1."LineNum"'

OP_RESTANTE = 'SELECT * FROM OWOR T0 WHERE T0."CmpltQty" < T0."PlannedQty" AND T0."OriginNum" = {doc_num} AND T0."Status" not in (\'L\',\'C\')'

PRECO_LISTA_ITEM = 'SELECT T1."AvgPrice" FROM "OITW" T1 INNER JOIN OWHS T2 ON T1."WhsCode" = T2."WhsCode" WHERE T1."WhsCode" = \'{whs_code}\' AND T1."ItemCode" = \'{item_code}\''
