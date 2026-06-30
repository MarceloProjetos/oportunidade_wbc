-- ============================================================================
-- Relatório de Expedição/OS — adaptação da view SAP HANA
--   SBOALTAMIRAPROD.VW_OS_EXPED_IMPRESSAO_V2  → PostgreSQL/Supabase
--
-- Fontes (tabelas-espelho, somente leitura via `anon`):
--   public.ordens_servico_engenharia (e) — espelho de VW_EXPORT_ORDENS_SERVICO_1
--   public.wbc_arvore_produto        (a) — espelho de WBCCAD.dbo.INTEGRACAO_ORCPRDARV
-- Chave de junção: e."CodigoOrcam" = a."ORCNUM".
--
-- DUAS VIEWS (propósitos diferentes):
--   1) vw_os_exped_impressao  → reproduz a V2 (ramo EXP, nível 1). Vem SÓ da tabela
--      de OS (o espelho já traz os campos do orçamento). NÃO junta a árvore → não
--      multiplica linhas. É a adaptação direta da V2.
--   2) vw_os_exped_arvore     → detalhe da ÁRVORE WBC: 1 linha por componente (todos
--      os níveis), com o cabeçalho do pedido/OS repetido. Usa o join por ORCNUM.
--
-- INDISPONÍVEL nos espelhos (sai NULL): bloco Filial/endereço (OBPL), GrpMaterialEstrut /
--   GrpItensEstrut (OITM), e o ramo ALMX (outra view VW_OS_ALMOX_IMPRESSAO). U_INO_NIVEL
--   é constante '1' (a V2 só traz nível 1). Verificado por 3 revisões adversariais: nenhuma
--   coluna referenciada é inexistente.
-- ============================================================================


-- ----------------------------------------------------------------------------
-- 1) vw_os_exped_impressao — V2 fiel (ramo EXP, nível 1), SÓ tabela de OS
-- ----------------------------------------------------------------------------
create or replace view public.vw_os_exped_impressao as
select
  'EXP'::text            as "TIPO",
  e."NPED"               as "NPED",
  e."DtVenc"             as "DtVenc",
  e."NomeClien"          as "NomeClien",
  e."NomedVend"          as "NomedVend",
  e."DtPedido"           as "DtPedido",
  e."DiasTotal"          as "DiasTotal",
  e."DtInic"             as "DtInic",
  e."Obs"                as "Obs",
  e."DtEncerr"           as "DtEncerr",
  e."DtLiber"            as "DtLiber",
  e."CodClien"           as "CodClien",
  e."Status"             as "Status",
  e."Deposito"           as "Deposito",
  e."UM"                 as "UM",
  e."CodItemEstrut"      as "CodItemEstrut",
  e."DescItemEstrut"     as "DescItemEstrut",
  null::text             as "GrpMaterialEstrut",   -- OITM.MatGrp: indisponível nos espelhos
  null::integer          as "GrpItensEstrut",      -- OITM.ItmsGrpCod: indisponível
  e."QtdBasEstrut"       as "QtdBasEstrut",
  e."QtdPlanEstrut"      as "QtdPlanEstrut",
  e."QtdSaida"           as "QtdSaida",
  e."TipoEmissOP"        as "TipoEmissOP",
  e."DeposEstrut"        as "DeposEstrut",
  e."TipoItemEstrut"     as "TipoItemEstrut",
  e."QtdLiberEstrut"     as "QtdLiberEstrut",
  e."PesoEstrut"         as "PesoEstrut",
  e."CodigoOrcam"        as "U_INO_COD",           -- T5.U_INO_COD = CodigoOrcam
  e."CodDetalhOrcamento" as "CodDetalhOrcamento",
  e."DtEntregaPED"       as "DtEntregaPED",
  e."ObsPedido"          as "ObsPedido",
  e."CodigoOrcam"        as "CodigoOrcam",
  e."PesoOrcam"          as "PesoOrcam",
  e."CodItemOrcam"       as "CodItemOrcam",
  e."QtdOrcam"           as "QtdOrcam",
  e."DescProdOrcam"      as "DescProdOrcam",
  e."CorOrcam"           as "CorOrcam",
  e."PrecoOrcam"         as "PrecoOrcam",
  e."TotalOrcam"         as "TotalOrcam",
  null::text             as "Filial",              -- OBPL.BPLName: indisponível
  null::text             as "Tipo Logradouro",     -- OBPL.AddrType
  null::text             as "Rua Filial",          -- OBPL.Street
  null::text             as "NFilial",             -- OBPL.StreetNo
  null::text             as "Complemento Filial",  -- OBPL.Building
  null::text             as "CEP Filial",          -- OBPL.ZipCode
  null::text             as "Bairro Filial",       -- OBPL.Block
  null::text             as "Cidade Filial",       -- OBPL.City
  null::text             as "Estado Filial",       -- OBPL.State
  null::text             as "CNPJ Filial",         -- OBPL.TaxIdNum
  null::text             as "IE Filial",           -- OBPL.TaxIdNum2
  null::text             as "Matriz",              -- OBPL.MainBPL
  e."Usuario"            as "Usuario",
  '1'::text              as "U_INO_NIVEL",         -- a V2 só traz nível 1
  e."U_INO_VERSAOWBC"    as "U_INO_VERSAOWBC",
  e."U_INO_PROJETO"      as "U_INO_PROJETO"
from public.ordens_servico_engenharia e
where e."Status" <> 'C'
  -- mesmos recortes do ramo EXP_OP_NIVEL1 da V2 (coalesce p/ não derrubar item nulo):
  and substring(coalesce(e."CodItemEstrut", ''), 1, 3) not in ('ALP', 'ATR', 'TEG', 'REF', 'TPO')
  and substring(coalesce(e."CodItemEstrut", ''), 1, 4) not in ('GALV', 'PAR0', 'PAR3')
  and substring(coalesce(e."CodItemEstrut", ''), 1, 6) <> 'PAIFET';

comment on view public.vw_os_exped_impressao is
  'Adaptação de SBOALTAMIRAPROD.VW_OS_EXPED_IMPRESSAO_V2 (ramo EXP, nível 1). Origem: ordens_servico_engenharia. Filial/OITM/ALMX indisponíveis (NULL). Não junta a árvore WBC (não multiplica).';


-- ----------------------------------------------------------------------------
-- 2) vw_os_exped_arvore — detalhe da árvore WBC (1 linha por componente)
--    Cabeçalho do pedido (1 linha por NPED) × árvore do orçamento (todos os níveis).
--    A multiplicação aqui é INTENCIONAL (é o grão do BOM). O cabeçalho é colapsado
--    com DISTINCT ON p/ NÃO multiplicar pelo nº de itens de estrutura da OS.
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
  'Detalhe da árvore de produto WBC (1 linha por componente, todos os níveis) com o cabeçalho do pedido/OS. Junta ordens_servico_engenharia (colapsada por NPED) com wbc_arvore_produto por CodigoOrcam=ORCNUM.';


-- ----------------------------------------------------------------------------
-- Leitura read-only para a `anon` (rode após criar as views, se o consumidor
-- for ler as VIEWS diretamente em vez das tabelas-base).
-- ----------------------------------------------------------------------------
-- grant select on public.vw_os_exped_impressao to anon;
-- grant select on public.vw_os_exped_arvore     to anon;
--
-- VERIFICAÇÃO:
--   select count(*) from public.vw_os_exped_impressao;
--   select * from public.vw_os_exped_arvore where "NPED" = 84112 order by "U_INO_NIVEL";
