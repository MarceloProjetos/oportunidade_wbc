# Guia para a equipe do PCP — pedir nova data de entrega

**Para quem é:** a equipe que mantém o app da Produção (PCP).
**O que vocês vão construir:** um jeito de a Produção **pedir** uma nova data de entrega de um pedido de venda e
**ver a resposta** do Comercial.
**Versão:** 15/10/2026. Contrato técnico completo: `API_DATA_ENTREGA_COMBINADA.md` (na raiz deste repositório).

Leia na ordem. Cada parte é curta. Não pule a parte 2.

---

## 1. A ideia em 6 frases

1. Todo pedido de venda tem uma **data de entrega no SAP**.
2. Às vezes a Produção não consegue cumprir essa data (máquina quebrou, fábrica cheia, etc.).
3. Agora a Produção pode **pedir** uma nova data pelo servidor `.11`.
4. O pedido fica **esperando**. Quem decide é o **Comercial**, numa tela do OrçaView.
5. O Comercial **aprova** ou **recusa**. Ele também pode mudar a data sozinho (por exemplo, quando o cliente pede).
6. **O SAP nunca muda.** A data que vale passa a se chamar **data combinada**.

---

## 2. Palavras que vocês precisam usar igual a nós

| Palavra | O que significa |
| --- | --- |
| **pedido** | o pedido de venda. Sempre identificado pelo **DocNum**, o número que aparece na tela (ex.: `84453`). |
| **solicitação** | o pedido de **mudança de data**. Não confunda com "pedido". |
| **data do SAP** | a data de entrega que está no SAP. Campo `data_sap`. Nunca muda por causa disto. |
| **data combinada** | a data que vale agora. Campo `data_vigente`. Se ninguém mudou nada, é igual à do SAP. |
| **pendente** | solicitação esperando o Comercial decidir. |
| **aprovada / recusada** | a decisão do Comercial. |
| **atraso nosso** | dias de atraso por culpa interna (fábrica). Mudança pedida pelo **cliente** não conta. |

> ⚠️ Regra de ouro: na tela do app, escreva **"solicitação de nova data"**, nunca "pedido de nova data".

---

## 3. O que vocês precisam fazer (lista de tarefas)

Faça nesta ordem. Marque cada uma quando terminar.

- [ ] **Tarefa 1 — Pedir acesso.** Peçam ao Marcelo para acrescentar dois escopos **na chave que vocês já usam**
  para ler `/pedidos/<n>/situacao`: `entrega:solicitar` e `entrega:ler`. A chave **não muda**.
- [ ] **Tarefa 2 — Botão "Pedir nova data".** Na tela do pedido no app, um botão que abre um formulário (parte 4).
- [ ] **Tarefa 3 — Enviar a solicitação.** O formulário chama o servidor (parte 5).
- [ ] **Tarefa 4 — Tratar a resposta.** Mostrar a mensagem certa para cada resposta (parte 6).
- [ ] **Tarefa 5 — Mostrar a situação do pedido.** Mostrar data combinada, pendente e a conversa (parte 7).
- [ ] **Tarefa 6 — Ver o que mudou.** Uma vez por dia (ou ao abrir o app), buscar as decisões novas (parte 8).
- [ ] **Tarefa 7 — Teste junto com o Comercial** (parte 10).

---

## 4. O formulário "Pedir nova data"

Quatro campos. Todos obrigatórios, menos a observação.

| Campo na tela | Nome no envio | Regra |
| --- | --- | --- |
| Nova data | `data_nova` | formato `AAAA-MM-DD` (ex.: `2026-11-27`). De **hoje** até **365 dias** à frente. Não pode ser a mesma data combinada de agora. |
| Motivo | `motivo` | uma lista com **exatamente** estas 4 opções (tabela abaixo). |
| Observação | `observacao` | texto livre, opcional, até 1000 caracteres. Pode ter quebra de linha. |
| Quem está pedindo | `solicitante` | o nome da pessoa logada no app. Até 80 caracteres. **Preencha sozinho** com o usuário logado. |

**Os 4 motivos** (o valor da esquerda é o que vai no envio; o da direita é o que aparece na tela):

| Valor enviado | Texto na tela | Conta como atraso nosso? |
| --- | --- | --- |
| `producao_sobrecarregada` | Produção sobrecarregada | sim |
| `tempo_producao_apertado` | Tempo de produção apertado | sim |
| `quebra_maquina` | Quebra de máquina | sim |
| `alteracao_cliente` | Alteração por parte do cliente | não |

> ⚠️ `alteracao_cliente` é **só** quando o cliente pediu. Nunca use para "não contar atraso".

**Antes de abrir o formulário**, chame o GET da parte 7. Se já existir uma solicitação **pendente**, não deixe
abrir: mostre "Já existe um pedido de nova data esperando o Comercial: [data] por [pessoa] em [dia]".

---

## 5. Como enviar a solicitação

**Endereço:** `POST http://192.168.7.11:8077/pedidos/<NUMERO>/data-entrega/solicitacoes`
Troque `<NUMERO>` pelo DocNum do pedido.

**Cabeçalhos:**
- `X-API-Key: <a chave de vocês>` (a mesma de sempre; **nunca** coloque a chave na URL)
- `Content-Type: application/json`

**Corpo (exemplo completo):**

```json
{
  "data_nova": "2026-11-27",
  "motivo": "quebra_maquina",
  "observacao": "Prensa 3 parada até 20/11.",
  "solicitante": "Ana Souza"
}
```

**Tempo de espera:** configure o seu cliente HTTP para esperar **pelo menos 60 segundos**.

### Exemplo em JavaScript (fetch)

```javascript
async function pedirNovaData(numeroPedido, dataNova, motivo, observacao, solicitante) {
  const controle = new AbortController();
  const timer = setTimeout(() => controle.abort(), 60000); // espera até 60 s
  try {
    const resp = await fetch(
      `http://192.168.7.11:8077/pedidos/${numeroPedido}/data-entrega/solicitacoes`,
      {
        method: 'POST',
        headers: { 'X-API-Key': CHAVE_DA_API, 'Content-Type': 'application/json' },
        body: JSON.stringify({ data_nova: dataNova, motivo, observacao, solicitante }),
        signal: controle.signal,
      },
    );
    const corpo = await resp.json();
    return { status: resp.status, corpo };
  } finally {
    clearTimeout(timer);
  }
}
```

### Exemplo com curl (para testar no terminal)

```
curl -X POST "http://192.168.7.11:8077/pedidos/84453/data-entrega/solicitacoes" ^
  -H "X-API-Key: SUA_CHAVE" -H "Content-Type: application/json" ^
  -d "{\"data_nova\":\"2026-11-27\",\"motivo\":\"quebra_maquina\",\"solicitante\":\"Ana Souza\"}"
```

---

## 6. O que fazer com cada resposta

A resposta sempre é JSON. **Decida pelo campo `motivo`**, nunca pelo texto de `error`.

### 6.1 Deu certo

| HTTP | Como reconhecer | O que mostrar ao usuário |
| --- | --- | --- |
| **201** | `ok: true` e `ja_existia: false` | "Solicitação enviada. Agora o Comercial decide. A data ainda **não** mudou." |
| **200** | `ok: true` e `ja_existia: true` | "Essa solicitação já tinha sido enviada. Nada novo foi criado." |

Exemplo de resposta 201 (resumida):

```json
{
  "ok": true,
  "ja_existia": false,
  "solicitacao": {"id": 812, "doc_num": 84453, "data_nova": "2026-11-27", "situacao": "pendente",
                  "motivo": "quebra_maquina", "solicitado_por": "Ana Souza"},
  "entrega": {"data_sap": "2026-11-20", "data_vigente": "2026-11-20", "pendente": {"id": 812}},
  "aviso": "Nada mudou ainda: a data so muda quando o Comercial aprovar no OrcaView. O SAP nao e alterado."
}
```

> Repare: depois do 201, `data_vigente` **continua** a antiga. Só muda quando o Comercial aprovar.

### 6.2 Recusado — erro de quem preencheu (corrigir e enviar de novo)

| HTTP | `motivo` | O que mostrar |
| --- | --- | --- |
| 400 | `data_invalida` | "Data inválida. Use dia/mês/ano válidos." |
| 400 | `motivo_invalido` | "Escolha um dos 4 motivos." |
| 400 | `observacao_longa` | "A observação passou de 1000 caracteres." |
| 400 | `observacao_invalida` | "A observação tem um caractere que não é aceito." |
| 400 | `solicitante_invalido` | "Falta o nome de quem pede (até 80 caracteres)." |
| 422 | `data_no_passado` | "A nova data não pode ser no passado." |
| 422 | `data_muito_distante` | "A nova data passa de um ano. Confira o ano." |
| 422 | `data_igual_a_vigente` | "Essa já é a data combinada do pedido." |

### 6.3 Recusado — o pedido não aceita solicitação (não adianta repetir)

| HTTP | `motivo` | O que mostrar |
| --- | --- | --- |
| 409 | `ja_ha_pendente` | "Já existe um pedido de nova data esperando o Comercial." Use o campo `pendente` da resposta para mostrar **quem** pediu, **quando** e **qual data**. |
| 409 | `pedido_fechado` | "Este pedido está fechado no SAP." |
| 409 | `pedido_cancelado` | "Este pedido foi cancelado no SAP." |
| 409 | `sem_data_sap` | "Este pedido não tem data de entrega no SAP." |
| 404 | `pedido_nao_encontrado` | "Pedido não encontrado. Confira o número." |
| 404 | `fora_do_recorte` | "Este pedido não aparece na tela do Comercial. Avise o suporte." |

### 6.4 Problema no servidor (pode tentar de novo depois)

| HTTP | `motivo` / `error` | O que fazer |
| --- | --- | --- |
| 503 | `indeterminado` | "O SAP não respondeu agora. Nada foi gravado. Tente de novo em alguns minutos." |
| 503 | `base_indisponivel` | Antes de tentar de novo, **faça o GET da parte 7**. Se a solicitação já aparecer lá, não envie de novo. |
| 503 | `base_nao_configurada` | Avise o Marcelo (problema de configuração do servidor). |
| 429 | `error: "rate_limited"` | Espere os segundos de `retry_after_s` e tente de novo. |
| — | o tempo de espera acabou (timeout) | **Não repita direto.** Faça o GET da parte 7 primeiro. |

### 6.5 Erros de acesso (forma diferente!)

Estes vêm com `error` e o `motivo` é **texto**:

| HTTP | `error` | Causa |
| --- | --- | --- |
| 401 | `unauthorized` | faltou a chave, ou a chave está errada |
| 403 | `forbidden` | a chave não tem o escopo (`entrega:solicitar` ou `entrega:ler`). Peça ao Marcelo. |

> 💡 Repetir o envio com **os mesmos** dados é seguro: o servidor responde `ja_existia: true` e não duplica.

---

## 7. Mostrar a situação de um pedido

**Endereço:** `GET http://192.168.7.11:8077/pedidos/<NUMERO>/data-entrega`
Mesmo cabeçalho `X-API-Key`.

Exemplo de resposta:

```json
{
  "ok": true,
  "pedido": 84453,
  "status_pedido": "Aberto",
  "data_sap": "2026-11-20",
  "data_vigente": "2026-11-27",
  "alterada": true,
  "atraso_nosso_dias": 7,
  "atrasado": true,
  "atrasado_fonte": "combinada",
  "pendente": null,
  "conversa": [
    {"id": 812, "origem": "producao_api", "data_nova": "2026-11-27", "motivo": "quebra_maquina",
     "observacao": "Prensa 3 parada até 20/11.", "situacao": "aprovada",
     "solicitado_por": "Ana Souza", "solicitado_em": "2026-11-10T12:00:00+00:00",
     "decidido_por": "adriano@altamira.com.br", "decidido_em": "2026-11-10T15:40:00+00:00",
     "motivo_recusa": null}
  ]
}
```

**Como mostrar na tela do app:**

1. **Data que vale:** use `data_vigente`. Se `alterada` for `true`, mostre também "(no SAP: `data_sap`)".
2. **Pendente:** se `pendente` não for `null`, mostre "Esperando o Comercial: nova data X, pedida por Y".
3. **Conversa:** liste os itens de `conversa` na ordem em que vêm (o mais antigo primeiro). Para cada item:
   - se `origem` = `producao_api`: "Produção pediu [data_nova] — [motivo] — por [solicitado_por]";
   - se `origem` = `comercial`: "Comercial mudou para [data_nova] — [motivo]" (a Produção também precisa cumprir
     essa data);
   - se `situacao` = `aprovada`: "Aprovada por [decidido_por]";
   - se `situacao` = `recusada`: "Recusada por [decidido_por]: [motivo_recusa]";
   - se `situacao` = `pendente`: "Esperando o Comercial".
4. **Horários:** `solicitado_em` e `decidido_em` vêm em **UTC** (`+00:00`). Converta para o horário de Brasília
   antes de mostrar.
5. **`null`:** nas datas, em `atraso_nosso_dias` e em `atrasado`, `null` quer dizer **"não dá para saber agora"**.
   Mostre "—", nunca "0" nem "não". Já `pendente: null` quer dizer **"não há pendente"**.

---

## 8. Ver o que mudou (rotina diária)

**Endereço:** `GET http://192.168.7.11:8077/pedidos/data-entrega/solicitacoes?situacao=decididas&dias=1`

- `situacao=pendente` → todas as solicitações ainda esperando o Comercial.
- `situacao=decididas&dias=1` → o que foi aprovado ou recusado nas últimas 24 horas (inclui as mudanças que o
  Comercial fez sozinho).
- `situacao=todas&dias=7` → tudo que foi pedido ou decidido na última semana.

Cada item traz `doc_num` (o pedido). Use isso para avisar a equipe da fábrica: "o pedido 84453 teve a data
aprovada para 27/11".

---

## 9. Regras que não podem ser quebradas

1. **Não mostre os motivos nem a conversa a cliente ou a representante.** Os motivos são internos.
2. **Nunca escreva no SAP** por causa disto. A data do SAP fica como está.
3. **Não grave direto no banco.** Use **só** o servidor `.11` (as rotas acima). O banco tem regras que só o servidor
   cumpre.
4. **Não diga "data alterada"** depois de enviar. Diga "solicitação enviada, aguardando o Comercial".
5. **Uma pendente por pedido.** Se já houver uma, espere a decisão.
6. **Sempre o DocNum.** Nunca o DocEntry.

---

## 10. Teste junto com o Comercial (roteiro)

Combine um horário com o Marcelo e uma pessoa do Comercial. Escolham **um pedido aberto** juntos.

1. No app, peça uma nova data para esse pedido (motivo `tempo_producao_apertado`, observação "TESTE").
   Esperado: 201 e a mensagem "Solicitação enviada".
2. Peça de novo, igual. Esperado: 200 com `ja_existia: true`.
3. Peça outra data diferente. Esperado: 409 `ja_ha_pendente`, mostrando quem pediu.
4. O Comercial **recusa** na tela do OrçaView com o texto "teste".
5. No app, abra o pedido. Esperado: a conversa mostra "Recusada por … — teste" e não há pendente.
6. Rode o GET da parte 8 com `situacao=decididas&dias=1`. Esperado: a solicitação aparece como recusada.

Se todos os passos passarem, está pronto.

---

## 11. Perguntas frequentes

**A data muda na hora que a Produção pede?** Não. Só quando o Comercial aprovar.

**O SAP é alterado?** Nunca.

**E se o Comercial mudar a data sem a Produção pedir?** Aparece na conversa com `origem: "comercial"` e na lista
da parte 8. Essa é a data que a Produção deve cumprir.

**Posso cancelar uma solicitação que enviei errado?** Ainda não. Peça ao Comercial para recusar com o texto
"pedido por engano".

**O que é `atrasado_fonte`?** `sap` = o atraso foi calculado pela data do SAP (ninguém mudou a data).
`combinada` = calculado pela data combinada.

**Vocês usam IA (MCP)?** Existem três ferramentas no MCP `192.168.7.11:8078`: `data_entrega_pedido`,
`solicitacoes_data_entrega` e `solicitar_data_entrega`. A de pedir só grava com `confirmar=True`, depois que uma
**pessoa** confirmar. O token do MCP de vocês precisa dos mesmos dois escopos.

**Dúvida que não está aqui?** Leia `API_DATA_ENTREGA_COMBINADA.md` ou fale com o Marcelo.
