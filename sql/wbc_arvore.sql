-- ============================================================================
-- WBC — Árvore de Produto — DDL para o Supabase (PostgreSQL)
-- Origem: SQL Server  WBCCAD.dbo.INTEGRACAO_ORCPRDARV  (filtrada por ORCNUM)
-- Pipeline: extract_wbc_arvore.py  (sub-sync disparada após a OS por NPED)
--
-- Como usar: cole e execute no SQL Editor do Supabase (nesta ordem). Depois, para
-- liberar a leitura ao consumidor, rode também `wbc_arvore_read_policy.sql`.
-- ----------------------------------------------------------------------------
-- SEGURANÇA / RLS
--   * RLS ENABLE + FORCE em todas as tabelas. Sem policy aqui → só o `service_role`
--     (BYPASSRLS, usado pelo pipeline) escreve/lê. A leitura read-only para o outro
--     programa é liberada à parte (policy de SELECT p/ `anon`, no arquivo read_policy).
--   * Nomes de coluna entre aspas preservam o case EXATO devolvido pelo pyodbc
--     (o pipeline insere com o nome idêntico ao da tabela SQL Server).
--   * Idempotente: CREATE ... IF NOT EXISTS.
-- ============================================================================


-- ----------------------------------------------------------------------------
-- 1) Tabela principal: wbc_arvore_produto (espelho da INTEGRACAO_ORCPRDARV)
-- ----------------------------------------------------------------------------
create table if not exists public.wbc_arvore_produto (
  id                       bigint generated always as identity primary key,

  -- ===== Colunas da INTEGRACAO_ORCPRDARV (case idêntico ao SQL Server) =====
  "ORCNUM"                 text,         -- nvarchar(8) — código WBC (= NºOrçament do pedido)
  "GRPCOD"                 integer,
  "SUBGRPCOD"              integer,
  "ORCITM"                 integer,
  "PRDCOD"                 text,         -- nvarchar(80)
  "ORCPRDARV_NIVEL"        integer,
  "CORCOD"                 text,         -- nvarchar(20)
  "PRDDSC"                 text,         -- nvarchar(70)
  "ORCQTD"                 numeric,
  "ORCTOT"                 numeric,
  "ORCPES"                 numeric,
  "idIntegracao_OrcPrdArv" integer,      -- PK identity na origem (apenas referência)
  "orcprdarv_dth"          timestamp,

  -- ===== Controle / auditoria (adicionados pelo pipeline) =====
  id_execucao              uuid,        -- UUID da carga (agrupa as linhas de um ORCNUM)
  data_hora_extracao       timestamp,   -- horário do servidor ao extrair
  origem_view              text default 'WBCCAD.dbo.INTEGRACAO_ORCPRDARV',
  inserted_at              timestamptz default now()
);

comment on table public.wbc_arvore_produto is
  'Espelho por ORCNUM da WBCCAD.dbo.INTEGRACAO_ORCPRDARV (árvore de produto WBC). Carga sob demanda (replace por ORCNUM), disparada após a OS do pedido. Escrita só pelo service_role; leitura read-only via policy p/ anon.';
comment on column public.wbc_arvore_produto."ORCNUM" is
  'Código WBC do orçamento (= NºOrçament do pedido no SAP). Chave de substituição da carga.';
comment on column public.wbc_arvore_produto.id_execucao is
  'UUID da carga; usado pela poda escopada ao ORCNUM (replace).';

-- Índices: ORCNUM é a chave de substituição e a coluna que o consumidor filtra.
create index if not exists idx_wbc_arv_orcnum      on public.wbc_arvore_produto ("ORCNUM");
create index if not exists idx_wbc_arv_prdcod      on public.wbc_arvore_produto ("PRDCOD");
create index if not exists idx_wbc_arv_id_execucao on public.wbc_arvore_produto (id_execucao);

alter table public.wbc_arvore_produto enable row level security;
alter table public.wbc_arvore_produto force  row level security;
-- (sem policies aqui: leitura do consumidor é liberada em wbc_arvore_read_policy.sql)


-- ----------------------------------------------------------------------------
-- 2) Log da sincronização (mantém os N mais recentes via pipeline)
-- ----------------------------------------------------------------------------
create table if not exists public.sincronizacao_log_wbc_arvore (
  id                       bigint generated always as identity primary key,
  data_hora_sincronizacao  timestamptz,
  nped                     integer,        -- pedido que disparou a sync
  orcnum                   text,           -- ORCNUM sincronizado
  duracao_segundos         numeric(10,2),
  status                   text,           -- 'sucesso' | 'falha'
  qtd_registros            integer
);

comment on table public.sincronizacao_log_wbc_arvore is
  'Log das sincronizações da árvore WBC por ORCNUM. Uso exclusivo do backend (service_role).';

alter table public.sincronizacao_log_wbc_arvore enable row level security;
alter table public.sincronizacao_log_wbc_arvore force  row level security;
-- (sem policies: somente service_role acessa)


-- ============================================================================
-- VERIFICAÇÃO (opcional — rodar após o CREATE)
-- ============================================================================
-- RLS ligado e forçado nas 2 tabelas?
SELECT c.relname, c.relrowsecurity AS rls, c.relforcerowsecurity AS force_rls
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname='public'
AND c.relname IN ('wbc_arvore_produto','sincronizacao_log_wbc_arvore');
--
-- Nenhuma policy ainda (esperado: 0 linhas, até rodar o read_policy)?
SELECT tablename, policyname, cmd, roles FROM pg_policies
WHERE tablename IN ('wbc_arvore_produto','sincronizacao_log_wbc_arvore');
