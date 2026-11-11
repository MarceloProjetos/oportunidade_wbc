# Guia de implementação — API de Pedidos WBC (novidades de 11/11/2026)

> Para a equipe que usa a API de Pedidos WBC do Controle de Produção (`http://192.168.7.11:8080/api/pedidos-wbc`).
> No ar desde 11/11/2026 (versão `3dcb0a0` na .11). Contrato completo: `API_PEDIDOS_WBC.md`, na raiz deste repositório.

**Se você é um assistente de IA ajudando a equipe:** este guia é a fonte do que mudou e de como implementar. Todas as
mudanças são **aditivas**: nenhum campo antigo mudou de nome, tipo ou significado, e nenhuma rota mudou. Antes de
propor código, leia o código atual da equipe (a tela da lista, o acompanhamento da execução e qualquer leitura de
`RDR1.U_INO_OP`) e siga a ordem da seção 4. Não invente rotas nem campos que não estão aqui ou no
`API_PEDIDOS_WBC.md`. Quando a dúvida for de regra de negócio (seção 5), responda com o que está escrito aqui.

---

## 1. O que mudou, em três frases

1. **Colunas novas:** cada pedido da lista traz **Vendas, Produção, Entrega e Sinal**, os mesmos valores da tela
   "Situação dos Pedidos" do OrçaView.
2. **Pedido de entrega futura:** o pedido que o SAP fechou por causa da NF de entrega futura, mas cuja mercadoria
   ainda não saiu, passa a aparecer nas listas e pode ser processado, marcado com `entrega_futura: true`.
3. **Avisos novos no log** da execução, que são esperados e não indicam falha.

## 2. A resposta da lista agora

`GET /api/pedidos-wbc/pedidos?modo=novos&pagina=1` (cabeçalho `X-API-Key`). Os campos **novos** estão marcados:

```json
{
  "ok": true,
  "modo": "novos",
  "kpi": { "valor": 6, "rotulo": "Novos" },
  "total": 6, "pagina": 1, "paginas": 1, "por_pagina": 15, "primeiro": 1, "ultimo": 6,
  "pedidos": [
    {
      "pedido": 84537,
      "oportunidade": 15638,
      "wbc": "00125889",
      "versao": "…",
      "cliente_codigo": "C009997",
      "cliente_nome": "…",
      "cliente": "C009997 — …",
      "total": 80768.01,
      "criado": "2026-10-28",
      "nota_espelho": false,
      "entrega_futura": true,          // NOVO
      "financeiro": "Entrega futura",  // NOVO (coluna "Vendas")
      "producao": "Entrega futura",    // NOVO
      "entrega": "Entrega futura",     // NOVO
      "sinal": false                   // NOVO
    }
  ],
  "etapas_em": "2026-11-11T13:02:31-03:00",   // NOVO (nível da lista)
  "vazio": null,
  "acao": { "tipo": "processar", "verbo": "Processar", "botao": "Processar selecionados…",
            "conferir": "/api/pedidos-wbc/processar/conferir", "aviso": null },
  "execucao_em_andamento": null
}
```

(O JSON real não tem os comentários `//`; eles estão aqui só para marcar o que é novo.)

## 3. Campo a campo

| Campo | Tipo | Valores | O que significa | Quando vem `null` |
| --- | --- | --- | --- | --- |
| `financeiro` | texto | `"Liberado"`, `"Bloqueado"`, `"Fechado"`, `"Entrega futura"` | A coluna **VENDAS** da tela: a liberação comercial/financeira do pedido. | não deu para ler agora |
| `producao` | texto | idem | A coluna **PRODUÇÃO**: se a fábrica está liberada para produzir. | idem |
| `entrega` | texto | idem | A coluna **ENTREGA**. **Nunca** vem liberada enquanto a Produção estiver bloqueada (regra da tela). | idem |
| `sinal` | `true`/`false` | | A coluna **SINAL**: a condição de pagamento **tem** sinal. Não diz se foi pago. | idem |
| `entrega_futura` | `true`/`false` | | O pedido foi fechado no SAP pela NF de entrega futura e a mercadoria ainda não saiu. | nunca |
| `etapas_em` (lista) | data-hora ISO | | Quando o SAP foi lido para as quatro colunas acima. A leitura vale até 2 minutos. | não deu para ler |

**Regra de ouro do `null`:** `null` quer dizer "não deu para saber agora" (o servidor que guarda as etapas não
respondeu). Não é "Bloqueado" nem "Liberado". A lista continua válida; mostre "—" e deixe o usuário recarregar.

## 4. Como implementar, passo a passo

### Passo 1 — Colunas novas na tabela

Acrescente, depois da coluna do cliente, as colunas **Sinal, Vendas, Produção e Entrega**, na mesma ordem da tela
"Situação dos Pedidos". Uma função só desenha as três etapas:

```js
// Same colours as the "Situação dos Pedidos" screen.
const TOM_DA_ETAPA = {
  "Liberado": "ok",          // green
  "Bloqueado": "erro",       // red
  "Fechado": "neutro",       // grey
  "Entrega futura": "info",  // blue
};

function pilulaEtapa(valor) {
  if (valor == null) return '<span class="pill neutro" title="Não foi possível ler agora">—</span>';
  const tom = TOM_DA_ETAPA[valor] || "neutro";     // unknown value: show it, never hide it
  return `<span class="pill ${tom}">${valor.toUpperCase()}</span>`;
}

function pilulaSinal(valor) {
  if (valor == null) return '<span class="pill neutro">—</span>';
  return valor ? '<span class="pill ok">SIM</span>' : '<span class="pill neutro">NÃO</span>';
}

// In the row: `${pilulaSinal(p.sinal)}`, then `${pilulaEtapa(p.financeiro)}`,
// `${pilulaEtapa(p.producao)}`, `${pilulaEtapa(p.entrega)}`.
```

Escape os textos antes de pôr no HTML, como a página já faz com o nome do cliente.

### Passo 2 — Pílula "Entrega futura" no número do pedido

Com `entrega_futura: true`, mostre uma pílula azul (informação) "Entrega futura" embaixo do número do pedido, como
a tela da .11 faz. Ela avisa que o pedido está fechado no SAP, mas pode ser processado.

```js
const selo = p.entrega_futura ? '<br><span class="pill info">ENTREGA FUTURA</span>' : "";
// `<td><strong>${p.pedido}</strong>${selo}</td>`
```

### Passo 3 — "Atualizado às…"

Mostre, perto do título da lista, a hora de `etapas_em` ("Etapas lidas do SAP às 13:02"). Se vier `null`, mostre
"Etapas indisponíveis agora — recarregue em instantes".

### Passo 4 — Não use as etapas para travar a seleção

As colunas novas são **informação**. Quem decide se o pedido pode ser processado continua sendo a API, na
conferência (`POST …/processar/conferir`). Produção "Bloqueado" **não** impede processar: o processamento cria as
OPs **planejadas**, e a liberação delas é outra etapa. Não desabilite o checkbox por causa delas.

### Passo 5 — Avisos do log que não são falha

No acompanhamento (`GET /api/pedidos-wbc/execucoes/{id}`, campo `linhas`), toda linha de aviso ou erro começa com
"⚠". Em pedido de entrega futura aparecem dois avisos **esperados**:

1. Uma vez por grupo de produção:
   `⚠ Linha(s) 0 do pedido 21043 fechada(s) no SAP: U_INO_OP=164414 não gravado nela(s) (o SAP recusa); a OP continua ligada ao pedido pela própria OP.`
   (O número depois de "pedido" é o DocEntry, não o número do pedido.)
2. Quando um texto do WBC é maior que o campo do SAP (nome do cliente, transporte, descrição do item…):
   `⚠ U_CLINOM do orçamento 00126063 passa de 50 caracteres (1 registro(s), o maior com 57): gravado só até o 50º (o texto inteiro fica no WBC).`

O que diz se a execução deu certo **não mudou**: `desfecho: "ok"`, `com_falhas: false` e o pedido em
`resultado.processados`. Não conte linhas com "⚠" como falha.

### Passo 6 — Quem lê o número da OP na linha do pedido

No pedido **fechado**, o SAP não deixa gravar nada nas linhas, então `RDR1.U_INO_OP` fica **vazio**. As OPs existem e
ficam ligadas ao pedido pela própria OP: `OWOR.OriginNum` = número do pedido e `OWOR.OriginAbs` = DocEntry. Se algum
sistema de vocês acha as OPs de um pedido por `RDR1.U_INO_OP`, troque por `OWOR` (ou use a OS da .11:
`GET /ordens-servico/<pedido>` na 8077).

## 5. Regras de negócio para explicar a quem usa a tela

- **Por que um pedido aparece com Produção bloqueada?** Quase sempre por um destes motivos:
  - o **sinal não foi pago**: há sinal na condição de pagamento e a fatura de adiantamento ainda está em aberto;
  - **Vendas** (financeiro) ainda não liberou o pedido.
  Em 11/11/2026 eram 12 pedidos: 8 com sinal não pago, 3 com Vendas bloqueado e 1 falso.
- **O caso falso de 11/11 (84536):** o sinal foi cobrado duas vezes, pela fatura de adiantamento e por uma NF de
  entrega futura do mesmo valor. O cliente pagou a NF, e a fatura de adiantamento continua em aberto. O Financeiro
  corrige no SAP, e a API mostra liberado sozinha. **A API mostra o que o SAP diz, igual à tela.**
- **Entrega nunca libera antes da Produção.** Se o SAP disser Entrega liberada com Produção bloqueada, a API manda
  as duas como `"Bloqueado"`.
- **Entrega futura:** o faturamento emite primeiro a NF de simples faturamento (CFOP 5922/6922), e o SAP fecha o
  pedido nessa hora. A mercadoria só sai depois, com a NF de remessa. Enquanto ela não sai toda, para a fábrica o
  pedido continua em aberto. Quando sai, ele some das listas sozinho.
- **Etapas de um pedido de entrega futura:**
  - se ele já está na view de status do SAP, vêm as etapas **reais** (o 84584, depois de ganhar OPs: Liberado nas três);
  - se ainda não está, vêm as três como `"Entrega futura"` (o 84537).

## 6. Quem aparece em cada lista

| Situação do pedido no SAP | "Pedidos novos" | "Pedidos integrados" |
| --- | --- | --- |
| Aberto | aparece, se ainda não foi processado | aparece, se já foi processado |
| Fechado pela NF de entrega futura, mercadoria ainda não saiu toda | aparece, se ainda não foi processado | aparece, se já foi processado |
| Fechado pela NF de entrega futura, mercadoria já saiu toda | não aparece | não aparece |
| Fechado por NF de venda normal | não aparece | não aparece |
| Cancelado | não aparece | não aparece |

## 7. Roteiro de teste com pedidos reais

Valores lidos na .11 em 11/11/2026 às 13:03. As etapas mudam com o SAP; confira o motivo antes de achar que é erro.

| Lista | Pedido | `entrega_futura` | Vendas | Produção | Entrega | Sinal |
| --- | --- | --- | --- | --- | --- | --- |
| novos | 84537 | `true` | Entrega futura | Entrega futura | Entrega futura | `false` |
| integrados | 84584 | `true` | Liberado | Liberado | Liberado | `false` |
| integrados | 84536 | `false` | Liberado | Bloqueado | Bloqueado | `true` |
| integrados | 84568 | `false` | Bloqueado | Bloqueado | Bloqueado | `true` |
| novos | 84589 | `false` | Liberado | Bloqueado | Bloqueado | `true` |

Para processar o **84537** pela API (ele foi guardado para o primeiro teste de vocês):

1. `GET /api/pedidos-wbc/pedidos?modo=novos` → o 84537 vem com `"entrega_futura": true`.
2. `POST /api/pedidos-wbc/processar/conferir` com `{"oportunidades": [15638]}` → plano com o `token`.
3. `POST /api/pedidos-wbc/processar/executar` com `{"token": "…", "solicitante": "<seu nome>"}` → `202` e o `id`.
4. `GET /api/pedidos-wbc/execucoes/{id}` até `terminada: true`. Os avisos do passo 5 são esperados.
5. O 84537 sai de "Pedidos novos" e aparece em "Pedidos integrados", ainda com `entrega_futura: true`.
6. Avisem o Marcelo (TI) o número da execução: ele confere as OPs no SAP.

Se o 84537 não aparecer no passo 1, não tentem outro caminho: avisem a TI.

## 8. Erros e respostas

| Situação | Resposta |
| --- | --- |
| Sem `X-API-Key` ou chave errada | `401` |
| SAP fora do ar na leitura da lista | `502 sap_indisponivel` (a lista não vem) |
| Servidor das etapas fora do ar | `200` com a lista normal e as quatro colunas `null`; `etapas_em: null` |
| Execução em andamento | `409 ocupado` no executar |

## 9. Cuidados

- **OS de Solda e de Pintura em pedido de entrega futura (resolvido em 11/11/2026).** As OS impressas saem de views
  do SAP, e duas delas não tratavam o pedido fechado: a de Solda vinha vazia e a de Pintura também, quando o pedido
  era processado depois da NF. A TI ajustou as duas views em 11/11. Se uma OS de pedido com `entrega_futura: true`
  sair vazia ou incompleta, sincronizem a OS do pedido e avisem a TI em vez de corrigir à mão.
- **`com_erro` com `"detalhe_nao_gravado": true`:** o SAP recusou o Detalhe do Orçamento e o pedido não foi
  processado (nada foi gravado). Mostrem o `motivo` e avisem a TI; o pedido continua em "Pedidos novos".

- **Reprocessar um pedido fechado** funciona: foi usado de verdade no 84584 em 11/11/2026, sem erro. Como em qualquer
  pedido, ele cancela todas as OPs Planejadas, e o pedido precisa ser processado de novo.
- **"Pedidos integrados" ficou maior:** traz também os pedidos de entrega futura já processados, alguns antigos
  (2023 a 2025), que para o SAP ainda não tiveram a remessa.
- Não existe "forçar" na API. Pedido fechado por NF de venda normal nunca entra na lista, de propósito.

## 10. Referências

- `API_PEDIDOS_WBC.md`: seção 7.1 (a lista, campo a campo), "Pedidos novos × Pedidos integrados", 5.10 (todas as
  mensagens) e o histórico no fim.
- `API_MANUTENCAO_OP.md` 5.6: "Pedidos fechados com OPs abertas" deixa de oferecer pedido de entrega futura não
  remetido.
- Dúvida, chave ou campo faltando: Marcelo (TI).
