/* =====================================================================
   VW_INO_OPORTUNIDADE_INTEGRACAO
   ---------------------------------------------------------------------
   Retrato de cada oportunidade da integração WBC x SAP, com o documento
   vigente já resolvido — o suficiente para decidir se ela precisa ser
   atualizada, sem uma única ida ao Service Layer.

   Substitui, por oportunidade, quatro consultas do legado:
     GetDocNumOportunidades   -> as colunas de OOPR
     GetIntIdWBCOrcamentos    -> COT_DOCENTRY IS NOT NULL
     GetIdOrcamentosPedido    -> PED_DOCENTRY IS NOT NULL
     GetStatusAtualCot        -> COT_REVISAO
     ChecaPNPedido            -> PED_CARDCODE

   ---------------------------------------------------------------------
   ATENÇÃO AO SCHEMA
   ---------------------------------------------------------------------
   Crie a view **dentro da company que recebe a escrita** — a mesma do
   SL_COMPANY_DB da integração. Criar em SBOALTAMIRAPROD e ler a partir da
   homologação faria a integração decidir com o estado da produção e
   escrever na homologação, sem nenhum sintoma visível.

   Em homologação:  CREATE VIEW "SBOALTAMIRAHOMOLOG"."VW_INO_OPORTUNIDADE_INTEGRACAO"
   Em produção:     CREATE VIEW "SBOALTAMIRAPROD"."VW_INO_OPORTUNIDADE_INTEGRACAO"

   PRIVILÉGIOS (conferido em 31/08/2026)
   O usuário ALTAMIRA tem `CREATE ANY` **apenas no schema ALTAMIRA** — nas
   companies (SBOALTAMIRAHOMOLOG / SBOALTAMIRAPROD) ele só tem SELECT. Então:

     a) view dentro da company  -> precisa do DBA, e depois
        GRANT SELECT ON <schema>."VW_INO_OPORTUNIDADE_INTEGRACAO" TO ALTAMIRA;

     b) view no schema ALTAMIRA -> o próprio usuário cria, sem DBA. Mas aí o
        nome precisa dizer de qual company ela lê (ex.:
        "ALTAMIRA"."VW_INO_OPORTUNIDADE_HOMOLOG"), senão volta o risco de ler
        uma company e escrever noutra.
   ===================================================================== */

CREATE VIEW "SBOALTAMIRAHOMOLOG"."VW_INO_OPORTUNIDADE_INTEGRACAO" AS
SELECT
    /* --- identificação ------------------------------------------------ */
    O."OpprId",                                   -- chave da oportunidade
    O."U_ORCNUM_WBC",                             -- número do orçamento no WBC
    O."OpenDate",                                 -- usado para a janela de busca
    O."CardCode",                                 -- parceiro atual
    O."SlpCode",                                  -- vendedor
    O."CprCode",                                  -- contato

    /* --- estado da oportunidade --------------------------------------- */
    O."Status",                                   -- 'O' aberta, 'L' perdida, 'W' vendida.
                                                  -- É a ÚNICA prova de que o encerramento
                                                  -- aconteceu: U_INO_StatusWBC é espelhado
                                                  -- a cada ciclo, muito antes disso.
    O."U_INO_StatusWBC",                          -- SitCode do WBC espelhado (texto)
    O."U_INO_Update",                             -- 'Y' = VALORES alterados no WBC.
                                                  -- NÃO é marca de troca de parceiro.
    O."U_INO_PN_Correc",                          -- parceiro corrigido; comparar com
                                                  -- PED_CARDCODE para saber se a troca
                                                  -- ainda está pendente

    /* --- cotação vigente ---------------------------------------------- */
    Q."DocEntry"        AS "COT_DOCENTRY",        -- NULL = não existe cotação
    Q."DocNum"          AS "COT_DOCNUM",
    Q."U_INO_VERSAOWBC" AS "COT_REVISAO",         -- revisão do WBC já aplicada
    Q."DocStatus"       AS "COT_DOCSTATUS",

    /* --- pedido vigente ------------------------------------------------ */
    R."DocEntry"        AS "PED_DOCENTRY",        -- NULL = não existe pedido
    R."DocNum"          AS "PED_DOCNUM",
    R."U_INO_VERSAOWBC" AS "PED_REVISAO",
    R."DocStatus"       AS "PED_DOCSTATUS",
    R."CardCode"        AS "PED_CARDCODE"         -- equivalente do ChecaPNPedido

FROM "SBOALTAMIRAHOMOLOG"."OOPR" O

/* Cotação vigente: a mais recente entre as NÃO canceladas.
   `DocEntry DESC` é o mesmo critério do legado e do Service Layer
   ($orderby=DocEntry desc&$top=1 com Cancelled eq 'tNO'). */
LEFT JOIN (
    SELECT "U_INO_COTWBC", "DocEntry", "DocNum", "U_INO_VERSAOWBC", "DocStatus",
           ROW_NUMBER() OVER (
               PARTITION BY "U_INO_COTWBC" ORDER BY "DocEntry" DESC
           ) AS "RN"
    FROM "SBOALTAMIRAHOMOLOG"."OQUT"
    WHERE "CANCELED" = 'N'
      AND LENGTH("U_INO_COTWBC") > 0
) Q
  ON  Q."U_INO_COTWBC" = O."U_ORCNUM_WBC"
  AND Q."RN" = 1

/* Pedido vigente: mesmo critério. */
LEFT JOIN (
    SELECT "U_INO_COTWBC", "DocEntry", "DocNum", "U_INO_VERSAOWBC", "DocStatus",
           "CardCode",
           ROW_NUMBER() OVER (
               PARTITION BY "U_INO_COTWBC" ORDER BY "DocEntry" DESC
           ) AS "RN"
    FROM "SBOALTAMIRAHOMOLOG"."ORDR"
    WHERE "CANCELED" = 'N'
      AND LENGTH("U_INO_COTWBC") > 0
) R
  ON  R."U_INO_COTWBC" = O."U_ORCNUM_WBC"
  AND R."RN" = 1

WHERE O."U_INO_IntegrouWBC" = 'Y'      -- participa da integração
  AND LENGTH(O."U_ORCNUM_WBC") > 0;    -- tem número de orçamento

/* ---------------------------------------------------------------------
   A janela de datas NÃO entra na view de propósito: é parâmetro do
   worker (MESES_DE_JANELA) e mudá-la não pode exigir DBA.

       SELECT * FROM "SBOALTAMIRAHOMOLOG"."VW_INO_OPORTUNIDADE_INTEGRACAO"
       WHERE "OpenDate" >= ?
       ORDER BY "OpprId" DESC;

   --- Como a decisão lê estas colunas --------------------------------

   sem cotação ......... COT_DOCENTRY IS NULL
   revisão congelada ... COT_REVISAO >= revisão do WBC
   sem pedido .......... PED_DOCENTRY IS NULL
   troca de PN pendente. LENGTH(U_INO_PN_Correc) > 0
                         AND PED_DOCENTRY IS NOT NULL
                         AND PED_CARDCODE <> U_INO_PN_Correc
   já encerrada ........ Status IN ('L','W')

   --- Por que a view NÃO decide sozinha -------------------------------
   Ela traz só o lado SAP. O SitCode e a revisão vigentes vivem no WBC
   (SQL Server), e o HANA não os alcança: SYS.REMOTE_SOURCES está vazia e
   SYS.VIRTUAL_TABLES tem 0 linhas — não há SDA configurado. O worker
   continua fazendo duas leituras, e é a combinação delas que decide.

   --- Conferido em homologação em 31/08/2026 --------------------------
   10.217 oportunidades na view
      297  sem cotação
    7.658  sem pedido
    3.421  já encerradas
        4  com troca de PN pendente
   --------------------------------------------------------------------- */
