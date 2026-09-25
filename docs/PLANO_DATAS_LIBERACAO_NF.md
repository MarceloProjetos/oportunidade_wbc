# PLANO — Datas reais de liberação + dados de NF na API de Situação dos Pedidos

**Status (25/09/2026):** plano escrito, **nada codado**. Sondagem de leitura no HANA de
produção feita (§2). Pendem as decisões do §6 antes da F1.
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

**Ambiguidades do pedido original** (viraram decisões no §6):

- "Através da `VW_EVOL_ORCAMENTO_ALT`" não vale para as datas de liberação: a view não tem
  nenhuma delas. Elas vêm do histórico do pedido no SAP (`ADOC`).
- "Primeira nota **de entrega**": não há nota de entrega no SAP (a `ODLN` está vazia para
  pedidos). Toda nota é de saída (`OINV`). Se houver venda com simples faturamento + remessa,
  a primeira nota pode não ser a que acompanha a carga.
- `NumNF` da view é o **número interno** da nota no SAP, não o número impresso na DANFE.
- `Y/N` como texto destoa do resto da API, que usa `true`/`false`.

## 1. Fatos que travam o desenho (medidos em 25/09/2026, 281 pedidos da view)

| Fato | Número |
| --- | --- |
| `Financeiro` = `ORDR.U_INO_PedLib` (`S`/`N`) e `Data_Lib_Fin` = `ORDR.U_INO_DT_PED_LIB` | 281 de 281 |
| **Produção = Entrega**, sempre o mesmo valor | 281 de 281 |
| Produção liberada ⇔ Financeiro liberado **e** (sem sinal **ou** sinal pago) | 281 de 281, zero exceção |
| `Data_Lib_Prod` = maior entre `Data_Lib_Fin` e `Data_Pagto`, **+ 3 dias corridos** | 270 de 270 com as duas datas |
| `Data_Lib_Fin` é **digitada** e pode não ser o dia real: o 84428 foi liberado em **23/09 às 16:51** (ADOC) e o campo diz **24/09** | 1 caso visto; F0 mede o tamanho |
| `ADOC` guarda cada versão do pedido com `UpdateDate` + `UpdateTS` (HHMMSS) + usuário | presente no 84428 |
| Pagamento (`ORCT`) tem `DocTime` e `CreateTS` | colunas existem |
| `NumNF` da view = `OINV.DocNum` da **primeira** nota do pedido | 1.359 de 1.359 com nota |
| Pedidos com mais de uma nota | 215 de 2.529 (até 42 notas) |
| Pedidos sem nota em `VW_EVOL` (`TipoDoc=17`) | 151 |

Consequência: **a data real da liberação da Produção (e da Entrega) é o momento em que a
última das duas condições ficou verdadeira** — liberação do Financeiro (hora no ADOC) ou
pagamento do sinal (hora no ORCT). Não existe um campo "liberei a produção" no SAP.

## 2. Arquitetura

```
VW_STATUS_PEDIDO_DDP + ORDR (SELECT atual, NÃO muda)
            │
            ├─ consulta nova de enriquecimento (1 por recorte, mesmo cache de 120 s)
            │     ├─ ADOC   → 1ª versão com U_INO_PedLib='S' depois do último 'N' → lib_fin_em
            │     ├─ ORCT   → hora do pagamento do sinal → pagto_sinal_em   (fonte confirmada na F0)
            │     └─ VW_EVOL_ORCAMENTO_ALT (TipoDoc='17', NumDoc=DocNum)
            │            → data_criacao_pn, representante, nf_doc_num, nf_data
            │       + OINV.Serial da nota → nf_numero_fiscal
            │
            └─ função pura `com_liberacao_e_nf()` (fora do núcleo diffável, como `com_alerta`)
                  → lib_producao_em = lib_entrega_em = max(lib_fin_em, pagto_sinal_em)
                  → primeira_nf_emitida = nf_doc_num is not None
```

**Por que fora do núcleo:** `situacao_pedidos._pedido` e o SELECT são cópia conferida por teste
do serviço da web. Enriquecer depois, numa consulta própria, deixa a web intocada e o teste de
paridade verde.

## 3. Fases

### F0 — Sonda (só leitura) · minha
Objetivo: saber, com número, se as fontes batem antes de escrever código.
- De onde sai `Data_Pagto` (ORCT direto, ou ORCT pagando a ODPI do adiantamento) e se a hora
  do ORCT casa com ela nos 129 pedidos com sinal pago.
- Cobertura do ADOC nos 281: quantos têm a transição `N→S` registrada; quantos foram
  re-bloqueados depois de liberados.
- Quanto `U_INO_DT_PED_LIB` diverge do dia da transição no ADOC (o 84428 é um dia).
- Notas canceladas (`OINV.CANCELED`) e se a view já as exclui.
- Custo da consulta de enriquecimento no recorte inteiro (tempo medido).
- Saída: tabela de números neste plano; ajuste do desenho se algo não bater.

### F1 — Consulta + regra pura · minha
Objetivo: a API sabe calcular os campos novos.
- `situacao_pedidos_hana.py`: consulta de enriquecimento por `DocEntry`, dentro do mesmo cache.
- `situacao_pedidos.py`: `com_liberacao_e_nf()` fora de `FUNCOES_NUCLEO`.
- Testes com stub (a suíte não alcança produção): liberação só Financeiro, com sinal pago
  depois, re-bloqueio, sem ADOC, pedido com várias notas, sem nota.

### F2 — Contrato · minha
Objetivo: o grupo lê os campos novos na API e na documentação.
- Perfil `completo`: `lib_fin_em`, `pagto_sinal_em`, `lib_producao_em`, `lib_entrega_em`,
  `data_criacao_pn`, `representante`, `nf_doc_num`, `nf_numero_fiscal`, `nf_data`,
  `primeira_nf_emitida`.
- `API_SITUACAO_PEDIDOS.md` §6.2 + a tool MCP `situacao_pedido`.
- Campo sem valor = `null` ("não foi possível saber"), como o resto da API.

### F3 — No ar · Marcelo
- `deploy_update.bat` na .11.
- Conferência em 3 pedidos reais: 84428 (sem sinal), um com sinal pago depois do Financeiro,
  um com várias notas (84080).
- Aviso ao grupo com a lista de campos.

## 4. Campos novos (perfil `completo`)

| Campo | Tipo | Fonte |
| --- | --- | --- |
| `lib_fin_em` | datetime ISO \| null | ADOC, transição `U_INO_PedLib` N→S |
| `pagto_sinal_em` | datetime ISO \| null | ORCT (F0 confirma) |
| `lib_producao_em` | datetime ISO \| null | maior dos dois acima; `null` se Produção bloqueada |
| `lib_entrega_em` | datetime ISO \| null | igual a `lib_producao_em` (o SAP não separa) |
| `data_criacao_pn` | date ISO \| null | `VW_EVOL.DataCriacaoPN` |
| `representante` | str \| null | `VW_EVOL.Representante` |
| `nf_doc_num` | int \| null | `VW_EVOL.NumNF` (nº interno da 1ª nota) |
| `nf_numero_fiscal` | int \| null | `OINV.Serial` da mesma nota |
| `nf_data` | date ISO \| null | `VW_EVOL.DataNF` |
| `primeira_nf_emitida` | bool | `nf_doc_num` não nulo |

## 5. Riscos

- Pedido editado antes de o ADOC existir (pedido antigo) fica sem hora: vem `null`.
- Liberação manual do Financeiro sem sinal registrado no SAP fica com a data do Financeiro.
- A `VW_EVOL_ORCAMENTO_ALT` começa em 06/01/2025: pedido anterior fica sem os 4 campos dela.

## 6. Decisões

1. **Entrega tem data própria?** O SAP não separa Produção de Entrega (281/281 iguais).
   **Recomendado:** `lib_entrega_em` igual a `lib_producao_em`, e dizer isso na doc.
2. **"Primeira nota de entrega" = primeira nota de saída?** Não há nota de entrega no SAP.
   **Recomendado:** sim, a primeira `OINV` não cancelada. Confirmar se há simples faturamento.
3. **Formato do sim/não:** `true`/`false` ou `"Y"`/`"N"`. **Recomendado:** `true`/`false`,
   igual a `sinal`, `ddo` e `atrasado`.
4. **Número da nota:** interno (`NumNF`) ou o da DANFE (`Serial`). **Recomendado:** os dois.
5. **`representante` × `vendedor`:** a API já tem `vendedor` (OSLP). **Recomendado:**
   acrescentar `representante` separado e conferir na F0 quantos divergem.
6. **Perfil `resumo`:** entra só `primeira_nf_emitida` ou nada. **Recomendado:** nada; o
   `resumo` espelha as colunas da tela.
