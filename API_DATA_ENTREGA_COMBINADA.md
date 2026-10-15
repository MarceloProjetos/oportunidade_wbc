# API — Data de entrega combinada (Produção → Comercial)

Contrato técnico das rotas e ferramentas da .11 (`192.168.7.11`) para a **Produção** pedir uma nova data de
entrega de um pedido de venda e acompanhar a decisão do **Comercial**. Desenho completo:
`docs/DESENHO_DATA_ENTREGA_COMBINADA.md`. Versão de 15/10/2026.

> **Equipe do PCP:** comece pelo guia passo a passo `docs/GUIA_EQUIPE_PCP_DATA_ENTREGA.md` — este arquivo é a
> referência técnica.

## 1. O que é

- **Vocabulário:** "pedido" = pedido de venda (o **DocNum**, o número da tela). "Solicitação" = o pedido de
  mudança da data.
- A Produção **pede**; o Comercial **aprova ou recusa** na tela Situação dos Pedidos do OrçaView. O Comercial
  também pode mudar a data por conta própria (essas mudanças aparecem na conversa, com `origem: "comercial"`).
- **Nada muda sozinho e o SAP nunca é alterado.** A data do SAP (`data_sap`) fica como está; a data que vale é
  a `data_vigente`.
- Motivo interno conta como **atraso nosso**; mudança pedida pelo cliente não.

## 2. Acesso

| Escopo | Para quê |
| --- | --- |
| `entrega:solicitar` | `POST /pedidos/<n>/data-entrega/solicitacoes` e a ferramenta `solicitar_data_entrega` |
| `entrega:ler` | os dois `GET` e as ferramentas `data_entrega_pedido` e `solicitacoes_data_entrega` |

`leitura` **não** abre estas rotas: a conversa traz motivos internos (ex.: quebra de máquina) que não podem chegar
a cliente nem a representante. Concessão na .11, mantendo a chave atual:

```
python -m seguranca acrescentar <nome-da-chave-da-producao> --escopos entrega:ler,entrega:solicitar
python -m seguranca acrescentar mcp-servico --escopos entrega:ler,entrega:solicitar --declara-usuario
python -m seguranca acrescentar orcaview-90 --escopos entrega:ler
python -m seguranca listar
```

Nenhum agente de IA recebe `entrega:solicitar`. Chave só no cabeçalho `X-API-Key`. Confira no `listar` que o
`mcp-servico` tem `declara_usuario`: sem isso, toda solicitação vinda do MCP fica registrada como `mcp-servico`,
e não como o cliente real.

## 3. Regras das datas

- **Data do SAP** = data de entrega do pedido no SAP (view `VW_STATUS_PEDIDO_DDP`).
- **Data vigente** = a da última mudança **aprovada** (pela hora da decisão); sem nenhuma, a do SAP.
- **Data de referência** = a do SAP, trocada por cada mudança aprovada com motivo `alteracao_cliente`.
- **Atraso nosso (dias)** = vigente − referência, quando maior que zero.
- **Atrasado:** sem mudança aprovada, vale o SAP (`atrasado_fonte: "sap"`); com mudança, `true` quando o pedido
  está aberto e (vigente > referência ou hoje > vigente) (`atrasado_fonte: "combinada"`).
- `null` nas datas (`data_*`), em `atraso_nosso_dias` e em `atrasado` = **não dá para afirmar**. Nunca é zero
  nem "não". Já `pendente: null` = **não há** solicitação pendente; `observacao`, `decidido_*` e `motivo_recusa`
  nulos = campo não preenchido.

Exemplo: SAP 20/11 → Produção pede 27/11 por quebra de máquina, Comercial aprova → vigente 27/11, atraso nosso
7 dias → depois o cliente pede 05/12 → vigente e referência 05/12, atraso nosso 0.

## 4. Motivos

| Código | Rótulo | Atraso nosso |
| --- | --- | --- |
| `producao_sobrecarregada` | Produção sobrecarregada | sim |
| `tempo_producao_apertado` | Tempo de produção apertado | sim |
| `quebra_maquina` | Quebra de máquina | sim |
| `alteracao_cliente` | Alteração por parte do cliente | não |

`alteracao_cliente` só quando o **cliente** pediu.

## 5. `POST /pedidos/<n>/data-entrega/solicitacoes`

```json
{"data_nova": "2026-11-27", "motivo": "quebra_maquina",
 "observacao": "Prensa 3 parada até 20/11", "solicitante": "Ana (PCP)"}
```

- `data_nova`: `AAAA-MM-DD`, de hoje (relógio de Brasília) até hoje + 365, diferente da data vigente. Antecipar
  é aceito.
- `motivo`: um dos 4 códigos (texto). `observacao`: opcional, até 1000 caracteres; quebra de linha aceita
  (`\n`, `\r\n`), outros caracteres de controle não.
- `solicitante`: obrigatório, até 80 caracteres — o nome de quem pede.

**201** — gravada como pendente:

```json
{"ok": true, "ja_existia": false,
 "solicitacao": {"id": 812, "doc_num": 84453, "data_nova": "2026-11-27", "data_anterior": "2026-11-20",
   "data_sap": "2026-11-20", "motivo": "quebra_maquina", "gera_atraso": true, "observacao": "...",
   "origem": "producao_api", "situacao": "pendente", "solicitado_por": "Ana (PCP)",
   "solicitado_cliente": "<chave>", "solicitado_em": "2026-11-10T12:00:00+00:00",
   "decidido_por": null, "decidido_em": null, "motivo_recusa": null},
 "entrega": {"...": "o mesmo bloco do GET, já com a pendente"},
 "aviso": "Nada mudou ainda: a data so muda quando o Comercial aprovar no OrcaView. O SAP nao e alterado."}
```

**200 `ja_existia: true`** — a mesma data e o mesmo motivo já estavam pendentes (repetição segura depois de um
timeout). A `observacao` nova é ignorada.

## 6. `GET /pedidos/<n>/data-entrega`

```json
{"ok": true, "pedido": 84453, "status_pedido": "Aberto", "sap_disponivel": true, "cache_idade_s": 34,
 "data_sap": "2026-11-20", "data_vigente": "2026-11-27", "data_referencia": "2026-11-20",
 "atraso_nosso_dias": 7, "alterada": true, "atrasado": true, "atrasado_fonte": "combinada",
 "pendente": null, "conversa": [ "...linhas no formato de 'solicitacao', mais antiga primeiro" ]}
```

Pedido fora da tela do Comercial: `data_sap: null`, status lido do SAP ao vivo; aberto → `atrasado: null`.

## 7. `GET /pedidos/data-entrega/solicitacoes`

- `?situacao=pendente` (padrão: todas as pendentes) | `decididas` (aprovadas/recusadas nos últimos `dias`,
  incluindo as mudanças diretas do Comercial) | `todas` (pedidas ou decididas nos últimos `dias`).
- `?dias=7` (1 a 90), `?limite=100` (1 a 200). Mais novas primeiro.
- Resposta: `{"ok": true, "situacao": "...", "dias": 7, "total": N, "solicitacoes": [...]}` — cada item tem
  `doc_num`.

## 8. Erros

Recusas destas rotas: `{"ok": false, "motivo": "<chave estável>", "error": "<texto>", "pedido": <n>}`. **Decida
pelo `motivo`**, nunca pelo texto.

| HTTP | `motivo` | O que fazer |
| --- | --- | --- |
| 400 | `corpo_invalido`, `data_invalida`, `motivo_invalido` (traz `motivos`), `observacao_longa`, `observacao_invalida`, `solicitante_invalido`, `chave_nao_suportada` | corrigir o envio |
| 404 | `pedido_nao_encontrado` | conferir o número (é o DocNum) — também para número acima de 2.147.483.647 |
| 404 | `fora_do_recorte` | pedido aberto que não está na tela do Comercial — avisar o suporte |
| 409 | `ja_ha_pendente` (traz `pendente`: quem, quando, data, motivo) | esperar o Comercial decidir; não repetir |
| 409 | `pedido_fechado`, `pedido_cancelado`, `sem_data_sap`, `docnum_ambiguo` | não há data a mudar |
| 422 | `data_no_passado`, `data_muito_distante`, `data_igual_a_vigente`, `parametro_invalido` | corrigir a data/parâmetro |
| 429 | `error: "rate_limited"` + `retry_after_s` | aguardar (teto padrão: 30 pedidos/min) |
| 503 | `indeterminado` | SAP sem resposta (ou a view falhou); nada gravado; tentar de novo |
| 503 | `base_indisponivel`, `base_nao_configurada` | base de dados fora; no POST, conferir com o GET antes de repetir |

**Atenção:** 401/403 vêm do guarda comum, em outra forma: `{"ok": false, "error": "unauthorized"|"forbidden",
"motivo": "<texto>"}` — ali o `motivo` é **texto**, e quem decide é o `error`.

## 9. MCP (`192.168.7.11:8078`)

| Ferramenta | Escopo | O que faz |
| --- | --- | --- |
| `data_entrega_pedido(pedido)` | `entrega:ler` | o `GET` do §6 |
| `solicitacoes_data_entrega(situacao, dias)` | `entrega:ler` | o `GET` do §7 |
| `solicitar_data_entrega(pedido, data_nova, motivo, observacao, solicitante, confirmar)` | `entrega:solicitar` | `confirmar=False` = prévia (não grava); `True` = o `POST` do §5 |

A porta 8078 carimba o **cliente** que chamou (`solicitado_cliente`, provado) e, quando o cliente MCP manda
`X-SIS-Usuario` com uma credencial `declara_usuario`, a **pessoa** (`solicitado_por`). Sem esse cabeçalho, a
pessoa é o `solicitante` informado — declarada, não provada.

## 10. Tempos e repetição

- Use timeout de cliente **≥ 60 s** (a 1ª falha do SAP leva ~51 s; o MCP espera 60 s nas leituras e até
  180 s no POST).
- Repetir o POST com os **mesmos** dados é seguro: volta `ja_existia`. Antes de repetir depois de um timeout,
  confira com o `GET /pedidos/<n>/data-entrega`.
- Datas e horas de `solicitado_em`/`decidido_em` vêm em **UTC** (`+00:00`).
