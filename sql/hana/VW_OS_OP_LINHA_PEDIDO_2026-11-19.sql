-- VW_OS_OP_LINHA_PEDIDO — 19/11/2026. Run in SAP HANA (SBOALTAMIRAPROD) by the owner; nothing here runs it.
-- Only this file: VW_OS_INTEGRACAO and VW_OS_PINTURA_V0 read this view and need no change.
--
-- Same view as VW_OS_OP_LINHA_PEDIDO_2026-11-12.sql (the rules are described there) plus ONE exception:
-- the 69 OPs of order 84537 that were closed by mistake on 18/11/2026 and then recreated no longer
-- count as OPs of the order. The OS was listing 14 of them next to the 14 new ones (30 extra rows,
-- most of them matched by position against the new quote detail, i.e. the wrong item).
-- The exception is the last line (DocEntry 165921..165990 = the old OPs; the new ones are 166170..166183).
-- No comment inside the statement: the first run (19/11 11:15) recreated the view WITHOUT the exception.
-- After running: synchronise the OS of order 84537 so the copy picks it up.
-- Rollback: run sql/hana/VW_OS_OP_LINHA_PEDIDO_2026-11-12.sql
CREATE OR REPLACE VIEW "SBOALTAMIRAPROD"."VW_OS_OP_LINHA_PEDIDO" (
	 "OPEntry",
	 "PedidoEntry",
	 "LineNum",
	 "OPPai" ) AS SELECT
	 O."DocEntry" AS "OPEntry",
	 P."DocEntry" AS "PedidoEntry",
	 R."LineNum" AS "LineNum",
	 CASE WHEN EXISTS (SELECT
	 1
		FROM RDR1 X
		WHERE X."DocEntry" = P."DocEntry"
		AND X."U_INO_OP" = O."DocEntry")
THEN 'Y'
WHEN O."Status" IN ('P',
	 'R')
	AND EXISTS (SELECT
	 1
		FROM RDR1 X
		WHERE X."DocEntry" = P."DocEntry"
		AND X."LineStatus" = 'C'
		AND IFNULL(X."U_INO_OP",
	 0) = 0
		AND X."ItemCode" = O."ItemCode"
		AND X."U_INO_ORCITM" = O."U_INO_LinhaRef")
THEN 'Y'
WHEN I."ItmsGrpCod" = 325
	AND NOT EXISTS (SELECT
	 1
		FROM RDR1 X
		WHERE X."DocEntry" = P."DocEntry"
		AND X."U_INO_ORCITM" = O."U_INO_LinhaRef")
THEN 'Y'
ELSE 'N'
END AS "OPPai"
FROM OWOR O
INNER JOIN ORDR P ON O."OriginNum" = P."DocNum"
INNER JOIN OITM I ON O."ItemCode" = I."ItemCode"
INNER JOIN RDR1 R ON R."DocEntry" = P."DocEntry"
AND (R."U_INO_ORCITM" = O."U_INO_LinhaRef"
 OR (R."LineNum" = (SELECT
	 MIN(X."LineNum")
		FROM RDR1 X
		WHERE X."DocEntry" = P."DocEntry")
 AND NOT EXISTS (SELECT
	 1
		FROM RDR1 X
		WHERE X."DocEntry" = P."DocEntry"
		AND X."U_INO_ORCITM" = O."U_INO_LinhaRef")))
WHERE O."Status" <> 'C'
AND NOT (O."OriginNum" = 84537 AND O."DocEntry" BETWEEN 165921 AND 165990) WITH READ ONLY;
