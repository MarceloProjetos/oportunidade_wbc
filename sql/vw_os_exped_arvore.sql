-- ============================================================================
-- vw_os_exped_arvore — detalhe da ÁRVORE de produto WBC (explosão do BOM)
--   1 linha por componente (todos os níveis) com o cabeçalho do pedido/OS.
--
-- Fontes (tabelas-espelho, somente leitura via `anon`):
--   public.ordens_servico_engenharia (e) — espelho de VW_EXPORT_ORDENS_SERVICO_1
--   public.wbc_arvore_produto        (a) — espelho de WBCCAD.dbo.INTEGRACAO_ORCPRDARV
-- Chave de junção: e."CodigoOrcam" = a."ORCNUM".
--
-- POR QUE ESTA VIEW CONTINUA EXISTINDO (e não virou tabela direta do HANA):
--   O ramo "cabeçalho + estrutura de OS" da EXPED agora é espelhado DIRETO do HANA
--   na tabela public.vw_os_exped_impressao_v2 (55 colunas, sem perdas). Esta view
--   é COMPLEMENTAR: entrega a explosão da árvore WBC (1 linha por componente, todos
--   os níveis) — um grão que a view V2 do HANA NÃO traz. A multiplicação de linhas
--   aqui é INTENCIONAL (é o grão do BOM); o cabeçalho é colapsado com DISTINCT ON
--   p/ não multiplicar pelo nº de itens de estrutura da OS.
--
-- REDUNDÂNCIA REMOVIDA: a antiga view `vw_os_exped_impressao` (adaptação lossy da
--   V2 a partir dos espelhos — Filial/OITM/ALMX saíam NULL) foi descontinuada;
--   substituída por public.vw_os_exped_impressao_v2 (espelho direto do HANA). O
--   DROP abaixo remove a view antiga do banco, se ela chegou a ser criada.
-- ============================================================================

drop view if exists public.vw_os_exped_impressao;


-- ----------------------------------------------------------------------------
-- vw_os_exped_arvore — cabeçalho do pedido (1 linha por NPED) × árvore do orçamento
-- ----------------------------------------------------------------------------
create or replace view public.vw_os_exped_arvore as
with cab as (   -- 1 linha de cabeçalho por pedido (qualquer linha serve; valores iguais)
  select distinct on (e."NPED")
    e."NPED", e."CodigoOrcam", e."NomeClien", e."NomedVend", e."CodClien",
    e."DtPedido", e."DtVenc", e."DtEntregaPED", e."Status", e."ObsPedido",
    e."Usuario", e."U_INO_VERSAOWBC", e."U_INO_PROJETO"
  from public.ordens_servico_engenharia e
  where e."CodigoOrcam" is not null
  order by e."NPED", e."id"
)
select
  'EXP'::text            as "TIPO",
  cab."NPED"             as "NPED",
  cab."NomeClien"        as "NomeClien",
  cab."NomedVend"        as "NomedVend",
  cab."CodClien"         as "CodClien",
  cab."DtPedido"         as "DtPedido",
  cab."DtVenc"           as "DtVenc",
  cab."DtEntregaPED"     as "DtEntregaPED",
  cab."Status"           as "Status",
  cab."ObsPedido"        as "ObsPedido",
  cab."Usuario"          as "Usuario",
  cab."U_INO_VERSAOWBC"  as "U_INO_VERSAOWBC",
  cab."U_INO_PROJETO"    as "U_INO_PROJETO",
  cab."CodigoOrcam"      as "CodigoOrcam",
  -- ===== detalhe da árvore WBC (componente) =====
  a."ORCPRDARV_NIVEL"    as "U_INO_NIVEL",     -- nível na árvore (1, 2, ...)
  a."GRPCOD"             as "GRPCOD",
  a."SUBGRPCOD"          as "SUBGRPCOD",
  a."ORCITM"             as "ORCITM",
  a."PRDCOD"             as "CodItemOrcam",    -- = PRDCOD
  a."PRDDSC"             as "DescProdOrcam",   -- = PRDDSC
  a."CORCOD"             as "CorOrcam",        -- = CORCOD
  a."ORCQTD"             as "QtdOrcam",        -- = ORCQTD
  a."ORCTOT"             as "TotalOrcam",      -- = ORCTOT
  a."ORCPES"             as "PesoOrcam"        -- = ORCPES
from cab
join public.wbc_arvore_produto a
  on a."ORCNUM" = cab."CodigoOrcam"
order by cab."NPED", a."ORCPRDARV_NIVEL", a."ORCITM";

comment on view public.vw_os_exped_arvore is
  'Detalhe da árvore de produto WBC (1 linha por componente, todos os níveis) com o cabeçalho do pedido/OS. Junta ordens_servico_engenharia (colapsada por NPED) com wbc_arvore_produto por CodigoOrcam=ORCNUM. Complementa a tabela vw_os_exped_impressao_v2 (espelho direto do HANA).';


-- ----------------------------------------------------------------------------
-- Leitura read-only p/ a `anon` (rode se o consumidor for ler a VIEW direto).
-- ----------------------------------------------------------------------------
-- grant select on public.vw_os_exped_arvore to anon;
--
-- VERIFICAÇÃO:
--   select * from public.vw_os_exped_arvore where "NPED" = 84112 order by "U_INO_NIVEL";
