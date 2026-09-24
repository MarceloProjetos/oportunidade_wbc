# CLAUDE.md — ServidorIntegracaoSAP

Guia para agentes. Objetivo: achar o arquivo certo sem varrer o repo.
Regra de ouro: para a maioria das tarefas bastam **2 arquivos** (o módulo + seu teste).
O "porquê" histórico das regras abaixo (datas, medições, incidentes) está em
`docs/INCIDENTES.md` — só abra quando a regra não bastar.

## O que é

Serviço de integração SAP B1 → Supabase **e** Integração WBC → SAP. Roda em produção no
`192.168.7.11` (`C:\Python\ServidorIntegracaoSAP`, **Python 3.14.7** do sistema em
`C:\Program Files\Python314`, sem venv) como 5 serviços NSSM:

- **API HTTP** (porta 8077, `OrcaView-OS-API`) — gatilhos sob demanda + consultas + Painel de
  Sincronização (`GET /sincronizar`). `GET /` é a **entrada**: leva ao painel WBC
  (`web/entrada.html` sonda a porta e, se o painel não responde, mostra o caminho para `/sincronizar`).
- **Agendador** (`OrcaView-Scheduler`) — 4 jobs: oportunidades (intervalo, janela útil),
  Vendas BI a cada 15 min na janela, Vendas BI de hora em hora 24/7 fora dela, espelho de
  orçamentos (janela útil).
- **Fachada MCP** (`OrcaView-MCP`) — stdio no cliente (`mcp/mcp_server.py`) ou HTTP na .11
  (`mcp/serve_http.py`, porta 8078).
- **Painel WBC** (porta `PAINEL_PORTA`=8079, `OrcaView-WBC-Painel`, FastAPI) — a **porta de
  entrada** das duas telas; `python -m wbcpython dashboard`.
- **Worker WBC** (`OrcaView-WBC-Worker`) — `python -m wbcpython worker`: lê os orçamentos do
  WBC e cria/atualiza/cancela cotação e pedido no SAP (Service Layer) a cada
  `WORKER_INTERVAL_SECONDS`, dentro do expediente dele. **Escreve em PRODUÇÃO.**

O pacote `wbcpython/` (ex-projeto WBCPython, importado em 2026-09-08) tem guia próprio em
`docs/wbc/README.md` e as decisões em `docs/wbc/DECISOES.md`. Plano da integração:
`docs/PLANO_INTEGRACAO_WBCPYTHON.md`.

## Mapa do repositório (código-fonte = raiz, plano)

| Arquivo | Responsabilidade |
| --- | --- |
| `api.py` | Todas as rotas Flask + auth (X-API-Key) + rate-limit + entrypoint waitress |
| `config.py` | Configuração: env vars, defaults, `Settings` (dataclass), `*_ready()`. Exceção: `RATE_*` e `SYNC_LOTE_MAX` são lidos no topo do `api.py` |
| `pipeline_core.py` | Núcleo compartilhado: `SupabaseLoader`, locks de arquivo, validação, retry |
| `extract_sap_to_supabase.py` | Pipeline OPORTUNIDADES (carga completa, agendada) |
| `extract_ordens_servico_engenharia.py` | Pipeline OS por N_PED (sob demanda) da view HANA consolidada `VW_OS_INTEGRACAO` → tabela única `vw_os_integracao` + `diagnosticar_nped` + `consultar_status_pedido` (ORDR) |
| `extract_vendas_bi.py` | Pipeline VENDAS BI (agendado): `VW_PEDIDO_ALTA` + `VW_FATO_FATURAMENTO` → agregados `bi_vendas_*` que o app desenha no modo Vendas do Dashboard |
| `extract_orcamentos_espelho.py` | Espelho de `VW_EVOL_ORCAMENTO_ALT` no Supabase (agendado) |
| `situacao_pedidos.py` | `/pedidos/situacao`: regra pura, **porte** do `situacao_pedidos_service.py` do web — diffável, com teste (`test_situacao_pedidos_diffavel.py`) |
| `situacao_pedidos_hana.py` | Leitura HANA + cache + endereço de entrega das rotas `/pedidos/*` |
| `sap_montagem_labels.py` | Rótulos de montagem, **porte** do web (mesmo teste de diff) |
| `pedidos_bloqueados.py` | Lista **hardcode** de pedidos fora dos agregados (84337). **3 cópias**: aqui, `web_orcaview_V118/backend/services/pedidos_bloqueados.py` e `mobile_orcaview_V4/lib/pedidos/bloqueados.ts` — acrescentar exige os três |
| `monitoring.py` | `collect_status()` — checks do `/status` (`SELECTABLE_CHECKS`: sap, sql_server, supabase, scheduler, scheduled_task, wbc_worker, windows_update; disco vem sempre). Check novo = `SELECTABLE_CHECKS` + despacho + alerta + saída + `_CHECK_ALIASES` do `api.py` |
| `windows_update.py` | Reboot pendente (winreg) + updates pendentes/último patch (COM via PowerShell, thread daemon + cache). **Porte** do homônimo do repo SAP_RDP — diffável |
| `ordens_producao_sl.py` | Escrita em SAP nº 1: status de Ordem de Produção via Service Layer (REST). Nasce desligado (`OP_SL_ENABLED`). Irmão diffável de `web_orcaview_V118/backend/services/compras_sap_service.py` |
| `wbcpython/` | Escrita em SAP nº 2 (o worker). `domain/` (máquina de estados do SitCode, sem I/O), `application/processar.py` (o caso de uso), `infrastructure/{service_layer,wbc_sql,hana}/`, `tracking/` (SQLite de acompanhamento), `host/worker.py` (APScheduler + trava), `dashboard/` (painel FastAPI+HTMX), `cli.py`, `safety.py` (travas). Imports absolutos `wbcpython.*` |
| `sap_connection.py` · `db_utils.py` · `retry.py` | `SAPExtractor` (HANA via hdbcli) · `read_dbapi_query` · retry compartilhado |
| `feriados_br.py` | Feriados nacionais BR até 2030 (o agendador pula) |
| `wake_altservidor_ia.py` | Wake-on-LAN do `.90` (tarefa `OrcaView-WOL-AltservidorIA`, `install_wol_task.ps1`). **Byte-idêntico** a `web_orcaview_V118/tools/wake_altservidor_ia.py`, stdlib-only, Python 3.8+ |
| `scripts/scheduled_execution.py` | Loop do agendador (APScheduler, janela 7-18, seg-sex) |
| `mcp/` | Fachada MCP fina e read-only sobre a API 8077 — NÃO fala com banco |
| `web/sincronizar.html` · `web/entrada.html` | Painel de Sincronização (`GET /sincronizar`) · `GET /` (sonda o painel WBC e redireciona) |
| `tests/` | pytest; `test_<modulo>.py` espelha o módulo. `tests/wbc/` = suíte do pacote `wbcpython` (mesma árvore dele) |
| `docs/` | `PLANO_*.md` abertos (encerrados em `arquivo/`); `wbc/` (README, DECISOES, APRENDIZADOS, RISCOS_PRODUCAO, RETOMADA); `INCIDENTES.md`; `changelog/` (meses anteriores) |
| `API_*.md` (raiz) | Contratos HTTP entregues a outras equipes (OS, OP, RH, situação de pedidos). **Ficam na raiz**: repo público, links externos |
| `sql/` | DDL de referência (NÃO roda automaticamente); `sql/hana/` = view que o worker lê; `sql/migracoes/` = alterações já aplicadas |
| `maintenance/` | Conferidor de Vendas BI e scripts de disco/log do servidor |

Dependências: `config` ← todos · `pipeline_core` ← extract_* e api · `api.py` orquestra
(importa os pipelines, `situacao_pedidos*`, `ordens_producao_sl`, `windows_update`,
`monitoring`, `feriados_br`) · `mcp/` só chama HTTP (não importa nada da raiz).

## Tarefa → o que ler

| Tarefa | Ler |
| --- | --- |
| Endpoint HTTP (novo/alterar) | `api.py` (ache a rota por grep) + `tests/test_api.py` |
| Variável de ambiente / default | `config.py` + `.env.example` + `tests/test_config.py` |
| Lógica de extração/carga | o `extract_*.py` do pipeline + seu teste |
| Vendas BI | `extract_vendas_bi.py` + `tests/test_extract_vendas_bi.py` (+ `maintenance/conferir_vendas_bi.py`) |
| Situação de pedidos (`/pedidos/*`) | `situacao_pedidos.py` + `situacao_pedidos_hana.py` + `tests/test_api_situacao_pedidos.py` |
| RH (`/rh/colaboradores`) e `/usuarios-ativos` | `api.py` (bloco RH no fim do arquivo) + `tests/test_api.py` + `API_RH_COLABORADORES.md` |
| Status de Ordem de Produção (escrita SAP) | `ordens_producao_sl.py` + `tests/test_ordens_producao_sl.py` (+ `docs/PLANO_OP_STATUS.md`) |
| Check do `/status` | `monitoring.py` + `tests/test_monitoring.py` |
| Windows Update / reboot pendente | `windows_update.py` + `tests/test_windows_update.py` |
| Agendamento/janela/feriado | `scripts/scheduled_execution.py` + `feriados_br.py` |
| Tool MCP | `mcp/mcp_server.py` (+ `mcp/README.md` só p/ registro no cliente) |
| Schema/RLS Supabase | `sql/*.sql` (DDL de referência) |
| Regra de negócio da Integração WBC (SitCode, cotação × pedido, encerramento) | `wbcpython/domain/sitcode.py` + `tests/wbc/domain/` (+ `docs/wbc/DECISOES.md` pela busca do heading) |
| Ciclo do worker WBC | `wbcpython/application/processar.py` + `wbcpython/host/worker.py` + `tests/wbc/test_processar.py` |
| Painel WBC (rota, fragmento, entrada com chave) | `wbcpython/dashboard/web.py` + `tests/wbc/dashboard/` (templates em `wbcpython/dashboard/templates/`) |
| Comando da CLI `wbcpython` | `wbcpython/cli.py` + `tests/wbc/test_cli.py` |
| Variável do WBC (`SL_*`, `HANA_*`, `WBC_SQL_*`, `WORKER_*`, `PAINEL_*`) | `wbcpython/config.py` + `.env.example` (bloco WBC) + `tests/wbc/test_config.py` + `tests/test_config_paridade_wbc.py` |
| Check `wbc_worker` do `/status` | `monitoring.py` (`_wbc_worker_signal`) + `tests/test_monitoring.py` |

## NÃO reler (não é fonte, ou raramente muda)

- `CHANGELOG.md` (só o mês corrente; os anteriores em `docs/changelog/AAAA-MM.md`) e `README.md`
  inteiros — no README, vá direto à seção pela busca do heading.
- `docs/wbc/DECISOES.md` inteiro — tem **índice no topo**; vá pela busca do título.
- `docs/arquivo/` (planos encerrados) e `sql/migracoes/` (DDL já aplicado).
- `exports/` (dados de cliente), `logs/`, `state/`, `.locks/` — runtime/gerados.
- `install_*.bat/.ps1`, `run_*.bat`, `maintenance/` — só para tarefas de deploy/operação.

## Convenções

- **Comentários e docstrings em português** (decisão de 24/09/2026). Os 11 módulos da raiz
  traduzidos para inglês em jul/2026 ficam como estão; código novo e trechos reescritos, em PT.
  Identificadores, logs e mensagens HTTP já são PT. Os `.ps1` são **ASCII de propósito**
  (PowerShell 5.1/BOM) — sem acentos; e o PowerShell escreve o stdout em **cp850**: quem lê
  saída de PS força `[Console]::OutputEncoding` na 1ª linha do script.
- **Arquivos-irmãos de outro repo não se reescrevem de um lado só** (nem `ruff --fix`):
  `ordens_producao_sl.py`, `windows_update.py`, `situacao_pedidos.py`,
  `sap_montagem_labels.py`, `wake_altservidor_ia.py`, `pedidos_bloqueados.py`.
- Repo GitHub ainda se chama `oportunidade_wbc` (de propósito); env vars/endpoints antigos
  (`OPORTUNIDADE_WBC_*`, `/api/oportunidade-wbc/status` no web) são funcionais — NÃO renomear.

## Gotchas (custam caro se ignorados)

- **Agendador roda como módulo**: `python -m scripts.scheduled_execution`. Rodar o script
  direto → `ModuleNotFoundError: scripts` → serviço PAUSED.
- **Entry de produção da API é `python api.py`** (sobe waitress + log em `logs/api.log`).
  `waitress-serve api:app` funciona mas NÃO configura o log em arquivo. Não renomear `app`.
- `/health` = liveness leve e aberto. `/status` tem **dois níveis**: sem credencial, a visão
  mínima (`_status_publico`); o completo pede `OS_API_KEY` **ou** `STATUS_ID` (baixo
  privilégio — `_autorizado()` **não** o aceita nas outras rotas). **O código HTTP não
  depende da credencial** (o watchdog do `.90` decide por ele). `collect_status` /
  `SELECTABLE_CHECKS` são contrato entre repos: a redução mora no `api.py`. `?checks=`,
  `?strict=1` → 503 se degradado; as demais rotas exigem `X-API-Key`.
- Escritas têm **rate-limit in-process** (`RATE_SYNC_OS_MAX`, `RATE_FORCE_OPORT_MAX`, …) e
  **locks**: `_sync_lock` (thread) p/ OS, `oportunidades_sync_lock` (arquivo, cross-process,
  409 se ocupado) p/ carga completa.
- **Duas coisas aqui mudam dado no SAP, e as duas miram PRODUÇÃO** (`SBOALTAMIRAPROD`):
  `ordens_producao_sl.py` e o **worker do `wbcpython`**. No worker, `WBC_BLOCK_PRODUCTION_WRITES`
  está **`false` de propósito** na .11 desde 2026-09-02 (`docs/wbc/DECISOES.md`, "Virada para
  produção"); a trava somente-leitura do SQL Server do WBC (`wbcpython/safety.py`) **não tem
  chave** e não pode ganhar uma. Em `ordens_producao_sl.py`, três invariantes com teste — não
  afrouxe sem decisão explícita: (1) `OP_SL_ENABLED` **nasce `false`**; (2)
  `POST /ordens-producao/<n>/status` é **fail-closed** (sem `OS_API_KEY` → **503**, ao
  contrário das outras rotas, que escrevem no Supabase, reversível); (3) alvo == status atual
  devolve `ja_estava` **sem PATCH**. A allowlist `OP_STATUS_PERMITIDOS` é conferida **antes da
  rede**. Cancelar e voltar para Planejada estão **fora de escopo** (decisão 2026-08-07).
- **Vendas BI: "Pedidos" é `SUM("VlrPedido")`, sem índice**; faturamento é `SUM("Valor")` de
  `VW_FATO_FATURAMENTO`, sem `ValorAdiant`. Trocar diverge do Power BI **sem erro**. Vendedor
  casa por **nome** (`OSLP.SlpName` = `app_profiles.slp_name`), nunca por `slp_code`.
- **DocEntry ≠ DocNum na OP** (a OP 125060 é o DocEntry 126599). O default das rotas é DocNum;
  `?chave=docentry` troca. DocNum que casa com mais de uma ordem é **recusado** (409).
- **Troca de parceiro do pedido** (`U_INO_PN_Correc` + `U_INO_Update = 'Y'`): com pedido
  existente é cancelar-e-recriar (`troca_de_pn`); sem pedido, ele **nasce** no `PN_Correc`
  (`cria_pedido_no_pn_corrigido`). `'N'` + `PN_Correc` = já corrigido à mão, não mexer. O SAP
  recusa o cancelamento ao `orcaview` (`-1116`) — não é defeito da integração.
- **Sessão do Service Layer: nunca um login por request.** O SL tem teto de sessões e vazá-las
  derruba o SL para todo mundo — sessão compartilhada com TTL, a substituída sai pelo
  `/Logout`. O cliente do **WBC** (`wbcpython/infrastructure/service_layer/client.py`, `httpx`)
  faz **login preguiçoso** (na 1ª requisição; entrar no `with` não autentica) e `Logout` na
  saída. Não "corrija" o `__enter__` para logar cedo.
- `config.get_settings()` é cacheado — testes usam `reset_settings()` após mexer em env.
- **Windows Update: `pendentes` só sai quando a varredura do agente é recente** — senão
  `None` + motivo. **Nunca troque `None` por `0`** ("0 pendentes" mente sem varredura). O
  bloco **nunca gera alerta** (decisão do Marcelo; há teste). `AUOptions=4` desta máquina
  fica — não "corrija" para 2.
- **Testes: a suíte NUNCA alcança produção.** `tests/conftest.py` troca os destinos do `.env`
  por falsos e **trava os drivers** (`hdbcli`, `pyodbc`, `pymssql`, `create_client`): teste que
  tenta conectar falha dizendo o destino — faça o stub. Só `@pytest.mark.integration` (com
  `--run-integration`) usa o `.env` real. `tests/wbc/conftest.py` ainda apaga o bloco WBC
  (senão `OS_API_KEY` do `.env` faria o painel exigir chave). Nunca deixe a suíte ler o winreg
  real: `_stub_all_ok` stuba `_windows_update_signal` (esta máquina TEM reboot pendente).
- **`.gitignore` tem `_*.py`** (temporários) e ela casa com `__init__.py`/`__main__.py`:
  arquivo novo com esse nome precisa de `git add -f`. `tests/test_repo_layout.py` pega a falta.
- **`wbcpython` roda como módulo, com cwd na raiz**: `python -m wbcpython <comando>`. Ele lê o
  `.env` do cwd (pydantic-settings) e resolve `state/wbc_tracking.db`, `logs/wbcpython.log` e
  `state/wbc_previsao.json` relativos ao cwd (painel: `run_wbc_painel.bat`; worker: o
  `AppDirectory` do NSSM). Não há `pip install -e`, hatchling nem uv.
- **Defaults do worker existem em dois configs** (`config.py` da raiz relê `TRACKING_DB_URL`,
  `WORKER_*`, `PAINEL_PORTA` para o check `wbc_worker`). Mudou um, mude o outro —
  `tests/test_config_paridade_wbc.py` cobra.
- **`OS_API_KEY` é compartilhada** pela API 8077 e pelo painel WBC (cookie HMAC). Trocar a
  chave derruba os cookies de todo mundo — é o desenho. Sem ela, painel e API ficam abertos.
- **`?checks=wbc` é o SQL Server, não o worker** (alias antigo). O worker é `wbc_worker`
  (aliases `worker`, `integracao_wbc`); não alarma antes do primeiro ciclo registrado.
- **A tarefa legada "Integração WBC" está desativada desde 2026-09-08.** O check
  `scheduled_task` **fica** (`.90` e a tool `estado_tarefa_wbc` o leem), `retired=true`, sem
  alerta. Não apague o bloco nem `monitor_wbc_task.ps1`: `WBC_TASK_MONITOR=true` é o rollback.
- **Nunca `git subtree add` do repo antigo `MCPs\WBCPython`**: o histórico dele versiona um
  `.env.bak` com senha, e este repo é público.
- ⚠️⚠️ **O worker chama o `python.exe` por caminho absoluto** (NSSM `Application`, gravado no
  dia do `install_wbc_services.bat`); os outros 4 pegam o `python` do PATH no reboot. **Trocar
  o Python da máquina não alcança o worker** — calado. Depois de trocar:
  `nssm set OrcaView-WBC-Worker Application "<novo>\python.exe"` (com a parada por arquivo
  antes). Quem diz a verdade é o processo, não o `/status`:
  `Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Select ProcessId, ExecutablePath`.
- **Parada limpa do worker é por ARQUIVO**: gravar `state\wbc_worker.stop` e só então
  `nssm stop` (o `deploy_update.bat` faz isso). Ctrl+C/kill no meio de um POST deixa cotação
  sem vínculo (`docs/wbc/RETOMADA.md`). Ver `wbcpython/host/parada.py`.
- ⚠️ **`hdbcli` 2.29.25 no Python 3.14 derruba o processo (access violation) quando a conexão
  falha** — medido no desktop em 24/09/2026; na `.11` (pin 2.29.23) não conferido.

## Deploy

`deploy_update.bat` na .11: para os 5 serviços, `git pull --ff-only`, `pip` no **Python 3.14
do sistema** só se o hash dos `requirements*` mudou (`state\deps.sha256`), e religa (o worker
só se estava rodando). `requirements.txt` é a fonte de instalação — não migrar deps para o
`pyproject.toml` sem decisão explícita. **`pip`, restart e deploy são do Marcelo.**

## Comandos

```bash
python -m pytest              # suíte completa (SIS + WBC; ~40 s, sem rede)
python -m pytest tests/wbc    # só a suíte do WBC (--run-integration liga os de rede)
python -m ruff check .        # lint (py314 + UP; deve ficar em 0). Sem ruff instalado: uvx ruff@0.15.20 check .
python api.py                 # sobe a API local (porta 8077)
python -m scripts.scheduled_execution   # agendador (loop; Ctrl+C p/ sair)
python -m wbcpython --help    # CLI do WBC: env, doctor, check-sap, check-hana, pendentes, ciclo, worker, dashboard, pesos, datas-de-abertura, faxina, janela
python -m wbcpython pendentes --exportar state/wbc_previsao.json   # o que o ciclo FARIA (só leitura)
python -m wbcpython dashboard # painel WBC (PAINEL_HOST/PAINEL_PORTA do .env)
```

Tooling em `pyproject.toml` (pytest + ruff); dependências de dev em `requirements-dev.txt`.

**Gate antes do commit:** `.githooks/pre-commit` roda `ruff check` e a suíte (~40 s) e barra
o commit se algo falhar — é o único gate automático (não há CI). Clone novo:
`git config core.hooksPath .githooks`. Não use `--no-verify`: conserte o que ele acusou.
