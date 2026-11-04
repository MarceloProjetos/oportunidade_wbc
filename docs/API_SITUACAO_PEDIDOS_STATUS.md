# Situação dos Pedidos — todo pedido agora tem `status_pedido`

Guia para quem consome a API do servidor `192.168.7.11` (REST na porta **8077**).

> Contrato completo: [`API_SITUACAO_PEDIDOS.md`](../API_SITUACAO_PEDIDOS.md). Chave e rede: §4 do
> mesmo documento; nada muda nisso.
> Guia escrito em **04/11/2026**. A mudança vale depois do deploy na .11.

---

## 1. Em 30 segundos

- **A regra nova:** todo pedido que a API envia tem `status_pedido`, qualquer que seja o status.
- **Se o SAP não souber dizer agora,** o pedido sai com o **último status conhecido**.
- **O que muda no projeto de vocês:** ler `status_pedido` e nunca mais mostrar "sem situação".
  O passo a passo está na seção 5.
- **Nenhum campo antigo mudou de nome ou de tipo.** Mudou um valor: em vez de `null`, o status agora vem
  preenchido quando o SAP não responde (seção 3).

---

## 2. O que estava errado

Conferido na API de produção em 04/11/2026:

| Onde | O que acontecia |
| --- | --- |
| `GET /pedidos/situacao` no perfil `resumo`, o padrão | **Os 97 pedidos da lista saíam sem o campo `status_pedido`.** |
| `GET /pedidos/<n>/situacao` com o SAP fora do ar | `status_pedido: null` ou, no `503`, nenhum status. |
| `GET /ordens-servico/<n>` e `/ordens-servico/disponiveis` | `status_pedido: null` quando o SAP não respondia. |
| Pedido fora da lista | A lista não traz esse pedido. Quem só lê a lista não recebe status nenhum dele. |

A API sabia o status, mas não o enviava em todos os caminhos. Do outro lado, a tela ficava sem o que
mostrar e escrevia "sem situação".

---

## 3. O que mudou na API

| Rota | Antes | Agora |
| --- | --- | --- |
| `GET /pedidos/situacao?campos=resumo` | sem `status_pedido` | **com `status_pedido` em todo pedido** |
| `GET /pedidos/situacao?campos=completo` | vazio se a view não trouxesse | último conhecido, se a view vier vazia |
| `GET /pedidos/<n>/situacao`, `404` e `503` | `null` ou sem o campo | último status conhecido |
| `GET /ordens-servico/<n>` | `null` com o SAP fora | último status conhecido |
| `GET /ordens-servico/disponiveis` | `null` em pedido sem registro na ORDR | último status conhecido |
| `POST /ordens-servico/<n>/sincronizar` | `null` se o diagnóstico falhasse | último status conhecido |
| `GET /pedidos/<n>/data-entrega` | `null` com o SAP fora | último status conhecido |

**Dois campos novos** aparecem só quando o status **não** é a resposta do SAP de agora:

| Campo | Valores | O que diz |
| --- | --- | --- |
| `status_origem` | `"ultimo_conhecido"` | O SAP não informou agora. Este foi o último status que ele informou. |
| | `"sem_historico"` | O SAP não informou agora e este pedido nunca foi visto. O status vem `"Desconhecido"`. |
| `status_em` | data e hora, ou `null` | Quando esse status foi visto pela última vez. |

**Se `status_origem` não veio, o status é o que o SAP respondeu agora.**

**Os valores possíveis de `status_pedido` agora são quatro:** `Aberto`, `Fechado`, `Cancelado` e
`Desconhecido`. O último só aparece com `status_origem: "sem_historico"`.

**O que continua igual:**

- Os códigos HTTP. SAP fora continua `404` com `motivo: "indeterminado"` ou `503`.
- `pedido_cancelado` continua `null` quando o SAP não respondeu. O último status conhecido é informação, não
  confirmação.
- As etapas `financeiro`, `producao` e `entrega`, e a lista continuar trazendo só o recorte da view.
- Número que não existe no SAP continua `404`, `motivo: "pedido_nao_encontrado"` e `status_pedido: null`.
  Ele não é um pedido, então não tem status.

---

## 4. Exemplos

Formato das respostas. Os números são ilustrativos.

**Um pedido da lista**, `GET /pedidos/situacao` (cortado):

```json
{ "doc_num": 84543, "status_pedido": "Aberto", "financeiro": "Liberado", "producao": "Bloqueado", "entrega": "Bloqueado" }
```

**SAP fora do ar, pedido já visto**, `GET /pedidos/84470/situacao` (cortado):

```json
{
  "ok": false,
  "motivo": "indeterminado",
  "status_pedido": "Cancelado",
  "status_origem": "ultimo_conhecido",
  "status_em": "2026-11-04T10:35:00-03:00",
  "pedido_cancelado": null
}
```

**SAP fora do ar, pedido nunca visto:**

```json
{
  "ok": false,
  "motivo": "indeterminado",
  "status_pedido": "Desconhecido",
  "status_origem": "sem_historico",
  "status_em": null,
  "pedido_cancelado": null
}
```

---

## 5. O que vocês precisam mudar

### Passo 1 — Ler o status da lista

Em cada pedido de `GET /pedidos/situacao`, leiam `status_pedido`. Antes ele só vinha no perfil `completo`; agora vem
também no `resumo`.

### Passo 2 — Buscar quem não veio na lista

A lista só traz os pedidos correntes. Para cada pedido do sistema de vocês que não veio nela, chamem:

```
GET /pedidos/<numero>/situacao?campos=resumo
```

Leiam `status_pedido` **também nas respostas `404` e `503`**. O corpo do erro agora traz o status.

Não façam essa chamada mais rápido que a cada 2 minutos: o dado por trás não muda mais rápido que isso.

### Passo 3 — Trocar o "sem situação" pelo status

"Sem situação" deixa de existir. Mostrem assim:

| O que veio | O que mostrar |
| --- | --- |
| `status_pedido` sem `status_origem` | O status: Aberto, Fechado ou Cancelado |
| `status_origem: "ultimo_conhecido"` | O status e a data: "Fechado (última informação em 04/11, 10:35)" |
| `status_origem: "sem_historico"` | "Desconhecido" |
| `motivo: "pedido_nao_encontrado"` | "Pedido não encontrado" |

Na entrega futura, `status_pedido` continua `Aberto`. Para mostrar "aguardando remessa", olhem `aviso.tipo` na rota
de um pedido, ou `faturamento` e `remessa` na lista (`API_SITUACAO_PEDIDOS.md`, §2.2).

### Passo 4 — Aceitar o valor novo

Se o código de vocês aceita só uma lista fechada de status, acrescentem `Desconhecido`. Valor fora da lista não pode
quebrar a tela.

### Passo 5 — Não decidir pelo último status conhecido

Status com `status_origem` serve para **mostrar**, não para decidir.

- Não movam um pedido de etapa por causa dele.
- Para confirmar um cancelamento, continuem usando `pedido_cancelado: true`, que só vem com resposta real do SAP.
- O botão Liberar pode ser desabilitado quando o status confirmado for `Cancelado` ou `Fechado`, ou seja, sem
  `status_origem`.

### Como testar

1. Abram a lista e confirmem que todo pedido tem `status_pedido`.
2. Procurem um pedido cancelado, que não vem na lista: a tela deve mostrar "Cancelado".
3. Procurem um pedido fechado fora da lista: a tela deve mostrar "Fechado".
4. Simulem a API respondendo `503`: a tela deve mostrar o último status com a data, sem travar nada.
5. Consultem um número que não existe: a tela deve mostrar "Pedido não encontrado".

---

## 6. Perguntas comuns

**A lista passou a trazer todos os pedidos do SAP?** Não. Ela continua sendo o recorte da view. Quem não vem nela é
consultado pela rota de um pedido, como no passo 2.

**E se a lista inteira responder `503`?** Nenhum pedido vem nessa resposta. Mantenham na tela a última lista
recebida e tentem de novo.

**O último status conhecido pode estar desatualizado?** Pode, e é por isso que ele vem com `status_em`. Na carga
inicial, pedido fechado por NF de entrega futura e ainda nunca consultado pode aparecer como `Fechado`, até a
primeira resposta real do SAP.

**Preciso mudar a chave ou a URL?** Não.
