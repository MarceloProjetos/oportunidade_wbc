# Ordens de Serviço — o que mudou e os campos novos

**Data:** 15/07/2026 *(atualizado em 05/08/2026 — §2.1 e §2.3)* · **Servidor:** `192.168.7.11` · **API:** `http://192.168.7.11:8077`
**Público:** equipes que consomem OS (pela API ou lendo o Supabase direto)

---

## 1. Resumo em 30 segundos

Duas mudanças estruturais, ambas **já em produção**:

1. **As 6 tabelas de OS viraram 1**: `vw_os_integracao` (56 colunas). As antigas foram **apagadas**.
2. **Nasceram flags de processo** — `Solda`, `Pintura`, `Almox`, `Exped` e, desde 05/08, `Compras` — que dizem, **por item**, por quais processos ele passa. As 4 primeiras substituem as 4 tabelas que identificavam isso antes.

**05/08/2026:** entraram **`U_INO_D_Adicionais`** (Dados Adicionais do item — §2.3) e a **5ª flag de processo `Compras`** (§2.1). Quem lê o `resumo.processos` ganha uma chave nova, `compras`.

> ⚠️ **Isto quebra quem lia as tabelas antigas.** Elas não foram renomeadas nem mantidas em paralelo — foram dropadas. Detalhes na §5.

**Onde isso aparece para você:**

| Você consome... | Leia a seção |
| --- | --- |
| A **API 8077** (HTTP) | §3 |
| O **Supabase direto** (chave `anon`) | §4 |

---

## 2. Os campos novos

### 2.1 As flags de processo ⭐

| Campo | Tipo | Significado |
| --- | --- | --- |
| `Solda` | `integer` | `1` = o item vai para **solda**, `0` = não vai |
| `Pintura` | `integer` | `1` = o item vai para **pintura**, `0` = não vai |
| `Almox` | `integer` | `1` = o item passa pelo **almoxarifado**, `0` = não passa |
| `Exped` | `integer` | `1` = o item passa pela **expedição**, `0` = não passa |
| `Compras` | `integer` | `1` = o item passa por **compras**, `0` = não passa — **⭐ nova em 05/08/2026** |

> 🔑 **A regra mais importante deste documento: as flags são POR ITEM, não por pedido.**
>
> Um pedido tem centenas de itens e normalmente eles são **mistos** — parte vai para solda, parte não. Não existe "o pedido 84172 é de solda". Existe *"43 dos 302 itens do pedido 84172 vão para solda"*.
>
> Exemplo real (pedido 84172, 302 itens, medido em 06/08): **solda 43 · pintura 63 · almox 49 · exped 112 · compras 177**.

**De onde elas vêm:** antes, para saber se um item ia para solda, você olhava se ele aparecia na tabela `vw_os_solda`. O processo era *a tabela*. Agora o processo é *uma coluna do item* — mesma informação, sem precisar de 4 tabelas. `Compras` nasceu já como coluna: nunca teve tabela.

> A lista **pode crescer** (cresceu de 4 para 5 em 05/08). Leia as flags **por nome**, nunca "as 4 últimas colunas" nem por posição.

### 2.2 `U_INO_ORCITM`

| Campo | Tipo |
| --- | --- |
| `U_INO_ORCITM` | `text` |

Item do orçamento (UDF). Vinha da antiga `vw_os_solda`.

### 2.3 `U_INO_D_Adicionais` ⭐ (05/08/2026)

| Campo | Tipo na view | Tipo no espelho |
| --- | --- | --- |
| `U_INO_D_Adicionais` | `NVARCHAR(5000)` | `text` |

**Dados Adicionais** do item — a UDF `U_INO_D_Adicionais` da *linha* do documento (`RDR1`/`QUT1`, `NCLOB` na origem; a view entrega recortada em 5.000 caracteres).

> 🔑 Como as flags de processo, é **por item**, não do pedido. Duas linhas do mesmo `N_PED` têm textos diferentes.

**Onde ele aparece:**

| Você consome... | O que fazer |
| --- | --- |
| O **Supabase direto** | nada — a coluna já vem no `select *` da `vw_os_integracao` |
| A **API 8077** | peça explicitamente: `GET /ordens-servico/{nped}?linhas=1&adicionais=1` |

Ele fica **fora** do payload padrão de propósito: são até 5.000 caracteres **por item** (~1,5 MB num pedido de 302 linhas), e quase todo consumidor da API só quer o `resumo`.

---

## 3. Se você consome a API 8077

### 3.1 O que mudou no contrato

**Nada foi removido nem renomeado.** O `resumo` ganhou **um bloco novo**: `processos`. Se você já consome a API, seu código continua funcionando sem alteração — só passa a ter mais informação disponível.

### 3.2 `GET /ordens-servico/{nped}`

Detalhe da OS de um pedido. Requer o header `X-API-Key`.

```bash
curl -H "X-API-Key: SUA_CHAVE" http://192.168.7.11:8077/ordens-servico/84172
```

**Resposta** (resposta real do mesmo pedido, medida em 06/08/2026, encurtada):

```json
{
  "ok": true,
  "nped": 84172,
  "resumo": {
    "cliente": "AFTERCLICK SERVICOS INTEGRADOS LTDA",
    "cod_cliente": "C009997",
    "descricao": "Porta-Paletes",
    "status": "R",
    "status_desc": "Liberado",
    "data_pedido": "2026-07-30T00:00:00",
    "data_entrega": "2026-08-14T00:00:00",
    "data_liberacao": "2026-08-03T00:00:00",
    "obs": null,
    "num_linhas": 302,
    "num_ops": 121,
    "ops": [146504, 146505, "..."],
    "total_orcamento": 2408365.16,

    "processos": {                                    // ⭐ NOVO
      "solda":   { "tem": true, "linhas": 43  },
      "pintura": { "tem": true, "linhas": 63  },
      "almox":   { "tem": true, "linhas": 49  },
      "exped":   { "tem": true, "linhas": 112 },
      "compras": { "tem": true, "linhas": 177 }        // ⭐ desde 05/08
    },

    "ultima_sincronizacao": "2026-08-06T11:38:11.973106",
    "id_execucao": "58ff2126-95a3-44f5-8187-113d9e5113c1"
  }
}
```

> Mesmo pedido, números diferentes dos que este doc trazia em 15/07 (eram 344 linhas e 137 OPs): o espelho reflete a OS **no momento da sincronização**, não um histórico.

Com `?linhas=1`, a resposta traz também `linhas[]` — os itens da OS, cada um com as suas 5 flags de processo.
Somando `&adicionais=1`, cada linha traz ainda o `U_INO_D_Adicionais` (§2.3) — sozinho, o `adicionais=1` não faz nada, porque o campo é por item e só existe dentro de `linhas[]`.

### 3.3 O bloco `processos`

Sempre traz **todas as chaves** (hoje 5, desde 05/08), mesmo zeradas — nenhuma some do payload:

| Campo | Tipo | Significado |
| --- | --- | --- |
| `<processo>.tem` | `bool` | **Algum** item do pedido passa por esse processo? |
| `<processo>.linhas` | `int` | **Quantos** itens passam |

*"O pedido 84172 vai para solda?"* → `resumo.processos.solda.tem` → `true` (e `.linhas` = 43 diz quantos itens).

Ele é **agregado** de propósito: como as flags são por item (§2.1), um booleano único do pedido seria enganoso.

### 3.4 Demais endpoints (inalterados)

| Endpoint | O que faz | Chave? |
| --- | --- | --- |
| `GET /health` | A API está de pé? | não |
| `GET /status` | Diagnóstico (SAP, SQL, Supabase, latências) | não |
| `GET /ordens-servico/disponiveis` | Pedidos com OS criada no SAP | sim |
| `GET /ordens-servico/{nped}` | Detalhe/resumo (§3.2) | sim |
| `POST /ordens-servico/{nped}/sincronizar` | Sincroniza o pedido e devolve o resumo | sim |
| `GET /historico` | Últimas sincronizações | sim |

**Autenticação:** header `X-API-Key: <chave>` (ou `Authorization: Bearer <chave>`).

**Respostas de negócio** do `GET /ordens-servico/{nped}`:
- `404` + `"pedido sem OS sincronizada"` → o pedido ainda não foi sincronizado. Dispare o `POST .../sincronizar` ou use `/ordens-servico/disponiveis`.

### 3.5 ⚠️ Pedido **cancelado** no SAP com OS viva (desde 03/09/2026)

A OS sincronizada **não some** quando o pedido é cancelado no SAP: as OPs continuam na
OWOR, o `resumo` continua vindo com `num_ops`, `processos` e `exped_disponivel: true`.
Até 03/09 nada na API dizia "cancelado" — e a `Situação dos Pedidos`
(`GET /pedidos/{n}/situacao`) responde **404** para pedido cancelado, porque a view
de origem os exclui. Resultado visto em produção: 84282, 84305 e 84314 apareciam na
lista de OS como pedidos normais e "sem situação" na tela de quem consome.

Agora os dois endpoints leem `ORDR.CANCELED` ao vivo:

| Endpoint | Campos novos |
| --- | --- |
| `GET /ordens-servico/disponiveis` | em cada item: `status_pedido` (`"Aberto"` \| `"Cancelado"` \| `"Fechado"` \| `null`) e `pedido_cancelado` (`true` \| `false` \| `null`) |
| `GET /ordens-servico/{nped}` | no nível de topo: os mesmos dois, e **quando cancelado** também `aviso: {"tipo": "pedido_cancelado", "motivo": "..."}` |

```json
{
  "ok": true,
  "nped": 84314,
  "status_pedido": "Cancelado",
  "pedido_cancelado": true,
  "aviso": {"tipo": "pedido_cancelado", "motivo": "Pedido cancelado no SAP - a OS sincronizada e historico, nao libere nem produza por ela."},
  "resumo": { "...": "inalterado" }
}
```

Regras para quem consome:

- **Cheque `pedido_cancelado` antes de oferecer "Liberar"** ou de mostrar a OS como
  produzível. O pedido cancelado **continua na lista de propósito**: esconder faria o
  problema (OPs vivas de pedido morto) sumir da vista de quem precisa cancelá-las.
- `null` nos dois campos = a API não conseguiu perguntar ao SAP naquele instante (ou a OS
  não tem pedido na ORDR). Não conclua "não cancelado" a partir de `null`.
- O `tipo` `pedido_cancelado` é o **mesmo** que o `POST .../sincronizar` já devolvia.
- Nada foi removido nem renomeado; quem ignora os campos novos continua funcionando.

**Ao sincronizar**, a resposta pode trazer avisos em vez de erro (todos `HTTP 200`): `sem_os` (OS ainda não gerada), `pedido_cancelado`, `pedido_nao_encontrado`, `cancelada`. O `502` é falha real de sincronização.

> ⏱️ A sincronização de um pedido grande leva **~10 s**. Se seu cliente HTTP tiver timeout curto, ele pode estourar **mesmo com a sincronização dando certo** no servidor — confira com `GET /ordens-servico/{nped}` antes de concluir que falhou.

---

## 4. Se você lê o Supabase direto (chave `anon`)

### 4.1 O que mudou

- **Tabela:** `public.vw_os_integracao` (era `ordens_servico_engenharia`, `vw_os_exped_impressao_v2`, `vw_os_pintura_v0`, `vw_os_almox_impressao`, `vw_os_solda`, `wbc_arvore_produto` — **todas dropadas**).
- **Chave do pedido:** `"N_PED"` (com underscore) — era `"NPED"`.
- **Acesso:** **não mudou.** Mesmo projeto, mesma chave `anon`, leitura liberada, escrita bloqueada. Nada a trocar de credencial.

### 4.2 De → Para

| Antes | Agora |
| --- | --- |
| `.from('ordens_servico_engenharia')` | `.from('vw_os_integracao')` |
| `.from('vw_os_exped_impressao_v2')` | `.from('vw_os_integracao')` + filtrar `"Exped" = 1` |
| `.from('vw_os_pintura_v0')` | `.from('vw_os_integracao')` + filtrar `"Pintura" = 1` |
| `.from('vw_os_almox_impressao')` | `.from('vw_os_integracao')` + filtrar `"Almox" = 1` |
| `.from('vw_os_solda')` | `.from('vw_os_integracao')` + filtrar `"Solda" = 1` |
| `.from('wbc_arvore_produto')` | `.from('vw_os_integracao')` (já vem na mesma linha) |
| `.eq('NPED', 84080)` | `.eq('N_PED', 84080)` |
| `"NºOrçament"` | `"N_Orcamento"` |
| `"CodCli"` (só na almox) | `"CodClien"` |
| join com `status_ordens_servico_eng` | traduzir no cliente (§5.2) |

### 4.3 Exemplos

```sql
-- itens de solda de um pedido (equivale à antiga vw_os_solda)
select * from public.vw_os_integracao
where "N_PED" = 84172 and "Solda" = 1
order by "id";

-- cabeçalho + total correto + itens por processo
select "N_PED",
       max("NomeClien")                        as cliente,
       max("Status")                           as status,
       count(*)                                as itens,
       count(distinct "N_OP")                  as ops,
       sum("TotalOrcam")                       as total_orcamento,
       count(*) filter (where "Solda"   = 1)   as solda,
       count(*) filter (where "Pintura" = 1)   as pintura,
       count(*) filter (where "Almox"   = 1)   as almox,
       count(*) filter (where "Exped"   = 1)   as exped
from public.vw_os_integracao
where "N_PED" = 84172
group by "N_PED";
```

```js
// supabase-js — itens que vão para pintura
const { data, error } = await supabase
  .from('vw_os_integracao')
  .select('N_PED,N_OP,CodItemEstrut,DescItemEstrut,QtdBasEstrut,Pintura')
  .eq('N_PED', 84172)
  .eq('Pintura', 1)
  .order('id');
```

> **Case sensitive:** os nomes preservam o case exato da view SAP e **exigem aspas duplas** em SQL (`"N_PED"`, não `n_ped`). No supabase-js vão literais.

---

## 5. Armadilhas (leia antes de codar)

### 5.1 Uma linha por item — `TotalOrcam` é POR LINHA

A tabela é **desnormalizada por item de estrutura/orçamento**: um pedido tem centenas de linhas e os campos de **cabeçalho** (`NomeClien`, `Status`, `DtPedido`, `DtEntregaPED`…) **se repetem em todas**.

- **Para o total do pedido, use `sum("TotalOrcam")`.** Pegar a primeira linha dá o valor de *um item aleatório*. (Caso real: `R$ 96,78` num orçamento de `R$ 3,05 mi`.)
- Para cabeçalho, use `distinct` ou `limit 1` **com `order by "id"`** — sem `ORDER BY`, o PostgREST devolve uma linha arbitrária e a resposta muda entre chamadas.
- Para contar OPs: `count(distinct "N_OP")`.

### 5.2 O lookup de status sumiu

A tabela `status_ordens_servico_eng` foi removida. `"Status"` traz o código cru:

| Código | Descrição |
| --- | --- |
| `P` | Planejado |
| `R` | Liberado (em produção) |
| `L` | Encerrado |
| `C` | Cancelado |

(Pela API isso já vem pronto em `resumo.status_desc`.)

### 5.3 Colunas que deixaram de existir

Sem substituto na view nova. Se você usa alguma, **avise** — a solução é incluí-la na view no SAP.

- **Endereço da filial (12 colunas):** `Filial`, `"Tipo Logradouro"`, `"Rua Filial"`, `NFilial`/`"Nº Filial"`, `"Complemento Filial"`, `"CEP Filial"`, `"Bairro Filial"`, `"Cidade Filial"`, `"Estado Filial"`, `"CNPJ Filial"`, `"IE Filial"`, `Matriz`
- **Solda:** `U_INO_ORCAMENTO`, `U_INO_EXPL_SOLDA`, `ItmsGrpCod_OITM`, `LinhaOrcam` *(mas `U_INO_ORCITM` **existe** — §2.2)*
- **Textos técnicos:** `TextoLivPED`, `InfoAdicPED`, `InfoAdicPED2`, `ComposicaoPED`, `MATExistPED`, `AcabamentoPED`, `CapacidadePED`, `CordPED`, `ObsImpostOrcamento`
- **Outras:** `TIPO` *(substituído pelas 4 flags)*, `GrpMaterialEstrut`, `GrpItensEstrut`, `PesoEstrut`, `U_INO_COD`, `U_INO_NIVEL`, `U_INO_PROJETO`, `DocEntry_OP`, `DocEntry_PED`, `QtdConcl`, `QtdRejeit`
- **Árvore WBC:** `GRPCOD`, `SUBGRPCOD`, `ORCITM`, `idIntegracao_OrcPrdArv`, `orcprdarv_dth`

**Equivalências da árvore WBC** (confira a semântica antes de confiar):

| Antes | Agora |
| --- | --- |
| `ORCNUM` | `"CodigoOrcam"` / `"N_Orcamento"` |
| `PRDCOD` | `"CodItemOrcam"` / `"CodItemEstrut"` |
| `PRDDSC` | `"DescProdOrcam"` |
| `ORCPRDARV_NIVEL` | `"NivelItemOrcam"` |
| `ORCQTD` / `ORCTOT` / `ORCPES` | `"QtdOrcam"` / `"TotalOrcam"` / `"PesoOrcam"` |
| `CORCOD` | `"CorOrcam"` |
| `GRPCOD` | `"GrupoItem"` *(⚠️ pode não ser o mesmo domínio — validar)* |

### 5.4 O pedido só existe depois de sincronizado

A carga é **sob demanda, por pedido**. Se o `N_PED` não retorna nada, ele ainda não foi sincronizado — dispare o `POST /ordens-servico/{nped}/sincronizar` ou veja `/ordens-servico/disponiveis`.

---

## 6. Referência — as 56 colunas

**Pedido / OS:** `N_PED` *(chave)* · `N_OP` · `Status` · `LinhRef` · `VisOrder`

**Cliente / vendedor:** `CodClien` · `NomeClien` · `NomedVend` · `Usuario`

**Datas:** `DtPedido` · `DtVenc` · `DtInic` · `DtLiber` · `DtEncerr` · `DtEntregaPED` · `DataEntrega` · `DiasTotal`

**Item do pedido:** `CodItemPED` · `DescItemPED` · `U_INO_D_Adicionais` *(⭐ 05/08)* · `Quantity` · `UM` · `Deposito` · `Obs` · `ObsPedido`

**Estrutura / árvore:** `LinhEstrut` · `LinhVisEstrut` · `CodItemEstrut` · `DescItemEstrut` · `QtdBasEstrut` · `QtdPlanEstrut` · `QtdSaida` · `QtdLiberEstrut` · `TipoEmissOP` · `TipoItemEstrut` · `DeposEstrut` · `GrupoItem`

**Orçamento:** `CodigoOrcam` · `N_Orcamento` · `CodDetalhOrcamento` · `NivelItemOrcam` · `CodItemOrcam` · `DescProdOrcam` · `CorOrcam` · `QtdOrcam` · `PesoOrcam` · `PrecoOrcam` · `TotalOrcam`

**WBC:** `U_INO_VERSAOWBC` · `U_INO_LINHA` · `U_INO_ORCITM`

**Processo (⭐ novas):** `Solda` · `Pintura` · `Almox` · `Exped` · `Compras` *(⭐ 05/08)*

**Controle** (geradas na carga, não vêm do SAP): `id` *(PK — use no `order by`)* · `id_execucao` · `data_hora_extracao` *(quando o pedido foi sincronizado)* · `origem_view` · `inserted_at`

---

## 7. Dúvidas / campo faltando

Se faltar algum campo da §5.3, o caminho é **incluí-lo na view `VW_OS_INTEGRACAO` no SAP** — ele passa a aparecer na tabela automaticamente. Não dá para recriar as tabelas antigas. Fale com a equipe da integração.
