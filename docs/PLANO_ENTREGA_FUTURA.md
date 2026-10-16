# Plano — Entrega futura não é pedido fechado

> **Status (16/10/2026):** plano escrito, nada codado. Diagnóstico feito no SAP de produção, só leitura.
> Próximo passo: o Marcelo fecha as decisões D1–D4 (§5); depois F0.
> Página: https://claude.ai/artifact/NQeW5baaQ5NLYSTDt59JFn (mesma url a cada atualização)

## O problema

A equipe do PCP recebe da .11 `status_pedido: "Fechado"` para 28 pedidos que, para eles, ainda estão
em Liberado, Em Produção ou Aguardando Entrega. A .11 diz "Fechado" sempre que `ORDR.DocStatus = 'C'`
(função `classificar_pedido` em `extract_ordens_servico_engenharia.py`). Só que o SAP fecha o pedido
assim que **qualquer NF de saída** é copiada dele, inclusive a NF de **simples faturamento para entrega
futura** (CFOP 5922/6922), emitida antes de a mercadoria sair. A mercadoria só sai depois, com a NF de
**remessa** (CFOP 5116/6116).

## O que o SAP mostrou (16/10/2026, leitura no HANA de produção)

Os 28 pedidos da lista do PCP:

| Grupo | Pedidos | Como fecharam |
| --- | --- | --- |
| **Entrega futura** (7) | 84260, 84367, 84407, 84423, 84430, 84464, 84471 | NF de saída com CFOP **5922/6922** copiada do pedido |
| **NF de venda normal** (21) | 83411, 84146, 84172, 84238, 84246, 84262, 84273, 84280, 84296, 84300, 84306, 84309, 84318, 84324, 84326, 84340, 84350, 84412, 84436, 84438, 84447 | NF de saída com CFOP 5101/6101/6401 |

Remessa das 7 de entrega futura:

| Pedido | NF futura | Qtd | Remessas (5116) |
| --- | --- | --- | --- |
| 84260 | 5623 (02/09) | 1 | nenhuma |
| 84260 | 5817 (29/09) | 4 | 6 remessas (29/09 a 08/10), soma 3,0 |
| 84367 | 5699 (15/09) | 1 | nenhuma |
| 84407 | 5743 (18/09) | 175 | 5918 (15/10), 87 — **parcial** (o PCP mostra "2 cargas") |
| 84423 | 5763 (22/09) | 2 | nenhuma |
| 84430 | 5778 (25/09) | 15 | nenhuma |
| 84464 | 5868 (07/10) | 1 | nenhuma |
| 84471 | 5892 (09/10) | 2 | nenhuma |

Em 2026: **59** NFs de entrega futura vivas, **40** com alguma remessa, **19** sem nenhuma.

## Fatos que travam o desenho

- **Não é fatura de reserva.** Todas têm `OINV.isIns = 'N'`. O que marca a entrega futura é o **CFOP
  da linha** (`INV1.CFOPCode` 5922/6922).
- **O campo "Nota Espelho" do pedido não serve.** `ORDR.U_U_INO_NotaEspelho` está `N` nos 28; só 6
  pedidos de 2026 têm `Y`.
- **A remessa não aponta para o pedido.** A NF 5116/6116 nasce avulsa (`INV1.BaseType = -1`); o elo
  é a tabela de documentos referenciados: `INV21.RefObjType = '13'` e `INV21.RefDocEntr` = DocEntry da
  NF futura.
- **Quantidade da remessa pode não bater com a da NF futura** (84260: 4 na futura, remessas de 0,48 e
  0,6). O critério de "remessa completa" precisa ser medido (F0), não suposto.
- **Nada de entrega (ODLN)** em nenhum dos 28: a Altamira fatura direto, sem documento de entrega.
- **NF de venda normal (5101/6101/6401) não deixa rastro posterior no SAP.** Se ela sai antes da
  mercadoria, o SAP não tem como saber quando a mercadoria saiu (D1).
- **84412:** a NF viva é a 5832, série manual (`SeqCode = -2`, `Model = 0`, nº 122). Conferir na F0.
- **Quem usa a regra:** `classificar_pedido` serve três leituras — `consultar_status_pedido`
  (rotas `/pedidos/<n>/situacao`, `/pedidos/<n>/data-entrega`, `/ordens-servico/<nped>`),
  `listar_pedidos_com_os` (lista de OS) e `diagnosticar_nped`. Corrigir ali corrige as três.
- **Só leitura no SAP.** Nada deste plano grava no SAP.

## Fases

### F0 — Medir o critério (minha, só leitura)
**Meta:** saber, com dados, quando uma entrega futura está de fato toda remetida.
- Nas 59 NFs futuras de 2026: comparar quantidade por item e valor entre a NF futura e as remessas
  que apontam para ela (`INV21`). Escolher o critério que acerta os casos já encerrados.
- Conferir se toda remessa 5116/6116 tem `INV21` (se alguma não tiver, ela fica invisível).
- Conferir a NF manual do 84412.
- Lista do que muda hoje: pedidos fechados que passariam a "aguardando remessa".

### F1 — A regra na .11 (minha)
**Meta:** a .11 deixa de chamar de "Fechado" o pedido de entrega futura ainda não remetido.
- Função nova, uma consulta para vários pedidos: NFs vivas do pedido com CFOP 5922/6922 e as
  remessas vivas que apontam para cada uma (`INV21`), com o critério da F0.
- `classificar_pedido` passa a receber esse resultado; as três leituras usam a mesma resposta.
- Campos novos na resposta (D2): `faturamento: "entrega_futura"` e `remessa: "nenhuma" | "parcial" |
  "completa"`. Sem entrega futura, os campos não mudam nada.
- Testes com os casos reais: sem remessa (84464), parcial (84407), completa (um dos 40 históricos),
  NF futura cancelada, NF de venda normal.

### F2 — Rotas, MCP e contrato (minha) + revisão
**Meta:** o app do PCP recebe a situação certa em todas as rotas que ele usa.
- `/pedidos/<n>/situacao`, `/pedidos/<n>/data-entrega`, lista e detalhe de OS: entrega futura não
  remetida responde como pedido em aberto, com os campos novos (D2, D3).
- Descrição das ferramentas MCP, `API_SITUACAO_PEDIDOS.md`, `API_DATA_ENTREGA_COMBINADA.md`, guia do PCP.
- Revisão do Sonnet 5.5 em esforço alto; achado real é corrigido antes de seguir.

### F3 — No ar (do Marcelo) + aviso à equipe do PCP
**Meta:** os 7 pedidos voltam a aparecer como em aberto no app do PCP.
- Marcelo: deploy da .11.
- Eu: confiro os 7 pedidos pela API e escrevo o texto para a equipe do PCP.

## Decisões

1. **D1 — Os 21 pedidos com NF de venda normal (5101/6101/6401).** Pelo SAP eles estão faturados
   e, pela regra fiscal, a mercadoria sai junto com essa NF. O SAP não guarda nada depois disso.
   *Recomendação:* a .11 continua dizendo "Fechado" para eles, mas passa a mandar a data da última
   NF (`faturado_em`), e o app do PCP mantém a etapa própria até a Expedição confirmar a entrega.
   Se a Altamira emite essa NF antes de produzir, só a Expedição deles sabe quando saiu.
2. **D2 — Que valor mandar para a entrega futura não remetida.** *Recomendação:* `status_pedido:
   "Aberto"` + `faturamento: "entrega_futura"` + `remessa: "nenhuma" | "parcial"`. O app do PCP já
   trata "Aberto" como pedido vivo, então para de fechar sem precisar mudar nada lá.
3. **D3 — Quando a remessa conta como completa.** *Recomendação:* quantidade por item somada nas
   remessas ≥ quantidade da NF futura; a F0 confirma nos 40 casos históricos. Se as unidades não
   baterem, cai para valor.
4. **D4 — A Situação dos Pedidos do OrçaView também perde esses pedidos.** A view do SAP
   (`VW_STATUS_PEDIDO_DDP`) só traz pedido aberto, então a entrega futura some da tela do Comercial
   e a data combinada recusa (`fora_do_recorte`). *Recomendação:* fora deste plano; vira um plano
   próprio depois da F3.

---
Diagnóstico: consultas só de leitura no HANA (`ORDR`, `RDR1`, `OINV`, `INV1`, `INV21`, `ODLN`, `DLN1`),
16/10/2026. Referência dos campos: skill `sap-service-layer` (`Document.ReserveInvoice`,
`DocumentLine.LineStatus`).
