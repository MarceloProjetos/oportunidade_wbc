# API — Itens dos pedidos de compra

Contrato da rota da .11 (`192.168.7.11:8077`) que entrega os itens dos **pedidos de compra** do SAP, lidos
da view `VW_ALTA_PEDIDOS_COMPRA_ITENS`. Só leitura: nada é gravado no SAP nem na base de dados. Versão de
22/10/2026.

## 1. O que é

- **Pedido de compra** = o que a Altamira comprou de um fornecedor. O número é o **DocNum** do pedido de
  compra (não confunda com o pedido de venda).
- Cada linha da resposta é **um item** de um pedido de compra: fornecedor, item, grupo, quantidade pedida,
  quantidade em aberto e datas. **Não há preço.**
- A view traz pedidos de 2026 (medido em 22/10/2026: 1.397 itens em 774 pedidos).

## 2. Acesso

A **mesma chave** que vocês já usam nas outras consultas da .11 (escopo `leitura`). Nada a pedir nem a
configurar. Chave só no cabeçalho `X-API-Key`.

## 3. `GET /compras/pedidos-itens`

Sem parâmetro nenhum, devolve **todos** os itens da view. Os filtros são opcionais e se combinam (E):

| Parâmetro | Exemplo | O que faz |
| --- | --- | --- |
| `pedido` | `9863` | só os itens deste pedido de compra (DocNum) |
| `fornecedor` | `F000122` | código do fornecedor, exato (maiúscula ou minúscula tanto faz) |
| `item` | `I000139` | código do item, exato |
| `busca` | `belenus` | trecho do nome do fornecedor, da descrição, do código do item ou do grupo |
| `status` | `aberto` | `aberto`, `fechado` ou `todos` (padrão: `todos`). Olha o **item**, não o pedido |
| `entrega_de` | `2026-11-01` | entrega prevista a partir desta data (`AAAA-MM-DD`) |
| `entrega_ate` | `2026-11-30` | entrega prevista até esta data (`AAAA-MM-DD`) |
| `recarregar` | `1` | ignora o cache e lê o SAP agora |

- **`status` é do item:** um pedido aberto pode ter itens já fechados. "O que falta chegar" = `status=aberto`
  e o campo `qtd_em_aberto`.
- **Regra do filtro `status`:** um item conta como **aberto** quando `status_item` é `Aberto` **ou**
  `qtd_em_aberto` é maior que zero. `fechado` = todo o resto. Motivo: a view junta numa linha só todas as
  entregas do mesmo item no mesmo pedido e marca `Fechado` assim que **uma** delas fecha, mesmo com
  mercadoria ainda por chegar (ex.: pedido 10716, item `WMA00001`: `Fechado` com 2.000 em aberto).
- **Cuidado ao ler o campo `status_item`:** ele vem como está na view e pode dizer `Fechado` num item que
  ainda tem `qtd_em_aberto`. Para saber se falta chegar, use `qtd_em_aberto > 0` (ou o filtro
  `status=aberto`). Nesses itens a `data_entrega` é a da entrega mais antiga, não a das que faltam.
- Item sem data de entrega nunca entra num filtro de data.
- Filtro que não casa com nada devolve **200** com a lista vazia.

Exemplo:

```
curl.exe -s -H "X-API-Key: SUA_CHAVE" "http://192.168.7.11:8077/compras/pedidos-itens?status=aberto&entrega_ate=2026-11-30"
```

Resposta **200**:

```json
{"ok": true, "cache_idade_s": 34.2, "total_na_view": 1397, "total_filtrado": 1,
 "itens": [
   {"doc_entry": 15025, "doc_num": 9863, "filial": 1,
    "data_pedido": "2026-06-30", "data_entrega": "2026-11-09",
    "cod_fornecedor": "F000122", "fornecedor": "BELENUS LTDA",
    "cod_item": "PAG516SEXTA5/8000916", "descricao": "P. ALTAMIRA 5/16-18 X 5/8 ZB GRAU 8",
    "grupo": "Fixação", "status_pedido": "Aberto", "status_item": "Aberto",
    "qtd_pedida": 1200.0, "qtd_em_aberto": 1200.0}
 ]}
```

| Campo | Significado |
| --- | --- |
| `doc_num` | número do pedido de compra (o da tela do SAP) |
| `doc_entry` | chave interna do SAP; use `doc_num` para falar com pessoas |
| `filial` | código da filial do pedido |
| `data_pedido` / `data_entrega` | data do pedido e data de entrega prevista (`AAAA-MM-DD`; `null` = sem data) |
| `status_pedido` / `status_item` | `Aberto` ou `Fechado` |
| `qtd_pedida` / `qtd_em_aberto` | quantidade pedida e quanto ainda falta receber |
| `total_na_view` | quantos itens a view tem ao todo |
| `total_filtrado` | quantos itens vieram nesta resposta |
| `cache_idade_s` | há quantos segundos o retrato do SAP foi tirado |

Ordem dos itens: data de entrega, número do pedido e código do item.

## 4. Erros

`{"ok": false, "motivo": "<chave estável>", "error": "<texto>"}` — **decida pelo `motivo`**.

| HTTP | `motivo` | O que fazer |
| --- | --- | --- |
| 422 | `parametro_invalido` | corrigir o parâmetro (`status` fora da lista, `pedido` não numérico, data fora de `AAAA-MM-DD`) |
| 503 | `sap_indisponivel` | SAP sem resposta; tentar de novo em instantes |
| 502 | `falha` | erro inesperado; avisar o suporte |

401/403 vêm do guarda comum: `{"ok": false, "error": "unauthorized"|"forbidden", ...}` — chave errada ou sem o
escopo `leitura`.

## 5. Tempos e cache

- O retrato do SAP fica em cache por **2 minutos** e é o mesmo para todos que perguntam. `recarregar=1` só
  quando precisar do dado deste instante.
- Use timeout de cliente **≥ 60 s** (a 1ª falha do SAP leva ~51 s).

## 6. MCP (`192.168.7.11:8078`)

| Ferramenta | Escopo | O que faz |
| --- | --- | --- |
| `pedidos_compra_itens(pedido, fornecedor, item, busca, status, entrega_de, entrega_ate)` | `leitura` | o `GET` do §3, com `status="aberto"` por padrão e no máximo 200 itens (`cortado: true` quando havia mais) |

O token MCP precisa de `mcp` + `leitura` (o mesmo das outras ferramentas de consulta).
