# CLAUDE.md — ServidorIntegracaoSAP

Guia para agentes. Objetivo: achar o arquivo certo sem varrer o repo.
Regra de ouro: para a maioria das tarefas bastam **2 arquivos** (o módulo + seu teste).

## O que é

Serviço de integração SAP B1 → Supabase **e** Integração WBC → SAP. Roda em produção no
`192.168.7.11` (`C:\Python\ServidorIntegracaoSAP`) como 5 processos independentes:

- **API HTTP** (porta 8077, serviço NSSM `OrcaView-OS-API`) — gatilhos sob demanda + consultas
  + Painel de Sincronização (`GET /sincronizar`). `GET /` é a **entrada**: leva ao painel WBC
  (`web/entrada.html` sonda a porta e, se o painel não responde, mostra o caminho para `/sincronizar`).
- **Agendador** (serviço NSSM `OrcaView-Scheduler`) — carga periódica de oportunidades.
- **Fachada MCP** — stdio no cliente (`mcp/mcp_server.py`) ou HTTP na .11 (`mcp/serve_http.py`, porta 8078).
- **Painel WBC** (porta `PAINEL_PORTA`=8079, serviço `OrcaView-WBC-Painel`, FastAPI) — a
  **porta de entrada** das duas telas; `python -m wbcpython dashboard`.
- **Worker WBC** (serviço `OrcaView-WBC-Worker`) — `python -m wbcpython worker`: lê os
  orçamentos do WBC e cria/atualiza/cancela cotação e pedido no SAP (Service Layer) a cada
  `WORKER_INTERVAL_SECONDS`, dentro do expediente dele. **Escreve em PRODUÇÃO.**

O pacote `wbcpython/` (ex-projeto WBCPython, importado em 2026-09-08) tem guia próprio em
`docs/wbc/README.md` e as decisões em `docs/wbc/DECISOES.md`. Plano da integração:
`docs/PLANO_INTEGRACAO_WBCPYTHON.md`.

## Mapa do repositório (código-fonte = raiz, plano)

| Arquivo | Responsabilidade |
| --- | --- |
| `api.py` | Todas as rotas Flask + auth (X-API-Key) + rate-limit + entrypoint waitress |
| `config.py` | TODA a configuração: env vars, defaults, `Settings` (dataclass), `*_ready()` |
| `pipeline_core.py` | Núcleo compartilhado: `SupabaseLoader`, locks de arquivo, validação, retry |
| `extract_sap_to_supabase.py` | Pipeline OPORTUNIDADES (carga completa, agendada) |
| `extract_ordens_servico_engenharia.py` | Pipeline OS por N_PED (sob demanda) da view HANA consolidada `VW_OS_INTEGRACAO` → tabela única `vw_os_integracao` + `diagnosticar_nped` |
| `extract_vendas_bi.py` | Pipeline VENDAS BI (agendado, 15min): `VW_PEDIDO_ALTA` + `VW_FATO_FATURAMENTO` → agregados `bi_vendas_*` que o app desenha no modo Vendas do Dashboard |
| `monitoring.py` | `collect_status()` — checks SAP/SQL/Supabase/scheduler/windows_update/disco do `/status` |
| `windows_update.py` | Reboot pendente (winreg, ~0,2ms) + updates pendentes/último patch (COM via PowerShell, 3-30s → thread daemon + cache). O `monitoring.py` só o consulta no check `windows_update` |
| `ordens_producao_sl.py` | Escrita em SAP nº 1: status de Ordem de Produção via Service Layer (REST). Sessão + máquina de estados + allowlist. Nasce desligado (`OP_SL_ENABLED`) |
| `wbcpython/` | Escrita em SAP nº 2 (o worker). Pacote da Integração WBC → SAP: `domain/` (máquina de estados do SitCode, sem I/O), `application/processar.py` (o caso de uso), `infrastructure/{service_layer,wbc_sql,hana}/`, `tracking/` (SQLite de acompanhamento), `host/worker.py` (APScheduler + trava), `dashboard/` (painel FastAPI+HTMX), `cli.py`, `safety.py` (travas). Imports absolutos `wbcpython.*`; roda com `python -m wbcpython` na raiz |
| `sap_connection.py` | `SAPExtractor` (HANA via hdbcli) |
| `db_utils.py` | `read_dbapi_query` (28 linhas) |
| `feriados_br.py` | Feriados nacionais BR até 2030 (agendador pula) |
| `scripts/scheduled_execution.py` | Loop do agendador (APScheduler, janela 7-18, seg-sex) |
| `mcp/` | Fachada MCP fina e read-only sobre a API 8077 — NÃO fala com banco |
| `web/sincronizar.html` | Painel de Sincronização, servido em `GET /sincronizar` (link "⇄ Integração WBC" via `GET /painel-wbc`) |
| `web/entrada.html` | `GET /`: sonda o painel WBC e redireciona; fallback com o botão para `/sincronizar` |
| `tests/` | pytest; `test_<modulo>.py` espelha o módulo. `tests/wbc/` = suíte do pacote `wbcpython` (mesma árvore dele) |
| `docs/wbc/` | Docs do WBC: `README.md` (como rodar), `DECISOES.md`, `APRENDIZADOS.md`, `RISCOS_PRODUCAO.md`, `PROGRESS.md` (diário), `ai_spec/00_index.md` (onde a spec antiga mora hoje) |
| `sql/hana/` | View HANA `VW_INO_OPORTUNIDADE_INTEGRACAO` que o worker lê (DDL de referência) |
| `run_wbc_worker.bat` · `run_wbc_painel.bat` | Wrappers NSSM do worker e do painel (cwd = raiz, venv-ou-sistema, UTF-8) |

Dependências: `config` ← todos · `pipeline_core` ← extract_* e api · `api.py` orquestra e
importa os 2 pipelines (oportunidades + OS) · `mcp/` só chama HTTP (não importa nada da raiz).

## Tarefa → o que ler

| Tarefa | Ler |
| --- | --- |
| Endpoint HTTP (novo/alterar) | `api.py` + `tests/test_api.py` |
| Variável de ambiente / default | `config.py` + `.env.example` + `tests/test_config.py` |
| Lógica de extração/carga | o `extract_*.py` do pipeline + seu teste |
| Status de Ordem de Produção (escrita SAP) | `ordens_producao_sl.py` + `tests/test_ordens_producao_sl.py` (+ plano em `docs/PLANO_OP_STATUS.md`) |
| Check do `/status` | `monitoring.py` + `tests/test_monitoring.py` |
| Windows Update / reboot pendente | `windows_update.py` + `tests/test_windows_update.py` (+ plano em `../SAP_RDP/docs/PLANO_WINDOWS_UPDATE.md`) |
| Agendamento/janela/feriado | `scripts/scheduled_execution.py` + `feriados_br.py` |
| Tool MCP | `mcp/mcp_server.py` (+ `mcp/README.md` só p/ registro no cliente) |
| Schema/RLS Supabase | `sql/*.sql` (DDL de referência; NÃO roda automaticamente) |
| Regra de negócio da Integração WBC (SitCode, cotação × pedido, encerramento) | `wbcpython/domain/sitcode.py` + `tests/wbc/domain/` (+ `docs/wbc/DECISOES.md` pela busca do heading) |
| Ciclo do worker WBC (o que ele faz por orçamento) | `wbcpython/application/processar.py` + `wbcpython/host/worker.py` + `tests/wbc/test_processar.py` |
| Painel WBC (rota, fragmento, entrada com chave) | `wbcpython/dashboard/web.py` + `tests/wbc/dashboard/` (templates em `wbcpython/dashboard/templates/`) |
| Comando da CLI `wbcpython` | `wbcpython/cli.py` + `tests/wbc/test_cli.py` |
| Variável do WBC (`SL_*`, `HANA_*`, `WBC_SQL_*`, `WORKER_*`, `PAINEL_*`) | `wbcpython/config.py` + `.env.example` (bloco WBC) + `tests/wbc/test_config.py` |
| Check `wbc_worker` do `/status` | `monitoring.py` (`_wbc_worker_signal`) + `tests/test_monitoring.py` |

## NÃO reler (não é fonte, ou raramente muda)

- `CHANGELOG.md` (histórico longo) e `README.md` inteiro — no README, vá direto à seção pela busca do heading.
- `docs/wbc/PROGRESS.md` (89 KB de diário) e `docs/wbc/DECISOES.md` inteiro — só pela busca do heading.
- `exports/` (dados de cliente), `logs/`, `state/`, `.locks/` — runtime/gerados.
- `install_*.bat/.ps1`, `run_*.bat`, `maintenance/` — só para tarefas de deploy/operação.

## Gotchas (custam caro se ignorados)

- **Agendador roda como módulo**: `python -m scripts.scheduled_execution`. Rodar o
  script direto → `ModuleNotFoundError: scripts` → serviço PAUSED.
- **Entry de produção da API é `python api.py`** (sobe waitress + log em `logs/api.log`).
  `waitress-serve api:app` funciona mas NÃO configura o log em arquivo. Não renomear `app`.
- `/health` = liveness leve e aberto; `/status` = diagnóstico profundo aberto
  (`?checks=`, `?strict=1` → 503 se degradado). Demais rotas exigem `X-API-Key`.
- Escritas têm **rate-limit in-process** (`RATE_SYNC_OS_MAX`, `RATE_FORCE_OPORT_MAX`) e
  **locks**: `_sync_lock` (thread) p/ OS, `oportunidades_sync_lock` (arquivo, cross-process,
  409 se ocupado) p/ carga completa.
- **Duas coisas aqui mudam dado dentro do SAP, e as duas miram PRODUÇÃO** (`SBOALTAMIRAPROD`):
  `ordens_producao_sl.py` (status de OP) e o **worker do `wbcpython`** (cotação, pedido,
  oportunidade, `OrcDetalhe`). No worker a trava `WBC_BLOCK_PRODUCTION_WRITES` está
  **`false` de propósito** na .11 desde 2026-09-02 (`docs/wbc/DECISOES.md`, "Virada para
  produção"); a trava de somente-leitura do SQL Server do WBC (`wbcpython/safety.py`)
  **não tem chave** e não pode ganhar uma. Sobre `ordens_producao_sl.py`: três invariantes com teste cravando — não
  afrouxe nenhuma sem decisão explícita: (1) `OP_SL_ENABLED` **nasce `false`** (rollback em
  produção = uma linha no `.env` + restart); (2) `POST /ordens-producao/<n>/status` é
  **fail-closed** — sem `OS_API_KEY` responde **503**, ao contrário de todas as outras
  rotas, que caem abertas (as outras escrevem no Supabase, reversível; esta, não); (3)
  alvo == status atual devolve `ja_estava` **sem mandar PATCH** — a idempotência é do
  desenho, não do SAP. A allowlist `OP_STATUS_PERMITIDOS` é conferida **antes da rede** e
  descarta código desconhecido: nada que não seja `bopos*` conhecido vira corpo de PATCH.
  Cancelar e voltar para Planejada estão **fora de escopo** (decisão 2026-08-07).
- **Vendas BI: a medida de "Pedidos" é `SUM("VlrPedido")`, sem índice.** O modelo do
  Power BI tem uma coluna `valorXindice` (`VlrPedido * Indice_Pedido`) que **não** é a
  que o dashboard mostra — medido em 11/08/2026: agosto fecha em R$ 1.314.876,11 pelo
  bruto (igual ao PBI) e R$ 1.309.079,46 pelo índice. Trocar isso faz a tela do app
  divergir do Power BI **sem erro nenhum aparecer**. Idem faturamento: `SUM("Valor")` de
  `VW_FATO_FATURAMENTO`, sem `ValorAdiant`. Vendedor casa por **nome**
  (`OSLP.SlpName` = `app_profiles.slp_name`) — o `slp_code` dos dois lados **diverge**.
- **DocEntry ≠ DocNum na OP** (a OP 125060 é o DocEntry 126599). O default das rotas é
  DocNum (o número da tela); `?chave=docentry` troca. DocNum que casa com mais de uma
  ordem é **recusado** (409), nunca resolvido por `[0]`.
- **Sessão do Service Layer: nunca um login por request.** O SL tem teto de sessões
  concorrentes e vazá-las derruba o SL para todo mundo, cliente B1 incluído — por isso a
  sessão é compartilhada com TTL e a sessão **substituída sai pelo `/Logout`**.
  `ordens_producao_sl.py` é irmão de `web_orcaview_V117/backend/services/compras_sap_service.py`:
  **mantenha os dois diffáveis** (correção num vale para o outro).
- `config.get_settings()` é cacheado — testes usam `reset_settings()` após mexer em env.
- **Windows Update: "0 pendentes" MENTE se o agente não varre.** Não é erro tratável — a busca
  `IsInstalled=0` **responde** (3,1s aqui, 22,5s na .12), diz **0** e está errada, porque o cache
  de varredura do agente está vazio. A .12 ficou **610 dias sem patch** exatamente assim. Por isso
  `windows_update.py` só publica `pendentes` quando `LastSearchSuccessDate` (COM
  `Microsoft.Update.AutoUpdate`, 7-17ms) é recente; senão devolve **`None` + motivo**.
  **Nunca troque `None` por `0`.** Custos medidos: reboot pendente 0,2ms (winreg, independe do
  agente) · varredura 7-17ms · `Get-HotFix` ~1s · busca 3,1s aqui / 30s a frio — daí a thread
  daemon no `api.main()`: a busca estouraria o timeout de quem chama o `/status`.
  `LastInstallationSuccessDate` parece um `Get-HotFix` barato mas inclui **ruído do Defender**
  (divergiu nas duas máquinas) — não trocar. **`windows_update.py` é PORTE do módulo homônimo do
  repo SAP_RDP: mantenha os dois diffáveis** (bug corrigido aqui vai para lá, e vice-versa).
- **O bloco `windows_update` NUNCA gera alerta** — nem reboot pendente, nem update pendente.
  É **informação, não saúde do sistema** (decisão do Marcelo em 2026-07-16, revisando a D1 do
  plano: *"se um dia o servidor não reiniciar não importa"*). Alerta derruba `healthy` e faz o
  `?strict=1` responder **503**: era o único ponto em que este módulo mexeria no comportamento
  de quem monitora a integração, e ele fica fechado. **Não reintroduza** — há teste cravando
  (`test_windows_update_nunca_gera_alerta`). Esta máquina tem `AUOptions=4` (instala sozinha, e
  **é isso que a mantém em dia** — não "corrija" para 2, foi o que matou a .12).
- **Testes: NUNCA deixe a suíte ler o winreg real.** `_stub_all_ok` (tests/test_monitoring.py)
  stuba `_windows_update_signal` de propósito: **esta máquina TEM reboot pendente**, e sem o
  stub os testes passam a depender de um fato do ambiente, não do código.
- Scripts `.ps1` são ASCII **de propósito** (PowerShell 5.1/BOM). Não adicionar acentos. O
  PowerShell escreve o stdout em **cp850**, não UTF-8 (medido) — quem lê saída de PS force
  `[Console]::OutputEncoding` na 1ª linha do script, senão "Atualização" chega "Atualiza??o".
- Repo GitHub ainda se chama `oportunidade_wbc` (mantido de propósito); pasta local e
  prod já são `ServidorIntegracaoSAP`. Env vars/endpoints antigos (`OPORTUNIDADE_WBC_*`,
  `/api/oportunidade-wbc/status` no web) são funcionais — NÃO renomear.
- Deploy prod = `deploy_update.bat` na .11 (para os 5 serviços, `git pull --ff-only`, pip
  **no Python 3.12 do sistema** se `requirements*` mudou — não há venv lá — e religa; o
  worker WBC só religa se estava rodando). `requirements.txt` é a fonte de instalação
  (não migrar deps para pyproject sem decisão explícita). `pip`/restart são do Marcelo.
- **`wbcpython` roda como módulo, com cwd na raiz**: `python -m wbcpython <comando>`. Ele lê
  o `.env` do cwd (pydantic-settings) e resolve `state/wbc_tracking.db`, `logs/wbcpython.log`
  e `state/wbc_previsao.json` relativos ao cwd — os `run_wbc_*.bat` garantem isso. Não há
  `pip install -e`, hatchling nem uv: o pacote é uma pasta na raiz.
- **`OS_API_KEY` é compartilhada** pela API 8077 e pelo painel WBC (que a pede uma vez e
  guarda um cookie HMAC). Trocar a chave derruba os cookies de todo mundo — é o desenho.
  Sem `OS_API_KEY` o painel fica aberto, como a API (fail-open documentado).
- **`?checks=wbc` é o SQL Server, não o worker** — o alias existia antes do worker e
  monitores usam. O worker é `wbc_worker` (aliases `worker`, `integracao_wbc`). O check
  **não alarma** antes do primeiro ciclo registrado na máquina (`installed=false` /
  `last=null` são informação): na .11 antes da virada, `/status?strict=1` continua 200.
- **A tarefa legada "Integração WBC" foi desativada em 2026-09-08** (virada para o worker).
  O check `scheduled_task` **fica** — o card do `.90` (`status.js`) e a tool MCP
  `estado_tarefa_wbc` leem o bloco — mas nasce `retired=true`, `available=false`, sem alerta
  (`WBC_TASK_MONITOR_DEFAULT = False`). Não apague o bloco nem `monitor_wbc_task.ps1`:
  `WBC_TASK_MONITOR=true` é o rollback. A tarefa `OrcaView-Monitor-WBC-Task` do Task
  Scheduler pode ser removida (`Unregister-ScheduledTask`); com o monitor desligado o JSON
  dela é ignorado.
- **`tests/wbc/conftest.py` neutraliza o `.env`**: o `load_dotenv()` do `config` da raiz
  vaza o `.env` para o `os.environ` da sessão inteira; sem a fixture, `OS_API_KEY` do `.env`
  faria o painel exigir chave em todo teste. Teste que precisa de um valor faz `setenv` depois.
- **Nunca `git subtree add` do repo antigo `MCPs\WBCPython`**: o histórico dele versiona um
  `.env.bak` com senha e este repo é público. O import de 2026-09-08 foi por cópia, sem
  histórico, de propósito.
- **Parada limpa do worker**: NSSM manda Ctrl+C, o worker termina o ciclo (9–14 s) e sai; o
  serviço tem `AppStopMethodConsole 60000`. Não reduzir: matar no meio de um POST no SAP
  deixa cotação criada sem vínculo (já aconteceu por outro motivo — `docs/wbc/RETOMADA.md`).

## Comandos

```bash
python -m pytest              # suíte completa (SIS + WBC; rápida, sem rede)
python -m pytest tests/wbc    # só a suíte do WBC (843 testes; --run-integration liga os de rede)
python -m ruff check .        # lint (config no pyproject.toml; deve ficar em 0)
python api.py                 # sobe a API local (porta 8077)
python -m scripts.scheduled_execution   # agendador (loop; Ctrl+C p/ sair)
python -m wbcpython --help    # CLI do WBC: env, doctor, check-sap, check-hana, pendentes, ciclo, worker, dashboard, pesos
python -m wbcpython pendentes --exportar state/wbc_previsao.json   # o que o ciclo FARIA (só leitura)
python -m wbcpython faxina    # apaga decisões mais velhas que EVENTOS_RETENCAO_DIAS (o worker faz 1x/dia)
python -m wbcpython dashboard # painel WBC (PAINEL_HOST/PAINEL_PORTA do .env)
```

Tooling em `pyproject.toml` (pytest + ruff). Dependências de runtime seguem em
`requirements.txt` — não migrar para `[project]` (mudaria o deploy).
