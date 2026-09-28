"""Queries do módulo Manutenção de OP, transcritas de `Controllers/Querys.resx`.
Todas em HANA (SAP B1) — este módulo não acessa o WBC.
"""

OPS_MANUTENCAO = """
SELECT 'N' "Selecionar", T1."DocNum" "Número OP", T1."Status", T1."ItemCode" "Cód. Produto",
       T2."ItemName" "Produto", T1."PlannedQty" "Qtde. Planejada", T1."CmpltQty" "Qtde. Apontada",
       T1."PlannedQty" - T1."CmpltQty" "Qtde. Restante", T1."PostDate" "Data Pedido",
       T1."StartDate" "Data inicio", T1."DueDate" "Data Vencimento", T1."CardCode" "Cod. Cliente",
       T3."CardName" "Cliente"
FROM "ORDR" T0
  LEFT OUTER JOIN OWOR T1 ON T0."CardCode" = T1."CardCode" AND T0."DocNum" = T1."OriginNum"
  LEFT OUTER JOIN "OITM" T2 ON T1."ItemCode" = T2."ItemCode"
  LEFT JOIN "OCRD" T3 ON T1."CardCode" = T3."CardCode"
WHERE T0."DocNum" = '{doc_num}' AND T1."Status" != 'C'
""".strip()
# filtros adicionais opcionais concatenados pelo código original (agora via parâmetro):
#   AND (T1."DocNum" BETWEEN {n1} AND {n2})   -- ou = {n1}
#   AND (T1."Status" BETWEEN '{s1}' AND '{s2}') -- ou = '{s1}'
#   ORDER BY T1."DocNum" DESC

OPDE = 'SELECT T0."DocEntry" FROM OWOR T0 WHERE T0."DocNum" = \'{doc_num}\''

QTDE_FALTANTE_OP = 'SELECT T1."ItemCode", T1."PlannedQty" - T1."IssuedQty" FROM OWOR T0 INNER JOIN WOR1 T1 ON T0."DocEntry" = T1."DocEntry" WHERE T0."DocEntry" = \'{doc_entry}\''


# --- Adicionadas em 16/09/2026 ao implementar liberar/replanejar -------------------
# O `mudaStatus` original operava sobre as linhas selecionadas na grade, que já vinham de
# `OPS_MANUTENCAO`. Na CLI não há grade: ou se informam os números das OPs, ou se pedem
# todas as de um pedido. Daí estas duas.

# OPs por número (DocNum), para quando o usuário informa as OPs explicitamente.
OPS_POR_DOCNUM = """
SELECT T0."DocEntry", T0."DocNum", T0."Status", T0."ItemCode", T0."PlannedQty",
       T0."CmpltQty", T0."OriginNum"
FROM OWOR T0
WHERE T0."DocNum" IN ({doc_nums})
ORDER BY T0."DocNum"
""".strip()

# OPs de um pedido. Repete o filtro `Status != 'C'` de `OPS_MANUTENCAO` (grade do legado):
# uma OP cancelada não é candidata a manutenção — o SAP não a libera nem replaneja.
OPS_POR_PEDIDO = """
SELECT T0."DocEntry", T0."DocNum", T0."Status", T0."ItemCode", T0."PlannedQty",
       T0."CmpltQty", T0."OriginNum"
FROM OWOR T0
WHERE T0."OriginNum" = '{doc_num}' AND T0."Status" != 'C'
ORDER BY T0."DocNum"
""".strip()


# --- Adicionadas em 17/09/2026 ao concluir o módulo 3 -----------------------------
# Linhas de componente de uma OP com a quantidade ainda não baixada.
#
# O legado usa `QTDE_FALTANTE_OP` (acima), que devolve só ItemCode e a diferença, sem o
# `LineNum` — e então usa o ÍNDICE da linha no recordset como `BaseLine` da saída de
# insumo. Isso só funciona enquanto a ordem devolvida pelo banco coincidir com a numeração
# das linhas da OP, o que a consulta original nem sequer garante (não tem ORDER BY).
# Aqui o `LineNum` é lido explicitamente e usado como `BaseLine`. Ver seção 7.18 do guia.
LINHAS_FALTANTES_OP = """
SELECT T1."LineNum", T1."ItemCode", T1."PlannedQty", T1."IssuedQty",
       T1."PlannedQty" - T1."IssuedQty" "Faltante"
FROM OWOR T0
  INNER JOIN WOR1 T1 ON T0."DocEntry" = T1."DocEntry"
WHERE T0."DocEntry" = {doc_entry}
ORDER BY T1."LineNum"
""".strip()


# --- Filial (BPLId) dos lançamentos de estoque (21/09/2026) -----------------------
# A Service Layer exige `BPLId` explícito na saída e na entrada de mercadoria quando a
# empresa usa filiais ("Specify an active branch [OIGE.BPLId]"). A DI API do legado
# preenchia sozinha, a partir da filial padrão do usuário logado — por isso o C# não
# informa o campo em lugar nenhum. Mesma classe de divergência das seções 7.9/7.10/7.13.
#
# Três candidatos, em ordem de preferência (ver `_filial_do_movimento`).

FILIAIS_ATIVAS = """
SELECT T0."BPLId" FROM OBPL T0
WHERE IFNULL(T0."Disabled",'N') <> 'Y'
ORDER BY T0."BPLId"
""".strip()

# Nota de nomenclatura: em `OWHS` a coluna é "BPLid" (d minúsculo) e em `ORDR` é "BPLId"
# (D maiúsculo). É inconsistência do próprio B1, não erro de transcrição.
FILIAL_CANDIDATA_DA_OP = """
SELECT
  IFNULL((SELECT W."BPLid" FROM OWHS W WHERE W."WhsCode" = T0."Warehouse"), 0) "filial_deposito_op",
  IFNULL((SELECT MIN(W2."BPLid") FROM WOR1 L
            INNER JOIN OWHS W2 ON W2."WhsCode" = L."wareHouse"
          WHERE L."DocEntry" = T0."DocEntry"), 0) "filial_componentes",
  IFNULL((SELECT R."BPLId" FROM ORDR R WHERE R."DocEntry" = T0."OriginAbs"), 0) "filial_pedido"
FROM OWOR T0
WHERE T0."DocEntry" = {doc_entry}
""".strip()


# --- Série de numeração dos lançamentos de estoque (21/09/2026) -------------------
# Segunda metade da mesma lacuna do `BPLId`: informar só a filial não basta, a Service
# Layer também exige a `Series`. A DI API escolhia a série padrão do usuário logado.
#
# Confirmado no ambiente da Altamira: as séries NÃO são por filial (`NNM1."BPLId"` nulo,
# uma série "Primário" servindo todas), e os documentos existentes em `OIGE` têm
# `Series = 20` com `BPLId = 1`.
#
# `ObjectCode`: '60' = Saída de Mercadoria (OIGE), '59' = Entrada de Mercadoria (OIGN).
#
# Precedência: série da própria filial primeiro; série sem filial depois (serve todas);
# nunca uma série bloqueada. Empate resolvido pelo menor número, para ser determinístico.
SERIE_DO_DOCUMENTO = """
SELECT T0."Series", T0."SeriesName", IFNULL(T0."BPLId", 0) "filial"
FROM NNM1 T0
WHERE T0."ObjectCode" = '{object_code}'
  AND IFNULL(T0."Locked",'N') <> 'Y'
  AND (T0."BPLId" IS NULL OR T0."BPLId" = {filial})
ORDER BY IFNULL(T0."BPLId", 999999) ASC, T0."Series" ASC
""".strip()


# --- Hierarquia das OPs de um pedido (21/09/2026) ---------------------------------
# Para encerrar todas as OPs de um pedido é preciso respeitar a dependência física: a
# saída de insumo de uma OP pai consome o item que uma OP filha produz. Encerrar o pai
# primeiro falha por falta de estoque.
#
# A relação é descoberta pelo item: se o item produzido pela OP B (OWOR."ItemCode")
# aparece como componente da OP A (WOR1."ItemCode"), então B precisa ser encerrada antes
# de A. Não existe campo de "OP pai" no B1 para esta cascata — o vínculo é pelo item.
COMPONENTES_DAS_OPS = """
SELECT T0."DocEntry", T0."ItemCode"
FROM WOR1 T0
WHERE T0."DocEntry" IN ({doc_entries})
ORDER BY T0."DocEntry", T0."LineNum"
""".strip()
