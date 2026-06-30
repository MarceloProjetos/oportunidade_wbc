# Changelog

Mudanças notáveis deste projeto. Formato inspirado em
[Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/).

## [2026-06-30] — Monitoramento via `/status` + botão limpar

### Adicionado

- **Endpoint `GET /status`** (sob demanda, requer `X-API-Key`) — diagnóstico do servidor e
  dependências **sem polling** (roda só quando chamado). Lógica em `monitoring.py`. Checa
  **SAP**, **SQL Server (WBC)** e **Supabase** com latência (`ms`) por item; traz **sistema**
  (host/IP/SO/Python/uptime/disco e CPU+memória via `psutil`, se instalado). O `/health`
  segue **leve** (liveness) de propósito — não faz checagem externa.
  - **Sinal indireto do agendador** (`scheduler`): idade da última carga de oportunidades
    (lida do log); `stale=true` se passar de **35 min dentro da janela comercial** (dia útil
    07–18h) — indica possível queda do `OrcaView-Scheduler`.
  - **Alerta de disco** da unidade do app (`disk_low`) + lista legível em `alerts`.
  - **`?checks=sap,sql`** roda só as checagens escolhidas (`sap`, `sql`/`sql_server`,
    `supabase`, `scheduler`/`agendador`); **`?strict=1`** devolve **HTTP 503** se houver
    falha de conexão **ou** alerta (p/ monitores por código de status).
  - `psutil` adicionado ao `requirements.txt` (opcional — sem ele, o `/status` funciona e
    CPU/memória ficam indisponíveis).
- **Botão "🗑 limpar"** ao lado do campo Nº do pedido (NPED), para esvaziar a lista.

### Documentação

- README: nova seção **Monitoramento** (`/health` e `/status` com as flags `?checks=` e
  `?strict=1`) + entrada no índice.

## [2026-06-29] — Painel: "Buscar na Lista" + chave de acesso recolhível

### Adicionado

- **Botão "📋 Buscar na Lista"** na coluna de Ordens de Serviço: abre um modal com até
  **30 pedidos com OS criada** no SAP (`NPED · cliente · nº OS · data`), com seleção
  múltipla ("selecionar todos") e **↻ atualizar**. Os escolhidos entram no campo NPED
  **separados por vírgula**, somando sem duplicar ao que já estiver lá.
  - Backend: função `listar_pedidos_com_os(limit=30)` (query em `OWOR` LEFT JOIN `ORDR`
    p/ o `CardName`; `OriginNum > 0 AND Status <> 'C'` exclui OS canceladas; mais recentes
    primeiro por `MAX(DocEntry)`) + endpoint `GET /ordens-servico/disponiveis` (exige `X-API-Key`).
- **Cartão da chave de acesso recolhível**: cadeado (🔒/🔓) no canto direito do cabeçalho
  mostra/oculta o card. **Oculto por padrão** ao carregar; acessível
  (`aria-expanded`/`aria-controls`). "Buscar na Lista" sem chave abre o card automaticamente.

## [2026-06-29] — Faxina: remoção de planos, docs e deploy não usado

### Removido

- **Docs de planejamento** de features já concluídas: `PLANO_PAINEL_OPORTUNIDADES.md` e
  `PLANO_SYNC_ORDENS_SERVICO.md` (links no README/GUIA ajustados).
- **Suporte Docker** (deploy real é NSSM/Windows): `Dockerfile`, `docker-compose.yml`,
  `.dockerignore` + badge/TOC/seção no README.
- **`run_all.bat`** — launcher manual que brigava pela porta 8077 com os serviços NSSM.
  Use `install_services.bat`; ou, para teste, `run_scheduler.bat`/`run_api.bat` isolados.
- **`scripts/test_connections.py` + `setup.sh`** — diagnóstico/setup que executava conexões
  **reais** durante o `pytest`. Suíte agora roda **113 testes**, sem chamadas externas.
- Bytecode órfão em `__pycache__` (`exemplo_avancado`, `pandas_guide` — `.py` já inexistentes).

## [2026-06-29] — Log deixa de ser configurado no import

### Corrigido

- **Logging só no entrypoint, não no import.** `scheduled_execution.py`, `api.py` e os dois
  `extract_*.py` chamavam `logging.basicConfig` no nível do módulo — então **importar** o
  módulo (ex.: na suíte de testes) redirecionava todo o log da sessão para o arquivo de
  produção (`logs/scheduled_execution.log` / `logs/api.log`). A configuração foi movida para
  uma função `_configure_logging()` chamada só no `main`/`__main__`. Verificado: rodar a
  suíte completa não escreve mais nos logs de produção. (Nota: servir via
  `waitress-serve api:app` não passa por `main()` — use `python api.py` para ter o log em
  arquivo.)

### Documentação

- README: nota para ler os logs no **PowerShell** com `-Encoding utf8` (são UTF-8; sem isso
  os acentos saem trocados, ex.: `execuÃ§Ã£o`).

## [2026-06-29] — Limpeza: config morta + dedupe de helpers

### Removido

- **`DIAS_SEMANA`** (env/config) — era validado mas **nunca aplicado**: o agendador
  decide rodar só por `is_business_day` (seg–sex menos feriados BR), então `DIAS_SEMANA`
  não restringia nada (config enganosa). Removidos `parse_dias_semana`, `DIAS_SEMANA_RE`,
  `DOW_CRON`, `DIAS_SEMANA_DEFAULT`, o campo `Settings.dias_semana` e as menções em
  `.env.example`, `README.md` e `docker-compose.yml`. A regra continua a mesma:
  **dias úteis (seg–sex, sem feriados nacionais)**.
- **Aliases bilíngues duplicados** em `scripts/scheduled_execution.py`
  (`esta_na_janela_comercial`, `pode_executar_carga`, `_parse_dias_semana` e o parâmetro
  `dias_semana` descartado) — ficam só as funções canônicas `is_within_commercial_window`
  e `can_run_load`. Testes ajustados para os nomes canônicos.

## [2026-06-29] — Correção: agendador não subia (ModuleNotFoundError)

### Corrigido

- **`run_scheduler.bat`** passa a iniciar o agendador como módulo
  (`python -m scripts.scheduled_execution`) em vez de `python scripts\scheduled_execution.py`.
  Rodar o arquivo direto colocava apenas a pasta `scripts\` no `sys.path`, então a 1ª linha
  `import scripts._bootstrap` quebrava com `ModuleNotFoundError: No module named 'scripts'` —
  o processo morria na largada. Como serviço NSSM isso aparecia como `SERVICE_PAUSED` e, na
  prática, **o sincronismo agendado de oportunidades (a cada 30 min, 07–18h, dias úteis) nunca
  rodava**; só o "Forçar sincronismo" (via API, sem apscheduler) funcionava. A API não era
  afetada porque `api.py` fica na raiz do projeto.

## [2026-06-26] — Painel unificado + Oportunidades

### Adicionado

- **Painel unificado** em `GET /` (2 colunas): **Ordens de Serviço** (por NPED, sob demanda)
  e **Oportunidades** (carga completa agendada). Chave única compartilhada na página.
- **Endpoints de oportunidades** (`api.py`): `GET/DELETE /oportunidades/historico` (lê/limpa
  o `sincronizacao_log`), `GET /oportunidades/info` (total de linhas na tabela + agenda
  intervalo/janela) e `POST /oportunidades/sincronizar` (**força** a carga completa — a
  mesma do agendador). Todos exigem `X-API-Key`. A coluna de Oportunidades mostra
  "📊 N linhas · 🕑 a cada 30 min · 07–18h · dias úteis".
- **Lock de arquivo cross-process** (`pipeline_core.oportunidades_sync_lock`, lib `filelock`)
  compartilhado entre o agendador (`scripts/scheduled_execution.py`) e o "forçar sincronismo"
  da API: nunca rodam duas cargas snapshot de oportunidades ao mesmo tempo (a 2ª recebe `409`
  / o agendado pula). Dependência `filelock` no `requirements.txt`; `.locks/` no `.gitignore`.
- **`run_all.bat`** — launcher único que sobe `run_scheduler.bat` + `run_api.bat`.
- **`install_services.bat`** — registra agendador + API como **serviços NSSM** (auto-start
  no boot, restart automático se cair, log em arquivo com rotação). Para o servidor não
  depender de janela manual / sobreviver a reboot.
- **Log em arquivo na API** (`logs/api.log`, `TimedRotatingFileHandler`, rotação diária,
  12 dias) além do console — o log persiste ao fechar a janela / rodar como serviço.

### Alterado

- `_fetch_log`/`_clear_log` da API passam a receber o **nome da tabela** (servem OS e
  oportunidades). Página reescrita (layout de 2 colunas, mantendo toda a função de OS).

Plano e decisões: `PLANO_PAINEL_OPORTUNIDADES.md`. Suíte: **123 testes**.

## [2026-06-25] — API de disparo + endurecimento (pós-revisão)

### Adicionado

- **API HTTP** (`api.py`, Flask) para o app disparar a sync por NPED:
  `POST /sync/ordens-servico/<nped>`, `POST /sync/ordens-servico` (corpo
  `{"nped": N}` ou `{"npeds": [...]}`) e `GET /health`. Autenticação **opcional** por
  `OS_API_KEY` (header `X-API-Key` ou `Authorization: Bearer`); cargas **serializadas**
  por lock; respostas `200/207/400/401/502`. Config `OS_API_*`; `flask`/`waitress` no
  `requirements.txt`; testes em `tests/test_api.py`.
- **`run_api.bat`** — wrapper para subir a API no boot do servidor (Task Scheduler
  ONSTART / NSSM), espelhando o `run_scheduler.bat`. Instruções no README e no GUIA §6.1.
- **Página amigável** (`web/sincronizar.html`) servida em `GET /`: campo do nº do pedido +
  chave (com "lembrar") + botão **Sincronizar** (aceita vários pedidos, mostra resultado).
  Sem dependências, same-origin (sem CORS). Rota `GET /favicon.ico` → 204.
- **Histórico das últimas sincronizações**: endpoint `GET /historico` (lê a tabela de log
  via service_role; requer `X-API-Key`; `?limit=N`, default 20, máx 100) e seção
  "Últimas sincronizações" na página (atualiza após cada disparo e tem botão ↻).
  **Limpar histórico**: endpoint `DELETE /historico` (apaga o log; requer `X-API-Key`) +
  botão 🗑 na página (com confirmação).
- **Diagnóstico "OS não gerada" / "OS cancelada"**: `diagnosticar_nped` consulta a `OWOR`
  (`OriginNum` = nº do pedido) **antes** de sincronizar. Sem OP → `tipo: "sem_os"`; todas
  as OPs com `Status='C'` → `tipo: "cancelada"`. Nesses casos a API **não** tenta a carga
  (sem log de falha) e a página mostra selo âmbar **SEM OS** / **CANCELADA**. O batch passa
  a responder `200`/`207` (sem `502`).
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
