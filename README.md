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
git clone https://github.com/<seu-usuario>/oportunidade_wbc.git
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

> ⚠️ **Segurança:** o `.env` contém a `service_role` (acesso total ao banco). Ele está no
> [.gitignore](.gitignore) e **nunca** deve ir para o GitHub. Compartilhe credenciais por
> canal seguro, nunca no repositório.

---

## Banco de Dados (Supabase)

Execute os SQLs abaixo no **SQL Editor** do Supabase, na ordem.

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

Valida pacotes, `.env`, conexão SAP e Supabase, e lista views disponíveis.

### 2. Executar a extração

```bash
python extract_sap_to_supabase.py
```

Ou de forma programática, com controle de modo:

```python
from extract_sap_to_supabase import main

main(view_name='VW_EVOL_OPORTUNIDADE_ALT', execution_mode='snapshot')  # default
main(view_name='VW_EVOL_OPORTUNIDADE_ALT', execution_mode='insert')    # acumula histórico
main(view_name='VW_EVOL_OPORTUNIDADE_ALT', execution_mode='upsert')    # atualiza/insere
```

| Modo | Comportamento |
| --- | --- |
| `snapshot` (default) | Insere a nova carga e remove as execuções anteriores (carrega-depois-poda). A tabela reflete o estado atual e nunca fica vazia se algo falhar. |
| `insert` | Apenas insere (acumula histórico por `id_execucao`; pode duplicar). |
| `upsert` | Atualiza existentes ou insere novos. |

> No modo `snapshot` (default), a tabela mantém sempre os **últimos 6 meses** de
> oportunidades (janela deslizante recalculada a cada execução).

---

## Agendamento (Automático)

### Opção A — APScheduler (multiplataforma)

Já incluído em [scheduled_execution.py](scheduled_execution.py): roda uma carga **ao
iniciar** (startup) e depois nos horários **09:00, 12:30 e 17:35** (editáveis na
constante `HORARIOS`):

```bash
python scheduled_execution.py
```

Para rodar no boot do servidor (24/6), registre o wrapper [run_scheduler.bat](run_scheduler.bat)
no Task Scheduler (gatilho "Ao iniciar o sistema") ou via NSSM — ver
[Opção B](#opção-b--windows-task-scheduler) e a seção do serviço mais abaixo.

### Opção B — Windows Task Scheduler

1. Abra o **Agendador de Tarefas** → Criar Tarefa.
2. Ação → Iniciar programa:
   ```
   Programa:    C:\caminho\venv\Scripts\python.exe
   Argumentos:  extract_sap_to_supabase.py
   Iniciar em:  C:\Users\...\oportunidade_wbc
   ```

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

> O [Dockerfile](Dockerfile) usa `python:3.11-slim`. Para usar o SQL Server dentro do
> container, será necessário instalar o `msodbcsql18` na imagem (ver passo 3 da Instalação).

---

## Versionamento (GitHub)

Este projeto ainda não está versionado. Para publicá-lo no GitHub com segurança:

```bash
# 1. Inicializar o repositório
git init
git add .
git commit -m "chore: estado inicial do extrator SAP → Supabase"

# 2. Criar o repositório remoto (via gh CLI) e enviar
gh repo create oportunidade_wbc --private --source=. --push

# — ou manualmente —
git remote add origin https://github.com/<seu-usuario>/oportunidade_wbc.git
git branch -M main
git push -u origin main
```

> ✅ O [.gitignore](.gitignore) já protege `.env`, `logs/`, `venv/` e caches. Confirme com
> `git status` que o `.env` **não** aparece na lista antes do primeiro commit — ele contém
> a `service_role` e credenciais de produção.

---

## Estrutura de Diretórios

```text
oportunidade_wbc/
├── extract_sap_to_supabase.py   # Pipeline principal (SAP + SQL Server → Supabase)
├── test_connections.py          # Diagnóstico de pacotes e conexões
├── scheduled_execution.py       # Agendamento via APScheduler
├── exemplo_avancado.py          # Exemplos de uso avançado
├── pandas_guide.py              # Referência de manipulação com pandas
├── config.json                  # Configuração base (placeholders ${VAR})
├── requirements.txt             # Dependências Python
├── Dockerfile                   # Imagem do container
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
