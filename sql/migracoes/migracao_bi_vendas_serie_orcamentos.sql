-- ============================================================================
-- MIGRAÇÃO: bi_vendas_serie_mensal aceita metrica='orcamentos'
-- Decisão 4 do plano Vendas Resultados (opção 1, Marcelo 20/08): série de
-- ORÇAMENTOS EMITIDOS (cotações da VW_ORCAMENTO_ALT — a view NÃO é meta).
--
-- Como usar: cole e execute no SQL Editor do Supabase (produção). Idempotente.
--
-- ⚠️ ORDEM DO DEPLOY (mesma regra da migração de UF):
--   1º) ESTE script no Supabase (ampliar o CHECK não afeta o código velho);
--   2º) SÓ DEPOIS o deploy_update.bat na .11 — o código novo grava
--       'orcamentos', e no CHECK antigo a carga da série falharia a cada 15 min.
-- ============================================================================

alter table public.bi_vendas_serie_mensal
  drop constraint if exists bi_vendas_serie_mensal_metrica_check;

alter table public.bi_vendas_serie_mensal
  add constraint bi_vendas_serie_mensal_metrica_check
  check (metrica in ('pedidos','faturamento','orcamentos'));

-- Conferência (depois do deploy da .11 e de uma carga):
-- select ano, mes, valor, qtd_pedidos from public.bi_vendas_serie_mensal
--  where metrica='orcamentos' and vendedor='__TOTAL__' order by ano, mes;
-- Esperado p/ 2026 (probe 20/08, bruto): jan 64,8M · fev 34,7M · mar 42,7M ·
-- abr 38,0M · mai 35,8M · jun 78,4M · jul 117,4M · ago 33,8M (parcial).
