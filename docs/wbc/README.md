# Integração WBC → SAP (`wbcpython/`) — como rodar

Reescrita em Python da integração **WBC ↔ SAP Business One** (substitui o `WBCServConsole`
C#/DI-API): lê os orçamentos no SQL Server do WBC, decide pela máquina de estados do
`SitCode`, cria/atualiza/cancela **cotação e pedido no SAP** pelo Service Layer (REST),
vincula à oportunidade, espelha o status e grava o snapshot do UDO `OrcDetalhe`. Vem com um
**worker** (ciclo periódico, execução única garantida por trava no banco) e um **painel**
(FastAPI + Jinja2 + HTMX) que lê só o banco de acompanhamento.

Desde 2026-09-08 é um pacote do **ServidorIntegracaoSAP**: mesma raiz, mesmo `.env`, mesmo
`requirements.txt`, mesma suíte (`tests/wbc/`), mesmo `deploy_update.bat`. Não há `uv`,
`hatchling` nem `pip install -e`: o pacote é a pasta `wbcpython/` e roda com
`python -m wbcpython` **a partir da raiz do repositório**.

Os documentos desta pasta com "Documento histórico" no topo contam como o projeto chegou
aqui (decisões, aprendizados, riscos, diário). Este arquivo diz como ele funciona hoje.

---

## As duas regras

1. **Escrita em produção.** A trava `WBC_BLOCK_PRODUCTION_WRITES` (padrão `true`) bloqueia
   qualquer `POST`/`PATCH`/`PUT`/`DELETE` do Service Layer, e qualquer SQL de escrita, cujo
   destino seja `SBOALTAMIRAPROD` (`wbcpython/safety.py`, `ProductionWriteBlocked`). Na
   .11 ela está **`false` por decisão** (virada para produção, 02/09/2026 —
   `DECISOES.md`): o worker escreve em documento de verdade, e avisa isso na primeira
   linha de todo ciclo. Fora da .11, deixe `true`.
2. **O SQL Server do WBC (`WBCCAD`) é somente leitura, em ambiente nenhum.** Não há variável
   que desligue isto; há testes que verificam estruturalmente (`tests/wbc/test_safety_readonly.py`).
   Todo estado da integração vai para o **banco de acompanhamento**, nunca para o WBCCAD.

Credenciais nunca entram em arquivo versionado, log, docstring ou commit.

---

## Rodar

```bash
cd D:\ProjetoAltamira\MCPs\ServidorIntegracaoSAP      # a raiz (onde está o .env)
python -m wbcpython env          # para onde está apontado (não acessa rede)
python -m wbcpython doctor       # diagnóstico da instalação (não acessa rede)
python -m wbcpython check-sap    # login no Service Layer + leitura mínima (só leitura)
python -m wbcpython check-hana   # HANA: as views existem no schema configurado? (só leitura)
python -m wbcpython pendentes --exportar state/wbc_previsao.json   # o que o ciclo FARIA
python -m wbcpython ciclo        # UM ciclo completo e sai (escreve!)
python -m wbcpython ciclo --orcamento 00123316   # só este orçamento (janela de 12 meses)
python -m wbcpython ciclo --simular              # enche o painel sem tocar no SAP
python -m wbcpython worker       # contínuo: ciclo a cada WORKER_INTERVAL_SECONDS, no expediente
python -m wbcpython dashboard    # painel em PAINEL_HOST:PAINEL_PORTA (.env)
python -m wbcpython pesos --pedido 84315 --simular   # corrigir Weight1 das linhas (prévia)
python -m wbcpython datas-de-abertura                # preenche data_abertura no acompanhamento
python -m wbcpython faxina --dias 6                  # apaga decisões mais velhas (ações/erros ficam)
```

Saída esperada do `env` na .11:

```
WBCPython 0.1.0
ambiente=prod | company_db=SBOALTAMIRAPROD (PRODUÇÃO) | schema_hana=SBOALTAMIRAPROD | trava_de_escrita_em_producao=DESATIVADA
```

Na .11 worker e painel são serviços NSSM (`install_services.bat`): `OrcaView-WBC-Worker`
(`run_wbc_worker.bat`; **manual até a virada**, parada limpa de até 60 s) e
`OrcaView-WBC-Painel` (`run_wbc_painel.bat`; automático). Atualização pelo
`deploy_update.bat` — o worker só religa se estava rodando.

Dependências: as do `requirements.txt` da raiz (pydantic-settings, sqlalchemy, pymssql,
hdbcli, httpx, apscheduler, fastapi, uvicorn, jinja2, python-multipart). Sem `pymssql`
nada que toque o WBC funciona; sem `fastapi`/`uvicorn` o `dashboard` recusa subir e diz o
que instalar.

---

## Configuração

Tudo no `.env` da raiz, bloco **"Integração WBC → SAP"** do `.env.example`. Os nomes não
colidem com os do resto do repositório (`SAP_*` é o hdbcli das oportunidades; `HANA_*` é o
do WBC; `OP_SL_*` é o Service Layer do status de OP; `SL_*` é o do WBC).

| Grupo | Variáveis | Observação |
|---|---|---|
| Ambiente | `WBC_ENVIRONMENT`, `WBC_BLOCK_PRODUCTION_WRITES`, `WBC_PRODUCTION_COMPANY_DB` | as três de produção mudam **juntas** com `SL_COMPANY_DB` e `HANA_SCHEMA` (`RISCOS_PRODUCAO.md` §6) |
| Service Layer (escrita) | `SL_BASE_URL`, `SL_COMPANY_DB`, `SL_USERNAME`, `SL_PASSWORD`, `SL_VERIFY_SSL`, `SL_CA_BUNDLE`, `SL_TIMEOUT_SECONDS` | login só na primeira chamada de um ciclo **com escrita** (ciclo sem escrita não toca o SL); `Logout` no fim |
| WBC (só leitura) | `WBC_SQL_HOST`, `WBC_SQL_PORT`, `WBC_SQL_DATABASE`, `WBC_SQL_USERNAME`, `WBC_SQL_PASSWORD` | use usuário `db_datareader` |
| HANA (só leitura) | `HANA_HOST`, `HANA_PORT`, `HANA_USERNAME`, `HANA_PASSWORD`, `HANA_SCHEMA` | `HANA_SCHEMA` = mesma company de `SL_COMPANY_DB` |
| Acompanhamento | `TRACKING_DB_URL` | `sqlite:///./state/wbc_tracking.db` — relativo ao cwd (a raiz); o serviço **cria** o arquivo e as 4 tabelas na primeira subida |
| Worker | `WORKER_INTERVAL_SECONDS`, `WORKER_HORARIO_INICIO`, `WORKER_HORARIO_FIM`, `WORKER_DIAS_DE_TRABALHO`, `MESES_DE_JANELA`, `MESES_DE_JANELA_DIRIGIDA`, `LIMITE_DE_ESCRITA_POR_CICLO`, `FATOR_PESO_EMBARQUE` | produção em 08/09/2026: 180 s, 07:00–20:00, seg–sex, 6 meses |
| Log | `LOG_LEVEL`, `LOG_FILE` | `logs/wbcpython.log` — o mesmo texto da tela; rotação 5 MB × 3 |
| Painel | `PAINEL_HOST`, `PAINEL_PORTA`, `PAINEL_SENHA`, `OS_API_KEY`, `SIS_PAINEL_URL`, `OS_API_PORT` | ver abaixo |

---

## Painel

`python -m wbcpython dashboard` — seis abas: **Oportunidades** (KPIs que são também os
filtros da lista), **Próximo ciclo** (o retrato gerado por `pendentes --exportar`; o painel
não chama a simulação sozinho), **Detalhe** (o histórico de cada orçamento com a regra e o
motivo de cada decisão), **Executar** (os comandos da CLI em subprocesso, com a saída ao
vivo), **Execuções** (do worker) e **Log** (o arquivo `LOG_FILE`, filtrável, pausável).
Cada bloco se repinta no seu ritmo (log a cada 5 s, números a cada 30 s). Sem CDN: o
`htmx.min.js` é servido do próprio pacote.

**Entrada.** Com `OS_API_KEY` no `.env` (a mesma chave da API 8077 / Painel de
Sincronização), o painel pede a chave uma vez e guarda um cookie `HttpOnly` com um token
derivado dela (HMAC) — nunca a chave. Trocar a chave invalida todos os cookies. Scripts e
`curl` usam `X-API-Key` ou `?key=`, como na API. **Sem `OS_API_KEY` o painel fica aberto**,
igual à API (e o `dashboard` avisa no arranque se estiver exposto na rede).

**Botão "Sincronização SAP → Supabase"** (topo) → `GET /sincronizacao` → o Painel de
Sincronização: `SIS_PAINEL_URL` se configurada; senão o mesmo host da requisição, na porta
`OS_API_PORT` (8077). Do outro lado, `⇄ Integração WBC` (`GET /painel-wbc` da API) volta
para cá.

**Aba Executar.** Leitura e diagnóstico (`pendentes`, `env`, `doctor`, `check-sap`,
`check-hana`) sem proteção extra. Escrita (`ciclo`, `pesos`, `datas-de-abertura`) exige
`PAINEL_SENHA` **e** o nome de quem executa — e **em produção fica desabilitada de qualquer
forma** (ali a execução é pelo terminal, com alguém responsável na frente). `PAINEL_SENHA`
vazia desabilita, nunca libera. Uma execução por vez, sob a mesma trava do worker.

O painel **nunca acessa SAP ou WBC**: lê o banco de acompanhamento e o retrato em disco.

---

## Worker

`python -m wbcpython worker`: primeiro ciclo imediato (se dentro do expediente), depois a
cada `WORKER_INTERVAL_SECONDS`, só nos dias e horário de `WORKER_*`. Fora deles o processo
fica vivo e ocioso — não é falha. Trava no banco (`travas`) garante execução única entre
processos (worker × aba Executar × outra máquina). Um orçamento problemático vira erro
registrado e o laço segue. `Ctrl+C` (ou o `nssm stop`) termina o ciclo em andamento
(9–14 s medidos) antes de sair.

Cada ciclo: lista as oportunidades da janela na view HANA
`VW_INO_OPORTUNIDADE_INTEGRACAO` (`sql/hana/`; **uma consulta** substitui quatro do
legado), lê cada orçamento no WBC, decide (`domain/sitcode.py`), aplica pelo Service Layer
(`application/processar.py`), grava acompanhamento + evento + execução. Teto de escritas
por ciclo em `LIMITE_DE_ESCRITA_POR_CICLO`.

O `/status?checks=wbc_worker` da API 8077 (e a tool MCP `estado_integracao_wbc`) lê a
tabela `execucoes` e alarma quando o worker silencia **dentro do expediente dele**, quando um
ciclo fica `em_andamento` além do limite, ou quando o último falhou. Antes do primeiro ciclo
registrado na máquina não alarma (é informação, não saúde).

---

## Banco de acompanhamento

SQLite em `state/wbc_tracking.db` (o `state/` é runtime, ignorado pelo git). **Criado pelo
próprio serviço** — worker ou painel — na primeira subida, com as tabelas
`acompanhamento` (uma linha por orçamento, o estado corrente), `eventos` (o histórico:
regra, mensagem, detalhes), `execucoes` (uma por ciclo) e `travas`. Colunas novas do
modelo são acrescentadas em base existente (`tracking/repositorio.py`, `_acrescentar_colunas_novas`).
**Retenção (desde 08/09/2026):** `registrar_evento` não grava uma decisão igual à última do
orçamento (era isso que enchia o banco: 934.634 decisões em 6 dias, das quais 12.559 eram
mudanças de verdade), e o worker apaga uma vez por dia as decisões mais velhas que
`EVENTOS_RETENCAO_DIAS` (6). Ação, erro e reprocessamento nunca são apagados. À mão:
`python -m wbcpython faxina`.

Para copiar um banco de outra máquina: com o worker de origem **parado** (SQLite em uso copia
corrompido). Depois, `python -m wbcpython datas-de-abertura` se as linhas antigas não tiverem
`data_abertura`.

---

## Testes

```bash
python -m pytest tests/wbc            # 843 testes, ~10 s, sem rede
python -m pytest tests/wbc --run-integration   # + os que acessam SL / SQL Server / HANA de verdade
python -m ruff check .
```

Os testes do WBC começam com o ambiente **neutro** (`tests/wbc/conftest.py` apaga `SL_*`,
`HANA_*`, `WBC_*`, `WORKER_*`, `PAINEL_*`… do `os.environ` e zera `OS_API_KEY`): o `.env` da
máquina não muda o resultado. Os de integração ficam marcados `integration` e só rodam com
`--run-integration`, sempre apontando para `SBOALTAMIRAHOMOLOG`.

---

## Estrutura

```
wbcpython/
├── config.py                  # Settings (pydantic-settings) — lê o .env do cwd
├── safety.py                  # as duas travas
├── logs.py                    # uma fonte, duas telas (console + LOG_FILE)
├── cli.py                     # os comandos
├── domain/                    # máquina de estados, revisões, números, mapeamento (sem I/O)
├── application/               # processar.py (o caso de uso) · previsao.py (a prévia)
├── infrastructure/
│   ├── service_layer/         # cliente REST do SAP Service Layer + repositórios
│   ├── wbc_sql/               # repositório do WBC (SQLAlchemy + pymssql, só leitura)
│   └── hana/                  # views HANA via hdbcli
├── tracking/                  # banco de acompanhamento (modelos + repositório)
├── host/worker.py             # worker (APScheduler, trava, sinais)
└── dashboard/                 # painel FastAPI + Jinja2 + HTMX (web.py, comandos.py, dados.py, previsao.py)
    ├── templates/             # pagina.html (casca), entrar.html, _*.html (fragmentos)
    └── static/                # painel.css, htmx.min.js (versionado, 0BSD)
```

---

## Problemas comuns

| Sintoma | Causa provável | Solução |
|---|---|---|
| `No module named wbcpython` | rodou fora da raiz do repositório | `cd` na raiz e `python -m wbcpython …` |
| `env` mostra `PRODUÇÃO` | `SL_COMPANY_DB=SBOALTAMIRAPROD` | esperado na .11; em qualquer outra máquina, troque para `SBOALTAMIRAHOMOLOG` e ligue a trava |
| `ProductionWriteBlocked` | a trava impediu uma escrita em produção | comportamento correto fora da .11 |
| `ReadOnlyViolation` | tentativa de escrita no SQL Server do WBC | comportamento correto — o estado vai para o acompanhamento |
| `ModuleNotFoundError: pymssql` / `hdbcli` / `fastapi` | dependência faltando | `pip install -r requirements.txt` |
| painel pede chave e você não tem | `OS_API_KEY` do `.env` | a mesma do Painel de Sincronização |
| aba "Próximo ciclo" vazia | ninguém gerou o retrato | `python -m wbcpython pendentes --exportar state/wbc_previsao.json` |
| erro de certificado TLS | cert autoassinado do SL | `SL_VERIFY_SSL=false` ou `SL_CA_BUNDLE` |
| `/status` diz `worker WBC sem ciclo` | serviço `OrcaView-WBC-Worker` parado no expediente | `nssm status OrcaView-WBC-Worker`; log em `logs/wbcpython.log` |

---

## Documentação desta pasta

| Arquivo | Responde |
|---|---|
| `DECISOES.md` | o que já foi decidido e por quê — para não redebater nem "corrigir" o que foi escolhido de propósito |
| `APRENDIZADOS.md` | fatos descobertos rodando contra SAP e WBC de verdade: nomes de campo, tabelas que enganam, armadilhas |
| `RISCOS_PRODUCAO.md` | o raio de impacto medido da virada para produção, e o roteiro |
| `RETOMADA.md` | o estado e as perguntas abertas na época do standalone |
| `COMO_TESTAR_HOMOLOGACAO.md` | roteiro de teste em dez passos, por risco |
| `DEFEITOS_LEGADO.md` | o que o C# fazia errado e o que se preservou de propósito |
| `PROGRESS.md` | o diário completo, sessão a sessão |
| `ai_spec/00_index.md` | onde mora hoje o que a especificação original regia |
