-- ============================================================================
-- bi_vendas_ranking: ranking por ESCOPO (hoje / ontem / mês atual / mês passado)
--
-- Motivo: na tela do app os quatro cartões do topo viraram FILTRO — tocar em
-- "ontem" troca o ranking inteiro. Ranking de um dia não se deriva do ranking do
-- mês, então o pipeline passou a gravar um par (vendedores + clientes) por
-- escopo, e a chave natural mudou de (competencia, …) para (escopo, …).
--
-- `competencia` continua na tabela como informação (o dia, nos escopos diários;
-- o 1º do mês, nos mensais) — só deixou de ser chave.
--
-- Idempotente. Aplicar no SQL Editor do Supabase antes de subir o pipeline novo.
-- ============================================================================

alter table public.bi_vendas_ranking
  add column if not exists escopo text not null default 'mes_atual';

do $$
begin
  if exists (
    select 1 from pg_constraint
     where conname = 'bi_vendas_ranking_escopo_check'
       and conrelid = 'public.bi_vendas_ranking'::regclass
  ) then
    alter table public.bi_vendas_ranking drop constraint bi_vendas_ranking_escopo_check;
  end if;
end $$;

alter table public.bi_vendas_ranking
  add constraint bi_vendas_ranking_escopo_check
  check (escopo in ('hoje','ontem','mes_atual','mes_passado'));

-- Troca da chave natural. As linhas antigas (só do mês) já receberam
-- 'mes_atual' pelo default, então não há colisão na recriação.
alter table public.bi_vendas_ranking drop constraint if exists bi_vendas_ranking_pkey;
alter table public.bi_vendas_ranking
  add constraint bi_vendas_ranking_pkey primary key (escopo, tipo, vendedor, chave);

drop index if exists ix_bi_vendas_ranking_comp;
create index if not exists ix_bi_vendas_ranking_escopo
  on public.bi_vendas_ranking (vendedor, escopo, tipo, posicao);
