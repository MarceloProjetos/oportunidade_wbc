-- VW_OS_SOLDA_DETALHE as it was before 11/11/2026 (rollback). Checked identical to the live view.
CREATE OR REPLACE VIEW "SBOALTAMIRAPROD"."VW_OS_SOLDA_DETALHE" (
	 "TIPO",
	 "NPED",
	 "DtPedido",
	 "U_INO_ORCAMENTO",
	 "DtVenc",
	 "NomeClien",
	 "NomedVend",
	 "DescItemEstrut",
	 "GrpMaterialEstrut",
	 "GrpItensEstrut",
	 "CodDetalhOrcamento",
	 "DtEntregaPED",
	 "DiasTotal",
	 "ObsPedido",
	 "CodigoOrcam",
	 "LinhaOrcam",
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
	 "Nº Filial",
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
	 "U_INO_ORCITM",
	 "ItmsGrpCod_OITM",
	 "U_INO_EXPL_SOLDA",
	 "U_INO_VERSAOWBC",
	 "U_INO_PROJETO" ) AS ((SELECT
	 DISTINCT 'SOLD' as "TIPO" ,
	 T3."DocNum" as "NPED" ,
	 T3."DocDate" AS "DtPedido" ,
	 T3."U_INO_ORCAMENTO" ,
	 T3."DocDueDate" as "DtVenc" ,
	 T9."CardName" as "NomeClien" ,
	 T10."SlpName" as "NomedVend" ,
	 T11."ItemName" as "DescItemEstrut" ,
	 T11."MatGrp" as "GrpMaterialEstrut" ,
	 T11."ItmsGrpCod" as "GrpItensEstrut" ,
	 T3."U_INO_ORCAMENTO" as "CodDetalhOrcamento" ,
	 T3."DocDueDate" as "DtEntregaPED" ,
	 (DAYS_BETWEEN (TO_VARCHAR(T3."DocDate",
	 'YYYY-MM-DD') ,
	 TO_VARCHAR (T3."DocDueDate",
	 'YYYY-MM-DD'))) as "DiasTotal" ,
	 T3."Comments" as "ObsPedido" ,
	 T5."U_INO_COD" as "CodigoOrcam" ,
	 T6."VisOrder" as "LinhaOrcam" ,
	 T6."U_INO_PESO" as "PesoOrcam" ,
	 T6."U_INO_CODIGO" as "CodItemOrcam" ,
	 T6."U_INO_Qtde" as "QtdOrcam" ,
	 T6."U_INO_PROD" as "DescProdOrcam" ,
	 T6."U_INO_COR" as "CorOrcam" ,
	 T6."U_INO_PRECO" as "PrecoOrcam" ,
	 T6."U_INO_TOTAL" as "TotalOrcam" ,
	 T7."BPLName" AS "Filial" ,
	 T7."AddrType" AS "Tipo Logradouro" ,
	 T7."Street" AS "Rua Filial" ,
	 T7."StreetNo" AS "Nº Filial" ,
	 T7."Building" AS "Complemento Filial" ,
	 T7."ZipCode" AS "CEP Filial" ,
	 T7."Block" AS "Bairro Filial" ,
	 T7."City" AS "Cidade Filial" ,
	 T7."State" AS "Estado Filial" ,
	 T7."TaxIdNum" AS "CNPJ Filial" ,
	 T7."TaxIdNum2" AS "IE Filial" ,
	 T7."MainBPL" AS "Matriz" ,
	 T8."U_NAME" as "Usuario" ,
	 T6."U_INO_NIVEL" ,
	 T6."U_INO_ORCITM" ,
	 T11."ItmsGrpCod" as "ItmsGrpCod_OITM" ,
	 T11."U_INO_EXPL_SOLDA" ,
	 T3."U_INO_VERSAOWBC" ,
	 T3."U_INO_PROJETO" 
		FROM ORDR T3
		INNER JOIN "@INO_ORCAM" T5 ON T3."U_INO_ORCAMENTO" = T5."DocEntry"
		INNER JOIN "@INO_ORC_LINHA" T6 ON T5."DocEntry" = T6."DocEntry"
		INNER JOIN OBPL T7 ON T3."BPLId" = T7."BPLId"
		INNER JOIN "OUSR" T8 ON T3."UserSign" = T8."USERID"
		INNER JOIN OCRD T9 ON T3."CardCode" = T9."CardCode"
		INNER JOIN OSLP T10 ON T3."SlpCode" = T10."SlpCode"
		INNER JOIN OITM T11 ON T6."U_INO_CODIGO" = T11."ItemCode" 
		WHERE T11."ItmsGrpCod" = 332
		AND T3."DocStatus" <> 'C' 
		AND Substring (T11."ItemCode",
	 1,
	 3) NOT IN ('ALP',
	 'TPO',
	 'PAR')
		AND Substring (T11."ItemCode",
	 1,
	 6) NOT IN ('PAIFIT',
	'PPPTRE')
		AND Substring (T11."ItemCode",
	 1,
	 7) NOT IN ('ESTPRT0')
		AND Substring (T11."ItemCode",
	 1,
	 4) NOT IN ('PPFT')
		AND Substring (T11."ItemCode",
	 1,
	 9) NOT IN ('ESTGAVDIM')
		AND T6."U_INO_ORCITM" <> 0
		AND T6."U_INO_CODIGO" NOT IN (Select
	 "ItemCode"
			FROM "ItemSoldaEsp"))
	UNION ALL (SELECT
	 DISTINCT 'EXP' as "TIPO" ,
	 T3."DocNum" as "NºPED" ,
	 T3."DocDate" AS "DtPedido" ,
	 T3."U_INO_ORCAMENTO" ,
	 T3."DocDueDate" as "DtVenc" ,
	 T9."CardName" as "NomeClien" ,
	 T10."SlpName" as "NomedVend" ,
	 T11."ItemName" as "DescItemEstrut" ,
	 T11."MatGrp" as "GrpMaterialEstrut" ,
	 T11."ItmsGrpCod" as "GrpItensEstrut" ,
	 T3."U_INO_ORCAMENTO" as "CodDetalhOrcamento" ,
	 T3."DocDueDate" as "DtEntregaPED" ,
	 (DAYS_BETWEEN (TO_VARCHAR(T3."DocDate",
	 'YYYY-MM-DD') ,
	 TO_VARCHAR (T3."DocDueDate",
	 'YYYY-MM-DD'))) as "DiasTotal" ,
	 T3."Comments" as "ObsPedido" ,
	 T5."U_INO_COD" as "CodigoOrcam" ,
	 T6."VisOrder" as "LinhaOrcam" ,
	 T6."U_INO_PESO" as "PesoOrcam" ,
	 T6."U_INO_CODIGO" as "CodItemOrcam" ,
	 T6."U_INO_Qtde" as "QtdOrcam" ,
	 T6."U_INO_PROD" as "DescProdOrcam" ,
	 T6."U_INO_COR" as "CorOrcam" ,
	 T6."U_INO_PRECO" as "PrecoOrcam" ,
	 T6."U_INO_TOTAL" as "TotalOrcam" ,
	 T7."BPLName" AS "Filial" ,
	 T7."AddrType" AS "Tipo Logradouro" ,
	 T7."Street" AS "Rua Filial" ,
	 T7."StreetNo" AS "Nº Filial" ,
	 T7."Building" AS "Complemento Filial" ,
	 T7."ZipCode" AS "CEP Filial" ,
	 T7."Block" AS "Bairro Filial" ,
	 T7."City" AS "Cidade Filial" ,
	 T7."State" AS "Estado Filial" ,
	 T7."TaxIdNum" AS "CNPJ Filial" ,
	 T7."TaxIdNum2" AS "IE Filial" ,
	 T7."MainBPL" AS "Matriz" ,
	 T8."U_NAME" as "Usuario" ,
	 T6."U_INO_NIVEL" ,
	 T6."U_INO_ORCITM" ,
	 T11."ItmsGrpCod" as "ItmsGrpCod_OITM" ,
	 T11."U_INO_EXPL_SOLDA" ,
	 T3."U_INO_VERSAOWBC" ,
	 T3."U_INO_PROJETO" 
		FROM ORDR T3
		INNER JOIN "@INO_ORCAM" T5 ON T3."U_INO_ORCAMENTO" = T5."DocEntry"
		INNER JOIN "@INO_ORC_LINHA" T6 ON T5."DocEntry" = T6."DocEntry"
		INNER JOIN OBPL T7 ON T3."BPLId" = T7."BPLId"
		INNER JOIN "OUSR" T8 ON T3."UserSign" = T8."USERID"
		INNER JOIN OCRD T9 ON T3."CardCode" = T9."CardCode"
		INNER JOIN OSLP T10 ON T3."SlpCode" = T10."SlpCode"
		INNER JOIN OITM T11 ON T6."U_INO_CODIGO" = T11."ItemCode" 
		WHERE T3."DocStatus" <> 'C' 
		AND T6."U_INO_CODIGO" IN (Select
	 "ItemCode"
			FROM "ItemSoldaEsp")
		AND T6."U_INO_CODIGO" NOT IN ( SELECT
	 DISTINCT SUB_T6."U_INO_CODIGO"
			FROM ORDR SUB_T3
			INNER JOIN "@INO_ORCAM" SUB_T5 ON SUB_T3."U_INO_ORCAMENTO" = SUB_T5."DocEntry"
			INNER JOIN "@INO_ORC_LINHA" SUB_T6 ON SUB_T5."DocEntry" = SUB_T6."DocEntry"
			INNER JOIN OITM SUB_T11 ON SUB_T6."U_INO_CODIGO" = SUB_T11."ItemCode"
			WHERE SUB_T11."ItmsGrpCod" = 332
			AND SUB_T3."DocStatus" <> 'C' 
			AND SUB_T3."DocNum" = T3."DocNum" -- Vincula dinamicamente ao documento pai

			AND Substring (SUB_T11."ItemCode",
	 1,
	 3) NOT IN ('ALP',
	 'TPO',
	 'PAR')
			AND Substring (SUB_T11."ItemCode",
	 1,
	 6) NOT IN ('PAIFIT',
	'PPPTRE')
			AND Substring (SUB_T11."ItemCode",
	 1,
	 7) NOT IN ('ESTPRT0')
			AND Substring (SUB_T11."ItemCode",
	 1,
	 4) NOT IN ('PPFT')
			AND SUB_T6."U_INO_ORCITM" <> 0 ))) WITH READ ONLY;
