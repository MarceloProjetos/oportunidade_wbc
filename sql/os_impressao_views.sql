-- ============================================================================
-- Views de IMPRESSÃO de OS — DDL para o Supabase (PostgreSQL)
-- Espelho DIRETO 1:1 de três views do SAP HANA (schema SBOALTAMIRAPROD):
--
--   SBOALTAMIRAPROD.VW_OS_EXPED_IMPRESSAO_V2  → public.vw_os_exped_impressao_v2  (57 col)
--   SBOALTAMIRAPROD.VW_OS_PINTURA_V0          → public.vw_os_pintura_v0          (55 col)
--   SBOALTAMIRAPROD.VW_OS_ALMOX_IMPRESSAO     → public.vw_os_almox_impressao     (34 col)
--
-- Pipeline: extract_os_impressao_views.py (carga sob demanda, por NPED, disparada
--   após a OS). Estratégia replace_nped (carrega-depois-poda escopado ao NPED).
--
-- Como usar: cole e execute no SQL Editor do Supabase (na ordem em que está). A
--   leitura read-only p/ a chave `anon` já está incluída no final (policies de SELECT).
-- ----------------------------------------------------------------------------
-- OBS. IMPORTANTES sobre nomes de coluna (case/aspas):
--   * Os nomes entre aspas preservam o case EXATO devolvido pela view HANA — o
--     pipeline insere via PostgREST usando o nome idêntico ao da view.
--   * Estas views têm colunas com ESPAÇO ("Tipo Logradouro", "Rua Filial",
--     "CEP Filial", "CNPJ Filial", etc.) — obrigatoriamente entre aspas.
--   * A ALMOX usa "CodCli" (as outras usam "CodClien"); a ALMOX não tem TIPO/
--     estrutura/U_INO_NIVEL. Mapeado 1:1 conforme o catálogo do HANA.
--   * DECIMAL do HANA → numeric (sem precisão fixa, evita violar constraint);
--     INTEGER/SMALLINT → integer; NVARCHAR/VARCHAR → text; TIMESTAMP → timestamp.
--
-- SEGURANÇA / RLS: RLS ENABLE + FORCE em todas as tabelas. Escrita só pelo
--   service_role (o pipeline). Leitura das 3 tabelas de dados liberada p/ `anon`
--   (policy de SELECT); o log permanece trancado (uso interno).
-- Idempotente: CREATE ... IF NOT EXISTS + DROP POLICY IF EXISTS.
-- ============================================================================


-- ----------------------------------------------------------------------------
-- 1) vw_os_exped_impressao_v2  ← SBOALTAMIRAPROD.VW_OS_EXPED_IMPRESSAO_V2
-- ----------------------------------------------------------------------------
create table if not exists public.vw_os_exped_impressao_v2 (
  id                     bigint generated always as identity primary key,

  "TIPO"                 text,
  "NPED"                 integer,
  "DtVenc"               timestamp,
  "NomeClien"            text,
  "NomedVend"            text,
  "DtPedido"             timestamp,
  "DiasTotal"            integer,
  "DtInic"               timestamp,
  "Obs"                  text,
  "DtEncerr"             timestamp,
  "DtLiber"              timestamp,
  "CodClien"             text,
  "Status"               text,
  "Deposito"             text,
  "UM"                   text,
  "CodItemEstrut"        text,
  "DescItemEstrut"       text,
  "GrpMaterialEstrut"    integer,
  "GrpItensEstrut"       integer,
  "QtdBasEstrut"         numeric,
  "QtdPlanEstrut"        numeric,
  "QtdSaida"             integer,
  "TipoEmissOP"          integer,
  "DeposEstrut"          text,
  "TipoItemEstrut"       integer,
  "QtdLiberEstrut"       integer,
  "PesoEstrut"           numeric,
  "U_INO_COD"            text,
  "CodDetalhOrcamento"   integer,
  "DtEntregaPED"         timestamp,
  "ObsPedido"            text,
  "CodigoOrcam"          text,
  "PesoOrcam"            numeric,
  "CodItemOrcam"         text,
  "QtdOrcam"             numeric,
  "DescProdOrcam"        text,
  "CorOrcam"             text,
  "PrecoOrcam"           numeric,
  "TotalOrcam"           numeric,
  "Filial"               text,
  "Tipo Logradouro"      text,
  "Rua Filial"           text,
  "NFilial"              text,
  "Complemento Filial"   text,
  "CEP Filial"           text,
  "Bairro Filial"        text,
  "Cidade Filial"        text,
  "Estado Filial"        text,
  "CNPJ Filial"          text,
  "IE Filial"            text,
  "Matriz"               text,
  "Usuario"              text,
  "U_INO_NIVEL"          text,
  "U_INO_VERSAOWBC"      text,
  "U_INO_PROJETO"        text,
  "DocEntry_OP"          bigint,   -- adicionada à view depois (chave interna da OP)
  "DocEntry_PED"         bigint,   -- adicionada à view depois (chave interna do pedido)

  -- ===== Controle / auditoria (adicionados pelo pipeline) =====
  id_execucao            uuid,
  data_hora_extracao     timestamp,
  origem_view            text default 'VW_OS_EXPED_IMPRESSAO_V2',
  inserted_at            timestamptz default now()
);

comment on table public.vw_os_exped_impressao_v2 is
  'Espelho por NPED da view SAP HANA VW_OS_EXPED_IMPRESSAO_V2 (ramo EXP). Carga sob demanda (replace_nped) após a OS. Escrita só service_role; leitura read-only p/ anon.';

create index if not exists idx_vw_exped_nped        on public.vw_os_exped_impressao_v2 ("NPED");
create index if not exists idx_vw_exped_orcam        on public.vw_os_exped_impressao_v2 ("CodigoOrcam");
create index if not exists idx_vw_exped_id_execucao  on public.vw_os_exped_impressao_v2 (id_execucao);

alter table public.vw_os_exped_impressao_v2 enable row level security;
alter table public.vw_os_exped_impressao_v2 force  row level security;


-- ----------------------------------------------------------------------------
-- 2) vw_os_pintura_v0  ← SBOALTAMIRAPROD.VW_OS_PINTURA_V0
--    Mesma estrutura de colunas da EXPED (ramo PINTURA).
-- ----------------------------------------------------------------------------
create table if not exists public.vw_os_pintura_v0 (
  id                     bigint generated always as identity primary key,

  "TIPO"                 text,
  "NPED"                 integer,
  "DtVenc"               timestamp,
  "NomeClien"            text,
  "NomedVend"            text,
  "DtPedido"             timestamp,
  "DiasTotal"            integer,
  "DtInic"               timestamp,
  "Obs"                  text,
  "DtEncerr"             timestamp,
  "DtLiber"              timestamp,
  "CodClien"             text,
  "Status"               text,
  "Deposito"             text,
  "UM"                   text,
  "CodItemEstrut"        text,
  "DescItemEstrut"       text,
  "GrpMaterialEstrut"    integer,
  "GrpItensEstrut"       integer,
  "QtdBasEstrut"         numeric,
  "QtdPlanEstrut"        numeric,
  "QtdSaida"             integer,
  "TipoEmissOP"          integer,
  "DeposEstrut"          text,
  "TipoItemEstrut"       integer,
  "QtdLiberEstrut"       integer,
  "PesoEstrut"           numeric,
  "U_INO_COD"            text,
  "CodDetalhOrcamento"   integer,
  "DtEntregaPED"         timestamp,
  "ObsPedido"            text,
  "CodigoOrcam"          text,
  "PesoOrcam"            numeric,
  "CodItemOrcam"         text,
  "QtdOrcam"             numeric,
  "DescProdOrcam"        text,
  "CorOrcam"             text,
  "PrecoOrcam"           numeric,
  "TotalOrcam"           numeric,
  "Filial"               text,
  "Tipo Logradouro"      text,
  "Rua Filial"           text,
  "NFilial"              text,
  "Complemento Filial"   text,
  "CEP Filial"           text,
  "Bairro Filial"        text,
  "Cidade Filial"        text,
  "Estado Filial"        text,
  "CNPJ Filial"          text,
  "IE Filial"            text,
  "Matriz"               text,
  "Usuario"              text,
  "U_INO_NIVEL"          text,
  "U_INO_VERSAOWBC"      text,
  "U_INO_PROJETO"        text,

  -- ===== Controle / auditoria (adicionados pelo pipeline) =====
  id_execucao            uuid,
  data_hora_extracao     timestamp,
  origem_view            text default 'VW_OS_PINTURA_V0',
  inserted_at            timestamptz default now()
);

comment on table public.vw_os_pintura_v0 is
  'Espelho por NPED da view SAP HANA VW_OS_PINTURA_V0 (ramo PINTURA). Carga sob demanda (replace_nped) após a OS. Escrita só service_role; leitura read-only p/ anon.';

create index if not exists idx_vw_pintura_nped        on public.vw_os_pintura_v0 ("NPED");
create index if not exists idx_vw_pintura_orcam        on public.vw_os_pintura_v0 ("CodigoOrcam");
create index if not exists idx_vw_pintura_id_execucao  on public.vw_os_pintura_v0 (id_execucao);

alter table public.vw_os_pintura_v0 enable row level security;
alter table public.vw_os_pintura_v0 force  row level security;


-- ----------------------------------------------------------------------------
-- 3) vw_os_almox_impressao  ← SBOALTAMIRAPROD.VW_OS_ALMOX_IMPRESSAO
--    Mais enxuta (34 col): sem TIPO/estrutura/U_INO_NIVEL; usa "CodCli".
-- ----------------------------------------------------------------------------
create table if not exists public.vw_os_almox_impressao (
  id                     bigint generated always as identity primary key,

  "NPED"                 integer,
  "DtVenc"               timestamp,
  "CodCli"               text,
  "NomeClien"            text,
  "NomedVend"            text,
  "DtPedido"             timestamp,
  "DiasTotal"            integer,
  "U_INO_COD"            text,
  "CodDetalhOrcamento"   integer,
  "DtEntregaPED"         timestamp,
  "ObsPedido"            text,
  "CodigoOrcam"          text,
  "PesoOrcam"            numeric,
  "CodItemOrcam"         text,
  "QtdOrcam"             numeric,
  "DescProdOrcam"        text,
  "CorOrcam"             text,
  "PrecoOrcam"           numeric,
  "TotalOrcam"           numeric,
  "Filial"               text,
  "Tipo Logradouro"      text,
  "Rua Filial"           text,
  "NFilial"              text,
  "Complemento Filial"   text,
  "CEP Filial"           text,
  "Bairro Filial"        text,
  "Cidade Filial"        text,
  "Estado Filial"        text,
  "CNPJ Filial"          text,
  "IE Filial"            text,
  "Matriz"               text,
  "Usuario"              text,
  "U_INO_VERSAOWBC"      text,
  "U_INO_PROJETO"        text,

  -- ===== Controle / auditoria (adicionados pelo pipeline) =====
  id_execucao            uuid,
  data_hora_extracao     timestamp,
  origem_view            text default 'VW_OS_ALMOX_IMPRESSAO',
  inserted_at            timestamptz default now()
);

comment on table public.vw_os_almox_impressao is
  'Espelho por NPED da view SAP HANA VW_OS_ALMOX_IMPRESSAO (ramo ALMOX). Carga sob demanda (replace_nped) após a OS. Escrita só service_role; leitura read-only p/ anon.';

create index if not exists idx_vw_almox_nped        on public.vw_os_almox_impressao ("NPED");
create index if not exists idx_vw_almox_orcam        on public.vw_os_almox_impressao ("CodigoOrcam");
create index if not exists idx_vw_almox_id_execucao  on public.vw_os_almox_impressao (id_execucao);

alter table public.vw_os_almox_impressao enable row level security;
alter table public.vw_os_almox_impressao force  row level security;


-- ----------------------------------------------------------------------------
-- 4) Log compartilhado das sincronizações (1 linha por view a cada carga)
--    Mantém os N mais recentes via pipeline (coluna origem_view distingue a view).
-- ----------------------------------------------------------------------------
create table if not exists public.sincronizacao_log_os_impressao (
  id                       bigint generated always as identity primary key,
  data_hora_sincronizacao  timestamptz,
  nped                     integer,        -- pedido que disparou a sync
  origem_view              text,           -- tabela/view sincronizada (ex.: vw_os_pintura_v0)
  duracao_segundos         numeric(10,2),
  status                   text,           -- 'sucesso' | 'falha'
  qtd_registros            integer
);

comment on table public.sincronizacao_log_os_impressao is
  'Log das sincronizações das views de impressão de OS por NPED (1 linha por view). Uso exclusivo do backend (service_role).';

alter table public.sincronizacao_log_os_impressao enable row level security;
alter table public.sincronizacao_log_os_impressao force  row level security;
-- (sem policy: só o service_role acessa o log)


-- ----------------------------------------------------------------------------
-- 5) Leitura READ-ONLY p/ a chave `anon` nas 3 tabelas de dados.
--    Sem policy de INSERT/UPDATE/DELETE → anon é read-only por construção.
--    NÃO libera o log (uso interno). Idempotente (DROP POLICY IF EXISTS).
-- ----------------------------------------------------------------------------
drop policy if exists "vw_os_exped_read_anon" on public.vw_os_exped_impressao_v2;
create policy "vw_os_exped_read_anon"
  on public.vw_os_exped_impressao_v2 for select to anon using (true);

drop policy if exists "vw_os_pintura_read_anon" on public.vw_os_pintura_v0;
create policy "vw_os_pintura_read_anon"
  on public.vw_os_pintura_v0 for select to anon using (true);

drop policy if exists "vw_os_almox_read_anon" on public.vw_os_almox_impressao;
create policy "vw_os_almox_read_anon"
  on public.vw_os_almox_impressao for select to anon using (true);


-- ============================================================================
-- VERIFICAÇÃO (opcional — rodar após o CREATE)
-- ============================================================================
-- RLS ligado e forçado nas 4 tabelas?
--   SELECT c.relname, c.relrowsecurity AS rls, c.relforcerowsecurity AS force_rls
--   FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
--   WHERE n.nspname='public' AND c.relname IN
--     ('vw_os_exped_impressao_v2','vw_os_pintura_v0','vw_os_almox_impressao',
--      'sincronizacao_log_os_impressao');
--
-- 1 policy de SELECT p/ {anon} em cada tabela de dados (log sem policy)?
--   SELECT tablename, policyname, cmd, roles FROM pg_policies
--   WHERE tablename LIKE 'vw_os_%';
--
-- Após uma sync (ex.: NPED 84080):
--   SELECT count(*) FROM public.vw_os_exped_impressao_v2 WHERE "NPED" = 84080;
--   SELECT count(*) FROM public.vw_os_pintura_v0         WHERE "NPED" = 84080;
--   SELECT count(*) FROM public.vw_os_almox_impressao    WHERE "NPED" = 84080;
