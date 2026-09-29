"""Queries do módulo Manutenção de OP, transcritas de `Controllers/Querys.resx`.
Todas em HANA (SAP B1) — este módulo não acessa o WBC.

Since 28/09/2026 (F7 of docs/PLANO_CONTROLE_PRODUCAO_11.md) every value is a bound parameter
(`?`, passed to `HanaDirectReader.fetch_all(sql, params)`); the text is otherwise the original.
The only thing still formatted in is `{marcadores}` of the two `IN` lists, and it only ever
receives `?, ?, ...` built by `service._marcadores` — never a value.
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
WHERE T0."DocNum" = ? AND T1."Status" != 'C'
""".strip()
# Optional filters appended by `service._monta_filtro`, values as bound parameters
# (the C# original pasted the text box straight into the SQL):
#   AND (T1."DocNum" BETWEEN ? AND ?)   -- or = ?
#   AND (T1."Status" BETWEEN ? AND ?)   -- or = ?
#   ORDER BY T1."DocNum" DESC

# The legacy `OPDE` and `QTDE_FALTANTE_OP` (Querys.resx) were removed on 30/09/2026: nothing
# executed them — DocEntry comes from the listing, and `LINHAS_FALTANTES_OP` below replaced
# the second one.


# --- Adicionadas em 16/09/2026 ao implementar liberar/replanejar -------------------
# O `mudaStatus` original operava sobre as linhas selecionadas na grade, que já vinham de
# `OPS_MANUTENCAO`. Na CLI não há grade: ou se informam os números das OPs, ou se pedem
# todas as de um pedido. Daí estas duas.

# OPs por número (DocNum), para quando o usuário informa as OPs explicitamente.
# How much of the OP's components has already been issued (29/09/2026, F6 of
# docs/PLANO_API_MANUTENCAO_OP.md): more than zero = a goods issue (OIGE) was posted, and the
# OP must not go back to Planejada until that issue is cancelled in the SAP. Checked in PROD on
# 29/09: this sum and "a non-cancelled IGE1 line based on the OP" select the same 63,183 OPs.
# No goods issue of an OP had ever been cancelled, so whether the sum drops back after a
# cancellation is unverified — if it does not, Replanejar refuses too much (the safe side).
_BAIXADA = (
    '(SELECT IFNULL(SUM(L."IssuedQty"), 0) FROM WOR1 L WHERE L."DocEntry" = T0."DocEntry") "Baixada"'
)

OPS_POR_DOCNUM = f"""
SELECT T0."DocEntry", T0."DocNum", T0."Status", T0."ItemCode", T0."PlannedQty",
       T0."CmpltQty", T0."OriginNum", {_BAIXADA}
FROM OWOR T0
WHERE T0."DocNum" IN ({{marcadores}})
ORDER BY T0."DocNum"
""".strip()

# OPs de um pedido. Repete o filtro `Status != 'C'` de `OPS_MANUTENCAO` (grade do legado):
# uma OP cancelada não é candidata a manutenção — o SAP não a libera nem replaneja.
OPS_POR_PEDIDO = f"""
SELECT T0."DocEntry", T0."DocNum", T0."Status", T0."ItemCode", T0."PlannedQty",
       T0."CmpltQty", T0."OriginNum", {_BAIXADA}
FROM OWOR T0
WHERE T0."OriginNum" = ? AND T0."Status" != 'C'
ORDER BY T0."DocNum"
""".strip()

# The issued quantity of every OP of one sales order, by DocNum — for the API's search, which
# reads the screen's grid (`OPS_MANUTENCAO`) and must not add a column to it.
BAIXADA_DAS_OPS_DO_PEDIDO = f"""
SELECT T0."DocNum", {_BAIXADA}
FROM OWOR T0
WHERE T0."OriginNum" = ?
""".strip()


# --- Adicionadas em 17/09/2026 ao concluir o módulo 3 -----------------------------
# Linhas de componente de uma OP com a quantidade ainda não baixada.
#
# O legado usa `QTDE_FALTANTE_OP` (Querys.resx), que devolve só ItemCode e a diferença, sem o
# `LineNum` — e então usa o ÍNDICE da linha no recordset como `BaseLine` da saída de
# insumo. Isso só funciona enquanto a ordem devolvida pelo banco coincidir com a numeração
# das linhas da OP, o que a consulta original nem sequer garante (não tem ORDER BY).
# Aqui o `LineNum` é lido explicitamente e usado como `BaseLine`. Ver seção 7.18 do guia.
LINHAS_FALTANTES_OP = """
SELECT T1."LineNum", T1."ItemCode", T1."PlannedQty", T1."IssuedQty",
       T1."PlannedQty" - T1."IssuedQty" "Faltante"
FROM OWOR T0
  INNER JOIN WOR1 T1 ON T0."DocEntry" = T1."DocEntry"
WHERE T0."DocEntry" = ?
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
WHERE T0."DocEntry" = ?
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
WHERE T0."ObjectCode" = ?
  AND IFNULL(T0."Locked",'N') <> 'Y'
  AND (T0."BPLId" IS NULL OR T0."BPLId" = ?)
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
WHERE T0."DocEntry" IN ({marcadores})
ORDER BY T0."DocEntry", T0."LineNum"
""".strip()
