-- ============================================================================
-- Ordens de Serviço (Engenharia) — DDL para o Supabase (PostgreSQL)
-- Origem: SAP HANA view SBOALTAMIRAPROD.VW_EXPORT_ORDENS_SERVICO_1 (58 colunas)
-- Pipeline: extract_ordens_servico_engenharia.py  (carga sob demanda, por NPED)
--
-- Como usar: cole e execute no SQL Editor do Supabase (na ordem em que está).
-- ----------------------------------------------------------------------------
-- SEGURANÇA / RLS (decisão: leitura SOMENTE backend/scripts)
--   * RLS ENABLE + FORCE em todas as tabelas, SEM nenhuma policy.
--   * Só o `service_role` (BYPASSRLS) lê/escreve — é o que o pipeline usa.
--   * `anon` e `authenticated` NÃO têm acesso algum (nem leitura).
--   * NÃO há policy de escrita (INSERT/UPDATE/DELETE) → evita o warning do
--     Advisor "RLS Policy Always True" (que sinaliza policies de escrita com
--     USING/WITH CHECK = true). SELECT com USING(true) não é criado aqui.
--   * NÃO criamos extensões, funções SECURITY DEFINER nem triggers → evita os
--     lints "Extension in Public", "SECURITY DEFINER" e "function_search_path".
--   * O SQL Editor (role `postgres`, BYPASSRLS) consegue consultar normalmente.
--   * Obs.: o Advisor pode exibir um INFO "RLS enabled, no policy" — é
--     INTENCIONAL aqui (tabela de uso exclusivo do backend). Se um dia um app
--     precisar ler, adiciona-se uma policy de SELECT para a role apropriada.
--
-- Identificadores entre aspas preservam o case exato exigido pelo PostgREST na
-- inserção (o pipeline insere usando o nome de coluna idêntico ao da view SAP).
-- Idempotente: CREATE ... IF NOT EXISTS.
-- ============================================================================


-- ----------------------------------------------------------------------------
-- 1) Lookup do Status (apenas referência: código de 1 letra → descrição).
--    Códigos presentes na view hoje: P, R, L. (C semeado p/ uso futuro.)
--    SEED roda ANTES de ativar o RLS (não depende de bypass).
-- ----------------------------------------------------------------------------
create table if not exists public.status_ordens_servico_eng (
  codigo    text primary key,
  descricao text not null
);

insert into public.status_ordens_servico_eng (codigo, descricao) values
  ('P', 'Planejado'),
  ('R', 'Liberado'),    -- Released (em produção)
  ('L', 'Encerrado'),   -- Closed / Fechado
  ('C', 'Cancelado')    -- não aparece na view atualmente
on conflict (codigo) do nothing;

comment on table public.status_ordens_servico_eng is
  'Lookup de tradução do Status da OS (P/R/L/C). Uso exclusivo do backend (service_role).';

alter table public.status_ordens_servico_eng enable row level security;
alter table public.status_ordens_servico_eng force row level security;
-- (sem policies: somente service_role acessa)


-- ----------------------------------------------------------------------------
-- 2) Tabela principal: ordens_servico_engenharia
-- ----------------------------------------------------------------------------
create table if not exists public.ordens_servico_engenharia (
  id                   bigint generated always as identity primary key,

  -- ===== Campos da view VW_EXPORT_ORDENS_SERVICO_1 (58 colunas) =====
  "N_OP"               integer,
  "NPED"               integer,
  "CodItemPED"         text,
  "DescItemPED"        text,
  "QtdPlan"            numeric(21,6),
  "QtdConcl"           numeric(21,6),
  "QtdRejeit"          numeric(21,6),
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
  "PesoEstrut"         numeric(21,6),
  "TextoLivPED"        text,
  "InfoAdicPED"        text,
  "InfoAdicPED2"       text,
  "ComposicaoPED"      text,
  "MATExistPED"        text,
  "AcabamentoPED"      text,
  "CapacidadePED"      text,
  "CordPED"            text,
  "ObsImpostOrcamento" text,
  "CodDetalhOrcamento" integer,
  "ObsPedido"          text,
  "NºOrçament"         text,
  "DataEntrega"        timestamp,
  "PesoOrcam"          numeric(21,6),
  "CodItemOrcam"       text,
  "QtdOrcam"           numeric(21,6),
  "DescProdOrcam"      text,
  "CorOrcam"           text,
  "PrecoOrcam"         numeric(21,6),
  "TotalOrcam"         numeric(21,6),
  "Usuario"            text,
  "DtEntregaPED"       timestamp,
  "CodigoOrcam"        text,
  "U_INO_VERSAOWBC"    text,
  "U_INO_PROJETO"      text,

  -- ===== Campos de controle / auditoria (adicionados pelo pipeline) =====
  id_execucao          uuid,        -- UUID da carga (agrupa as linhas do sync de um NPED)
  data_hora_extracao   timestamp,   -- horário do servidor ao extrair (última sync do NPED)
  origem_view          text default 'VW_EXPORT_ORDENS_SERVICO_1',
  inserted_at          timestamptz default now()
);

comment on table public.ordens_servico_engenharia is
  'Espelho por NPED da view SAP VW_EXPORT_ORDENS_SERVICO_1. Carga sob demanda (replace_nped). Uso exclusivo do backend (service_role).';
comment on column public.ordens_servico_engenharia.id_execucao is
  'UUID da carga; usado pela poda escopada ao NPED (replace_nped).';
comment on column public.ordens_servico_engenharia.data_hora_extracao is
  'Quando este NPED foi sincronizado pela última vez (hora do servidor do ETL).';

-- Índices: NPED é a chave de substituição/consulta; id_execucao acelera a poda.
create index if not exists idx_os_eng_nped        on public.ordens_servico_engenharia ("NPED");
create index if not exists idx_os_eng_nop         on public.ordens_servico_engenharia ("N_OP");
create index if not exists idx_os_eng_codclien    on public.ordens_servico_engenharia ("CodClien");
create index if not exists idx_os_eng_status      on public.ordens_servico_engenharia ("Status");
create index if not exists idx_os_eng_id_execucao on public.ordens_servico_engenharia (id_execucao);

alter table public.ordens_servico_engenharia enable row level security;
alter table public.ordens_servico_engenharia force row level security;
-- (sem policies: somente service_role acessa)


-- ----------------------------------------------------------------------------
-- 3) Log da sincronização (mantém os N mais recentes via pipeline)
-- ----------------------------------------------------------------------------
create table if not exists public.sincronizacao_log_os_eng (
  id                       bigint generated always as identity primary key,
  data_hora_sincronizacao  timestamptz,
  nped                     integer,        -- pedido sincronizado nesta execução
  duracao_segundos         numeric(10,2),
  status                   text,           -- 'sucesso' | 'falha'
  qtd_registros            integer
);

comment on table public.sincronizacao_log_os_eng is
  'Log das sincronizações de OS por NPED. Uso exclusivo do backend (service_role).';

alter table public.sincronizacao_log_os_eng enable row level security;
alter table public.sincronizacao_log_os_eng force row level security;
-- (sem policies: somente service_role acessa)


-- ============================================================================
-- VERIFICAÇÃO (rodar após o CREATE — opcional)
-- ============================================================================
-- RLS ligado e forçado nas 3 tabelas?
SELECT c.relname, c.relrowsecurity AS rls, c.relforcerowsecurity AS force_rls
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname='public'
AND c.relname IN ('ordens_servico_engenharia','status_ordens_servico_eng','sincronizacao_log_os_eng');
--
-- Nenhuma policy (esperado: 0 linhas)?
SELECT tablename, policyname, cmd, roles FROM pg_policies
WHERE tablename IN ('ordens_servico_engenharia','status_ordens_servico_eng','sincronizacao_log_os_eng');
--
-- Seed do status (esperado: P, R, L, C)?
--   SELECT * FROM public.status_ordens_servico_eng ORDER BY codigo;
