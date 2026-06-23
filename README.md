# Extrator SAP B1 → Supabase

**Pipeline de integração da Altamira** que extrai a evolução de oportunidades de uma
view do **SAP B1 (HANA)**, enriquece com a situação do orçamento vinda do **SQL Server
(WBCcad)** e carrega tudo numa tabela do **Supabase (PostgreSQL)**.

![Python](https://img.shields.io/badge/Python-3.12-blue)
![SAP HANA](https://img.shields.io/badge/SAP-HANA-orange)
![SQL Server](https://img.shields.io/badge/SQL%20Server-2016-red)
![Supabase](https://img.shields.io/badge/Supabase-PostgreSQL-darkgreen)
![Docker](https://img.shields.io/badge/Docker-ready-blue)

---

## Sumário

- [Como Funciona](#como-funciona)
- [Pré-requisitos](#pré-requisitos)
- [Instalação](#instalação)
  - [1. Clonar o repositório](#1-clonar-o-repositório)
  - [2. Ambiente virtual + dependências](#2-ambiente-virtual--dependências)
  - [3. ODBC Driver 18 (SQL Server)](#3-odbc-driver-18-para-sql-server)
  - [4. Variáveis de ambiente (.env)](#4-variáveis-de-ambiente-env)
- [Banco de Dados (Supabase)](#banco-de-dados-supabase)
- [Como Rodar](#como-rodar)
- [Agendamento (Automático)](#agendamento-automático)
- [Docker](#docker)
- [Versionamento (GitHub)](#versionamento-github)
- [Estrutura de Diretórios](#estrutura-de-diretórios)
- [Troubleshooting](#troubleshooting)
- [Licença](#licença)

---

## Como Funciona

```text
┌──────────────────────┐        ┌────────────────────────┐
│   SAP B1 (HANA)      │        │   SQL Server (WBCcad)  │
│  VW_EVOL_OPORTUNI-   │        │  INTEGRACAO_ORCSIT     │
│  DADE_ALT (29 cols)  │        │  ORCNUM · SITCOD ·     │
│                      │        │  ORCALTDTH             │
└──────────┬───────────┘        └───────────┬────────────┘
           │ hdbcli                          │ pyodbc
           ▼                                 ▼
        ┌──────────────────────────────────────────┐
        │     extract_sap_to_supabase.py (pandas)   │
        │  merge  N_WBC  =  ORCNUM   (LEFT JOIN)     │
        │  + id_execucao + data_hora_extracao       │
        └─────────────────────┬─────────────────────┘
                              │ supabase-py (service_role)
                              ▼
                  ┌────────────────────────────┐
                  │      Supabase (Postgres)   │
                  │  oportunidades  ──FK──►    │
                  │  situacoes_orcamento       │
                  └────────────────────────────┘
```

1. **Extrai** a view `VW_EVOL_OPORTUNIDADE_ALT` do SAP HANA (schema `SBOALTAMIRAPROD`),
   limitando aos **últimos 6 meses** por `CreateDate` (filtro aplicado na própria query).
2. **Enriquece** com `SITCOD` e `ORCALTDTH` do SQL Server, casando `N_WBC = ORCNUM`.
3. **Adiciona** os campos de rastreio `id_execucao` (UUID) e `data_hora_extracao`.
4. **Carrega** no Supabase usando a chave `service_role` (ignora RLS), inserindo em
   **lotes** e, no modo `snapshot` (default), removendo as execuções anteriores ao final.

> Robustez (24/6): timeouts e *retry* com backoff nas conexões SAP/SQL/Supabase, inserção
> em lotes, e estratégia *snapshot* **carrega-depois-poda** — se a carga falhar, os dados
> antigos permanecem intactos. Parâmetros no topo de `extract_sap_to_supabase.py`
> (`MESES_RETROATIVOS`, `INSERT_BATCH_SIZE`, timeouts, `RETRY_*`).

---

## Pré-requisitos

| Componente | Versão | Obrigatório | Observação |
|------------|--------|:-----------:|------------|
| Python | 3.12+ | ✅ | |
| SAP HANA (`hdbcli`) | — | ✅ | Leitura da view de oportunidades |
| Supabase | Free/Pro | ✅ | Destino dos dados (PostgreSQL) |
| SQL Server (`pyodbc`) | 2016+ | ⬜ | Apenas para `SITCOD`/`ORCALTDTH` |
| **ODBC Driver 18** | 18.x | ⬜ | Necessário se usar o SQL Server |
| Docker | — | ⬜ | Execução em container (opcional) |

> ⬜ = opcional. Sem o SQL Server / ODBC Driver, o pipeline ainda roda — as colunas
> `SITCOD` e `ORCALTDTH` simplesmente ficam nulas (LEFT JOIN com fallback).

---

## Instalação

### 1. Clonar o repositório

```bash
git clone https://github.com/MarceloProjetos/oportunidade_wbc.git
cd oportunidade_wbc
```

### 2. Ambiente virtual + dependências

```bash
# Criar e ativar venv
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate      # Linux/Mac

# Instalar dependências Python
pip install -r requirements.txt
```

### 3. ODBC Driver 18 (para SQL Server)

> Só é necessário se você for usar o enriquecimento com `SITCOD`/`ORCALTDTH`.
> O `pyodbc` (instalado via `requirements.txt`) precisa de um driver ODBC nativo do
> sistema operacional — ele **não** vem junto.

**Windows** — instale via `winget` (recomendado):

```powershell
winget install --id Microsoft.msodbcsql.18 --exact `
  --accept-source-agreements --accept-package-agreements
```

Ou baixe o instalador `.msi` oficial da Microsoft:
🔗 <https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server>

**Linux (Debian/Ubuntu):**

```bash
curl https://packages.microsoft.com/keys/microsoft.asc | sudo tee /etc/apt/trusted.gpg.d/microsoft.asc
curl https://packages.microsoft.com/config/ubuntu/$(lsb_release -rs)/prod.list | sudo tee /etc/apt/sources.list.d/mssql-release.list
sudo apt-get update
sudo ACCEPT_EULA=Y apt-get install -y msodbcsql18
```

**Verificar a instalação** (deve listar `ODBC Driver 18 for SQL Server`):

```bash
python -c "import pyodbc; print([d for d in pyodbc.drivers() if 'SQL Server' in d])"
```

> O script tenta os drivers em ordem (`18 → 17 → Native Client 11.0 → SQL Server`) e usa
> o primeiro que conectar — então, instalado o Driver 18, ele é usado automaticamente.

### 4. Variáveis de ambiente (.env)

Copie o template e preencha com seus dados:

```bash
copy .env.example .env          # Windows
# cp .env.example .env           # Linux/Mac
```

| Variável | Descrição |
|----------|-----------|
| `SAP_HOST` / `SAP_PORT` | Host e porta do SAP HANA (porta típica: `30015`) |
| `SAP_USER` / `SAP_PASSWORD` | Credenciais do SAP HANA |
| `SAP_DATABASE` | Database tenant (ex.: `B1P`) — opcional |
| `SAP_SCHEMA` | Schema da view (ex.: `SBOALTAMIRAPROD`) |
| `SAP_VIEW_NAME` | View de origem (ex.: `VW_EVOL_OPORTUNIDADE_ALT`) |
| `SUPABASE_URL` | URL do projeto (`https://xxx.supabase.co`) |
| `SUPABASE_KEY` | Chave **anon** (leitura) |
| `SUPABASE_SERVICE_ROLE_KEY` | Chave **service_role** (escrita — ignora RLS) |
| `TABLE_NAME` | Tabela de destino (default: `oportunidades`) |
| `SQL_HOST` / `SQL_PORT` | Host e porta do SQL Server (porta típica: `1433`) |
| `SQL_USER` / `SQL_PASSWORD` | Credenciais do SQL Server |
| `SQL_DATABASE` | Database (ex.: `WBCCAD`) |
| `SQL_DRIVER` | Opcional — força um driver ODBC específico (ex.: `ODBC Driver 18 for SQL Server`) |

> Variáveis do **agendamento** (`INTERVALO_MINUTOS`, `JANELA_HORAS`, `DIAS_SEMANA`,
> `EXECUTION_MODE`) e do **log** (`SYNC_LOG_TABLE_NAME`) são opcionais e têm defaults —
> ver [Agendamento](#agendamento-automático) e [Banco de Dados](#banco-de-dados-supabase).

> ⚠️ **Segurança:** o `.env` contém a `service_role` (acesso total ao banco). Ele está no
> [.gitignore](.gitignore) e **nunca** deve ir para o GitHub. Compartilhe credenciais por
> canal seguro, nunca no repositório.

---

## Banco de Dados (Supabase)

Execute os SQLs abaixo no **SQL Editor** do Supabase, na ordem.

> ℹ️ **Hardening do Supabase (2026-06-22).** O projeto Supabase é **único e compartilhado**
> (web, mobile e este extrator). Numa rodada de endurecimento via Advisor, foram otimizadas
> policies RLS (Auth Init Plan), adicionados índices em FKs e revogado o `EXECUTE` público da
> função `update_followup`. **Este pipeline não foi afetado:** ele escreve com a
> **`service_role`**, que **ignora RLS** e não depende daquela função. Plano completo em
> `web_orcaview_V117/docs/SUPABASE_ADVISOR_PLANO_2026-06-22.md`.

> 💡 **Por que as colunas têm aspas?** No PostgreSQL identificadores sem aspas viram
> minúsculas. Como o script insere usando o case exato da view SAP (`CreateDate`,
> `N_WBC`, `NumOport`…), as colunas **precisam** ser criadas com aspas duplas para o
> PostgREST encontrá-las na inserção.

### 1. Tabela principal `oportunidades`

```sql
create table public.oportunidades (
  id                    bigint generated always as identity primary key,
  "CreateDate"          timestamp,
  "Cotacao"             integer,
  "TipoDoc"             text,
  "NumDoc"              integer,
  "NumOport"            integer,
  "StatusWBC"           text,
  "N_WBC"               text,
  "CodPN"               text,
  "NomePN"              text,
  "FirstName"           text,
  "E_MailL"             text,
  "Representante"       text,
  "DataOport"           timestamp,
  "DataCotacao"         timestamp,
  "Valor"               numeric(21,2),
  "UF"                  text,
  "Municipio"           text,
  "Usuario"             text,
  "DataContatoCliente"  timestamp,
  "AcaoContato"         text,
  "Lead"                text,
  "SituacaoCliente"     text,
  "N_Bitrix"            text,
  "PctComissao"         numeric(21,2),
  "Retorno"             text,
  "Indice"              text,
  "Negociacao"          text,
  "rn"                  bigint,
  "DataCriacaoPN"       timestamp,
  -- Enriquecimento vindo do SQL Server (INTEGRACAO_ORCSIT)
  "SITCOD"              integer,
  "ORCALTDTH"           timestamp,
  -- Campos de rastreio adicionados pelo script
  id_execucao           uuid,
  data_hora_extracao    timestamp,
  inserted_at           timestamptz default now()
);

create index idx_oportunidades_numoport    on public.oportunidades ("NumOport");
create index idx_oportunidades_id_execucao on public.oportunidades (id_execucao);

-- Leitura liberada para a chave anon
alter table public.oportunidades enable row level security;
create policy "leitura_anon" on public.oportunidades for select to anon using (true);
```

### 2. Tabela de domínio `situacoes_orcamento`

Traduz o código `SITCOD` para a descrição da situação do orçamento (WBCcad):

```sql
create table public.situacoes_orcamento (
  sitcod    integer primary key,
  descricao text not null
);

insert into public.situacoes_orcamento (sitcod, descricao) values
  (0,'Entrada'), (5,'Em Liberação'), (6,'Incompleto'), (7,'Urgente'),
  (8,'Prioridade'), (10,'Cálculo/Projeto'), (20,'Aprovação Técnica'),
  (30,'Cálculo Financeiro'), (40,'Emissão p/ Cliente'), (45,'Pro Forma'),
  (50,'Negociação/Aprovação'), (55,'Alteração Projeto/Orçam'),
  (60,'Fechado/Pedido'), (70,'Suspenso'), (90,'Perdido'), (99,'Cancelado')
on conflict (sitcod) do nothing;

alter table public.situacoes_orcamento enable row level security;
create policy "leitura_anon" on public.situacoes_orcamento for select to anon using (true);

-- Chave estrangeira (integridade SITCOD)
alter table public.oportunidades
  add constraint fk_oportunidades_sitcod
  foreign key ("SITCOD") references public.situacoes_orcamento (sitcod);
```

### 3. Tabela de log `sincronizacao_log`

Registra hora, duração e status de cada sincronização. O script grava uma linha ao final
de cada carga e mantém só os **6 registros mais recentes** (poda os antigos). É auxiliar —
falhas ao gravar este log são apenas registradas e **não** afetam a carga principal.

```sql
create table public.sincronizacao_log (
  id                       bigint generated always as identity primary key,
  data_hora_sincronizacao  timestamptz,
  duracao_segundos         numeric(10,2),
  status                   text,            -- 'sucesso' | 'falha'
  qtd_registros            integer          -- data_hora_sincronizacao já registra o horário; sem inserted_at
);

alter table public.sincronizacao_log enable row level security;
create policy "leitura_anon" on public.sincronizacao_log for select to anon using (true);
```

> O nome da tabela é configurável via `SYNC_LOG_TABLE_NAME` (default: `sincronizacao_log`).

### Consultar com a descrição da situação

```sql
select o."N_WBC", o."NomePN", o."Valor", o."SITCOD", s.descricao
from public.oportunidades o
left join public.situacoes_orcamento s on s.sitcod = o."SITCOD"
limit 100;
```

---

## Como Rodar

### 1. Testar as conexões (recomendado antes da 1ª carga)

```bash
python test_connections.py
```

Valida pacotes, `.env`, conexão SAP e Supabase (anon + **service_role** para escrita),
SQL Server (se configurado), e lista views disponíveis.

---

## Testes unitários

```bash
pip install -r requirements-dev.txt
pytest
```

Cobertura atual: `config`, validação SQL, erro de tenant SAP, modos de execução e janela
do agendador. Não exige credenciais reais (sem integração com SAP/Supabase em CI).

### 2. Executar a extração

```bash
python extract_sap_to_supabase.py
```

Ou de forma programática, com controle de modo:

```python
from extract_sap_to_supabase import main

main(view_name='VW_EVOL_OPORTUNIDADE_ALT', execution_mode='snapshot')  # default
main(view_name='VW_EVOL_OPORTUNIDADE_ALT', execution_mode='insert')    # acumula histórico
```

| Modo | Comportamento |
| --- | --- |
| `snapshot` (default) | Insere a nova carga e remove as execuções anteriores (carrega-depois-poda). A tabela reflete o estado atual e nunca fica vazia se algo falhar. |
| `insert` | Apenas insere (acumula histórico por `id_execucao`; pode duplicar). |

> O modo `upsert` foi **removido**: a tabela não tinha índice UNIQUE de negócio e o
> PostgREST não conseguia resolver conflitos (só duplicava linhas). Para manter o
> estado atual, use `snapshot`.

> No modo `snapshot` (default), a tabela mantém sempre os **últimos 6 meses** de
> oportunidades (janela deslizante recalculada a cada execução).

---

## Agendamento (Automático)

### Opção A — APScheduler (multiplataforma)

Já incluído em [scheduled_execution.py](scheduled_execution.py): roda uma carga **ao
iniciar** (startup) e depois **em intervalo fixo dentro da janela comercial** — por
padrão **a cada 30 min, das 07h às 18h59, seg–sex** (sem sábado, domingo nem
feriados nacionais brasileiros — calendário até 2030):

```bash
python scheduled_execution.py
```

Os horários são configuráveis por variáveis de ambiente:

| Variável | Default | Descrição |
|----------|---------|-----------|
| `INTERVALO_MINUTOS` | `30` | Minutos entre cargas (piso de 5; aceita valores > 59). |
| `JANELA_HORAS` | `7-18` | Faixa de horas inclusiva (ex.: `7-18` = 07h às 18h59). |
| `DIAS_SEMANA` | `mon-fri` | Legado (a execução usa seg–sex + feriados BR automático). |
| `EXECUTION_MODE` | `snapshot` | Modo de carga (`snapshot` ou `insert`). |

> **Dias úteis:** o agendador não roda em sábados, domingos nem feriados nacionais
> (fixos e móveis: Carnaval, Sexta-feira Santa, Corpus Christi etc.), calendário
> pré-carregado até **2030** em `feriados_br.py`. A carga de startup também respeita
> essa regra (só ignora a faixa de horas em dia útil).

> Robustez 24/7: um *lock* global serializa as cargas (nunca há duas simultâneas, nem
> com a do startup); `coalesce` + `misfire_grace_time` de 1h toleram servidor desligado;
> log com rotação diária (`logs/scheduled_execution.log`, 12 dias) e *heartbeat* horário;
> `SIGTERM` para parada limpa ao rodar como serviço.

Para rodar no boot do servidor (24/6), registre o wrapper [run_scheduler.bat](run_scheduler.bat)
no Task Scheduler (gatilho "Ao iniciar o sistema") ou via NSSM — ver
[Opção B](#opção-b--serviço-no-boot-windows).

### Opção B — Serviço no boot (Windows)

Para o agendador subir sozinho quando o servidor liga, registre o wrapper
[run_scheduler.bat](run_scheduler.bat) com gatilho **ONSTART** (ajuste o caminho):

```powershell
schtasks /Create /TN "OrcaView-ETL" /SC ONSTART /RL HIGHEST /RU SYSTEM /F ^
  /TR "C:\caminho\oportunidade_wbc\run_scheduler.bat"
```

Depois, em *Propriedades da tarefa → Configurações*, marque **"Reiniciar se a tarefa
falhar"**. Alternativa: instalar como serviço dedicado via **NSSM** (`nssm install
OrcaView-ETL ...`), que aparece em `services.msc`.

### Opção C — Linux/Mac cron

```cron
0 8 * * * cd /caminho/oportunidade_wbc && /caminho/venv/bin/python extract_sap_to_supabase.py >> logs/execution.log 2>&1
```

---

## Docker

```bash
# Build + execução única
docker compose up --build

# Para rodar agendado, ajuste o `command` em docker-compose.yml:
#   command: python scheduled_execution.py
```

> O [Dockerfile](Dockerfile) usa `python:3.12-slim` com **ODBC Driver 18** pré-instalado
> (SQL Server opcional). Passe as variáveis via `.env` (`env_file` no compose).

---

## Versionamento (GitHub)

Repositório: **<https://github.com/MarceloProjetos/oportunidade_wbc>**

Fluxo de trabalho do dia a dia:

```bash
git pull                       # trazer atualizações antes de começar
# ... editar arquivos ...
git add -A
git commit -m "feat: descrição da mudança"
git push
```

> ⚠️ **O `.env` nunca é versionado** — está no [.gitignore](.gitignore) (contém a
> `service_role` e senhas de produção). Antes de qualquer commit, confirme com
> `git status` que ele **não** aparece. Se um segredo vazar para o histórico, **rotacione
> as chaves** no Supabase/SAP — o git guarda todo o histórico.
>
> 💡 O repositório está **público**. Sendo dados internos (oportunidades/clientes),
> avalie torná-lo privado em *Settings → General → Danger Zone → Change visibility*.

---

## Estrutura de Diretórios

```text
oportunidade_wbc/
├── config.py                    # Configuração centralizada (.env)
├── sap_connection.py            # Conexão SAP HANA compartilhada
├── feriados_br.py               # Calendário de feriados nacionais (até 2030)
├── extract_sap_to_supabase.py   # Pipeline principal (SAP + SQL Server → Supabase)
├── test_connections.py          # Diagnóstico de pacotes e conexões
├── scheduled_execution.py       # Agendamento via APScheduler (IntervalTrigger)
├── run_scheduler.bat            # Wrapper p/ Task Scheduler / NSSM (boot 24/7)
├── examples/
│   └── exemplo_avancado.py      # Exemplos de uso avançado (referência)
├── requirements.txt             # Dependências Python
├── requirements-dev.txt         # pytest (testes unitários)
├── tests/                       # Suíte pytest
├── Dockerfile                   # Imagem do container (Python 3.12 + ODBC 18)
├── docker-compose.yml           # Orquestração Docker
├── setup.sh                     # Setup rápido (Linux/Mac)
├── .env.example                 # Template de variáveis de ambiente
├── .env                         # Credenciais reais (NÃO versionar)
├── .gitignore                   # Arquivos ignorados pelo git
└── README.md                    # Este arquivo
```

---

## Troubleshooting

### `getaddrinfo failed` no Supabase
URL inválida no `.env`. Confirme `SUPABASE_URL` (formato `https://xxx.supabase.co`, sem
`/rest/v1/`). Em **Project Settings → Data API**.

### `Could not find the table 'public.xxx' in the schema cache`
A tabela não existe ou o nome em `TABLE_NAME` está errado. Crie a tabela (ver
[Banco de Dados](#banco-de-dados-supabase)).

### `invalid input syntax for type integer: "1234.0"`
Coluna inteira recebendo float (pandas converte inteiros com `NaN` para float). O script
já trata isso convertendo inteiros-exatos de volta para `int` em `prepare_data`.

### Anon lê `0 registros` mas a tabela tem dados
RLS ativo sem policy de leitura para a `anon`. Crie a policy (ver SQLs acima) ou consuma
via `service_role` no servidor (**nunca** exponha a `service_role` no front-end).

### `Data source name not found` / driver ODBC não encontrado
O ODBC Driver 18 não está instalado. Ver [passo 3 da Instalação](#3-odbc-driver-18-para-sql-server).

### SQL Server: conexão recusada (10061)
Host/porta errados ou serviço inacessível. Teste a porta:
```powershell
Test-NetConnection -ComputerName <host> -Port <porta>
```

### Acentos quebrados no console (Windows)
O console usa cp1252. Os scripts já forçam UTF-8 (`sys.stdout.reconfigure`). Os dados no
banco são gravados corretamente — é só exibição local.

---

## Licença

Software proprietário — uso interno Altamira.
