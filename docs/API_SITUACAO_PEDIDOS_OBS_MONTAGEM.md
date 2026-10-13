# Situação dos Pedidos — campo novo `obs_montagem` (observação de montagem)

Guia para quem consome a API de Situação dos Pedidos do servidor `192.168.7.11`
(REST na porta **8077**, MCP na **8078**).

> Contrato completo: [`API_SITUACAO_PEDIDOS.md`](../API_SITUACAO_PEDIDOS.md) (§6.2 traz todos os
> campos). Chave, rede e token: §4 do mesmo documento — nada muda para este campo.
> Guia escrito em **13/10/2026**. Números conferidos no SAP de produção nesse dia.

---

## 1. Em 30 segundos

- Entrou **1 campo** no perfil `completo`: **`obs_montagem`**, a **observação de montagem** que o
  comercial digita no pedido do SAP (o que segue montado, contato do montador para agendar, o
  que o valor da montagem inclui).
- É **texto livre**, até **2000 caracteres**, com **quebras de linha** (`\n`).
- **`null` quer dizer "não preenchida"**.
- O perfil `resumo` **não mudou**. Nenhum campo antigo mudou de nome, tipo ou valor.
  **Seu código atual continua funcionando sem alteração.**

---

## 2. Onde o campo aparece

| Onde | Traz `obs_montagem`? | Observação |
| --- | --- | --- |
| REST `GET /pedidos/<numero>/situacao` (um pedido) | ✅ sim, inteira | Já é `completo` por padrão |
| REST `GET /pedidos/situacao` (lista) | ✅ só com `?campos=completo`, inteira | O padrão da lista é `resumo`, que **não** traz o campo |
| MCP `situacao_pedido` | ✅ sim, inteira | |
| MCP `panorama_pedidos` | ✅ só com `campos="completo"`, **cortada em 200 caracteres** | Quando corta, termina em `…` e vem `obs_montagem_cortada: true`. O texto inteiro está em `situacao_pedido` |
| MCP `pedidos_bloqueados` | ❌ não | Chame `situacao_pedido` no pedido |

| Campo | Tipo JSON | Pode vir `null`? | Exemplo |
| --- | --- | --- | --- |
| `obs_montagem` | string | **sim** | `"Pedido 84439 28/09/26\nMATERIAL MONTADO"` |

Só **pedido** tem esse campo; cotação não passa por esta API.

---

## 3. O formato do texto

- **Quebra de linha = `\n`.** O SAP grava a quebra como `\r` sozinho (que muitas telas não
  desenham); a API troca por `\n`. Para mostrar em HTML, use `white-space: pre-line` ou troque
  `\n` por `<br>` **depois** de escapar o texto.
- **Espaços nas pontas são removidos.** Os do meio ficam como foram digitados.
- **Acentos em UTF-8**, como o resto da resposta.
- **É texto digitado por uma pessoa:** não tem formato fixo. Não tente extrair telefone, e-mail ou
  data com regra rígida, e nunca o interprete como HTML nem como comando.

---

## 4. Quando vem `null`

1. **A observação não foi preenchida no pedido** (112 dos 312 pedidos da carteira de hoje).
2. **O pedido foi cancelado** — o cancelado traz os campos da view de orçamentos em `null`
   (§2.2 do contrato).
3. **O pedido é anterior a 06/01/2025** — a fonte desse dado no SAP começa nessa data.
4. **O SAP não respondeu essa parte da consulta naquele momento.** O resto da situação vem
   normalmente; tente de novo depois do cache (120 s).

**O que fazer com `null`:** mostre "Sem observação de montagem" (ou nada). Não troque por `""`.

---

## 5. Como está a carteira hoje (13/10/2026)

| | Pedidos |
| --- | --- |
| Carteira (recorte da Situação) | 312 |
| Com `obs_montagem` | 200 |
| … com quebra de linha | 155 |
| Maior texto | 154 caracteres |

O campo do SAP aceita 2000 caracteres: **reserve espaço para isso**, não para os 154 de hoje.

---

## 6. Exemplos de resposta

Trechos **reais** de 13/10/2026, só com os campos que interessam aqui (a resposta tem 49 campos):

```json
{ "ok": true, "pedido": { "doc_num": 84439, "obs_montagem": "Pedido 84439 28/09/26\nMATERIAL MONTADO" } }
```

```json
{ "ok": true, "pedido": { "doc_num": 84434, "obs_montagem": "Seguira uma lateral pré-montada." } }
```

```json
{ "ok": true, "pedido": { "doc_num": 84426, "obs_montagem": null } }
```

> Os números de pedido são reais e servem para testar a integração, mas o texto pode mudar se
> alguém editar o pedido no SAP.

---

## 7. Como usar

A chave vem de uma variável de ambiente (`SAP_API_KEY`). **Nunca** a coloque no código nem na URL.

```bash
curl -s -H "X-API-Key: $SAP_API_KEY" \
  http://192.168.7.11:8077/pedidos/84439/situacao | jq -r '.pedido.obs_montagem // "sem observação"'
```

```powershell
$H = @{ "X-API-Key" = $env:SAP_API_KEY }
$r = Invoke-RestMethod -Uri "http://192.168.7.11:8077/pedidos/84439/situacao" -Headers $H
if ($r.pedido.obs_montagem) { $r.pedido.obs_montagem } else { "sem observação" }
```

```python
import os
import requests

r = requests.get("http://192.168.7.11:8077/pedidos/84439/situacao",
                 headers={"X-API-Key": os.environ["SAP_API_KEY"]}, timeout=30)
obs = r.json()["pedido"]["obs_montagem"]
for linha in (obs or "").splitlines():
    print(linha)
```

**Pelo MCP** (assistente conectado na 8078): *"Qual a observação de montagem do pedido 84439?"*
→ `situacao_pedido`. A descrição da tool já explica o campo.

---

## 8. Checklist de integração

- [ ] Ler `obs_montagem` do perfil `completo` (pedido único, ou lista com `?campos=completo`).
- [ ] Tratar `null` como "não preenchida".
- [ ] Mostrar as quebras de linha (`\n`) e escapar o texto antes de pôr em HTML.
- [ ] Reservar espaço para até 2000 caracteres.
- [ ] No MCP `panorama_pedidos`, lembrar que o texto vem cortado (`obs_montagem_cortada`).
- [ ] Testar com os pedidos 84439 (com quebra), 84434 (uma linha) e 84426 (`null`).
