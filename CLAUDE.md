# CLAUDE.md — ServidorIntegracaoSAP

Guia para agentes. Objetivo: achar o arquivo certo sem varrer o repo.
Regra de ouro: para a maioria das tarefas bastam **2 arquivos** (o módulo + seu teste).

## O que é

Serviço de integração SAP B1 → Supabase. Roda em produção no `192.168.7.11`
(`C:\Python\ServidorIntegracaoSAP`) como 3 processos independentes:

- **API HTTP** (porta 8077, serviço NSSM `OrcaView-OS-API`) — gatilhos sob demanda + consultas.
- **Agendador** (serviço NSSM `OrcaView-Scheduler`) — carga periódica de oportunidades.
- **Fachada MCP** — stdio no cliente (`mcp/mcp_server.py`) ou HTTP na .11 (`mcp/serve_http.py`, porta 8078).

## Mapa do repositório (código-fonte = raiz, plano)

| Arquivo | Responsabilidade |
| --- | --- |
| `api.py` | Todas as rotas Flask + auth (X-API-Key) + rate-limit + entrypoint waitress |
| `config.py` | TODA a configuração: env vars, defaults, `Settings` (dataclass), `*_ready()` |
| `pipeline_core.py` | Núcleo compartilhado: `SupabaseLoader`, locks de arquivo, validação, retry |
| `extract_sap_to_supabase.py` | Pipeline OPORTUNIDADES (carga completa, agendada) |
| `extract_ordens_servico_engenharia.py` | Pipeline OS por N_PED (sob demanda) da view HANA consolidada `VW_OS_INTEGRACAO` → tabela única `vw_os_integracao` + `diagnosticar_nped` |
| `monitoring.py` | `collect_status()` — checks SAP/SQL/Supabase/scheduler/windows_update/disco do `/status` |
| `windows_update.py` | Reboot pendente (winreg, ~0,2ms) + updates pendentes/último patch (COM via PowerShell, 3-30s → thread daemon + cache). O `monitoring.py` só o consulta no check `windows_update` |
| `sap_connection.py` | `SAPExtractor` (HANA via hdbcli) |
| `db_utils.py` | `read_dbapi_query` (28 linhas) |
| `feriados_br.py` | Feriados nacionais BR até 2030 (agendador pula) |
| `scripts/scheduled_execution.py` | Loop do agendador (APScheduler, janela 7-18, seg-sex) |
| `mcp/` | Fachada MCP fina e read-only sobre a API 8077 — NÃO fala com banco |
| `web/sincronizar.html` | Página única servida em `GET /` |
| `tests/` | pytest, 273 testes; `test_<modulo>.py` espelha o módulo |

Dependências: `config` ← todos · `pipeline_core` ← extract_* e api · `api.py` orquestra e
importa os 2 pipelines (oportunidades + OS) · `mcp/` só chama HTTP (não importa nada da raiz).

## Tarefa → o que ler

| Tarefa | Ler |
| --- | --- |
| Endpoint HTTP (novo/alterar) | `api.py` + `tests/test_api.py` |
| Variável de ambiente / default | `config.py` + `.env.example` + `tests/test_config.py` |
| Lógica de extração/carga | o `extract_*.py` do pipeline + seu teste |
| Check do `/status` | `monitoring.py` + `tests/test_monitoring.py` |
| Windows Update / reboot pendente | `windows_update.py` + `tests/test_windows_update.py` (+ plano em `../SAP_RDP/docs/PLANO_WINDOWS_UPDATE.md`) |
| Agendamento/janela/feriado | `scripts/scheduled_execution.py` + `feriados_br.py` |
| Tool MCP | `mcp/mcp_server.py` (+ `mcp/README.md` só p/ registro no cliente) |
| Schema/RLS Supabase | `sql/*.sql` (DDL de referência; NÃO roda automaticamente) |

## NÃO reler (não é fonte, ou raramente muda)

- `CHANGELOG.md` (455 linhas de histórico) e `README.md` inteiro — no README, vá direto à seção pela busca do heading.
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
- Deploy prod = `git pull` na .11 + restart dos serviços NSSM. `requirements.txt` é a
  fonte de instalação (não migrar deps para pyproject sem decisão explícita).

## Comandos

```bash
python -m pytest              # suíte completa (rápida, sem rede)
python -m ruff check .        # lint (config no pyproject.toml; deve ficar em 0)
python api.py                 # sobe a API local (porta 8077)
python -m scripts.scheduled_execution   # agendador (loop; Ctrl+C p/ sair)
```

Tooling em `pyproject.toml` (pytest + ruff). Dependências de runtime seguem em
`requirements.txt` — não migrar para `[project]` (mudaria o deploy).
