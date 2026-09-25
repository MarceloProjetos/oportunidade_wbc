# Situação dos Pedidos — campos novos (25/09/2026)

Guia de atualização para quem consome a API de Situação dos Pedidos do servidor
`192.168.7.11:8077`. Diz **o que entrou**, **de que tipo é cada campo**, **o que parar de
usar** e **como atualizar o seu projeto** — com casos reais para testar.

> Contrato completo: [`API_SITUACAO_PEDIDOS.md`](API_SITUACAO_PEDIDOS.md) (seções 2.8 e 6.2).
> No ar desde **25/09/2026, 15:35**.

---

## 1. Em 30 segundos

- Entraram **10 campos** no perfil `completo`. O perfil `resumo` **não mudou**.
- Nenhum campo antigo foi removido, renomeado ou mudou de tipo. **Seu código atual continua
  funcionando sem alteração.**
- Agora existe a **data e hora reais** em que o Financeiro, a Produção e a Entrega foram
  liberados — campos terminados em **`_em`**.
- **`data_lib_prod` não é a data em que a Produção foi liberada.** É uma estimativa. Se você
  mostra "Produção liberada em …" com ela, troque por `lib_producao_em`.
- Dá para saber se a **primeira nota fiscal** do pedido já saiu (`primeira_nf_emitida`) e
  qual o número dela na DANFE (`nf_numero_fiscal`).

---

## 2. Os campos novos

Todos aparecem **só com `campos=completo`**:

- `GET /pedidos/<numero>/situacao` → já é `completo` por padrão.
- `GET /pedidos/situacao` → o padrão é `resumo`; peça **`?campos=completo`**.

### 2.1 Liberação (data e hora reais)

| Campo | Tipo JSON | Pode vir `null`? | Exemplo | O que é |
| --- | --- | --- | --- | --- |
| `lib_fin_em` | string (data e hora) | sim | `"2026-09-23T16:51:16-03:00"` | Quando o **Financeiro** liberou o pedido |
| `sinal_pago_em` | string (data e hora) | sim | `"2026-09-25T08:13:35-03:00"` | Quando o **sinal** foi pago (o recebimento entrou no SAP) |
| `lib_producao_em` | string (data e hora) | sim | `"2026-09-25T08:13:35-03:00"` | Quando a **Produção** foi liberada |
| `lib_entrega_em` | string (data e hora) | sim | `"2026-09-25T08:13:35-03:00"` | Quando a **Entrega** foi liberada — **sempre igual** a `lib_producao_em` |

### 2.2 Primeira nota fiscal

| Campo | Tipo JSON | Pode vir `null`? | Exemplo | O que é |
| --- | --- | --- | --- | --- |
| `primeira_nf_emitida` | boolean | **não** (sempre `true`/`false`) | `true` | A primeira nota fiscal do pedido já foi emitida |
| `nf_numero_fiscal` | number (inteiro) | sim | `32228` | Número **da DANFE** (o que está impresso na nota) |
| `nf_doc_num` | number (inteiro) | sim | `5729` | Número **interno** da mesma nota no SAP |
| `nf_data` | string (data) | sim | `"2026-09-17"` | Data da primeira nota |

### 2.3 Cliente e representante

| Campo | Tipo JSON | Pode vir `null`? | Exemplo | O que é |
| --- | --- | --- | --- | --- |
| `data_criacao_pn` | string (data) | sim | `"2025-09-09"` | Data em que o cliente foi cadastrado no SAP |
| `representante` | string | sim | `"Robson"` | Representante do pedido. Quase sempre igual a `vendedor` |

### 2.4 Formato das datas

| Tipo | Formato | Exemplo | Campos |
| --- | --- | --- | --- |
| **Data e hora** | ISO 8601 **com fuso** | `2026-09-25T08:13:35-03:00` | os 4 campos `*_em` |
| **Data** | `AAAA-MM-DD`, sem hora | `2026-09-17` | `nf_data`, `data_criacao_pn` |

O fuso é sempre o de Brasília (`-03:00`). Converta com uma função de data de verdade
(`datetime.fromisoformat` em Python, `new Date(...)` em JavaScript) — **não corte a string**.

---

## 3. O que parar de usar

Estes campos **continuam na resposta**, com o mesmo valor de antes, mas **não significam o
que o nome sugere**:

| Campo antigo | O que parece | O que é de verdade | Use no lugar |
| --- | --- | --- | --- |
| `data_lib_prod` | Dia em que a Produção liberou | **Estimativa** calculada pelo SAP: a maior entre `data_lib_fin` e `data_pagto`, **mais 3 dias corridos**. Por isso caía em sábado, domingo e no futuro | **`lib_producao_em`** |
| `data_lib_fin` | Dia em que o Financeiro liberou | Data **digitada** à mão. Em 182 de 252 pedidos está 1 dia depois do real | **`lib_fin_em`** |
| `data_pagto` | Dia em que o sinal foi pago | Dia em que a **cobrança** do sinal foi emitida — paga ou não | **`sinal_pago_em`** |

Exemplo real, pedido **84348**: `data_lib_prod` diz **12/09** (um sábado). A Produção foi
liberada de verdade em **25/09 às 08:13**, quando o pagamento do sinal entrou no SAP.

---

## 4. As regras

1. **"Quando foi liberado?" se responde com os campos `*_em`.** Nunca com `data_lib_prod`,
   `data_lib_fin` ou `data_pagto`.

2. **`null` num campo `*_em` quer dizer "não foi possível saber a hora"** — não quer dizer
   "não liberado". Para saber **se** está liberado, continue usando `financeiro`,
   `producao` e `entrega` (`"Liberado"` / `"Bloqueado"` / `"Cancelado"`).
   **Não preencha o vazio com `data_lib_prod`**: ela é uma estimativa, não um fato.

3. **`lib_producao_em` só vem preenchido quando `producao` é `"Liberado"`.** Pedido
   bloqueado sempre vem com `lib_producao_em: null`, mesmo que já tenha sido liberado
   antes (veja a regra 5).

4. **Produção e Entrega são liberadas juntas.** O SAP não separa as duas: `lib_entrega_em`
   é sempre igual a `lib_producao_em`, e a `entrega` **nunca** aparece liberada antes da
   `producao`. O valor original do SAP para a Entrega fica em `entrega_sap` (campo de
   25/09, para conferência).

5. **Como o SAP decide que a Produção está liberada** (confirmado em 281 de 281 pedidos):
   - o **Financeiro** liberou, **e**
   - o pedido **não tem sinal**, **ou** a cobrança de sinal **mais recente** está paga.

   Por isso `lib_producao_em` é o **mais tardio** entre `lib_fin_em` e `sinal_pago_em`.
   Quando o sinal é **reemitido** (uma cobrança nova), o pedido que já tinha pago **volta a
   ficar bloqueado** até a nova ser paga — e `lib_producao_em` volta a `null`.

6. **`primeira_nf_emitida` nunca é `null`.** É `true` ou `false`. Os outros campos de nota
   (`nf_*`) vêm `null` quando não há nota.

7. **Use `nf_numero_fiscal` para mostrar a nota a uma pessoa.** É o número da DANFE.
   `nf_doc_num` é interno do SAP e serve para conferência com a equipe do SAP.

8. **A nota pode sair antes da liberação da Produção.** Acontece hoje no 84420: nota emitida
   em 25/09 com a Produção bloqueada. A API mostra o que está no SAP; não trate como erro.

9. **Pedidos anteriores a 06/01/2025** vêm com `data_criacao_pn`, `representante` e `nf_*`
   em `null` e `primeira_nf_emitida: false` — a fonte desses dados no SAP começa nessa data.

10. **Escreva o cliente para ignorar campos que ele não conhece.** Campos novos podem
    aparecer sem aviso; campos existentes não mudam de nome nem de tipo sem aviso antes.

11. **Os dados têm até 2 minutos de atraso** (cache da API). `cache_idade_s` diz a idade.

---

## 5. Como atualizar o seu projeto

### Passo a passo

- [ ] Se você usa a **lista** (`/pedidos/situacao`), passe `?campos=completo`.
- [ ] Acrescente os 10 campos ao seu modelo de dados (tipos na seção 5.1), todos opcionais
      menos `primeira_nf_emitida`.
- [ ] Troque **toda** exibição de "liberado em" que use `data_lib_prod`, `data_lib_fin` ou
      `data_pagto` pelos campos `*_em` (seção 3).
- [ ] Mostre **data e hora** (ex.: `25/09/2026 08:13`), não só a data.
- [ ] Para `null` num `*_em`, mostre algo como "hora não disponível" — sem estimar.
- [ ] Se hoje você mostra "detectado pelo Altamira" (a hora em que **o seu sistema**
      percebeu a mudança), pode trocar pela hora real do SAP; mantenha a sua só como
      reserva quando o campo vier `null`.
- [ ] Mostre a nota com `nf_numero_fiscal` e `nf_data` quando `primeira_nf_emitida` for
      `true`.
- [ ] Teste com os pedidos da seção 6.

### 5.1 Tipos (TypeScript)

```ts
/** Campos acrescentados em 25/09/2026 ao perfil `completo`. */
interface LiberacaoENotaFiscal {
  lib_fin_em: string | null;        // "2026-09-23T16:51:16-03:00"
  sinal_pago_em: string | null;
  lib_producao_em: string | null;
  lib_entrega_em: string | null;    // sempre igual a lib_producao_em
  primeira_nf_emitida: boolean;     // nunca null
  nf_numero_fiscal: number | null;  // número da DANFE
  nf_doc_num: number | null;        // número interno do SAP
  nf_data: string | null;           // "2026-09-17"
  data_criacao_pn: string | null;   // "2025-09-09"
  representante: string | null;
}
```

### 5.2 Exemplo em Python

```python
from datetime import datetime

def quando_liberou(pedido: dict) -> str:
    """Texto de "Produção liberada" para a tela, sem estimar."""
    if pedido["producao"] != "Liberado":
        return "Produção bloqueada"
    em = pedido.get("lib_producao_em")
    if em is None:
        return "Produção liberada (hora não disponível)"
    return "Produção liberada em " + datetime.fromisoformat(em).strftime("%d/%m/%Y %H:%M")

def nota(pedido: dict) -> str:
    if not pedido["primeira_nf_emitida"]:
        return "Primeira nota ainda não emitida"
    data = datetime.fromisoformat(pedido["nf_data"]).strftime("%d/%m/%Y")
    return f"1ª NF {pedido['nf_numero_fiscal']} em {data}"
```

### 5.3 Exemplo em JavaScript

```js
function quandoLiberou(p) {
  if (p.producao !== "Liberado") return "Produção bloqueada";
  if (!p.lib_producao_em) return "Produção liberada (hora não disponível)";
  const d = new Date(p.lib_producao_em); // o -03:00 da string é respeitado
  return "Produção liberada em " + d.toLocaleString("pt-BR", {
    timeZone: "America/Sao_Paulo", dateStyle: "short", timeStyle: "short",
  });
}
```

### 5.4 Antes e depois, na tela

| | Antes (errado) | Depois (certo) |
| --- | --- | --- |
| 84428 | Produção liberada no SAP em **27/09/2026** (domingo, no futuro) | Produção liberada em **23/09/2026 16:51** |
| 84348 | Produção liberada no SAP em **12/09/2026** (sábado) | Produção liberada em **25/09/2026 08:13** |

---

## 6. Casos reais para testar

Valores de 25/09/2026. Os pedidos mudam com o tempo (sinal reemitido, nota nova), então
use a lista como ponto de partida, não como resultado fixo para sempre.

| Pedido | Situação | O que a API devolve |
| --- | --- | --- |
| **84428** | Sem sinal | `lib_fin_em` = `lib_producao_em` = `lib_entrega_em` = `2026-09-23T16:51:16-03:00`; `sinal_pago_em: null`; `primeira_nf_emitida: false` |
| **84348** | Sinal pago **depois** do Financeiro | `lib_fin_em` = `2026-09-08T15:06:26-03:00`; `sinal_pago_em` = `lib_producao_em` = `2026-09-25T08:13:35-03:00` |
| **84080** | Pedido com várias notas | `lib_producao_em` = `2026-08-27T08:33:08-03:00`; `primeira_nf_emitida: true`; `nf_numero_fiscal: 32228`; `nf_doc_num: 5729`; `nf_data: "2026-09-17"` |
| **84420** | Sinal reemitido, em aberto | `producao: "Bloqueado"`; `lib_fin_em` preenchido; `sinal_pago_em`, `lib_producao_em` e `lib_entrega_em` = `null`; **mas** `primeira_nf_emitida: true` (DANFE 32280) |

Chamada para testar:

```text
GET http://192.168.7.11:8077/pedidos/84348/situacao
X-API-Key: <sua chave>
```

---

## 7. Perguntas frequentes

**Meu código vai quebrar?**
Não. Só entraram campos; nenhum saiu nem mudou.

**Por que `lib_producao_em` veio `null` com a Produção liberada?**
Não foi possível achar no SAP a hora de uma das condições (histórico de alteração ausente,
ou sinal quitado por um caminho que não deixa hora). Acontece em 6 de 274 pedidos hoje.
Mostre "hora não disponível".

**Posso usar `data_lib_prod` quando `lib_producao_em` vier `null`?**
Não. Ela erra o dia real em até 118 dias para trás e 15 para frente. Mostrar uma estimativa
como se fosse fato foi exatamente o problema que isto corrige.

**Qual a diferença entre `representante` e `vendedor`?**
`vendedor` vem do cadastro de vendedor do pedido; `representante`, da view de orçamentos do
SAP. Hoje divergem em 1 de 280 pedidos. Use o que o seu negócio já usa.

**Por que só a primeira nota?**
É a que diz se o pedido começou a ser faturado. Um pedido pode ter muitas notas (há um com
42); a lista completa não está nesta API.

**O assistente de IA (MCP) já sabe disso?**
Sim. A tool `situacao_pedido` foi atualizada para responder "quando foi liberado" pelos
campos `*_em`.

---

Dúvidas: fale com a equipe do OrçaView / servidor de integração SAP.
