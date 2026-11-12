-- VW_OS_OP_LINHA_PEDIDO — 12/11/2026 (NEW view). Run in SAP HANA (SBOALTAMIRAPROD) by the owner; nothing here runs it.
-- Run order: 1) this file  2) VW_OS_PINTURA_V0_2026-11-12.sql  3) VW_OS_INTEGRACAO_2026-11-12.sql
--
-- One row per live OP (Status <> 'C') and order line it belongs to. The single home of two rules that
-- VW_OS_INTEGRACAO and VW_OS_PINTURA_V0 used to carry each on its own:
--   LineNum : the order line of the OP's group (RDR1.U_INO_ORCITM = OWOR.U_INO_LinhaRef). When no line
--             carries the group any more (lines merged into one after the OPs were created, 84574),
--             the first line of the order.
--   OPPai   : 'Y' when the OP is the parent OP of its group:
--             a) it is stamped on an order line (RDR1.U_INO_OP), or
--             b) Planned/Released, and its line is closed with no stamp (SAP refuses the stamp on a
--                closed line: order closed by the future-delivery NF before its OPs, 84584), or
--             c) its group has no order line and its item is a sales product (OITM group 325; measured:
--                every OP stamped on a line is 325 and no child OP is).
-- Rollback: run sql/hana/VW_OS_INTEGRACAO_antes_2026-11-12.sql and sql/hana/VW_OS_PINTURA_V0_2026-11-11.sql;
-- this view may then stay (nothing reads it) or be dropped.
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
WHERE O."Status" <> 'C' WITH READ ONLY;
