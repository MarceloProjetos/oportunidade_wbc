-- ============================================================================
-- ALTER: nova coluna "Solda" na VW_OS_INTEGRACAO — alinhar o espelho à view HANA
-- Data: 2026-07-15
--
-- A view SBOALTAMIRAPROD.VW_OS_INTEGRACAO ganhou a coluna 50: "Solda" (INTEGER,
-- NOT NULL) — flag por ITEM: 1 = vai para solda, 0 = não vai. Ela devolve a
-- capacidade de identificar solda, perdida na consolidação (o antigo "TIPO" e a
-- tabela vw_os_solda foram dropados em 2026-07-14).
--
-- POR QUE ESTE ARQUIVO EXISTE (não re-rode o vw_os_integracao.sql!):
--   o DDL base começa com `drop table ... cascade` — re-executá-lo APAGARIA a
--   tabela de produção. Para uma tabela que já existe, use este ALTER.
--
-- POR QUE É URGENTE: o pipeline extrai com `SELECT *` e insere via PostgREST
--   casando a coluna por NOME. Coluna que existe na view e NÃO existe na tabela
--   derruba o INSERT com PGRST204 ("Could not find the 'Solda' column ... in the
--   schema cache"). Enquanto este ALTER não rodar, a sync de OS falha.
--
-- Nome entre aspas preserva o case byte-exato da view. Tipo: INTEGER → integer
-- (mesma convenção de N_OP/N_PED/LinhEstrut). Sem NOT NULL de propósito — o
-- espelho é permissivo, como todas as demais colunas (uma carga com dado
-- inesperado não deve quebrar).
-- Idempotente: add column if not exists.
-- ============================================================================

alter table public.vw_os_integracao
  add column if not exists "Solda" integer;

comment on column public.vw_os_integracao."Solda" is
  'Flag por item: 1 = o item vai para solda, 0 = não vai. Origem: VW_OS_INTEGRACAO (INTEGER NOT NULL na view).';

-- Índice parcial: a pergunta típica é "quais itens deste pedido vão para solda?".
-- Parcial (só Solda=1) fica pequeno e serve tanto o filtro por N_PED quanto o count.
create index if not exists idx_os_int_solda
  on public.vw_os_integracao ("N_PED") where "Solda" = 1;

-- OBRIGATÓRIO: sem isto o PostgREST segue com o schema em cache e o 1º INSERT
-- após o ALTER falha com PGRST204.
notify pgrst, 'reload schema';


-- ============================================================================
-- VERIFICAÇÃO (opcional)
-- ============================================================================
-- A coluna existe e é integer?
--   SELECT column_name, data_type, is_nullable
--   FROM information_schema.columns
--   WHERE table_schema='public' AND table_name='vw_os_integracao' AND column_name='Solda';
--
-- Após uma sync (ex.: N_PED 84080) — quantos itens vão para solda?
--   SELECT "Solda", count(*) FROM public.vw_os_integracao
--   WHERE "N_PED" = 84080 GROUP BY "Solda";
