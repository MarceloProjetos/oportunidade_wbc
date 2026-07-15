-- ============================================================================
-- ALTER: flags de PROCESSO na VW_OS_INTEGRACAO — alinhar o espelho à view HANA
-- Data: 2026-07-15
--
-- A view SBOALTAMIRAPROD.VW_OS_INTEGRACAO ganhou 4 colunas (50-53), todas
-- INTEGER NOT NULL, flags POR ITEM (1 = o item passa pelo processo, 0 = não):
--
--   50) "Solda"     51) "Pintura"     52) "Almox"     53) "Exped"
--
-- Elas substituem, por 4 colunas, as 4 TABELAS que a consolidação de 14/07
-- dropou (vw_os_solda, vw_os_pintura_v0, vw_os_almox_impressao,
-- vw_os_exped_impressao_v2) — antes o processo era identificado pela tabela em
-- que a linha aparecia (e pela coluna "TIPO"); agora é uma flag na tabela única.
--
-- POR QUE ESTE ARQUIVO EXISTE (não re-rode o vw_os_integracao.sql!):
--   o DDL base começa com `drop table ... cascade` — re-executá-lo APAGARIA a
--   tabela de produção. Para uma tabela que já existe, use este ALTER.
--
-- POR QUE É URGENTE: o pipeline extrai com `SELECT *` e insere via PostgREST
--   casando a coluna por NOME. Coluna que existe na view e NÃO existe na tabela
--   derruba o INSERT com PGRST204 ("Could not find the '<col>' column ... in the
--   schema cache"). Enquanto este ALTER não rodar, a sync de OS falha.
--
-- Nomes entre aspas preservam o case byte-exato da view. Tipo: INTEGER → integer
-- (mesma convenção de N_OP/N_PED/LinhEstrut). Sem NOT NULL de propósito — o
-- espelho é permissivo, como todas as demais colunas (uma carga com dado
-- inesperado não deve quebrar).
-- Idempotente: add column if not exists (a "Solda" já foi aplicada antes neste
-- mesmo dia — re-rodar o bloco inteiro é no-op para ela).
-- ============================================================================

-- U_INO_ORCITM veio junto na mesma revisão da view (posição 50) e foi o que
-- derrubou o insert com PGRST204 até ser adicionada aqui — a view tem 54
-- colunas, não 53. Lição: gerar o ALTER a partir das colunas REAIS da view
-- (SELECT * ... WHERE "N_PED" = -1), nunca de uma lista transcrita à mão.
alter table public.vw_os_integracao
  add column if not exists "U_INO_ORCITM" text,
  add column if not exists "Solda"   integer,
  add column if not exists "Pintura" integer,
  add column if not exists "Almox"   integer,
  add column if not exists "Exped"   integer;

comment on column public.vw_os_integracao."Solda" is
  'Flag por item: 1 = o item vai para solda, 0 = não vai. Origem: VW_OS_INTEGRACAO (INTEGER NOT NULL na view).';
comment on column public.vw_os_integracao."Pintura" is
  'Flag por item: 1 = o item vai para pintura, 0 = não vai. Origem: VW_OS_INTEGRACAO (INTEGER NOT NULL na view).';
comment on column public.vw_os_integracao."Almox" is
  'Flag por item: 1 = o item passa pelo almoxarifado, 0 = não passa. Origem: VW_OS_INTEGRACAO (INTEGER NOT NULL na view).';
comment on column public.vw_os_integracao."Exped" is
  'Flag por item: 1 = o item passa pela expedição, 0 = não passa. Origem: VW_OS_INTEGRACAO (INTEGER NOT NULL na view).';

-- Índices parciais: a pergunta típica é "quais itens deste pedido vão para X?".
-- Parciais (só =1) ficam pequenos e servem tanto o filtro por N_PED quanto o count.
create index if not exists idx_os_int_solda
  on public.vw_os_integracao ("N_PED") where "Solda" = 1;
create index if not exists idx_os_int_pintura
  on public.vw_os_integracao ("N_PED") where "Pintura" = 1;
create index if not exists idx_os_int_almox
  on public.vw_os_integracao ("N_PED") where "Almox" = 1;
create index if not exists idx_os_int_exped
  on public.vw_os_integracao ("N_PED") where "Exped" = 1;

-- OBRIGATÓRIO: sem isto o PostgREST segue com o schema em cache e o 1º INSERT
-- após o ALTER falha com PGRST204.
notify pgrst, 'reload schema';


-- ============================================================================
-- VERIFICAÇÃO (opcional)
-- ============================================================================
-- As 4 colunas existem e são integer?
--   SELECT column_name, data_type FROM information_schema.columns
--   WHERE table_schema='public' AND table_name='vw_os_integracao'
--     AND column_name IN ('Solda','Pintura','Almox','Exped') ORDER BY column_name;
--
-- Após uma sync (ex.: N_PED 84172) — quantos itens por processo?
--   SELECT count(*) FILTER (WHERE "Solda"   = 1) AS solda,
--          count(*) FILTER (WHERE "Pintura" = 1) AS pintura,
--          count(*) FILTER (WHERE "Almox"   = 1) AS almox,
--          count(*) FILTER (WHERE "Exped"   = 1) AS exped,
--          count(*) AS total
--   FROM public.vw_os_integracao WHERE "N_PED" = 84172;
