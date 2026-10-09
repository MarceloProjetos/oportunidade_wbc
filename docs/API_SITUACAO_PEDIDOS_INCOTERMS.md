# Situação dos Pedidos — campo novo `incoterms` (modalidade de frete)

Guia para quem consome a API de Situação dos Pedidos do servidor `192.168.7.11`
(REST na porta **8077**, MCP na **8078**). Explica **o que o campo diz**, **onde ele aparece**,
**o que pode vir nele**, **os cuidados** e **como usar**, com exemplos prontos para copiar.

> Contrato completo da API: [`API_SITUACAO_PEDIDOS.md`](../API_SITUACAO_PEDIDOS.md) (§6.2 traz
> todos os campos). Chave, rede e token: §4 do mesmo documento — nada muda para este campo.
> Guia escrito em **09/10/2026**. Números conferidos no SAP de produção nesse dia.

---

## 1. Em 30 segundos

- Entrou **1 campo** no perfil `completo`: **`incoterms`**, a **modalidade de frete** do pedido
  (quem paga e quem faz o transporte).
- Ele vem **em texto, pronto para mostrar**: `"CIF - Remetente"`, `"FOB - Destinatário"`,
  `"Próprio Remetente"`… (lista completa na seção 3).
- **`null` quer dizer "não informado"** — nunca "sem frete" e nunca "CIF".
- O perfil `resumo` **não mudou**. Nenhum campo antigo mudou de nome, tipo ou valor.
  **Seu código atual continua funcionando sem alteração.**

---

## 2. O que é "Incoterms" aqui

No SAP, cada pedido de venda tem, na aba de impostos, o campo **Incoterms**. Na Altamira
ele guarda a **modalidade do frete**: a mesma informação que vai para a nota fiscal
eletrônica (o campo "Modalidade do frete" da NF-e). Ele responde duas perguntas:

1. **Quem paga o frete?** A Altamira (remetente) ou o cliente (destinatário)?
2. **Quem transporta?** Uma transportadora contratada, um terceiro, ou veículo próprio?

> Apesar do nome, **não** são os Incoterms internacionais (EXW, FCA, DAP…). São as modalidades
> de frete da NF-e brasileira. CIF e FOB aqui significam "frete por conta do remetente" e
> "frete por conta do destinatário".

---

## 3. Os valores possíveis

| Texto que vem em `incoterms` | Código no SAP | Quem paga o frete | Quem transporta | Em linguagem simples |
| --- | --- | --- | --- | --- |
| `"CIF - Remetente"` | `0` | **Altamira** | Transportadora contratada pela Altamira | "A Altamira entrega, frete incluso" |
| `"FOB - Destinatário"` | `1` | **Cliente** | Transportadora do cliente, ou o cliente retira | "O cliente busca / paga o frete" |
| `"Terceiros"` | `2` | Um terceiro (nem Altamira nem cliente) | Contratado por esse terceiro | Caso raro |
| `"Próprio Remetente"` | `3` | **Altamira** | **Veículo da própria Altamira** | "Vai no nosso caminhão" |
| `"Próprio Destinatário"` | `4` | **Cliente** | **Veículo do próprio cliente** | "O cliente busca com o caminhão dele" |
| `"Sem Frete"` | `9` | — | Não há transporte | Ex.: serviço, retirada sem transporte |
| `null` | vazio | ? | ? | **Não informado** no pedido. Não suponha nada |

**Atenção aos acentos:** `"FOB - Destinatário"` e `"Próprio …"` vêm com acento, em UTF-8.
Compare o texto **exatamente** como está na tabela, ou use o mapa da seção 7.

### 3.1 Como está a carteira hoje (09/10/2026, 309 pedidos)

| `incoterms` | Pedidos |
| --- | --- |
| `"Próprio Remetente"` | 174 |
| `"CIF - Remetente"` | 101 |
| `"FOB - Destinatário"` | 29 |
| `null` | 5 |

Os outros três textos (`Terceiros`, `Próprio Destinatário`, `Sem Frete`) **não aparecem hoje**,
mas são válidos e podem aparecer amanhã. Seu código precisa aceitá-los.

---

## 4. Onde o campo aparece

O campo só existe no perfil **`completo`**:

| Onde | Traz `incoterms`? | Como pedir |
| --- | --- | --- |
| REST `GET /pedidos/<numero>/situacao` (um pedido) | ✅ sim | Já é `completo` por padrão |
| REST `GET /pedidos/situacao` (lista) | ✅ só com `?campos=completo` | O padrão da lista é `resumo`, que **não** traz o campo |
| MCP `situacao_pedido` | ✅ sim | Pergunte pelo pedido normalmente |
| MCP `panorama_pedidos` | ✅ só com `campos="completo"` | |
| MCP `pedidos_bloqueados` | ❌ não | Para saber o frete, chame `situacao_pedido` no pedido |

### 4.1 Tipo do campo

| Campo | Tipo JSON | Pode vir `null`? | Exemplo |
| --- | --- | --- | --- |
| `incoterms` | string | **sim** | `"CIF - Remetente"` |

---

## 5. Quando vem `null`

`null` sempre quer dizer **"não dá para dizer qual é o frete"**. Acontece em quatro casos:

1. **O pedido está sem Incoterms preenchido no SAP** (5 dos 309 pedidos de hoje).
2. **O pedido foi cancelado.** A resposta do cancelado (`status_pedido: "Cancelado"`) traz
   todos os campos de nota e frete em `null`. Ver §2.2 do contrato.
3. **O pedido é anterior a 06/01/2025.** A fonte desse dado no SAP começa nessa data.
4. **O SAP não respondeu essa parte da consulta naquele momento.** A situação do pedido
   continua vindo normalmente; só os campos dessa parte vêm `null`. Tente de novo depois do
   cache (120 s).

**O que fazer com `null`:** mostre "Frete não informado". **Não** troque por CIF, **não**
troque por "Sem Frete". São afirmações diferentes, e "Sem Frete" é um valor que existe.

---

## 6. Exemplos de resposta

Trechos **reais** de 09/10/2026, só com os campos que interessam aqui (a resposta tem 48
campos; os outros estão no contrato):

**CIF (pedido 84466)**

```json
{
  "ok": true,
  "pedido": {
    "doc_num": 84466,
    "status_pedido": "Aberto",
    "incoterms": "CIF - Remetente"
  }
}
```

**FOB (pedido 84447)**

```json
{ "ok": true, "pedido": { "doc_num": 84447, "incoterms": "FOB - Destinatário" } }
```

**Veículo próprio da Altamira (pedido 84474)**

```json
{ "ok": true, "pedido": { "doc_num": 84474, "incoterms": "Próprio Remetente" } }
```

**Sem Incoterms preenchido**

```json
{ "ok": true, "pedido": { "doc_num": 84452, "incoterms": null } }
```

> Os números de pedido acima são reais e servem para você testar a sua integração, mas o
> valor pode mudar se alguém editar o pedido no SAP.

---

## 7. Como usar — exemplos prontos

Em todos os exemplos a chave vem de uma variável de ambiente (`SAP_API_KEY`). **Nunca** a
coloque no código nem na URL.

### 7.1 `curl` — um pedido

```bash
curl -s -H "X-API-Key: $SAP_API_KEY" \
  http://192.168.7.11:8077/pedidos/84466/situacao | jq '.pedido.incoterms'
# "CIF - Remetente"
```

### 7.2 `curl` — a carteira inteira, contando por frete

```bash
curl -s -H "X-API-Key: $SAP_API_KEY" \
  "http://192.168.7.11:8077/pedidos/situacao?campos=completo" \
  | jq '[.pedidos[].incoterms] | group_by(.) | map({frete: .[0], pedidos: length})'
```

### 7.3 PowerShell 5.1

```powershell
$H = @{ "X-API-Key" = $env:SAP_API_KEY }

# Um pedido
$r = Invoke-RestMethod -Uri "http://192.168.7.11:8077/pedidos/84466/situacao" -Headers $H
$r.pedido.incoterms

# Todos os pedidos FOB (o cliente paga o frete)
$lista = Invoke-RestMethod -Uri "http://192.168.7.11:8077/pedidos/situacao?campos=completo" -Headers $H
$lista.pedidos | Where-Object { $_.incoterms -eq "FOB - Destinatário" } |
    Select-Object doc_num, card_name, incoterms
```

> No PowerShell 5.1, se o arquivo `.ps1` for salvo **sem BOM**, o `"Destinatário"` escrito no
> script pode ser lido com acento errado e a comparação falha calada. Salve o script em
> **UTF-8 com BOM**, ou compare só pelo início: `$_.incoterms -like "FOB*"`.

### 7.4 Python

```python
import os
import requests

BASE = "http://192.168.7.11:8077"
SESSAO = requests.Session()
SESSAO.headers["X-API-Key"] = os.environ["SAP_API_KEY"]

# What the freight mode means: who pays, who transports.
FRETE = {
    "CIF - Remetente":      {"codigo": "0", "paga": "Altamira", "transporte": "transportadora"},
    "FOB - Destinatário":   {"codigo": "1", "paga": "cliente",  "transporte": "transportadora ou retirada"},
    "Terceiros":            {"codigo": "2", "paga": "terceiro", "transporte": "terceiro"},
    "Próprio Remetente":    {"codigo": "3", "paga": "Altamira", "transporte": "veículo da Altamira"},
    "Próprio Destinatário": {"codigo": "4", "paga": "cliente",  "transporte": "veículo do cliente"},
    "Sem Frete":            {"codigo": "9", "paga": None,       "transporte": "sem transporte"},
}


def frete_do_pedido(doc_num: int) -> str:
    r = SESSAO.get(f"{BASE}/pedidos/{doc_num}/situacao", timeout=30)
    if r.status_code == 404:
        return "não foi possível consultar o pedido"   # 404 = "não sei"; ver §2.3 do contrato
    r.raise_for_status()
    inc = r.json()["pedido"]["incoterms"]
    if inc is None:
        return "frete não informado"
    info = FRETE.get(inc)
    if info is None:
        return f"frete: {inc}"   # unknown value: show it as it came, never guess
    return f"{inc} — paga: {info['paga'] or '—'}; transporte: {info['transporte']}"


print(frete_do_pedido(84466))
# CIF - Remetente — paga: Altamira; transporte: transportadora

# Orders where the CUSTOMER pays the freight (FOB or own vehicle)
carteira = SESSAO.get(f"{BASE}/pedidos/situacao", params={"campos": "completo"}, timeout=60).json()
cliente_paga = [p["doc_num"] for p in carteira["pedidos"]
                if FRETE.get(p["incoterms"] or "", {}).get("paga") == "cliente"]
print(len(cliente_paga), "pedidos com frete por conta do cliente")
```

### 7.5 JavaScript / TypeScript

```ts
type Incoterms =
  | "CIF - Remetente"
  | "FOB - Destinatário"
  | "Terceiros"
  | "Próprio Remetente"
  | "Próprio Destinatário"
  | "Sem Frete";

interface PedidoCompleto {
  doc_num: number;
  // ...other contract fields...
  incoterms: Incoterms | string | null; // `string`: accept a value that is new to you
}

const ROTULO_CURTO: Record<string, string> = {
  "CIF - Remetente": "CIF",
  "FOB - Destinatário": "FOB",
  "Terceiros": "Terceiros",
  "Próprio Remetente": "Próprio (Altamira)",
  "Próprio Destinatário": "Próprio (cliente)",
  "Sem Frete": "Sem frete",
};

function textoFrete(p: PedidoCompleto): string {
  if (p.incoterms === null) return "Frete não informado";
  return ROTULO_CURTO[p.incoterms] ?? p.incoterms; // unknown value passes through as-is
}

const r = await fetch("http://192.168.7.11:8077/pedidos/84466/situacao", {
  headers: { "X-API-Key": process.env.SAP_API_KEY! },
});
const { pedido } = await r.json();
console.log(textoFrete(pedido)); // "CIF"
```

### 7.6 MCP (assistente de IA)

Com o servidor MCP registrado (§8.1 do contrato), perguntas assim funcionam:

- *"O frete do pedido 84466 é CIF ou FOB?"* → a IA chama `situacao_pedido` e lê `incoterms`.
- *"Quem paga o frete do pedido 84447?"* → idem. A descrição da tool explica cada valor.
- *"Quantos pedidos em aberto são FOB?"* → `panorama_pedidos(campos="completo")` e conta
  `incoterms`. É uma consulta pesada (~435 KB); para um pedido só, prefira `situacao_pedido`.

A descrição da tool já instrui a IA a dizer **"frete não informado"** quando o campo vem
`null`, em vez de supor CIF. Se você escreve o seu próprio prompt, reforce isso.

---

## 8. Cuidados (leia antes de codar)

1. **É texto, não código.** O campo traz o rótulo (`"CIF - Remetente"`), não o número (`0`).
   Se você precisa do código, use o mapa da seção 3 / 7.4.
2. **Aceite valor desconhecido.** Se o SAP ganhar um código que a regra não conhece, o campo
   traz **o código cru** (ex.: `"03"`). Hoje nenhum pedido da carteira está assim, mas o seu
   código não pode quebrar: mostre o valor como veio.
3. **`null` ≠ "Sem Frete" ≠ CIF.** Ver seção 5.
4. **Só pedido tem frete.** Cotação não tem Incoterms. Esta API só fala de pedidos, então isso
   só importa se você também ler outra fonte de cotações.
5. **Cache de 120 s.** Alguém mudou o frete no SAP agora? A API mostra o valor novo em até
   2 minutos.
6. **Só no `completo`.** Se o campo "sumiu", confira se a lista foi chamada com
   `?campos=completo`.

---

## 9. Checklist para atualizar o seu projeto

- [ ] Na lista, pedir `?campos=completo` se for mostrar o frete.
- [ ] Ler `pedido.incoterms` (um pedido) ou `pedidos[].incoterms` (lista).
- [ ] Mostrar `null` como "Frete não informado".
- [ ] Aceitar os 6 textos da seção 3 **e** um valor desconhecido, sem quebrar.
- [ ] Comparar texto com acento (UTF-8) ou usar o mapa.
- [ ] Testar com os pedidos 84466 (CIF), 84447 (FOB) e 84474 (Próprio Remetente).

---

## 10. Antes de nos chamar

| Sintoma | Causa provável |
| --- | --- |
| O campo `incoterms` não existe na resposta | Lista chamada sem `?campos=completo`, ou cliente com cache antigo de antes de 09/10/2026 |
| Veio `null` num pedido que tem frete no SAP | Pedido anterior a 06/01/2025, cancelado, ou o SAP não respondeu essa parte agora (seção 5) |
| `"FOB - Destinatário"` não bate na comparação | Acento lido errado: script salvo sem UTF-8 (ver 7.3) |
| Veio um número, como `"03"` | Código que a regra do SAP não traduz. Mostre como veio e nos avise |
| 401 / 403 / 404 | Não é deste campo: ver §10 e §13 do contrato |
