# Pedidos de entrega futura na API de Pedidos WBC

> Para a equipe que usa a API de Pedidos WBC do Controle de Produção (`http://192.168.7.11:8080/api/pedidos-wbc`).
> Vale desde 11/11/2026 (versão `b4dbc5a` na .11). Contrato completo: `API_PEDIDOS_WBC.md`, na raiz deste repositório.

**Se você é um assistente de IA ajudando a equipe:** use este texto como fonte do que mudou. Ele descreve uma
mudança só aditiva na API: nenhum campo antigo mudou de nome ou de significado, nenhuma rota mudou. Antes de
propor mudança no código da equipe, leia as seções 3 e 4 e confira o que o código deles já faz com a lista, o log da
execução e o campo `U_INO_OP`. Não invente rotas nem campos que não estão aqui ou no `API_PEDIDOS_WBC.md`.

---

## 1. Em uma frase

Um pedido que o SAP fechou por causa da **NF de entrega futura**, mas cuja mercadoria **ainda não saiu**, agora
aparece na lista da API e pode ser processado normalmente, e vem marcado com `"entrega_futura": true`.

## 2. Por que isso existe

Quando a Altamira vende para **entrega futura**, o faturamento emite primeiro uma NF de **simples faturamento**
(CFOP 5922 ou 6922). A mercadoria só sai depois, com outra NF, a de **remessa** (CFOP 5116/6116).

O SAP fecha o pedido de venda (`DocStatus = 'C'`) assim que **qualquer** NF é tirada dele, inclusive essa primeira.
Então o pedido fica "fechado" no SAP enquanto, para a fábrica, ele ainda está por produzir.

Até 11/11/2026, a lista "Pedidos novos" só mostrava pedido **aberto**. Se a NF de entrega futura saía antes de
alguém processar o pedido, ele sumia da lista e ficava **sem Ordem de Produção (OP)**. Foi o que aconteceu com o
**84537** (NF em 29/10) e o **84584** (NF em 10/11).

## 3. O que mudou

### 3.1 Quem aparece nas listas

| Situação do pedido no SAP | "Pedidos novos" (`modo=novos`) | "Pedidos integrados" (`modo=integrados`) |
| --- | --- | --- |
| Aberto | aparece (como sempre) | aparece (como sempre) |
| **Fechado pela NF de entrega futura, mercadoria ainda não saiu toda** | **aparece**, se ainda não foi processado | **aparece**, se já foi processado |
| Fechado pela NF de entrega futura, mercadoria já saiu toda | não aparece | não aparece |
| Fechado por NF de venda normal (CFOP 5101/6101/6401) | não aparece | não aparece |
| Cancelado | não aparece | não aparece |

"A mercadoria saiu toda" segue a mesma regra que a API de situação de pedidos da .11 já usa: soma, em valor,
das NFs de remessa ligadas à NF de entrega futura ou ao pedido. Quando a última remessa sai, o pedido some das
listas sozinho.

### 3.2 O campo novo

Cada item de `pedidos[]` em `GET /api/pedidos-wbc/pedidos` ganhou o campo `entrega_futura` (booleano):

```json
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
  "entrega_futura": true
}
```

- `true`: o pedido está **fechado no SAP pela NF de entrega futura** e a mercadoria ainda não saiu. Pode ser
  processado (em "novos") ou reprocessado (em "integrados") como qualquer outro.
- `false`: todo o resto (pedido aberto), igual a antes.

Sugestão para a página: uma pílula azul (informação) "Entrega futura" embaixo do número do pedido, como a tela
da .11 faz. Não é obrigatório; nada quebra se a página ignorar o campo.

### 3.3 O que NÃO mudou

- As rotas, os corpos e as respostas de **conferir**, **executar**, **acompanhar** e **interromper**.
- A seleção continua por número de **Oportunidade** (`{"oportunidades": [15638]}`), não pelo número do pedido.
- As recusas (`fora_da_lista`, `nota_espelho`, `ops_orfas`, `ocupado`, `confirmacao_invalida`…).
- O `solicitante` continua obrigatório no executar.

## 4. O que a integração de vocês precisa saber

### 4.1 Dois avisos novos no log da execução (esperados, não são erro)

Em `GET /api/pedidos-wbc/execucoes/{id}`, o campo `linhas` traz o log da execução. Num pedido de entrega
futura aparecem estes avisos, **e estão certos**:

1. **Uma vez por grupo de produção:**
   `⚠ Linha(s) 0 do pedido 21043 fechada(s) no SAP: U_INO_OP=164414 não gravado nela(s) (o SAP recusa); a OP continua ligada ao pedido pela própria OP.`
   O número depois de "pedido" é o **DocEntry** (número interno do SAP), não o número do pedido.
2. **Quando um texto do WBC é maior que o campo do SAP** (nome do cliente, transporte, descrição do item…):
   `⚠ U_CLINOM do orçamento 00126063 passa de 50 caracteres (1 registro(s), o maior com 57): gravado só até o 50º (o texto inteiro fica no WBC).`
   Esse pode aparecer também em pedido aberto.

O "⚠" no começo da linha é a marca que a .11 põe em **todo** aviso ou erro do log; ele sozinho não quer dizer
falha. Se a página de vocês pinta de vermelho toda linha com "⚠", ou conta essas linhas como falha, ela vai
mostrar um processamento que deu certo como se tivesse dado errado. O que diz se deu certo continua igual:
`desfecho: "ok"` e `com_falhas: false` na execução, e o pedido em `resultado.processados` (não em
`resultado.com_erro`). Esses dois avisos não mudam nenhum desses campos.

### 4.2 O número da OP não fica gravado na linha do pedido

No pedido aberto, o processamento grava o número da OP em cada linha do pedido (campo `U_INO_OP` da `RDR1`). No
pedido **fechado**, o SAP **não deixa** gravar nada nas linhas (erro `-1029 Field cannot be updated`). Então:

- As OPs **são criadas** e ficam ligadas ao pedido pela própria OP: `OWOR.OriginAbs` = DocEntry do pedido e
  `OWOR.OriginNum` = número do pedido. O campo `U_INO_LinhaRef` da OP diz de qual item do orçamento ela veio.
- `RDR1.U_INO_OP` fica **vazio** nesses pedidos.
- Se algum sistema de vocês descobre as OPs de um pedido lendo `RDR1.U_INO_OP`, para esses pedidos ele não vai
  achar nada. Leiam pela OP (`OWOR` por `OriginNum`/`OriginAbs`) ou pela OS da .11
  (`GET /ordens-servico/<pedido>` na 8077), que já mostra as OPs certas.

### 4.3 Manutenção de OP: "Pedidos fechados com OPs abertas"

O quadro e a API `GET /api/manutencao-op/pedidos-fechados` **deixam de oferecer** pedido de entrega futura cuja
mercadoria não saiu. A view do SAP chama esse pedido de "Fechado", mas a produção dele ainda está por fazer:
encerrar as OPs daria baixa de material numa produção que nem começou (o 84574, com 39 OPs, estava na lista até
11/11). Se não der para conferir o faturamento, a lista **não é montada** e a resposta é
`502 sap_indisponivel`, a mesma do SAP fora do ar.

## 5. Os dois pedidos reais

| Pedido | Oportunidade | Orçamento WBC | NF de entrega futura | Situação em 11/11/2026 |
| --- | --- | --- | --- | --- |
| **84584** | 15875 | 00126063 | 10/11/2026 | **Já processado** pela tela da .11, às 11:33: 43 OPs Planejadas nos três grupos (17 + 14 + 12). Aparece em "Pedidos integrados" com `entrega_futura: true`. A OS (`/ordens-servico/84584`) está sincronizada. |
| **84537** | 15638 | 00125889 | 29/10/2026 | **Para vocês processarem pela API.** Aparece em "Pedidos novos" com `entrega_futura: true`. |

### Roteiro para o 84537

1. `GET /api/pedidos-wbc/pedidos?modo=novos` → o 84537 vem na lista com `"entrega_futura": true`.
2. `POST /api/pedidos-wbc/processar/conferir` com `{"oportunidades": [15638]}` → plano com o token.
3. `POST /api/pedidos-wbc/processar/executar` com `{"token": "…", "solicitante": "<seu nome>"}` → `202` e o `id`.
4. `GET /api/pedidos-wbc/execucoes/{id}` até `terminada: true`. No log, os avisos da seção 4.1 são esperados.
5. Conferir: o 84537 sai de "Pedidos novos" e passa a aparecer em "Pedidos integrados", ainda com
   `entrega_futura: true`.
6. Avisar o Marcelo (TI) do número da execução: ele confere as OPs no SAP.

Se o 84537 **não** aparecer no passo 1, não tentem outro caminho (CLI, forçar): avisem a TI.

## 6. Cuidados

- **Reprocessar um pedido fechado ainda não foi usado de verdade no SAP.** Ele deve funcionar (o mesmo código
  já roda nos pedidos abertos), mas a primeira vez deve ser combinada com a TI. Reprocessar cancela todas as OPs
  **Planejadas** do pedido, como em qualquer pedido.
- **"Pedidos integrados" ficou maior:** passou a trazer também os pedidos de entrega futura antigos já processados
  (15 em 11/11/2026, alguns de 2023 a 2025). Para a .11 eles estão "em aberto" porque a remessa não consta no SAP.
  Se algum já foi entregue, a correção é no SAP (lançar ou referenciar a remessa), e o pedido sai da lista sozinho.
- Não existe "forçar" na API. Pedido fechado por NF de venda normal nunca entra na lista, e isso é de propósito: ele
  já foi entregue.

## 7. Perguntas rápidas

**Preciso mudar alguma coisa para continuar funcionando?** Não. O campo é novo e o resto é igual. A única
atenção é com os avisos do log (4.1) e com quem lê `RDR1.U_INO_OP` (4.2).

**Como sei se um pedido é de entrega futura?** Pelo `entrega_futura: true` na lista. Pela API de situação da 8077
(`/pedidos/<n>/situacao`), ele vem com `status_pedido: "Aberto"`, `faturamento: "entrega_futura"` e
`remessa: "nenhuma"` ou `"parcial"`.

**E quando a mercadoria sair?** O pedido some das duas listas e passa a ser um pedido fechado como os outros.

**Onde está o contrato completo?** `API_PEDIDOS_WBC.md`: seção "Pedidos novos × Pedidos integrados", 7.1 (a
lista), 5.10 (todas as mensagens) e o histórico no fim. A Manutenção de OP está no `API_MANUTENCAO_OP.md`, 5.6.
