# Consumo (read-only) — Views de impressão de OS (EXPED · PINTURA · ALMOX)

Guia rápido para **outra equipe consumir** as 3 novas tabelas no Supabase, **somente leitura**.
Elas são o espelho **direto** de 3 views do SAP HANA, atualizadas quando um pedido é
sincronizado. Complementa o guia geral em [`CONSUMO_DADOS.md`](CONSUMO_DADOS.md).

> 🔑 A **URL do projeto** e a **chave `anon`** (read-only) são entregues pelo backend por
> canal seguro. A `service_role` (escrita) **nunca** é compartilhada.

## Modelo de acesso

- **Ler:** direto no Supabase com a chave **`anon`** — *read-only por construção* (as tabelas
  têm policy só de `SELECT`; `INSERT/UPDATE/DELETE` com a `anon` são recusados pelo banco).
- **Atualizar:** você não escreve. Os dados aparecem/mudam quando o backend **sincroniza o
  pedido** (botão *Sincronizar* / `POST /sync/...`). Cada re-sync **substitui** as linhas
  **daquele `NPED`** (não duplica).
- **Filtro sempre por `NPED`** (o número do pedido, inteiro).

## As 3 tabelas

| Tabela (Supabase) | View de origem (HANA) | Conteúdo | Colunas | Leitura `anon` |
|---|---|---|---|---|
| `vw_os_exped_impressao_v2` | `VW_OS_EXPED_IMPRESSAO_V2` | Expedição (ramo EXP) — cabeçalho + estrutura da OS | 55 | ✅ |
| `vw_os_pintura_v0` | `VW_OS_PINTURA_V0` | Pintura — mesma estrutura da EXPED | 55 | ✅ |
| `vw_os_almox_impressao` | `VW_OS_ALMOX_IMPRESSAO` | Almoxarifado — versão enxuta (só orçamento/cliente) | 34 | ✅ |
| `sincronizacao_log_os_impressao` | — | Log das sincronizações | — | ❌ (uso interno) |

> Exemplo real de volume (pedido **84080**): EXPED **374** linhas · PINTURA **328** · ALMOX **14**.
> Cada linha tem `id` (PK) e os campos de controle `id_execucao`, `data_hora_extracao`,
> `origem_view`, `inserted_at`.

### ⚠️ Duas pegadinhas de nome de coluna (leia antes de codar)

1. **Colunas são *case-sensitive*** e **algumas têm espaço** — precisam de **aspas** quando
   você seleciona campos específicos: `"Tipo Logradouro"`, `"Rua Filial"`, `"NFilial"`,
   `"Complemento Filial"`, `"CEP Filial"`, `"Bairro Filial"`, `"Cidade Filial"`,
   `"Estado Filial"`, `"CNPJ Filial"`, `"IE Filial"`. Com `select=*` **não** precisa de aspas.
2. **A ALMOX usa `CodCli`** para o código do cliente; **EXPED e PINTURA usam `CodClien`**.

### Campos principais

**`vw_os_exped_impressao_v2` e `vw_os_pintura_v0`** (mesma estrutura):

- **Pedido/cliente:** `TIPO`, `NPED`, `CodClien`, `NomeClien`, `NomedVend`, `Status`,
  `DtPedido`, `DtVenc`, `DtInic`, `DtEncerr`, `DtLiber`, `DtEntregaPED`, `DiasTotal`,
  `Obs`, `ObsPedido`, `Usuario`, `Deposito`, `UM`.
- **Estrutura (BOM da OS):** `CodItemEstrut`, `DescItemEstrut`, `GrpMaterialEstrut`,
  `GrpItensEstrut`, `QtdBasEstrut`, `QtdPlanEstrut`, `QtdSaida`, `QtdLiberEstrut`,
  `PesoEstrut`, `TipoEmissOP`, `DeposEstrut`, `TipoItemEstrut`.
- **Orçamento:** `CodigoOrcam`, `U_INO_COD`, `CodDetalhOrcamento`, `CodItemOrcam`,
  `DescProdOrcam`, `CorOrcam`, `QtdOrcam`, `PesoOrcam`, `PrecoOrcam`, `TotalOrcam`,
  `U_INO_NIVEL`, `U_INO_VERSAOWBC`, `U_INO_PROJETO`.
- **Filial/endereço:** `Filial`, `"Tipo Logradouro"`, `"Rua Filial"`, `"NFilial"`,
  `"Complemento Filial"`, `"CEP Filial"`, `"Bairro Filial"`, `"Cidade Filial"`,
  `"Estado Filial"`, `"CNPJ Filial"`, `"IE Filial"`, `Matriz`.

**`vw_os_almox_impressao`** (34 col — sem `TIPO`/estrutura/`U_INO_NIVEL`):
`NPED`, `DtVenc`, **`CodCli`**, `NomeClien`, `NomedVend`, `DtPedido`, `DiasTotal`,
`U_INO_COD`, `CodDetalhOrcamento`, `DtEntregaPED`, `ObsPedido`, `CodigoOrcam`,
`PesoOrcam`, `CodItemOrcam`, `QtdOrcam`, `DescProdOrcam`, `CorOrcam`, `PrecoOrcam`,
`TotalOrcam`, `Filial` + bloco de endereço (`"CEP Filial"` etc.), `Matriz`, `Usuario`,
`U_INO_VERSAOWBC`, `U_INO_PROJETO`.

> Lista completa de colunas e tipos: [`sql/os_impressao_views.sql`](../sql/os_impressao_views.sql).

## Como ler (exemplos)

> Substitua `<URL>` e `<ANON>` pela URL do projeto e pela chave `anon` que o backend te passar.

### 1) REST (curl)

```bash
ANON="<ANON>"; BASE="<URL>/rest/v1"

# Expedição de um pedido (todas as colunas)
curl "$BASE/vw_os_exped_impressao_v2?NPED=eq.84080&select=*" \
  -H "apikey: $ANON" -H "Authorization: Bearer $ANON"

# Pintura — só alguns campos (colunas com espaco vao entre aspas)
curl "$BASE/vw_os_pintura_v0?NPED=eq.84080&select=NPED,NomeClien,CodItemEstrut,\"CEP Filial\"" \
  -H "apikey: $ANON" -H "Authorization: Bearer $ANON"

# Almoxarifado (lembre: cliente = CodCli, nao CodClien)
curl "$BASE/vw_os_almox_impressao?NPED=eq.84080&select=NPED,CodCli,NomeClien,DescProdOrcam,QtdOrcam" \
  -H "apikey: $ANON" -H "Authorization: Bearer $ANON"
```

### 2) JavaScript (`@supabase/supabase-js`)

```js
import { createClient } from '@supabase/supabase-js'
const supabase = createClient('<URL>', '<ANON>')

const NPED = 84080

const { data: exped } = await supabase
  .from('vw_os_exped_impressao_v2')
  .select('*')
  .eq('NPED', NPED).order('id', { ascending: true })

const { data: pintura } = await supabase
  .from('vw_os_pintura_v0')
  .select('NPED, NomeClien, CodItemEstrut, "CEP Filial", "CNPJ Filial"')
  .eq('NPED', NPED)

const { data: almox } = await supabase
  .from('vw_os_almox_impressao')
  .select('NPED, CodCli, NomeClien, DescProdOrcam, QtdOrcam, TotalOrcam')
  .eq('NPED', NPED)
```

### 3) Python (`supabase`)

```python
from supabase import create_client
sb = create_client("<URL>", "<ANON>")
NPED = 84080

exped = (sb.table("vw_os_exped_impressao_v2")
           .select("*").eq("NPED", NPED).order("id").execute().data)

pintura = (sb.table("vw_os_pintura_v0")
             .select('NPED, NomeClien, CodItemEstrut, "CEP Filial", "CNPJ Filial"')
             .eq("NPED", NPED).execute().data)

almox = (sb.table("vw_os_almox_impressao")
           .select("NPED, CodCli, NomeClien, DescProdOrcam, QtdOrcam, TotalOrcam")
           .eq("NPED", NPED).execute().data)
```

## Regras (resumo)

1. **Ler** → chave **`anon`**, direto no Supabase (read-only). **Nunca** pedir/usar a `service_role`.
2. **Filtrar por `NPED`** (inteiro). Ex.: `?NPED=eq.84080`.
3. Colunas **case-sensitive**; as com **espaço** entre **aspas** (`"CEP Filial"`); cliente na ALMOX = **`CodCli`**.
4. **Paginar** leituras grandes (PostgREST devolve no máx. **1000 linhas**/requisição — `?limit=1000&offset=0`).
5. Não acumula histórico: cada re-sync do pedido **substitui** as linhas daquele `NPED`.
6. Se receber `PGRST205` (tabela não encontrada) logo após uma mudança de schema, é cache —
   avise o backend (`NOTIFY pgrst, 'reload schema';`).
