# Changelog

Mudanças notáveis deste projeto. Formato inspirado em
[Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/).

## [2026-06-25] — API de disparo + endurecimento (pós-revisão)

### Adicionado

- **API HTTP** (`api.py`, Flask) para o app disparar a sync por NPED:
  `POST /sync/ordens-servico/<nped>`, `POST /sync/ordens-servico` (corpo
  `{"nped": N}` ou `{"npeds": [...]}`) e `GET /health`. Autenticação **opcional** por
  `OS_API_KEY` (header `X-API-Key` ou `Authorization: Bearer`); cargas **serializadas**
  por lock; respostas `200/207/400/401/502`. Config `OS_API_*`; `flask`/`waitress` no
  `requirements.txt`; testes em `tests/test_api.py`.
- **`pipeline_core.coerce_positive_int`** (regex `^\d+$` + `> 0`) — validação de NPED
  reutilizada por `extract`/`export`/`api` (rejeita negativo, zero, sinal e decimal).
  Testes em `tests/test_pipeline_core.py`.

### Alterado

- Logger `httpx` rebaixado para `WARNING` nos entrypoints de OS (logs de produção
  sem a URL gigante por requisição).
- `export_os_json` valida os NPEDs antes de filtrar no PostgREST.

Total da suíte: **106 testes passando**.

## [2026-06-25] — Ordens de Serviço de Engenharia

Novo pipeline **independente** do de oportunidades: sincroniza Ordens de Serviço do
SAP para o Supabase **sob demanda, por `NPED`**, e exporta para JSON. Reaproveita o
"motor" do projeto via um núcleo compartilhado, sem alterar o comportamento do
pipeline de oportunidades.

### Adicionado

- **`extract_ordens_servico_engenharia.py`** — sincroniza a view SAP
  `VW_EXPORT_ORDENS_SERVICO_1` por `NPED` (um ou vários), no modo **`replace_nped`**
  (carrega-depois-poda **escopado ao pedido**: não duplica e não toca nos demais).
  Sem enriquecimento SQL Server / sem `SITCOD`.
- **`pipeline_core.py`** — núcleo genérico extraído de `extract_sap_to_supabase.py`
  (`SupabaseLoader`, `prepare_data`, `with_retries`, `build_view_query`,
  `validate_sql_identifier`). `SupabaseLoader.delete_other_executions` ganhou
  `where_eq=` (poda escopada) e `registrar_sincronizacao` ganhou `extra_fields=`.
- **`export_os_json.py`** — exporta a tabela já sincronizada para JSON (por `NPED`
  ou `--all`), com `--slim` (remove textos NCLOB), `--compact`, `--array`,
  `--no-status`, `-o/--output` e `--stdout`. Adiciona `status_desc`; grava UTF-8 legível.
- **`sql/ordens_servico_engenharia.sql`** — DDL idempotente das 3 tabelas
  (`ordens_servico_engenharia`, lookup `status_ordens_servico_eng`, log
  `sincronizacao_log_os_eng`), já com o seed do status.
- **Configuração `OS_*`** em `config.py` / `.env.example` (view, tabela, lookup, log,
  modo, batch).
- **Testes** sem credenciais: `tests/test_ordens_servico_eng.py`,
  `tests/test_export_os_json.py` (total da suíte: **75 passando**).
- **Documentação**: `GUIA_ORDENS_SERVICO_ENGENHARIA.md` (didático, com exemplos e
  saídas reais), `PLANO_SYNC_ORDENS_SERVICO.md` (decisões de projeto) e nova seção no
  `README.md`.

### Alterado

- **`extract_sap_to_supabase.py`** passou a **importar** o núcleo de `pipeline_core.py`
  em vez de definir as funções localmente — mudança *behavior-preserving* (a API
  pública `from extract_sap_to_supabase import ...` continua funcionando).
- **`.gitignore`** ignora `exports/` (os JSONs exportados contêm dados de cliente).

### Segurança

- As 3 tabelas usam **RLS `ENABLE` + `FORCE` sem nenhuma policy** → acesso **somente**
  via `service_role` (backend). `anon`/`authenticated` ficam sem acesso. Decisão e
  racional (lições do Security Advisor: evita *RLS Policy Always True*, *Extension in
  Public*, *SECURITY DEFINER*) documentados em `PLANO_SYNC_ORDENS_SERVICO.md §5.5`.
