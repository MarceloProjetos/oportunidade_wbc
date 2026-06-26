-- ============================================================================
-- Leitura READ-ONLY das tabelas de OS para a chave `anon` (equipe consumidora)
-- Rode UMA VEZ no SQL Editor do Supabase, DEPOIS de ordens_servico_engenharia.sql.
--
-- O que faz:
--   * Cria política SOMENTE de SELECT para a role `anon` na tabela principal e no
--     lookup de status. Como NÃO existe nenhuma política de INSERT/UPDATE/DELETE,
--     a `anon` fica **read-only por construção** (não consegue escrever).
--   * Escrita/atualização continua só via service_role (backend/API).
--   * SELECT com USING(true) NÃO dispara o warning "RLS Policy Always True" do
--     Advisor (esse warning é só para policies de escrita) e ainda remove o INFO
--     "RLS enabled, no policy" dessas duas tabelas.
--
-- NÃO libera o log (`sincronizacao_log_os_eng`) — uso interno; permanece trancado.
-- Idempotente (DROP POLICY IF EXISTS antes do CREATE).
-- ============================================================================

-- 1) Tabela principal — leitura para anon
drop policy if exists "os_eng_read_anon" on public.ordens_servico_engenharia;
create policy "os_eng_read_anon"
  on public.ordens_servico_engenharia
  for select to anon
  using (true);

-- 2) Lookup de status — leitura para anon
drop policy if exists "os_eng_status_read_anon" on public.status_ordens_servico_eng;
create policy "os_eng_status_read_anon"
  on public.status_ordens_servico_eng
  for select to anon
  using (true);

-- ----------------------------------------------------------------------------
-- VERIFICAÇÃO (opcional)
-- ----------------------------------------------------------------------------
-- Deve listar as 2 policies de SELECT para {anon}:
--   SELECT tablename, policyname, cmd, roles, qual
--   FROM pg_policies
--   WHERE tablename IN ('ordens_servico_engenharia','status_ordens_servico_eng');
--
-- Confirme que a anon NÃO escreve (deve falhar / 0 linhas afetadas via PostgREST):
--   -- INSERT/UPDATE/DELETE com a chave anon → negado (não há policy de escrita).
