# API — Ordens de Produção (consulta e mudança de status no SAP)

Documento para quem vai **consumir** a API. Descreve dois endpoints do Servidor de
Integração SAP (`192.168.7.11:8077`):

- **consultar** uma Ordem de Produção (status, item, quantidade);
- **mudar o status** de uma OP para **Liberada** ou **Encerrada** — a mudança acontece
  **dentro do SAP**, na base de produção, na hora.

Não é um espelho nem uma fila: a chamada vai direto ao SAP pelo Service Layer e a resposta
já reflete o que ficou gravado.

> Os endpoints de **Ordens de Serviço** (`/ordens-servico/...`) são outra coisa e estão
> documentados em `API_OS_INTEGRACAO.md`.

---

## 1. Antes de tudo: `DocNum` ≠ `DocEntry`

A mesma OP tem **dois números** no SAP, e trocá-los é o erro mais comum:

| | O que é | Exemplo |
| --- | --- | --- |
| **DocNum** | O número que aparece na tela do SAP. É o que as pessoas falam. | `129850` |
| **DocEntry** | A chave interna da tabela. Ninguém decora. | `131431` |

**A API usa DocNum por padrão.** Se você tem o DocEntry, acrescente `?chave=docentry`.

As respostas sempre devolvem os dois (`doc_num` e `doc_entry`), então dá para conferir.

---

## 2. Base URL e autenticação

```
http://192.168.7.11:8077
```

Toda chamada exige o cabeçalho:

```
X-API-Key: <sua chave>
```

Peça a chave ao Marcelo — ela não vai neste documento. Também é aceito
`Authorization: Bearer <chave>`.

Sem chave, ou com chave errada: **401**.

---

## 3. `GET /ordens-producao/{numero}` — consultar

Leitura pura, não muda nada no SAP.

```bash
curl "http://192.168.7.11:8077/ordens-producao/129850" \
     -H "X-API-Key: SUA_CHAVE"
```

Resposta real (OP 129850, em 07/08/2026):

```json
{
  "ok": true,
  "op": {
    "doc_num": 129850,
    "doc_entry": 131431,
    "item": "PPLPRTGALVA175000000#0#0#1050",
    "quantidade_planejada": 12.0,
    "status": "boposPlanned",
    "status_desc": "Planejada",
    "origem": "bopooSalesOrder",
    "origem_numero": 83955,
    "data_entrega": "2026-06-07",
    "transicoes_permitidas": ["liberada", "encerrada"]
  }
}
```

Pelo DocEntry:

```bash
curl "http://192.168.7.11:8077/ordens-producao/131431?chave=docentry" \
     -H "X-API-Key: SUA_CHAVE"
```

### O campo que economiza seu trabalho: `transicoes_permitidas`

Já vem filtrado pelo status atual da OP **e** pelo que a API tem permissão de fazer. Use
ele para habilitar/desabilitar botões na sua tela em vez de reimplementar a regra:

| Status da OP | `transicoes_permitidas` |
| --- | --- |
| Planejada | `["liberada", "encerrada"]` |
| Liberada | `["encerrada"]` |
| Encerrada | `[]` |
| Cancelada | `[]` |

---

## 4. `POST /ordens-producao/{numero}/status` — mudar o status

```bash
curl -X POST "http://192.168.7.11:8077/ordens-producao/129850/status" \
     -H "X-API-Key: SUA_CHAVE" \
     -H "Content-Type: application/json" \
     -d '{"status":"liberada","status_atual":"planejada"}'
```

### Corpo

| Campo | Obrigatório | Valores | Para que serve |
| --- | --- | --- | --- |
| `status` | **sim** | `"liberada"` ou `"encerrada"` (também aceita `boposReleased` / `boposClosed`) | O status que a OP deve ficar |
| `status_atual` | não, mas **recomendado** | o status que você acredita que a OP tem agora | Se não bater com o SAP, a API **não escreve** e devolve 409 |

`status_atual` é a sua proteção contra duas pessoas mexendo na mesma OP. Sem ele, quem
clicar por último vence, mesmo trabalhando com a tela desatualizada. Mande sempre o valor
que veio do `GET`.

### Resposta

```json
{
  "ok": true,
  "doc_num": 129850,
  "doc_entry": 131431,
  "item": "PPLPRTGALVA175000000#0#0#1050",
  "status_anterior": "boposPlanned",
  "status_novo": "boposReleased",
  "ja_estava": false
}
```

`ja_estava: true` significa que a OP **já estava** no status pedido e nada foi enviado ao
SAP. Isso é normal e é a resposta certa — não trate como erro.

---

## 5. O que pode e o que não pode

| Status atual | → `liberada` | → `encerrada` |
| --- | --- | --- |
| **Planejada** | ✅ muda | ✅ muda |
| **Liberada** | 200 `ja_estava: true` | ✅ muda |
| **Encerrada** | ⛔ 409 | 200 `ja_estava: true` |
| **Cancelada** | ⛔ 409 | ⛔ 409 |

**Fora do escopo desta API** (pedido desses volta **400**, sem sequer consultar o SAP):

- cancelar uma OP;
- voltar uma OP para Planejada;
- criar OP, mudar quantidade, item, datas ou componentes.

Encerrada e Cancelada são **finais**: no SAP não há caminho de volta a partir delas.

---

## 6. Códigos de resposta

| HTTP | `tipo` | O que aconteceu | O que fazer |
| --- | --- | --- | --- |
| `200` | — | Mudou, ou já estava no status pedido (`ja_estava`) | Seguir |
| `400` | `status_invalido` | Status desconhecido, ou fora do que a API faz (ex.: cancelar) | Corrigir a chamada |
| `400` | — | Número da OP inválido (não é inteiro positivo) | Corrigir a chamada |
| `401` | — | `X-API-Key` faltando ou errada | Conferir a chave |
| `404` | `nao_encontrada` | Não existe OP com esse número no SAP | Conferir o número |
| `409` | `transicao_invalida` | A OP está Encerrada ou Cancelada | Nada a fazer — é final |
| `409` | `conflito` | `status_atual` não bate com o SAP (alguém mudou antes) | Recarregar com o `GET` e decidir de novo |
| `409` | `ambigua` | O número casou com mais de uma OP | Repetir usando `?chave=docentry` |
| `429` | `rate_limited` | Trava anti-loop (20 escritas/min) | Respeitar o header `Retry-After` |
| `502` | `indisponivel` | SAP fora do ar, ou o SAP recusou a mudança | Ver `motivo`; tentar mais tarde |
| `503` | `desativado` | A integração está desligada no servidor | Avisar o TI |
| `503` | `sem_chave` | O servidor está sem chave configurada | Avisar o TI |

Todo erro vem no mesmo formato, com uma frase pronta para mostrar ao usuário:

```json
{
  "ok": false,
  "tipo": "conflito",
  "motivo": "A OP 129850 esta como Liberada no SAP, nao Planejada. Alguem mudou enquanto isso - recarregue e tente de novo.",
  "status_atual": "boposReleased"
}
```

Decida pelo **`tipo`**, não pelo texto do `motivo` — o texto pode mudar.

---

## 7. Exemplos completos

### Python

```python
import requests

BASE = "http://192.168.7.11:8077"
HEADERS = {"X-API-Key": "SUA_CHAVE"}
TIMEOUT = (5, 40)   # (conexao, leitura): o SAP pode demorar na 1a chamada do dia


def consultar(doc_num: int) -> dict:
    r = requests.get(f"{BASE}/ordens-producao/{doc_num}", headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()["op"]


def encerrar(doc_num: int) -> dict:
    """Encerra a OP conferindo antes que ninguem mexeu nela."""
    op = consultar(doc_num)

    if op["status"] == "boposClosed":
        return op                          # ja encerrada, nada a fazer

    if "encerrada" not in op["transicoes_permitidas"]:
        raise RuntimeError(f"OP {doc_num} esta {op['status_desc']} - nao da para encerrar")

    r = requests.post(
        f"{BASE}/ordens-producao/{doc_num}/status",
        headers=HEADERS, timeout=TIMEOUT,
        json={"status": "encerrada", "status_atual": op["status"]},
    )
    if r.status_code == 409:
        raise RuntimeError(r.json()["motivo"])   # alguem mudou no meio do caminho
    r.raise_for_status()
    return r.json()


print(encerrar(129850))
```

### JavaScript (fetch)

```js
const BASE = 'http://192.168.7.11:8077';
const HEADERS = { 'X-API-Key': 'SUA_CHAVE', 'Content-Type': 'application/json' };

async function mudarStatus(docNum, status, statusAtual) {
  const r = await fetch(`${BASE}/ordens-producao/${docNum}/status`, {
    method: 'POST',
    headers: HEADERS,
    body: JSON.stringify({ status, status_atual: statusAtual }),
  });
  const body = await r.json();
  if (!r.ok) {
    // body.tipo diz o que houve; body.motivo ja vem pronto para a tela
    throw Object.assign(new Error(body.motivo), { tipo: body.tipo, http: r.status });
  }
  return body;
}

// fluxo recomendado: ler, mostrar, e so entao escrever com o status_atual travado
const { op } = await (await fetch(`${BASE}/ordens-producao/129850`, { headers: HEADERS })).json();
if (op.transicoes_permitidas.includes('liberada')) {
  await mudarStatus(op.doc_num, 'liberada', op.status);
}
```

### PowerShell

```powershell
$headers = @{ 'X-API-Key' = 'SUA_CHAVE' }
$op = (Invoke-RestMethod "http://192.168.7.11:8077/ordens-producao/129850" -Headers $headers).op
$corpo = @{ status = 'liberada'; status_atual = $op.status } | ConvertTo-Json
Invoke-RestMethod "http://192.168.7.11:8077/ordens-producao/129850/status" `
  -Method Post -Headers $headers -ContentType 'application/json' -Body $corpo
```

---

## 8. Cinco recomendações para quem integra

1. **Sempre `GET` antes do `POST`**, e mande o `status` que veio de lá como `status_atual`.
   É uma chamada barata que evita sobrescrever a decisão de outra pessoa.
2. **Use `transicoes_permitidas` na interface.** Botão que não pode ser clicado não deveria
   estar habilitado — deixar o usuário descobrir pelo erro é pior.
3. **Repetir é seguro.** Se a resposta se perdeu (timeout, rede), pode repetir a mesma
   chamada: se já tinha aplicado, volta `200 ja_estava: true` e nada é feito duas vezes.
4. **Configure timeout de leitura generoso** (≥ 30s). A primeira chamada depois de um
   restart do SAP paga a carga interna dele e é a mais lenta do dia.
5. **Não faça laço.** O limite é 20 escritas por minuto e existe para proteger o SAP; ao
   receber `429`, espere o `Retry-After` em vez de retentar em seguida.

---

## 9. Suporte

- Endpoint de saúde, aberto e sem chave: `GET http://192.168.7.11:8077/health`
- Diagnóstico completo: `GET http://192.168.7.11:8077/status`
- Dúvida, campo faltando ou erro `502`/`503`: falar com o Marcelo (TI).

Se um `motivo` de erro não estiver claro o suficiente para ser mostrado ao usuário final,
avise — a mensagem pode ser melhorada do nosso lado.
