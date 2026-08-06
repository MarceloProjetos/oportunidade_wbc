-- ============================================================================
-- ALTER: flag "Compras" na VW_OS_INTEGRACAO — alinhar o espelho à view HANA
-- Data: 2026-08-05 (o SEGUNDO ALTER do dia — o outro é o U_INO_D_Adicionais)
--
-- A view SBOALTAMIRAPROD.VW_OS_INTEGRACAO ganhou 1 coluna na POSIÇÃO 56, logo
-- depois de "Exped":
--
--   56) "Compras"  INTEGER
--
-- É a QUINTA flag de PROCESSO por item (1 = o item passa pelo processo, 0 =
-- não), na mesma família de Solda/Pintura/Almox/Exped (add. 15/07). Como as
-- outras quatro, é POR ITEM: um pedido normalmente tem itens mistos, então não
-- existe "o pedido X é de compras" — existe "N dos M itens do pedido X passam
-- por compras".
--
-- URGÊNCIA: a mesma dos ALTERs anteriores. O pipeline extrai com `SELECT *` e
--   insere via PostgREST casando por NOME — coluna que existe na view e não
--   existe na tabela derruba o INSERT com PGRST204 e NENHUM pedido sincroniza.
--
-- NÃO re-rode o vw_os_integracao.sql: o DDL base começa com `drop table ...
--   cascade` e APAGARIA a tabela de produção.
--
-- Tipo: INTEGER → integer (mesma convenção das outras 4 flags). Sem NOT NULL,
-- embora a view declare NOT NULL — o espelho é permissivo de propósito.
-- Idempotente: add column if not exists.
-- ============================================================================

alter table public.vw_os_integracao
  add column if not exists "Compras" integer;

comment on column public.vw_os_integracao."Compras" is
  'Flag por item: 1 = o item passa por compras, 0 = não passa. Origem: VW_OS_INTEGRACAO (INTEGER na view, posição 56). 5ª flag de processo, irmã de Solda/Pintura/Almox/Exped.';


-- ----------------------------------------------------------------------------
-- OBRIGATÓRIO: recarregar o cache do schema do PostgREST.
-- ----------------------------------------------------------------------------
notify pgrst, 'reload schema';


-- ============================================================================
-- VERIFICAÇÃO (rodar depois do ALTER)
-- ============================================================================
-- A coluna existe?
--   SELECT column_name, data_type FROM information_schema.columns
--   WHERE table_schema='public' AND table_name='vw_os_integracao'
--     AND column_name='Compras';
--
-- Depois de re-sincronizar um pedido, quantos itens passam por compras?
--   SELECT count(*) AS linhas, sum("Compras") AS itens_em_compras
--   FROM public.vw_os_integracao WHERE "N_PED" = 84227;
