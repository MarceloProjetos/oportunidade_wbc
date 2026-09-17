-- ============================================================================
-- ORCAMENTOS_ESPELHO — espelho da view SAP HANA SBOALTAMIRAPROD.VW_EVOL_ORCAMENTO_ALT
-- Pipeline: extract_orcamentos_espelho.py (snapshot a cada 60 min, no expediente)
--
-- Por que existe: a view foi refeita em 09/2026 e passou de 12 para 34 colunas —
-- ganhou o CNAE do cliente, o bloco de montagem (TipoMontagem/ValorMontagem/
-- Montador) e o bloco de nota fiscal (NumNF/DataNF/QuitacaoNF). Até então esses
-- campos só existiam no Power BI. O espelho leva tudo ao Supabase para o web e o
-- app lerem sem depender do HANA.
--
-- Como usar: cole e execute no SQL Editor do Supabase (na ordem em que está).
-- Idempotente: CREATE ... IF NOT EXISTS + DROP POLICY IF EXISTS.
-- ----------------------------------------------------------------------------
-- DECISÕES QUE ESTA DDL CARREGA (sonda de 17/09/2026 na view inteira, 5.643 linhas):
--
--   * `nf_quitada` é BOOLEANO DE TRÊS ESTADOS: NULL = nota ainda não emitida.
--     O `QuitacaoNF` da view (texto 'Sim'/'Não') sozinho mente: 4.310 linhas
--     dizem 'Não' apenas porque não há nota. O pipeline aplica a regra no SELECT
--     do HANA, para nenhum leitor precisar redescobri-la;
--   * texto vazio vira NULL: `AcaoContato`, `Lead` e `SituacaoCliente` vêm da
--     view como STRING VAZIA, nunca NULL;
--   * SEM chave única: a view é de evolução e pode repetir a mesma `cotacao`
--     (hoje 1 caso em 5.643). A carga é snapshot por `id_execucao` — insere e
--     depois poda as execuções anteriores —, então a unicidade não é necessária
--     e uma constraint aqui derrubaria a carga no dia em que a view repetir.
--
-- ⚠️ A view NÃO tem mais 2024 (começa em 06/01/2025). Quem precisar do histórico
--    anterior tem de ir na VW_ORCAMENTO_ALT, que tem 16 colunas e NENHUM dos
--    campos novos.
-- ----------------------------------------------------------------------------
-- SEGURANÇA / RLS (mesmo padrão dos outros espelhos):
--   * RLS ENABLE + FORCE;
--   * leitura para `authenticated` (o app e o web entram autenticados);
--   * NADA para `anon` — o fechamento do anon está em curso (ver o plano de
--     segurança do Supabase); abrir tabela nova para anon andaria para trás.
--     Se algum leitor anônimo precisar, a policy comentada no fim é o caminho;
--   * escrita só pelo `service_role` (BYPASSRLS), sem policy.
-- ============================================================================

create table if not exists public.orcamentos_espelho (
  id                    bigint generated always as identity primary key,

  -- ===== Colunas da view VW_EVOL_ORCAMENTO_ALT (34) =====
  cotacao               integer,
  tipo_doc              text,          -- '23' = cotação, '17' = pedido
  num_doc               integer,
  num_oport             integer,
  status_wbc            text,          -- 40 = emissão p/ cliente, 60 = fechado/pedido, 99 = cancelado
  n_wbc                 text,          -- 8 dígitos, com zeros à esquerda
  versao                text,
  data_criacao_pn       date,          -- cliente cadastrado desde
  cod_pn                text,
  nome_pn               text,
  contato_cliente       text,
  email_cliente         text,
  representante         text,
  valor                 numeric(21,6),
  data_oport            date,
  data_cotacao          date,
  uf                    text,
  municipio             text,
  data_contato_cliente  date,
  acao_contato          text,
  lead                  text,
  situacao_cliente      text,
  n_bitrix              text,
  pct_comissao          numeric(21,6),
  retorno               text,
  indice                text,
  cnae                  text,
  descricao_cnae        text,
  tipo_montagem         text,
  valor_montagem        numeric(21,6),
  montador              text,
  num_nf                integer,
  data_nf               date,
  nf_quitada            boolean,       -- NULL = sem nota emitida (ver decisões acima)

  -- ===== Controle / auditoria (postos pelo pipeline; não estão na view) =====
  id_execucao           uuid,          -- UUID da carga; a poda do snapshot usa esta coluna
  data_hora_extracao    timestamptz,
  inserted_at           timestamptz default now()
);

comment on table public.orcamentos_espelho is
  'Espelho da view SAP HANA VW_EVOL_ORCAMENTO_ALT (34 colunas: orçamento + CNAE + montagem + nota fiscal). Snapshot de hora em hora por extract_orcamentos_espelho.py. nf_quitada NULL = sem nota. Escrita só service_role; leitura p/ authenticated.';

comment on column public.orcamentos_espelho.nf_quitada is
  'NULL = nota ainda não emitida; true/false só quando num_nf existe. O QuitacaoNF cru da view diz "Não" para todo orçamento sem nota.';

create index if not exists idx_orc_esp_data_cotacao on public.orcamentos_espelho (data_cotacao);
create index if not exists idx_orc_esp_n_wbc        on public.orcamentos_espelho (n_wbc);
create index if not exists idx_orc_esp_cotacao      on public.orcamentos_espelho (cotacao);
create index if not exists idx_orc_esp_num_nf       on public.orcamentos_espelho (num_nf);
create index if not exists idx_orc_esp_id_execucao  on public.orcamentos_espelho (id_execucao);

alter table public.orcamentos_espelho enable row level security;
alter table public.orcamentos_espelho force  row level security;

drop policy if exists "orcamentos_espelho_read_auth" on public.orcamentos_espelho;
create policy "orcamentos_espelho_read_auth"
  on public.orcamentos_espelho for select to authenticated using (true);

-- Se (e só se) um leitor anônimo precisar desta tabela:
-- drop policy if exists "orcamentos_espelho_read_anon" on public.orcamentos_espelho;
-- create policy "orcamentos_espelho_read_anon"
--   on public.orcamentos_espelho for select to anon using (true);

notify pgrst, 'reload schema';

-- ----------------------------------------------------------------------------
-- Conferência depois de rodar:
--   SELECT count(*) FROM public.orcamentos_espelho;                 -- ~5.6 mil
--   SELECT count(DISTINCT id_execucao) FROM public.orcamentos_espelho;  -- tem de ser 1
--   SELECT nf_quitada, count(*) FROM public.orcamentos_espelho GROUP BY 1;
--   SELECT c.relrowsecurity AS rls, c.relforcerowsecurity AS force_rls
--     FROM pg_class c WHERE c.relname = 'orcamentos_espelho';
-- ----------------------------------------------------------------------------
