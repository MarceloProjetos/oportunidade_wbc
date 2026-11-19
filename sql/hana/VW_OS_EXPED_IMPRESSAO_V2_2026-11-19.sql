-- VW_OS_EXPED_IMPRESSAO_V2 — 19/11/2026. Run in SAP HANA (SBOALTAMIRAPROD) by the owner; nothing here runs it.
-- Needs VW_OS_OP_LINHA_PEDIDO (sql/hana/VW_OS_OP_LINHA_PEDIDO_2026-11-19.sql), already in place.
-- One change, in the three blocks that read OPs (ALMX_OP, EXP_OP_NIVEL1, EXP_OP_PAIFET): an OP only counts when
-- VW_OS_OP_LINHA_PEDIDO lists it. Which OPs belong to an order is decided there, not here; today the only
-- difference is order 84537, whose 69 OPs closed by mistake still put 3 extra rows on the shipping list.
-- Column list unchanged. Rollback: sql/hana/VW_OS_EXPED_IMPRESSAO_V2_antes_2026-11-19.sql
CREATE OR REPLACE VIEW "SBOALTAMIRAPROD"."VW_OS_EXPED_IMPRESSAO_V2" ( "TIPO",
	 "NPED",
	 "DtVenc",
	 "NomeClien",
	 "NomedVend",
	 "DtPedido",
	 "DiasTotal",
	 "DtInic",
	 "Obs",
	 "DtEncerr",
	 "DtLiber",
	 "CodClien",
	 "Status",
	 "Deposito",
	 "UM",
	 "CodItemEstrut",
	 "DescItemEstrut",
	 "GrpMaterialEstrut",
	 "GrpItensEstrut",
	 "QtdBasEstrut",
	 "QtdPlanEstrut",
	 "QtdSaida",
	 "TipoEmissOP",
	 "DeposEstrut",
	 "TipoItemEstrut",
	 "QtdLiberEstrut",
	 "PesoEstrut",
	 "U_INO_COD",
	 "CodDetalhOrcamento",
	 "DtEntregaPED",
	 "ObsPedido",
	 "CodigoOrcam",
	 "PesoOrcam",
	 "CodItemOrcam",
	 "QtdOrcam",
	 "DescProdOrcam",
	 "CorOrcam",
	 "PrecoOrcam",
	 "TotalOrcam",
	 "Filial",
	 "Tipo Logradouro",
	 "Rua Filial",
	 "NFilial",
	 "Complemento Filial",
	 "CEP Filial",
	 "Bairro Filial",
	 "Cidade Filial",
	 "Estado Filial",
	 "CNPJ Filial",
	 "IE Filial",
	 "Matriz",
	 "Usuario",
	 "U_INO_NIVEL",
	 "U_INO_VERSAOWBC",
	 "U_INO_PROJETO",
	 "DocEntry_OP",
	 "DocEntry_PED" ) AS WITH "ALMX_PEDIDO" AS (SELECT
	 'ALMX_PEDIDO' AS "TIPO",
	 T3."DocNum" AS "NºPED",
	 T3."DocDueDate" AS "DtVenc",
	 T9."CardName" AS "NomeClien",
	 T10."SlpName" AS "NomedVend",
	 T3."DocDate" AS "DtPedido",
	 DAYS_BETWEEN(T3."DocDate",
	 T3."DocDueDate") AS "DiasTotal",
	 '' AS "DtInic",
	 '' AS "Obs",
	 '' AS "DtEncerr",
	 '' AS "DtLiber",
	 '' AS "CodClien",
	 '' AS "Status",
	 '' AS "Deposito",
	 '' AS "UM",
	 T6."U_INO_CODIGO" AS "CodItemEstrut",
	 T11."ItemName" AS "DescItemEstrut",
	 T11."MatGrp" AS "GrpMaterialEstrut",
	 T11."ItmsGrpCod" AS "GrpItensEstrut",
	 0 AS "QtdBasEstrut",
	 T6."U_INO_Qtde" AS "QtdPlanEstrut",
	 0 AS "QtdSaida",
	 0 AS "TipoEmissOP",
	 '' AS "DeposEstrut",
	 0 AS "TipoItemEstrut",
	 0 AS "QtdLiberEstrut",
	 T6."U_INO_PESO" AS "PesoEstrut",
	 T5."U_INO_COD",
	 T3."U_INO_ORCAMENTO" AS "DocEntry_ORC",
	 T3."DocDueDate" AS "DtEntregaPED",
	 T3."Comments" AS "ObsPedido",
	 T5."U_INO_COD" AS "CodigoOrcam",
	 T6."U_INO_PESO" AS "PesoOrcam",
	 T6."U_INO_CODIGO" AS "CodItemOrcam",
	 T6."U_INO_Qtde" AS "QtdOrcam",
	 T6."U_INO_PROD" AS "DescProdOrcam",
	 T6."U_INO_COR" AS "CorOrcam",
	 T6."U_INO_PRECO" AS "PrecoOrcam",
	 T6."U_INO_TOTAL" AS "TotalOrcam",
	 T7."BPLName" AS "Filial",
	 T7."AddrType" AS "Tipo Logradouro",
	 T7."Street" AS "Rua Filial",
	 T7."StreetNo" AS "Nº Filial",
	 T7."Building" AS "Complemento Filial",
	 T7."ZipCode" AS "CEP Filial",
	 T7."Block" AS "Bairro Filial",
	 T7."City" AS "Cidade Filial",
	 T7."State" AS "Estado Filial",
	 T7."TaxIdNum" AS "CNPJ Filial",
	 T7."TaxIdNum2" AS "IE Filial",
	 T7."MainBPL" AS "Matriz",
	 T8."U_NAME" AS "Usuario",
	 T6."U_INO_NIVEL",
	 T3."U_INO_VERSAOWBC",
	 T3."U_INO_PROJETO",
	 NULL AS "DocEntry_OP",
	 T3."DocEntry" AS "DocEntry_PED"
	FROM ORDR T3
	INNER JOIN "@INO_ORCAM" T5 ON T3."U_INO_ORCAMENTO" = T5."DocEntry"
	INNER JOIN "@INO_ORC_LINHA" T6 ON T5."DocEntry" = T6."DocEntry"
	INNER JOIN OBPL T7 ON T3."BPLId" = T7."BPLId"
	INNER JOIN OUSR T8 ON T3."UserSign" = T8."USERID"
	INNER JOIN OCRD T9 ON T3."CardCode" = T9."CardCode"
	INNER JOIN OSLP T10 ON T3."SlpCode" = T10."SlpCode"
	INNER JOIN OITM T11 ON T6."U_INO_CODIGO" = T11."ItemCode"
	LEFT JOIN OITT T12 ON T6."U_INO_CODIGO" = T12."Code"
	WHERE T3."DocStatus" <> 'C'
	AND IFNULL(T12."Code",
	 '') = ''
	AND T6."U_INO_ORCITM" = 0
	AND SUBSTRING(T6."U_INO_CODIGO",
	 1,
	 3) NOT IN ('ALP',
	 'ATR',
	 'TEG',
	 'REF',
	 'TPO',
	 'PPL',
	 'CAG')
	AND SUBSTRING(T6."U_INO_CODIGO",
	 1,
	 4) <> 'GALV'),
	 "ALMX_OP" AS (SELECT
	 'ALMX_OP' AS "TIPO",
	 T0."OriginNum" AS "NºPED",
	 T3."DocDueDate" AS "DtVenc",
	 T9."CardName" AS "NomeClien",
	 T10."SlpName" AS "NomedVend",
	 T3."DocDate" AS "DtPedido",
	 DAYS_BETWEEN(T3."DocDate",
	 T3."DocDueDate") AS "DiasTotal",
	 T0."StartDate" AS "DtInic",
	 T0."Comments" AS "Obs",
	 T0."CloseDate" AS "DtEncerr",
	 T0."RlsDate" AS "DtLiber",
	 T0."CardCode" AS "CodClien",
	 T0."Status" AS "Status",
	 T0."Warehouse" AS "Deposito",
	 T0."Uom" AS "UM",
	 T1."ItemCode" AS "CodItemEstrut",
	 T11."ItemName" AS "DescItemEstrut",
	 T11."MatGrp" AS "GrpMaterialEstrut",
	 T11."ItmsGrpCod" AS "GrpItensEstrut",
	 T1."BaseQty" AS "QtdBasEstrut",
	 T1."PlannedQty" AS "QtdPlanEstrut",
	 0 AS "QtdSaida",
	 0 AS "TipoEmissOP",
	 '' AS "DeposEstrut",
	 0 AS "TipoItemEstrut",
	 0 AS "QtdLiberEstrut",
	 T1."U_INO_PESO" AS "PesoEstrut",
	 T5."U_INO_COD",
	 T3."U_INO_ORCAMENTO" AS "DocEntry_ORC",
	 T3."DocDueDate" AS "DtEntregaPED",
	 T3."Comments" AS "ObsPedido",
	 T5."U_INO_COD" AS "CodigoOrcam",
	 T6."U_INO_PESO" AS "PesoOrcam",
	 T6."U_INO_CODIGO" AS "CodItemOrcam",
	 T6."U_INO_Qtde" AS "QtdOrcam",
	 T6."U_INO_PROD" AS "DescProdOrcam",
	 T6."U_INO_COR" AS "CorOrcam",
	 T6."U_INO_PRECO" AS "PrecoOrcam",
	 T6."U_INO_TOTAL" AS "TotalOrcam",
	 T7."BPLName" AS "Filial",
	 T7."AddrType" AS "Tipo Logradouro",
	 T7."Street" AS "Rua Filial",
	 T7."StreetNo" AS "Nº Filial",
	 T7."Building" AS "Complemento Filial",
	 T7."ZipCode" AS "CEP Filial",
	 T7."Block" AS "Bairro Filial",
	 T7."City" AS "Cidade Filial",
	 T7."State" AS "Estado Filial",
	 T7."TaxIdNum" AS "CNPJ Filial",
	 T7."TaxIdNum2" AS "IE Filial",
	 T7."MainBPL" AS "Matriz",
	 T8."U_NAME" AS "Usuario",
	 T6."U_INO_NIVEL",
	 T3."U_INO_VERSAOWBC",
	 T3."U_INO_PROJETO",
	 T0."DocEntry" AS "DocEntry_OP",
	 T3."DocEntry" AS "DocEntry_PED"
	FROM OWOR T0
	INNER JOIN WOR1 T1 ON T0."DocEntry" = T1."DocEntry"
	INNER JOIN OITM T2 ON T0."ItemCode" = T2."ItemCode" --INNER JOIN RDR1 T4 ON T4."U_INO_OP" = T0."DocEntry"
 --INNER JOIN ORDR T3 ON T3."DocEntry" = T4."DocEntry"

	INNER JOIN ORDR T3 ON T3."DocNum" = T0."OriginNum"
	INNER JOIN "@INO_ORCAM" T5 ON T3."U_INO_ORCAMENTO" = T5."DocEntry"
	INNER JOIN "@INO_ORC_LINHA" T6 ON T5."DocEntry" = T6."DocEntry"
	AND T6."U_INO_CODIGO" = T1."ItemCode"
	AND T6."LineId" = T1."U_INO_DESC"
	INNER JOIN OBPL T7 ON T3."BPLId" = T7."BPLId"
	INNER JOIN OUSR T8 ON T0."UserSign" = T8."USERID"
	INNER JOIN OCRD T9 ON T0."CardCode" = T9."CardCode"
	INNER JOIN OSLP T10 ON T3."SlpCode" = T10."SlpCode"
	INNER JOIN OITM T11 ON T1."ItemCode" = T11."ItemCode"
	LEFT JOIN OITT T12 ON T1."ItemCode" = T12."Code"
	WHERE T0."Status" <> 'C' 
	AND EXISTS (SELECT
	 1 
		FROM "SBOALTAMIRAPROD"."VW_OS_OP_LINHA_PEDIDO" L 
		WHERE L."OPEntry" = T0."DocEntry" 
		AND L."PedidoEntry" = T3."DocEntry") 
	AND IFNULL(T12."Code",
	 '') = ''
	AND T6."U_INO_ORCITM" = 0
	AND SUBSTRING(T1."ItemCode",
	 1,
	 3) NOT IN ('ALP',
	 'ATR',
	 'TEG',
	 'REF',
	 'TPO',
	 'PPL',
	'CAG')
	AND SUBSTRING(T1."ItemCode",
	 1,
	 4) <> 'GALV'),
	 "EXP_OP_NIVEL1" AS (SELECT
	 'EXP' AS "TIPO",
	 T0."OriginNum" AS "NºPED",
	 T3."DocDueDate" AS "DtVenc",
	 T9."CardName" AS "NomeClien",
	 T10."SlpName" AS "NomedVend",
	 T3."DocDate" AS "DtPedido",
	 DAYS_BETWEEN(T3."DocDate",
	 T3."DocDueDate") AS "DiasTotal",
	 T0."StartDate" AS "DtInic",
	 T0."Comments" AS "Obs",
	 T0."CloseDate" AS "DtEncerr",
	 T0."RlsDate" AS "DtLiber",
	 T0."CardCode" AS "CodClien",
	 T0."Status" AS "Status",
	 T0."Warehouse" AS "Deposito",
	 T0."Uom" AS "UM",
	 T1."ItemCode" AS "CodItemEstrut",
	 T11."ItemName" AS "DescItemEstrut",
	 T11."MatGrp" AS "GrpMaterialEstrut",
	 T11."ItmsGrpCod" AS "GrpItensEstrut",
	 T1."BaseQty" AS "QtdBasEstrut",
	 T1."PlannedQty" AS "QtdPlanEstrut",
	 0 AS "QtdSaida",
	 0 AS "TipoEmissOP",
	 '' AS "DeposEstrut",
	 0 AS "TipoItemEstrut",
	 0 AS "QtdLiberEstrut",
	 T1."U_INO_PESO" AS "PesoEstrut",
	 T5."U_INO_COD",
	 T3."U_INO_ORCAMENTO" AS "DocEntry_ORC",
	 T3."DocDueDate" AS "DtEntregaPED",
	 T3."Comments" AS "ObsPedido",
	 T5."U_INO_COD" AS "CodigoOrcam",
	 T6."U_INO_PESO" AS "PesoOrcam",
	 T6."U_INO_CODIGO" AS "CodItemOrcam",
	 T6."U_INO_Qtde" AS "QtdOrcam",
	 T6."U_INO_PROD" AS "DescProdOrcam",
	 T6."U_INO_COR" AS "CorOrcam",
	 T6."U_INO_PRECO" AS "PrecoOrcam",
	 T6."U_INO_TOTAL" AS "TotalOrcam",
	 T7."BPLName" AS "Filial",
	 T7."AddrType" AS "Tipo Logradouro",
	 T7."Street" AS "Rua Filial",
	 T7."StreetNo" AS "Nº Filial",
	 T7."Building" AS "Complemento Filial",
	 T7."ZipCode" AS "CEP Filial",
	 T7."Block" AS "Bairro Filial",
	 T7."City" AS "Cidade Filial",
	 T7."State" AS "Estado Filial",
	 T7."TaxIdNum" AS "CNPJ Filial",
	 T7."TaxIdNum2" AS "IE Filial",
	 T7."MainBPL" AS "Matriz",
	 T8."U_NAME" AS "Usuario",
	 T6."U_INO_NIVEL",
	 T3."U_INO_VERSAOWBC",
	 T3."U_INO_PROJETO",
	 T0."DocEntry" AS "DocEntry_OP",
	 T3."DocEntry" AS "DocEntry_PED"
	FROM OWOR T0
	INNER JOIN WOR1 T1 ON T0."DocEntry" = T1."DocEntry"
	INNER JOIN OITM T2 ON T0."ItemCode" = T2."ItemCode" ---INNER JOIN RDR1 T4 ON T4."U_INO_OP" = T0."DocEntry"
 ---INNER JOIN ORDR T3 ON T3."DocEntry" = T4."DocEntry"

	INNER JOIN ORDR T3 ON T3."DocNum" = T0."OriginNum"
	INNER JOIN "@INO_ORCAM" T5 ON T3."U_INO_ORCAMENTO" = T5."DocEntry"
	INNER JOIN "@INO_ORC_LINHA" T6 ON T5."DocEntry" = T6."DocEntry"
	AND T6."U_INO_CODIGO" = T1."ItemCode"
	AND T6."LineId" = T1."U_INO_DESC"
	INNER JOIN OBPL T7 ON T3."BPLId" = T7."BPLId"
	INNER JOIN OUSR T8 ON T0."UserSign" = T8."USERID"
	INNER JOIN OCRD T9 ON T0."CardCode" = T9."CardCode"
	INNER JOIN OSLP T10 ON T3."SlpCode" = T10."SlpCode"
	INNER JOIN OITM T11 ON T1."ItemCode" = T11."ItemCode"
	LEFT JOIN OITT T12 ON T1."ItemCode" = T12."Code"
	WHERE T0."Status" <> 'C' 
	AND EXISTS (SELECT
	 1 
		FROM "SBOALTAMIRAPROD"."VW_OS_OP_LINHA_PEDIDO" L 
		WHERE L."OPEntry" = T0."DocEntry" 
		AND L."PedidoEntry" = T3."DocEntry") 
	AND T6."U_INO_NIVEL" = '1'
	AND SUBSTRING(T1."ItemCode",
	 1,
	 3) NOT IN ('ALP',
	 'ATR',
	 'TEG',
	 'REF',
	 'TPO')
	AND SUBSTRING(T1."ItemCode",
	 1,
	 4) NOT IN ('GALV',
	 'PAR0',
	 'PAR3')
	AND SUBSTRING(T1."ItemCode",
	 1,
	 6) NOT IN ('PAIFET',
	 'PPLGAR',
	 'PPLEST',
	'PPLPRG')),
	 "EXP_OP_PAIFET" AS (SELECT
	 'EXP' AS "TIPO",
	 T0."OriginNum" AS "NºPED",
	 T3."DocDueDate" AS "DtVenc",
	 T9."CardName" AS "NomeClien",
	 T10."SlpName" AS "NomedVend",
	 T3."DocDate" AS "DtPedido",
	 DAYS_BETWEEN(T3."DocDate",
	 T3."DocDueDate") AS "DiasTotal",
	 T0."StartDate" AS "DtInic",
	 T0."Comments" AS "Obs",
	 T0."CloseDate" AS "DtEncerr",
	 T0."RlsDate" AS "DtLiber",
	 T0."CardCode" AS "CodClien",
	 T0."Status" AS "Status",
	 T0."Warehouse" AS "Deposito",
	 T0."Uom" AS "UM",
	 T1."ItemCode" AS "CodItemEstrut",
	 T11."ItemName" AS "DescItemEstrut",
	 T11."MatGrp" AS "GrpMaterialEstrut",
	 T11."ItmsGrpCod" AS "GrpItensEstrut",
	 T1."BaseQty" AS "QtdBasEstrut",
	 T1."PlannedQty" AS "QtdPlanEstrut",
	 0 AS "QtdSaida",
	 0 AS "TipoEmissOP",
	 '' AS "DeposEstrut",
	 0 AS "TipoItemEstrut",
	 0 AS "QtdLiberEstrut",
	 T1."U_INO_PESO" AS "PesoEstrut",
	 T5."U_INO_COD",
	 T3."U_INO_ORCAMENTO" AS "DocEntry_ORC",
	 T3."DocDueDate" AS "DtEntregaPED",
	 T3."Comments" AS "ObsPedido",
	 T5."U_INO_COD" AS "CodigoOrcam",
	 T6."U_INO_PESO" AS "PesoOrcam",
	 T6."U_INO_CODIGO" AS "CodItemOrcam",
	 T6."U_INO_Qtde" AS "QtdOrcam",
	 T6."U_INO_PROD" AS "DescProdOrcam",
	 T6."U_INO_COR" AS "CorOrcam",
	 T6."U_INO_PRECO" AS "PrecoOrcam",
	 T6."U_INO_TOTAL" AS "TotalOrcam",
	 T7."BPLName" AS "Filial",
	 T7."AddrType" AS "Tipo Logradouro",
	 T7."Street" AS "Rua Filial",
	 T7."StreetNo" AS "Nº Filial",
	 T7."Building" AS "Complemento Filial",
	 T7."ZipCode" AS "CEP Filial",
	 T7."Block" AS "Bairro Filial",
	 T7."City" AS "Cidade Filial",
	 T7."State" AS "Estado Filial",
	 T7."TaxIdNum" AS "CNPJ Filial",
	 T7."TaxIdNum2" AS "IE Filial",
	 T7."MainBPL" AS "Matriz",
	 T8."U_NAME" AS "Usuario",
	 T6."U_INO_NIVEL",
	 T3."U_INO_VERSAOWBC",
	 T3."U_INO_PROJETO",
	 T0."DocEntry" AS "DocEntry_OP",
	 T3."DocEntry" AS "DocEntry_PED"
	FROM OWOR T0
	INNER JOIN WOR1 T1 ON T0."DocEntry" = T1."DocEntry"
	INNER JOIN OITM T2 ON T0."ItemCode" = T2."ItemCode" ---INNER JOIN RDR1 T4 ON T4."U_INO_OP" = T0."DocEntry"
 ---INNER JOIN ORDR T3 ON T3."DocEntry" = T4."DocEntry"

	INNER JOIN ORDR T3 ON T3."DocNum" = T0."OriginNum"
	INNER JOIN "@INO_ORCAM" T5 ON T3."U_INO_ORCAMENTO" = T5."DocEntry"
	INNER JOIN "@INO_ORC_LINHA" T6 ON T5."DocEntry" = T6."DocEntry"
	AND T6."U_INO_CODIGO" = T1."ItemCode"
	AND T6."LineId" = T1."U_INO_DESC"
	INNER JOIN OBPL T7 ON T3."BPLId" = T7."BPLId"
	INNER JOIN OUSR T8 ON T0."UserSign" = T8."USERID"
	INNER JOIN OCRD T9 ON T0."CardCode" = T9."CardCode"
	INNER JOIN OSLP T10 ON T3."SlpCode" = T10."SlpCode"
	INNER JOIN OITM T11 ON T1."ItemCode" = T11."ItemCode"
	LEFT JOIN OITT T12 ON T1."ItemCode" = T12."Code"
	WHERE T0."Status" <> 'C' 
	AND EXISTS (SELECT
	 1 
		FROM "SBOALTAMIRAPROD"."VW_OS_OP_LINHA_PEDIDO" L 
		WHERE L."OPEntry" = T0."DocEntry" 
		AND L."PedidoEntry" = T3."DocEntry") 
	AND SUBSTRING(T1."ItemCode",
	 1,
	 6) = 'PAIFET') SELECT
	 "TIPO" ,
	 "NºPED" ,
	 "DtVenc" ,
	 "NomeClien" ,
	 "NomedVend" ,
	 "DtPedido" ,
	 "DiasTotal" ,
	 "DtInic" ,
	 "Obs" ,
	 "DtEncerr" ,
	 "DtLiber" ,
	 "CodClien" ,
	 "Status" ,
	 "Deposito" ,
	 "UM" ,
	 "CodItemEstrut" ,
	 "DescItemEstrut" ,
	 "GrpMaterialEstrut" ,
	 "GrpItensEstrut" ,
	 "QtdBasEstrut" ,
	 "QtdPlanEstrut" ,
	 "QtdSaida" ,
	 "TipoEmissOP" ,
	 "DeposEstrut" ,
	 "TipoItemEstrut" ,
	 "QtdLiberEstrut" ,
	 "PesoEstrut" ,
	 "U_INO_COD" ,
	 "DocEntry_ORC" ,
	 "DtEntregaPED" ,
	 "ObsPedido" ,
	 "CodigoOrcam" ,
	 "PesoOrcam" ,
	 "CodItemOrcam" ,
	 "QtdOrcam" ,
	 "DescProdOrcam" ,
	 "CorOrcam" ,
	 "PrecoOrcam" ,
	 "TotalOrcam" ,
	 "Filial" ,
	 "Tipo Logradouro" ,
	 "Rua Filial" ,
	 "Nº Filial" ,
	 "Complemento Filial" ,
	 "CEP Filial" ,
	 "Bairro Filial" ,
	 "Cidade Filial" ,
	 "Estado Filial" ,
	 "CNPJ Filial" ,
	 "IE Filial" ,
	 "Matriz" ,
	 "Usuario" ,
	 "U_INO_NIVEL" ,
	 "U_INO_VERSAOWBC" ,
	 "U_INO_PROJETO" ,
	 "DocEntry_OP" ,
	 "DocEntry_PED"
FROM ( SELECT
	 *
	FROM EXP_OP_NIVEL1
	UNION ALL SELECT
	 *
	FROM EXP_OP_PAIFET
	UNION ALL SELECT
	 *
	FROM ALMX_OP ) WITH READ ONLY;
