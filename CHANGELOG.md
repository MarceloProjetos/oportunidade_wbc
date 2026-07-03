# Changelog

Mudanças notáveis deste projeto. Formato inspirado em
[Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/).

## [2026-07-03] — Diagnóstico distingue pedido cancelado × sem OS + respostas sem acento

### Adicionado

- **`diagnosticar_nped` agora consulta também a `ORDR` (status do PEDIDO)**, na mesma conexão,
  best-effort (falha na ORDR não invalida o diagnóstico da OS; chaves `pedido_*` ficam `null`).
  Novas chaves: `pedido_existe`, `pedido_cancelado`, `pedido_status` (`Aberto`/`Cancelado`/
  `Fechado` — `CANCELED` `'Y'`/`'C'` = cancelado; `DocStatus` `'C'` sem cancelamento = fechado).
- **Novos `tipo` na resposta de sync** (antes tudo caía em `sem_os`): `pedido_cancelado`
  ("Pedido cancelado no SAP - nao ha OS a sincronizar.") e `pedido_nao_encontrado`
  ("Pedido nao encontrado no SAP."). O `sem_os` ganhou o status no texto
  ("OS ainda nao gerada para este pedido (pedido aberto)."). Todas as respostas de sync
  incluem `status_pedido`. Painel web: badges `PEDIDO CANCELADO` / `NAO ENCONTRADO` /
  `OS CANCELADA`. Retrocompatível: diag sem as chaves novas cai no `sem_os` genérico.
  +7 testes (suíte **165 passed**).

### Alterado

- **Mensagens das respostas JSON sem acento, de propósito** (`nao`, `esta`, `historico`,
  `indisponivel`, `invalido`…) — legíveis em qualquer terminal sem depender do escape
  `\uXXXX` do JSON (curl/PowerShell mostravam `não`). Vale p/ `motivo`/`error` da API
  e p/ `coerce_positive_int` (400). Logs e docstrings seguem acentuados.

## [2026-07-03] — Tooling p/ agentes: CLAUDE.md + pyproject.toml (pytest/ruff)

Fases 1 e 2 do plano "menos tokens por tarefa, respostas mais consistentes".
**Sem mudança de comportamento** — suíte **158 passed** antes e depois.

### Adicionado

- **`CLAUDE.md` — guia do repositório para agentes** (carregado automaticamente por sessão):
  mapa de módulos + grafo de dependências, tabela **"tarefa → o que ler"** (a maioria das
  tarefas = 2 arquivos), lista do que **não** reler (CHANGELOG/README inteiro/`exports/`/
  `logs/`/`state/`) e gotchas operacionais (scheduler via `-m scripts.scheduled_execution`,
  entry `python api.py` vs `waitress-serve`, `get_settings()` cacheado, `.ps1` ASCII,
  repo GitHub mantém nome antigo, deploy = `git pull` na `.11`).
- **`pyproject.toml` — config central de tooling** (pytest `testpaths` + ruff: regras
  `E, W, F, I`, linha 120, `E741` ignorado de propósito). **NÃO** tem `[project]`/
  `[build-system]`: `requirements.txt` segue sendo a fonte de instalação do deploy
  (decisão explícita, comentada no próprio arquivo). `ruff==0.15.20` pinado no
  `requirements-dev.txt`.

### Alterado

- **Baseline de lint zerada** (`python -m ruff check .` → 0): 12 achados mecânicos
  auto-corrigidos — ordenação de imports (7 módulos), whitespace em linha vazia (3) e
  2 imports sem uso (`typing.List` em `extract_wbc_arvore.py`, `os` em `tests/test_config.py`).
  Diff 22+/21− em 8 arquivos, nenhum caminho de execução alterado.

## [2026-07-03] — Fachada MCP (Fase 4, escrita com confirmação) + endpoint de sync

### Adicionado

- **`POST /ordens-servico/<nped>/sincronizar` (API 8077) — par de ESCRITA do `GET`.** Sincroniza
  (SAP → Supabase) a OS de um pedido e devolve o `resumo` resultante **numa chamada**. Reúsa
  `_sync_one` (serializado no `_sync_lock`): diagnostica a OWOR antes — se não há OS gerada ou está
  cancelada, devolve o aviso **sem sincronizar**. Idempotente (`replace_nped`) + dispara a árvore WBC
  (best-effort). Status `200` (sincronizado **ou** aviso sem_os/cancelada) · `502` (falha de sync) ·
  `400`/`401`. +6 testes (suíte **155 passed**).
- **`mcp/` — Fase 4: 2 tools de ESCRITA com confirmação humana.** `sincronizar_pedido_os(nped, confirmar?)`
  (usa o endpoint acima) e `forcar_carga_oportunidades(confirmar?)` (`POST /oportunidades/sincronizar`;
  `409` se já houver carga). **Confirmação em 2 camadas:** (1) `annotations` (`readOnlyHint=False`,
  `idempotentHint=True`, `openWorldHint=True`) — o cliente MCP sinaliza que é escrita e pede aprovação;
  (2) **preview-então-confirma** — com `confirmar=False` (default) a tool **não escreve**: devolve um
  preview do estado atual + instrução pro modelo mostrar ao usuário e só chamar com `confirmar=True`
  após o "sim". Novo helper `_post` (mesmo tratamento de erro do `_get`; repassa o 409). Validado:
  preview **read-only** contra a `.11` (não escreve). 10 tools no total (8 leitura + 2 escrita).
- **Rate-limit nas ESCRITAS (trava anti-loop), no lado da API.** Janela deslizante in-process,
  thread-safe, por bucket — **generosa** (não atrapalha uso normal, pega runaway/loop de agente):
  default **60** syncs de OS/min (`sync_os`) e **6** cargas completas/min (`force_oport`),
  configurável por env `RATE_SYNC_OS_MAX` / `RATE_FORCE_OPORT_MAX`. Aplicada em
  `POST /ordens-servico/<nped>/sincronizar` e `POST /oportunidades/sincronizar`; se estourar,
  responde **`429`** com `Retry-After` + motivo (o `_post` do MCP repassa à tool → o modelo para).
  +3 testes (suíte **158 passed**).

## [2026-07-03] — Fachada MCP (Fase 3, modo remoto HTTP) — código

### Adicionado

- **`mcp/serve_http.py` — modo remoto da fachada MCP (Streamable HTTP + token estático).**
  Serve o **mesmo** FastMCP (tools + resources da Fase 1) via uvicorn, atrás de um middleware
  ASGI que exige `Authorization: Bearer <SIS_MCP_TOKEN>` (401 sem/errado; encaminha o escopo
  `lifespan` p/ o session-manager iniciar; compara com `hmac.compare_digest`). **Não** usa
  `mcp.run("streamable-http")` (que sobe o uvicorn interno sem hook de auth) — monta
  `mcp.streamable_http_app()` + `uvicorn.run`. Config via `mcp/.env`: `SIS_MCP_TOKEN`,
  `SIS_MCP_HOST` (default `0.0.0.0`), `SIS_MCP_PORT` (default `8078`), `SIS_API_BASE` (na `.11`
  = `http://127.0.0.1:8077` → a `OS_API_KEY` **nunca sai do servidor**). A stdio (`mcp_server.py`)
  fica intacta p/ uso local. **Testado localmente:** sem token → 401, token errado → 401, token
  válido → 400 (passou pelo auth; 400 = handshake MCP). Sem TLS (rede interna, decisão do usuário).
- **`run_mcp.bat` + `install_mcp_service.bat`** — sobem o MCP HTTP como serviço Windows via NSSM
  (`OrcaView-MCP`, `SERVICE_AUTO_START`, `DependOnService OrcaView-OS-API`, logs rotativos),
  espelhando o padrão do `OrcaView-OS-API` (8077). `uvicorn` adicionado ao `mcp/requirements.txt`.
- **Seção "Modo remoto" no `mcp/README.md`** — passo-a-passo de deploy na `.11` (git pull + deps +
  `mcp/.env` + `install_mcp_service.bat` + firewall por IP), registro do cliente
  (`claude mcp add --transport http … --header "Authorization: Bearer …"`), validação e rollback.
  **`web_orcaview_V117` (.90): nenhuma mudança** — consome só a REST `/status`; a Mira não faz
  tool-calling.

### Corrigido

- **`serve_http.py`: HTTP 421 "Invalid Host header" ao acessar pela LAN.** A proteção
  DNS-rebinding do transporte StreamableHTTP (`TransportSecurityMiddleware`) vem **ativa por
  default no `mcp` 1.28.1** da `.11` e só aceita `Host` loopback → um cliente chegando por
  `http://192.168.7.11:8078/mcp` levava 421. O `build_app()` passou a setar
  `mcp.settings.transport_security = TransportSecuritySettings(enable_dns_rebinding_protection=False)`
  **antes** do `streamable_http_app()` (seguro: o acesso já é barrado pelo `StaticBearerMiddleware`,
  a rede é interna e os clientes são apps MCP, não navegadores). Reproduzido o 421 e **verificado o
  fix** (Host LAN → 406/400). **MCP remoto no ar na `.11`** (serviço `OrcaView-MCP`). O
  `PLANO_FASE3.md` foi removido — o conteúdo operacional está consolidado no `mcp/README.md`.
- **`mcp_server.py`: `httpx` com `trust_env=False` — não usa proxy do ambiente na chamada à API
  interna.** O serviço `OrcaView-MCP` (LocalSystem) herdava um proxy HTTP do ambiente da máquina
  e o `httpx` roteava **até a chamada de `127.0.0.1:8077`** pelo proxy → **WinError 10061 "conexão
  recusada"** mesmo com a API no ar (um `curl` interativo, sem proxy no perfil do usuário,
  funcionava — daí a assimetria). Como a fachada só fala com a API interna (loopback/LAN),
  `trust_env=False` ignora qualquer `HTTP_PROXY`/`ALL_PROXY` do ambiente. Validado: `verificar_saude`
  → `ok` contra a `.11`. (Obs.: no incidente real desta `.11` não havia proxy — a causa foi o loopback
  abaixo — mas `trust_env=False` fica como endurecimento correto: uma fachada interna nunca deve usar proxy.)
- **`SIS_API_BASE` na `.11`: loopback → IP da própria máquina (LocalSystem não alcança a pseudo-interface
  de loopback).** O serviço `OrcaView-MCP` roda como **LocalSystem (Sessão 0)** e, neste servidor, esse
  contexto **não conecta em `127.0.0.1:8077`** (`WinError 10061`) — apesar da API no ar e do `curl` +
  `verificar_saude` **interativos** (usuário admin) darem 200. Sem venv, sem proxy: a única diferença era
  o contexto do serviço. Fix (config, no `mcp/.env` gitignored): `SIS_API_BASE=http://192.168.7.11:8077`
  (a API escuta em `0.0.0.0`; o pacote segue local, a chave não sai da máquina). Documentado no README
  (seção "Modo remoto" → Gotcha). **MCP remoto respondendo as tools no Claude** (ex.: `listar_pedidos_com_os`,
  `detalhe_pedido_os`). Fase 3 concluída ponta a ponta.

## [2026-07-03] — Detalhe de OS (endpoint) + Fachada MCP (Fase 1, read-only)

### Adicionado

- **`GET /ordens-servico/<nped>` (API 8077)** — detalhe da OS de **um** pedido, lendo o espelho
  `ordens_servico_engenharia` no Supabase (service_role). Devolve um `resumo` (cliente, status +
  `status_desc`, total, nº de linhas e de OPs, última sincronização) e, com `?linhas=1`, também as
  `linhas` (colunas **enxutas**, sem os textos NCLOB — evita puxar MBs por chamada). **404** se o
  pedido não tem OS sincronizada; requer `X-API-Key`. A rota estática `/ordens-servico/disponiveis`
  mantém prioridade sobre o `<nped>` dinâmico no roteador do Werkzeug. +9 testes (suíte **146 passed**).
- **`mcp/` — Fase 1 da fachada MCP: +3 tools + 2 resources (segue 100% leitura).** Tools:
  `detalhe_pedido_os(nped, incluir_linhas?)` (usa o endpoint acima), `estado_tarefa_wbc()` (só o
  bloco `scheduled_task` do `/status`), `ultimos_erros(limit?)` (filtra as falhas do `/historico`).
  **Resources** (contexto anexável sem gastar tool-call): `sap-integracao://status` e
  `sap-integracao://historico-os`. O helper `_get` passou a repassar corpo JSON de erro estruturado
  — um 404 vira a mensagem real (ex.: "pedido sem OS sincronizada") em vez de "HTTP 404"; um 404 **HTML**
  do Flask sinaliza rota não deployada. Isolado: só mexe em `mcp/` (nada no app/web/Supabase).

## [2026-07-02] — Fachada MCP (Fase 0, read-only)

### Adicionado

- **`mcp/` — servidor MCP (fachada fina)** que expõe os endpoints da API REST (8077)
  como *tools* para clientes MCP (Claude Desktop/Code, assistente Mira), permitindo
  operar/consultar o servidor de integração em linguagem natural. **Camada aditiva e
  isolada:** não reimplementa lógica, não fala com SAP/SQL/Supabase direto, não roda
  agendador — cada tool só chama um endpoint HTTP existente; quem toca o banco continua
  sendo a API. **Fase 0 = 100% leitura:** `verificar_saude` (`/status`),
  `listar_sincronizacoes_os` (`/historico`), `listar_sincronizacoes_oportunidades`
  (`/oportunidades/historico`), `info_oportunidades` (`/oportunidades/info`),
  `listar_pedidos_com_os` (`/ordens-servico/disponiveis`). Implementado com o MCP Python
  SDK (FastMCP, transporte stdio) + httpx; a `SIS_API_KEY` fica no server MCP (injetada
  server-side no `X-API-Key`, nunca vai ao LLM). Config em `mcp/.env` (`SIS_API_BASE`,
  `SIS_API_KEY`); instruções de registro em `mcp/README.md`. Ações de escrita ficam para
  a Fase 2 (com confirmação humana). Não requer mudança nos consumidores nem no Supabase.

## [2026-07-02] — Limpeza diária de logs do Azure (manutenção do servidor .11)

### Adicionado

- **`maintenance/clean_azure_logs.ps1`** — script que apaga logs antigos de
  `C:\WindowsAzure\Logs` (guest agent do Azure: `TransparentInstaller.log`, `WaAppAgent`,
  `MonitoringAgent`, `*.etl`… que se acumulam em ~10 MB cada). Remove **apenas arquivos do dia
  anterior para trás** (mantém os de hoje; `-KeepDays` configurável), **pula arquivos em uso**
  pelo agente (sem erro) e **nunca apaga pastas**; recusa caminho vazio/raiz de disco. Tem
  `-DryRun` (lista sem apagar) e `-Install` (registra a tarefa diária `OrcaView-Clean-Azure-Logs`
  às 06:00 como SYSTEM, usando `-Command "& '<script>'"` — não `-File`, pelo mesmo motivo do
  monitor). Grava resumo em `maintenance/clean_azure_logs.log`. 100% ASCII (PowerShell 5.1).

## [2026-07-02] — Monitor da tarefa agendada "Integração WBC" no `/status`

### Ajustado (pós-deploy)

- **`install_monitor_task.ps1`: a tarefa passou a usar `-Command "& '<script>'"` em vez de
  `-File`.** Causa raiz do monitor "Desatualizado" em produção: a tarefa `OrcaView-Monitor-WBC-Task`
  rodava `powershell.exe -File "…monitor_wbc_task.ps1"` e, **sob SYSTEM + não-interativo**
  (PowerShell 5.1), falhava com `LastTaskResult=1` **sem executar o script** (não gravava
  estado nem log) — enquanto na mão (administrador) e via `-Command "& '…ps1'"` o **mesmo**
  script roda até o fim e grava o estado (comprovado capturando a saída da execução SYSTEM).
  Trocado o `-File` por `-Command "& '<script>'"` (aspas simples dobradas p/ robustez). Tarefas
  já registradas: re-rodar `install_monitor_task.ps1` (idempotente) ou `Set-ScheduledTask`.
- **`monitor_wbc_task.ps1`: log de diagnóstico + escrita com fallback.** Novo
  `state/monitor_wbc_task.log` (grava resultado de cada execução e a exceção real na falha),
  `trap` global e fallback de escrita (se o `Move-Item` de replace falhar → `WriteAllText`
  direto). Fim do `exit 1` silencioso.
- `monitor_wbc_task.ps1`: `LastTaskResult` agora usa `[int64]` (HRESULT/exit code pode
  exceder Int32, ex. `0x800710E0`, que estourava e caía no catch). Além disso, o código
  `0x800710E0` (Win32 4320 = "operador/administrador recusou o pedido" — no Task Scheduler
  quase sempre um **disparo sobreposto pulado**, não falha do programa) passou a ser uma
  **nota** informativa (novo campo `notes` no JSON), sem afetar `healthy` nem gerar alerta.
  Falhas reais (qualquer outro código ≠ 0/running/never-run) continuam como `problems`.

### Adicionado

- **`monitor_wbc_task.ps1`** — script PowerShell que consulta a tarefa agendada do Windows
  **"Integração WBC"** (`Get-ScheduledTask`/`Get-ScheduledTaskInfo`) e grava o estado em
  `state/wbc_task_state.json` (escrita atômica, UTF-8 sem BOM). Detecta: tarefa desabilitada,
  travada em execução (`Running` > 10 min), última execução com erro (`LastTaskResult`),
  ausência de execução (> 15 min sem rodar) e gatilhos perdidos. Nunca lança exceção para
  fora (falhas viram `problems` no JSON); mantido **100% ASCII** de propósito (PowerShell 5.1
  lê `.ps1` sem BOM como ANSI e corromperia acentos — o nome da tarefa é montado via códigos
  de caractere).
- **Check `scheduled_task` no `/status`** (`monitoring.py`) — a API apenas **lê** o JSON do
  monitor (sem subprocesso por request) e o expõe em `GET /status`, alimentando `alerts[]` e
  o `?strict=1` (**HTTP 503** quando a tarefa está ruim). Se o JSON ficar mais velho que
  `WBC_TASK_STALE_MIN` (default 25 min), sinaliza "monitor possivelmente parado". Novo alias
  `?checks=tarefa` (também `task`, `wbc_task`).
- **`install_monitor_task.ps1`** — registra (idempotente) a tarefa que roda o monitor a cada
  10 min como **SYSTEM** (dispensa senha e enxerga a tarefa do `administrador`) + execução
  inicial.
- **`deploy_monitor.ps1`** / **`deploy_monitor.bat`** — deploy em um passo: copia os arquivos
  (se necessário), roda o install e reinicia o serviço `OrcaView-OS-API`.
- **Settings** (`config.py`): `WBC_TASK_NAME`, `WBC_TASK_STATE_FILE`, `WBC_TASK_STALE_MIN`
  (documentados no `.env.example`). Testes cobrindo saudável/travada/stale/ausente + o quirk
  do `ConvertTo-Json` do PowerShell 5.1 (array de 1 item vira escalar).

## [2026-06-30] — Views de relatório (adaptação da VW_OS_EXPED_IMPRESSAO_V2)

### Adicionado

- **`sql/vw_os_exped_impressao.sql`** — duas views PostgreSQL que adaptam a view SAP
  `SBOALTAMIRAPROD.VW_OS_EXPED_IMPRESSAO_V2` para as tabelas-espelho do Supabase:
  - `vw_os_exped_impressao` (V2 fiel, ramo EXP nível 1) — vem **só** de
    `ordens_servico_engenharia` (o espelho já traz os campos do orçamento); **não junta** a
    árvore → não multiplica linhas.
  - `vw_os_exped_arvore` (BOM detalhado) — **1 linha por componente** da árvore WBC com o
    cabeçalho do pedido; junta por `CodigoOrcam = ORCNUM` (cabeçalho colapsado por `NPED` p/
    não inflar).
  - Campos de Filial (OBPL), grupos de item (OITM) e ramo ALMX ficam **NULL** (fontes não
    espelhadas). Mapeamento coluna-a-coluna + SQL verificados por revisão adversarial
    (3/3, nenhuma coluna inexistente). Guia em `docs/CONSUMO_DADOS.md`.

## [2026-06-30] — Guia de consumo (read-only) no repositório

### Documentação

- **`docs/CONSUMO_DADOS.md`** — guia **sanitizado** (sem chaves) para a equipe consumidora:
  tabelas e campos de `ordens_servico_engenharia`, `status_ordens_servico_eng` e
  `wbc_arvore_produto`; como ligar `NPED` → `ORCNUM` (`CodigoOrcam`) → árvore; e exemplos
  **read-only** em REST/JS/Python. As credenciais (URL + chave `anon`) seguem só no handoff
  confidencial, fora do git (`HANDOFF_*.md`).

## [2026-06-30] — fix WBC: ORCNUM vem de `CodigoOrcam` (não `NºOrçament`)

### Corrigido

- `resolver_orcnum` lia o ORCNUM do pedido em `NºOrçament`, que na view de OS vem **nulo**
  → a sync da árvore falhava (`orcnum=None`). Passa a usar
  `COALESCE("CodigoOrcam", "NºOrçament")` — o código está em `CodigoOrcam`. Validado no SAP
  real (84112 → `00124853`, 34 linhas carregadas). DDL/PLANO/teste ajustados.

## [2026-06-30] — Árvore de Produto WBC (sub-sync após a OS)

### Adicionado

- **Sincronização da árvore WBC** (`WBCCAD.dbo.INTEGRACAO_ORCPRDARV`) para o Supabase,
  disparada **após a OS** de um pedido quando a OS está OK (existe e não cancelada) — sem
  mudança para o usuário (mesmo botão "Sincronizar"). Novo `extract_wbc_arvore.py`:
  resolve o `ORCNUM` (= `NºOrçament` na view de OS do SAP) → `SELECT * INTEGRACAO_ORCPRDARV
  WHERE ORCNUM=?` → grava em `wbc_arvore_produto` com **replace por ORCNUM**
  (carrega-depois-poda escopado) + log em `sincronizacao_log_wbc_arvore`. Hook em
  `api.py` (`_sync_one`) é **best-effort**: falha na árvore não quebra a resposta da OS
  (vem no campo `wbc`).
- **DDL Supabase**: `sql/wbc_arvore.sql` (tabela espelho + log, RLS forçado sem policy) e
  `sql/wbc_arvore_read_policy.sql` (SELECT para `anon` → consumidor read-only com a anon key).
- Config `WBC_ARVORE_VIEW` / `WBC_ARVORE_TABLE` / `WBC_ARVORE_SYNC_LOG_TABLE` /
  `WBC_ARVORE_INSERT_BATCH_SIZE`. `db_utils.read_dbapi_query` passa a aceitar `params`
  (consulta parametrizada). Plano em `PLANO_WBC_ARVORE.md`.
- **Supabase aplicado em produção (2026-06-30):** `wbc_arvore.sql` + `wbc_arvore_read_policy.sql`
  rodados e verificados — RLS forçado nas 2 tabelas, policy `SELECT` para `anon` e case das
  colunas confere. Pendente: deploy do código no servidor + 1 teste real.

## [2026-06-30] — `/status` aberto + chave aceita via `?key=`

### Alterado

- **`/status` agora é aberto** (sem `X-API-Key`) — pensado p/ monitoramento e p/ abrir
  direto no navegador (dados de rede interna; painel roda só na intranet).
- **A autenticação aceita a chave por query string** `?key=` / `?api_key=`, além do header
  `X-API-Key` e do `Authorization: Bearer`. Assim os endpoints autenticados funcionam no
  navegador (que não envia header). Obs.: por query string a chave aparece na URL/histórico
  do navegador — em terminal, prefira o header via `curl ... -H "X-API-Key: ..."`.

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
