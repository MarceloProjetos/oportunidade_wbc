# Controle de Produção — como o pacote vive dentro do SIS

> Guia de quem vai mexer no `controleproducao/` (o Anderson, o Marcelo, um agente). O
> histórico técnico do pacote está em `migration_guide.md` (§7 é o diário) e as decisões em
> `decisoes.md`, nesta pasta; o plano da implantação, riscos e decisões abertas em
> `docs/PLANO_CONTROLE_PRODUCAO_11.md`. Este arquivo é só "como rodar e onde está cada coisa".

## O que é

Reescrita Python do addon C# "Controle de Produção — WBC" do SAP Business One. Dois módulos
em uso (a numeração é a do addon):

- **2 — Pedidos WBC:** pega um pedido de venda **que o worker WBC já criou** no SAP e cria por
  cima dele o OrcDetalhe, os itens, os recursos de rateio `GGF_` e as **Ordens de Produção**
  (cascata de semiacabados); `cancelar-ops` desfaz as OPs de um pedido.
- **3 — Manutenção de OP:** liberar, replanejar e **encerrar com movimentação de estoque**
  (saída de insumo + entrada de produto + fechamento — irreversível).

Módulo 4 (Romaneio) é esqueleto, fora do menu. Módulo 1 saiu do escopo em 22/09/2026.

Desde 28/09/2026 o pacote mora aqui, no mesmo molde do `wbcpython/`: **um Python** (o global
da .11, sem venv), **um `.env`** (o da raiz), **um login** (o cookie do painel WBC), **um deploy**
(`deploy_update.bat`) e um serviço NSSM próprio, `OrcaView-ControleProducao`, na `CP_PORTA`
(8080). Escreve em SAP de **produção** pelo Service Layer — só na .11.

## Como rodar (na raiz do repositório, sempre)

```bash
python -m controleproducao --help                 # web, pedidos-wbc, manutencao-op, romaneio, conexoes, diag
python -m controleproducao web                    # a tela (CP_HOST/CP_PORTA do .env; log em CP_LOG_FILE)
python -m controleproducao conexoes testar        # Service Layer, HANA e SQL Server do WBC (só leitura)
python -m controleproducao pedidos-wbc buscar     # pedidos pendentes de OP (só leitura)
python -m controleproducao pedidos-wbc processar-novos 00125431   # GRAVA — cria as OPs do orçamento
python -m controleproducao pedidos-wbc cancelar-ops 84371         # GRAVA — cancela as OPs do pedido
python -m controleproducao manutencao-op buscar 84391             # só leitura
python -m controleproducao manutencao-op encerrar 156209          # GRAVA — irreversível (estoque)
python -m controleproducao diag entidade Resources --campos       # só leitura
```

No Windows, para a saída com acento não quebrar: `$env:PYTHONUTF8='1'` antes (os `.bat` já
fazem isso). Os comandos que gravam pedem confirmação; `--sim` pula a pergunta — inclusive
em produção, então nunca num script.

## Configuração: o `.env` da raiz

O pacote lê **o mesmo `.env`** do SIS, por caminho fixo (`controleproducao/config.py` acha a
raiz pelo próprio arquivo — funciona de qualquer diretório). O serviço lê o `.env` **uma vez,
na subida** (`get_settings` é `lru_cache`): linha nova ou alterada na .11 só vale depois de
`nssm restart OrcaView-ControleProducao` (o `deploy_update.bat` também religa); a CLI é
processo novo a cada comando e lê na hora. Foi o que mordeu em 28/09 com `WBC_SQL_DRIVER`,
que entrou depois do primeiro start. Nomes iguais aos do bloco WBC:

| O que | Variáveis | Observação |
| --- | --- | --- |
| Service Layer (escrita) | `SL_BASE_URL`, `SL_COMPANY_DB`, `SL_USERNAME`, `SL_PASSWORD`, `SL_VERIFY_SSL`, `SL_CA_BUNDLE`, `SL_TIMEOUT_SECONDS` | usuário/senha vazios caem em `OP_SL_USERNAME`/`OP_SL_PASSWORD` (também com a linha presente e vazia — `env_ignore_empty`) |
| HANA (só leitura) | `HANA_HOST`, `HANA_PORT`, `HANA_USERNAME`, `HANA_PASSWORD` | vazios caem em `SAP_*`. **`HANA_SCHEMA` não é lido**: o pacote lê ORDR/OWOR na company de `SL_COMPANY_DB` (`Settings.hana_schema` é uma propriedade). `HANA_SCHEMA_LEGADO` só para `comparar-ops` |
| SQL Server do WBC (só leitura) | `WBC_SQL_HOST/PORT/DATABASE/USERNAME/PASSWORD` | vazios caem em `SQL_*`/`SQLSERVER_*`. Driver: **`pymssql`** desde 29/09/2026 (o mesmo do worker; paridade 10/10 contra o `pyodbc` antigo em PROD) — não depende de ODBC. `WBC_SQL_DRIVER` e `WBC_SQL_TRUST_SERVER_CERTIFICATE` ficaram **sem efeito** |
| A tela | `CP_HOST` (default 127.0.0.1 = só a máquina; na .11 é `0.0.0.0` desde 28/09 ~14:45 (F6), com a regra de firewall `OrcaView-ControleProducao-8080` só para `192.168.0.0/16`; nunca o IP da máquina, porque o deploy e o `/status` sondam `127.0.0.1`), `CP_PORTA` (8080), `CP_LOG_FILE` (`logs/controleproducao.log`), `CP_CLI_LOG_FILE` (`logs/controleproducao_cli.log`, os comandos da CLI que gravam), `CP_URL` (link vindo do painel; vazio = mesmo host:porta) | `CP_PORTA` existe em três configs (raiz, `wbcpython`, aqui) e `CP_LOG_FILE` em dois — `tests/test_config_paridade_wbc.py` cobra |
| Histórico (Execuções) | `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | só na .11; sem elas, a tela fica com a memória do processo (`/health` → `"historico"`) |
| Login | `OS_API_KEY` | a mesma da API 8077 e do painel. **Sem ela, a tela fica só leitura** (escrita responde 503) |
| Módulo 3 | `SL_BUSINESS_PLACE_ID` | filial dos lançamentos de estoque quando não dá para derivar do dado (0 = derivar) |

## Produção: quem pode gravar, e como se entra

- **Trava pelo IP.** Escrita (POST/PATCH) na company de produção só sai da máquina que tem o
  IP da .11 (`wbcpython/safety.py`, `PRODUCTION_MACHINE_IP`). Em qualquer outra, o
  `ServiceLayerClient` recusa **antes do login** (`ProductionWriteBlocked`), e a camada de cima
  já barra antes de criar a tarefa: a web responde **503**, a CLI sai com **código 2**.
  Homologação nunca é barrada. Não há chave no `.env` para isso — é a regra do SIS.
- **Login.** Com `OS_API_KEY` no `.env`, toda página pede a chave uma vez e grava o cookie
  `wbc_painel` — o **mesmo** do painel WBC (outro processo, outra porta; o navegador não separa
  cookie por porta). Quem entrou no painel já está aqui. Scripts usam `X-API-Key`. POST vindo
  do navegador precisa de `Origin`/`Referer` do próprio host (CSRF). `/docs` não existe.
- **Sessões.** Uma sessão do Service Layer por tarefa/comando, com `/Logout` ao fechar (o
  teto de sessões é compartilhado com o worker).
- **Rotas abertas** (sem chave): `/entrar`, `/sair`, `/health`, `/health/ocupado`,
  `/painel-wbc`, `/static/*`. `/health/ocupado` devolve `1` com tarefa em andamento — é o que
  o `deploy_update.bat` lê antes de parar o serviço.

## Testes e lint

```bash
python -m pytest tests/controleproducao -q     # a suíte do pacote (~4 s)
python -m pytest -q                            # a suíte inteira (SIS + WBC + pacote) — é o pre-commit
python -m ruff check .                         # tem de ficar em 0
```

- `tests/controleproducao/conftest.py` desliga o `.env` (`env_file=None`), apaga as variáveis
  do ambiente, aponta hosts para `127.0.0.1` e limpa o cache do `get_settings` — nenhum teste
  alcança SAP/HANA/WBC (o `conftest` da raiz ainda trava os drivers).
- O `conftest` da raiz fixa `PRODUCTION_MACHINE_IP` num endereço nunca local: **todo teste é
  "outra máquina"**. Quem precisa fazer papel da .11 usa a fixture `como_a_11`.
- Testes de rota que gravam usam a fixture `cliente` de `test_web_modulos.py` (chave + cookie
  + `Origin`), porque a tela agora está atrás do gate.
- `ruff`: `E501` liberado só para o SQL de `controleproducao/modules/*/queries.py`
  (`pyproject.toml`); o resto segue as regras do repo, inclusive `UP` (py314).

## Homologação: do seu notebook, nunca na .11

O `.env` da .11 é o do worker WBC — trocar `SL_COMPANY_DB` lá apontaria o worker para
homologação. Para testar contra `SBOALTAMIRAHOMOLOG`:

1. `.env` **local** com `SL_COMPANY_DB=SBOALTAMIRAHOMOLOG` (+ credenciais que alcancem a
   homologação, `OS_API_KEY` para a tela). A trava pelo IP garante que daqui nada vai para
   produção; homologação não é produção para ela.
2. `python -m controleproducao conexoes testar` → `pedidos-wbc buscar` → o reteste (F4 do plano).

## Deploy e serviço (na .11 — é o Marcelo quem executa)

`deploy_update.bat` (aborta se `/health/ocupado` = 1; para os 6 serviços; `git pull`; `pip`
pelo hash do `requirements.txt`; religa; valida `/health`) e `install_wbc_services.bat`
(registra `OrcaView-ControleProducao` → `run_controleproducao.bat` → `python -m controleproducao
web`, cwd na raiz, stdout em `logs\controleproducao_service.log` zerado a cada start). O log
do Python é `logs/controleproducao.log` (5 MB × 3) — é também a marca "já subiu" do check
`controle_producao` do `/status` (`/status?checks=cp` com `X-API-Key` ou `STATUS_ID`; sem
credencial vem a visão pública, `restrito:true`, e o bloco `controle_producao` não sai). O
barra do topo é a casca comum da "Central Integração SAP" (`casa/`, desde 01/10/2026): as cinco
telas em todas as três; a API 8077 e o painel respondem `GET /controle-producao[/<tela>]`.

**Rede (F6, 28/09/2026):** na .11 `CP_HOST=0.0.0.0` + regra de firewall da 8080 **só para a
LAN** (molde: a regra da 8079; receita no README, seção "Controle de Produção") +
`nssm restart OrcaView-ControleProducao` — a exposição vira a mesma do painel: quem tem a
`OS_API_KEY`. Com `CP_HOST=127.0.0.1` (o default do código) o serviço só escuta em loopback:
de fora a 8080 **recusa conexão** (não é queda) e, na própria .11, o painel tem de ser aberto
por `http://localhost:8079/` — os botões montam o link com o host da página e o cookie
`wbc_painel` é por host.

**Módulo 3:** `Liberar`, `Replanejar` (de volta à tela desde 29/09 — D4) e `Encerrar` pela
tela; `Replanejar` também pela CLI (`python -m controleproducao manutencao-op replanejar`) e pela
API JSON, sempre recusando OP com insumo baixado ou produto apontado; a API 8077 deixou de encerrar OP
(`OP_STATUS_PERMITIDOS_DEFAULT = 'boposReleased'`) — encerrar com estoque é só aqui.
**Módulo 2:** `Reprocessar` em "Pedidos integrados" (fora da tela de 28 a 30/09 — D8,
revertida pelo Marcelo): cancela todas as OPs planejadas do pedido (de qualquer origem) e não
recria — o pedido volta para "Pedidos novos". Os comandos da CLI que gravam deixam rastro em
`logs/controleproducao_cli.log`. Quem opera a tela:
[GUIA_OPERADOR.md](GUIA_OPERADOR.md).
Pré-voo do piloto (só leitura, PROD): `python maintenance/pre_voo_controleproducao.py
<orçamento>`.

## Mapa

| Onde | O que |
| --- | --- |
| `controleproducao/main.py` | FastAPI: routers, `/static`, `acesso.instalar(app)`; sem `/docs` |
| `controleproducao/cli.py` | Typer: `web` e os comandos; `_avisa_ambiente` (trava → saída 2) |
| `controleproducao/config.py` | `Settings` plano; fallbacks; `hana_schema` = `sl_company_db`; `CP_*`; `OS_API_KEY` |
| `controleproducao/core/acesso.py` | middleware de login, `/entrar`, `/sair`, `/health`, `/painel-wbc`, CSRF |
| `controleproducao/core/guardas.py` · `core/web.py` | regra "quem grava onde" (IP) e sua tradução em 503 |
| `controleproducao/core/service_layer_client.py` | cliente httpx; `_assegura_escrita` em `_request`/`_via_batch`; `/Logout` |
| `controleproducao/core/confirmacao.py` · `core/tarefas.py` | token de uso único; execuções em memória (1 por módulo) |
| `controleproducao/core/historico.py` | tela Execuções: as 30 últimas terminadas no Supabase (`controle_producao_execucoes`, `sql/`), só na .11; sobrevivem ao restart |
| `controleproducao/modules/{pedidos_wbc,manutencao_op,romaneio}/` | `service.py` (regra), `queries.py` (SQL), `router.py`, `schemas.py` |
| `controleproducao/modules/pedidos_wbc/resources/` | `Solda.txt`, `Explosao.txt` — listas de negócio lidas pelo código, não remover |
| `controleproducao/templates/` · `static/` | Jinja + CSS (guia visual em `GUIA_ESTILO_ORCAVIEW.md`) |
| `tests/controleproducao/` | os 244 testes do pacote + config, acesso, comando `web`, trava/logout, SQL ligado e trava de leitura, histórico (≈460 em 30/09) |
| `wbcpython/dashboard/acesso.py` | o cookie/HMAC compartilhado (fora de `web.py` para não puxar o painel inteiro) |

## O que NÃO veio do pacote original

`wheels/`, `scripts/00–06.ps1`, `config/log_config.json`, `MANIFESTO.sha256`,
`requirements-windows.txt`, o `.venv`, o `CLAUDE.md`/`LEIA-ME.md` do pacote e as docs de
instalação isolada (`CHECKLIST_IMPLANTACAO`, `OPERACAO`, `SOLUCAO_DE_PROBLEMAS`,
`SEGURANCA`) — o SIS já tem deploy, serviço, log, monitoração e login. A pasta com o pacote
original (`IntegracaoPedido_CriacaoOP/`) foi apagada em 30/09/2026 (D2): o original está no
zip com o Anderson.
