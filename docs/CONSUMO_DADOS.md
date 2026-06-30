# Consumo dos dados (read-only) — OS de Engenharia + Árvore WBC

Guia para **outro programa/equipe consumir** os dados no Supabase, **somente leitura**.

> 🔑 **Credenciais não ficam neste repositório.** A **URL do projeto** e a **chave `anon`**
> (read-only) são entregues pelo time de backend por canal seguro. A chave de escrita
> (`service_role`) **nunca** é compartilhada. (O handoff com as chaves é um arquivo
> confidencial, fora do git — `HANDOFF_*.md`.)

## Modelo de acesso

- **Ler:** direto no Supabase com a chave **`anon`** — *read-only por construção*: as tabelas
  têm policy só de `SELECT`; `INSERT/UPDATE/DELETE` com a `anon` são **recusados** pelo banco.
- **Criar/atualizar:** **somente** pela API HTTP (`POST /sync/...`), que internamente usa a
  `service_role`. "Atualizar" = **re-sincronizar** o pedido do SAP (substitui as linhas
  **daquele** pedido — não duplica — e dispara também a árvore WBC do orçamento).

## Pré-requisito (uma vez, time de backend)

Rodar no SQL Editor do Supabase os scripts de leitura (liberam só `SELECT` para a `anon`):

- [`sql/ordens_servico_engenharia_read_policy.sql`](../sql/ordens_servico_engenharia_read_policy.sql) (OS)
- [`sql/wbc_arvore_read_policy.sql`](../sql/wbc_arvore_read_policy.sql) (árvore WBC)

Sem eles, a `anon` recebe **0 linhas** (as tabelas nascem trancadas a backend).

## Tabelas

| Tabela | Conteúdo | Leitura `anon` |
|---|---|---|
| `ordens_servico_engenharia` | Linhas da OS por pedido (`NPED`) | ✅ |
| `status_ordens_servico_eng` | Tradução do `Status` (P/R/L/C) | ✅ |
| `wbc_arvore_produto` | Árvore de produto WBC (lista de materiais) por `ORCNUM` | ✅ |
| `sincronizacao_log_os_eng` · `sincronizacao_log_wbc_arvore` | Logs das sincronizações | ❌ (uso interno) |

> ⚠️ **Nomes de coluna são *case-sensitive*** (vieram do SAP / SQL Server WBC). Use
> exatamente como na DDL. Lista completa de colunas e tipos:
> [`sql/ordens_servico_engenharia.sql`](../sql/ordens_servico_engenharia.sql) e
> [`sql/wbc_arvore.sql`](../sql/wbc_arvore.sql).

### `ordens_servico_engenharia` — 1 linha por item de estrutura/OP (filtre por `NPED`)

Campos principais (a DDL tem os 58 + controle):

- **Pedido:** `id` (PK), `N_OP`, **`NPED`**, `CodItemPED`/`DescItemPED`, `QtdPlan`/`QtdConcl`/`QtdRejeit`,
  `DtPedido`/`DtInic`/`DtVenc`/`DtEncerr`/`DtLiber`, `CodClien`/`NomeClien`, `NomedVend`, `Status`, `Deposito`/`UM`.
- **Estrutura (BOM):** `CodItemEstrut`/`DescItemEstrut`, `QtdBasEstrut`/`QtdSaida`/`QtdLiberEstrut`, `PesoEstrut`, `TipoItemEstrut`.
- **Orçamento:** **`CodigoOrcam`** (= `ORCNUM` da árvore WBC), `NºOrçament`, `CodItemOrcam`, `DescProdOrcam`, `QtdOrcam`/`PrecoOrcam`/`TotalOrcam`, `U_INO_VERSAOWBC`/`U_INO_PROJETO`.
- **Textos longos:** `TextoLivPED`, `InfoAdicPED`, `ComposicaoPED`, `AcabamentoPED`, `ObsPedido`, … (podem ser grandes).
- **Controle:** `id_execucao` (uuid da carga), `data_hora_extracao`, `origem_view`, `inserted_at`.

### `wbc_arvore_produto` — 1 linha por item da árvore (filtre por `ORCNUM`)

| Coluna | Tipo | Descrição |
|---|---|---|
| `id` | bigint (PK) | Chave técnica da linha |
| `ORCNUM` | text | **Código do orçamento WBC** (chave de filtro) |
| `GRPCOD` / `SUBGRPCOD` | integer | Grupo / subgrupo |
| `ORCITM` | integer | Item do orçamento |
| `ORCPRDARV_NIVEL` | integer | Nível na árvore (1 = item; 2 = componente; …) |
| `PRDCOD` | text | Código do produto |
| `CORCOD` | text | Código de cor |
| `PRDDSC` | text | Descrição do produto |
| `ORCQTD` | numeric | Quantidade |
| `ORCTOT` | numeric | Valor total |
| `ORCPES` | numeric | Peso |
| `idIntegracao_OrcPrdArv` | integer | PK na origem (referência) |
| `orcprdarv_dth` | timestamp | Data/hora do registro na origem |
| `id_execucao` | uuid | Identifica a carga (todas as linhas de uma sync têm o mesmo) |
| `data_hora_extracao` | timestamp | Quando o `ORCNUM` foi sincronizado pela última vez |
| `origem_view` | text | `WBCCAD.dbo.INTEGRACAO_ORCPRDARV` |
| `inserted_at` | timestamptz | Carimbo do banco no insert |

### Ligar pedido (`NPED`) → árvore WBC

A árvore é por **`ORCNUM`**, não por `NPED`. Para um pedido:

1. `ordens_servico_engenharia?NPED=eq.<nped>&select=CodigoOrcam` → pega o **`CodigoOrcam`** (= `ORCNUM`, ex.: `00124853`).
2. `wbc_arvore_produto?ORCNUM=eq.<orcnum>&select=*&order=id.asc` → linhas da árvore daquele orçamento.

## Como ler (exemplos)

> Substitua `<URL>` e `<ANON>` pela URL do projeto e pela chave `anon` que o backend te passar.

### REST (curl)

```bash
ANON="<ANON>"; BASE="<URL>/rest/v1"

# OS de um pedido
curl "$BASE/ordens_servico_engenharia?NPED=eq.84080&select=*" \
  -H "apikey: $ANON" -H "Authorization: Bearer $ANON"

# Árvore WBC de um orçamento (ORCNUM vem de ordens_servico_engenharia.CodigoOrcam)
curl "$BASE/wbc_arvore_produto?ORCNUM=eq.00124853&select=*&order=id.asc" \
  -H "apikey: $ANON" -H "Authorization: Bearer $ANON"
```

> **Filtros PostgREST:** `?coluna=eq.valor`, `gt`/`lt`/`gte`/`lte`, `like`, `in.(a,b)`.
> **Paginação:** máx. **1000 linhas** por resposta — use `?limit=1000&offset=0`.

### JavaScript (`@supabase/supabase-js`)

```js
import { createClient } from '@supabase/supabase-js'
const supabase = createClient('<URL>', '<ANON>')

// OS de um pedido
const { data: os } = await supabase
  .from('ordens_servico_engenharia')
  .select('NPED, N_OP, NomeClien, Status, TotalOrcam, CodigoOrcam')
  .eq('NPED', 84080).order('id', { ascending: true })

// Árvore WBC (2 passos)
const orcnum = os?.[0]?.CodigoOrcam            // ex.: '00124853'
const { data: arvore } = await supabase
  .from('wbc_arvore_produto')
  .select('ORCNUM, ORCPRDARV_NIVEL, PRDCOD, PRDDSC, ORCQTD, ORCTOT, ORCPES')
  .eq('ORCNUM', orcnum).order('id', { ascending: true })
```

### Python (`supabase`)

```python
from supabase import create_client
sb = create_client("<URL>", "<ANON>")

os_rows = (sb.table("ordens_servico_engenharia")
             .select("*").eq("NPED", 84080).order("id").execute().data)

orcnum = os_rows[0]["CodigoOrcam"] if os_rows else None   # ex.: '00124853'
arvore = (sb.table("wbc_arvore_produto")
            .select("ORCNUM, ORCPRDARV_NIVEL, PRDCOD, PRDDSC, ORCQTD, ORCTOT, ORCPES")
            .eq("ORCNUM", orcnum).order("id").execute().data)
```

## Views de relatório (adaptação da `VW_OS_EXPED_IMPRESSAO_V2`)

Em [`sql/vw_os_exped_impressao.sql`](../sql/vw_os_exped_impressao.sql) há duas views prontas
(rode no SQL Editor; depois `grant select ... to anon` se for ler as views direto):

- **`vw_os_exped_impressao`** — adapta a `VW_OS_EXPED_IMPRESSAO_V2` (ramo EXP, nível 1). Vem só
  de `ordens_servico_engenharia` (não junta a árvore → não multiplica linhas).
- **`vw_os_exped_arvore`** — detalhe da árvore WBC: **1 linha por componente** (todos os
  níveis) com o cabeçalho do pedido. Junta por `CodigoOrcam = ORCNUM`.

> Campos de **Filial/endereço** (OBPL), **GrpMaterialEstrut/GrpItensEstrut** (OITM) e o ramo
> **ALMX** vêm de fontes não espelhadas → saem **NULL**. `U_INO_NIVEL` na view fiel é `'1'`
> (a V2 só traz nível 1).

## Regras (resumo)

1. **Ler** → chave **`anon`**, direto no Supabase (read-only).
2. **Criar/atualizar** → **API** (`POST /sync/...`) com `X-API-Key`. Nunca escrever direto no banco.
3. **Nunca** pedir/usar a `service_role`.
4. Colunas são **case-sensitive**.
5. **Paginar** leituras grandes (máx. 1000 linhas/requisição).
6. Tratar o JSON com parser (textos têm acentos).

> Não acumula histórico: cada re-sync **substitui** o pedido (`ordens_servico_engenharia` por
> `NPED`; `wbc_arvore_produto` por `ORCNUM`) — sempre a versão atual, sem duplicatas.
