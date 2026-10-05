"""Queries do módulo Pedidos WBC, transcritas de `Controllers/Querys.resx`.

Este é o módulo com as queries mais pesadas do addon original (várias tinham
`CommandTimeout` aumentado manualmente para 300-500s — ver "Débitos técnicos" item 9 no
migration_guide.md). São executadas via `HanaDirectReader` (`hdbcli`), o caminho
preferencial de leitura desde a revisão da decisão em 16/09/2026 (seção 6.4). A orientação
anterior, de transformá-las em Calculation Views antes de ir para produção, foi descartada
— o peso delas continua sendo um ponto de atenção de performance, mas isso é otimização de
query, não mais uma mudança de caminho de acesso.

Prefixo indica o banco: HANA (SAP B1) ou MSSQL (WBC / WBCCAD).
"""

# --- MSSQL (WBC) ---------------------------------------------------------------

BUSCA_PEDIDOS_PARA_INTEGRAR = """
SELECT DISTINCT 'N' AS "Selecionar", OPR1."OpprId" as "Num Oportunidade", ORDR."DocNum" as "Nº Pedido",
       ORDR."CardCode" as "Cod.Cliente", ORDR."CardName" as "Nome Cliente", ORDR."DocTotal" as "Total Pedido",
       ORDR."DocDate" as "Data Lancamento", OOPR."U_ORCNUM_MASC" as "Nº Oportunidade"
FROM ORDR
  INNER JOIN OPR1 ON OPR1."DocId" = ORDR."DocEntry" AND OPR1."ObjType" = 17
  INNER JOIN OOPR ON OPR1."OpprId" = OOPR."OpprId"
WHERE IFNULL(OOPR."U_INO_IntegrouWBC",'N') = 'Y'
  AND IFNULL(ORDR."U_INO_ProcessWBC",'N') = 'N'
  AND ORDR."DocStatus" = 'O'
  AND OOPR."U_ORCNUM_MASC" <> ''
  AND ORDR."U_INO_Integrar" = 'Y'
  {filtro}
""".strip()

BUSCA_PEDIDOS_INTEGRADOS = """
SELECT DISTINCT 'N' AS "Selecionar", OPR1."OpprId" as "Num Oportunidade", ORDR."DocNum" as "Nº Pedido",
       ORDR."CardCode" as "Cod.Cliente", ORDR."CardName" as "Nome Cliente", ORDR."DocTotal" as "Total Pedido",
       ORDR."DocDate" as "Data Lancamento", OOPR."U_ORCNUM_MASC" as "Nº Oportunidade"
FROM ORDR
  INNER JOIN OPR1 ON OPR1."DocId" = ORDR."DocEntry" AND OPR1."ObjType" = 17
  INNER JOIN OOPR ON OPR1."OpprId" = OOPR."OpprId"
WHERE IFNULL(OOPR."U_INO_IntegrouWBC",'N') = 'Y'
  AND IFNULL(ORDR."U_INO_ProcessWBC",'N') = 'Y'
  AND ORDR."DocStatus" = 'O'
  AND OOPR."U_ORCNUM_MASC" <> ''
  {filtro}
""".strip()

GET_ORCS_WBC = """
SELECT ISNULL(A.ORCNUM,'') as ORCNUM, ISNULL(A.REVISAO,'') as VERSAO, ISNULL(A.SITCOD,0) as SITCOD,
       ISNULL(A.CLINOM,'') as CLINOM, ISNULL(A.REPCOD,'') as REPCOD, ISNULL(A.CLIMUN,'') as CLIMUN,
       ISNULL(A.ESTCOD,'') as ESTCOD, ISNULL(A.ORCDAT,'1900-01-01') as ORCDAT,
       ISNULL(A.ORCALTDTH,'1900-01-01') as ORCALTDTH, ISNULL(B.ORCNUM,'') as ORCNUM_ITEM,
       ISNULL(B.GRPCOD,0) as GRPCOD, ISNULL(B.SUBGRPCOD,0) as SUBGRPCOD, ISNULL(B.ORCITM,0) as ORCITM,
       ISNULL(B.ORCPRDCOD,'') as ORCPRDCOD, ISNULL(B.ORCPRDQTD,1.0) as ORCPRDQTD, ISNULL(B.ORCTXT,'') as ORCTXT,
       ISNULL(B.ORCVAL,0.0) as ORCVAL, ISNULL(B.ORCIPI,0.0) as ORCIPI, ISNULL(B.ORCICM,0.0) as ORCICM,
       ISNULL(B.idIntegracao_OrcItm,0) as idIntegracao_OrcItm
FROM dbo.INTEGRACAO_ORCLST A
  LEFT JOIN INTEGRACAO_ORCITM B ON A.ORCNUM = B.ORCNUM
WHERE A.ORCNUM = ?
""".strip()

GET_TABLE_VALDIXSON = """
SELECT DISTINCT ARV.ORCNUM, ARV.GRPCOD, ARV.SUBGRPCOD,
       CASE WHEN (qry.ORCITM = 0) THEN qry.ORCITM ELSE ARV.ORCITM END,
       ISNULL((SELECT TOP 1 prd.PRDCOD FROM INTEGRACAO_ORCPRD prd
               WHERE prd.ORCNUM = ? AND prd.PRDDSC = ARV.PRDDSC), ARV.PRDCOD) PRDCOD,
       ARV.ORCPRDARV_NIVEL, ARV.CORCOD, ARV.PRDDSC, ARV.ORCQTD, ARV.ORCTOT, ARV.ORCPES,
       ARV.idIntegracao_OrcPrdArv, ARV.orcprdarv_dth
FROM INTEGRACAO_ORCPRDARV ARV
  LEFT JOIN (SELECT prd.ORCITM, prd.PRDCOD FROM INTEGRACAO_ORCPRD prd
             WHERE prd.ORCNUM = ? AND prd.ORCITM = '0') qry ON ARV.PRDCOD = qry.PRDCOD
WHERE ARV.ORCNUM = ?
ORDER BY ARV.idIntegracao_OrcPrdArv
""".strip()

NOVA_TABELA_QUOT = """
SELECT DISTINCT ISNULL(ORCVALVND,0), ISNULL(ORCVALLST,0), ISNULL(ORCVALINV,0), ISNULL(ORCVALLUC,0),
       ISNULL(ORCVALEXP,0), ISNULL(ORCVALCOM,0), ISNULL(ORCPERCOM,0), ISNULL(REPCOD,0), ISNULL(CLICOD,0),
       ISNULL(CLINOM,0), ISNULL(CLICONCOD,0), ISNULL(CLICON,0), ISNULL(ORCVALTRP,0), ISNULL(ORCVALEMB,0),
       ISNULL(ORCVALMON,0), ISNULL(PGTCOD,0), ISNULL(TIPMONCOD,0), ISNULL(PRZENT,0), ISNULL(ORCBAS1,0),
       ISNULL(ORCBAS2,0), ISNULL(ORCBAS3,0), ISNULL(ORCPGT,0), ISNULL(ORCIMP_REVISAO,0), ISNULL(ORCIMP_EMAIL,0),
       ISNULL(ORCIMP_FONE,0), ISNULL(ORCIMP_CIDADE,0), ISNULL(ORCIMP_UF,0), ISNULL(ORCIMP_TIPO_VENDA,0),
       ISNULL(ORCIMP_TRANSPORTE,0), ISNULL(ORCIMP_ACABAMENTO,0), REPLACE(ISNULL(ORCIMP_MONTAGEM,0),'      ',' '),
       ISNULL(ORCIMP_TABELA_PRECO,0), ISNULL(ORCIMP_RETORNO,0), ISNULL(ORCIMP_INDICE_VENDAS,0),
       ISNULL(ORCIMP_NEGOCIACAO,0)
FROM dbo.INTEGRACAO_ORCIMP
WHERE ORCNUM = ?
""".strip()

GET_ORCCAB = """
SELECT ISNULL(CAST(ORCPERCOM as float),0) AS ORCPERCOM, ISNULL(CAST(ORCVALCOM as float),0) AS ORCVALCOM,
       ISNULL(TIPMONCOD,'') AS TIPMONCOD, ISNULL(CAST(ORCBAS3 as float),0) AS ORCBAS3,
       ISNULL(REPCOD,'') AS REPCOD, ISNULL(PRZENT,0) as PRZENT, PGTCOD
FROM INTEGRACAO_ORCCAB
WHERE ORCNUM = ?
""".strip()

PEGA_VALORES_RECURSOS = "SELECT ORCBAS1, ORCBAS2, ORCBAS3 FROM INTEGRACAO_ORCCAB WHERE ORCNUM = ?"

PEGA_ESTRUTURA_PRD_WBC = """
SELECT tst.*, CONVERT(varchar, ROW_NUMBER() OVER (ORDER BY idIntegracao_OrcPrdArv)) AS num_row,
       ISNULL((SELECT TOP 1 INTEGRACAO_ORCPRD.PRDCOD FROM INTEGRACAO_ORCPRD
               WHERE ORCNUM IN ({orc_nums}) AND INTEGRACAO_ORCPRD.PRDDSC = tst.PRDDSC), tst.PRDCOD)
FROM INTEGRACAO_ORCPRDARV tst
WHERE tst.ORCNUM IN ({orc_nums})
""".strip()

NEW_BUSCA_ITEM = """
SELECT ARV.PRDCOD
FROM INTEGRACAO_ORCPRDARV ARV
WHERE ARV.ORCNUM = ?
  AND ARV.idIntegracao_OrcPrdArv > ?
  AND ARV.idIntegracao_OrcPrdArv < ISNULL(
        (SELECT MIN(tst.idIntegracao_OrcPrdArv) FROM INTEGRACAO_ORCPRDARV tst
         WHERE tst.idIntegracao_OrcPrdArv > ? AND tst.ORCNUM = ?
           AND tst.ORCPRDARV_NIVEL = (SELECT ARV2.ORCPRDARV_NIVEL FROM INTEGRACAO_ORCPRDARV ARV2
                                       WHERE ARV2.idIntegracao_OrcPrdArv = ?)), 99999999)
  AND ARV.ORCPRDARV_NIVEL = (SELECT ARV2.ORCPRDARV_NIVEL FROM INTEGRACAO_ORCPRDARV ARV2
                              WHERE ARV2.idIntegracao_OrcPrdArv = ?) + 1
ORDER BY ARV.idIntegracao_OrcPrdArv
""".strip()

VERIFICA_SEMI_ACABADO_PESO = """
SELECT TOP 1 REPLACE(CONVERT(varchar, ORCPES), '.', ','), REPLACE(CONVERT(varchar, ORCQTD), '.', ','),
       CONVERT(varchar, idIntegracao_OrcPrdArv)
FROM INTEGRACAO_ORCPRDARV
WHERE PRDCOD = ? AND idIntegracao_OrcPrdArv > ? AND ORCNUM = ?
ORDER BY idIntegracao_OrcPrdArv
""".strip()

# --- HANA (SAP B1) --------------------------------------------------------------

GET_ID_ORCAMENTOS_PEDIDO = """
SELECT top 1 A."DocEntry", A."CardCode", A."DocNum"
FROM ORDR A INNER JOIN RDR1 B ON A."DocEntry" = B."DocEntry"
WHERE A."U_INO_COTWBC" = '{orc_num}' AND A."CANCELED" = 'N'
ORDER BY A."DocEntry" DESC
""".strip()

# Acrescentada em 22/09/2026. No addon a grade trazia as DUAS numerações lado a lado:
# "Nº Oportunidade" = OOPR."U_ORCNUM_MASC" (o número do orçamento WBC, ex. 00125566) e
# "Num Oportunidade" = OPR1."OpprId" (a chave da Oportunidade no SAP, ex. 15149). Quase
# todo o fluxo usa a primeira; SÓ o `AddPedidoOportunidade` usa a segunda, porque ali é
# `GetByKey` de SalesOpportunities. O porte colapsou as duas num parâmetro só, e com isso
# passava o número do orçamento onde o SAP espera a chave da Oportunidade. Como a CLI e a
# web recebem só o número do orçamento, a chave é reobtida aqui.
OPPR_ID_POR_ORCAMENTO = """
SELECT top 1 "OpprId" FROM OOPR
WHERE "U_ORCNUM_MASC" = '{orc_num}'
ORDER BY "OpprId" DESC
""".strip()

VERIFICA_TAB_UPDATE = 'SELECT "U_INO_UpdateDetalhe" FROM "ORDR" WHERE "DocEntry" = {doc_entry}'

VERIFICA_CONG = 'SELECT "U_INO_Congelado" FROM "ORDR" WHERE "DocEntry" = {doc_entry}'

# Adicionadas em 15/09/2026 — verificação de reprocessamento pedida pelo Anderson (o C#
# original não tinha isso em código: a proteção vinha só do filtro da grade da tela,
# "Pedidos Novos" só listava U_INO_ProcessWBC='N' — ver seção 7.6 do migration_guide.md).
VERIFICA_PROCESS_WBC = 'SELECT "U_INO_ProcessWBC" FROM "ORDR" WHERE "DocEntry" = {doc_entry}'

CHECA_OP_EXISTENTE_PEDIDO = """
SELECT "DocEntry" FROM OWOR
WHERE "OriginNum" = '{doc_num}' AND "ItemCode" = '{item_code}' AND "Status" <> 'C'
""".strip()

GET_ITENS_SAP = 'SELECT "U_INO_ItemSAP" FROM "@INO_GRP_PRODUTOS" WHERE "Code" = \'{grp_code}\''

SELECT_CODIGO_ITEM = 'SELECT COUNT("ItemCode") FROM OITM WHERE UPPER("ItemCode") = UPPER(\'{item_code}\')'

SELECT_CODIGO_ITEM_OP = 'SELECT top 1 "ItemCode" FROM OITM WHERE UPPER("ItemCode") = UPPER(\'{item_code}\')'


PEGA_DOC_ENTRY_PED = """
SELECT top 1 ORDR."DocEntry" as "DocEntry"
FROM ORDR
  INNER JOIN OPR1 ON OPR1."DocId" = ORDR."DocEntry" AND OPR1."ObjType" = 17
  INNER JOIN OOPR ON OPR1."OpprId" = OOPR."OpprId"
WHERE OOPR."U_ORCNUM_WBC" = '{orc_num}' AND ORDR."CANCELED" = 'N'
ORDER BY ORDR."DocEntry" DESC
""".strip()



# ⚠️ 24/09/2026 — restrita às linhas DO GRUPO (`U_INO_ORCITM IN (...)`). A versão do C#
# (`BuscaMAXItemLinha`) filtrava só por pedido + item e pegava a primeira linha do
# `ORDER BY LineNum DESC`, ou seja, a linha sem OP de MAIOR número daquele item — não a
# linha do grupo que está sendo processado. Com a regra antiga (toda linha com quantidade
# 1) isso não aparecia. Com a regra nova (quantidade = nº de módulos), a OP recebe a
# quantidade de OUTRA linha: no 84274 três OPs saíram com 255 (a quantidade da linha 6)
# para linhas de 8, 1 e 18. O pedido é identificado pelo DocEntry, não pelo DocNum (7.43).
BUSCA_MAX_ITEM_LINHA = """
SELECT max(T0."LineNum"), max(T0."Quantity"), max(T0."LineTotal")
FROM RDR1 T0
WHERE T0."DocEntry" = {doc_entry} AND IFNULL(T0."U_INO_OP",0) = 0
  AND T0."ItemCode" = '{item_code}' AND T0."U_INO_ORCITM" IN ({orc_itens})
GROUP BY T0."ItemCode", T0."LineNum", T0."LineTotal"
ORDER BY T0."LineNum" DESC
""".strip()

MAX_PRECO_PEDIDO = 'SELECT SUM(T0."LineTotal") FROM RDR1 T0 INNER JOIN ORDR T1 ON T0."DocEntry" = T1."DocEntry" WHERE T1."DocNum" = \'{doc_num}\''

ENTREGA_MULTIPLA = 'SELECT T0."U_INO_EntregaMultipla" FROM ORDR T0 WHERE T0."DocEntry" = \'{doc_entry}\''

SELECT_ORC_ITEM_SAP = 'SELECT "U_INO_ItemSAP" FROM "@INO_GRP_PRODUTOS" WHERE "Code" = \'{grp_code}\''

COUNT_RECURSO = 'SELECT COUNT("VisResCode") FROM ORSC WHERE "VisResCode" = \'{nome_recurso}\''

PEGA_RECURSO_CODE = 'SELECT "VisResCode" FROM ORSC WHERE "VisResCode" = \'{nome_recurso}\''

MANUAL_LINHA = 'SELECT T0."U_xPed", T0."U_nItem", T0."U_INO_Composicao", T0."U_INO_COR" FROM "RDR1" T0 WHERE T0."DocEntry" = {doc_entry}'



GET_PESO_PEDIDO = 'SELECT sum(T0."U_INO_PESO") FROM "@INO_ORC_LINHA" T0 WHERE T0."DocEntry" = \'{doc_entry}\' AND T0."U_INO_NIVEL" = 1 AND T0."U_INO_ORCITM" = \'{orc_item}\''

# Transcrição do `BuscaOPS` original, ampliada em 22/09/2026 com as colunas que
# identificam a OP para quem lê o acompanhamento. O FILTRO é o mesmo (`Status = 'P'`,
# `OriginNum` = nº do pedido) — só o SELECT mudou, então o conjunto cancelado continua
# sendo exatamente o do legado. `DocEntry` segue como PRIMEIRA coluna porque é a chave
# usada na Service Layer.
BUSCA_OPS = """
SELECT T0."DocEntry", T0."DocNum", T0."ItemCode", IFNULL(T1."ItemName", '') AS "ItemName",
       T0."PlannedQty"
FROM OWOR T0 LEFT JOIN OITM T1 ON T1."ItemCode" = T0."ItemCode"
WHERE T0."Status" in ('P') and T0."OriginNum" = '{origin_num}'
ORDER BY T0."DocNum"
""".strip()

# Só para o acompanhamento: o nome do item produzido, que não está entre os componentes
# da OP de semiacabado (aqueles são os filhos). Leitura barata no HANA — o recurso caro
# da execução é a Service Layer, não o banco.
DESCRICAO_ITEM = 'SELECT IFNULL("ItemName", \'\') FROM OITM WHERE "ItemCode" = \'{item_code}\''


# --- Adicionadas em 15/09/2026 ao portar o módulo (extraídas do Querys.resx original,
# não estavam nas queries já transcritas na sessão anterior) ---------------------

# HANA (SAP B1) — Controllers/Querys.resx, chave "Linha". Original:
#   SELECT T0."VisOrder" FROM RDR1 T0 WHERE T0."DocEntry" = {0} and T0."U_INO_ORCITM" = {1}
# Localiza a linha do pedido que corresponde a um item do orçamento (`U_INO_ORCITM`),
# usada por `AtualizaDoc(Tipo="OP")` para gravar `U_INO_OP` na linha certa.
#
# ⚠️ Diferença deliberada em relação ao original: selecionamos TAMBÉM o `LineNum`. O C#
# usava `VisOrder` porque a DI API endereça linha por POSIÇÃO (`Lines.SetCurrentLine(i)`);
# a Service Layer endereça por `LineNum` (a chave da linha em `DocumentLines`). Nos casos
# normais os dois coincidem, mas divergem se as linhas do pedido tiverem sido reordenadas —
# e aí usar `VisOrder` como se fosse `LineNum` atualizaria a LINHA ERRADA. Ver seção 7.11
# do migration_guide.md.

# Mesma coisa, para VÁRIOS itens de orçamento de uma vez. Adicionada em 16/09/2026 junto com
# a otimização que agrupa as gravações de `U_INO_OP` numa única chamada à Service Layer:
# antes eram N consultas + N PATCHes (um por item do grupo), agora é 1 consulta + 1 PATCH.
# Read-only (29/09/2026): the weight of each order line, to put it next to the WBC tree's
# in the execution log — the weight was the one thing going wrong without a trace.
PESOS_DAS_LINHAS_DO_PEDIDO = """
SELECT T0."LineNum", T0."ItemCode", T0."Quantity", T0."Weight1", T0."LineTotal", T0."LineStatus",
       T0."U_INO_ORCITM"
FROM RDR1 T0
WHERE T0."DocEntry" = {doc_entry}
ORDER BY T0."LineNum"
""".strip()

# Change log of one order line (ADOC = header versions, ADO1 = their lines; ObjType 17 =
# sales order), oldest first, with who saved each version — `service._causa_do_peso`.
HISTORICO_DA_LINHA_DO_PEDIDO = """
SELECT T0."LogInstanc", T0."Quantity", T0."Weight1", T0."LineTotal", T1."UpdateDate", T1."UpdateTS",
       T2."U_NAME", T2."USER_CODE",
       (SELECT MIN(A."LogInstanc") FROM ADOC A
        WHERE A."ObjType" = T0."ObjType" AND A."DocEntry" = T0."DocEntry") AS "PrimeiraVersao"
FROM ADO1 T0
INNER JOIN ADOC T1 ON T1."ObjType" = T0."ObjType" AND T1."DocEntry" = T0."DocEntry"
                  AND T1."LogInstanc" = T0."LogInstanc"
LEFT JOIN OUSR T2 ON T2."USERID" = COALESCE(T1."UserSign2", T1."UserSign")
WHERE T0."ObjType" = '17' AND T0."DocEntry" = {doc_entry} AND T0."LineNum" = {line_num}
ORDER BY T0."LogInstanc"
""".strip()

LINHAS_PEDIDO_POR_ORCITM = """
SELECT T0."LineNum", T0."U_INO_ORCITM" FROM RDR1 T0
WHERE T0."DocEntry" = {doc_entry} AND T0."U_INO_ORCITM" IN ({orc_itens})
ORDER BY T0."LineNum"
""".strip()

# MSSQL (WBC) — Controllers/Querys.resx, chave "PegaLinha"
PEGA_LINHA = """
SELECT convert(varchar, ARV.[ORCITM])
FROM [INTEGRACAO_ORCPRDARV] ARV WHERE ARV.idIntegracao_OrcPrdArv = ?
""".strip()

# MSSQL (WBC) — Controllers/Querys.resx, chave "NovaTabelaQuotLinha". Usada só no caminho
# de fallback de `preencheTabela` (quando o orçamento não tem estrutura detalhada em
# INTEGRACAO_ORCPRDARV) — cada linha de INTEGRACAO_ORCIMP vira uma linha "rasa" do UDO
# INO_ORC_LINHA (só GRPCOD/ORCVAL/ORCITM/ORCTXT preenchidos, o resto fica em branco no
# legado — comportamento replicado, não é engano da transcrição).
NOVA_TABELA_QUOT_LINHA = """
SELECT '', ISNULL(GRPCOD,0), '', '', '', '', ISNULL(ORCVAL,0), '', '', ISNULL(ORCITM,0), ISNULL(ORCTXT,0)
FROM [dbo].[INTEGRACAO_ORCIMP]
WHERE ORCNUM = ?
""".strip()

# HANA (SAP B1) — Controllers/Querys.resx, chave "GetLinha"
GET_LINHA = """
SELECT top 1 case when (T0."LineNum") = 0 then '0' else to_varchar(T0."LineNum") end
FROM RDR1 T0 INNER JOIN ORDR T1 ON T0."DocEntry" = T1."DocEntry"
WHERE T1."U_INO_COTWBC" = '{orc_num}' and T0."U_INO_ORCITM" = '{orc_itm}' and T1."CANCELED" = 'N'
ORDER BY T1."DocEntry" desc
""".strip()

# HANA (SAP B1) — Controllers/Querys.resx, chave "pegaDocNumPed"
# ⚠️ 23/09/2026 — substitui `PEGA_DOC_NUM_PED` (`pegaDocNumPed` do C#), que buscava o DocNum
# pela Oportunidade SEM filtrar cancelados e SEM ordenar, e o chamador usava a primeira
# linha. Quando a Oportunidade tem mais de um pedido (um cancelado e o que o substituiu), a
# primeira linha pode ser o CANCELADO: no 84425 ela devolveu o 84424 (CANCELED='Y'). O
# DocEntry, por outro lado, vem de `PEGA_DOC_ENTRY_PED`, que filtra e ordena — então os dois
# números podiam apontar para pedidos diferentes. Agora o DocNum sai do próprio DocEntry:
# são o mesmo pedido por construção.
DOC_NUM_POR_DOC_ENTRY = 'SELECT "DocNum" FROM ORDR WHERE "DocEntry" = {doc_entry}'


# --- Comparação de OPs entre ambientes (adicionadas em 15/09/2026, a pedido do Anderson:
# conferir se as OPs geradas pelo Python batem com as que o addon legado gerou em produção
# para o mesmo orçamento). Não têm equivalente no C# — são queries de auditoria, só leitura.
#
# O `{schema}` permite comparar schemas diferentes na MESMA conexão HANA (ex.: produção vs
# homologação). Vem vazio para usar o schema corrente (o de SL_COMPANY_DB), ou como
# `"SBOALTAMIRAPROD".` para qualificar. Ver `_prefixo_schema()` em service.py.
# -----------------------------------------------------------------------------------

PEDIDO_POR_ORCAMENTO = """
SELECT T2."DocEntry", T2."DocNum",
       IFNULL(T2."U_INO_EntregaMultipla",'') AS "EntregaMultipla",
       IFNULL(T2."U_INO_ProcessWBC",'') AS "ProcessWBC",
       IFNULL(T2."U_INO_Congelado",'') AS "Congelado",
       IFNULL(T2."U_INO_COTWBC",'') AS "CotWBC",
       T2."DocStatus", T2."CANCELED"
FROM {schema}OOPR T0
  INNER JOIN {schema}OPR1 T1 ON T0."OpprId" = T1."OpprId"
  INNER JOIN {schema}ORDR T2 ON T1."DocNumber" = T2."DocNum"
WHERE T0."U_ORCNUM_WBC" = '{orc_num}' AND T1."ObjType" = 17
ORDER BY T2."DocEntry" DESC
""".strip()

# `SELECT *` proposital: esta é uma query de auditoria, e usar `*` evita depender dos nomes
# exatos de coluna de OWOR/WOR1 (que variam entre versões do B1) — os campos são lidos
# defensivamente no Python. Não replicar esse padrão na lógica de negócio.
OPS_DO_PEDIDO = """
SELECT * FROM {schema}OWOR T0
WHERE to_varchar(IFNULL(T0."OriginAbs",0)) = '{doc_entry}'
   OR to_varchar(IFNULL(T0."OriginNum",0)) = '{doc_num}'
ORDER BY T0."DocEntry"
""".strip()

LINHAS_DA_OP = 'SELECT * FROM {schema}WOR1 T0 WHERE T0."DocEntry" = {doc_entry} ORDER BY T0."LineNum"'

# Mapeamento grupo do WBC -> item SAP "pai" de produção. Igual ao SELECT_ORC_ITEM_SAP, mas
# com schema parametrizável, para diagnosticar "por que essa OP não foi criada".
ITEM_SAP_DO_GRUPO = 'SELECT "U_INO_ItemSAP" FROM {schema}"@INO_GRP_PRODUTOS" WHERE "Code" = \'{grp_code}\''


# --- Cancelamento em lote das OPs de um pedido (adicionado em 16/09/2026 a pedido do
# Anderson, para limpar um pedido de vendas). Sem equivalente no C#: o addon cancelava OPs
# uma a uma (`CancelaOP`, chamado por `BuscaOPS` no reprocessamento), nunca em lote com
# verificação prévia.
# -----------------------------------------------------------------------------------

PEDIDO_POR_DOCNUM = """
SELECT T0."DocEntry", T0."DocNum", T0."CardCode", T0."CardName",
       IFNULL(T0."U_INO_ProcessWBC",'') AS "ProcessWBC",
       IFNULL(T0."U_INO_COTWBC",'') AS "CotWBC",
       T0."DocStatus", T0."CANCELED"
FROM ORDR T0
WHERE T0."DocNum" = '{doc_num}'
""".strip()

# Linhas do pedido que têm OP vinculada — usada pela limpeza (`limpa_vinculos_do_pedido`)
# para zerar só o que precisa, em vez de reescrever todas as linhas do documento.
LINHAS_COM_OP = """
SELECT T0."LineNum" FROM RDR1 T0
WHERE T0."DocEntry" = {doc_entry} AND IFNULL(T0."U_INO_OP",0) <> 0
ORDER BY T0."LineNum"
""".strip()


# --- Verificação do PATCH parcial de DocumentLines (21/09/2026, seção 7.11 do guia) ------
# Pergunta que essas duas respondem: ao mandar `DocumentLines` com apenas algumas linhas
# (só `LineNum` + o campo alterado), a Service Layer preserva as demais linhas e os demais
# campos, ou substitui a coleção inteira? Só leitura.

# Fotografia das linhas do pedido, com os campos que mais importam se algo for perdido.
FOTO_LINHAS_PEDIDO = """
SELECT T0."LineNum", T0."ItemCode", T0."Quantity", T0."Price", T0."LineTotal",
       T0."WhsCode", IFNULL(T0."U_INO_OP",0) "U_INO_OP",
       IFNULL(T0."U_INO_ORCITM",'') "U_INO_ORCITM"
FROM RDR1 T0
WHERE T0."DocEntry" = {doc_entry}
ORDER BY T0."LineNum"
""".strip()

# Histórico de versões do documento (ADOC/ADO1). O B1 guarda uma cópia das linhas a cada
# atualização; se alguma versão tiver menos linhas que a anterior, o PATCH comeu linhas.
# Devolve vazio se o log de histórico não estiver ativo para o objeto.
HISTORICO_LINHAS_PEDIDO = """
SELECT T0."LogInstanc", COUNT(*) "linhas",
       MIN(T0."LineNum") "primeira_linha", MAX(T0."LineNum") "ultima_linha"
FROM ADO1 T0
WHERE T0."DocEntry" = {doc_entry} AND T0."ObjType" = '17'
GROUP BY T0."LogInstanc"
ORDER BY T0."LogInstanc"
""".strip()

# Mesma projeção de PEDIDO_POR_DOCNUM, mas achando o pedido pelo DocEntry. DocNum e
# DocEntry são numerações distintas do B1 e não se deduzem uma da outra — auditar um
# DocEntry visto num log exige entrar por ele.
PEDIDO_POR_DOCENTRY = """
SELECT T0."DocEntry", T0."DocNum", T0."CardCode", T0."CardName",
       IFNULL(T0."U_INO_ProcessWBC",'') AS "ProcessWBC",
       IFNULL(T0."U_INO_COTWBC",'') AS "CotWBC",
       T0."DocStatus", T0."CANCELED"
FROM ORDR T0
WHERE T0."DocEntry" = {doc_entry}
""".strip()


# ⚠️ Sem uso HOJE, mas NÃO é lixo: é a query que `_get_doc_entry_table_valdixon` deveria
# executar. Aquela função é um esqueleto que devolve 0 — ver a nota grande nela. Mantida
# aqui, com transcrição fiel do `Querys.resx`, para a correção não precisar reescrevê-la.
GET_DOC_ENTRY_TABLE_VALDIXON = 'SELECT max("DocEntry") FROM "@INO_ORCAM"'


# ---------------------------------------------------------------------------
# Removidas em 23/09/2026 (nenhum ponto do app as usava e nenhuma correção pendente
# depende delas):
#   SELECT_ESTRUTURA_EXISTE · PEGA_QUANTIDADE_PEDIDO · NUMERO_PEDIDO · CONTAGEM_LINHA
#   GET_DOC_TOTAL_PEDIDO · LINHA_PEDIDO
# and on 30/09/2026 GET_VERSAO_PEDIDO (read per group and discarded, as in the C#).
# Eram transcrições do `Querys.resx` de trechos do C# que o porte resolveu de outro jeito.
# Query transcrita e não usada é pior que ausente: quem lê presume que algum caminho a
# executa e vai procurar onde. O texto original está no `Querys.resx` do addon.
# ---------------------------------------------------------------------------
