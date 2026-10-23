# CLAUDE.md — ServidorIntegracaoSAP

Guia para agentes: achar o arquivo certo sem varrer o repo. Para a maioria das tarefas bastam
**2 arquivos** (o módulo + seu teste). O "porquê" das regras (datas, medições, incidentes) está em
`docs/INCIDENTES.md` — só abra quando a regra não bastar.

## O que é

Integração SAP B1 → Supabase **e** Integração WBC → SAP. Produção na `192.168.7.11`
(`C:\Python\ServidorIntegracaoSAP`, **Python 3.14.7** do sistema, sem venv), 6 serviços NSSM:

- **API HTTP** 8077 (`OrcaView-OS-API`) — gatilhos, consultas e Painel de Sincronização
  (`GET /sincronizar`); `GET /` leva ao painel WBC (`web/entrada.html`).
- **Agendador** (`OrcaView-Scheduler`) — oportunidades, Vendas BI (15 min na janela, 1 h fora),
  espelho de orçamentos e `arquivo_pedidos` (2 min, 24/7, só grava na .11).
- **Fachada MCP** (`OrcaView-MCP`) — stdio (`mcp/mcp_server.py`) ou HTTP 8078 (`mcp/serve_http.py`).
- **Painel WBC** 8079 (`OrcaView-WBC-Painel`, `python -m wbcpython dashboard`) — porta de entrada
  das telas 8077 e 8080.
- **Worker WBC** (`OrcaView-WBC-Worker`, `python -m wbcpython worker`) — cria/atualiza/cancela
  cotação e pedido no SAP a partir do WBC. **Escreve em PRODUÇÃO.**
- **Controle de Produção** 8080 (`OrcaView-ControleProducao`, `python -m controleproducao web`) —
  Pedidos WBC → Ordens de Produção e Manutenção de OP (liberar/replanejar/encerrar com estoque).
  **Escreve em PRODUÇÃO.** Processo separado do painel de propósito (`hdbcli` caindo não o derruba).

Guias dos pacotes: `docs/wbc/README.md` (+ `DECISOES.md`) e `docs/controleproducao/README.md`
(diário em `migration_guide.md` §7; `decisoes.md`; `PARA_O_ANDERSON.md`).

## Mapa do repositório (código-fonte = raiz, plano)

| Arquivo | Responsabilidade |
| --- | --- |
| `api.py` | Rotas Flask + auth (X-API-Key) + rate-limit + entrypoint waitress |
| `config.py` | Env vars, defaults, `Settings`, `*_ready()`. Exceção: `RATE_*` e `SYNC_LOTE_MAX` no topo do `api.py` |
| `pipeline_core.py` | `SupabaseLoader`, locks de arquivo, validação, retry |
| `extract_sap_to_supabase.py` | Pipeline OPORTUNIDADES (carga completa, agendada) |
| `extract_ordens_servico_engenharia.py` | OS por N_PED (sob demanda): `VW_OS_INTEGRACAO` → `vw_os_integracao`, `diagnosticar_nped`, `consultar_status_pedido` |
| `faturamento_pedido.py` | Como o pedido FECHADO foi faturado (entrega futura × remessas, pela `INV21`); não remetida = `Aberto`. `docs/PLANO_ENTREGA_FUTURA.md` |
| `extract_vendas_bi.py` | VENDAS BI: `VW_PEDIDO_ALTA` + `VW_FATO_FATURAMENTO` → `bi_vendas_*` |
| `extract_orcamentos_espelho.py` | Espelho de `VW_EVOL_ORCAMENTO_ALT` (agendado) |
| `arquivo_pedidos.py` | Só na .11, 2 em 2 min: `ADOC` → `pedidos_sap_alteracoes` antes do teto de 99 (classifica `operacao/historico_pedido.py`); `--simular` só lê |
| `situacao_pedidos.py` · `sap_montagem_labels.py` | **Portes** do web, diffáveis (`test_situacao_pedidos_diffavel.py`) |
| `situacao_pedidos_hana.py` | Leitura HANA + cache + endereço de entrega das rotas `/pedidos/*` |
| `pedidos_compra_hana.py` | Itens dos pedidos de compra (`VW_ALTA_PEDIDOS_COMPRA_ITENS`, cache 120 s) → `GET /compras/pedidos-itens` (escopo `leitura`); contrato `API_PEDIDOS_COMPRA.md` |
| `controle_montagem.py` | Controle de montagem do pedido (tabela `pedidos_montagem` + previsão `@ALTA_PREV`), só leitura, para `/pedidos/<n>/situacao` |
| `entrega_combinada.py` | Data de entrega combinada (tabela `pedidos_entrega_alteracoes`); contrato `API_DATA_ENTREGA_COMBINADA.md` |
| `pedidos_bloqueados.py` | Lista **hardcode** (84337). **3 cópias**: aqui, `web_orcaview_V118/backend/services/pedidos_bloqueados.py`, `mobile_orcaview_V4/lib/pedidos/bloqueados.ts` |
| `monitoring.py` | `collect_status()` (`SELECTABLE_CHECKS`). Check novo = `SELECTABLE_CHECKS` + despacho + alerta + saída + `_CHECK_ALIASES` do `api.py` + `_stub_all_ok` do teste |
| `windows_update.py` | Reboot/updates pendentes (winreg + COM via PowerShell, cache). Porte diffável do SAP_RDP |
| `ordens_producao_sl.py` | Escrita SAP nº 1: status de OP via Service Layer. Irmão de `web_orcaview_V118/backend/services/compras_sap_service.py` |
| `wbcpython/` | Escrita SAP nº 2 (o worker): `domain/` (sem I/O), `application/`, `infrastructure/`, `tracking/` (SQLite), `host/`, `dashboard/`, `cli.py`, `safety.py`, `padroes.py` |
| `controleproducao/` | Escrita SAP nº 3: `main.py` (FastAPI), `cli.py` (Typer), `config.py`, `core/` (trava/logout, guardas, tarefas, histórico, acesso, `sql_ligado`), `modules/{pedidos_wbc,manutencao_op,romaneio}/`. NÃO se chama `app` (colide com `api.py:app`) |
| `casa/` | Casca comum das telas da .11: `_casa.html` (`cabeca()`/`barra()`), `casa.css` (**sem `color-mix`**), `casa.js`, `TELAS`; `acesso.py` (cookie de login comum); cada app chama `casa.instalar(env)` |
| `sap_connection.py` · `db_utils.py` · `retry.py` | `SAPExtractor` (hdbcli; `execute_query(sql, params)`) · `read_dbapi_query` · retry |
| `sql_seguro.py` | `sql(t"...")`: `{v}` → `?`, `{n:ident}` identificador, `{n:int}` literal; `nome_simples()` |
| `feriados_br.py` | Feriados BR até 2030 (o agendador pula) |
| `wake_altservidor_ia.py` | WoL do .90. **Byte-idêntico** a `web_orcaview_V118/tools/wake_altservidor_ia.py`, stdlib, Python 3.8+ |
| `scripts/scheduled_execution.py` | Loop do agendador (APScheduler, 7–18, seg–sex) |
| `mcp/` | Fachada fina sobre a 8077, NÃO fala com banco. `acesso_mcp.py` = token por cliente, `ESCOPO_DA_FERRAMENTA`, auditoria; importa `seguranca/` por sys.path **append** (nunca insert) |
| `seguranca/` | Credenciais (SHA-256; `OS_API_KEY` = admin), auditoria (30 dias), interruptor do agente (escrita só seg–sex 7–19h), aprovações. `docs/SEGURANCA_11.md`, `docs/APROVACOES_11.md` |
| `operacao/` | Leituras da própria .11 (serviços, conexões, histórico de pedido, log do worker, versão, ronda do .90, logins SAP, backup, Hyper-V); `boots`/`travamentos` **idênticos** ao SAP_RDP; `acoes_agente`, `reinicio` |
| `web/` | `sincronizar.html` (Jinja na casca `casa/`), `entrar.html`, `entrada.html`, `inicio.html` (página inicial da Central) |
| `tests/` | pytest; `test_<modulo>.py` espelha o módulo; `tests/wbc/` e `tests/controleproducao/` = suítes dos pacotes |
| `docs/` | `wbc/`, `controleproducao/`, `INCIDENTES.md`, `changelog/`. Planos apagados ficam no git (`git log --diff-filter=D -- 'docs/PLANO_*'`) |
| `API_*.md` (raiz) | Contratos HTTP de outras equipes. **Ficam na raiz** (repo público, links externos). Mudou rota/campo/mensagem = mude o contrato no mesmo commit |
| `sql/` | DDL de referência (NÃO roda sozinho); `sql/hana/` = view do worker; `sql/migracoes/` = já aplicadas |
| `maintenance/` | Conferidor de Vendas BI, scripts de disco/log, relatos do ALTHOST/Hyper-V |

Dependências: `config` ← todos · `pipeline_core` ← extract_* e api · `api.py` orquestra ·
`seguranca/` ← api, controleproducao e mcp · `mcp/` só chama HTTP (da raiz importa só `seguranca/`).

## Tarefa → o que ler

Módulo da raiz = ele + `tests/test_<modulo>.py`; `tests/wbc/` e `tests/controleproducao/` espelham a
árvore do pacote. Abaixo, só o que não é óbvio.

| Tarefa | Ler |
| --- | --- |
| Endpoint HTTP | `api.py` (rota por grep) + `tests/test_api.py` |
| Variável de ambiente | `config.py` + `.env.example` + `tests/test_config.py` |
| `/pedidos/*` | `situacao_pedidos.py` + `situacao_pedidos_hana.py` + `tests/test_api_situacao_pedidos.py` |
| RH e `/usuarios-ativos` | `api.py` (bloco RH no fim) + `tests/test_api.py` + `API_RH_COLABORADORES.md` |
| Tool MCP | `mcp/mcp_server.py` (+ `ESCOPO_DA_FERRAMENTA`) |
| Regra do WBC (SitCode, cotação × pedido, encerramento) | `wbcpython/domain/sitcode.py` + `tests/wbc/domain/` (+ `docs/wbc/DECISOES.md` pelo heading) |
| Ciclo do worker | `wbcpython/application/processar.py` + `host/worker.py` + `tests/wbc/test_processar.py` |
| Peso reescalado pelo SAP (worker desfaz; simulação até `GRAVA_A_PARTIR_DE`) | `wbcpython/domain/peso_reescalado.py` + `application/pesos_reescalados.py` + `tests/wbc/domain/test_peso_reescalado.py` |
| Variável do WBC (`SL_*`, `HANA_*`, `WBC_SQL_*`, `WORKER_*`, `PAINEL_*`) | `wbcpython/config.py` + `.env.example` + `tests/wbc/test_config.py` + `tests/test_config_paridade_wbc.py` |
| Regra do CP (OPs, itens, rateio `GGF_`, `U_INO_ProcessWBC`) | `controleproducao/modules/pedidos_wbc/service.py` + `tests/controleproducao/test_cancelar_ops.py`/`test_recurso_rateio.py` |
| Liberar/replanejar/encerrar OP | `controleproducao/modules/manutencao_op/service.py` + `tests/controleproducao/test_manutencao_op*.py` |
| Tela/rota do CP | `controleproducao/modules/<m>/router.py` + `main.py` + `tests/controleproducao/test_web_modulos.py` |
| Barra, tema, paleta das telas da .11 | `casa/` + `tests/test_casa.py`. Mudou `casa.css`/`casa.js` = suba `casa.VERSAO` |
| Pedidos WBC (tela e `/api/pedidos-wbc`) | `controleproducao/modules/pedidos_wbc/acoes.py` + `tests/controleproducao/test_api_pedidos_wbc.py`; guia `API_PEDIDOS_WBC.md` + `docs/exemplos/pedidos_wbc_clone.html` |
| Manutenção de OP (tela e `/api/manutencao-op`; `solicitante` obrigatório, chave só no cabeçalho) | `controleproducao/modules/manutencao_op/acoes.py` + `api_router.py` + `core/acesso.py` + `tests/controleproducao/test_api_manutencao_op.py` |
| Execuções do CP (30 últimas no Supabase, só .11) | `controleproducao/core/historico.py` + `tests/controleproducao/test_historico.py` |
| Login compartilhado (cookie `wbc_painel`) | `casa/acesso.py` + `tests/wbc/dashboard/test_entrada.py` + `tests/controleproducao/test_acesso.py` |
| Trava pelo IP / `/Logout` no CP | `controleproducao/core/service_layer_client.py` + `tests/controleproducao/test_service_layer_client.py` |
| Variável do CP (`CP_*`, `SL_BUSINESS_PLACE_ID`…) | `controleproducao/config.py` + `tests/controleproducao/test_config.py` |
| Leituras da .11 para o agente | `operacao/` + `tests/test_operacao.py` |
| Credencial, escopo, auditoria, interruptor | `seguranca/` + `tests/test_seguranca.py`, `test_api_seguranca.py`, `test_mcp_acesso.py` |

## NÃO reler

- `CHANGELOG.md` (só o mês corrente; anteriores em `docs/changelog/`) e `README.md` inteiros — vá
  pela busca do heading. Idem `docs/wbc/DECISOES.md` (índice no topo).
- `sql/migracoes/`, `exports/` (dados de cliente), `logs/`, `state/`, `.locks/`.
- `install_*`, `run_*.bat`, `maintenance/` — só em tarefa de deploy/operação.

## Convenções

- **Comentários e docstrings em inglês técnico**, só o porquê. PT existente não se traduz; trecho
  reescrito sai em EN. Identificadores, logs e mensagens HTTP seguem em PT. Os `.ps1` são **ASCII**
  (PowerShell 5.1/BOM), e o stdout do PS sai em **cp850**: quem lê força `[Console]::OutputEncoding`.
- **SQL do HANA com valor de fora**: `execute_query(*sql(t"..."))` do `sql_seguro` — nunca f-string
  com valor; nome de objeto por `{x:ident}`/`nome_simples` (`tests/test_sql_seguro.py`).
- **Arquivos-irmãos de outro repo não se reescrevem de um lado só** (nem `ruff --fix`, nem idioma):
  `ordens_producao_sl.py`, `windows_update.py`, `situacao_pedidos.py`, `sap_montagem_labels.py`,
  `wake_altservidor_ia.py`, `pedidos_bloqueados.py`, `operacao/boots.py`, `operacao/travamentos.py`
  e seus testes (idênticos no SAP_RDP: `cmp` tem de dar igual).
- O repo GitHub se chama `oportunidade_wbc` de propósito; `OPORTUNIDADE_WBC_*` e
  `/api/oportunidade-wbc/status` são funcionais — NÃO renomear.

## Gotchas

Regra com teste = 1 linha + o teste que a protege; sem teste = o porquê junto.

**Produção e escrita no SAP**
- **As três escritas (`ordens_producao_sl.py`, worker WBC, `controleproducao`) ligam só pelo IP da .11**
  (`wbcpython/safety.py` `PRODUCTION_MACHINE_IP`); fora dela `ProductionWriteBlocked` (503 web, saída 2
  CLI). Sem chave no `.env`: `WBC_BLOCK_PRODUCTION_WRITES`/`OP_SL_ENABLED` são ignoradas — não
  ressuscite. Trocar o IP da .11 desliga as três. — `tests/conftest.py` (IP `192.0.2.1`; papel de .11
  = `127.0.0.1`/`como_a_11`), `tests/controleproducao/test_service_layer_client.py`
- Rota de OP: fora da .11 desligada; `POST /ordens-producao/<n>/status` fail-closed (sem `OS_API_KEY` →
  503); alvo == atual → `ja_estava` sem PATCH; allowlist antes da rede; default só `boposReleased`
  (encerrar é do CP; rollback no INCIDENTES). — `tests/test_ordens_producao_sl.py`, `tests/test_config.py`
- Leitura do SQL Server do WBC não tem escrita nem chave para ganhar uma. — `tests/controleproducao/test_sqlserver_client.py`, `tests/wbc/test_safety_readonly.py`
- Sessão do Service Layer: nunca um login por request (o teto derruba o SL de todos); login preguiçoso
  na 1ª requisição + `/Logout` — não "corrija" o `__enter__`. — `tests/wbc/infrastructure/test_service_layer_client.py`
- DocEntry ≠ DocNum na OP: default DocNum, `?chave=docentry` troca, DocNum ambíguo → 409. — `tests/test_api.py`
- Troca de PN (`U_INO_PN_Correc` + `U_INO_Update='Y'`): cancelar-e-recriar ou nascer no PN corrigido;
  `'N'` = corrigido à mão. O `-1116` do SAP ao `orcaview` não é defeito. — `tests/wbc/domain/test_sitcode.py`
- Troca de PN em pedido com OP (`docs/PLANO_TROCA_PN_COM_OP.md`): só Planejadas → o worker as cancela antes
  do pedido; Liberada/Encerrada ou contagem `None` → para e pede gente (log ERROR); toda troca relê as OPs e
  consulta `127.0.0.1:CP_PORTA/health/ocupado` antes de cancelar o pedido. O `/health/ocupado` não vê o
  `processar-novos` rodado à mão pela CLI. — `tests/wbc/domain/test_troca_com_ops.py`, `tests/wbc/test_processar_troca_com_ops.py`
- **Parada limpa do worker é por arquivo** (`state\wbc_worker.stop`, depois `nssm stop`). — `tests/wbc/host/test_parada_por_arquivo.py`
- ⚠️⚠️ **O worker chama o `python.exe` por caminho absoluto** (NSSM `Application`); os outros 5 pegam o
  do PATH. Trocar o Python da máquina **não alcança o worker**, calado. Depois de trocar:
  `nssm set OrcaView-WBC-Worker Application "<novo>\python.exe"` (com a parada por arquivo antes). A
  verdade é o processo, não o `/status`:
  `Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Select ProcessId, ExecutablePath`.
- **Nunca `git subtree add` do repo antigo `MCPs\WBCPython`**: o histórico tem `.env.bak` com senha e
  este repo é público.

**Controle de Produção** (detalhe em `docs/controleproducao/README.md` § "O que morde")
- `processar-novos` não tem rollback (marca `ProcessWBC='Y'` antes da 1ª OP). Retomada:
  `manutencao-op buscar` → `cancelar-ops` → `processar-novos` **sem `--force`** (duplica OP).
- `reprocessar-integrados` cancela toda OP planejada e não recria. — `tests/controleproducao/test_cancelar_ops.py`
- Processar recusa (`409 ops_orfas`, também na execução e com "forçar") pedido cujo orçamento tem pedido
  **cancelado** com OP P/R viva. — `tests/controleproducao/test_ops_orfas.py`
- Liberar/Replanejar/Encerrar recusam OP com saída ou entrada lançada; Interromper para entre OPs;
  `encerrar` (OIGE+OIGN) é o único caminho de encerrar. — `tests/controleproducao/test_manutencao_op.py`, `test_tarefas.py`
- `hana_schema` = `SL_COMPANY_DB`; `HANA_SCHEMA` não é lido. — `tests/controleproducao/test_config.py`
- SQL só por `core/sql_ligado` (nunca `.format` com valor); leitores só SELECT/WITH. — `tests/controleproducao/test_sql_ligado.py`
- `_update_pedido` (recria linhas) FECHADO com erro até o Anderson validar. Worker, pacote e addon C#
  escrevem no mesmo `ORDR`/`OWOR`; desde a F2 do `PLANO_TROCA_PN_COM_OP` o worker olha `OWOR` antes de
  cancelar pedido na troca de PN (e cancela as OPs planejadas dele).
- `deploy_update.bat` aborta com `/health/ocupado` = 1 — nunca `nssm stop` com tarefa em andamento. — `tests/controleproducao/test_acesso.py`
- `CP_HOST` é `0.0.0.0` na .11, **nunca o IP da máquina** (deploy e `/status` sondam
  `127.0.0.1:CP_PORTA`). `.env` é lido na subida: linha nova = `nssm restart OrcaView-ControleProducao`.

**Acesso e segurança**
- ⚠️⚠️ **Segurança acrescenta, NUNCA tira função** (Marcelo): proteção nova vai EM VOLTA do que existe;
  quem migra de chave recebe os escopos que já usa. Tirar função é decisão dele.
- ⚠️⚠️ **O agente nunca aprova**: escrita de agente = `POST /aprovacoes`; ação nova = entrada em
  `acoes_agente.CATALOGO`, nunca comando livre; credencial `agente` nunca recebe `aprovar`/`admin`
  (`PROIBIDOS_AO_AGENTE`); a 8078 carimba `pedido_por`/`em_nome_de`; nunca dê `aprovar` à chave que a
  Mira usa para pedir. — `tests/test_aprovacoes.py`
- Rota protegida da 8077 declara escopo (`@requer_chave('leitura')`); ferramenta MCP nova entra em
  `ESCOPO_DA_FERRAMENTA` (senão exige `admin`). Chave registrada só no cabeçalho; só a `OS_API_KEY`
  mantém o `?key=` antigo. — `tests/test_api_seguranca.py`, `tests/test_mcp_acesso.py`
- `/health` é aberto; `/status` tem dois níveis (mínimo sem credencial; completo com `OS_API_KEY` ou
  `STATUS_ID`, que não abre outra rota); o código HTTP não depende da credencial (o watchdog do .90
  decide por ele); `?strict=1` → 503 se degradado. `collect_status`/`SELECTABLE_CHECKS` são contrato
  entre repos: a redução mora no `api.py`. — `tests/test_api.py`
- `OS_API_KEY` é uma só para 8077, painel e CP (cookie `wbc_painel`, HMAC em `casa/acesso.py`); escrita
  por cookie exige `Origin`/`Referer`; trocar a chave derruba os cookies (é o desenho); sem ela painel
  e API ficam abertos e o CP fica só leitura. — `tests/controleproducao/test_acesso.py`, `tests/wbc/dashboard/test_entrada.py`
- Escritas têm rate-limit in-process e locks (`_sync_lock`; `oportunidades_sync_lock` → 409). — `tests/test_api.py`

**Runtime e operação**
- Agendador roda como módulo: `python -m scripts.scheduled_execution` (o script direto dá
  `ModuleNotFoundError` e o serviço fica PAUSED).
- Entry da API é `python api.py` (waitress + `logs/api.log`); `waitress-serve api:app` não liga o log.
  Não renomear `app`.
- `wbcpython` e `controleproducao` rodam como módulo com cwd na raiz (`.env`, `state/`, `logs/`
  relativos ao cwd; o CP acha o `.env` por `__file__` e, com `env_ignore_empty`, `SL_USERNAME=` vazio
  cai no fallback `OP_SL_*`/`SAP_*` — o `wbcpython` não). Sem `pip install -e`. `SL_BASE_URL`/
  `SL_VERIFY_SSL` têm defaults diferentes no worker e no CP: vale o `.env` da .11.
- Defaults compartilhados só em `wbcpython/padroes.py` (o `deploy_update.bat` repete as portas: mude
  lá também). — `tests/test_config_paridade_wbc.py`
- HANA fora: disjuntor de 30 s (`HanaIndisponivel`). — `tests/test_sap_connection.py`
- Só o worker rotaciona `logs/wbcpython.log`. — `tests/wbc/test_logs.py`
- SQLite do acompanhamento em WAL (`-wal`/`-shm` são normais). — `tests/wbc/host/test_janela_sob_demanda.py`
- `?checks=wbc` é o SQL Server; o worker é `wbc_worker` (não alarma antes do 1º ciclo). — `tests/test_api.py`, `tests/test_monitoring.py`
- Tarefa legada "Integração WBC" aposentada: o check `scheduled_task` fica (`retired=true`);
  não apague o bloco nem `monitor_wbc_task.ps1` (`WBC_TASK_MONITOR=true` é o rollback). — `tests/test_monitoring.py`
- Windows Update: sem varredura recente `pendentes` é `None`, **nunca `0`**; o bloco nunca alerta. —
  `tests/test_windows_update.py`, `tests/test_monitoring.py`. `AUOptions=4` desta máquina fica — não
  "corrija" para 2 (decisão do Marcelo; sem teste).
- Vendas BI: "Pedidos" = `SUM("VlrPedido")` sem índice; faturamento sem `ValorAdiant`; vendedor casa
  por **nome**, nunca `slp_code`. — `tests/test_extract_vendas_bi.py`
- ⚠️ `hdbcli` 2.29.25 no Python 3.14 derruba o processo (access violation) quando a conexão falha
  (medido no desktop; na .11, pin 2.29.23, não conferido) — por isso a suíte trava os drivers.
- Processo travado na .11: `python -m pdb -p <PID>` anexa a um processo vivo (mesmo usuário ou admin).
  **Não exercitado ainda**: é leitura de estado, não pare nada por ele.

**Testes e repo**
- A suíte NUNCA alcança produção: `tests/conftest.py` troca destinos e trava `hdbcli`/`pyodbc`/`pymssql`/
  `create_client`; só `--run-integration` usa o `.env` real. Credenciais/auditoria em pasta temporária
  (`_seguranca_isolada`); `tests/wbc/conftest.py` apaga o bloco WBC. Nunca deixe ler o winreg real
  (`_stub_all_ok`; esta máquina tem reboot pendente).
- `config.get_settings()` é cacheado: `reset_settings()` depois de mexer em env. — `tests/conftest.py`
- `.gitignore` tem `_*.py`: `__init__.py`/`__main__.py` novos pedem `git add -f`. — `tests/test_repo_layout.py`

## Deploy

`deploy_update.bat` na .11 (etapas em `logs/deploy.log`, lido por `GET /operacao/deploy`): aborta se o
CP estiver ocupado, para os 6 serviços, `git pull --ff-only`, `pip` no Python do sistema só se o hash
dos `requirements*` mudou (`state\deps.sha256`), e religa (o worker só se estava rodando).
`requirements.txt` é a fonte de instalação — não migrar para `pyproject.toml` sem decisão.
**`pip`, restart e deploy são do Marcelo.**

## Comandos

```bash
python -m pytest              # suíte completa (SIS + WBC + CP; ~2–3 min, sem rede)
python -m pytest tests/wbc    # só o WBC (--run-integration liga os de rede)
python -m pytest tests/controleproducao
python -m ruff check .        # deve ficar em 0. Sem ruff: uvx ruff@0.15.20 check .
python api.py                 # API local (8077)
python -m scripts.scheduled_execution
python -m wbcpython --help    # env, doctor, check-sap, check-hana, pendentes, ciclo, worker, dashboard, pesos, datas-de-abertura, faxina, janela
python -m wbcpython pendentes --exportar state/wbc_previsao.json   # o que o ciclo FARIA (só leitura)
python -m controleproducao --help   # web, pedidos-wbc, manutencao-op, romaneio, conexoes, diag
python -m controleproducao pedidos-wbc buscar   # só leitura
```

Tooling em `pyproject.toml`; dev em `requirements-dev.txt`.

**Gate antes do commit:** `.githooks/pre-commit` roda `ruff check` e a suíte inteira (**~2–3 min**)
e barra o commit se algo falhar — único gate (não há CI). Clone novo:
`git config core.hooksPath .githooks`. Nunca `--no-verify`: conserte o que ele acusou.
