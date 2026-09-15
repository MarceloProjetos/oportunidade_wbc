# PLANO — Porta-paletes: quantidade lida do texto e total da linha por `LineTotal`

> **Status em 2026-09-15 (tarde):** **F0, F1 e F2 codadas e testadas — nada rodou no SAP ainda.**
> A regra foi medida na base inteira (21.447 linhas) antes de ser escrita: 6.442 linhas de
> porta-paletes, **6.299 lidas (97,8%)**, 143 sem número. `LineTotal` substitui `Price`, os
> três pontos que somavam `Quantity × Price` mudaram juntos, e o painel colore as linhas
> `[porta-paletes]` e `[line-total]` na aba Log. Suíte: **1.800 passando, 12 skips**.
> **Pendem F3** (ensaio em homologação, do Marcelo: zero centavos de diferença, conferir o
> caminho `PATCH`) **e F4** (pull + restart na .11).
>
> O relato que motivou o plano descrevia a quantidade lida como "ficou" e a unidade CJ como
> "revertida", mas em `master` não havia nada disso: o plano partiu do zero.

Relato do usuário: *"o item PORTA-PALETES sempre é criado como 01 unidade conjunto"*. O
pedido tem duas partes que sobreviveram à conversa com o negócio: **a quantidade sai do
`ORCTXT`** (só para porta-paletes) e **o total da linha vai como `LineTotal`**, para o SAP
derivar o preço e o total bater por construção. A unidade **não muda**: `MeasureUnit` não é
enviado, o SAP usa a do cadastro do item.

---

## Números

| | |
|---|---|
| Linhas do WBC com `ORCPRDQTD` nula | **21.447 de 21.447** (medido em 15/09; o relato dizia 20.997) |
| Linhas de porta-paletes na base | **6.442** com a regra final (F0, `maintenance/medir_porta_paletes.py`) |
| Com quantidade legível no texto | **6.299 (97,8%)** — as 143 restantes são junções, colunas, protetores e avulsos |
| Leituras erradas conhecidas | **4** ("01 conjunto … para 186 módulos" lê 186); o total não depende delas |
| Linhas com `Quantity × Price ≠ ORCVAL` por arredondamento | **3 de 18** no ensaio relatado (±R$ 0,01) |
| Folga da conferência pós-atualização | **R$ 0,01** (`TOLERANCIA_DE_TOTAL`, `processar.py:112`) |
| Pontos que somam `Quantity × Price` hoje | **3** — `total_do_payload`, `total_das_linhas`, prévia do CLI |
| Documentos afetados | **2** — cotação e pedido dividem `domain/linhas.py` |

---

## Onde está agora

Em `master` (não deployado): `ItemOrcamentoWbc.quantidade_para_documento`
([`models.py`](../wbcpython/infrastructure/wbc_sql/models.py)) tem três degraus — `ORCPRDQTD`
positiva, número do texto, 1 — e a leitura do texto vive em `quantidade_no_texto` /
`eh_porta_paletes` ([`linhas.py`](../wbcpython/domain/linhas.py)). A linha vai com
`Quantity`, `LineTotal`, `WarehouseCode`, `Weight1` e os UDFs; `Price` e `MeasureUnit` não vão
(testes-guarda). Na .11 ainda roda o código antigo: quantidade 1 e `Price = ORCVAL`.

Os três lugares que somavam `Quantity × Price` mudaram juntos:

| Ponto | Arquivo | Depois |
|---|---|---|
| `total_do_payload` | [`application/processar.py`](../wbcpython/application/processar.py) | Soma `LineTotal` do payload; segue recusando documento sem valor |
| `total_das_linhas` | [`infrastructure/service_layer/documentos.py`](../wbcpython/infrastructure/service_layer/documentos.py) | Lê o `LineTotal` que o SAP devolve; folga de 1 centavo e cancelar-e-recriar iguais |
| Prévia do CLI | [`cli.py`](../wbcpython/cli.py) | Mostra `ItemCode ×qtd = LineTotal` no `pendentes` |

No log, a quantidade lida sai como `[porta-paletes] 00125442: Item 1: 272 módulos lidos do texto →
Quantity 272, LineTotal R$ 707.201,92 (unitário R$ 2.600,0071)` (INFO); a porta-paletes sem
"N Módulos" sai como WARNING com o mesmo tema; a conferência pós-`PATCH` sai como `[line-total]`.
A aba Log do painel pinta essas linhas de azul-aço e mostra o tema como selo clicável que filtra.

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
caixa. Fora disso a linha segue como hoje (quantidade 1, sem nota).

Quantidade = **o inteiro imediatamente anterior à primeira ocorrência de "Módulo(s)"**
depois de porta-paletes. Comparação sem acento e sem caixa (`MODULO`, `Módulos`, `módulo`).

```
PORTA-PALETES ÁREA 1 10 Módulos...            → 10   (o 1 é da área)
PORTA-PALETES - OPÇÃO 1 14 Módulos...         → 14   (o 1 é da opção)
ÁREA: SECA  PORTA-PALETES 226 Módulos...      → 226  (rótulo antes do nome vale)
Porta palete 16 modulos duplos                → 16
PORTA-PALETES 36 Sapatas para colunas         → 1, com nota WARNING (sem "Módulo")
ESTANTE 12 Módulos                            → 1, calada (não é porta-paletes)
STOPS TRASEIROS PARA PORTA-PALETES 24 Stops   → 1, calada (acessório: "para porta-paletes")
PLANOS METÁLICOS 60 Planos ... porta-paletes  → 1, calada (descrição já começou antes do nome)
```

O que define "linha de porta-paletes" foi decidido pela medição (F0), não pela regex mais
simples: com o nome em qualquer lugar do texto entram 3.436 acessórios e a leitura erra; com o
nome só no início ficam de fora 355 linhas legítimas com rótulo ("ÁREA: X", "ITEM 02 -").
A regra adotada aceita o rótulo e recusa "de/para/tipo porta-paletes" e um "N palavra" antes
do nome.

Precedência: `ORCPRDQTD` positiva > número do texto > 1. Número zero ou ausente em linha de
porta-paletes → 1 **com nota de atenção** (WARNING no log, tema `[porta-paletes]`). Não vira
erro do orçamento: as 143 linhas assim são junções, colunas, protetores e avulsos.

Limite conhecido: quatro linhas `PORTA-PALETES - MONTANTES COMPLEMENTARES 01 conjunto composto
por 192 montantes … para 186 módulos` leem 186. O total da linha não depende disso.

---

## §3 Fases

### F0 — Medir na base com a regra final  *(minha · só leitura)* — ✅ 15/09

**Meta:** saber quantas linhas a regra alcança **antes** de mudar o que vai ao SAP.

- `maintenance/medir_porta_paletes.py` lê `INTEGRACAO_ORCIMP` (pymssql na .11, pyodbc na
  estação; os dois passam por `assert_read_only_sql`) e usa a **função de produção** para
  contar — medir com uma regex e implementar outra era o risco.
- Três variantes medidas: nome em qualquer lugar (9.476 linhas, 66,8% lidas — pega acessório),
  nome no início (6.040, 98,4% — perde 355 com rótulo), **rótulo aceito + acessório recusado
  (6.442, 97,8%, adotada)**.
- **O que mordeu:** o relato dizia 5.968 / 98,5%; a base cresceu para 21.447 linhas e a
  variante "no início" reproduz o relato (6.040 / 98,4%). A diferença não era a regra, era o
  escopo. As 143 sem número e as 4 leituras erradas estão listadas no DECISOES.md.

### F1 — Quantidade lida do texto  *(minha)* — ✅ 15/09

**Meta:** porta-paletes com "N Módulos" nasce no SAP com `Quantity = N` e o mesmo total.

- `quantidade_no_texto` e `eh_porta_paletes` em `domain/linhas.py`;
  `ItemOrcamentoWbc.quantidade_para_documento` consulta o texto quando `ORCPRDQTD` não vale
  (import local, para não fechar o ciclo `mapeamento → models → linhas`).
- `ResultadoLinhas.notas` (`Nota(texto, atencao)`): leitura = INFO, sem número = WARNING; nada
  vai ao acompanhamento. `avisos` continua só para grupo fora do de-para.
- Testes em `tests/wbc/domain/test_linhas.py`: `TestQuantidadeNoTexto` (13 textos reais que
  leem, 5 que ficam em `None`, 6 que não são porta-paletes), precedência do `ORCPRDQTD`,
  **`LineTotal == ORCVAL` ao centavo** com 8, 134 e 272 módulos, `Weight1 = peso ÷ qtd`.
- O texto padrão do `conftest` dos testes de domínio virou `PORTA-PALETES` (sem "N Módulos"):
  é a linha real do 00125535 e mantém quantidade 1 nos testes que não são sobre a leitura.

### F2 — `LineTotal` no lugar de `Price`, e os três pontos  *(minha)* — ✅ 15/09

**Meta:** total da linha bate por construção; a conferência pós-`PATCH` compara a mesma
grandeza dos dois lados.

- `linhas.py`: a linha leva `"LineTotal": float(item.valor)` e **não leva `Price`** (decisão
  3). O comentário sobre `Price`/`UnitPrice` virou registro histórico.
- `total_do_payload` soma `LineTotal`; `total_das_linhas` lê `LineTotal` do SAP; prévia do CLI
  imprime `ItemCode ×qtd = LineTotal`. Folga de 1 centavo e cancelar-e-recriar iguais.
- Testes-guarda: sem `Price`/`UnitPrice`, sem `MeasureUnit`/`UoMEntry`;
  `TestTotalDasLinhas` com documento dublado do SL.
- **Tema no log e no painel** (pedido do Marcelo, imagem da aba Log): `[porta-paletes]` e
  `[line-total]` abrem a mensagem; `logs.LinhaDeLog.tema` reconhece; a aba Log pinta a linha de
  azul-aço, mantém âmbar no WARNING e vermelho no ERROR, e o tema vira selo clicável que
  preenche o filtro. Prévia conferida nos dois temas com o CSS real. `painel.css?v=20260915`.
- CHANGELOG + DECISOES.md; suíte 1.800 / 12 skips; commit e push em `master`.

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
3. **Enviar só `LineTotal`, sem `Price` — ✅ decidido (Marcelo, "segue as recomendações").**
   Mandar os dois deixa o SL escolher a ordem em que aplica os campos, e a versão da .11 já
   mostrou comportamento irregular no `PATCH`. Um campo só tem uma verdade. Reabre se a F3
   mostrar que o SL ignora `LineTotal` sozinho.
4. **Precedência `ORCPRDQTD` > texto > 1 — ✅ decidido (Marcelo).** A coluna hoje é nula, mas
   a regra antiga já dizia "número positivo é respeitado"; o texto entra como segundo caminho.
7. **Rótulo antes do nome conta; acessório não.** *Decidido pela medição (F0).* "ÁREA: SECA
   PORTA-PALETES 226 Módulos" é porta-paletes; "STOPS PARA PORTA-PALETES 24 Stops" não. Sem
   isso, 355 linhas legítimas ficariam em 1 ou 3.436 acessórios seriam lidos errado.
8. **Sem número = nota WARNING no log, não erro do orçamento.** *Decidido.* As 143 linhas assim
   são junções, colunas e avulsos; marcar ERRO no acompanhamento poluiria o painel com o que
   está certo.
5. **Os três pontos mudam junto — ✅ decidido (Marcelo).** `total_do_payload`,
   `total_das_linhas` e a prévia do CLI passam a falar `LineTotal`.
6. **`MeasureUnit` não vai — ✅ decidido (negócio).** Sem o campo, a linha usa a unidade do
   cadastro do item. Teste-guarda impede a volta.

---

Plano no repositório: `docs/PLANO_PORTA_PALETES_QUANTIDADE.md` · ServidorIntegracaoSAP · 2026-09-15
