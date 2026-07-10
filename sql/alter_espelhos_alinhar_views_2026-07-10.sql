-- ============================================================================
-- Alinhamento das tabelas-espelho de OS as views HANA (deriva de schema)
-- Gerado 2026-07-10 a partir dos nomes REAIS de coluna das views (byte-exatos).
--
-- Motivo: durante o desenvolvimento as views HANA ganharam colunas que as tabelas
-- Supabase (criadas antes) nao tinham -> o INSERT do pipeline falhava com PGRST204
-- ("Could not find the '<col>' column ... in the schema cache").
--   * VW_OS_EXPED_IMPRESSAO_V2 ganhou DocEntry_OP / DocEntry_PED
--   * VW_OS_SOLDA_DETALHE       ganhou o bloco de endereco da FILIAL (12 colunas;
--     a coluna do numero e "No Filial" com espaco e 'o' ordinal, IGUAL a view)
--
-- Tipos pela convencao dos DDLs existentes (sql/os_impressao_views.sql):
--   bloco de filial/endereco -> text ; DocEntry (chave) -> bigint.
--
-- Como usar: cole e execute no SQL Editor do Supabase. Idempotente (IF NOT EXISTS).
-- O NOTIFY no fim recarrega o cache de schema do PostgREST (senao PGRST204 persiste).
-- ============================================================================


-- vw_os_exped_impressao_v2: +2 coluna(s) (view VW_OS_EXPED_IMPRESSAO_V2)
alter table public.vw_os_exped_impressao_v2 add column if not exists "DocEntry_OP" bigint;
alter table public.vw_os_exped_impressao_v2 add column if not exists "DocEntry_PED" bigint;

-- vw_os_solda: +12 coluna(s) (view VW_OS_SOLDA_DETALHE)
alter table public.vw_os_solda add column if not exists "Filial" text;
alter table public.vw_os_solda add column if not exists "Tipo Logradouro" text;
alter table public.vw_os_solda add column if not exists "Rua Filial" text;
alter table public.vw_os_solda add column if not exists "Nº Filial" text;
alter table public.vw_os_solda add column if not exists "Complemento Filial" text;
alter table public.vw_os_solda add column if not exists "CEP Filial" text;
alter table public.vw_os_solda add column if not exists "Bairro Filial" text;
alter table public.vw_os_solda add column if not exists "Cidade Filial" text;
alter table public.vw_os_solda add column if not exists "Estado Filial" text;
alter table public.vw_os_solda add column if not exists "CNPJ Filial" text;
alter table public.vw_os_solda add column if not exists "IE Filial" text;
alter table public.vw_os_solda add column if not exists "Matriz" text;

notify pgrst, 'reload schema';
