"""Consultas ao WBC — **somente leitura**, sempre parametrizadas.

Diferenças deliberadas em relação ao SQL do sistema legado (`Querys.resx`):

1. **Parâmetros nomeados** (`:orcnum`) no lugar de `String.Format`. Além de
   eliminar a classe de bugs em que uma aspas simples no dado quebra a consulta,
   é o que permite manter a promessa de que a integração nunca monta SQL
   concatenando valores.
2. **`COALESCE` no lugar de `ISNULL`.** `ISNULL` só existe no SQL Server;
   `COALESCE` é padrão e funciona também em SQLite — o que torna as consultas
   testáveis sem um SQL Server à mão, sem manter duas versões do SQL.
3. **Sem `GROUP BY` sobre todas as colunas.** O legado agrupava por toda a lista
   de campos, o que só servia para deduplicar linhas; o agrupamento em
   cabeçalho + itens é feito aqui, em Python, onde é explícito e testável.
"""

from __future__ import annotations

from typing import Final

# Cabeçalho + itens de um orçamento específico.
# Equivalente ao `GetOrcsWBC` do legado.
ORCAMENTO_POR_NUMERO: Final = """
SELECT
    COALESCE(A.ORCNUM, '')                    AS orcnum,
    COALESCE(A.REVISAO, '')                   AS revisao,
    COALESCE(
        (SELECT S.SITCOD
           FROM INTEGRACAO_ORCSIT S
          WHERE S.ORCNUM = A.ORCNUM
            AND S.idIntegracao_OrcSit = (
                SELECT MAX(S2.idIntegracao_OrcSit)
                  FROM INTEGRACAO_ORCSIT S2
                 WHERE S2.ORCNUM = A.ORCNUM
                   AND S2.ORCALTDTH = (
                       SELECT MAX(S3.ORCALTDTH)
                         FROM INTEGRACAO_ORCSIT S3
                        WHERE S3.ORCNUM = A.ORCNUM))),
        A.SITCOD, 0
    )                                     AS sitcode,
    COALESCE(A.CLINOM, '')                    AS cliente_nome,
    COALESCE(A.REPCOD, '')                    AS representante,
    COALESCE(A.CLIMUN, '')                    AS municipio,
    COALESCE(A.ESTCOD, '')                    AS uf,
    A.ORCDAT                                  AS data_orcamento,
    A.ORCALTDTH                               AS data_alteracao,
    COALESCE(D.ORCPERCOM, 0)                  AS percentual_comissao,
    COALESCE(D.ORCVALCOM, 0)                  AS valor_comissao,
    COALESCE(D.ORCBAS2, 0)                    AS base2,
    B.ORCIMP_DATA_ULTIMA_ALTERACAO            AS data_ultima_alteracao_impressao,
    COALESCE(B.ORCIMP_RETORNO, 0)             AS retorno,
    COALESCE(B.ORCIMP_NEGOCIACAO, 0)          AS negociacao,
    COALESCE(B.ORCIMP_INDICE_VENDAS, 0)       AS indice_vendas,
    COALESCE(B.ORCITM, 0)                     AS orcitm,
    COALESCE(B.GRPCOD, 0)                     AS grupo,
    COALESCE(B.SUBGRPCOD, 0)                  AS subgrupo,
    COALESCE(B.ORCPRDCOD, '')                 AS produto,
    B.ORCPRDQTD                               AS quantidade,
    COALESCE(B.ORCTXT, '')                    AS texto,
    COALESCE(B.ORCVAL, 0.0)                   AS valor,
    COALESCE(B.ORCIPI, 0.0)                   AS ipi,
    COALESCE(B.ORCICM, 0.0)                   AS icms,
    COALESCE(B.idIntegracao_OrcImp, 0)        AS id_integracao,
    -- Dados de impressão: alimentam o snapshot do OrcDetalhe. Todos vêm de
    -- INTEGRACAO_ORCIMP, que é de onde o legado os lê (`NovaTabelaQuot`).
    COALESCE(B.ORCVALVND, 0)                  AS imp_valor_venda,
    COALESCE(B.ORCVALLST, 0)                  AS imp_valor_lista,
    COALESCE(B.ORCVALINV, 0)                  AS imp_valor_investimento,
    COALESCE(B.ORCVALLUC, 0)                  AS imp_valor_lucro,
    COALESCE(B.ORCVALEXP, 0)                  AS imp_valor_expedicao,
    COALESCE(B.ORCVALCOM, 0)                  AS imp_valor_comissao,
    COALESCE(B.ORCPERCOM, 0)                  AS imp_percentual_comissao,
    COALESCE(B.ORCVALTRP, 0)                  AS imp_valor_transporte,
    COALESCE(B.ORCVALEMB, 0)                  AS imp_valor_embalagem,
    COALESCE(B.ORCVALMON, 0)                  AS imp_valor_montagem,
    COALESCE(B.ORCBAS1, 0)                    AS imp_base1,
    COALESCE(B.ORCBAS2, 0)                    AS imp_base2,
    COALESCE(B.ORCBAS3, 0)                    AS imp_base3,
    COALESCE(B.CLICOD, 0)                     AS imp_cliente_codigo,
    COALESCE(B.CLICONCOD, 0)                  AS imp_contato_codigo,
    COALESCE(B.CLICON, '')                    AS imp_contato,
    COALESCE(B.PGTCOD, '')                    AS imp_pagamento_codigo,
    COALESCE(B.ORCPGT, '')                    AS imp_pagamento_texto,
    COALESCE(B.TIPMONCOD, '')                 AS imp_montagem_tipo,
    COALESCE(B.PRZENT, 0)                     AS imp_prazo_entrega,
    COALESCE(B.ORCIMP_REVISAO, '')            AS imp_revisao,
    COALESCE(B.ORCIMP_EMAIL, '')              AS imp_email,
    COALESCE(B.ORCIMP_FONE, '')               AS imp_fone,
    COALESCE(B.ORCIMP_CIDADE, '')             AS imp_cidade,
    COALESCE(B.ORCIMP_UF, '')                 AS imp_uf,
    COALESCE(B.ORCIMP_TIPO_VENDA, '')         AS imp_tipo_venda,
    COALESCE(B.ORCIMP_TRANSPORTE, '')         AS imp_transporte,
    COALESCE(B.ORCIMP_ACABAMENTO, '')         AS imp_acabamento,
    COALESCE(B.ORCIMP_MONTAGEM, '')           AS imp_montagem,
    COALESCE(B.ORCIMP_TABELA_PRECO, '')       AS imp_tabela_preco
FROM INTEGRACAO_ORCLST A
LEFT JOIN INTEGRACAO_ORCIMP B ON A.ORCNUM = B.ORCNUM
LEFT JOIN INTEGRACAO_ORCCAB D ON A.ORCNUM = D.ORCNUM
WHERE A.ORCNUM = :orcnum
ORDER BY B.ORCITM
"""
"""Cabeçalho e linhas de um orçamento.

**O SitCode vem de `INTEGRACAO_ORCSIT`, não de `INTEGRACAO_ORCLST`.** É lá que
está a situação real da proposta. `ORCLST.SITCOD` é uma cópia que diverge:
8.560 dos 59.265 orçamentos (14%) têm valor diferente entre as duas tabelas.

`ORCSIT` guarda uma linha por mudança de situação, então a atual é a mais
recente — critério de desempate: maior `ORCALTDTH` e, entre elas, maior
`idIntegracao_OrcSit` (há um caso de 2017 com duas linhas idênticas, e sem
desempate determinístico o resultado ficaria a critério do plano de execução).

`ORCLST.SITCOD` fica como **reserva**: 1.660 orçamentos da base não têm linha em
`ORCSIT`, e nesses o valor antigo é melhor que zero — zero significaria "abaixo
do mínimo" e a integração simplesmente ignoraria o orçamento em silêncio.

Medido antes de trocar: nos 20 orçamentos da janela de integração, **nenhuma
decisão muda** com a nova fonte.


**As linhas vêm de `INTEGRACAO_ORCIMP`, não de `INTEGRACAO_ORCITM`** — e isso
não é detalhe de implementação, é correção de um erro que passaria despercebido.

`INTEGRACAO_ORCITM` *parece* a tabela de itens pelo nome, e é a que uma leitura
apressada da especificação sugere. Mas ela **parou de receber dados**: o maior
`ORCNUM` presente nela é `00125478`, enquanto os orçamentos que a integração
precisa processar hoje já passam de `00125535`. Lendo dela, todo orçamento
recente vem **sem nenhuma linha** — e um documento sem linha é recusado pelo SAP
com `-5002 Document total value must be zero or greater than zero`. Era
exatamente esse o sintoma observado no ambiente real.

O sistema legado sempre leu de `INTEGRACAO_ORCIMP` (ver `NovaTabelaQuotLinha`
em `Querys.resx`). Ela é desnormalizada: os campos de cabeçalho se repetem em
cada linha do orçamento. Daí os dados de impressão (`ORCIMP_*`) virem agora
dessa mesma tabela, o que de quebra elimina o `LEFT JOIN` extra que multiplicava
linhas.

`ORCPRDQTD` vem **sem `COALESCE` de propósito**: `NULL` precisa chegar como
`None` até `ItemOrcamentoWbc.quantidade_para_documento`, que é quem decide o
fallback para 1 — e, hoje, `ORCPRDQTD` é `NULL` em 100% das 20.997 linhas da
tabela. Substituir o nulo aqui esconderia esse fato de quem lê o código.
"""

# Situação atual de um orçamento, sem trazer os itens.
# Consulta leve, para o worker decidir se vale a pena carregar o resto.
SITUACAO_POR_NUMERO: Final = """
SELECT
    COALESCE(A.ORCNUM, '')  AS orcnum,
    COALESCE(A.REVISAO, '') AS revisao,
    COALESCE(
        (SELECT S.SITCOD
           FROM INTEGRACAO_ORCSIT S
          WHERE S.ORCNUM = A.ORCNUM
            AND S.idIntegracao_OrcSit = (
                SELECT MAX(S2.idIntegracao_OrcSit)
                  FROM INTEGRACAO_ORCSIT S2
                 WHERE S2.ORCNUM = A.ORCNUM
                   AND S2.ORCALTDTH = (
                       SELECT MAX(S3.ORCALTDTH)
                         FROM INTEGRACAO_ORCSIT S3
                        WHERE S3.ORCNUM = A.ORCNUM))),
        A.SITCOD, 0
    )   AS sitcode
FROM INTEGRACAO_ORCLST A
WHERE A.ORCNUM = :orcnum
"""
"""Situação atual, sem carregar as linhas — mesma fonte de SitCode da consulta
completa. As duas **precisam** concordar: se divergissem, a decisão mudaria
conforme o caminho que carregou o dado."""

SITUACOES_EM_LOTE: Final = """
SELECT
    COALESCE(A.ORCNUM, '')  AS orcnum,
    COALESCE(A.REVISAO, '') AS revisao,
    COALESCE(
        (SELECT S.SITCOD
           FROM INTEGRACAO_ORCSIT S
          WHERE S.ORCNUM = A.ORCNUM
            AND S.idIntegracao_OrcSit = (
                SELECT MAX(S2.idIntegracao_OrcSit)
                  FROM INTEGRACAO_ORCSIT S2
                 WHERE S2.ORCNUM = A.ORCNUM
                   AND S2.ORCALTDTH = (
                       SELECT MAX(S3.ORCALTDTH)
                         FROM INTEGRACAO_ORCSIT S3
                        WHERE S3.ORCNUM = A.ORCNUM))),
        A.SITCOD, 0
    )   AS sitcode
FROM INTEGRACAO_ORCLST A
WHERE A.ORCNUM IN :orcnums
"""
"""A mesma situação de `SITUACAO_POR_NUMERO`, para vários orçamentos de uma vez.

Existe por causa de uma conta simples: desde que a leitura do SAP passou a vir
do HANA numa consulta só, o ciclo avalia a janela inteira — 1.785 orçamentos em
agosto de 2026. A 29 ms por consulta, perguntar um a um levaria 0,8 minuto **por
ciclo** só para descobrir quem mudou de situação.

O texto é deliberadamente idêntico ao da consulta unitária, com `IN` no lugar do
`=`. As duas **precisam** concordar: se divergissem, a decisão mudaria conforme o
caminho que carregou o dado — e o teste `test_situacao_em_lote_usa_a_mesma_fonte`
compara as duas letra a letra."""

# Orçamentos alterados a partir de uma data, para varredura incremental.
# Não tem equivalente no legado, que dependia inteiramente da lista vinda do SAP.
ORCAMENTOS_ALTERADOS_DESDE: Final = """
SELECT
    COALESCE(A.ORCNUM, '')  AS orcnum,
    COALESCE(A.REVISAO, '') AS revisao,
    COALESCE(A.SITCOD, 0)   AS sitcode
FROM INTEGRACAO_ORCLST A
WHERE A.ORCALTDTH >= :desde
  AND COALESCE(A.SITCOD, 0) > :sitcode_minimo
ORDER BY A.ORCALTDTH
"""


# Linhas detalhadas do orçamento — a árvore de produtos.
# Equivale ao `GetTableValdixson` do legado, e alimenta as linhas do snapshot
# do OrcDetalhe (`INO_ORC_LINHA`).
LINHAS_DA_ARVORE: Final = """
SELECT DISTINCT
    COALESCE(ARV.ORCNUM, '')                  AS orcnum,
    COALESCE(ARV.GRPCOD, 0)                   AS grupo,
    COALESCE(ARV.SUBGRPCOD, 0)                AS subgrupo,
    CASE WHEN EXISTS (
        SELECT 1 FROM INTEGRACAO_ORCPRD P
        WHERE P.ORCNUM = ARV.ORCNUM
          AND P.ORCITM = 0
          AND P.PRDCOD = ARV.PRDCOD
    ) THEN 0 ELSE COALESCE(ARV.ORCITM, 0) END AS orcitm,
    COALESCE(
        (SELECT MAX(P.PRDCOD) FROM INTEGRACAO_ORCPRD P
         WHERE P.ORCNUM = ARV.ORCNUM AND P.PRDDSC = ARV.PRDDSC),
        ARV.PRDCOD, ''
    )                                         AS produto,
    COALESCE(ARV.ORCPRDARV_NIVEL, 0)          AS nivel,
    COALESCE(ARV.CORCOD, '')                  AS cor,
    COALESCE(ARV.PRDDSC, '')                  AS descricao,
    COALESCE(ARV.ORCQTD, 0)                   AS quantidade,
    COALESCE(
        (SELECT MAX(P.ORCTOT) FROM INTEGRACAO_ORCPRD P
         WHERE P.ORCNUM = ARV.ORCNUM
           AND P.PRDDSC = ARV.PRDDSC
           AND P.ORCQTD = ARV.ORCQTD),
        ARV.ORCTOT, 0
    )                                         AS total,
    COALESCE(ARV.ORCPES, 0)                   AS peso,
    COALESCE(ARV.idIntegracao_OrcPrdArv, 0)   AS id_integracao
FROM INTEGRACAO_ORCPRDARV ARV
WHERE ARV.ORCNUM = :orcnum
ORDER BY COALESCE(ARV.idIntegracao_OrcPrdArv, 0)
"""
"""Linhas da árvore de produtos, para o snapshot do OrcDetalhe.

Três pontos em que esta consulta difere do `GetTableValdixson` do legado, todos
deliberados:

1. **O código do produto** é resolvido por `PRDDSC` contra `INTEGRACAO_ORCPRD`,
   como no legado — a árvore guarda um código que nem sempre é o comercial.
   O legado usa `TOP 1` sem `ORDER BY`, o que deixa o resultado a critério do
   plano de execução; aqui é `MAX`, que é determinístico. Havendo um só
   candidato — o caso normal — os dois devolvem o mesmo valor.
2. **O total** vem de `INTEGRACAO_ORCPRD` casando `(ORCNUM, PRDDSC, ORCQTD)`,
   com o total da árvore como reserva. O legado faz isso com **uma consulta por
   linha** (`getDocTot` dentro do laço); aqui é uma subconsulta, o que evita o
   N+1 num orçamento com dezenas de linhas.
3. **O `ORCITM` zerado** quando o produto aparece em `INTEGRACAO_ORCPRD` com
   `ORCITM = 0`. O legado obtém o mesmo efeito com um `LEFT JOIN` cujo `CASE`
   só pode resultar em `0` (a subconsulta já filtra por `ORCITM = '0'`); com
   `EXISTS` a intenção fica explícita e não há risco de o join multiplicar
   linhas.
"""


# Peso de cada item do orçamento, para o `Weight1` das linhas do pedido.
# Equivale ao `GetPesoPedido` do legado (`Querys.resx`), que faz a mesma soma
# sobre o snapshot já gravado no SAP:
#
#   SELECT sum(T0."U_INO_PESO") FROM "@INO_ORC_LINHA" T0
#   WHERE T0."DocEntry" = '{0}' AND T0."U_INO_NIVEL" = 1 AND T0."U_INO_ORCITM" = '{1}'
PESOS_NIVEL_1_POR_ITEM: Final = """
SELECT
    COALESCE(ARV.ORCITM, 0)  AS orcitm,
    SUM(COALESCE(ARV.ORCPES, 0)) AS peso
FROM INTEGRACAO_ORCPRDARV ARV
WHERE ARV.ORCNUM = :orcnum
  AND ARV.ORCPRDARV_NIVEL = 1
GROUP BY ARV.ORCITM
"""
"""Peso de cada item, somando **só o nível 1** da árvore de produtos.

**O nível importa, e somar tudo estaria errado.** `INTEGRACAO_ORCPRDARV` é uma
estrutura de produto: o nível 1 é a peça que embarca, o 2 são seus componentes e
o 3 a matéria-prima — e cada nível **repõe a mesma massa**, decomposta. No
orçamento `00124853`, a coluna de 348,02 kg no nível 1 reaparece no nível 2 como
341,83 kg de aço mais 6,19 kg de tinta. Somar os níveis conta o mesmo aço duas
ou três vezes: no item 1 daquele orçamento dá 1.818,70 kg no lugar de 760,65; no
item 1 do `00125527`, 2.980 kg no lugar de 1.095.

O legado filtra `U_INO_NIVEL = 1` pelo mesmo motivo, sobre o snapshot que ele
grava no SAP (`@INO_ORC_LINHA`). Aqui a soma vem direto do WBC: o número é o
mesmo e a leitura não depende de o snapshot já ter sido gravado.

**Nem todo item tem peso.** A árvore existe para 5.397 dos ~59 mil orçamentos da
base. O recorte que importa, porém, é outro: entre os orçamentos que chegam a
virar pedido (SitCode ≥ 60), **314 de 316 linhas têm nível 1** — a árvore é
montada justamente quando o negócio fecha. As linhas sem peso ficam de fora do
dicionário, e quem monta a linha do documento simplesmente não envia o campo.
"""
