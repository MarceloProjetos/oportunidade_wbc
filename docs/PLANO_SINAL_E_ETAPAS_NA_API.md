# Plano — Sinal e etapas na API de Pedidos WBC

> **Status (11/11/2026):** F0 feita e **corrigida** (só leitura): **1 falso bloqueio só, o 84536**; o 84589 é real.
> **F3 no ar (`3dcb0a0`) e conferida na .11**: 116 pedidos nas duas listas, 0 sem etapas, 0 diferentes da API de
> situação, páginas em 0,1–2,0 s, sem chave = 401. F1 espera o Financeiro (só o 84536); F2 só se F1 mostrar
> repetição; D1 e D2 abertas.
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
| **Sinal cobrado duas vezes, pago pela NF** | **84536** | O sinal (40%, R$ 15.554,40) foi cobrado pelo adiantamento **2765** (27/10) **e** pela NF de entrega futura **32528** (CFOP 5922, 05/11, mesmo valor). O cliente pagou a NF (pagamento 6860, 10/11); o 2765 ficou em aberto e é ele que segura a Produção. | **Não** — falso bloqueio |
| **Sinal cobrado e não pago** | 84550, 84552, 84557, 84571, 84577, 84579, 84583, **84589** | Adiantamento emitido, em aberto, sem recebimento. No 84589 o 1º adiantamento (2811) foi **anulado por devolução** (1351) depois que o preço mudou, e o 2817 é o sinal válido (R$ 29.406,65, vence 13/11). | Sim, para o SAP (falta o Financeiro confirmar) |
| **Vendas (financeiro) bloqueado** | 84542, 84568, 84592 | `Financeiro = 'Bloqueado'`; 84542 e 84592 sem sinal. | Sim |

## Fatos que travam o desenho

- **A regra mora na view do SAP**, e a definição dela não é legível pelo usuário de leitura (`SYS.VIEWS`
  devolve vazio). Mudar a regra = mudar a view (do Marcelo) ou sobrepor no código.
- **Dois códigos leem a view:** `situacao_pedidos.py` (.11) e o do web, **irmãos diffáveis**: mudança de regra vai
  nos dois no mesmo commit.
- **"Pago" no adiantamento não quer dizer recebido.** `ODPI.PaidToDate` cheio e `DocStatus = 'C'` também aparecem
  quando o adiantamento é **anulado por devolução** (como o 2811). Recebimento de verdade é `RCT2` com
  `InvType = 203`. Em 30 dias, 45 adiantamentos fechados não têm recebimento ligado (anulados por devolução ou
  baixados de outro jeito).
- **Pedido fechado por entrega futura fora da view** (84537) vem com as etapas "Entrega futura" (regra de 16/10,
  `PLANO_ENTREGA_FUTURA`); dentro dela (84584, depois das OPs), com as etapas reais.

## Fases

### F0 — Diagnóstico <span>✅ concluída 11/11 (só leitura), corrigida no mesmo dia</span>
**Meta:** saber, pedido a pedido, se o bloqueio é real.
- Tabela acima: 1 falso (84536), 8 com sinal não pago, 3 com Vendas bloqueado.
- <span>O que mordeu:</span> a 1ª leitura deu o 84589 como falso, porque o adiantamento 2811 tinha `PaidToDate`
  cheio. Antes de cancelar o 2817 (pedido do Marcelo), a conferência achou a devolução 1351 que anulou o 2811 e
  nenhum recebimento. **Cancelar o 2817 teria apagado o único sinal válido do pedido.**

### F1 — Corrigir no SAP o 84536 + conferir os 8 <span>(Financeiro / Marcelo)</span>
**Meta:** a tela mostra o 84536 liberado sem mudar código.
- 84536: anular o adiantamento 2765 (como fazem com os outros: devolução baseada nele), porque o sinal já foi
  cobrado e pago pela NF 32528 (D1).
- 84589: **nada a fazer no SAP**; o sinal 2817 ainda não foi pago.
- Os 8 "sinal não pago": o Financeiro confirma se algum foi pago fora do SAP (depósito ainda não lançado).

### F2 — Regra do sinal pago <span>(só se F1 mostrar que se repete)</span>
**Meta:** sinal cobrado e pago numa NF do pedido libera a Produção mesmo com adiantamento em aberto.
- Proposta: sinal pago quando há **recebimento** (`RCT2`) do adiantamento **ou** de uma NF do pedido que cubra o
  valor do sinal. Nunca pelo `PaidToDate` sozinho (ver os fatos acima).
- Onde: na view (D2). Medir antes, em produção e só leitura, quantos pedidos mudariam.

### F3 — Etapas e sinal na API de Pedidos WBC <span>✅ no ar 11/11 (`3dcb0a0`), conferida</span>
**Meta:** a equipe vê, em cada pedido da lista, Vendas, Produção, Entrega e Sinal, iguais à Situação dos Pedidos.
- `pedidos[]` ganhou `financeiro`, `producao`, `entrega` (`"Liberado"`, `"Bloqueado"`, `"Fechado"` ou
  `"Entrega futura"`) e `sinal` (`true`/`false`); a lista ganhou `etapas_em`.
- `acoes.etapas_dos_pedidos`: uma chamada à 8077 (`/pedidos/situacao`) por página e uma por pedido fora do recorte da
  view (o fechado por entrega futura). 8077 fora = campos `null`; a lista continua.
- Nas duas listas. Testes (`test_etapas_na_api.py`), contrato `API_PEDIDOS_WBC.md` 7.1 e guia da equipe
  (`docs/GUIA_EQUIPE_API_PEDIDOS_WBC.md`).
- Conferência na .11 (script só leitura, pela API com a chave): sem chave = 401 (conferido à parte); todas as páginas
  de novos (1) e integrados (8); cada pedido comparado com `/pedidos/<n>/situacao` da 8077 → **116 pedidos, 0
  diferenças, 0 nulos**.
- <span>O que mordeu:</span> o 84584 entrou na view depois de ganhar OPs e passou a vir com as etapas reais
  (Liberado), não "Entrega futura" como o texto da equipe dizia; corrigido.

### F4 — Equipe implementa
- Guia de implementação para a equipe da API (`docs/GUIA_EQUIPE_API_PEDIDOS_WBC.md` + página publicada).

## Decisões

1. **D1 — 84536: o que fazer com o adiantamento 2765.** *Recomendo:* anular o 2765 por devolução, como o Financeiro
   já faz com os outros (o sinal foi cobrado e pago pela NF 32528). Quem decide é o Financeiro.
2. **D2 — Onde mora a regra do sinal pago.** *Recomendo:* **na view** — um lugar só, e tela, app, .11 e Mira passam
   a concordar sozinhos. Preciso do texto atual da view (o meu usuário não lê a definição).
3. ✅ **D3 — Nome dos campos** (aprovada 11/11). `financeiro`, `producao`, `entrega`, `sinal` — os mesmos da API de situação
   (`API_SITUACAO_PEDIDOS.md`), e o guia diz que "Vendas" na tela = `financeiro`. Um vocabulário só.
4. ✅ **D4 — Fonte das etapas no Controle de Produção** (aprovada 11/11). loopback à 8077 (`/pedidos/situacao`), como o CP
   já faz para sincronizar a OS: mesmo cache e mesma regra da tela; ler a view direto no CP duplicaria a regra.

---
Diagnóstico: consultas só de leitura no HANA (`VW_STATUS_PEDIDO_DDP`, `ORDR`, `ODPI`/`DPI1`, `OINV`/`INV1`,
`ORCT`/`RCT2`, `ORIN`/`RIN1`, `OITR`/`ITR1`), 11/11/2026. Origem: tela "Situação dos Pedidos" de 11/11 12:04 e pedido da
equipe da API.
