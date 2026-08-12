-- ============================================================================
-- BI_VENDAS — agregados do dashboard "Vendas" do app (o que hoje é Power BI)
-- Origem: SAP HANA — SBOALTAMIRAPROD.VW_PEDIDO_ALTA e .VW_FATO_FATURAMENTO
-- Pipeline: extract_vendas_bi.py (agendado)
-- Consumidor: mobile_orcaview_V3, aba Dashboard, modo "Vendas"
--             (plano em mobile_orcaview_V3/docs/PLANO_VENDAS_BI.md)
--
-- Como usar: cole e execute no SQL Editor do Supabase, na ordem em que está.
-- Idempotente: CREATE ... IF NOT EXISTS + DROP POLICY IF EXISTS.
-- ----------------------------------------------------------------------------
-- POR QUE ESTAS TABELAS SÃO PEQUENAS (e isso é o desenho, não um acaso)
--
-- A tela precisa de ~90 linhas por escopo de visibilidade: 4 KPIs, 36 pontos de
-- pedidos, 24 de faturamento e dois rankings. Nenhuma LINHA DE PEDIDO sai do
-- servidor — o que resolve metade da conversa de segurança antes de começar, e
-- deixa a carga inteira caber no cache offline do app.
-- ----------------------------------------------------------------------------
-- SEGURANÇA / RLS — diferente dos espelhos antigos, e de propósito:
--   * RLS ENABLE + FORCE, como no resto.
--   * **NENHUMA policy para `anon`.** Faturamento por cliente e ranking de
--     vendedor são dado sensível; a chave anon já expõe demais em outras
--     tabelas e estas não entram nessa lista.
--   * `authenticated` lê conforme o papel em app_profiles:
--       - admin / diretoria  → tudo, inclusive o consolidado '__TOTAL__'
--       - representante / vendas → SÓ as linhas do próprio `slp_name`
--   * Escrita é só do `service_role` (BYPASSRLS) — sem policy de INSERT/UPDATE.
--
-- ⚠️ O join com o vendedor é pelo **NOME** (`app_profiles.slp_name` =
--    `OSLP.SlpName`), NUNCA pelo código: `app_profiles.slp_code` diverge do
--    `OSLP.SlpCode` em produção (Edson 6≠7, Luiz Augusto 12≠9, Robson 47≠11).
--    Casar por código entregaria o número de um vendedor para outro.
-- ============================================================================


-- ----------------------------------------------------------------------------
-- 1) Tabelas
-- ----------------------------------------------------------------------------

-- Os quatro cartões do topo. `vendedor` = OSLP.SlpName ou '__TOTAL__'.
create table if not exists public.bi_vendas_kpi (
  escopo        text        not null check (escopo in ('hoje','ontem','mes_atual','mes_passado')),
  vendedor      text        not null,
  competencia   date        not null,
  valor         numeric(18,2) not null default 0,
  qtd_pedidos   integer     not null default 0,
  atualizado_em timestamptz not null default now(),
  primary key (escopo, vendedor)
);

-- Séries mensais dos dois gráficos. 12 meses por ano; mês sem linha na origem
-- simplesmente não existe aqui — quem transforma ausência em `null` (e não em
-- zero) é o app, e é isso que impede a linha de 2026 de despencar em setembro.
create table if not exists public.bi_vendas_serie_mensal (
  metrica       text        not null check (metrica in ('pedidos','faturamento')),
  vendedor      text        not null,
  ano           smallint    not null,
  mes           smallint    not null check (mes between 1 and 12),
  valor         numeric(18,2) not null default 0,
  qtd_pedidos   integer     not null default 0,
  atualizado_em timestamptz not null default now(),
  primary key (metrica, vendedor, ano, mes)
);

-- Ranking, um par (vendedores + clientes) POR ESCOPO — os quatro cartões do
-- topo da tela filtram os três blocos, e ranking de um dia não se deriva do
-- ranking do mês. `vendedor` aqui é o ESCOPO DE VISIBILIDADE, não o dono da
-- linha: só existe '__TOTAL__' (representante não vê a carteira dos outros).
create table if not exists public.bi_vendas_ranking (
  escopo        text        not null check (escopo in ('hoje','ontem','mes_atual','mes_passado')),
  competencia   date        not null,
  tipo          text        not null check (tipo in ('vendedor','cliente')),
  vendedor      text        not null,
  chave         text        not null,
  nome          text        not null,
  valor         numeric(18,2) not null default 0,
  posicao       smallint    not null,
  atualizado_em timestamptz not null default now(),
  primary key (escopo, tipo, vendedor, chave)
);

-- O app lê sempre por vendedor (o seu ou '__TOTAL__'); o índice acompanha.
create index if not exists ix_bi_vendas_serie_vendedor
  on public.bi_vendas_serie_mensal (vendedor, metrica, ano, mes);
create index if not exists ix_bi_vendas_ranking_escopo
  on public.bi_vendas_ranking (vendedor, escopo, tipo, posicao);


-- ----------------------------------------------------------------------------
-- 2) RLS
-- ----------------------------------------------------------------------------
alter table public.bi_vendas_kpi          enable row level security;
alter table public.bi_vendas_kpi          force  row level security;
alter table public.bi_vendas_serie_mensal enable row level security;
alter table public.bi_vendas_serie_mensal force  row level security;
alter table public.bi_vendas_ranking      enable row level security;
alter table public.bi_vendas_ranking      force  row level security;

-- Função de apoio: o `slp_name` de quem está pedindo, ou NULL.
-- SECURITY DEFINER para não depender da policy de app_profiles (e não cair na
-- recursão que o docs/SUPABASE_RLS.md descreve). STABLE + search_path fixo.
create or replace function public.bi_vendas_slp_name_do_usuario()
returns text
language sql
stable
security definer
set search_path = public
as $$
  select p.slp_name from public.app_profiles p where p.id = (select auth.uid())
$$;

create or replace function public.bi_vendas_ve_tudo()
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1 from public.app_profiles p
    where p.id = (select auth.uid()) and p.role in ('admin','diretoria')
  )
$$;

revoke all on function public.bi_vendas_slp_name_do_usuario() from public, anon;
revoke all on function public.bi_vendas_ve_tudo()            from public, anon;
grant execute on function public.bi_vendas_slp_name_do_usuario() to authenticated;
grant execute on function public.bi_vendas_ve_tudo()            to authenticated;

drop policy if exists bi_vendas_kpi_sel on public.bi_vendas_kpi;
create policy bi_vendas_kpi_sel on public.bi_vendas_kpi
  for select to authenticated
  using (
    public.bi_vendas_ve_tudo()
    or vendedor = public.bi_vendas_slp_name_do_usuario()
  );

drop policy if exists bi_vendas_serie_sel on public.bi_vendas_serie_mensal;
create policy bi_vendas_serie_sel on public.bi_vendas_serie_mensal
  for select to authenticated
  using (
    public.bi_vendas_ve_tudo()
    or vendedor = public.bi_vendas_slp_name_do_usuario()
  );

drop policy if exists bi_vendas_ranking_sel on public.bi_vendas_ranking;
create policy bi_vendas_ranking_sel on public.bi_vendas_ranking
  for select to authenticated
  using (
    public.bi_vendas_ve_tudo()
    or vendedor = public.bi_vendas_slp_name_do_usuario()
  );


-- ----------------------------------------------------------------------------
-- 3) Conferência rápida depois de rodar o pipeline
-- ----------------------------------------------------------------------------
-- select escopo, valor, qtd_pedidos from public.bi_vendas_kpi where vendedor = '__TOTAL__';
-- select ano, mes, valor from public.bi_vendas_serie_mensal
--   where metrica='pedidos' and vendedor='__TOTAL__' order by ano, mes;
