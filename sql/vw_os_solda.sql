-- ============================================================================
-- vw_os_solda — DDL para o Supabase (PostgreSQL)
-- Espelho DIRETO 1:1 da view SAP HANA (schema SBOALTAMIRAPROD):
--
--   SBOALTAMIRAPROD.VW_OS_SOLDA_DETALHE  → public.vw_os_solda  (42 col)
--
-- Pipeline: extract_os_impressao_views.py (registry config.OS_IMPRESSAO_VIEWS).
--   Carga sob demanda, por NPED, disparada após a OS. Estratégia replace_nped
--   (carrega-depois-poda escopado ao NPED). Log compartilhado com as views de
--   impressão: sincronizacao_log_os_impressao (coluna origem_view distingue).
--
-- Como usar: cole e execute no SQL Editor do Supabase. A leitura read-only p/ a
--   chave `anon` já está incluída no final (policy de SELECT).
-- ----------------------------------------------------------------------------
-- OBS. sobre nomes/tipos:
--   * Nomes entre aspas preservam o case EXATO da view HANA (o pipeline insere via
--     PostgREST com o nome idêntico). Desde 2026-07-10 a view TAMBÉM tem o bloco de
--     endereço da filial, com colunas COM espaço ("Rua Filial", "Nº Filial", etc.).
--   * DECIMAL do HANA → numeric (sem precisão fixa); INTEGER/SMALLINT → integer;
--     NVARCHAR/VARCHAR → text; TIMESTAMP → timestamp.
--   * Além do bloco de filial, tem campos de solda/orçamento (LinhaOrcam,
--     U_INO_ORCAMENTO, U_INO_ORCITM, U_INO_EXPL_SOLDA, ItmsGrpCod_OITM). TIPO = 'SOLD'.
--
-- SEGURANÇA / RLS: RLS ENABLE + FORCE. Escrita só pelo service_role (o pipeline).
--   Leitura liberada p/ `anon` (policy de SELECT). Idempotente.
-- ============================================================================

create table if not exists public.vw_os_solda (
  id                    bigint generated always as identity primary key,

  "TIPO"                text,
  "NPED"                integer,
  "DtPedido"            timestamp,
  "U_INO_ORCAMENTO"     integer,
  "DtVenc"              timestamp,
  "NomeClien"           text,
  "NomedVend"           text,
  "DescItemEstrut"      text,
  "GrpMaterialEstrut"   integer,
  "GrpItensEstrut"      integer,
  "CodDetalhOrcamento"  integer,
  "DtEntregaPED"        timestamp,
  "DiasTotal"           integer,
  "ObsPedido"           text,
  "CodigoOrcam"         text,
  "LinhaOrcam"          integer,
  "PesoOrcam"           numeric,
  "CodItemOrcam"        text,
  "QtdOrcam"            numeric,
  "DescProdOrcam"       text,
  "CorOrcam"            text,
  "PrecoOrcam"          numeric,
  "TotalOrcam"          numeric,
  "Usuario"             text,
  "U_INO_NIVEL"         text,
  "U_INO_ORCITM"        text,
  "ItmsGrpCod_OITM"     integer,
  "U_INO_EXPL_SOLDA"    text,
  "U_INO_VERSAOWBC"     text,
  "U_INO_PROJETO"       text,

  -- ===== Bloco de endereço da FILIAL (adicionado à view depois; nomes COM espaço) =====
  -- GOTCHA: o número aqui é "Nº Filial" (espaço + 'º' ordinal, U+00BA), byte-exato da
  --   view HANA. As views de impressão (exped/pintura/almox) usam "NFilial" (ASCII) —
  --   nomes DIFERENTES p/ o mesmo conceito. O pipeline faz SELECT * e casa por NOME:
  --   NÃO troque o nome só no Supabase (quebra a carga com PGRST204); só mudando o alias
  --   na view HANA. Round-trip UTF-8 validado em prod (2026-07-10). Consumidor deve
  --   normalizar num adapter: row["Nº Filial"] ?? row["NFilial"].
  "Filial"              text,
  "Tipo Logradouro"     text,
  "Rua Filial"          text,
  "Nº Filial"           text,
  "Complemento Filial"  text,
  "CEP Filial"          text,
  "Bairro Filial"       text,
  "Cidade Filial"       text,
  "Estado Filial"       text,
  "CNPJ Filial"         text,
  "IE Filial"           text,
  "Matriz"              text,

  -- ===== Controle / auditoria (adicionados pelo pipeline) =====
  id_execucao           uuid,
  data_hora_extracao    timestamp,
  origem_view           text default 'VW_OS_SOLDA_DETALHE',
  inserted_at           timestamptz default now()
);

comment on table public.vw_os_solda is
  'Espelho por NPED da view SAP HANA VW_OS_SOLDA_DETALHE (detalhe de solda). Carga sob demanda (replace_nped) após a OS. Escrita só service_role; leitura read-only p/ anon.';

create index if not exists idx_vw_solda_nped        on public.vw_os_solda ("NPED");
create index if not exists idx_vw_solda_orcam        on public.vw_os_solda ("CodigoOrcam");
create index if not exists idx_vw_solda_id_execucao  on public.vw_os_solda (id_execucao);

alter table public.vw_os_solda enable row level security;
alter table public.vw_os_solda force  row level security;

-- Leitura READ-ONLY p/ `anon` (sem policy de INSERT/UPDATE/DELETE → read-only).
drop policy if exists "vw_os_solda_read_anon" on public.vw_os_solda;
create policy "vw_os_solda_read_anon"
  on public.vw_os_solda for select to anon using (true);

-- ============================================================================
-- VERIFICAÇÃO (opcional)
--   SELECT tablename, policyname, cmd, roles FROM pg_policies WHERE tablename='vw_os_solda';
--   Após sync (ex.: NPED 84124):
--   SELECT count(*) FROM public.vw_os_solda WHERE "NPED" = 84124;
-- ============================================================================
