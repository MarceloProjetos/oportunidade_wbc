# Plano — Sincronização de Ordens de Serviço de Engenharia (VW_EXPORT_ORDENS_SERVICO_1 → Supabase)

> **Status:** proposta para discussão (nada implementado ainda).
> **Projeto:** `oportunidade_wbc`
> **Autor do plano:** Claude (Opus 4.8) — 2026-06-25
> **Origem:** notebook Jupyter de teste (consulta a `VW_EXPORT_ORDENS_SERVICO_1` filtrando por `NPED`).

---

## 1. Objetivo

Criar, **dentro do projeto `oportunidade_wbc`**, um segundo pipeline de ETL que:

1. Lê a view **`SBOALTAMIRAPROD.VW_EXPORT_ORDENS_SERVICO_1`** do SAP B1 (HANA), **filtrando por um `NPED` selecionado**;
2. Carrega os dados numa **nova tabela do Supabase** (`ordens_servico_engenharia`) com **os mesmos campos** da view;
3. Acrescenta **campos de controle de sincronização** (data/hora, id de execução, etc.);
4. Roda **sob demanda** (por NPED), reaproveitando o núcleo do projeto, **sem afetar** o pipeline de `oportunidades`.

---

## 2. Decisões tomadas (conversa de 2026-06-25)

| # | Tema | Decisão |
|---|---|---|
| 1 | **Escopo** | **Somente o NPED selecionado** (carga por pedido, não a view inteira). |
| 2 | **Arquitetura** | Módulo separado + **`pipeline_core.py`** (núcleo compartilhado). |
| 3 | **Colunas** | **Manter todas** as 58 colunas (incl. textos grandes `NCLOB`). Haverá exportação posterior p/ JSON → ver §9 (não é problema). |
| 4 | **`Status`** | Criar tabela de tradução. Códigos reais na view: `P`, `R`, `L` (ver §5.4). |
| 5 | **Execução** | **Sob demanda** (CLI / chamada de função por NPED). Sem agendador. |
| 6 | **Nome da tabela** | **`ordens_servico_engenharia`**. |

---

## 3. Descobertas da inspeção da view (read-only, 2026-06-25)

| Métrica | Valor |
|---|---|
| Conexão | `192.168.7.10:30015` = host `SAPBusinessOneHana-vm` do `.env` (mesma HANA/schema/usuário do pipeline atual) |
| Schema / View | `SBOALTAMIRAPROD.VW_EXPORT_ORDENS_SERVICO_1` |
| Colunas | **58** |
| Linhas totais | 31.110 |
| NPEDs distintos | 2.386 (~13 linhas por pedido — granularidade de **linha de estrutura/OP**) |
| Linhas do NPED 84080 | **383** (Status `R`) |
| Colunas de data | `DtPedido`, `DtVenc`, `DtInic`, `DtEncerr`, `DtLiber`, `DataEntrega`, `DtEntregaPED` |
| Colunas de texto grande | 6× `NCLOB` (até 2 GB) + `InfoAdicPED2` `NVARCHAR(5000)` |
| Distribuição de `Status` | `L` 28.401 linhas / 2.273 NPEDs · `R` 2.064 / 75 · `P` 645 / 38 |

**Implicações:**

- Como a carga é **por NPED** (≈100–400 linhas/pedido), volume e payload deixam de ser preocupação.
- Nomes com acento/símbolo (`NºOrçament`) exigem **identificadores entre aspas** no Postgres — padrão já adotado no projeto.
- Não há chave única natural na view; com carga por NPED, a **chave lógica de substituição é o `NPED`** (ver §8).

---

## 4. Arquitetura proposta

```text
                 ┌───────────────────────────────────────────────┐
                 │  config.py  (+ campos OS_*)                    │
                 │  sap_connection.py   db_utils.py               │
                 └───────────────────────────────────────────────┘
                                  ▲                 ▲
        ┌─────────────────────────┴───┐     ┌───────┴───────────────────────┐
        │ pipeline_core.py  (NOVO)    │     │  movido de                     │
        │  • SupabaseLoader           │◄────┤  extract_sap_to_supabase.py    │
        │  • prepare_data             │     │  (sem mudança de comportamento)│
        │  • with_retries             │     └────────────────────────────────┘
        │  • build_view_query         │
        │  • validate_sql_identifier  │
        └──────────┬──────────────────┘
                   │                              ┌───────────────────────────────┐
   ┌───────────────┴────────────────────┐         │ extract_sap_to_supabase.py    │
   │ extract_ordens_servico_engenharia.py│         │ (oportunidades — INALTERADO   │
   │  (NOVO)                             │         │  no comportamento; passa a    │
   │  • extract_os_to_dataframe(nped)    │         │  importar do pipeline_core)   │
   │  • main(nped=…)  → substitui por NPED│        └───────────────────────────────┘
   │  • SEM SQL Server / SEM SITCOD      │
   └───────────────┬────────────────────┘
                   ▼
        ┌──────────────────────────────────────┐
        │  Supabase (PostgreSQL)               │
        │  public.ordens_servico_engenharia    │
        │  public.status_ordens_servico_eng    │  (lookup de Status)
        │  public.sincronizacao_log_os_eng     │  (log)
        └──────────────────────────────────────┘
```

**Núcleo compartilhado (`pipeline_core.py`)** — recebe as funções **já genéricas** de `extract_sap_to_supabase.py` (movidas; o arquivo de oportunidades passa a importá-las — mudança *behavior-preserving*, coberta pelos testes atuais):
`SupabaseLoader`, `prepare_data`, `with_retries`, `build_view_query`, `validate_sql_identifier`.

**Fora do pipeline de OS** (continua intacto no arquivo de oportunidades): `get_sqlserver_connection`, `extract_sqlserver_view`, `query_sqlserver_view`, `validate_sitcod_fk`, `_sitcod_as_int` e o bloco de *enrichment* SQL Server.

---

## 5. Modelo de dados (Supabase / PostgreSQL)

### 5.1 Tabela principal `public.ordens_servico_engenharia`

DDL gerado a partir dos **tipos reais** da view (mapeamento HANA→Postgres no Anexo A). Identificadores entre aspas para preservar o *case* exato (exigência do PostgREST na inserção).

```sql
create table public.ordens_servico_engenharia (
  id                   bigint generated always as identity primary key,

  -- ===== Campos da view VW_EXPORT_ORDENS_SERVICO_1 (58 colunas) =====
  "N_OP"               integer,
  "NPED"               integer,
  "CodItemPED"         text,
  "DescItemPED"        text,
  "QtdPlan"            numeric(21,6),
  "QtdConcl"           numeric(21,6),
  "QtdRejeit"          numeric(21,6),
  "DtPedido"           timestamp,
  "DiasTotal"          integer,
  "DtVenc"             timestamp,
  "DtInic"             timestamp,
  "LinhRef"            text,
  "Obs"                text,
  "DtEncerr"           timestamp,
  "DtLiber"            timestamp,
  "CodClien"           text,
  "NomeClien"          text,
  "NomedVend"          text,
  "Status"             text,
  "Deposito"           text,
  "UM"                 text,
  "LinhEstrut"         integer,
  "CodItemEstrut"      text,
  "DescItemEstrut"     text,
  "QtdBasEstrut"       numeric(21,6),
  "QtdPlanEstrut"      numeric,
  "QtdSaida"           numeric(21,6),
  "TipoEmissOP"        text,
  "DeposEstrut"        text,
  "LinhVisEstrut"      integer,
  "TipoItemEstrut"     integer,
  "QtdLiberEstrut"     numeric(21,6),
  "PesoEstrut"         numeric(21,6),
  "TextoLivPED"        text,
  "InfoAdicPED"        text,
  "InfoAdicPED2"       text,
  "ComposicaoPED"      text,
  "MATExistPED"        text,
  "AcabamentoPED"      text,
  "CapacidadePED"      text,
  "CordPED"            text,
  "ObsImpostOrcamento" text,
  "CodDetalhOrcamento" integer,
  "ObsPedido"          text,
  "NºOrçament"         text,
  "DataEntrega"        timestamp,
  "PesoOrcam"          numeric(21,6),
  "CodItemOrcam"       text,
  "QtdOrcam"           numeric(21,6),
  "DescProdOrcam"      text,
  "CorOrcam"           text,
  "PrecoOrcam"         numeric(21,6),
  "TotalOrcam"         numeric(21,6),
  "Usuario"            text,
  "DtEntregaPED"       timestamp,
  "CodigoOrcam"        text,
  "U_INO_VERSAOWBC"    text,
  "U_INO_PROJETO"      text,

  -- ===== Campos de controle / auditoria (adicionados pelo pipeline) =====
  id_execucao          uuid,          -- UUID da carga (agrupa as linhas daquele sync de NPED)
  data_hora_extracao   timestamp,     -- horário do servidor ao extrair (igual a oportunidades)
  origem_view          text default 'VW_EXPORT_ORDENS_SERVICO_1',
  inserted_at          timestamptz default now()
);

-- Índices: NPED é a chave de substituição/consulta; id_execucao acelera a poda
create index idx_os_eng_nped        on public.ordens_servico_engenharia ("NPED");
create index idx_os_eng_nop         on public.ordens_servico_engenharia ("N_OP");
create index idx_os_eng_codclien    on public.ordens_servico_engenharia ("CodClien");
create index idx_os_eng_status      on public.ordens_servico_engenharia ("Status");
create index idx_os_eng_id_execucao on public.ordens_servico_engenharia (id_execucao);

-- Segurança (decisão: leitura SOMENTE backend/scripts): RLS ENABLE + FORCE,
-- SEM nenhuma policy → só o service_role (BYPASSRLS) acessa. Ver §5.5.
alter table public.ordens_servico_engenharia enable row level security;
alter table public.ordens_servico_engenharia force row level security;
```

### 5.2 Campos de controle — justificativa

| Campo | Tipo | Por quê |
|---|---|---|
| `id_execucao` | `uuid` | Agrupa as linhas de uma carga de um NPED. Usado pela poda (substituição por NPED). |
| `data_hora_extracao` | `timestamp` | Quando aquele NPED foi sincronizado pela última vez. |
| `origem_view` | `text` | Proveniência (default fixo). |
| `inserted_at` | `timestamptz default now()` | Carimbo do banco no INSERT. |

### 5.3 Tabela de log `public.sincronizacao_log_os_eng`

Mesma mecânica do `sincronizacao_log` de oportunidades, **separada** e com um campo `nped` para saber qual pedido foi sincronizado.

```sql
create table public.sincronizacao_log_os_eng (
  id                       bigint generated always as identity primary key,
  data_hora_sincronizacao  timestamptz,
  nped                     integer,     -- pedido sincronizado nesta execução
  duracao_segundos         numeric(10,2),
  status                   text,        -- 'sucesso' | 'falha'
  qtd_registros            integer
);

alter table public.sincronizacao_log_os_eng enable row level security;
alter table public.sincronizacao_log_os_eng force row level security;  -- sem policy (ver §5.5)
```

### 5.4 Tabela de tradução de `Status`  `public.status_ordens_servico_eng`

Códigos **reais** encontrados na view: `P`, `R`, `L` (não há `C`). Texto conforme padrão SAP B1; o `C` é semeado para o caso de aparecer no futuro.

```sql
create table public.status_ordens_servico_eng (
  codigo    text primary key,
  descricao text not null
);

insert into public.status_ordens_servico_eng (codigo, descricao) values
  ('P', 'Planejado'),
  ('R', 'Liberado'),       -- Released (em produção)
  ('L', 'Encerrado'),      -- Closed/Fechado  ← você citou "cancelado"; confirmar (ver §13)
  ('C', 'Cancelado')       -- não aparece na view hoje
on conflict (codigo) do nothing;

alter table public.status_ordens_servico_eng enable row level security;
alter table public.status_ordens_servico_eng force row level security;  -- sem policy (ver §5.5)
```

> **Decisão de robustez:** será um **lookup de leitura** (join na hora de consultar/exportar), **sem FK rígida** na tabela principal. Assim, se o SAP introduzir um novo código de status, a carga **não quebra** (diferente do FK de `SITCOD` em oportunidades, que exige validação dinâmica). Se preferir o FK rígido (como em oportunidades), dá para adicionar.

Consulta com a descrição:

```sql
select o.*, s.descricao as status_desc
from public.ordens_servico_engenharia o
left join public.status_ordens_servico_eng s on s.codigo = o."Status"
where o."NPED" = 84080;
```

### 5.5 Segurança / RLS (lições do Security Advisor)

Decisão: **leitura somente backend/scripts**. As 3 tabelas usam **RLS `ENABLE` + `FORCE`
e NENHUMA policy** — só o `service_role` (que tem `BYPASSRLS`, e é a chave usada pelo
pipeline) lê/escreve; `anon` e `authenticated` ficam **sem acesso**. O SQL Editor (role
`postgres`, `BYPASSRLS`) ainda consulta normalmente para conferência.

Como isso evita os alertas do Advisor vistos em produção:

| Alerta do Advisor | Como evitamos |
|---|---|
| **RLS Policy Always True** (policies de escrita `USING/WITH CHECK (true)`) | Não criamos **nenhuma** policy de escrita — escrita é via `service_role`. Também não criamos `SELECT ... using(true)` (que, embora o Advisor ignore, exporia dados ao `anon`). |
| **Extension in Public** | Não criamos extensões. |
| **SECURITY DEFINER / function_search_path** | Não criamos funções nem triggers (linhas são **substituídas**, não atualizadas; `inserted_at default now()` basta — sem `updated_at`/trigger). |

> **DDL executável (fonte da verdade):** [sql/ordens_servico_engenharia.sql](sql/ordens_servico_engenharia.sql),
> já endurecido (ENABLE+FORCE, seed do status **antes** do RLS, `COMMENT`s e bloco de
> verificação). O Advisor pode exibir um INFO "RLS enabled, no policy" — é **intencional**
> aqui; se um dia um app precisar ler, adiciona-se uma policy de `SELECT` para a role certa.

---

## 6. Configuração (`config.py` / `.env`)

Acréscimos **aditivos** (não alteram nada do que já existe), todos com default.

| Variável `.env` | Default | Descrição |
|---|---|---|
| `OS_SAP_VIEW_NAME` | `VW_EXPORT_ORDENS_SERVICO_1` | View de origem. |
| `OS_TABLE_NAME` | `ordens_servico_engenharia` | Tabela destino. |
| `OS_STATUS_TABLE_NAME` | `status_ordens_servico_eng` | Lookup de status. |
| `OS_SYNC_LOG_TABLE_NAME` | `sincronizacao_log_os_eng` | Tabela de log. |
| `OS_EXECUTION_MODE` | `replace_nped` | `replace_nped` (substitui o pedido) ou `insert` (acumula histórico). |
| `OS_INSERT_BATCH_SIZE` | `200` | Lote de inserção (linhas largas com NCLOB). |

A conexão SAP é **a mesma** já configurada — nenhuma credencial nova.

---

## 7. Novos arquivos / mudanças de código

| Arquivo | Ação | Conteúdo |
|---|---|---|
| `pipeline_core.py` | **novo** | `SupabaseLoader`, `prepare_data`, `with_retries`, `build_view_query`, `validate_sql_identifier` (movidos). |
| `extract_sap_to_supabase.py` | **editar** | Importa do `pipeline_core` (sem mudar comportamento). |
| `extract_ordens_servico_engenharia.py` | **novo** | Pipeline de OS por NPED: `extract_os_to_dataframe(nped)` + `main(nped)`. |
| `config.py` | **editar** | Campos `OS_*` (aditivo). |
| `sql/ordens_servico_engenharia.sql` | **novo** | DDL das §5.1–5.4 para o SQL Editor do Supabase. |
| `tests/test_ordens_servico_eng.py` | **novo** | Testes unitários (mocks; sem credenciais reais). |
| `scripts/test_connections.py` | **editar** | Checagem opcional da tabela `ordens_servico_engenharia`. |
| `README.md` / `.env.example` | **editar** | Documentar o pipeline de OS e as variáveis `OS_*`. |

### 7.1 Esboço de `extract_ordens_servico_engenharia.py`

```python
"""ETL sob demanda: VW_EXPORT_ORDENS_SERVICO_1 (por NPED) → Supabase."""
import sys, time, uuid
from datetime import datetime
from pipeline_core import SupabaseLoader, prepare_data, build_view_query
from sap_connection import SAPExtractor
from config import get_settings

def extract_os_to_dataframe(nped: int):
    s = get_settings()
    nped = int(nped)                       # valida/sanitiza (NPED é INTEGER)
    sap = SAPExtractor(s.sap_host, s.sap_port, s.sap_user, s.sap_password, s.sap_database)
    if not sap.connect():
        return None
    base = build_view_query(s.os_sap_view_name, s.sap_schema)
    df = sap.execute_query(f'SELECT * FROM {base} WHERE "NPED" = {nped}')
    sap.close()
    return df

def main(nped: int, execution_mode: str = 'replace_nped', execution_id=None) -> bool:
    s = get_settings()
    inicio = time.monotonic(); resultado = False; qtd = 0
    loader = None
    try:
        df = extract_os_to_dataframe(nped)
        if df is None:
            return False
        if len(df) == 0:
            # pedido inexistente/vazio na view → NÃO apaga o que já existe (evita wipe acidental)
            print(f'NPED {nped}: 0 linhas na view; nada alterado.')
            return False
        loader = SupabaseLoader(s.supabase_url, s.supabase_write_key)
        data, exec_id = prepare_data(df, execution_id)   # injeta id_execucao + data_hora_extracao
        qtd = len(data)
        ok = loader.insert_data(s.os_table_name, data, batch_size=s.os_insert_batch_size)
        if ok and execution_mode == 'replace_nped':
            # carrega-depois-poda, ESCOPADO ao NPED: remove só as linhas antigas deste pedido
            loader.client.table(s.os_table_name)\
                  .delete().eq('NPED', int(nped)).neq('id_execucao', exec_id).execute()
        resultado = ok
        return ok
    finally:
        # log isolado (nunca afeta o resultado), com o nped
        ...

if __name__ == '__main__':
    npeds = [int(a) for a in sys.argv[1:]] or [84080]   # uso: python ... 84080 [84095 ...]
    for n in npeds:
        main(n)
```

Reaproveita `prepare_data` (controle) e `SupabaseLoader` (insert em lotes + log). A poda do `replace_nped` adiciona um método pequeno no `SupabaseLoader` (`delete_nped(table, nped, keep_execution_id)`) — ou usa o `.delete().eq(...).neq(...)` direto, como acima.

---

## 8. Estratégia de carga — `replace_nped` (substituição por pedido)

Carga **sob demanda** para um (ou vários) NPED:

1. Extrai `SELECT * FROM view WHERE "NPED" = :nped` (NPED validado como inteiro).
2. Se vier **0 linhas** → aborta sem apagar nada (evita wipe acidental de um pedido válido já carregado).
3. `prepare_data` injeta `id_execucao` (UUID) + `data_hora_extracao` e normaliza tipos/NaN.
4. **Insere** as novas linhas (lote de 200, com retry).
5. **Só após** a inserção, **apaga as linhas antigas daquele NPED** (`where "NPED" = :nped and id_execucao <> atual`). Se a inserção falhar, o pedido continua com os dados anteriores — nunca fica vazio.
6. Registra em `sincronizacao_log_os_eng` (com o `nped`).

Resultado: a tabela **acumula vários pedidos**, cada um **atualizável independentemente** sob demanda. Rodar de novo o mesmo NPED = refresh daquele pedido; rodar outro NPED não mexe nos demais.

> **Modo alternativo `insert`** (config): apenas insere, acumulando histórico de cargas por `id_execucao` (sem apagar) — útil se quiser trilha de auditoria/evolução do mesmo pedido.

---

## 9. Exportação posterior para JSON — não é problema (manter todas as colunas)

Manter as 58 colunas, incluindo os `NCLOB`, **não atrapalha** a exportação para JSON. Pontos de atenção (todos tratáveis):

1. **Caracteres especiais** nos textos grandes (`\par`, quebras de linha, aspas, acentos) são **escapados automaticamente** por qualquer serializador JSON de verdade (`json.dumps`, PostgREST/Supabase). Consumir sempre com um parser JSON — nunca "montar string na mão".
2. **Acentos legíveis:** exportar em UTF-8 com `ensure_ascii=False` (senão viram `\uXXXX` — ainda válido, mas ilegível).
3. **`NaN`/`NaT` não são JSON válido** → o pipeline já converte para `null` no `prepare_data`. Manter isso na exportação.
4. **Precisão de decimais:** `numeric(21,6)` exportado como número vira `float` (risco mínimo de arredondamento). Se a exatidão for crítica (preço/peso conferindo centavo a centavo), exportar esses campos como **string**.
5. **Tamanho:** por NPED são centenas de linhas → JSON pequeno. Se um dia exportar a tabela inteira, os `NCLOB` repetidos aumentam o arquivo (mitigável omitindo colunas grandes na exportação, sem precisar removê-las da tabela).

> Como a tabela guarda os dados crus e fiéis à view, a exportação JSON é só um `select` + serialização. Posso entregar junto um utilitário `export_os_json.py` (por NPED ou geral) quando chegarmos lá.

---

## 10. Execução sob demanda

- **CLI:** `python extract_ordens_servico_engenharia.py 84080` (ou vários: `... 84080 84095`).
- **Programático:** `from extract_ordens_servico_engenharia import main; main(84080)`.
- **Futuro (opcional):** expor como endpoint no projeto web (`web_orcaview`) ou agendar — a base fica pronta; hoje fica **sob demanda**, conforme decidido.

---

## 11. Riscos e mitigação

| Risco | Sev. | Mitigação |
|---|---|---|
| Nomes com acento/símbolo (`NºOrçament`) confundirem o PostgREST na inserção. | Média | Coluna criada com aspas no *case* exato; **validar na carga de teste do NPED 84080**. Plano B: `df.rename` p/ nome ASCII-safe. |
| Extração retornar 0 linhas e apagar um pedido válido. | Média | Passo 2 do §8: 0 linhas → aborta sem deletar. |
| `Status='L'` significar "Encerrado" e não "Cancelado". | Baixa | Lookup editável; confirmar texto (§13). Sem FK rígida → não quebra carga. |
| Refator mover funções p/ `pipeline_core.py` quebrar oportunidades. | Baixa | Mudança *behavior-preserving*; rodar `pytest` antes/depois. |
| `TIMESTAMP(7)` / `numeric(34)`. | Baixa | `timestamp`/`numeric` absorvem; `prepare_data` já serializa `Decimal`/datas. |
| Injeção via NPED. | Baixa | NPED convertido para `int` antes de montar a query. |
| Tabela em projeto Supabase compartilhado. | Baixa | Só **criação** de objetos novos + leitura `anon`; escrita via `service_role`. |

---

## 12. Passo a passo de implementação

**Fase 0 — Preparação** *(feito)*: inspeção da view + códigos de `Status`.

**Fase 1 — Banco (Supabase)**
- [ ] `sql/ordens_servico_engenharia.sql` (DDL §5.1–5.4) e executar no SQL Editor.

**Fase 2 — Núcleo compartilhado**
- [ ] Criar `pipeline_core.py`; ajustar imports em `extract_sap_to_supabase.py`; `pytest` verde.

**Fase 3 — Pipeline de OS**
- [ ] `config.py`: campos `OS_*`.
- [ ] `extract_ordens_servico_engenharia.py` + método de poda por NPED no `SupabaseLoader`.
- [ ] `tests/test_ordens_servico_eng.py`; `pytest`.

**Fase 4 — Carga de teste**
- [ ] `python extract_ordens_servico_engenharia.py 84080` → validar (§13.2/§14).

**Fase 5 — Docs**
- [ ] README, `.env.example`, `test_connections.py`.

**Fase 6 — Commit**
- [ ] `git status` (sem `.env`), commit, push.

---

## 13. Pendências a confirmar com você

1. **Texto do `Status='L'`** — confirmo `Encerrado` (padrão SAP B1 = Closed)? Você citou "cancelado", mas `C` não existe na view; `L` é o status de fechamento.
2. **Vários NPEDs por execução** — aceitar uma lista (`... 84080 84095`) além de um único? (Recomendo sim, custa pouco.)
3. **Refator:** criar `pipeline_core.py` (recomendado) — confirmado pela decisão #2.
4. **FK de Status:** manter **lookup sem FK** (recomendado, robusto) ou FK rígida como em oportunidades?

---

## Anexo A — Mapa de tipos HANA → PostgreSQL

| HANA | PostgreSQL | Observação |
|---|---|---|
| `INTEGER` | `integer` | |
| `DECIMAL(p,s)` | `numeric(p,s)` | `DECIMAL(34)` sem escala → `numeric` |
| `NVARCHAR`/`VARCHAR` | `text` | tamanho não restringido (fidelidade + simplicidade) |
| `NCLOB`/`CLOB` | `text` | textos grandes (até 2 GB no HANA) |
| `TIMESTAMP` | `timestamp` | sem timezone, igual a oportunidades |

## Anexo B — Query de extração

```sql
-- Produção (sob demanda, por NPED):
SELECT * FROM "SBOALTAMIRAPROD"."VW_EXPORT_ORDENS_SERVICO_1" WHERE "NPED" = 84080;
```
