# Plano — Entrega futura não é pedido fechado

> **Status (16/10/2026):** F0 concluída (só leitura no SAP de produção). D1–D4 aprovadas pelo Marcelo;
> D3 virou "por valor" com a medição. **Aberta: D5** (3 pedidos antigos). Próximo passo: F1.
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

## Fatos que travam o desenho

- **Não é fatura de reserva.** Todas têm `OINV.isIns = 'N'`. O que marca a entrega futura é o **CFOP
  da linha** (`INV1.CFOPCode` 5922/6922).
- **O campo "Nota Espelho" do pedido não serve.** `ORDR.U_U_INO_NotaEspelho` está `N` nos 28; só 6
  pedidos de 2026 têm `Y`.
- **A remessa nasce avulsa** (`INV1.BaseType = -1`) e se liga pela tabela de documentos referenciados
  `INV21`, de **dois jeitos**: para a NF futura (`RefObjType = '13'`) **ou para o pedido**
  (`RefObjType = '17'`). Algumas remessas só têm a referência ao pedido (83542, 83919, 84039). Todas
  as 91 remessas vivas de 2026 têm alguma referência.
- **A NF futura pode ser anulada por devolução** (`ORIN` copiada da NF futura, CFOP 1922/2922): 5 casos
  em 2026. Sem descontar a anulação, o pedido pareceria sem remessa para sempre.
- **Quantidade não serve para medir a remessa.** Itens em conjunto (CJ) saem fracionados: 0,41 na NF
  futura e 1 na remessa (83898), 0,95 e 3 (84021), sempre com o mesmo valor.
- **Nada de entrega (ODLN)** em nenhum dos 28: a Altamira fatura direto, sem documento de entrega.
- **NF de venda normal (5101/6101/6401) não deixa rastro posterior no SAP** (D1).
- **84412:** a NF viva é a 5832, série manual (`SeqCode = -2`, `Model = 0`, nº 122, R$ 850, CFOP 5101),
  emitida no lugar da NF-e 5798 cancelada. É venda normal: fica no grupo da D1.
- **Quem usa a regra:** `classificar_pedido` serve três leituras — `consultar_status_pedido`
  (rotas `/pedidos/<n>/situacao`, `/pedidos/<n>/data-entrega`, `/ordens-servico/<nped>`),
  `listar_pedidos_com_os` (lista de OS) e `diagnosticar_nped`. Corrigir ali corrige as três.
- **Só leitura no SAP.** Nada deste plano grava no SAP.

## A regra (medida na F0)

Por **pedido**, em valor (`LineTotal`, sem imposto):

- **Futura líquida** = soma das NFs vivas com CFOP 5922/6922 copiadas do pedido − soma das devoluções
  vivas copiadas dessas NFs.
- **Remetido** = soma das NFs vivas com CFOP 5116/6116/5117/6117 que referenciam (`INV21`) uma dessas
  NFs ou o próprio pedido (cada remessa conta uma vez).
- **Remessa completa** quando falta menos de R$ 1,00 ou 0,5% da futura líquida (o maior dos dois).
  Diferença medida nos casos encerrados: R$ 0,02 (84021) e 2% a mais (83898).

Resultado nos 55 pedidos com NF futura emitida em 2026 (16/10):

| Situação | Pedidos |
| --- | --- |
| Remessa completa (100% do valor) | 37 pedidos, de 83542 a 84209 |
| Anulada inteira por devolução | 83422, 83823, 83949, 84176 |
| Anulação parcial + remessa do resto (102%) | 83898 |
| Pedido ainda aberto no SAP (nada muda) | 84420, 84467, 84468 |
| **Fechado no SAP, remessa pendente** | **84260** (68,6%), **84367** (0%), **84407** (45,5%), **84423**, **84430**, **84464**, **84471** (0%) — os 7 da lista do PCP |
| **Fechado no SAP, remessa pendente, antigos** | **83627** (75,1%, entrega 17/03), **84022** (0%, entrega 01/06), **84271** (0%, entrega 24/09) — D5 |

## Fases

### F0 — Medir o critério <span>✅ concluída 16/10</span>
**Meta:** saber, com dados, quando uma entrega futura está de fato toda remetida.
- 59 NFs futuras de 2026 (55 pedidos), 91 remessas, devoluções: regra acima. Quantidade descartada; valor por pedido
  acerta todos os casos encerrados.
- Nenhuma remessa sem `INV21`; a referência pode ser à NF (13) ou ao pedido (17).
- 84412 conferido (venda normal, série manual).
- Com a regra, 10 pedidos fechados no SAP passam a "aguardando remessa": os 7 da lista + 3 antigos (D5).

### F1 — A regra na .11 (minha)
**Meta:** a .11 deixa de chamar de "Fechado" o pedido de entrega futura ainda não remetido.
- Função nova, uma consulta para vários pedidos, com a regra acima.
- `classificar_pedido` passa a usar esse resultado; as três leituras dão a mesma resposta.
- Campos novos: `faturamento: "entrega_futura"` e `remessa: "nenhuma" | "parcial" | "completa"`; para
  NF de venda normal, `faturado_em` (data da última NF viva).
- Testes com os casos reais: sem remessa (84464), parcial (84407), completa só com referência ao pedido
  (83919), anulada (83422), anulação parcial (83898), NF de venda normal (83411).

### F2 — Rotas, MCP e contrato (minha) + revisão
**Meta:** o app do PCP recebe a situação certa em todas as rotas que usa.
- `/pedidos/<n>/situacao`, `/pedidos/<n>/data-entrega`, lista e detalhe de OS: entrega futura não
  remetida responde como pedido em aberto, com os campos novos.
- Descrição das ferramentas MCP, `API_SITUACAO_PEDIDOS.md`, `API_DATA_ENTREGA_COMBINADA.md`, guia do PCP.
- Revisão do Sonnet 5.5 em esforço alto; achado real é corrigido antes de seguir.

### F3 — No ar (do Marcelo) + aviso à equipe do PCP
**Meta:** os pedidos de entrega futura voltam a aparecer como em aberto no app do PCP.
- Marcelo: deploy da .11.
- Eu: confiro os pedidos pela API e escrevo o texto para a equipe do PCP.

## Decisões

1. ✅ **D1 — Os 21 pedidos com NF de venda normal.** A .11 continua dizendo "Fechado", passa a mandar
   `faturado_em` (data da última NF), e o app do PCP mantém a etapa própria até a Expedição confirmar.
2. ✅ **D2 — Valor para a entrega futura não remetida:** `status_pedido: "Aberto"` +
   `faturamento: "entrega_futura"` + `remessa: "nenhuma" | "parcial"`.
3. ✅ **D3 — Quando a remessa conta como completa:** a recomendação aprovada previa cair para valor se
   as unidades não batessem. A F0 mostrou que não batem (itens em conjunto saem fracionados), então
   vale o **valor por pedido, descontada a anulação** (regra acima).
4. ✅ **D4 — A Situação dos Pedidos do OrçaView:** fora deste plano; plano próprio depois da F3.
5. **D5 — Três pedidos antigos voltariam a "em aberto":** 83627 (MG VIDROS, falta R$ 109.764 de
   R$ 441.416, última remessa 16/04), 84022 (CORELLO, R$ 195.642 sem remessa; o pedido-irmão 83949 de
   mesmo valor foi anulado), 84271 (PRONAP, R$ 26.833 sem remessa desde 21/09). Pode ser remessa
   esquecida no SAP ou entrega de fato pendente. *Recomendação:* a regra vale para todos; antes do
   deploy, o faturamento confere os três. Se já foram entregues, a correção é no SAP (anular o saldo da
   NF futura ou referenciar a remessa), e a .11 passa a dizer "Fechado" sozinha.

---
Diagnóstico: consultas só de leitura no HANA (`ORDR`, `RDR1`, `OINV`, `INV1`, `INV21`, `ORIN`, `RIN1`,
`RIN21`, `ODLN`, `DLN1`), 16/10/2026. Referência dos campos: skill `sap-service-layer`.
