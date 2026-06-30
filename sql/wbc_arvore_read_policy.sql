-- ============================================================================
-- Leitura READ-ONLY da árvore WBC para a chave `anon` (programa consumidor)
-- Rode UMA VEZ no SQL Editor do Supabase, DEPOIS de wbc_arvore.sql.
--
-- O que faz:
--   * Cria política SOMENTE de SELECT para a role `anon` na tabela da árvore. Como
--     NÃO existe nenhuma política de INSERT/UPDATE/DELETE, a `anon` fica
--     **read-only por construção** (não consegue escrever).
--   * Escrita continua só via service_role (o pipeline).
--   * SELECT com USING(true) não dispara o warning "RLS Policy Always True" do
--     Advisor (esse warning é só p/ policies de escrita) e remove o INFO
--     "RLS enabled, no policy" desta tabela.
--
-- NÃO libera o log (`sincronizacao_log_wbc_arvore`) — uso interno; permanece trancado.
-- Idempotente (DROP POLICY IF EXISTS antes do CREATE).
-- ============================================================================

drop policy if exists "wbc_arvore_read_anon" on public.wbc_arvore_produto;
create policy "wbc_arvore_read_anon"
  on public.wbc_arvore_produto
  for select to anon
  using (true);

-- ----------------------------------------------------------------------------
-- VERIFICAÇÃO (opcional)
-- ----------------------------------------------------------------------------
-- Deve listar 1 policy de SELECT para {anon}:
--   SELECT tablename, policyname, cmd, roles, qual
--   FROM pg_policies WHERE tablename = 'wbc_arvore_produto';
--
-- Confirme que a anon NÃO escreve (INSERT/UPDATE/DELETE via chave anon → negado).
