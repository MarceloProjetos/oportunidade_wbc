# PLANO — Porta-paletes: quantidade lida do texto e total da linha por `LineTotal`

> **Status em 2026-09-15:** **nada implementado.** O relato que motivou o plano descrevia a
> quantidade lida do texto como "ficou" e a unidade CJ como "revertida", mas em `master` não
> há nada disso: [`models.py`](../wbcpython/infrastructure/wbc_sql/models.py) devolve
> quantidade 1 sempre que `ORCPRDQTD` é nula, [`linhas.py`](../wbcpython/domain/linhas.py)
> envia `Quantity` + `Price` e nunca enviou `MeasureUnit`. Não há teste, commit, CHANGELOG nem
> DECISOES sobre o assunto — nem no WBCPython standalone, nem em worktree, nem em outra
> sessão. O plano parte do zero. Suíte de referência: 1.744 testes verdes.

Relato do usuário: *"o item PORTA-PALETES sempre é criado como 01 unidade conjunto"*. O
pedido tem duas partes que sobreviveram à conversa com o negócio: **a quantidade sai do
`ORCTXT`** (só para porta-paletes) e **o total da linha vai como `LineTotal`**, para o SAP
derivar o preço e o total bater por construção. A unidade **não muda**: `MeasureUnit` não é
enviado, o SAP usa a do cadastro do item.

---

## Números

| | |
|---|---|
| Linhas do WBC com `ORCPRDQTD` nula | **20.997 de 20.997** (a coluna é nula na tabela inteira) |
| Linhas de porta-paletes na base | **5.968** (medido pelo relato; **remedir na F0** com a regra final) |
| Com quantidade legível no texto | **5.878 (98,5%)** — as 90 restantes são junções, colunas e avulsos |
| Linhas com `Quantity × Price ≠ ORCVAL` por arredondamento | **3 de 18** no ensaio relatado (±R$ 0,01) |
| Folga da conferência pós-atualização | **R$ 0,01** (`TOLERANCIA_DE_TOTAL`, `processar.py:112`) |
| Pontos que somam `Quantity × Price` hoje | **3** — `total_do_payload`, `total_das_linhas`, prévia do CLI |
| Documentos afetados | **2** — cotação e pedido dividem `domain/linhas.py` |

---

## Onde está agora

Toda linha vai ao SAP com `Quantity = 1` e `Price = ORCVAL`. A regra "quantidade 1 quando
`ORCPRDQTD` é nula" está em `ItemOrcamentoWbc.quantidade_para_documento`
([`models.py:53`](../wbcpython/infrastructure/wbc_sql/models.py)), e `preco_unitario` é
`ORCVAL ÷ quantidade`. O motor de linhas monta `Quantity`, `Price`, `WarehouseCode`,
`Weight1` e os UDFs ([`linhas.py:172`](../wbcpython/domain/linhas.py)).

Três lugares somam `Quantity × Price` e precisam mudar junto com o campo enviado:

| Ponto | Arquivo | Papel |
|---|---|---|
| `total_do_payload` | [`application/processar.py:123`](../wbcpython/application/processar.py) | Recusa documento sem valor (SAP devolve `-5002`) e é o "esperado" da conferência |
| `total_das_linhas` | [`infrastructure/service_layer/documentos.py:318`](../wbcpython/infrastructure/service_layer/documentos.py) | Relê a cotação depois do `PATCH`; divergindo além de 1 centavo, cancela e recria |
| Prévia do CLI | [`cli.py:614`](../wbcpython/cli.py) | Mostra `ItemCode ×qtd = total` no `pendentes` |

`U_INO_PRECO` do OrcDetalhe ([`mapeamento.py:198`](../wbcpython/domain/mapeamento.py)) usa o
`preco_unitario` da **árvore de produtos**, não da linha do orçamento. **Não é afetado.**

---

## §1 Fatos que travam o desenho

- **`ORCVAL` é o total da linha.** Com 8 módulos, o unitário é `ORCVAL ÷ 8`. O total da
  linha **não pode mudar** — foi uma semana consertando divergência entre documento e
  orçamento (cotações `00125616` e `00125577`, DECISOES.md).
- **`Price` com 4 casas** no SAP: `272 × 2600,0071 = 707.201,93` contra `707.201,92` do WBC.
  Dividir e mandar unitário cria diferença de 1 centavo que não existia com quantidade 1.
- **`Price` é o líquido que força o valor** (`SpecPrice = 'N'`), proteção contra desconto
  de parceiro cadastrado (comentário em `linhas.py:175–186`). Trocar por `LineTotal` só
  vale se o SAP continuar gravando o total forçado — **é o que a F3 confere**.
- **`Weight1` é peso de uma unidade** e já é dividido por `quantidade_para_documento`
  (`linhas.py:210`). Com quantidade real, o peso unitário passa a ser `peso ÷ qtd` de fato.
  Efeito esperado, não regressão.
- **Sem flag no `.env`.** Regra de produção liga por constante, nunca por `*_ENABLED`.
- **Nunca escrever em `SBOALTAMIRAPROD`** fora do ciclo normal; homologação é
  `safety.assert_write_allowed`. Ciclo de homologação, pull e restart na .11 são do Marcelo.

---

## §2 A regra da quantidade

Só se aplica quando, **no mesmo `ORCTXT`**, a palavra "Módulo(s)" vem **depois** de
"porta-paletes" em qualquer grafia: `PORTA-PALETES`, `Porta palete`, `porta paletes`,
`PORTA PALETE`, com hífen, espaço ou nada, singular ou plural, com ou sem acento, qualquer
caixa. Fora disso a linha segue como hoje (quantidade 1, sem aviso).

Quantidade = **o inteiro imediatamente anterior à primeira ocorrência de "Módulo(s)"**
depois de porta-paletes. Comparação sem acento e sem caixa (`MODULO`, `Módulos`, `módulo`).

```
PORTA-PALETES ÁREA 1 10 Módulos...        → 10   (o 1 é da área)
PORTA-PALETES - OPÇÃO 1 14 Módulos...     → 14   (o 1 é da opção)
Porta palete 16 modulos duplos            → 16
PORTA-PALETES junção dupla                → 1, com aviso (sem "Módulo")
ESTANTE 12 Módulos                        → 1, sem aviso (não é porta-paletes)
```

Precedência: `ORCPRDQTD` positiva > número do texto > 1. Número zero ou ausente em linha de
porta-paletes → 1 **com aviso** em `ResultadoLinhas.avisos`, no mesmo canal do fallback de
grupo.

Padrão (sobre o texto normalizado, `unicodedata.NFKD` sem marcas, minúsculas):

```
porta[\s\-]*paletes?.*?\b(\d+)\s*modulos?\b
```

O `.*?` preguiçoso é o que faz "ÁREA 1 10 Módulos" devolver 10 e não 1: o `\d+` só casa
quando o que vem depois é "modulo".

---

## §3 Fases

### F0 — Medir na base com a regra final  *(minha · só leitura)*

**Meta:** saber quantas linhas a regra alcança **antes** de mudar o que vai ao SAP.

- Script em `maintenance/` que lê `INTEGRACAO_ORCIMP` pelo repositório existente (passa por
  `assert_read_only_sql`) e conta: linhas de porta-paletes, com número lido, sem número,
  e a distribuição das quantidades.
- Lista as sem número para o negócio confirmar que são junções/colunas/avulsos.
- Confere os dois textos-armadilha (ÁREA 1, OPÇÃO 1) contra o padrão.
- Números vão para este plano e para o DECISOES.md. Se divergirem muito de 5.968 / 5.878,
  o padrão volta à mesa antes da F1.

### F1 — Quantidade lida do texto  *(minha)*

**Meta:** porta-paletes com "N Módulos" nasce no SAP com `Quantity = N` e o mesmo total.

- Função pura `quantidade_no_texto(texto) -> int | None` em `domain/linhas.py`, com o
  padrão acima. `ItemOrcamentoWbc.quantidade_para_documento` passa a consultar o texto
  quando `ORCPRDQTD` não vale.
- `linhas_do_documento` emite aviso quando o texto é de porta-paletes e não trouxe número.
- Testes em `tests/wbc/domain/test_linhas.py` (`TestQuantidade`): os cinco exemplos de §2,
  precedência do `ORCPRDQTD`, aviso só para porta-paletes sem número, e
  **`Quantity × preco_unitario == ORCVAL` ao centavo** com 8, 134 e 272 módulos.
- `Weight1` continua `peso ÷ qtd`: teste afirmando que a quantidade lida entra na divisão.
- Docstrings de `models.py` e `linhas.py` que dizem "quantidade é sempre 1" mudam.

### F2 — `LineTotal` no lugar de `Price`, e os três pontos  *(minha)*

**Meta:** total da linha bate por construção; a conferência pós-`PATCH` compara a mesma
grandeza dos dois lados.

- `linhas.py`: a linha leva `"LineTotal": float(item.valor)` e **deixa de levar `Price`**
  (decisão 3). O comentário sobre `Price`/`UnitPrice` vira registro histórico com o motivo
  da troca.
- `total_do_payload` soma `LineTotal` das linhas (em `Decimal`, como hoje).
- `total_das_linhas` lê `LineTotal` de cada `DocumentLines` devolvida pelo SAP, em vez de
  multiplicar `Quantity × Price`. A folga de 1 centavo e o cancelar-e-recriar ficam iguais.
- Prévia do CLI imprime `ItemCode ×qtd = LineTotal`.
- Testes: payload sem `Price` e com `LineTotal = ORCVAL`; `total_do_payload` igual ao
  ORCVAL somado; `total_das_linhas` com documento dublado; teste-guarda que falha se
  `MeasureUnit` aparecer na linha.
- CHANGELOG + DECISOES.md (regra, números da F0, motivo de `LineTotal`).
- Commit e push em `master`, `git add` nominal.

### F3 — Ensaio em homologação  *(do Marcelo · escrita em homologação)*

**Meta:** prova de que o SAP respeita `LineTotal` no `POST` **e** no `PATCH`, e de que o
total forçado continua forçado.

- Ciclo completo em homologação (referência do relato: 1.540 avaliados, 42 com ação, 0 erro).
- Critério de aceite: **zero linhas com diferença de centavo** entre `LineTotal` no SAP e
  `ORCVAL`; `Price` derivado = `ORCVAL ÷ qtd`; unidade da linha = a do cadastro (UN no
  `I000003`, a do próprio item nas 6 linhas de GRPCOD 16).
- Conferir uma cotação **atualizada** (caminho `PATCH` + `ReplaceCollectionsOnPatch`): é o
  caminho que já falhou em silêncio uma vez.
- **Se o SL ignorar `LineTotal` no PATCH**, a alternativa é mandar os
  dois campos (`Price` calculado + `LineTotal`) e medir de novo — decisão 3 reabre.

### F4 — Produção  *(do Marcelo)*

**Meta:** o worker da .11 cria porta-paletes com a quantidade certa.

- `git pull` + restart de `OrcaView-WBC-Worker` e `OrcaView-WBC-Painel` na .11 (já há um
  pull pendente da janela sob demanda; sobe junto).
- Primeiro pedido real de porta-paletes conferido no SAP: quantidade, unitário, total.

---

## §4 Decisões

1. **Escopo da leitura — ✅ decidido (Marcelo, 15/09).** Só quando "Módulo(s)" vem precedido
   de "porta-paletes" em qualquer variação, no mesmo texto. Estantes e mezaninos com
   "N Módulos" seguem em 1.
2. **`LineTotal` no lugar de `Price` — ✅ decidido (Marcelo).** O SAP deriva o preço; o total
   bate por construção.
3. **Enviar só `LineTotal`, sem `Price`.** *Recomendado.* Mandar os dois deixa o SL escolher
   a ordem em que aplica os campos, e a versão do SL da .11 já mostrou comportamento
   irregular no `PATCH`. Um campo só tem uma verdade. Reabre se a F3 mostrar que o SL
   ignora `LineTotal` sozinho.
4. **Precedência `ORCPRDQTD` > texto > 1.** *Recomendado.* A coluna hoje é nula, mas a regra
   antiga já dizia "número positivo é respeitado"; o texto entra como segundo caminho, não
   como substituto.
5. **Os três pontos mudam junto — ✅ decidido (Marcelo).** `total_do_payload`,
   `total_das_linhas` e a prévia do CLI passam a falar `LineTotal`.
6. **`MeasureUnit` não vai — ✅ decidido (negócio).** Sem o campo, a linha usa a unidade do
   cadastro do item. Teste-guarda impede a volta.

---

Plano no repositório: `docs/PLANO_PORTA_PALETES_QUANTIDADE.md` · ServidorIntegracaoSAP · 2026-09-15
