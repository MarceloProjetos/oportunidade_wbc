-- ============================================================================
-- VW_OS_INTEGRACAO — tabela ÚNICA consolidada de OS no Supabase (PostgreSQL)
-- Origem: SAP HANA view SBOALTAMIRAPROD.VW_OS_INTEGRACAO (54 colunas)
-- Pipeline: extract_ordens_servico_engenharia.py (carga sob demanda, por N_PED)
--
-- Consolidação 2026-07-14: esta tabela SUBSTITUI os 6 espelhos separados que
-- existiam antes (ordens_servico_engenharia, status_ordens_servico_eng,
-- vw_os_exped_impressao_v2, vw_os_pintura_v0, vw_os_almox_impressao, vw_os_solda,
-- wbc_arvore_produto) e seus 3 logs. A view HANA VW_OS_INTEGRACAO já traz OS +
-- estrutura/árvore de produto + orçamento numa só consulta.
--
-- Como usar: cole e execute no SQL Editor do Supabase (na ordem em que está).
-- ----------------------------------------------------------------------------
-- SEGURANÇA / RLS (mesmo padrão dos espelhos antigos, replicado aqui):
--   * RLS ENABLE + FORCE.
--   * 1 policy de SELECT para a role `anon` (leitura read-only por construção —
--     não há policy de INSERT/UPDATE/DELETE, então anon não escreve).
--   * Escrita/leitura do pipeline e da API sai pelo `service_role` (BYPASSRLS) —
--     sem policy necessária.
--   * O log (sincronizacao_log_os_integracao) NÃO recebe policy: uso interno,
--     trancado ao service_role.
--
-- Identificadores entre aspas preservam o case byte-exato exigido pelo PostgREST
-- na inserção (o pipeline insere usando o nome de coluna idêntico ao da view SAP).
-- A view usa "N_PED" (com underscore) como chave do pedido.
-- Idempotente: CREATE ... IF NOT EXISTS + DROP POLICY IF EXISTS.
-- ============================================================================


-- ----------------------------------------------------------------------------
-- 0) Derrubar a view dependente e as tabelas antigas (cascade remove policies).
--    Rode UMA VEZ na migração; depois é inócuo (IF EXISTS).
-- ----------------------------------------------------------------------------
drop view  if exists public.vw_os_exped_arvore;
drop table if exists public.ordens_servico_engenharia      cascade;
drop table if exists public.status_ordens_servico_eng      cascade;
drop table if exists public.vw_os_exped_impressao_v2        cascade;
drop table if exists public.vw_os_pintura_v0                cascade;
drop table if exists public.vw_os_almox_impressao           cascade;
drop table if exists public.vw_os_solda                     cascade;
drop table if exists public.wbc_arvore_produto              cascade;
drop table if exists public.sincronizacao_log_os_eng        cascade;
drop table if exists public.sincronizacao_log_os_impressao  cascade;
drop table if exists public.sincronizacao_log_wbc_arvore    cascade;


-- ----------------------------------------------------------------------------
-- 1) Tabela única (54 colunas da view + metadados de auditoria do pipeline)
-- ----------------------------------------------------------------------------
create table if not exists public.vw_os_integracao (
  id                   bigint generated always as identity primary key,

  -- ===== Colunas da view VW_OS_INTEGRACAO (54) =====
  "N_OP"               integer,
  "N_PED"              integer,
  "Quantity"           numeric(21,6),
  "CodItemPED"         text,
  "DescItemPED"        text,
  "DtPedido"           timestamp,
  "DiasTotal"          integer,
  "DtVenc"             timestamp,
  "DtInic"             timestamp,
  "LinhRef"            text,
  "Obs"                text,
  "DtEncerr"           timestamp,
  "DtLiber"            timestamp,
  "CodClien"           text,
  "NomeClien"          text,
  "NomedVend"          text,
  "Status"             text,
  "Deposito"           text,
  "UM"                 text,
  "LinhEstrut"         integer,
  "CodItemEstrut"      text,
  "DescItemEstrut"     text,
  "QtdBasEstrut"       numeric(21,6),
  "QtdPlanEstrut"      numeric,
  "QtdSaida"           numeric(21,6),
  "TipoEmissOP"        text,
  "DeposEstrut"        text,
  "LinhVisEstrut"      integer,
  "TipoItemEstrut"     integer,
  "QtdLiberEstrut"     numeric(21,6),
  "GrupoItem"          smallint,
  "CodDetalhOrcamento" integer,
  "ObsPedido"          text,
  "N_Orcamento"        text,
  "DataEntrega"        timestamp,
  "NivelItemOrcam"     text,
  "PesoOrcam"          numeric(21,6),
  "CodItemOrcam"       text,
  "QtdOrcam"           numeric(21,6),
  "DescProdOrcam"      text,
  "CorOrcam"           text,
  "PrecoOrcam"         numeric(21,6),
  "TotalOrcam"         numeric(21,6),
  "VisOrder"           integer,
  "Usuario"            text,
  "DtEntregaPED"       timestamp,
  "CodigoOrcam"        text,
  "U_INO_VERSAOWBC"    text,
  "U_INO_LINHA"        integer,
  "U_INO_ORCITM"       text,
  -- Flags de PROCESSO por item (1 = passa pelo processo, 0 = não). Substituem, por
  -- 4 colunas, as 4 tabelas dropadas na consolidação (solda/pintura/almox/exped).
  "Solda"              integer,
  "Pintura"            integer,
  "Almox"              integer,
  "Exped"              integer,

  -- ===== Controle / auditoria (adicionados pelo pipeline; NÃO estão na view) =====
  id_execucao          uuid,        -- UUID da carga (agrupa as linhas do sync de um N_PED)
  data_hora_extracao   timestamp,   -- horário do servidor ao extrair (última sync do N_PED)
  origem_view          text default 'VW_OS_INTEGRACAO',
  inserted_at          timestamptz default now()
);

comment on table public.vw_os_integracao is
  'Espelho por N_PED da view SAP HANA consolidada VW_OS_INTEGRACAO (OS + estrutura/árvore + orçamento). Carga sob demanda (replace_nped). Escrita só service_role; leitura read-only p/ anon.';

create index if not exists idx_os_int_nped        on public.vw_os_integracao ("N_PED");
create index if not exists idx_os_int_nop         on public.vw_os_integracao ("N_OP");
create index if not exists idx_os_int_orcam       on public.vw_os_integracao ("CodigoOrcam");
create index if not exists idx_os_int_id_execucao on public.vw_os_integracao (id_execucao);

alter table public.vw_os_integracao enable row level security;
alter table public.vw_os_integracao force  row level security;

-- 1 policy de SELECT para anon (read-only por construção; sem policy de escrita).
drop policy if exists "vw_os_integracao_read_anon" on public.vw_os_integracao;
create policy "vw_os_integracao_read_anon"
  on public.vw_os_integracao for select to anon using (true);


-- ----------------------------------------------------------------------------
-- 2) Log da sincronização (mantém os N mais recentes via pipeline). Uso interno.
-- ----------------------------------------------------------------------------
create table if not exists public.sincronizacao_log_os_integracao (
  id                       bigint generated always as identity primary key,
  data_hora_sincronizacao  timestamptz,
  nped                     integer,        -- pedido sincronizado nesta execução
  duracao_segundos         numeric(10,2),
  status                   text,           -- 'sucesso' | 'falha'
  qtd_registros            integer
);

comment on table public.sincronizacao_log_os_integracao is
  'Log das sincronizações da tabela única de OS por N_PED. Uso exclusivo do backend (service_role).';

alter table public.sincronizacao_log_os_integracao enable row level security;
alter table public.sincronizacao_log_os_integracao force  row level security;
-- (sem policy: só o service_role acessa o log)


-- ----------------------------------------------------------------------------
-- 3) OBRIGATÓRIO: recarregar o cache do schema do PostgREST — senão o 1º INSERT
--    do pipeline pode falhar com PGRST204 até o cache recarregar sozinho.
-- ----------------------------------------------------------------------------
notify pgrst, 'reload schema';


-- ============================================================================
-- VERIFICAÇÃO (opcional — rodar após o CREATE)
-- ============================================================================
-- RLS ligado e forçado?
--   SELECT c.relname, c.relrowsecurity AS rls, c.relforcerowsecurity AS force_rls
--   FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
--   WHERE n.nspname='public'
--     AND c.relname IN ('vw_os_integracao','sincronizacao_log_os_integracao');
--
-- 1 policy de SELECT p/ {anon} na tabela de dados (log sem policy)?
--   SELECT tablename, policyname, cmd, roles FROM pg_policies
--   WHERE tablename IN ('vw_os_integracao','sincronizacao_log_os_integracao');
--
-- Após uma sync (ex.: N_PED 84080):
--   SELECT count(*) FROM public.vw_os_integracao WHERE "N_PED" = 84080;
