-- ============================================================================
-- MIGRAÇÃO: bi_vendas_ranking aceita tipo='uf' (fase 2 do card Clientes por UF)
-- Pedido do Marcelo em 20/08/2026 — plano em
-- PLANO_VENDAS_RESULTADOS do web_orcaview_V117, decisão 3, fase 2 (entregue na V117.834;
-- apagado de docs/ em 05/09/2026, texto no git daquele repo).
--
-- Como usar: cole e execute no SQL Editor do Supabase (projeto de produção).
-- Idempotente: DROP CONSTRAINT IF EXISTS + ADD.
--
-- ⚠️ ORDEM DO DEPLOY (a lição da V117.131 do mobile, ao contrário):
--   1º) ESTE script no Supabase — ampliar o domínio do CHECK não afeta o
--       código velho, que continua gravando 'vendedor'/'cliente' normalmente.
--   2º) SÓ DEPOIS o `git pull` + restart dos NSSM na .11 — o código novo grava
--       'uf', e no CHECK antigo a carga INTEIRA do ranking falharia a cada
--       15 min (upsert atômico), deixando o card do web em silêncio.
-- ============================================================================

alter table public.bi_vendas_ranking
  drop constraint if exists bi_vendas_ranking_tipo_check;

alter table public.bi_vendas_ranking
  add constraint bi_vendas_ranking_tipo_check
  check (tipo in ('vendedor','cliente','uf'));

-- ----------------------------------------------------------------------------
-- Conferência (depois do deploy da .11 e de uma carga de 15 min):
-- select escopo, chave, valor, posicao from public.bi_vendas_ranking
--  where tipo = 'uf' and vendedor = '__TOTAL__' order by escopo, posicao;
-- Esperado: até ~27 linhas por escopo (uma por UF com venda; 'ND' = sem UF
-- no cadastro), e o SUM(valor) de cada escopo = o KPI do mesmo escopo.
-- ----------------------------------------------------------------------------
