# Situação dos Pedidos — campos novos de liberação e nota fiscal

Guia de atualização para quem consome a API de Situação dos Pedidos do servidor
`192.168.7.11:8077`. Diz **o que entrou**, **de que tipo é cada campo**, **o que parar de
usar** e **como atualizar o seu projeto** — com casos reais para testar.

> Contrato completo: [`API_SITUACAO_PEDIDOS.md`](../API_SITUACAO_PEDIDOS.md) (seções 2.8, 6.2 e 10).
> Campos no ar desde **25/09/2026, 15:35**. Guia revisado em **02/10/2026**.

> **O que mudou desde a primeira versão deste guia (02/10/2026)**
>
> 1. **Correção:** o SAP guarda só as **99 últimas versões** de cada pedido. Em pedido muito
>    alterado, a API mostrava como "Financeiro liberado" a hora da versão mais antiga que
>    sobrou — uma hora **mais tarde** que a real, e que mudava a cada alteração nova do
>    pedido. Agora esse caso vem **`null`** ("não dá para saber"), como diz a regra 2.
>    Afeta 25 dos 295 pedidos da carteira de hoje. Veja a seção 7.
> 2. **Acesso:** cada equipe tem a **sua chave**, que só vale no cabeçalho. Resposta nova:
>    **403** `sem_permissao` (seção 6).
> 3. Exemplos e números conferidos de novo em 02/10, contra o SAP.

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
- **`null` num campo `_em` quer dizer "não dá para saber a hora"** — nunca "não liberado".

---

## 2. Os campos novos

Todos aparecem **só com `campos=completo`**:

- `GET /pedidos/<numero>/situacao` → já é `completo` por padrão.
- `GET /pedidos/situacao` → o padrão é `resumo`; peça **`?campos=completo`**.

### 2.1 Liberação (data e hora reais)

| Campo | Tipo JSON | Pode vir `null`? | Exemplo | O que é |
| --- | --- | --- | --- | --- |
| `lib_fin_em` | string (data e hora) | sim | `"2026-06-01T08:32:16-03:00"` | Quando o **Financeiro** liberou o pedido |
| `sinal_pago_em` | string (data e hora) | sim | `"2026-06-19T08:26:59-03:00"` | Quando o **sinal** foi pago (o recebimento entrou no SAP) |
| `lib_producao_em` | string (data e hora) | sim | `"2026-06-19T08:26:59-03:00"` | Quando a **Produção** foi liberada |
| `lib_entrega_em` | string (data e hora) | sim | `"2026-06-19T08:26:59-03:00"` | Quando a **Entrega** foi liberada — **sempre igual** a `lib_producao_em` |

### 2.2 Primeira nota fiscal

| Campo | Tipo JSON | Pode vir `null`? | Exemplo | O que é |
| --- | --- | --- | --- | --- |
| `primeira_nf_emitida` | boolean | **não** (sempre `true`/`false`) | `true` | A primeira nota fiscal do pedido já foi emitida |
| `nf_numero_fiscal` | number (inteiro) | sim | `31799` | Número **da DANFE** (o que está impresso na nota) |
| `nf_doc_num` | number (inteiro) | sim | `5268` | Número **interno** da mesma nota no SAP |
| `nf_data` | string (data) | sim | `"2026-07-07"` | Data da primeira nota |

### 2.3 Cliente e representante

| Campo | Tipo JSON | Pode vir `null`? | Exemplo | O que é |
| --- | --- | --- | --- | --- |
| `data_criacao_pn` | string (data) | sim | `"2026-04-29"` | Data em que o cliente foi cadastrado no SAP |
| `representante` | string | sim | `"Patrick Agne"` | Representante do pedido. Quase sempre igual a `vendedor` |

### 2.4 Formato das datas

| Tipo | Formato | Exemplo | Campos |
| --- | --- | --- | --- |
| **Data e hora** | ISO 8601 **com fuso** | `2026-06-19T08:26:59-03:00` | os 4 campos `*_em` |
| **Data** | `AAAA-MM-DD`, sem hora | `2026-07-07` | `nf_data`, `data_criacao_pn` |

- **Data e hora:** o fuso é sempre o de Brasília (`-03:00`). Converta com uma função de data
  de verdade (`datetime.fromisoformat` em Python, `new Date(...)` em JavaScript) — **não
  corte a string**.
- **Data sem hora, em JavaScript:** **não** use `new Date("2026-07-07")`. O JavaScript lê
  essa string como meia-noite em **UTC**, e no Brasil ela aparece como **06/07/2026** — um
  dia antes. Monte o texto direto da string (exemplo na seção 5.3). Em Python,
  `date.fromisoformat` não tem esse problema.

### 2.5 Como fica na resposta

Trecho real do pedido **84008** (02/10/2026), só com os campos de liberação e nota — os
antigos ao lado dos novos, para comparar:

```json
{
  "doc_num": 84008,
  "financeiro": "Liberado",
  "producao": "Liberado",
  "entrega": "Liberado",
  "sinal": true,

  "data_lib_fin": "2026-06-02",
  "data_pagto": "2026-06-18",
  "data_lib_prod": "2026-06-21",

  "lib_fin_em": "2026-06-01T08:32:16-03:00",
  "sinal_pago_em": "2026-06-19T08:26:59-03:00",
  "lib_producao_em": "2026-06-19T08:26:59-03:00",
  "lib_entrega_em": "2026-06-19T08:26:59-03:00",

  "primeira_nf_emitida": true,
  "nf_numero_fiscal": 31799,
  "nf_doc_num": 5268,
  "nf_data": "2026-07-07",

  "data_criacao_pn": "2026-04-29",
  "representante": "Patrick Agne",
  "vendedor": "Patrick Agne"
}
```

O mesmo pedido como linha do tempo — o que **aconteceu** e o que cada campo diz:

| Quando | O que aconteceu | Campo |
| --- | --- | --- |
| 01/06 08:32 | O Financeiro liberou | **`lib_fin_em`** ✅ |
| 02/06 | *(nada — é a data digitada à mão, 1 dia depois)* | `data_lib_fin` ❌ |
| 18/06 | A cobrança do sinal foi **emitida** (não paga) | `data_pagto` ❌ |
| 19/06 08:26 | O sinal foi **pago** → Produção e Entrega liberadas | **`sinal_pago_em`**, **`lib_producao_em`**, **`lib_entrega_em`** ✅ |
| 21/06 (domingo) | *(nada — é a estimativa: 18/06 + 3 dias)* | `data_lib_prod` ❌ |
| 07/07 | Saiu a 1ª nota fiscal, DANFE 31799 | **`nf_data`**, **`nf_numero_fiscal`** ✅ |

---

## 3. O que parar de usar

Estes campos **continuam na resposta**, com o mesmo valor de antes, mas **não significam o
que o nome sugere**:

| Campo antigo | O que parece | O que é de verdade | Use no lugar |
| --- | --- | --- | --- |
| `data_lib_prod` | Dia em que a Produção liberou | **Estimativa** calculada pelo SAP: a maior entre `data_lib_fin` e `data_pagto`, **mais 3 dias corridos**. Por isso cai em sábado, domingo e no futuro. Acerta o dia em só 12 de 262 pedidos | **`lib_producao_em`** |
| `data_lib_fin` | Dia em que o Financeiro liberou | Data **digitada** à mão. Em 188 de 264 pedidos está 1 dia depois do real | **`lib_fin_em`** |
| `data_pagto` | Dia em que o sinal foi pago | Dia em que a **cobrança** do sinal foi emitida — paga ou não | **`sinal_pago_em`** |

---

## 4. As regras

**Liberação**

1. **"Quando foi liberado?" se responde com os campos `*_em`.** Nunca com `data_lib_prod`,
   `data_lib_fin` ou `data_pagto`.

2. **`null` num campo `*_em` quer dizer "não foi possível saber a hora"** — não quer dizer
   "não liberado". Para saber **se** está liberado, continue usando `financeiro`,
   `producao` e `entrega` (`"Liberado"` / `"Bloqueado"` / `"Cancelado"`).
   **Não preencha o vazio com `data_lib_prod`**: ela é uma estimativa, não um fato.

3. **Por que vem `null`:** o motivo mais comum é o **limite do SAP**: ele guarda só as
   **99 últimas versões** de cada pedido. Num pedido alterado muitas vezes (ex.: o 84080, com
   mais de 2.000 alterações), a versão em que o Financeiro liberou já foi apagada pelo
   próprio SAP — e a API não inventa a hora. Os outros casos são raros: pedido sem histórico
   e sinal quitado por um caminho que não registra a hora.

4. **`lib_producao_em` só vem preenchido quando `producao` é `"Liberado"`.** Pedido
   bloqueado sempre vem com `lib_producao_em: null`, mesmo que já tenha sido liberado
   antes (veja a regra 6).

5. **Produção e Entrega são liberadas juntas.** O SAP não separa as duas: `lib_entrega_em`
   é sempre igual a `lib_producao_em`, e a `entrega` **nunca** aparece liberada antes da
   `producao`. O valor original do SAP para a Entrega fica em `entrega_sap` (só para
   conferência).

6. **Como o SAP decide que a Produção está liberada** (confirmado em 281 de 281 pedidos):
   - o **Financeiro** liberou, **e**
   - o pedido **não tem sinal**, **ou** a cobrança de sinal **mais recente** está paga.

   Por isso `lib_producao_em` é o **mais tardio** entre `lib_fin_em` e `sinal_pago_em`.
   Quando o sinal é **reemitido** (uma cobrança nova), o pedido que já tinha pago **volta a
   ficar bloqueado** até a nova ser paga — e `lib_producao_em` volta a `null`.

7. **`lib_producao_em` pode vir preenchido com `lib_fin_em` em `null`.** Acontece quando a
   hora do Financeiro se perdeu (regra 3), mas o sinal foi pago **depois** da versão mais
   antiga que sobrou. Aí o sinal com certeza veio por último, e a hora dele é a da
   liberação. Exemplo: o 84348 (seção 7).

8. **Se o Financeiro bloquear e liberar de novo, vale a liberação mais recente.** É o
   único caso em que `lib_fin_em` de um pedido muda legitimamente para uma hora mais nova.

**Nota fiscal**

9. **`primeira_nf_emitida` nunca é `null`.** É `true` ou `false`. Os outros campos de nota
   (`nf_*`) vêm `null` quando não há nota.

10. **Use `nf_numero_fiscal` para mostrar a nota a uma pessoa.** É o número da DANFE.
    `nf_doc_num` é interno do SAP e serve para conferência com a equipe do SAP.

11. **A nota pode sair antes da liberação da Produção.** Acontece hoje no 84420: nota
    emitida em 25/09 com a Produção bloqueada. A API mostra o que está no SAP; não trate
    como erro.

**Casos especiais**

12. **Pedido cancelado** (`pedido_cancelado: true`, etapas `"Cancelado"`) vem com os 10
    campos novos em `null` e `primeira_nf_emitida: false`. A resposta do cancelado não
    passa por essas consultas — **não** conclua dali que ele nunca teve nota.

13. **Pedidos anteriores a 06/01/2025** vêm com `data_criacao_pn`, `representante` e `nf_*`
    em `null` e `primeira_nf_emitida: false` — a fonte desses dados no SAP começa nessa data.

**Geral**

14. **Escreva o cliente para ignorar campos que ele não conhece.** Campos novos podem
    aparecer sem aviso; campos existentes não mudam de nome nem de tipo sem aviso antes.

15. **Os dados têm até 2 minutos de atraso** (cache da API). `cache_idade_s` diz a idade do
    retrato. `?recarregar=1` força a leitura no SAP — use só quando precisar do instante.

---

## 5. Como atualizar o seu projeto

### Passo a passo

- [ ] Se você usa a **lista** (`/pedidos/situacao`), passe `?campos=completo`.
- [ ] Acrescente os 10 campos ao seu modelo de dados (tipos na seção 5.1), todos opcionais
      menos `primeira_nf_emitida`.
- [ ] Troque **toda** exibição de "liberado em" que use `data_lib_prod`, `data_lib_fin` ou
      `data_pagto` pelos campos `*_em` (seção 3).
- [ ] Mostre **data e hora** (ex.: `19/06/2026 08:26`), não só a data.
- [ ] Para `null` num `*_em`, mostre algo como "hora não disponível" — sem estimar.
- [ ] Se hoje você mostra "detectado pelo Altamira" (a hora em que **o seu sistema**
      percebeu a mudança), pode trocar pela hora real do SAP; mantenha a sua só como
      reserva quando o campo vier `null`.
- [ ] Mostre a nota com `nf_numero_fiscal` e `nf_data` quando `primeira_nf_emitida` for
      `true`.
- [ ] Se você **guarda** `lib_fin_em`, atualize o valor guardado quando a API mandar outro
      (regra 8) — inclusive `null` (seção 7).
- [ ] Teste com os pedidos da seção 7.

### 5.1 Tipos (TypeScript)

```ts
/** Campos acrescentados em 25/09/2026 ao perfil `completo`. */
interface LiberacaoENotaFiscal {
  lib_fin_em: string | null;        // "2026-06-01T08:32:16-03:00"
  sinal_pago_em: string | null;
  lib_producao_em: string | null;
  lib_entrega_em: string | null;    // sempre igual a lib_producao_em
  primeira_nf_emitida: boolean;     // nunca null
  nf_numero_fiscal: number | null;  // número da DANFE
  nf_doc_num: number | null;        // número interno do SAP
  nf_data: string | null;           // "2026-07-07" — sem hora
  data_criacao_pn: string | null;   // "2026-04-29" — sem hora
  representante: string | null;
}
```

### 5.2 Exemplo em Python

```python
import os
from datetime import date, datetime

import requests

BASE = "http://192.168.7.11:8077"
CABECALHO = {"X-API-Key": os.environ["SAP_API_KEY"]}  # nunca no código, nunca na URL


def situacao(numero: int) -> dict:
    r = requests.get(f"{BASE}/pedidos/{numero}/situacao", headers=CABECALHO, timeout=30)
    corpo = r.json()
    if not corpo.get("ok"):  # 404 traz "motivo"; 401/403/503 trazem "error"
        raise RuntimeError(f"HTTP {r.status_code}: {corpo.get('motivo') or corpo.get('error')}")
    return corpo["pedido"]


def quando_liberou(p: dict) -> str:
    """Texto de "Produção liberada" para a tela, sem estimar."""
    if p["producao"] == "Cancelado":
        return "Pedido cancelado"
    if p["producao"] != "Liberado":
        return "Produção bloqueada"
    if p["lib_producao_em"] is None:
        return "Produção liberada (hora não disponível)"
    return "Produção liberada em " + datetime.fromisoformat(p["lib_producao_em"]).strftime("%d/%m/%Y %H:%M")


def nota(p: dict) -> str:
    if not p["primeira_nf_emitida"]:
        return "1ª nota ainda não emitida"
    quando = date.fromisoformat(p["nf_data"]).strftime("%d/%m/%Y") if p["nf_data"] else "data não disponível"
    return f"1ª NF {p['nf_numero_fiscal']} em {quando}"


p = situacao(84008)
print(quando_liberou(p))  # Produção liberada em 19/06/2026 08:26
print(nota(p))            # 1ª NF 31799 em 07/07/2026
```

**Na lista:** "o que teve a Produção liberada nas últimas 24 horas".

```python
from datetime import timedelta, timezone

r = requests.get(f"{BASE}/pedidos/situacao", headers=CABECALHO, timeout=60,
                 params={"campos": "completo", "status": "aberto"})
pedidos = r.json()["pedidos"]
desde = datetime.now(timezone.utc) - timedelta(hours=24)
recentes = [p for p in pedidos
            if p["lib_producao_em"] and datetime.fromisoformat(p["lib_producao_em"]) >= desde]
```

### 5.3 Exemplo em JavaScript

```js
function quandoLiberou(p) {
  if (p.producao === "Cancelado") return "Pedido cancelado";
  if (p.producao !== "Liberado") return "Produção bloqueada";
  if (!p.lib_producao_em) return "Produção liberada (hora não disponível)";
  const d = new Date(p.lib_producao_em); // tem hora e fuso: aqui new Date() é o certo
  return "Produção liberada em " + d.toLocaleString("pt-BR", {
    timeZone: "America/Sao_Paulo", dateStyle: "short", timeStyle: "short",
  });
}

// Data SEM hora: nada de new Date("2026-07-07") — mostraria 06/07 (seção 2.4).
function dataBR(aaaaMmDd) {
  const [a, m, d] = aaaaMmDd.split("-");
  return `${d}/${m}/${a}`;
}

function nota(p) {
  if (!p.primeira_nf_emitida) return "1ª nota ainda não emitida";
  const quando = p.nf_data ? dataBR(p.nf_data) : "data não disponível";
  return `1ª NF ${p.nf_numero_fiscal} em ${quando}`;
}
```

### 5.4 Antes e depois, na tela

| Pedido | Antes (errado) | Depois (certo) |
| --- | --- | --- |
| 84428 | Produção liberada no SAP em **27/09/2026** (domingo, no futuro) | Produção liberada em **23/09/2026 16:51** |
| 84348 | Produção liberada no SAP em **12/09/2026** (sábado) | Produção liberada em **25/09/2026 08:13** |
| 84008 | Produção liberada no SAP em **21/06/2026** (domingo) | Produção liberada em **19/06/2026 08:26** |
| 84080 | Produção liberada no SAP em **19/06/2026** | Produção liberada (**hora não disponível**) |

---

## 6. Acesso e erros

Desde **02/10/2026** cada equipe tem a **sua chave**, com o escopo `leitura` — é o que estas
rotas pedem. Se a sua equipe já recebeu a chave nova, **nada muda no código**: continua sendo
o cabeçalho `X-API-Key`.

- A chave vai **só no cabeçalho**. Na URL (`?key=...`) ela é recusada com **401**.
- Toda chamada fica registrada no servidor (quem chamou, qual rota) por 30 dias.

| Código | O que aconteceu | O que fazer |
| --- | --- | --- |
| **401** | Chave ausente, errada ou enviada na URL | Confira o cabeçalho `X-API-Key` |
| **403** | `"tipo": "sem_permissao"` — a chave vale, mas não tem o escopo `leitura` | Mande-nos o `motivo` da resposta; o escopo é acrescentado **sem trocar a sua chave** |
| **404** | Não dá para afirmar a situação do pedido — leia o campo `motivo` | **Não** quer dizer "sem bloqueio" (contrato, seção 2.3) |
| **503** | O SAP HANA está fora | Não é erro seu. Tente de novo em alguns minutos |

Exemplo de 403:

```json
{
  "ok": false,
  "error": "forbidden",
  "tipo": "sem_permissao",
  "motivo": "A credencial 'sua-equipe' nao tem o escopo 'leitura'."
}
```

Tabela completa de erros: contrato, seção 10.

---

## 7. Casos reais para testar

Conferidos em **02/10/2026**. Os pedidos mudam com o tempo (sinal reemitido, nota nova),
então use a lista como ponto de partida, não como resultado fixo para sempre.

| Pedido | Situação | O que a API devolve |
| --- | --- | --- |
| **84428** | Sem sinal, já faturado | `lib_fin_em` = `lib_producao_em` = `lib_entrega_em` = `2026-09-23T16:51:16-03:00`; `sinal_pago_em: null`; `primeira_nf_emitida: true`; `nf_numero_fiscal: 32310`; `nf_doc_num: 5823`; `nf_data: "2026-09-30"` |
| **84008** | Sinal pago **depois** do Financeiro | `lib_fin_em` = `2026-06-01T08:32:16-03:00`; `sinal_pago_em` = `lib_producao_em` = `2026-06-19T08:26:59-03:00`; `nf_numero_fiscal: 31799`; `nf_data: "2026-07-07"` |
| **84348** | Histórico cortado pelo SAP, sinal pago depois (regra 7) | `lib_fin_em: null`; `sinal_pago_em` = `lib_producao_em` = `2026-09-25T08:13:35-03:00`; `primeira_nf_emitida: false` |
| **84080** | Histórico cortado pelo SAP, sem sinal (regra 3) | `producao: "Liberado"`, **mas** `lib_fin_em`, `lib_producao_em` e `lib_entrega_em` = `null`; `primeira_nf_emitida: true`; `nf_numero_fiscal: 32228`; `nf_doc_num: 5729`; `nf_data: "2026-09-17"` |
| **84420** | Sinal reemitido, em aberto | `producao: "Bloqueado"`; `lib_fin_em` = `2026-09-22T08:44:01-03:00`; `sinal_pago_em`, `lib_producao_em` e `lib_entrega_em` = `null`; **mas** `primeira_nf_emitida: true` (DANFE 32280, regra 11) |

Para testar:

```bash
curl -s -H "X-API-Key: $SAP_API_KEY" "http://192.168.7.11:8077/pedidos/84008/situacao"
```

```powershell
$H = @{ "X-API-Key" = $env:SAP_API_KEY }
$p = (Invoke-RestMethod -Uri "http://192.168.7.11:8077/pedidos/84008/situacao" -Headers $H).pedido
$p | Select-Object doc_num, producao, lib_fin_em, sinal_pago_em, lib_producao_em, primeira_nf_emitida, nf_numero_fiscal
```

> **Se você consultou antes de 02/10:** o 84348 vinha com `lib_fin_em` =
> `2026-09-08T16:30:26-03:00` e o 84080 com `lib_producao_em` = `2026-08-27T08:33:08-03:00`.
> As duas horas estavam erradas (eram a versão mais antiga que o SAP ainda guardava, não a
> liberação) e agora vêm `null`. Se você guardou essas horas, atualize-as com o que a API
> devolve hoje.

---

## 8. Perguntas frequentes

**Meu código vai quebrar?**
Não. Só entraram campos; nenhum saiu nem mudou de tipo.

**Por que `lib_producao_em` veio `null` com a Produção liberada?**
Na maioria das vezes, porque o SAP já apagou a versão do pedido em que o Financeiro
liberou — ele guarda só as 99 últimas (regra 3). Hoje são 18 de 284 pedidos com a Produção
liberada. Mostre "hora não disponível".

**O `lib_fin_em` de um pedido mudou. É erro?**
Pode ser uma de duas coisas. (1) O Financeiro bloqueou e liberou de novo: vale a liberação
mais recente (regra 8). (2) Até 02/10, a API mostrava uma hora errada em pedido com mais de
99 alterações, e essa hora andava para frente a cada alteração nova; agora esse caso vem
`null` (seção 7).

**Posso usar `data_lib_prod` quando `lib_producao_em` vier `null`?**
Não. Ela erra o dia real de 34 dias para trás até 15 para frente, e acerta em só 12 de 262
pedidos. Mostrar uma estimativa como se fosse fato foi exatamente o problema que isto
corrige.

**Qual a diferença entre `representante` e `vendedor`?**
`vendedor` vem do cadastro de vendedor do pedido; `representante`, da view de orçamentos do
SAP. Hoje divergem em 2 de 294 pedidos. Use o que o seu negócio já usa.

**Por que só a primeira nota?**
É a que diz se o pedido começou a ser faturado. Um pedido pode ter muitas notas (há um com
42); a lista completa não está nesta API.

**Recebi a chave nova da minha equipe. Preciso mudar algo?**
Só o valor do cabeçalho `X-API-Key`. Se ela estiver na URL (`?key=`), passe para o
cabeçalho: na URL ela não vale.

**O assistente de IA (MCP) já sabe disso?**
Sim. A tool `situacao_pedido` responde "quando foi liberado" pelos campos `*_em` e diz "hora
não disponível" quando eles vêm `null`.

---

Dúvidas: fale com a equipe do OrçaView / servidor de integração SAP.
