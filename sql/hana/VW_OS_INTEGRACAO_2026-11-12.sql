-- VW_OS_INTEGRACAO — 12/11/2026. Run in SAP HANA (SBOALTAMIRAPROD) by the owner; nothing here runs it.
-- Run order: 1) VW_OS_OP_LINHA_PEDIDO_2026-11-12.sql  2) VW_OS_PINTURA_V0_2026-11-12.sql  3) this file
-- Two changes:
--   1. the order line of each OP is read from VW_OS_OP_LINHA_PEDIDO instead of being matched here; an OP group whose
--      order line was removed (lines merged into one after the OPs were created, 84574) no longer
--      vanishes from the OS;
--   2. the group number in the 50th column comes from the OP (U_INO_LinhaRef), which equals the line's
--      U_INO_ORCITM on every row that existed before.
-- Column list unchanged. Rollback: sql/hana/VW_OS_INTEGRACAO_antes_2026-11-12.sql
CREATE OR REPLACE VIEW "SBOALTAMIRAPROD"."VW_OS_INTEGRACAO" (
	 "N_OP",
	 "N_PED",
	 "Quantity",
	 "CodItemPED",
	 "DescItemPED",
	 "U_INO_D_Adicionais",
	 "DtPedido",
	 "DiasTotal",
	 "DtVenc",
	 "DtInic",
	 "LinhRef",
	 "Obs",
	 "DtEncerr",
	 "DtLiber",
	 "CodClien",
	 "NomeClien",
	 "NomedVend",
	 "Status",
	 "Deposito",
	 "UM",
	 "LinhEstrut",
	 "CodItemEstrut",
	 "DescItemEstrut",
	 "QtdBasEstrut",
	 "QtdPlanEstrut",
	 "QtdSaida",
	 "TipoEmissOP",
	 "DeposEstrut",
	 "LinhVisEstrut",
	 "TipoItemEstrut",
	 "QtdLiberEstrut",
	 "GrupoItem",
	 "CodDetalhOrcamento",
	 "ObsPedido",
	 "N_Orcamento",
	 "DataEntrega",
	 "NivelItemOrcam",
	 "PesoOrcam",
	 "CodItemOrcam",
	 "QtdOrcam",
	 "DescProdOrcam",
	 "CorOrcam",
	 "PrecoOrcam",
	 "TotalOrcam",
	 "VisOrder",
	 "Usuario",
	 "DtEntregaPED",
	 "CodigoOrcam",
	 "U_INO_VERSAOWBC",
	 "U_INO_LINHA",
	 "U_INO_ORCITM",
	 "Solda",
	 "Pintura",
	 "Almox",
	 "Exped",
	 "Compras" ) AS SELECT
	 DISTINCT T0."DocNum" as "N_OP" ,
	 T0."OriginNum" as "N_PED" ,
	 T4."Quantity" AS "Quantity",
	 T4."ItemCode" as "CodItemPED" ,
	 T4."Dscription" as "DescItemPED" ,
	 CAST(T4."U_INO_D_Adicionais" AS NVARCHAR(5000)) as "U_INO_D_Adicionais",
	 CAST(T0."PostDate" AS DATE) as "DtPedido" ,
	 (DAYS_BETWEEN (TO_VARCHAR(T3."DocDate",
	 'YYYY-MM-DD') ,
	 TO_VARCHAR (T3."DocDueDate",
	 'YYYY-MM-DD'))) as "DiasTotal" ,
	 CAST(T0."DueDate" AS DATE) as "DtVenc" ,
	 CAST(T0."StartDate" AS DATE) as "DtInic" ,
	 T0."U_INO_LinhaRef" as "LinhRef" ,
	 T0."Comments" as "Obs" ,
	 CAST(T0."CloseDate" AS DATE) as "DtEncerr" ,
	 CAST(T0."RlsDate" AS DATE) as "DtLiber" ,
	 T0."CardCode" as "CodClien" ,
	 T9."CardName" as "NomeClien" ,
	 T10."SlpName" as "NomedVend" ,
	 T0."Status" as "Status" ,
	 T0."Warehouse" as "Deposito" ,
	 T0."Uom" as "UM" ,
	 T1."LineNum" as "LinhEstrut" ,
	 T1."ItemCode" as "CodItemEstrut" ,
	 T11."ItemName" as "DescItemEstrut" ,
	 T1."BaseQty" as "QtdBasEstrut" ,
	 cast(T1."PlannedQty" as numeric) as "QtdPlanEstrut" ,
	 T1."IssuedQty" as "QtdSaida" ,
	 T1."IssueType" as "TipoEmissOP" ,
	 T1."wareHouse" as "DeposEstrut" ,
	 T1."VisOrder" as "LinhVisEstrut" ,
	 T1."ItemType" as "TipoItemEstrut" ,
	 T1."ReleaseQty" as "QtdLiberEstrut" ,
	 T2."ItmsGrpCod" AS "GrupoItem",
	 T3."U_INO_ORCAMENTO" as "CodDetalhOrcamento" ,
	 T3."Comments" as "ObsPedido" ,
	 T5."U_INO_COD" as "N_Orcamento" ,
	 CAST(T4."ShipDate" AS DATE) as "DataEntrega" ,
	 T6."U_INO_NIVEL" AS "NivelItemOrcam",
	 T6."U_INO_PESO" as "PesoOrcam" ,
	 T6."U_INO_CODIGO" as "CodItemOrcam" ,
	 T6."U_INO_Qtde" as "QtdOrcam" ,
	 T6."U_INO_PROD" as "DescProdOrcam" ,
	 T6."U_INO_COR" as "CorOrcam" ,
	 T6."U_INO_PRECO" as "PrecoOrcam" ,
	 T6."U_INO_TOTAL" as "TotalOrcam" ,
	 T6."VisOrder" as "VisOrder",
	 T8."U_NAME" as "Usuario" ,
	 CAST(T3."DocDueDate" AS DATE) as "DtEntregaPED" ,
	 T5."U_INO_COD" as "CodigoOrcam" ,
	 T3."U_INO_VERSAOWBC" ,
	 CAST(T0."U_INO_LinhaRef" AS NVARCHAR(25)) ,
	 CASE WHEN CAST(IFNULL(T6."U_INO_LINHA",
	 0) AS INTEGER) = 0
THEN 1
ELSE CAST(T6."U_INO_LINHA" AS INTEGER) * 5
END AS "U_INO_LINHA",
	 CASE WHEN EXISTS ( SELECT
	 1
	FROM "SBOALTAMIRAPROD"."VW_OS_SOLDA_DETALHE" S
	WHERE S."NPED" = T0."OriginNum"
	AND S."CodItemOrcam" = T6."U_INO_CODIGO"
	AND S."LinhaOrcam" = T6."VisOrder" )
THEN 1
ELSE 0
END AS "Solda",
	 CASE WHEN EXISTS ( SELECT
	 1
	FROM "SBOALTAMIRAPROD"."VW_OS_PINTURA_V0" P
	WHERE P."NPED" = T0."OriginNum"
	AND P."CodItemEstrut" = T1."ItemCode"
	AND P."CodItemOrcam" = T6."U_INO_CODIGO" )
THEN 1
ELSE 0
END AS "Pintura",
	 CASE WHEN EXISTS ( SELECT
	 1
	FROM "SBOALTAMIRAPROD"."VW_OS_ALMOX_IMPRESSAO" A
	WHERE A."NPED" = T0."OriginNum"
	AND A."CodItemOrcam" = T6."U_INO_CODIGO" )
THEN 1
ELSE 0
END AS "Almox" ,
	 CASE WHEN EXISTS ( SELECT
	 1
	FROM "SBOALTAMIRAPROD"."VW_OS_EXPED_IMPRESSAO_V2" E
	WHERE E."NPED" = T0."OriginNum"
	AND E."CodItemEstrut" = T1."ItemCode"
	AND E."CodItemOrcam" = T6."U_INO_CODIGO" )
THEN 1
ELSE 0
END AS "Exped" ,
	 CASE WHEN EXISTS ( SELECT
	 1
	FROM "SBOALTAMIRAPROD"."VW_REL_COMPRAS" C
	WHERE C."DocNum" = T0."OriginNum"
	AND C."CodItemOrcam" = T6."U_INO_CODIGO"
	AND C."LinhaOrcam" = T6."VisOrder" )
THEN 1
ELSE 0
END AS "Compras"
FROM OWOR T0
INNER JOIN WOR1 T1 ON T0."DocEntry" = T1."DocEntry"
INNER JOIN OITM T2 ON T0."ItemCode" = T2."ItemCode"
INNER JOIN ORDR T3 ON T0."OriginNum" = T3."DocNum"
INNER JOIN "@INO_ORCAM" T5 ON T3."U_INO_ORCAMENTO" = T5."DocEntry"
INNER JOIN "@INO_ORC_LINHA" T6 ON T5."DocEntry" = T6."DocEntry"
and T6."LineId" = T1."U_INO_DESC"
-- 12/11/2026: the order line of each OP comes from VW_OS_OP_LINHA_PEDIDO (the line of the OP's group
-- or, when that line was removed, the first line of the order). The rule lives there, not here.
INNER JOIN "SBOALTAMIRAPROD"."VW_OS_OP_LINHA_PEDIDO" L ON L."OPEntry" = T0."DocEntry"
AND L."PedidoEntry" = T3."DocEntry"
INNER JOIN RDR1 T4 ON T4."DocEntry" = L."PedidoEntry"
AND T4."LineNum" = L."LineNum" 
INNER JOIN "OUSR" T8 ON T0."UserSign" = T8."USERID"
INNER JOIN OCRD T9 ON T0."CardCode" = T9."CardCode"
INNER JOIN OSLP T10 ON T3."SlpCode" = T10."SlpCode"
INNER JOIN OITM T11 ON T1."ItemCode" = T11."ItemCode"
WHERE T0."Status" <> 'C'
AND T3."DocDate" >= TO_DATE(YEAR(ADD_MONTHS(CURRENT_DATE,
	 -12)) || '-' || LPAD(MONTH(ADD_MONTHS(CURRENT_DATE,
	 -12)),
	 2,
	 '0') || '-01',
	 'YYYY-MM-DD')
ORDER BY T0."OriginNum",
	 T6."VisOrder",
	 T0."DocNum" WITH READ ONLY;
