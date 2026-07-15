# Ordens de Serviço — o que mudou e os campos novos

**Data:** 15/07/2026 · **Servidor:** `192.168.7.11` · **API:** `http://192.168.7.11:8077`
**Público:** equipes que consomem OS (pela API ou lendo o Supabase direto)

---

## 1. Resumo em 30 segundos

Duas mudanças, ambas **já em produção**:

1. **As 6 tabelas de OS viraram 1**: `vw_os_integracao` (54 colunas). As antigas foram **apagadas**.
2. **Nasceram 4 flags de processo** — `Solda`, `Pintura`, `Almox`, `Exped` — que dizem, **por item**, por quais processos ele passa. Elas substituem as 4 tabelas que identificavam isso antes.

> ⚠️ **Isto quebra quem lia as tabelas antigas.** Elas não foram renomeadas nem mantidas em paralelo — foram dropadas. Detalhes na §5.

**Onde isso aparece para você:**

| Você consome... | Leia a seção |
| --- | --- |
| A **API 8077** (HTTP) | §3 |
| O **Supabase direto** (chave `anon`) | §4 |

---

## 2. Os campos novos

### 2.1 As 4 flags de processo ⭐

| Campo | Tipo | Significado |
| --- | --- | --- |
| `Solda` | `integer` | `1` = o item vai para **solda**, `0` = não vai |
| `Pintura` | `integer` | `1` = o item vai para **pintura**, `0` = não vai |
| `Almox` | `integer` | `1` = o item passa pelo **almoxarifado**, `0` = não passa |
| `Exped` | `integer` | `1` = o item passa pela **expedição**, `0` = não passa |

> 🔑 **A regra mais importante deste documento: as flags são POR ITEM, não por pedido.**
>
> Um pedido tem centenas de itens e normalmente eles são **mistos** — parte vai para solda, parte não. Não existe "o pedido 84172 é de solda". Existe *"40 dos 344 itens do pedido 84172 vão para solda"*.
>
> Exemplo real (pedido 84172, 344 itens): **solda 40 · pintura 72 · almox 55 · exped 127**.

**De onde elas vêm:** antes, para saber se um item ia para solda, você olhava se ele aparecia na tabela `vw_os_solda`. O processo era *a tabela*. Agora o processo é *uma coluna do item* — mesma informação, sem precisar de 4 tabelas.

### 2.2 `U_INO_ORCITM`

| Campo | Tipo |
| --- | --- |
| `U_INO_ORCITM` | `text` |

Item do orçamento (UDF). Vinha da antiga `vw_os_solda`.

---

## 3. Se você consome a API 8077

### 3.1 O que mudou no contrato

**Nada foi removido nem renomeado.** O `resumo` ganhou **um bloco novo**: `processos`. Se você já consome a API, seu código continua funcionando sem alteração — só passa a ter mais informação disponível.

### 3.2 `GET /ordens-servico/{nped}`

Detalhe da OS de um pedido. Requer o header `X-API-Key`.

```bash
curl -H "X-API-Key: SUA_CHAVE" http://192.168.7.11:8077/ordens-servico/84172
```

**Resposta** (resposta real, encurtada):

```json
{
  "ok": true,
  "nped": 84172,
  "resumo": {
    "cliente": "AFTERCLICK SERVICOS INTEGRADOS LTDA",
    "cod_cliente": "C009997",
    "descricao": "Porta-Paletes",
    "status": "P",
    "status_desc": "Planejado",
    "data_pedido": "2026-07-15T00:00:00",
    "data_entrega": "2026-08-14T00:00:00",
    "data_liberacao": null,
    "obs": "DIFERENCIAL DE ALIQUOTA DE ICMS: R$ 116.419,59",
    "num_linhas": 344,
    "num_ops": 137,
    "ops": [143867, 143868, "..."],
    "total_orcamento": 2347906.95,

    "processos": {                                    // ⭐ NOVO
      "solda":   { "tem": true, "linhas": 40  },
      "pintura": { "tem": true, "linhas": 72  },
      "almox":   { "tem": true, "linhas": 55  },
      "exped":   { "tem": true, "linhas": 127 }
    },

    "ultima_sincronizacao": "2026-07-15T16:29:07.967286",
    "id_execucao": "bf295374-3c99-4d8f-b075-ab6f7f8366ad"
  }
}
```

Com `?linhas=1`, a resposta traz também `linhas[]` — os itens da OS, cada um com as suas 4 flags.

### 3.3 O bloco `processos`

Sempre traz **as 4 chaves**, mesmo zeradas (nenhuma some do payload):

| Campo | Tipo | Significado |
| --- | --- | --- |
| `<processo>.tem` | `bool` | **Algum** item do pedido passa por esse processo? |
| `<processo>.linhas` | `int` | **Quantos** itens passam |

*"O pedido 84172 vai para solda?"* → `resumo.processos.solda.tem` → `true` (e `.linhas` = 40 diz quantos itens).

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

## 6. Referência — as 54 colunas

**Pedido / OS:** `N_PED` *(chave)* · `N_OP` · `Status` · `LinhRef` · `VisOrder`

**Cliente / vendedor:** `CodClien` · `NomeClien` · `NomedVend` · `Usuario`

**Datas:** `DtPedido` · `DtVenc` · `DtInic` · `DtLiber` · `DtEncerr` · `DtEntregaPED` · `DataEntrega` · `DiasTotal`

**Item do pedido:** `CodItemPED` · `DescItemPED` · `Quantity` · `UM` · `Deposito` · `Obs` · `ObsPedido`

**Estrutura / árvore:** `LinhEstrut` · `LinhVisEstrut` · `CodItemEstrut` · `DescItemEstrut` · `QtdBasEstrut` · `QtdPlanEstrut` · `QtdSaida` · `QtdLiberEstrut` · `TipoEmissOP` · `TipoItemEstrut` · `DeposEstrut` · `GrupoItem`

**Orçamento:** `CodigoOrcam` · `N_Orcamento` · `CodDetalhOrcamento` · `NivelItemOrcam` · `CodItemOrcam` · `DescProdOrcam` · `CorOrcam` · `QtdOrcam` · `PesoOrcam` · `PrecoOrcam` · `TotalOrcam`

**WBC:** `U_INO_VERSAOWBC` · `U_INO_LINHA` · `U_INO_ORCITM`

**Processo (⭐ novas):** `Solda` · `Pintura` · `Almox` · `Exped`

**Controle** (geradas na carga, não vêm do SAP): `id` *(PK — use no `order by`)* · `id_execucao` · `data_hora_extracao` *(quando o pedido foi sincronizado)* · `origem_view` · `inserted_at`

---

## 7. Dúvidas / campo faltando

Se faltar algum campo da §5.3, o caminho é **incluí-lo na view `VW_OS_INTEGRACAO` no SAP** — ele passa a aparecer na tabela automaticamente. Não dá para recriar as tabelas antigas. Fale com a equipe da integração.
