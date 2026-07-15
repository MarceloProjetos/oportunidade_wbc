# ServidorIntegracaoSAP — Integração SAP B1 → Supabase

**Pipeline de integração da Altamira** que extrai a evolução de oportunidades de uma
view do **SAP B1 (HANA)**, enriquece com a situação do orçamento vinda do **SQL Server
(WBCcad)** e carrega tudo numa tabela do **Supabase (PostgreSQL)**.

![Python](https://img.shields.io/badge/Python-3.12-blue)
![SAP HANA](https://img.shields.io/badge/SAP-HANA-orange)
![SQL Server](https://img.shields.io/badge/SQL%20Server-2016-red)
![Supabase](https://img.shields.io/badge/Supabase-PostgreSQL-darkgreen)

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
- [Monitoramento](#monitoramento)
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

> ⬜ = opcional. Sem o SQL Server / ODBC Driver, o pipeline ainda roda — as colunas
> `SITCOD` e `ORCALTDTH` simplesmente ficam nulas (LEFT JOIN com fallback).

---

## Instalação

### 1. Clonar o repositório

```bash
# O repositório no GitHub ainda se chama "oportunidade_wbc"; clonamos na pasta nova.
git clone https://github.com/MarceloProjetos/oportunidade_wbc.git ServidorIntegracaoSAP
cd ServidorIntegracaoSAP
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

> Variáveis do **agendamento** (`INTERVALO_MINUTOS`, `JANELA_HORAS`,
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

### Executar a extração

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

## Testes unitários

```bash
pip install -r requirements-dev.txt
pytest
```

Cobertura atual: `config`, validação SQL, erro de tenant SAP, modos de execução e janela
do agendador. Não exige credenciais reais (sem integração com SAP/Supabase em CI).

---

## Pipeline de Ordens de Serviço

Pipeline **independente** do de oportunidades, que sincroniza a view SAP HANA
**consolidada** `VW_OS_INTEGRACAO` (OS + estrutura/árvore de produto + orçamento,
53 colunas) para uma **única** tabela Supabase `vw_os_integracao`, **sob demanda,
por `N_PED`**. Usa a **mesma conexão SAP** e reaproveita o núcleo compartilhado
[pipeline_core.py](pipeline_core.py) (`SupabaseLoader`, `prepare_data`, etc.).
**Não** faz enriquecimento com SQL Server nem usa `SITCOD`.

> Consolidação 2026-07-14: substituiu os 6 espelhos separados (OS engenharia, lookup
> de status, 3 views de impressão, solda e árvore WBC) e seus 3 logs por esta tabela
> única. A view usa `"N_PED"` (com underscore) como chave.

**1. Criar a tabela** — execute [sql/vw_os_integracao.sql](sql/vw_os_integracao.sql)
no SQL Editor do Supabase (dropa as tabelas antigas, cria `vw_os_integracao` com
RLS + policy de leitura `anon`, e o log `sincronizacao_log_os_integracao`).

**2. Sincronizar um ou mais pedidos:**

```bash
python extract_ordens_servico_engenharia.py 84080            # um pedido
python extract_ordens_servico_engenharia.py 84080 84095      # vários
```

Ou de forma programática:

```python
from extract_ordens_servico_engenharia import main, run_npeds
main(84080)                 # um NPED
run_npeds([84080, 84095])   # vários
```

| Modo (`OS_EXECUTION_MODE`) | Comportamento |
| --- | --- |
| `replace_nped` (default) | Carrega-depois-poda **escopado ao `N_PED`**: insere as linhas do pedido e remove só as linhas antigas **daquele** pedido. A tabela acumula vários pedidos, cada um atualizável de forma independente; um pedido nunca fica vazio se a carga falhar. |
| `insert` | Apenas insere (acumula histórico por `id_execucao`; pode duplicar). |

> Se a view retornar **0 linhas** para o `N_PED` (pedido inexistente), a tabela é
> **mantida inalterada** — não apaga um pedido válido já carregado.

A tabela tem os campos de controle `id_execucao`, `data_hora_extracao`, `origem_view`
e `inserted_at`. Consulta simples:

```sql
select * from public.vw_os_integracao where "N_PED" = 84080;
```

> Códigos de `Status` na view: `P` Planejado · `R` Liberado · `L` Encerrado ·
> `C` Cancelado (a API traduz via dicionário estático, sem tabela de lookup).

**3. Disparar pela API (do app)** — um endpoint HTTP que o app chama para sincronizar
um pedido sob demanda (a escrita continua via `service_role`):

```bash
# subir o serviço (produção, Windows): waitress
waitress-serve --listen=0.0.0.0:8077 api:app
# ou, para dev:  python api.py

# disparar a sync de um pedido:
curl -X POST http://localhost:8077/sync/ordens-servico/84080 -H "X-API-Key: SUA_CHAVE"
# vários: curl -X POST .../sync/ordens-servico -H "Content-Type: application/json" \
#              -H "X-API-Key: SUA_CHAVE" -d '{"npeds":[84080,84095]}'
```

| Rota | Método | O que faz |
| --- | --- | --- |
| `/` | GET | **Painel** unificado: OS (sob demanda) + Oportunidades (agendado). |
| `/health` | GET | Liveness (`{"status":"ok"}`). |
| `/historico` | GET · DELETE | Histórico de OS (lê o log) · limpar. Requer `X-API-Key`; `?limit=N`. |
| `/sync/ordens-servico/<nped>` | POST | Sincroniza um pedido. |
| `/sync/ordens-servico` | POST | Corpo `{"nped":N}` ou `{"npeds":[...]}`. |
| `/oportunidades/historico` | GET · DELETE | Histórico do pipeline agendado · limpar. Requer `X-API-Key`. |
| `/oportunidades/info` | GET | Total de linhas na tabela + agenda (intervalo/janela). Requer `X-API-Key`. |
| `/oportunidades/sincronizar` | POST | **Força** a carga completa de oportunidades (lock cross-process; `409` se já houver uma rodando). |

> 🖱️ **Jeito mais fácil:** abra `http://<servidor>:8077/` no navegador — uma telinha
> ([web/sincronizar.html](web/sincronizar.html)) com campo do pedido, chave (com "lembrar")
> e botão **Sincronizar** (aceita vários pedidos). Sem curl/DevTools.

**Notas operacionais:**

- 🔑 Defina `OS_API_KEY` no `.env` para exigir o header `X-API-Key` (ou `Authorization:
  Bearer`). Sem ela, o endpoint fica aberto e o serviço **loga um aviso no startup** —
  use só em rede interna/dev.
- 🔒 As cargas são **serializadas** (nunca duas ao mesmo tempo — evita conexões SAP
  concorrentes).
- 🧹 O log do `httpx` é rebaixado para `WARNING` (sem a URL gigante a cada requisição).
- 📒 A API grava em **`logs/api.log`** (rotação diária, 12 dias) além do console — o log
  persiste mesmo fechando a janela / rodando como serviço.
- 🚀 `api.py` usa **waitress** (produção); se ele não estiver instalado, cai no servidor
  de **dev do Flask**. Host/porta vêm de `OS_API_HOST`/`OS_API_PORT` (default `0.0.0.0:8077`).

**Subir a API no boot (Windows).** Use o wrapper [run_api.bat](run_api.bat). Pré-requisitos:
`python -m pip install waitress` e definir `OS_API_KEY` no `.env`.

Opção recomendada — **NSSM** (serviço dedicado, reinicia sozinho, aparece em `services.msc`):

```bat
nssm install OrcaView-OS-API "C:\caminho\ServidorIntegracaoSAP\run_api.bat"
nssm set OrcaView-OS-API AppDirectory "C:\caminho\ServidorIntegracaoSAP"
nssm set OrcaView-OS-API Start SERVICE_AUTO_START
nssm start OrcaView-OS-API
```

Alternativa — **Task Scheduler** (gatilho ONSTART; marque "Reiniciar se a tarefa falhar"):

```bat
schtasks /Create /TN "OrcaView-OS-API" /SC ONSTART /RL HIGHEST /RU SYSTEM /F ^
  /TR "C:\caminho\ServidorIntegracaoSAP\run_api.bat"
```

Liberar a porta no firewall (acesso de outras máquinas):

```bat
netsh advfirewall firewall add rule name="OrcaView OS API 8077" dir=in action=allow protocol=TCP localport=8077
```

Conferir: abra `http://127.0.0.1:8077/health` (ou de outra máquina `http://<ip-servidor>:8077/health`)
→ `{"status":"ok"}`. **Não** deixe o `run_api.bat` aberto manualmente junto com o serviço
(brigam pela porta 8077).

---

## Agendamento (Automático)

### Opção A — APScheduler (multiplataforma)

Já incluído em [scripts/scheduled_execution.py](scripts/scheduled_execution.py): roda uma carga **ao
iniciar** (startup) e depois **em intervalo fixo dentro da janela comercial** — por
padrão **a cada 30 min, das 07h às 18h59, seg–sex** (sem sábado, domingo nem
feriados nacionais brasileiros — calendário até 2030):

```bash
python scripts/scheduled_execution.py
```

Os horários são configuráveis por variáveis de ambiente:

| Variável | Default | Descrição |
|----------|---------|-----------|
| `INTERVALO_MINUTOS` | `30` | Minutos entre cargas (piso de 5; aceita valores > 59). |
| `JANELA_HORAS` | `7-18` | Faixa de horas inclusiva (ex.: `7-18` = 07h às 18h59). |
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
  /TR "C:\caminho\ServidorIntegracaoSAP\run_scheduler.bat"
```

Depois, em *Propriedades da tarefa → Configurações*, marque **"Reiniciar se a tarefa
falhar"**. Alternativa: instalar como serviço dedicado via **NSSM** (`nssm install
OrcaView-ETL ...`), que aparece em `services.msc`.

### Opção C — Linux/Mac cron

```cron
0 8 * * * cd /caminho/ServidorIntegracaoSAP && /caminho/venv/bin/python extract_sap_to_supabase.py >> logs/execution.log 2>&1
```

---

## Operação (iniciar / parar / serviços)

No servidor rodam **dois processos** 24/7:

| Processo | O que faz | Sobe com |
| --- | --- | --- |
| **Agendador** | carga de **oportunidades** a cada 30 min (07–18h, dias úteis) | `run_scheduler.bat` |
| **API / painel** | endpoint + página em `:8077` (OS sob demanda · forçar oportunidades) | `run_api.bat` |

### Iniciar

- **Como serviço (recomendado, 24/7)** — rode **uma vez**, como Administrador, com o
  [NSSM](https://nssm.cc) no PATH:
  ```bat
  install_services.bat
  ```
  Registra `OrcaView-Scheduler` e `OrcaView-OS-API` com **auto-start no boot**, **restart se
  cair** e log em `logs/`. Gerencie em `services.msc`.
- **Manual (teste/temporário)** — cada um isolado em sua janela: `run_scheduler.bat`
  (agendador) e `run_api.bat` (API).

### Parar

- **Serviço:** `nssm stop OrcaView-OS-API` e `nssm stop OrcaView-Scheduler` (ou pelo
  `services.msc`). Parada **limpa** — o agendador **espera** uma carga em andamento terminar.
- **Janela manual:** `Ctrl+C` na janela (a da API ainda pergunta *Terminate batch job? Y*).
  O Ctrl+C de uma janela para **só** aquele processo.

> ⚠️ Não misture: se registrou como serviço, **não** deixe janelas manuais abertas também
> (brigam pela porta 8077).

### Fim do dia / reinício do servidor

- É **24/7** — **não** se encerra no fim do dia. Fora do horário (07–18h), fins de semana e
  feriados, o agendador fica **ocioso** e volta sozinho no próximo dia útil às 07h.
- Se o servidor desligar/reiniciar (backup, Windows Update): como **serviço**, encerra limpo e
  **religa no boot**.
- **Integridade garantida:** OS (`replace_nped`) e Oportunidades (`snapshot`) usam
  *carrega-depois-poda* — se interrompidos no meio, **permanece a versão anterior** (nunca
  tabela vazia/corrompida). O lock de arquivo é liberado quando o processo termina.

### Logs

| Arquivo | Conteúdo |
| --- | --- |
| `logs/scheduled_execution.log` | agendador (rotação diária, 12 dias) |
| `logs/api.log` | API (rotação diária, 12 dias) |
| `logs/scheduler_service.log` · `logs/api_service.log` | saída bruta dos serviços (via NSSM) |

> Os logs são gravados em **UTF-8**. No **PowerShell**, leia com `-Encoding utf8`,
> senão os acentos saem trocados (ex.: `execuÃ§Ã£o`):
> `Get-Content .\logs\scheduled_execution.log -Tail 30 -Encoding utf8`

---

## Monitoramento

Dois endpoints para checagem (exemplos no PowerShell):

- **`GET /health`** — *liveness* leve (a API está de pé?). Sem chave, sem checagem externa —
  rápido e sempre disponível.
- **`GET /status`** — diagnóstico **sob demanda** (**aberto, sem chave** — pode abrir no
  navegador; roda só quando chamado, sem polling): conexões com **SAP**, **SQL Server (WBC)** e **Supabase** (com
  latência `ms`), **sinal indireto do agendador** (idade da última carga de oportunidades;
  `stale` se > 35 min na janela comercial → `OrcaView-Scheduler` pode ter caído), **alerta de
  disco** e métricas do sistema (CPU/memória via `psutil` se instalado; disco/IP/host/uptime
  via stdlib).

```powershell
# diagnóstico completo (aberto — funciona no navegador também)
curl.exe -s "http://192.168.7.11:8077/status" | ConvertFrom-Json

# só algumas checagens (aliases: sql/wbc -> sql_server, hana -> sap, agendador -> scheduler)
curl.exe -s "http://192.168.7.11:8077/status?checks=sap,sql"

# alertar por código de status: 503 se houver falha de conexão OU alerta
curl.exe -s -o NUL -w "%{http_code}" "http://192.168.7.11:8077/status?strict=1"
```

Campos úteis do JSON: `ok` (conexões verdes), `healthy` (`ok` e sem `alerts`),
`checks.*.ms` (latência), `scheduler.stale`, `system.disk_low`, `alerts[]`.

> **No navegador:** `/status` abre direto (`http://192.168.7.11:8077/status`). Os **demais**
> endpoints exigem a chave — no navegador, passe por query string `?key=SUA_CHAVE` (ex.:
> `…/oportunidades/info?key=SUA_CHAVE`). O `-H "X-API-Key: ..."` é parâmetro do **curl**
> (terminal) e **não** funciona colado na barra de endereço do navegador.

> CPU e memória exigem **`psutil`** (`python -m pip install psutil`). Sem ele o `/status`
> funciona normalmente, apenas com CPU/memória indisponíveis.

---

## Versionamento (GitHub)

Repositório: **<https://github.com/MarceloProjetos/oportunidade_wbc>** — o repo no GitHub mantém o nome `oportunidade_wbc`; a pasta local do projeto é `ServidorIntegracaoSAP`.

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
ServidorIntegracaoSAP/
├── config.py                    # Configuração centralizada (.env)
├── sap_connection.py            # Conexão SAP HANA compartilhada
├── db_utils.py                  # Leitura DB-API (SAP/SQL) → DataFrame
├── feriados_br.py               # Calendário de feriados nacionais (até 2030)
├── pipeline_core.py             # Núcleo compartilhado (SupabaseLoader, prepare_data, …)
├── extract_sap_to_supabase.py   # Pipeline de oportunidades (SAP + SQL Server → Supabase)
├── extract_ordens_servico_engenharia.py  # Sync de OS por N_PED (VW_OS_INTEGRACAO, replace_nped)
├── monitoring.py                # Diagnóstico do /status (conexões, agendador, tarefa)
├── api.py                       # API HTTP de disparo + /status (Flask, porta 8077)
├── web/                         # Página servida pela API (sincronizar.html)
├── sql/                         # DDL + policies do Supabase
├── scripts/
│   └── scheduled_execution.py   # Agendamento via APScheduler (IntervalTrigger)
├── mcp/                         # Fachada MCP read-only sobre a API (FastMCP/stdio)
├── maintenance/                 # Manutenção do servidor (limpeza de logs do Azure)
├── monitor_wbc_task.ps1         # Monitora a tarefa "Integração WBC" → state/*.json
├── install_monitor_task.ps1     # Registra a tarefa do monitor (a cada 10 min, SYSTEM)
├── run_scheduler.bat            # Wrapper p/ Task Scheduler / NSSM (agendador, boot 24/7)
├── run_api.bat                  # Wrapper p/ Task Scheduler / NSSM (API, boot 24/7)
├── install_services.bat         # Registra agendador + API como serviços NSSM (boot + restart)
├── requirements.txt             # Dependências Python
├── requirements-dev.txt         # pytest (testes unitários)
├── tests/                       # Suíte pytest
├── .env.example                 # Template de variáveis de ambiente
├── CHANGELOG.md                 # Histórico de mudanças
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
