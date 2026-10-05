# PLANO — Datas reais de liberação + dados de NF na API de Situação dos Pedidos

**Status (25/09/2026): ✅ PLANO ENTREGUE — no ar na .11 desde 15:35** (deploy `a770d7a`,
conferido ao vivo na API). Nada pendente de código; resta avisar o grupo consumidor.
Artifact: <https://claude.ai/artifact/BFc4XFUoJtKd8A4Hk1uM6j>

## 0. O pedido, reescrito

> Na API de Situação dos Pedidos do .11 (`GET /pedidos/...`, perfil `completo`), acrescentar:
>
> 1. **Quando a Produção foi liberada** — data e hora reais, não a `data_lib_prod` calculada.
> 2. **Quando a Entrega foi liberada** — data e hora.
> 3. Da `VW_EVOL_ORCAMENTO_ALT` (linha do pedido, `TipoDoc = '17'`), os campos que a API ainda
>    não tem: `DataCriacaoPN`, `Representante`, `NumNF`, `DataNF`. `TipoMontagem` e
>    `Montador` a API **já entrega** (bloco `montagem`, lido da ORDR).
> 4. Um campo que diga se a **primeira nota fiscal** do pedido já foi emitida (sim/não),
>    derivado de `NumNF`.
>
> Reaproveitar o que existe: a leitura da view de orçamentos, o SELECT e o cache das rotas
> `/pedidos/*`. Não mudar os campos atuais — só acrescentar.

**Ambiguidades do pedido original** (fechadas no §6):

- "Através da `VW_EVOL_ORCAMENTO_ALT`" não vale para as datas de liberação: a view não tem
  nenhuma delas. Elas vêm do histórico do pedido (`ADOC`) e do recebimento do sinal (`ORCT`).
- "Primeira nota **de entrega**": não há nota de entrega no SAP (a `ODLN` está vazia para
  pedidos). Toda nota é de saída (`OINV`).
- `NumNF` da view é o **número interno** da nota no SAP, não o número impresso na DANFE.
- `Y/N` como texto destoa do resto da API, que usa `true`/`false`.

## 1. Fatos que travam o desenho (medidos em 25/09/2026, 281 pedidos da view)

| Fato | Número |
| --- | --- |
| `Financeiro` = `ORDR.U_INO_PedLib` (`S`/`N`) e `Data_Lib_Fin` = `ORDR.U_INO_DT_PED_LIB` | 281 de 281 |
| **Produção = Entrega**, sempre o mesmo valor | 281 de 281 |
| **Produção liberada ⇔ Financeiro liberado e (sem sinal ou a ÚLTIMA Solicitação de Adiantamento `ODPI` fechada)** | 281 de 281, zero exceção |
| ⚠️ `Data_Pagto` **não é pagamento**: é a data de EMISSÃO da Solicitação de Adiantamento (`ODPI.DocDate`) | 129 de 129 |
| `Data_Lib_Prod` = maior entre `Data_Lib_Fin` e `Data_Pagto`, **+ 3 dias corridos** | 270 de 270 com as duas datas |
| ⚠️ `Data_Lib_Fin` é **digitada** e quase nunca é o dia real: +1 dia em 182, +3 em 47, igual em só 7 | 252 com transição no histórico |
| `ADOC` guarda cada versão do pedido com `UpdateDate` + `UpdateTS` (HHMMSS) | 279 de 281 pedidos têm histórico |
| Pedidos re-bloqueados no Financeiro depois de liberados | 11 |
| Sinal reemitido: pedido pago volta a bloquear quando nasce `ODPI` nova (84326, 84420) | 2 hoje |
| Recebimento (`ORCT`) é registrado dias depois da data de lançamento (`CreateDate ≠ DocDate`) | 44 de 46 |
| `NumNF` da view = `OINV.DocNum` da **primeira** nota do pedido; nenhuma cancelada | 1.359 de 1.359 |
| Pedidos com mais de uma nota | 215 de 2.529 (até 42 notas) |
| `Representante` (VW_EVOL) ≠ `vendedor` (OSLP) | 1 de 280 (84278: "Administração" × "Neto") |
| Pedidos da view sem linha na `VW_EVOL` | 1 de 281 |
| Custo das 3 consultas novas no recorte inteiro | ~1,0 s (ADOC 0,32 + sinal 0,23 + VW_EVOL 0,45) |

Consequência: **a liberação real da Produção (e da Entrega) é o momento em que a última das
duas condições ficou verdadeira** — a última passagem do Financeiro de `N` para `S` (hora no
`ADOC`) ou o registro do recebimento que fechou a última `ODPI` (`ORCT.CreateDate` +
`CreateTS`). Não existe um campo "liberei a produção" no SAP.

## 2. Arquitetura

```
VW_STATUS_PEDIDO_DDP + ORDR (SELECT atual, NÃO muda)
            │
            ├─ consultas de enriquecimento (1 vez por recorte, mesmo cache de 120 s)
            │     ├─ ADOC   → última passagem U_INO_PedLib N→S → lib_fin_em
            │     │           (pedido que já nasce 'S' = hora da 1ª versão)
            │     ├─ DPI1→ODPI (última não cancelada) → RCT2 (InvType 203) → ORCT
            │     │           → sinal_pago_em = CreateDate + CreateTS do recebimento
            │     └─ VW_EVOL_ORCAMENTO_ALT (TipoDoc='17', NumDoc=DocNum) + OINV.Serial
            │            → data_criacao_pn, representante, nf_doc_num, nf_numero_fiscal, nf_data
            │
            └─ função pura `com_liberacao_e_nf()` (fora do núcleo diffável, como `com_alerta`)
                  → lib_producao_em = lib_entrega_em = max(lib_fin_em, sinal_pago_em)
                  → primeira_nf_emitida = nf_doc_num is not None
```

**Por que fora do núcleo:** `situacao_pedidos._pedido` e o SELECT são cópia conferida por teste
do serviço da web. Enriquecer depois, em consultas próprias, deixa a web intocada e o teste de
paridade verde.

## 3. Fases

### F0 — Sonda (só leitura) · ✅ concluída 25/09/2026
- Regra da Produção confirmada **com a `ODPI`**, não com `Data_Pagto` (281/281).
- Cobertura da hora real nos 274 pedidos com Produção liberada: **268 calculados**, 5 sem hora
  do Financeiro no histórico, 1 com sinal sem recebimento achado para a última `ODPI`.
  Esses 6 vêm `null`.
- A `data_lib_prod` calculada erra o dia real de −118 a +15 dias; o mais comum é +4 (94
  pedidos) e +3 (43).
- Momento do sinal: `ORCT.CreateDate`+`CreateTS` (quando o recebimento entrou no sistema, que
  é quando a Produção de fato destravou). `DocDate` é a data contábil, lançada retroativa.
- Nenhuma NF cancelada na `VW_EVOL`; `OINV.DocNum` não se repete (Serial sai por join simples).
- Scripts: `f0.py`, `f0b.py`, `f0c.py`, `f0d.py` no scratchpad da sessão (descartáveis).

### F1 — Consultas + regra pura · ✅ concluída 25/09/2026
Objetivo: a API sabe calcular os campos novos.
- `situacao_pedidos_hana.py`: as 3 consultas de enriquecimento, por `DocEntry`, dentro do
  mesmo cache; falha nelas não derruba a rota (campos vêm `null`, com log).
- `situacao_pedidos.py`: `com_liberacao_e_nf()` fora de `FUNCOES_NUCLEO`.
- Testes com stub: só Financeiro, nasce liberado, sinal pago depois, re-bloqueio, sinal
  reemitido (ODPI nova aberta), sem histórico, várias notas, sem nota.

### F2 — Contrato · ✅ concluída 25/09/2026
Objetivo: o grupo lê os campos novos na API e na documentação.
- Perfil `completo`: os 10 campos do §4. Campo sem valor = `null` ("não foi possível saber").
- `API_SITUACAO_PEDIDOS.md` §6.2 + armadilha §2.8 + a tool MCP `situacao_pedido`.
- Conferido no HANA de produção com o código novo: 84428 → 23/09 16:51:16 (sem sinal);
  84348 → Financeiro 08/09 15:06, sinal registrado **25/09 08:13** (a view dizia 12/09);
  84326/84420 → `null` (sinal reemitido em aberto); 84080 → NF 5729 / DANFE 32228.
- ⚠️ 84420 tem a 1ª NF emitida (25/09) com a Produção bloqueada — é o dado, não defeito.
- O teste da tool MCP é pulado no desktop (mcp 2.x instalado); roda na .11 (mcp<2).

### F3 — No ar · ✅ concluída 25/09/2026 15:36
- Ao vivo (`situacao_pedido`, cache 0 s): 84428 → 23/09 16:51:16; 84348 → Fin 08/09 15:06,
  sinal e Produção 25/09 08:13:35; 84080 → NF 5729 / DANFE 32228; 84420 → `null`
  (sinal em aberto) com NF 5788 / DANFE 32280. `/status` sap ok (25 ms).
- `deploy_update.bat` na .11.
- Conferência ao vivo na API: 84428 (sem sinal, 23/09 16:51), 84348 (sinal pago depois do
  Financeiro, 25/09 08:13), 84080 (8 notas, DANFE 32228).
- Aviso ao grupo com a lista de campos.

## 4. Campos novos (perfil `completo`)

| Campo | Tipo | Fonte |
| --- | --- | --- |
| `lib_fin_em` | datetime ISO \| null | ADOC, última passagem `U_INO_PedLib` N→S |
| `sinal_pago_em` | datetime ISO \| null | registro do recebimento que fechou a última `ODPI` |
| `lib_producao_em` | datetime ISO \| null | maior dos dois acima; `null` se Produção bloqueada |
| `lib_entrega_em` | datetime ISO \| null | igual a `lib_producao_em` (o SAP não separa) |
| `data_criacao_pn` | date ISO \| null | `VW_EVOL.DataCriacaoPN` |
| `representante` | str \| null | `VW_EVOL.Representante` |
| `nf_doc_num` | int \| null | `VW_EVOL.NumNF` (nº interno da 1ª nota) |
| `nf_numero_fiscal` | int \| null | `OINV.Serial` da mesma nota (número da DANFE) |
| `nf_data` | date ISO \| null | `VW_EVOL.DataNF` |
| `primeira_nf_emitida` | bool | `nf_doc_num` não nulo |

## 5. Riscos

- Pedido sem histórico no `ADOC` fica sem hora (5 hoje): vem `null`.
- Sinal quitado por outro caminho que não o recebimento da `ODPI` (1 hoje): vem `null`.
- A `VW_EVOL_ORCAMENTO_ALT` começa em 06/01/2025: pedido anterior fica sem os campos dela.

## 6. Decisões (fechadas em 25/09/2026 — todas as recomendações)

1. ✅ `lib_entrega_em` igual a `lib_producao_em`; a documentação diz que o SAP não separa.
2. ✅ "Primeira nota" = primeira nota de saída (`OINV`) não cancelada.
3. ✅ `true`/`false`, igual a `sinal`, `ddo` e `atrasado`.
4. ✅ Os dois números da nota: `nf_doc_num` (interno) e `nf_numero_fiscal` (DANFE).
5. ✅ `representante` separado de `vendedor` (divergem em 1 de 280).
6. ✅ Nada novo no perfil `resumo`.
