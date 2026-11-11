# Plano — Sinal e etapas na API de Pedidos WBC

> **Status (11/11/2026):** F0 feita (só leitura). **F3 no ar (`3dcb0a0`) e conferida na .11**: 116 pedidos nas duas
> listas, 0 sem etapas, 0 diferentes da API de situação, páginas em 0,1–2,0 s. F1 espera o Financeiro; F2 só se F1
> mostrar repetição; D1 e D2 abertas.
> Página: artifact publicado com o mesmo conteúdo (mesma url a cada atualização).

**Começo → Meio → Fim:** separar os falsos bloqueios dos verdadeiros e corrigir no SAP → a regra do sinal pago
deixa de olhar só o adiantamento → a API de Pedidos WBC passa a mandar Vendas, Produção, Entrega e Sinal.

## O pedido

1. **"Bloqueados que não são bloqueados"** — na Situação dos Pedidos (11/11, 12:04), 12 pedidos aparecem com
   Produção e Entrega **bloqueadas**.
2. **A equipe da API pediu** que `GET /api/pedidos-wbc/pedidos` traga, por pedido, as colunas **Vendas, Produção e
   Entrega** e se o pedido **tem sinal**.

## O que o SAP mostrou (11/11/2026, só leitura)

A etapa vem pronta da view `VW_STATUS_PEDIDO_DDP` (coluna `Producao`); a .11 e o web só a leem. Nos 12 pedidos
a view diz `Producao = 'Bloqueada'` e `Data_Lib_Prod` vazio. Separando pelo motivo:

| Grupo | Pedidos | O que há no SAP | Bloqueio é real? |
| --- | --- | --- | --- |
| **Sinal pago, mas noutro documento** | **84589**, **84536** | 84589: adiantamento 2811 **pago** (R$ 29.414,02) e um 2º adiantamento, 2817, **criado no mesmo dia** com o mesmo valor, em aberto. 84536: o cliente pagou R$ 15.554,40 em 10/11 (pagamento 6860), mas o pagamento baixou a **NF de entrega futura 32528** (CFOP 5922, do mesmo valor), e o adiantamento 2765 ficou em aberto. | **Não** — falso bloqueio |
| **Sinal cobrado e não pago** | 84550, 84552, 84557, 84571, 84577, 84579, 84583 | Adiantamento emitido, em aberto, `PaidToDate = 0`; nenhum pagamento do cliente no SAP desde 20/10. | Sim, para o SAP (falta o Financeiro confirmar) |
| **Vendas (financeiro) bloqueado** | 84542, 84568, 84592 | `Financeiro = 'Bloqueado'`; 84542 e 84592 sem sinal. | Sim |

## Fatos que travam o desenho

- **A regra mora na view do SAP**, e a definição dela não é legível pelo usuário de leitura (`SYS.VIEWS`
  devolve vazio). Mudar a regra = mudar a view (do Marcelo) ou sobrepor no código.
- **Dois códigos leem a view:** `situacao_pedidos.py` (.11) e o do web, **irmãos diffáveis**: mudança de regra vai
  nos dois no mesmo commit.
- **A API de Pedidos WBC não tem essas colunas hoje.** A lista vem da `ORDR`/`OOPR` (Controle de Produção, 8080);
  as etapas estão na API de situação (8077, `GET /pedidos/situacao`, cache de 120 s).
- **Pedido fechado por entrega futura (84537) está fora da view**: as etapas dele vêm como "Entrega futura" (regra
  de 16/10, `PLANO_ENTREGA_FUTURA`).

## Fases

### F0 — Diagnóstico <span>✅ concluída 11/11 (só leitura)</span>
**Meta:** saber, pedido a pedido, se o bloqueio é real.
- Tabela acima: 2 falsos, 7 com sinal não pago, 3 com Vendas bloqueado.

### F1 — Corrigir no SAP os 2 falsos + conferir os 7 <span>(Financeiro / Marcelo)</span>
**Meta:** a tela mostra 84589 e 84536 liberados sem mudar código.
- 84589: cancelar o adiantamento duplicado **2817** (o 2811 já está pago).
- 84536: decidir com o Financeiro — o sinal virou NF de entrega futura; ou se cancela o adiantamento 2765, ou se
  reconcilia o pagamento 6860 com ele (D1).
- Os 7 "sinal não pago": o Financeiro confirma se algum foi pago fora do SAP (depósito ainda não lançado).

### F2 — Regra do sinal pago <span>(só se F1 mostrar que se repete)</span>
**Meta:** pagamento do sinal em outro documento do mesmo pedido libera a Produção.
- Proposta de regra: sinal pago quando **algum** adiantamento do pedido está pago **ou** o valor pago nas NFs do
  pedido cobre o sinal; adiantamento duplicado em aberto não bloqueia.
- Onde: na view (D2). Medir antes, em produção e só leitura, quantos pedidos mudariam.

### F3 — Etapas e sinal na API de Pedidos WBC <span>✅ no ar 11/11 (`3dcb0a0`), conferida</span>
**Meta:** a equipe vê, em cada pedido da lista, Vendas, Produção, Entrega e Sinal, iguais à Situação dos Pedidos.
- `pedidos[]` ganhou `financeiro`, `producao`, `entrega` (`"Liberado"`, `"Bloqueado"`, `"Fechado"` ou
  `"Entrega futura"`) e `sinal` (`true`/`false`); a lista ganhou `etapas_em`.
- `acoes.etapas_dos_pedidos`: uma chamada à 8077 (`/pedidos/situacao`) por página e uma por pedido fora do recorte da
  view (o fechado por entrega futura). 8077 fora = campos `null`; a lista continua.
- Nas duas listas. Testes (`test_etapas_na_api.py`), contrato `API_PEDIDOS_WBC.md` 7.1 e texto da equipe 3.3.
- Conferência na .11 (script só leitura, pela API com a chave): sem chave = 401; todas as páginas de novos (1) e
  integrados (8); cada pedido comparado com `/pedidos/<n>/situacao` da 8077 → **116 pedidos, 0 diferenças, 0 nulos**.
- <span>O que mordeu:</span> o 84584 entrou na view depois de ganhar OPs e passou a vir com as etapas reais
  (Liberado), não "Entrega futura" como o texto da equipe dizia; corrigido.

### F4 — No ar e avisar a equipe
- Deploy da .11 (Marcelo); conferir pela API nos 12 pedidos e no 84537; atualizar o texto da equipe.

## Decisões

1. **D1 — 84536: o que fazer com o adiantamento 2765.** *Recomendo:* cancelar o 2765 (o sinal já entrou pela NF de
   entrega futura e foi pago); quem decide é o Financeiro.
2. **D2 — Onde mora a regra do sinal pago.** *Recomendo:* **na view** — um lugar só, e tela, app, .11 e Mira passam
   a concordar sozinhos. Preciso do texto atual da view (o meu usuário não lê a definição).
3. ✅ **D3 — Nome dos campos** (aprovada 11/11). `financeiro`, `producao`, `entrega`, `sinal` — os mesmos da API de situação
   (`API_SITUACAO_PEDIDOS.md`), e o guia diz que "Vendas" na tela = `financeiro`. Um vocabulário só.
4. ✅ **D4 — Fonte das etapas no Controle de Produção** (aprovada 11/11). loopback à 8077 (`/pedidos/situacao`), como o CP
   já faz para sincronizar a OS: mesmo cache e mesma regra da tela; ler a view direto no CP duplicaria a regra.

---
Diagnóstico: consultas só de leitura no HANA (`VW_STATUS_PEDIDO_DDP`, `ORDR`, `ODPI`/`DPI1`, `OINV`/`INV1`,
`ORCT`/`RCT2`), 11/11/2026. Origem: tela "Situação dos Pedidos" de 11/11 12:04 e pedido da equipe da API.
