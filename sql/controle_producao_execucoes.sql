-- ============================================================================
-- controle_producao_execucoes — histórico da tela Execuções do Controle de Produção
-- ============================================================================
-- Onde rodar: SQL Editor do SUPABASE (Postgres). NÃO é SQL do HANA.
-- Quando: uma vez, antes (ou logo depois) do deploy de 29/09/2026. Sem a tabela, a
--   .11 só registra um aviso no log a cada execução e a tela mostra a memória do processo.
--
-- Quem escreve: só o serviço OrcaView-ControleProducao na .11 (controleproducao/core/historico.py),
--   com a SUPABASE_SERVICE_ROLE_KEY. Uma linha por execução terminada (concluída, erro ou
--   cancelada); depois de cada gravação o serviço apaga o que passar das 30 mais recentes
--   (por id) — o mesmo padrão de poda de sincronizacao_log / sincronizacao_log_os_integracao.
--
-- Por que tabela nova (regra "estender antes de criar", conferida em 29/09/2026):
--   * sincronizacao_log — heartbeat da carga de oportunidades (6 linhas, colunas de carga);
--   * sincronizacao_log_os_integracao — nped, duração, status, qtd_registros;
--   * rotinas_execucao — UMA linha por rotina (última execução), não histórico.
--   Nenhuma tem operação, módulo, log por linha nem resultado.
--
-- Acesso: RLS ligado e SEM policy — só o service_role lê e grava (igual às outras tabelas
--   de log do SIS). A anon key não enxerga nada daqui.
-- ============================================================================

create table if not exists public.controle_producao_execucoes (
  id                bigint generated always as identity primary key,
  tarefa_id         text        not null unique,   -- id da tela (/tarefas/<tarefa_id>)
  modulo            text        not null,          -- pedidos_wbc | manutencao_op
  nome              text        not null,          -- operação ("Processar novos", "Encerrar OPs"…)
  descricao         text,
  situacao          text        not null
                    check (situacao in ('concluída', 'erro', 'cancelada')),
  com_falhas        boolean     not null default false,  -- concluída, mas com itens em com_erro
  criada_em         timestamptz not null,
  iniciada_em       timestamptz,
  terminada_em      timestamptz,
  duracao_segundos  numeric(10,1),
  passo             text,                          -- último passo mostrado
  passos_feitos     integer     not null default 0,
  passos_total      integer     not null default 0,
  linhas            jsonb       not null default '[]'::jsonb,  -- o log da execução (até 500 linhas)
  resultado         jsonb,
  erro              text,
  solicitante       text,                          -- quem pediu (API: obrigatório; tela: nulo)
  origem            text        not null default 'tela' check (origem in ('tela', 'api')),
  gravada_em        timestamptz not null default now()
);

comment on table public.controle_producao_execucoes is
  'Últimas 30 execuções da tela Execuções do Controle de Produção (.11:8080). '
  'Escrita e poda pelo serviço OrcaView-ControleProducao (service_role); sem acesso anon.';

alter table public.controle_producao_execucoes enable row level security;
alter table public.controle_producao_execucoes force  row level security;
-- (sem policy: só o service_role acessa)

-- Tabela criada antes de 29/09/2026: as duas colunas de quem pediu (API da Manutenção de OP,
-- PLANO_API_MANUTENCAO_OP.md (removed 2026-10-06) F2). Aplicado pelo Marcelo em 29/09/2026; idempotente.
alter table public.controle_producao_execucoes
  add column if not exists solicitante text,
  add column if not exists origem text not null default 'tela'
    check (origem in ('tela', 'api'));

-- Recarrega o cache do PostgREST — sem isso a 1ª gravação pode falhar com PGRST205/PGRST204.
notify pgrst, 'reload schema';

-- ----------------------------------------------------------------------------
-- Conferência (rodar depois, no mesmo SQL Editor):
--   select relrowsecurity, relforcerowsecurity from pg_class
--    where relname = 'controle_producao_execucoes';              -- true, true
--   select count(*) from pg_policies
--    where tablename = 'controle_producao_execucoes';            -- 0
--   select tarefa_id, nome, situacao, criada_em from public.controle_producao_execucoes
--    order by id desc;                                            -- até 30 linhas
-- ----------------------------------------------------------------------------
