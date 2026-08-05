-- ============================================================================
-- ALTER: "U_INO_D_Adicionais" na VW_OS_INTEGRACAO — alinhar o espelho à view HANA
-- Data: 2026-08-05
--
-- A view SBOALTAMIRAPROD.VW_OS_INTEGRACAO ganhou 1 coluna na POSIÇÃO 6 (logo
-- depois de "DescItemPED"):
--
--   6) "U_INO_D_Adicionais"  NVARCHAR(5000)
--
-- É a UDF "Dados Adicionais" da LINHA do documento (existe em RDR1 e QUT1 como
-- NCLOB; a view a entrega recortada em NVARCHAR(5000)). Portanto o valor é POR
-- ITEM, não do cabeçalho do pedido — como CodItemPED/DescItemPED.
--
-- URGÊNCIA (medido em 2026-08-05): o pipeline extrai com `SELECT *` e insere via
--   PostgREST casando a coluna por NOME. Coluna que existe na view e NÃO existe
--   na tabela derruba o INSERT com PGRST204 ("Could not find the '<col>' column
--   ... in the schema cache"). A sync de OS JÁ ESTAVA FALHANDO: o log
--   sincronizacao_log_os_integracao tem sucesso às 17:03 (NPED 84229) e três
--   falhas seguidas às 19:13-19:14 (NPED 84227) — o intervalo em que a coluna
--   entrou na view. Enquanto este ALTER não rodar, NENHUM pedido sincroniza.
--
-- NÃO re-rode o vw_os_integracao.sql: o DDL base começa com `drop table ...
--   cascade` e APAGARIA a tabela de produção. Para tabela existente, use este
--   ALTER (mesma decisão do alter das flags de processo, 2026-07-15).
--
-- Tipo: NVARCHAR(5000) → `text` (o espelho não replica limite de tamanho; text
-- no PostgreSQL não tem custo extra sobre varchar(n)). Sem NOT NULL, como todas
-- as demais — o espelho é permissivo de propósito.
-- Idempotente: add column if not exists.
-- ============================================================================

alter table public.vw_os_integracao
  add column if not exists "U_INO_D_Adicionais" text;

comment on column public.vw_os_integracao."U_INO_D_Adicionais" is
  'Dados Adicionais da LINHA do documento (UDF U_INO_D_Adicionais de RDR1/QUT1). Origem: VW_OS_INTEGRACAO, posição 6, NVARCHAR(5000). Por ITEM — não é campo de cabeçalho.';


-- ----------------------------------------------------------------------------
-- OBRIGATÓRIO: recarregar o cache do schema do PostgREST — sem isto o INSERT do
-- pipeline continua devolvendo PGRST204 até o cache recarregar sozinho.
-- ----------------------------------------------------------------------------
notify pgrst, 'reload schema';


-- ============================================================================
-- VERIFICAÇÃO (rodar depois do ALTER)
-- ============================================================================
-- A coluna existe?
--   SELECT column_name, data_type FROM information_schema.columns
--   WHERE table_schema='public' AND table_name='vw_os_integracao'
--     AND column_name='U_INO_D_Adicionais';
--
-- Depois de re-sincronizar um pedido (ex.: 84227), veio dado?
--   SELECT count(*) AS linhas,
--          count("U_INO_D_Adicionais") AS com_adicionais
--   FROM public.vw_os_integracao WHERE "N_PED" = 84227;
