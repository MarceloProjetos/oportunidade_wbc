# Decisões tomadas

> **Documento histórico do WBCPython standalone (até 08/09/2026).** O projeto virou o pacote
> `wbcpython/` do ServidorIntegracaoSAP: onde se lê `uv run wbcpython …`, hoje é
> `python -m wbcpython …` na raiz do repositório; `uv sync` é `pip install -r requirements.txt`;
> `python/WBCPython` é esta raiz; `ai_spec/` está explicado em `ai_spec/00_index.md`.
> Como rodar hoje: [`README.md`](README.md) desta pasta.

Cada linha aqui foi uma escolha, não um acaso. O objetivo é que ninguém precise
redescutir — nem "corrigir" — algo que já foi decidido com motivo.

Decisões **em aberto** não estão aqui; estão em `RETOMADA.md`.

---

## De negócio

### Quantidade da linha: 1 quando `ORCPRDQTD` for vazia, nula, inconsistente ou zero

Decidido pelo usuário. Hoje é o caminho único — a coluna é nula em 100% das
20.997 linhas —, mas a regra está escrita para o dia em que o WBC passar a
preenchê-la, e não como um `1` fixo disfarçado. Negativo conta como
inconsistente.

Como `ORCVAL` é o **total** da linha, o preço enviado ao SAP é o unitário
(`ORCVAL / quantidade`), o que preserva o total qualquer que seja a quantidade.

Onde: `ItemOrcamentoWbc.quantidade_para_documento` e `.preco_unitario`.

### Grupo fora do de-para: usar Porta-Paletes (grupo `2`), **com aviso**

Decidido pelo usuário. O aviso vai para o log **e** para o acompanhamento — um
documento criado com item que ninguém escolheu conscientemente precisa ser
visível no dashboard, não só no log do servidor.

O legado tratava esse caso de duas formas incompatíveis: caía no grupo 2 em
silêncio (`ServiceProcess.cs:1223`, `:1256`) ou **descartava a linha inteira**
(`:356`, `:626`). A segunda é a pior: o documento chegava ao SAP com valor
menor que o do WBC e ninguém ficava sabendo.

Nos dados atuais o fallback nunca dispara: os 11 grupos do de-para cobrem 100%
das linhas.

Onde: `domain/linhas.py`.

### Depósito da linha: sempre `08`

Decidido pelo usuário. O legado aplicava `WarehouseCode = "08"` quando o
`ItemCode` começava com "I" — e como todos começam, era sempre.

### Peso da linha: manter como está (1 kg)

Decidido pelo usuário depois de investigação. O `Weight1 = 1.0` vem do
**cadastro do item no SAP** (`SalesUnitWeight` dos itens `I00000x`), não do
payload, e as cotações do legado trazem exatamente o mesmo valor.

O WBC não tem peso por linha nas tabelas integradas. Se um dia o peso precisar
estar correto, o lugar de corrigir é o cadastro do item no SAP — mexer no
código só trocaria um número errado por outro.

Onde: documentado no cabeçalho de `domain/linhas.py`.

---

## De arquitetura e segurança

### Nunca escrever no SQL Server do WBC

Diretiva do usuário, sem exceção de ambiente. Sustentada por três camadas: a
interface do repositório não oferece método de escrita; toda consulta passa por
`assert_read_only_sql` no ponto único de execução; e recomenda-se um usuário de
banco `db_datareader` no servidor.

`assert_read_only_sql` **não tem chave de desligar** — a assinatura não aceita
um parâmetro que a contorne. É de propósito.

### Nunca escrever em `SBOALTAMIRAPROD`

Diretiva do usuário. A trava **falha fechada**: se `production_company_db`
estiver vazio ou o alvo for indefinido, ela bloqueia em vez de liberar. Um bug
anterior fazia o contrário — configuração vazia desativava a trava *e* o
diagnóstico reportava "ATIVA".

### Ler o HANA do schema de produção

`VW_CLIENTE_MUNICIPIO_ALTA` e `VW_EVOL_OPORTUNIDADE_ALT` só existem em
`SBOALTAMIRAPROD` (verificado: em homologação não há nem privilégio de leitura).
Ler produção é permitido pela regra do projeto; escrever não é.

### Estado de processamento pertence ao banco de tracking

Nunca ao WBC. Se em algum momento parecer necessário gravar algo no WBC, isso é
sinal de que o estado pertence ao tracking — não de que falta um método no
repositório.

### Não mexer no build C# atual

Diretiva do usuário. O legado foi restaurado ao original; as correções
propostas ficaram em `correcoes_propostas_csharp/`, não aplicadas.

---

## Divergências deliberadas em relação ao legado

Cada uma corrige um defeito real. Estão detalhadas em `DEFEITOS_LEGADO.md`.

| Divergência | Motivo |
|---|---|
| `SEM_REVISAO = -1000` | Na ordenação do legado, revisão vazia caía *entre* dígitos e letras e podia disparar cancelamento e recriação de documento sobre dado ausente |
| Revisão normalizada para maiúscula na leitura | A fórmula ASCII-64 pressupõe maiúscula; um `'b'` faria revisão antiga parecer mais nova |
| Falha isolada por orçamento | No legado, um registro sem número encerrava o processo e deixava todo o resto sem processar, em silêncio |
| Trava de execução única | O legado permitia duas execuções sobrepostas sobre os mesmos orçamentos |
| Baixa da marca de troca de PN | Sem ela, a oportunidade continuaria marcada e o pedido seria recriado a cada execução |
| Deduplicação de itens | O `LEFT JOIN` com fan-out dobrava a quantidade que alimenta cotação e pedido |
| Sem filtro fixo de orçamento | O legado tinha `and U_ORCNUM_WBC = '00121819'` gravado na consulta |
| Janela de 6 meses explícita | O legado misturava -6 meses no ano e -9 no mês, dando um corte imprevisível |
| Espelhamento de status explícito | No legado vinha embutido em `AddCotacaoOportunidade`; traduzir a máquina de estados sem esse efeito colateral deixava a oportunidade em `'0'` e recriava a cotação a cada ciclo |
| Comando `pendentes` | Não existia equivalente: não havia como saber o que um ciclo faria antes de deixá-lo fazer |

### Tautologias do legado: implementado o comportamento **efetivo**

O legado tem condições com `||` que são sempre verdadeiras. Não foi assumido
que a intenção era `&&`: implementou-se o que o código **faz hoje**, e a
pergunta foi registrada para o negócio. Trocar por `&&` mudaria o comportamento
em produção com base num palpite.

---

## Cotação e pedido são procedimentos separados

Não é organização de código — é contenção de defeito.

Os dois documentos passavam pelo mesmo caminho com um parâmetro de tipo
(`_payload_documento(..., tipo=...)`, `montar_linhas_documento(..., pedido=...)`).
Enquanto foi assim, campo de um vazou para o outro **duas vezes**: primeiro o
pedido nasceu com os campos da cotação; depois a cotação saiu com
`U_INO_Composicao`, que produção nunca grava em cotação e que **aparece na view
de impressão do cliente**.

Os dois vazamentos têm a mesma causa: um parâmetro booleano no meio de uma
função longa não obriga ninguém a pensar em qual documento está mexendo. Um
módulo por documento obriga.

### Como ficou

| Arquivo | Responsabilidade |
|---|---|
| `domain/cotacao.py` | criação e manutenção da cotação — cabeçalho, linhas, payload |
| `domain/pedido.py` | idem para o pedido de venda |
| `domain/venda_comum.py` | **só** o que é igual nos dois: formatação, limites de campo e os campos comuns (parceiro, filial, vínculo, vendedor, contato) |
| `domain/linhas.py` | motor comum: item pelo grupo, quantidade, preço, depósito. Os UDFs específicos chegam por `udfs_da_linha` |

Na aplicação, `_executar_documento` virou um encaminhador de três linhas:
`_executar_cotacao` e `_executar_pedido` são caminhos distintos, cada um com o
seu `_payload_*`.

### A regra para o futuro

Um campo só entra em `venda_comum` se valer para os dois documentos **hoje**,
não se "quase" valer. Na dúvida, duplica-se — duas linhas iguais em dois
arquivos custam menos que um campo errado numa cotação de cliente.

Os testes seguem a mesma divisão (`test_cotacao.py`, `test_pedido.py`), e vários
deles afirmam a **ausência** de campos do outro documento. É o que transforma a
separação em algo que o CI protege, e não apenas numa convenção.

---

## Encerrar a oportunidade passa a cancelar a cotação

**Divergência deliberada do legado.** É a primeira mudança de comportamento
assumida deste porte, e por isso fica registrada em detalhe.

### O defeito relatado

Uma oportunidade cancelada no WBC deixava a cotação aberta no SAP. Caso
concreto: orçamento `00124744`, oportunidade `OpprId 14030`, `Status='L'`,
`U_INO_StatusWBC='99'` — e a cotação `DocNum 68438` com `CANCELED='N'` e
`DocStatus='O'`.

### O que o legado fazia

`cancelaFechaOportunidade` (`ServiceProcess.cs:91-122`) grava o
`U_INO_StatusWBC` e põe `oport.Status = sos_Missed`. Só. Nunca toca em
documento nenhum.

Não é um caso isolado, é o comportamento padrão: em produção, de **714
oportunidades encerradas, 686 (96%) continuam com a cotação aberta**. As 28
canceladas foram trabalho manual.

### O que passa a acontecer

No SitCode 70/90/99, havendo cotação e **não havendo pedido**, a integração
cancela a cotação antes de encerrar a oportunidade.

Três escolhas dentro disso:

* **Só a cotação.** O pedido nunca é cancelado automaticamente: pode já ter
  movimentado estoque ou faturamento, e desfazer isso é irreversível e caro.
* **Havendo pedido, nem a cotação.** Nesse caso ela foi convertida, e o SAP
  recusaria o cancelamento de qualquer forma.
* **Cancelar antes de encerrar.** Se o cancelamento falhar, a oportunidade não
  fica fechada com um documento aberto pendurado — e o ciclo seguinte tenta de
  novo. A ordem inversa criaria um estado que nenhuma execução futura corrigiria.

### Falha do SAP é aviso, não erro de ciclo

A cotação pode ter sido fechada, convertida ou cancelada por alguém entre a
leitura no HANA e a escrita no Service Layer. Nesse caso registra-se o evento e
o encerramento segue — que é exatamente o efeito que o legado já produzia. O
pior resultado possível da mudança é o comportamento antigo.

### Duas consequências que só apareceram na primeira execução real

**1. `sos_Lost` não existe.** O `encerrar()` mandava `Status: "sos_Lost"` — nome
plausível, inexistente. O `BoSoOsStatus` do Service Layer aceita apenas
`sos_Open`, `sos_Missed` e `sos_Sold`; o SAP recusava com
`HTTP 400 | SAP -1013`. O legado sempre usou `sos_Missed`.

O defeito atravessou o porte inteiro porque **o teste afirmava o valor errado**
(`assert corpo["Status"] == "sos_Lost"`): fixava o que o código fazia, não o que
o SAP aceita. O teste que entrou no lugar compara com a lista de nomes válidos
que o próprio SAP devolve na mensagem de erro. Nenhuma execução conseguiu
encerrar oportunidade antes disto — é o que explica as 41 de homologação com
cotação aberta e `U_INO_StatusWBC` já em '99'.

**2. Cancelar a cotação torna alcançável um ramo que era morto.** O ramo "não há
cotação → cria" respondia a qualquer SitCode, inclusive 70/90/99. No legado isso
nunca acontecia: sem cancelamento, um orçamento encerrado sempre tinha cotação.
Passando a cancelar, `tem_cotacao` vira falso na execução seguinte — e a
integração recriaria a cotação de um orçamento perdido para cancelá-la de novo,
ciclo após ciclo.

O encerramento passou a ser avaliado **antes** do ramo de criação. E, para não
repetir o PATCH eternamente, quando não há cotação a cancelar e a oportunidade
já está fechada, a regra é `encerramento_ja_aplicado` e nada é escrito.

**3. "Já encerrada" só se lê no `Status` da OOPR.** A primeira versão dessa
guarda usava `U_INO_StatusWBC == SitCode` como prova de encerramento. Está
errado, e o erro é silencioso na direção pior: `atualizar_status` espelha esse
campo a cada ciclo, muito antes de qualquer encerramento. Combinado com o
defeito do `sos_Lost`, **toda** oportunidade perdida tinha o SitCode espelhado e
seguia aberta — a guarda calaria exatamente os casos que precisam ser fechados.
Foi o que aconteceu na segunda execução do 00124619: um "sucesso" que não
escreveu nada.

A prova é `OOPR."Status"` ('O' aberta, 'L' perdida, 'W' vendida), agora trazido
pela consulta do HANA e exposto como `EstadoIntegracao.oportunidade_encerrada`.

**4. A leitura do HANA passou a ser por nome de coluna.** Acrescentar `Status`
ao `SELECT` deslocou todos os índices posicionais seguintes do tradutor:
`U_INO_StatusWBC` passou a receber o `Status`, `U_INO_Update` a receber o
SitCode, e assim por diante. O ciclo rodou sem erro nenhum, decidindo sobre um
retrato trocado — a pior forma de defeito.

O tradutor agora lê de um dicionário montado a partir de `cursor.description`.
E dois testes fecham o cerco: o cursor falso deriva o seu `description` do
**SQL de verdade** (uma lista escrita à mão acompanharia o tradutor sem nunca
discordar dele), e um teste compara as colunas projetadas com o contrato
declarado. Mexer no `SELECT` sem mexer no tradutor agora quebra o CI.

---

## Documento sem valor não é enviado ao SAP

O SAP recusa com `HTTP 400 | SAP -5002 | "Document total value must be zero or
greater than zero"`. A integração passa a verificar antes de enviar.

A causa é banal e legítima: o orçamento `00125275` tem **zero itens** no WBC.
Também vale para itens que existem mas ainda não chegaram a preço. Nenhum dos
dois é falha da integração, e transformá-los em erro de ciclo enche o dashboard
de falhas que ninguém pode corrigir.

Quatro decisões dentro disso:

* **Vale para cotação e pedido.** A regra do SAP é a mesma para os dois.
* **A ação não conta como executada.** Se contasse, o status da oportunidade
  seria espelhado como se houvesse documento, e o teto de escrita gastaria uma
  vaga com algo que nunca chegou ao SAP.
* **Nada trava para sempre.** A decisão depende de existir cotação, não de já
  termos tentado: quando o orçamento ganhar itens, o ciclo seguinte cria o
  documento normalmente.
* **O total é somado em `Decimal`.** As linhas carregam `float` — é o que o JSON
  do Service Layer aceita — e somar floats para comparar com zero é justamente
  onde um total legítimo de centavos poderia ser barrado. Há teste com R$ 0,01.

A prévia (`pendentes`) usa **a mesma função** de soma e avisa `documento NÃO
seria criado`. Duplicar o cálculo faria a prévia e o ciclo divergirem com o
tempo, que é o defeito que a prévia existe para não ter.

Um teste que afirmava o contrário caiu junto: `sem_de_para_o_documento_nao_leva_
linhas_falsas` fixava "documento sem linha em vez de item inventado". A escolha
era prudente, mas o resultado era o -5002. Hoje não se cria documento nenhum.

---

## ~~`U_INO_PN_Correc` só vale com `U_INO_Update = 'Y'`~~ — **errado, ver a seção seguinte**

Descoberto a partir de um erro que parecia ser de contato:
`-5002 Invalid contact person code [OQUT.CntctCode]` ao atualizar a cotação do
orçamento `00124884`.

O contato estava certo. O parceiro é que estava errado.

### O que acontecia

O payload escolhia o parceiro com `parceiro_novo or parceiro_atual`, sem olhar
`alterado`. A máquina de estados sempre exigiu `alterado` para agir sobre a
troca de PN (`_decidir_pedido`) — era o payload que discordava dela.

`U_INO_PN_Correc` preenchido **não** significa troca pendente. O legado nunca
baixava a marca (é a correção deliberada registrada em
`limpar_marca_de_troca_de_pn`), então o campo fica preenchido para sempre:

| | homolog | produção |
|---|---|---|
| `PN_Correc` preenchido com `Update <> 'Y'` | 590 | 592 |
| destes, `PN_Correc <> CardCode` | **578** | **580** |
| `Update = 'Y'` (troca de fato sinalizada) | 12 | 12 |

Todo documento dessas 578 saía para o parceiro errado.

### Por que só apareceu agora

O SAP não valida "o parceiro está certo" — ele não tem como saber. Só reclamou
porque o contato da oportunidade, por acaso, não existia no parceiro errado.
No caso do `00124884`: oportunidade em `C011151` com contato 8801 (que
**pertence** a `C011151`), payload mandando `CardCode = C011081`.

Quando o parceiro errado tivesse um contato compatível — ou quando a
oportunidade não tivesse contato —, o documento seria criado em silêncio para o
cliente errado. O erro foi sorte.

### A correção

`EstadoIntegracao.parceiro_do_documento` só usa `parceiro_novo` quando
`alterado` é verdadeiro. A escolha sai do payload e volta para o domínio, onde
já morava a mesma regra.

E, numa troca real, o **contato não é enviado**: ele pertence ao parceiro
antigo e não existe no novo — documento sem contato é melhor que documento
nenhum. `EstadoIntegracao.trocando_de_parceiro` é quem responde isso.

### O que ainda precisa ser decidido

As 22 cotações (em cada ambiente) cujo `CardCode` já difere do da oportunidade
não são corrigidas por esta mudança — foram criadas antes. Verificar se são
troca legítima de PN ou vítimas deste defeito. Registrado em `RETOMADA.md`.

---

## ~~Oportunidade fechada não aceita estágio novo~~ — o diagnóstico estava certo, a conclusão não

`-1029 [SalesOpportunitiesLines.SequenceNo] Field cannot be updated` ao vincular
o pedido recém-criado à oportunidade `14234` (orçamento `00124884`).

A mensagem aponta para `SequenceNo`, um campo sem relação com a causa. Foram
precisos seis experimentos em homologação para achá-la:

| tentativa | resultado |
|---|---|
| reenviar a coleção existente, sem acrescentar | **OK** |
| acrescentar sem `SequenceNo` | -1029 |
| acrescentar com `SequenceNo` igual ao das existentes | -1029 |
| acrescentar com `LineNum` explícito | -1029 |
| acrescentar copiando todos os campos de um estágio existente | -1029 |
| **acrescentar numa oportunidade `sos_Open`** | **OK** |

A causa é o estado: a `14234` está `sos_Sold`. Oportunidade fechada — ganha ou
perdida — recusa estágio novo. Não é limite de quantidade: há oportunidades com
107 estágios em homologação.

O documento já foi criado e está correto; o que o SAP não permite é o vínculo.
Falhar o ciclo por isso não desfaria nada e esconderia o resto do trabalho, então
vira aviso no log.

### De quebra: `PATCH` numa coleção **acrescenta**, não substitui

Descoberto ao tentar desfazer o estágio de teste na oportunidade `5681`:
reenviar a coleção original de 3 linhas deixou 4. A substituição só acontece com
`B1S-ReplaceCollectionsOnPatch: true` — o mesmo cabeçalho que já corrigia o
mesmo defeito nas linhas de documento, e que não estava sendo usado aqui.

`vincular_documento` continua lendo e reenviando a coleção inteira, o que
funciona nos dois modos. Mas fica registrado: quem for mexer nessa coleção
precisa saber que o padrão é acrescentar.

---

## Correção da seção anterior: a guarda da troca de PN é o pedido, não uma marca

A seção acima está errada no ponto principal, e fica registrada porque o engano
é instrutivo.

`U_INO_Update` sinaliza alteração de **valores** — não é a marca da troca de
parceiro. (É um UDF da oportunidade no SAP; a integração o lê e o baixa ao
vincular o pedido — ver a seção "A baixa de `U_INO_Update`, decidida".) A regra
correta: **sempre que
`U_INO_PN_Correc` estiver preenchido, o pedido deve passar para o parceiro
novo**, cancelando o anterior e criando outro.

### O que o legado realmente faz

`Program.cs:280-300`:

```csharp
if (ChecaPN(oComp, PNNew))                        // SELECT count(*) FROM OCRD
    if (!ChecaPNPedido(oComp, PNNew, item.OrcNum))// pedido já está no PN novo?
        CancelaPedido(...) → CriaPedido(..., PNNew, ...)
```

`ChecaPNPedido` é `SELECT count(*) FROM ORDR WHERE CardCode = PNNew AND
U_INO_COTWBC = orçamento AND CANCELED = 'N'`. **A idempotência vem daí** — do
estado do pedido —, não de baixar marca nenhuma. É isso que torna seguro agir
sempre que o campo está preenchido.

O `alterado == "Y"` está no `if` do legado, mas é a condição errada para se
apoiar: o que impede o retrabalho é o `ChecaPNPedido`.

### Por que as 578 são inofensivas

O `PN_Correc` residual fica preenchido para sempre porque ninguém o apaga — e
não precisa apagar: o pedido dessas oportunidades **já está** no parceiro novo,
então `troca_de_parceiro_pendente` é falso e nada acontece.

### O que mudou no código

* `EstadoIntegracao.parceiro_pedido_sap` — o `CardCode` do pedido vigente, que
  passou a vir do HANA (`R."CardCode" AS "PED_CARDCODE"`) e do Service Layer
  (`parceiro_aplicado`).
* `troca_de_parceiro_pendente` = `PN_Correc` preenchido **e** existe pedido
  **e** o pedido não está nesse parceiro. `U_INO_Update` não participa.
* A troca passou a ser avaliada **antes** da comparação de revisão: refazer o
  pedido já traz a revisão corrente junto.
* **A cotação nunca muda de parceiro.** No legado, `CriaCotacao` recebe o
  `CardCode` da oportunidade; `PNNew` só aparece no pedido.
* `limpar_marca_de_troca_de_pn` foi **removida**. Ela escrevia
  `U_INO_Update = 'N'` — campo que não é nosso — e apagava o `PN_Correc`,
  destruindo o registro de qual correção foi pedida.
* `ChecaPN` reproduzido em `RepositorioParceirosServiceLayer.existe`: sem ele,
  um parceiro corrigido inexistente faria o cancelamento acontecer e a criação
  falhar, deixando o orçamento **sem pedido nenhum**. `cancelar_e_recriar`
  cancela antes de criar, e essa ordem é deliberada.

### Detalhe que explicava o resto

`UpdatePedido` recebe um parâmetro `CardCode` e **nunca o usa**. Por isso a
chamada de `Program.cs:270`, que passa `PNNew` numa atualização comum, era
inerte — e por isso o `PN_Correc` residual nunca causou dano no legado.

---

## Oportunidade fechada: reabrir, vincular, restaurar

O diagnóstico anterior estava certo — oportunidade `sos_Sold`/`sos_Missed`
recusa estágio novo — mas a conclusão ("vira aviso") estava errada. O efeito
prático era o que o usuário viu na tela do orçamento `00124884`: os pedidos
listados na oportunidade eram os **cancelados** (`84252`, `84253`), e o novo
(`84314`, DocEntry `19507`, aberto e no parceiro corrigido) não aparecia.

O pedido existia. O que faltava era o vínculo — e sem ele a tela conta uma
história falsa.

O legado já resolvia isso, em `AddPedidoOportunidade`:

```csharp
if (updated) { oport.Status = sos_Open; oport.Update(); oport.GetByKey(...); }
oport.Lines.Add(); ... status = oport.Update();
if (status == 0) { oport.GetByKey(...); oport.Status = sos_Sold; oport.Update(); }
```

Reabre, acrescenta, fecha de novo. Uma diferença deliberada: o legado sempre
reclassifica como `sos_Sold`; aqui o **estado anterior é restaurado**, seja qual
for. Uma oportunidade perdida que recebe documento não deve virar ganha por
efeito colateral do vínculo.

A restauração fica num `finally`: se o vínculo falhar, a oportunidade não pode
ficar aberta por acidente. O estado dela é dado de negócio; o vínculo que falhou
é problema nosso.

### De quebra: `PercentageRate` estava errado

Mandávamos `0` nos dois documentos. O legado grava **0 na cotação**
(`AddCotacaoOportunidade`) e **100 no pedido** (`AddPedidoOportunidade`), e é o
que está nos vínculos reais em homologação. Corrigido em
`PERCENTUAL_DO_ESTAGIO`.

### A baixa de `U_INO_Update`, decidida

A integração **baixa** a marca ao vincular o pedido, como o legado. O campo é
um UDF da oportunidade no SAP e sinaliza alteração de valores pendente de
aplicação; com o pedido gravado, a alteração está aplicada e a marca não tem
mais o que sinalizar.

Só no pedido: as duas escritas de `U_INO_Update = "N"` estão dentro de
`AddPedidoOportunidade` (`ServiceProcess.cs:198` e `:210`) e nenhuma em
`AddCotacaoOportunidade`. Faz sentido — a cotação não aplica os valores, ela os
propõe.

A baixa vai no **mesmo PATCH** do estágio. Separá-la abriria uma janela em que a
marca está baixada e o vínculo não existe, e nesse intervalo a informação de que
os valores mudaram estaria perdida.

Isto é diferente da `limpar_marca_de_troca_de_pn` removida: aquela apagava
**também** o `U_INO_PN_Correc`, destruindo o registro de qual correção foi
pedida, e servia a uma regra de troca de PN que estava errada. O `PN_Correc`
continua intocado — quem garante a idempotência da troca é o parceiro do próprio
pedido.

---

## Comparação campo a campo: pedido 84316 (produção) x 84315 (homologação)

Mesmo orçamento (`00125430`), mesmo cliente, criados **no mesmo dia com 24
minutos de diferença** — o par ideal para achar campo esquecido.

**Nenhum campo faltando.** A lista "preenchido em produção e vazio em
homologação" saiu vazia. Impostos, totais e margem idênticos até o centavo:
`VatSum = 1781,84`, `DocTotal = 56.607,50`, `GrosProfit = 52.437,10`, e linha a
linha iguais.

Três divergências reais, e quatro que são ruído.

### 1. `Price` em vez de `UnitPrice` — corrigido

São campos **diferentes** no SAP: `UnitPrice` é o preço bruto, sobre o qual o
SAP ainda aplica desconto de parceiro ou lista de preços; `Price` é o líquido, e
força o valor. O legado usa `Price` (`ServiceProcess.cs:372`).

Hoje dá no mesmo — nenhum parceiro da integração tem desconto cadastrado, e as
27 linhas geradas em homologação saíram com `Price = PriceBefDi`. A troca é
proteção: no dia em que alguém cadastrar um desconto, o pedido sairia abaixo do
valor do orçamento, em silêncio.

O sintoma que denunciou isso foi o marcador `SpecPrice`: `'R'` em 12.869 linhas
do legado, `'N'` em 100% das nossas.

### 2. Espaços à direita nos UDFs de texto — corrigido

O texto vem do WBC com espaço no fim. O `84316` de produção tem **0** espaços
nos dois UDFs; o nosso `84315` tinha 1 e 4. Como os dois leram a mesma origem no
mesmo dia, quem apara é a gravação do DI-API — pelo Service Layer o espaço passa
direto, e `U_INO_D_Adicionais` aparece na impressão do cliente.

`rstrip` aplicado em `U_INO_D_Adicionais` e em `composicao()`. Só no fim: o
espaçamento interno do texto é proposital.

### 3. `ContactPersonCode` no pedido — mantido, por decisão

O legado grava `ContactPersonCode` **só na cotação** (`ServiceProcess.cs:1201`);
no pedido, o SAP preenche sozinho com o contato padrão do parceiro. Os números
de produção confirmam: 98,5% das cotações (76.793 de 77.956) têm o contato da
oportunidade, contra 65% dos pedidos (2.874 de 4.439).

No caso concreto, produção gravou `1456` (ANDERSON SILVA, contato padrão do
cadastro) enquanto a oportunidade aponta para `4567` (RODRIGO GARPELLI) — nos
**dois** ambientes. Ou seja: o nosso valor é o mais correto, e o do legado é um
efeito colateral de não enviar o campo.

Decisão do usuário: manter nos dois documentos.

### Ruído, não divergência

| Campo | Por quê |
|---|---|
| `DataSource` | `'S'` = Service Layer, `'O'` = DI-API. Registra qual API criou. |
| `DataVers`, `StationID`, `Ref1` | Atribuídos pelo SAP. |
| `U_INO_ORCAMENTO` | `DocEntry` do snapshot, próprio de cada ambiente. |
| `VATFirst` | Difere no par, mas impostos e totais são idênticos — sem efeito observável. |

---

## Comparação do OrcDetalhe: 514421 (produção) x 514706 (homologação)

Os snapshots apontados por `U_INO_ORCAMENTO` nos pedidos 84316 e 84315 — mesmo
orçamento `00125430`, mesmo dia. **59 colunas de cada lado, 4 diferenças**, e a
lista "preenchido em produção e vazio em homologação" saiu vazia.

| Campo | Produção | Nós | O que é |
|---|---|---|---|
| `CreateTime` / `UpdateTime` | 16:23 | 16:38 | os 15 min entre as execuções |
| `DataSource` | `'O'` | `'S'` | DI-API × Service Layer |
| `U_ORCIMP_NEGOCIACAO` | vazio | `'1.0000'` | **nós preenchemos, a produção não** |

### `U_ORCIMP_NEGOCIACAO`: a produção é que parou

Já estava em `RETOMADA.md` como suspeita; agora tem série mensal:

| mês | snapshots | preenchidos |
|---|---|---|
| jan–jun/2026 | 64.844 | 95% a 99% |
| **jul/2026** | 5.042 | **3%** |
| **ago/2026** | 2.559 | **5%** |

O WBC continua fornecendo o dado — nós preenchemos 825 de 825. O binário de
produção regrediu em julho. Nosso comportamento é o histórico e o correto.

### `U_ORCIMP_REVISAO`: um `or` que não deveria existir — corrigido

O mapeamento tinha `imp.revisao or orcamento.revisao`, contradizendo o próprio
comentário logo acima ("são colunas distintas e o legado usa cada uma no seu
lugar"). O legado grava só `item.ORCIMP_REVISAO`, sem alternativa.

O efeito é silencioso: no orçamento `00125528` a impressão não tem revisão e o
cabeçalho tem `'A'` — produção grava vazio, nós gravávamos `'A'`. O campo
passava a significar uma coisa ou outra conforme o dado, que é o pior caso para
quem lê o relatório. Fallback removido.

### Sobre comparar populações inteiras

A primeira tentativa comparou todos os nossos snapshots com todos os de
produção do mesmo orçamento e acusou divergência em quase tudo. Era artefato: a
produção grava um snapshot **a cada ciclo**, então cada um nosso casava com ~13
antigos, de revisões diferentes. Comparação de snapshot só vale entre os mais
recentes de cada lado, e no mesmo dia.

### O log é uma fonte e duas telas

O `pendentes`, o `ciclo` e o `worker` relatam pelo `logging`, não por `print`. O
mesmo registro sai em dois lugares: a tela de quem rodou o comando (texto puro,
para o relatório continuar legível) e um arquivo (com momento, nível e origem),
que a aba "Log" do painel mostra.

A alternativa — a tela com `print` e o painel lendo o tracking — daria duas
narrativas do mesmo evento, livres para discordar. E o tracking responde outra
pergunta: ele guarda o que aconteceu **com cada orçamento**, consultado por
orçamento; o log guarda a **execução**, na ordem em que aconteceu, com o que veio
antes do erro. Responder as duas com a mesma tabela deixaria as duas piores.

É o que torna o painel um monitor de verdade: o worker roda de madrugada, sem
ninguém no terminal, e a execução continua visível depois.

As duas telas não recebem exatamente o mesmo volume, e isso é deliberado: o
`httpx` registra uma linha por requisição, e numa prévia de 700 orçamentos elas
afogariam o relatório. Ficam fora da tela e dentro do arquivo — quem está no
terminal quer o resultado; quem investiga um erro depois quer a chamada que veio
antes dele. Aviso e erro de biblioteca passam nas duas: filtrar ruído não pode
virar esconder falha.

### A simulação da prévia mora no comando

Ela chegou a ser extraída para `application/previsao.py` para ser compartilhada
com o painel. Voltou para `cli._cmd_pendentes`: a prévia é uma ferramenta de
linha de comando, e quem a procura espera achá-la no comando que a executa.

O que continua compartilhado é só o **formato** do retrato que ela exporta —
justamente o ponto onde a falha seria silenciosa (o comando grava um nome, a
tela lê outro, a coluna aparece vazia sem erro).

### A aba "Próximo ciclo" lê um arquivo, não o SAP

O painel ganhou uma aba que mostra o que o worker faria na próxima passada:
ações, regra que decidiu e status da oportunidade, por orçamento.

A tentação era chamar a simulação de dentro do painel. Duas razões pesaram
contra:

1. **Carga.** Cada clique num filtro viraria uma varredura no HANA e no SQL
   Server. A regra do
   painel ("lê só o acompanhamento, nunca as origens") existe justamente para
   que uma tela de leitura não vire carga nos sistemas de produção.
2. **Procedência.** Um número na tela sem hora e sem company é um convite a
   olhar produção achando que é homologação. Um retrato datado carrega a própria
   procedência, e a aba a exibe antes dos números.

Então a prévia grava (`wbcpython pendentes --exportar previsao.json`) e a aba lê.
O caminho de decisão ficou em `application/previsao.py`, consumido pelos dois —
duplicá-lo faria a prévia e o ciclo divergirem na primeira regra nova, e a
prévia é exatamente a rede de segurança usada antes de rodar em homologação.

Efeito colateral pequeno e deliberado: a consulta do HANA passou a trazer
`CardName` e o `DocNum` dos documentos. São campos de **apresentação** — nenhuma
decisão os consulta — e vêm na mesma consulta porque buscá-los depois custaria
uma ida ao SAP por orçamento.


### O acompanhamento nunca esquece — por isso o painel precisa do corte

Sintoma: com a janela em 3 meses, o painel mostrava 886 orçamentos, e o resumo
do ciclo anunciava "719 processado(s): 719 com sucesso".

Duas causas distintas, e a consulta ao HANA não era nenhuma delas — ela filtra
`OOPR.OpenDate >= corte` corretamente (722 na janela em 01/09/2026).

**1. O acompanhamento acumula e nunca expira.** Dos 886, 167 eram de maio, de
quando a janela era de 6 meses. O ciclo já não os olha; o painel continuava
mostrando como se olhasse. O painel não tinha como aplicar o mesmo corte: a
tabela não guardava a data de abertura. Agora guarda (`data_abertura`, vinda de
`OOPR.OpenDate` na mesma consulta), e o painel filtra pelo mesmo critério, com
uma caixa para ver o histórico inteiro quando se quiser.

Linhas sem a data — as gravadas antes da coluna existir — passam pelo filtro de
propósito: sumir por falta de um dado que nunca foi gravado seria lido como
"esses orçamentos não existem", e não como "não sei a data".

Só que essa regra, sozinha, anulava a correção: as 886 linhas antigas nascem com
data nula, e as 167 que já saíram da janela **nunca mais serão verificadas** pelo
ciclo — ficariam nulas para sempre, passando pelo filtro para sempre. Daí o
`wbcpython datas-de-abertura`: lê o `OpenDate` no HANA sem filtro de janela
(o alvo é justamente quem está fora dela) e preenche só o que está vazio,
escrevendo apenas no acompanhamento. Idempotente, e nada é tocado no SAP.

**2. "Avaliado" não é "processado".** O ciclo lê a janela inteira porque ler é
barato; escrever é que tem teto. Mas o resumo contava tudo como processado com
sucesso, e os KPIs do painel também — 633 das 886 linhas eram `SEM_ACAO`. O
resumo passou a ser "N avaliado(s); M com ação (E com escrita no SAP); ...", e
os KPIs separam avaliados, com ação e sem ação.

As linhas `SEM_ACAO` continuam sendo gravadas: são a prova de que o orçamento
foi avaliado e conscientemente ignorado. O que mudou é que elas não se disfarçam
mais de trabalho feito.

### Migração de coluna sem perder histórico

`create_all` cria tabelas que faltam e **não** altera as que já existem: numa
base de acompanhamento em uso, uma coluna nova no modelo vira `no such column`
na primeira consulta. Recriar a base não é opção — ela é histórico operacional.

`_acrescentar_colunas_novas` faz `ADD COLUMN` só de colunas **anuláveis**, a
única alteração que SQLite e PostgreSQL aceitam sem reescrever a tabela e a
única que não pode perder dado. Renomear, apagar ou mudar tipo ficam de fora de
propósito: é aí que uma migração automática silenciosa destrói histórico. Coluna
obrigatória que falte vira erro no log dizendo o que fazer, em vez de um
`no such column` sem explicação.


### Espelhar o SitCode só quando o SAP discorda

Sintoma: dezenas de orçamentos apareciam com `atualizar_status_oportunidade`
sem que houvesse status a atualizar.

A condição era `if estado.tem_cotacao:` — sem nenhuma comparação. Era fidelidade
ao legado (lá a condição vinha com uma tautologia, e o efeito real era "existe
cotação"), mas o legado podia se dar a esse luxo. Medido na janela de
homologação: **160 espelhamentos, 154 deles regravando o valor que já estava
lá**. Só 6 divergiam de fato.

Cada um desses PATCHes consome o teto de escrita do ciclo, deixa um evento no
histórico da oportunidade e entra no resumo como trabalho feito — três formas de
o painel mentir sobre o que o worker fez.

Agora a ação só sai quando `U_INO_StatusWBC` difere do SitCode corrente. A
comparação é **textual e com `strip()`**: o campo é texto no SAP (`atualizar_status`
grava `str(sitcode)` de propósito), e comparar cru faria os valores com espaço
divergirem para sempre.

O encerramento também deixou de pedir espelhamento: `encerrar()` já grava o
`U_INO_StatusWBC` junto com o `Status`, e pedir os dois era o mesmo PATCH duas
vezes na mesma oportunidade, no mesmo ciclo.

O processador já tinha essa guarda no espelhamento pós-documento
(`_espelhar_status_apos_documento`) — ela existia no lugar errado. Estando na
decisão, a prévia e o painel também param de anunciar uma ação que não aconteceria.


### O peso da linha do pedido

O pedido passou a levar `Weight1` a partir da árvore de produtos do WBC. Três
detalhes decidem se o número sai certo, e todos vieram do legado — que já fazia
isso, num trecho fácil de não achar (`GetPesoPedido` em `Querys.resx` e
`ServiceProcess.cs:640`).

**1. Só o nível 1.** `INTEGRACAO_ORCPRDARV` é uma estrutura de produto: nível 1
é a peça que embarca, 2 são componentes, 3 é matéria-prima — e cada nível repõe
a mesma massa decomposta. No `00124853`, a coluna de 348,02 kg do nível 1
reaparece no nível 2 como 341,83 de aço mais 6,19 de tinta. Somar tudo conta o
mesmo aço duas ou três vezes:

| orçamento / item | só nível 1 | todos os níveis |
|---|---|---|
| `00124853` item 1 | 760,65 kg | 1.818,70 kg |
| `00125527` item 1 | 1.095,03 kg | 2.980,19 kg |

O legado filtra `U_INO_NIVEL = 1` pelo mesmo motivo. A diferença é a fonte: ele
soma sobre o snapshot que acabou de gravar no SAP (`@INO_ORC_LINHA`), nós somamos
direto no WBC — mesmo número, sem depender de o snapshot existir.

**2. O peso do pedido é de EMBARQUE, não o líquido.** Esta foi a descoberta que
mudou a regra, e só apareceu porque o comando foi rodado contra um pedido real.
O pedido 84112 (orçamento `00124853`) tem `Weight1 = 836`, e a árvore soma
760,65 — 10% de diferença. Medindo **1.271 linhas de pedido de 2026** na
produção:

| | |
|---|---|
| razão `Weight1` ÷ peso da árvore, mediana | **1,099** |
| razões entre 1,09 e 1,11 | 622 de 1.060 |
| pesos que são inteiros redondos | 1.056 de 1.061 |
| melhor fator, varrendo 1,000–1,300 de milésimo em milésimo | **1,100** |
| linhas deixadas em 1 kg (padrão do cadastro) | 127 |
| linhas deixadas em 0 | 64 |

`floor(760,65 × 1,10) = 836` — exatamente o que a produção gravou.

**A reprodução não é exata, e não pode ser.** Só 36,8% das linhas batem na
unidade com `floor(líquido × 1,1)` a partir do retrato ligado ao pedido — contra
25,1% arredondando. Os outros 63% não seguem fórmula nenhuma: o campo é
preenchido à mão em produção, com uma folga *típica* de 10%. O que a regra
garante é a ordem de grandeza certa e um critério único, em vez de 1 kg em 127
linhas e 0 em 64.

O fator vive em `FATOR_PESO_EMBARQUE` (padrão `1.10`): é regra de negócio —
embalagem — e não constante física.

**3. O peso é unitário.** `Weight1` no SAP é o peso de **uma** unidade; o total
da linha é ele vezes a quantidade. Por isso a divisão (`peso / quantidade`),
igual ao legado. Hoje é invisível — `ORCPRDQTD` é nula em 100% das linhas e a
quantidade cai no fallback 1 — mas no dia em que o WBC preencher a coluna, sem a
divisão o peso sairia multiplicado duas vezes.

**4. Sem peso, sem campo.** A árvore existe para 5.397 dos ~59 mil orçamentos da
base; no recorte que importa — os que viram pedido — são **314 de 316 linhas**.
Nas outras o campo simplesmente não é enviado e o SAP mantém o peso do cadastro
(1 kg). Enviar zero trocaria um número errado por outro, e nenhum relatório de
expedição conseguiria distinguir "não sei" de "não pesa nada". As linhas sem
peso viram aviso no log. Pela mesma razão, um item cujo peso trunca para zero
(menos de ~0,91 kg líquido) também não recebe o campo.

**A cotação não leva peso** — também do legado, onde a linha equivalente está
comentada (`:377`). A decisão fica legível na assinatura: `pedido.linhas` tem o
parâmetro `pesos`, `cotacao.linhas` não tem. Não há como passar peso para a
cotação por engano.

### `wbcpython pesos`: corrigir sem refazer

Para os pedidos criados antes da regra existir, e para quando a árvore do WBC é
corrigida depois do pedido:

```bash
uv run wbcpython pesos --pedido 84315 --simular   # mostra o que mudaria
uv run wbcpython pesos --pedido 84315             # grava
uv run wbcpython pesos --orcamento 00124853       # pelo orçamento
```

Ele **não** refaz o pedido — grava só o peso, casando as linhas pelo
`U_INO_ORCITM` que a integração já gravou em cada uma (não pela ordem: uma linha
a mais deslocaria o peso de todas as outras). Preço, item, texto e depósito
ficam como estão.

**Cuidado ao rodar em pedido antigo.** Como o `Weight1` da produção é preenchido
à mão em boa parte dos casos, o comando pode sobrescrever um número que alguém
digitou de propósito. Ele imprime `atual -> novo` linha a linha justamente para
isso, e `--simular` mostra tudo sem gravar. Use a simulação primeiro.

Isso exigiu um `PATCH` **sem** `B1S-ReplaceCollectionsOnPatch` — o oposto do que
`atualizar` faz. Com o cabeçalho, a coleção enviada substitui a existente:
mandar só as linhas com peso apagaria as demais. Sem ele o `PATCH` mescla, e a
mesma mesclagem que atrapalha ao atualizar um documento inteiro é exatamente o
que se quer aqui — linha identificada por `LineNum` muda só os campos enviados.

### O worker recusa iniciar contra produção — e isso fica como está

Ao levantar os riscos da virada, apareceu que o projeto **não tem caminho para
escrever em produção**:

| Situação | O que acontece |
|---|---|
| Apontado para produção, trava ativa | Leitura passa; escrita levanta `ProductionWriteBlocked` |
| Apontado para produção, trava desativada | O worker **recusa iniciar** (`cli.py:283`) |

Não é redundância mal resolvida: são duas defesas para dois erros diferentes. A
trava protege contra apontar para produção **sem perceber**; a recusa do worker
protege contra desligar a trava e esquecer ligada. Para valer, a segunda tem de
ser removida à mão por alguém que saiba o que está fazendo.

Fica como está. A virada para produção é um evento com roteiro
(`RISCOS_PRODUCAO.md`), não uma variável de ambiente.

### O relatório de riscos é medido, não estimado

`RISCOS_PRODUCAO.md` foi produzido rodando a máquina de estados contra
`SBOALTAMIRAPROD` em memória — sem abrir sessão de escrita, sem alterar o
`.env`, com o schema entrando por parâmetro. Todo número dele é verificável, e a
consulta que o produziu está no próprio documento.

A alternativa — um relatório com "pode haver duplicação de documentos" e "atenção
ao volume" — teria sido escrita em cinco minutos e não teria descoberto que o
legado está criando 26 cotações por dia neste momento. Risco genérico não muda
decisão nenhuma.

### O painel saiu do Streamlit para HTML + HTMX

Pedido do usuário, e o motivo técnico já estava dado: o painel deixou de ser uma
tela que alguém abre para conferir um número e virou um **monitor** — fica aberto
enquanto o worker roda sozinho.

O Streamlit reexecuta o script inteiro a cada interação e repinta a página
inteira. Num monitor isso é o defeito principal: o log pisca, a rolagem volta ao
topo e o campo de filtro se recria embaixo de quem está digitando, justamente
enquanto se lê a linha de erro que motivou abrir a tela.

Com HTMX, cada bloco pede o seu próprio fragmento de HTML no seu ritmo — o log a
cada 5 s, os números a cada 30 s — e troca só o pedaço que mudou. O
acompanhamento do log é pausável, porque investigar exige que a tela pare.

O que **não** mudou, e é o que tornou a troca barata: `dashboard/dados.py` e
`dashboard/previsao.py` já eram camada de dados pura e testada. Só a
apresentação foi reescrita. A separação foi feita quando o painel ainda era
Streamlit, sem saber que serviria para isto.

Três consequências que valem registro:

1. **O htmx.min.js está versionado** (`dashboard/static/`, 0BSD, 50 KB). A
   máquina do worker não tem saída para a internet: um painel que depende de CDN
   abre em branco no dia em que alguém precisa dele.
2. **Sem fonte de web e sem biblioteca de gráfico.** Pilha de fontes do sistema
   e barras em CSS. Mesma razão, mais o navegador antigo da fábrica.
3. **O estado vive no endereço** (`/?aba=log&tudo=1`), não em sessão. O painel
   pode ficar fixo numa TV numa aba, e um endereço colado no chat abre a mesma
   tela para quem receber.

A rota de reprocessamento continua sendo a única escrita, e continua sem tocar o
SAP: registra a solicitação, e o worker a executa no ciclo seguinte.

### O painel escuta em `127.0.0.1` por padrão

Ele não tem autenticação. Quem alcança o painel pode registrar um pedido de
reprocessamento em nome de quem quiser — e o reprocessamento termina em
documento criado no SAP.

Expor na rede é possível (`--host 0.0.0.0`), mas precisa ser um ato deliberado,
com o comando dizendo em voz alta que não há autenticação. O padrão não pode ser
o valor que expõe.

### A aba "Executar" roda a CLI de verdade, em subprocesso

Pedido do usuário: as principais opções da linha de comando disponíveis no
painel. Isso reabre a decisão de que **o painel não escreve no SAP** — vale a
pena registrar como as duas coisas convivem.

O que continua valendo: o *processo do painel* não escreve no SAP. Quem escreve
é a CLI, num processo próprio, exatamente como quando alguém digita o comando no
terminal. O painel monta a linha de argumentos e mostra a saída.

Por que subprocesso e não chamar as funções internas, que seria mais rápido e
daria resultado estruturado:

1. **Não diverge.** O que o painel faz é literalmente o comando. Não nasce um
   "caminho do painel" que possa passar a se comportar diferente do caminho do
   terminal na primeira regra nova.
2. **A saída é a mesma.** O texto na tela é o texto do terminal, na mesma ordem
   — extensão natural de "uma fonte, duas telas".
3. **Isolamento.** Um ciclo que estoure não derruba o painel, e dá para
   interromper. Chamada em processo não permite nenhum dos dois.

O custo é que o painel precisa rodar **na máquina do worker**, com drivers e
acesso ao SAP e ao WBC. É onde ele já roda.

Sobre a concorrência, o ponto que importava: `executar_ciclo` já toma a **trava
de execução única** no banco de acompanhamento, e a trava vale entre processos.
Painel e worker disputam a mesma, e quem perde não roda — que era exatamente o
risco de o painel passar a disparar ciclos. A serialização dentro do `Executor`
é só conveniência de tela, para dois cliques não virarem duas tentativas.

`terminate` e não `kill` ao interromper: o ciclo trata o sinal e libera a trava
ao sair. Um `kill` a deixaria presa até expirar, e o worker ficaria 30 minutos
parado por causa de um clique.

### Senha autoriza, nome audita — e são coisas diferentes

Os comandos que escrevem exigem `PAINEL_SENHA` (do `.env`) **e** o nome de quem
está executando. Não é redundância:

- A senha responde "esta pessoa pode?". O painel não tem autenticação de
  usuário, e quem alcança a porta não deveria poder criar pedido no SAP.
- O nome responde "quem mandou rodar isso?" — pergunta que aparece semanas
  depois, olhando um documento. Senha é segredo compartilhado: ela prova que
  alguém a conhecia, não quem era.

O nome vai para o histórico do orçamento no banco de acompanhamento, e não só
para a lista em memória do painel, que morre com o processo.

**Falha fechada**: `PAINEL_SENHA` vazia **desabilita** os comandos de escrita.
A armadilha óbvia era `"" == ""` liberar tudo justamente na instalação que
esqueceu de configurar. Há teste para esse caso específico.

A comparação usa `secrets.compare_digest`, não `==`: comparar string vaza tempo,
e do outro lado há uma rede interna inteira.

### Em produção o painel não executa nada que escreva

Escolha do usuário, e coerente com o `RISCOS_PRODUCAO.md`: o primeiro ciclo em
produção cancelaria 27 cotações e criaria 4 pedidos (R$ 170.973,41), tudo
irreversível. Não pode depender de um clique numa tela sem autenticação.

Ali os botões aparecem desabilitados e a tela diz **qual** guarda fechou a porta
— produção ou senha ausente —, porque os remédios são opostos: uma se resolve
preenchendo o `.env`, a outra não deve ser resolvida. A leitura continua
liberada: conferir o ambiente e rodar a prévia em produção é justamente o que se
quer poder fazer pela tela.

### O `pesos` pela tela exige prévia do **mesmo** pedido

Não é a caixa "simular" desmarcada que libera aplicar. O que libera é ter visto
a prévia daquele pedido: o comando altera peso de documento já criado, e a
diferença entre o peso líquido da árvore e o de embarque (×1,10) é o tipo de
coisa que se confere olhando.

Autorizar por "já simulou alguma coisa" deixaria aplicar no pedido errado depois
de conferir o certo — tem teste para isso.

### A lista de argumentos sai do catálogo, nunca do formulário

Cada comando declara seus campos (`dashboard/comandos.py`). Um campo que o
`Comando` não declara é ignorado, e o subprocesso recebe a **lista** de
argumentos, sem shell. É o que impede que um campo de texto na tela vire
execução de comando arbitrário na máquina do worker.

A senha também não vai para o ambiente do subprocesso: ela autoriza o clique e
não tem nada a fazer dentro do comando.

### Os indicadores do topo são os filtros da lista

Pedido do usuário. Cada número do topo é uma pergunta — "quais deram erro?" —, e
o número sozinho só diz quantos são. Clicar abre a lista daqueles.

A decisão que importa é **de onde sai o filtro**. Cada indicador virou um
`Recorte` em `dashboard/dados.py`, e o recorte é a mesma definição usada para
contar, reaproveitada para listar. "Com ação" não repete a lista de status: usa a
constante `STATUS_COM_ACAO` que os KPIs já usavam. Sem isso, um status novo
entraria em uma das duas e a lista passaria a discordar do número que a abriu.

O recorte é aplicado **em Python**, sobre as mesmas linhas que os indicadores
contaram, e não como um `WHERE` numa consulta separada. Uma consulta separada
acertaria hoje e divergiria no dia em que o teto de linhas entrasse em jogo — e
a divergência apareceria na tela como "o painel está mentindo", sem indicação de
qual dos dois lados está errado. Há um teste que compara, para cada recorte, o
valor exibido no indicador com o tamanho da lista que ele abre.

Três detalhes deliberados:

1. **`<button>`, não `<div>` clicável.** É acionável, então precisa ser
   alcançável pelo teclado e anunciado como botão (`aria-pressed`).
2. **O recorte vive no endereço** (`/?aba=oportunidades&recorte=com_erro`),
   como a aba. Um endereço colado no chat abre a mesma tela filtrada, e é o que
   permite ao topo — que repinta a cada 30 s — devolver o indicador ainda
   marcado, em vez de a marca sumir sozinha pouco depois do clique.
3. **Recorte desconhecido cai em "Avaliados"**, não em erro. O identificador vem
   do endereço, que pode ter sido editado à mão ou guardado num favorito de uma
   versão anterior do painel; mostrar tudo é o pior caso aceitável.

A lista mostra o recorte ativo como etiqueta removível. Uma lista filtrada que
não diz que está filtrada é a forma mais fácil de alguém concluir que faltam
orçamentos no painel — e o "nada encontrado" também nomeia o recorte, pelo mesmo
motivo.

O `recorte` e o seletor de "Situação" se somam em vez de competir: o recorte é a
pergunta grossa do topo, o seletor é a escolha fina de um status. Dentro de "com
ação" ainda dá para ver só as encerradas.

### `U_INO_Update = 'N'` com `PN_Correc` preenchido congela o pedido

Regra do negócio, trazida pelo usuário a partir do orçamento `00124045`: essa
combinação significa que **o pedido já está correto em nome de outro PN**, e a
integração não deve tocá-lo — nem trocar o parceiro, nem atualizar.

Esta decisão **corrige uma anterior**, e vale dizer em que exatamente. A antiga
dizia que `U_INO_Update` não podia entrar na decisão da troca, e continua certa
naquilo que afirmava: exigir `Update = 'Y'` *para poder trocar* era errado, e
foi um engano meu corrigido lendo `Program.cs:280-300`. O que o negócio
esclareceu agora é outra coisa: `Update = 'N'` **junto com** `PN_Correc` é sinal
positivo de que alguém já resolveu o caso à mão. Uma guarda, não uma condição.

O dano vinha por um caminho que a leitura do código não sugeria. Em `00124045` a
correção **já estava aplicada**: pedido vigente `84022` em `C011608`
(= `PN_Correc`), o antigo `83949` cancelado em `C008483`. Logo
`troca_de_parceiro_pendente` era **falsa**, o fluxo caía na comparação de
revisão e chamava `atualizar_pedido` — e como `campos_comuns` envia `CardCode`
**sempre**, com `parceiro_do_pedido` devolvendo o parceiro da *oportunidade*
fora da troca, o PATCH desfazia a correção.

Por isso a guarda entra no **início** do ramo do pedido, e não dentro do ramo da
troca: o estrago não vinha da troca.

Medido em produção, somente leitura:

| | |
|---|---|
| Oportunidades com `PN_Correc` preenchido | 603 |
| …com `Update = 'N'` | 592 |
| …que a regra antiga consideraria com troca pendente | 4 — **todas** com `Update = 'N'` |

As 4 são de 2024–2025, todas com o pedido vivo num **terceiro** parceiro, nem o
da oportunidade nem o `PN_Correc`: casos resolvidos à mão. Nenhuma era trabalho
legítimo, e refazer o pedido em qualquer uma seria destrutivo.

E o dano não era hipotético. Rodando a prévia sobre a janela de 6 meses em
homologação (1.540 oportunidades) com a regra antiga e com a nova, lado a lado:

| | Regra antiga | Regra nova |
|---|---|---|
| Escritas no ciclo | 16 | **13** |
| `atualiza_pedido` | 6 | **3** |
| `troca_de_pn` | 0 | 0 |
| `pedido_corrigido_a_mao` | — | 64 |

Os **3 `atualiza_pedido` que desapareceram** são pedidos que teriam o `CardCode`
revertido ao parceiro da oportunidade na próxima passada — o defeito do
`00124045`, prestes a acontecer em outros três. E o `troca_de_pn` em zero nas
duas colunas mostra que nenhuma troca legítima foi perdida: a janela atual não
tem nenhuma pendente.

**Escopo**: congela o pedido, não a oportunidade. Espelhar o SitCode continua,
porque não toca no documento nem no `CardCode` — escolha do usuário entre as
três alternativas propostas.

**Consequência que precisa ficar dita**: com esta guarda, a troca de PN só
acontece com `U_INO_Update = 'Y'`. Se o WBC preencher o `PN_Correc` sem marcar
`Update`, a troca nunca acontece. Fica em aberto com a equipe do WBC
(`RETOMADA.md`), e não é urgente: hoje as 11 oportunidades com `Update = 'Y'` e
`PN_Correc` já estão todas com o pedido no parceiro corrigido.

### Pedido fechado no SAP não é alterado nem cancelado

Reportado pelo usuário no orçamento `00124268`: o script tentou salvar alteração
num pedido já fechado. Regra: pedido fechado, a integração não escreve nele nem
o cancela.

O `DocStatus` do pedido **não era lido em lugar nenhum** — nem na consulta do
HANA, nem no Service Layer. A integração não tinha como saber, e caía em
`atualiza_pedido` sempre que a revisão do WBC fosse mais nova. No `00124268`:
pedido `83988`, `DocStatus = 'C'`, revisão `'A'` no SAP contra `'D'` no WBC.

O tamanho disso é o que surpreende. Em produção, **2.452 dos 2.568** pedidos
vigentes estão fechados — é o estado normal de um pedido entregue ou faturado,
não a exceção. A guarda não trata um caso de borda; trata a maioria.

| | Homologação | Produção |
|---|---|---|
| Pedidos vigentes abertos (`'O'`) | 146 | 116 |
| Pedidos vigentes fechados (`'C'`) | 2.424 | 2.452 |
| Cotações vigentes fechadas | 3 | 3 |

Três decisões dentro dessa:

1. **A guarda vem antes das outras** no ramo do pedido. As demais são regra de
   negócio; esta é o SAP recusando a escrita. Quem investiga precisa ver
   `pedido_fechado_no_sap` e não `pedido_corrigido_a_mao`, que descreveria o
   caso errado.
2. **"Fechado" não é "inexistente".** `tem_pedido` continua verdadeiro, então
   não se cria outro; e sem pedido a marca não impede a criação do primeiro.
   Confundir os dois deixaria orçamentos sem pedido para sempre.
3. **O nome do campo é diferente nas duas fontes**, e é por isso que o método
   entrou no protocolo em vez de na regra: no Service Layer é `DocumentStatus`
   com `bost_Open`/`bost_Close`; na consulta do HANA é `DocStatus` com
   `'O'`/`'C'`. Os valores foram conferidos contra o ambiente real (pedido
   `83988`: `DocumentStatus = 'bost_Close'`, `Cancelled = 'tNO'`), não
   deduzidos da documentação.

**Escopo**: o pedido. Espelhar o SitCode continua, como nas outras guardas.

**Fica em aberto**: existem **3 cotações fechadas** em cada ambiente, e o mesmo
defeito se aplica a elas — o SAP recusaria alterar ou cancelar. Não foi tratado
porque o pedido do usuário era sobre o pedido; a correção é a mesma linha, e o
dado (`DocStatus` da cotação) já viaja na consulta.

### Status de documento desconhecido congela, não libera

Encontrado em revisão do próprio código das duas guardas. `esta_fechado`
devolvia `False` para `DocStatus` vazio, nulo ou inesperado — e `False` faz o
ciclo **escrever**.

Isso é o oposto do que o resto do projeto faz. `safety.py` e a `PAINEL_SENHA`
bloqueiam quando a configuração está incompleta; aqui a guarda mais nova fazia o
contrário justamente no ponto mais sensível. E o defeito que originou a guarda
foi **uma coluna que não era lida**: se a coluna sumisse de novo — edição da
consulta, mudança de view, permissão —, a proteção evaporaria em silêncio, sem
nenhum teste falhar, e a integração voltaria a escrever em pedido fechado.

Agora: `'C'`/`bost_Close` fecha, `'O'`/`bost_Open` abre, qualquer outra coisa
**congela e emite `WARNING`** dizendo o documento, o valor recebido e os valores
esperados. Custo medido: nenhum — agrupando por status nos dois ambientes só
existem `'O'` e `'C'` (146+2.424 em homologação, 116+2.452 em produção), então
nenhuma linha cai nesse caminho hoje. O aviso existe para o dia em que uma cair.

A política mora num lugar só (`esta_fechado_pelo_status`), que é também o único
ponto que conhece os dois vocabulários — `bost_Open`/`bost_Close` do Service
Layer e `'O'`/`'C'` da tabela. A versão anterior duplicava a tradução e ainda
aceitava `'c'` na implementação do Service Layer, contradizendo o próprio
docstring, que dizia que ali o valor nunca é `'C'`.

### Troca de PN sobre pedido fechado sai com regra própria

Também da revisão. Uma troca de parceiro pedida sobre um pedido fechado é um
pedido de negócio que **nunca** poderá ser atendido: não dá para cancelar o
pedido nem refazê-lo. Congelava em silêncio, com a mesma regra
`pedido_fechado_no_sap` dos 243 congelamentos benignos da janela — indistinguível
deles no painel e no log.

Agora sai como `troca_de_pn_impossivel_pedido_fechado`, com o motivo escrito e
os dois parceiros no texto. E as duas guardas de congelamento passaram a
registrar motivo: antes retornavam sem nenhum, o que dava ao operador uma linha
sem explicação — exatamente na tela que existe para explicar.

### Virada para produção: as duas recusas foram removidas

Decisão do usuário em 02/09/2026. A integração passou a
apontar para `SBOALTAMIRAPROD`, e as duas recusas que impediam isso saíram:

| Onde | O que fazia | O que faz agora |
|---|---|---|
| `cli.py` (`worker`/`ciclo`) | Recusava iniciar com produção + trava desligada | Roda, e avisa na tela e no log |
| `cli.py` (`env`) | Saía com código 1 nessa combinação | Sai com 0, e avisa |
| `cli.py` (`doctor`) | Contava como problema impeditivo | Vira aviso; não impede |

As duas existiam pelo mesmo motivo — proteger contra **desligar a trava e
esquecer ligada**. Com a trava desligada de propósito, elas deixariam a
integração sem poder rodar.

O que ficou no lugar é visibilidade, e a distinção importa: nenhum ciclo em
produção começa sem uma linha dizendo que vai escrever em documento de verdade,
na tela **e** no arquivo de log — que é o que alguém lê ao abrir a execução da
madrugada. O `env` deixou de sair com erro porque, sendo agora o estado normal,
faria o `doctor` e a aba "Executar" marcarem falha em toda execução; um alarme
que toca sempre deixa de ser lido.

**O que não mudou**: a trava de somente-leitura do SQL Server do WBC
(`safety.py`, Regra 5) não tem chave de desligamento e continua absoluta. E o
painel continua **sem** executar comandos que escrevem quando apontado para
produção — foi decisão separada do usuário, e não foi revertida.

**Estado medido na virada**, com as duas guardas novas do pedido já em vigor:

| | |
|---|---|
| Oportunidades na janela (6 meses) | 1.635 |
| Escritas no primeiro ciclo | 99 |
| — cancelar cotação + encerrar oportunidade | **79** |
| — atualizar cotação + criar pedido | 8 |
| — só espelhar status | 12 |

**O legado continuava escrevendo em produção na data da virada** — 25 cotações e
4 pedidos em 02/09/2026, e todo dia útil das duas semanas anteriores. Por isso a
virada foi feita **sem iniciar o worker**: o usuário desliga o legado primeiro.
Dois processos gravando pela mesma chave `U_INO_COTWBC` criam documento
duplicado, e a trava de execução única não enxerga o legado.


## SitCode 99 marca a oportunidade como perdida — e não a cancela

Confirmação pedida pelo usuário: *"status wbc 99 faz o seguinte: marca a
oportunidade como perdeu sem cancelar a oportunidade. esta regra deve valer"*.

**A regra já era essa, e continua valendo.** No encerramento a integração faz
um `PATCH SalesOpportunities(id)` com `{"Status": "sos_Missed",
"U_INO_StatusWBC": "99"}`. Marcar como perdida e cancelar são coisas distintas
no SAP, e a integração só faz a primeira — igual ao legado
(`ServiceProcess.cs:91-122`). Vale notar que `sos_Lost` **não existe** no enum
`BoSoOsStatus`: os valores aceitos são `sos_Open`, `sos_Missed` e `sos_Sold`.

Duas coisas foram decididas na confirmação:

1. **A cotação vinculada continua sendo cancelada** no encerramento
   (`CANCELAR_COTACAO_NO_ENCERRAMENTO`). É divergência deliberada do legado, já
   registrada acima, e o usuário escolheu mantê-la. O cancelamento é da
   *cotação*, nunca da oportunidade.
2. **A ação foi renomeada.** Chamava-se `FECHAR_CANCELAR_OPORTUNIDADE`, nome
   herdado do legado que dizia "cancelar" para uma operação que não cancela — a
   origem exata da dúvida. Agora é `Acao.MARCAR_OPORTUNIDADE_PERDIDA`, rótulo
   "Marcar oportunidade como perdida", e a regra registrada no tracking passou
   de `encerramento` para `marca_perdida`.

Na janela de produção, **as 79 oportunidades a encerrar são todas SitCode 99**.

`tests/domain/test_sitcode.py::TestSitCode99MarcaPerdidaSemCancelar` fixa o
comportamento, inclusive o nome: um teste falha se a ação voltar a se chamar
"cancelar".

## `ciclo --simular`: encher o painel sem tocar no SAP

Pedido do usuário: *"é possível executar um ciclo sem escrever nada no sap? só
para popular os números?"*. Não era — e os dois caminhos que pareciam servir
não serviam:

* `pendentes` lê e decide a janela inteira sem escrever, mas **não grava no
  acompanhamento**. Por isso o painel seguia zerado mesmo depois de uma
  verificação que encontrou 99 ações.
* Rodar o `ciclo` com a trava de produção ligada seria pior do que não rodar.
  A trava levanta `ProductionWriteBlocked`, o processador registra exceção como
  erro, e o painel terminaria com 99 linhas vermelhas descrevendo uma falha que
  não aconteceu.

O ensaio aproveita a ordem que o processador já tinha: o acompanhamento é
gravado **antes** de qualquer ação. `somente_leitura` simplesmente não chama
`_executar`. A garantia é negativa e estrutural — não é uma lista de ações
filtradas uma a uma, é o único caminho que escreve não sendo percorrido. Uma
ação nova não tem como escapar do ensaio por esquecimento.

Três decisões que valem registro:

1. **O status não é tocado.** Linha nova nasce `PENDENTE` — avaliada, com ação
   identificada, não executada. E uma linha que já traz resultado de ciclo real
   não é rebaixada: o ensaio não desfez nada no SAP.
2. **O evento diz que foi ensaio.** Sem ele, semanas depois, uma linha parada em
   "Pendente" seria indistinguível de uma que falhou em silêncio.
3. **O botão do painel dispensa a senha** (`dispensa_senha`, exceção única e
   explícita). A senha protege o SAP; exigi-la aqui tornaria o comando
   **indisponível em produção**, que é exatamente onde ele serve para alguma
   coisa. O selo do cartão diz "escreve no acompanhamento", e o subtítulo da
   seção deixou de prometer que nada ali escreve.

O teto de escrita não se aplica ao ensaio: sem escritas, ele nunca é atingido, e
a janela inteira é avaliada — que é o ponto.

## `U_INO_Update` é um desvio, não uma autorização — e o congelamento foi revertido

Esta seção substitui uma decisão anterior, aplicada e desfeita no mesmo dia. O
erro vale mais registrado do que apagado.

**O que o campo é**, dito pelo usuário: um UDF da oportunidade no SAP,
preenchido **à mão pelo operador quando ele precisa trocar o PN de um pedido**.
Depois que o pedido novo é criado, volta para `'N'`.

**O que eu fiz de errado.** Perguntei se `Update = 'N'` deveria congelar o
pedido diante de uma revisão nova, o usuário respondeu "SIM", e eu apliquei.
A pergunta estava mal formulada: eu ainda acreditava que o campo significava
"os valores mudaram no WBC". Com o significado real, congelar em `'N'` inverte
a regra.

**A árvore do legado** (`Program.cs:230-310`), que o usuário pediu para ver
antes de decidir qualquer coisa — e que resolveu a questão:

```csharp
if (count == "0" && SitCode == 60)                         // não há pedido
    → updateCotacao + CriaPedido                           // não olha `alterado`

else if (count != "0" && SitCode == 60 && alterado != "Y")  // ← 'N'
    → GetStatusAtualPedido (U_INO_VERSAOWBC do ORDR)
      se versão do WBC > versão do pedido → UpdatePedido

if (count != "0" && SitCode == 60 && alterado == "Y")       // ← 'Y'
    → ChecaPN && !ChecaPNPedido → CancelaPedido + CriaPedido(PNNew)
```

O campo separa **dois caminhos mutuamente exclusivos**: `'N'` é a condição do
caminho da revisão, `'Y'` desvia para a troca de PN. Congelar em `'N'` deixava
a atualização por revisão sem caminho nenhum — com `'Y'` a decisão cai no ramo
da troca e retorna antes de chegar à comparação de revisão.

**Quem congela o pedido**, então, são dois campos, e nenhum é o `Update`:

1. `U_INO_VERSAOWBC` no próprio `ORDR` — a revisão carimbada no pedido. É o
   `revisao_pedido_sap` aqui, e era a intuição do usuário ao pedir o legado.
2. `DocStatus = 'C'` — pedido fechado. Divergência nossa; o legado não olhava.

A regra `pedido_congelado_sem_alteracao` foi removida.
`TestUpdateDesviaEntreDoisCaminhos` fixa os dois caminhos, e um dos testes
falha se o congelamento por `Update` voltar.

**O que continua valendo, e não mudou:**

* A troca de PN só acontece com `'Y'`, como no legado — aqui por rebote da
  guarda `pedido_corrigido_a_mao` (`PN_Correc` preenchido com `'N'` significa
  correção já aplicada), e não por uma verificação direta do campo.
* A integração **baixa** `U_INO_Update` para `'N'` ao vincular o pedido, no
  mesmo PATCH do estágio. Como a troca termina em cancelar-recriar-vincular, o
  campo volta sozinho para `'N'` depois do pedido novo — que é exatamente o que
  o usuário pediu. Verificado: o PATCH do vínculo de **pedido** leva
  `U_INO_Update: 'N'`; o de cotação não leva o campo.

**Lição de método, e o motivo de a seção ficar:** eu propus uma mudança de
regra de negócio a partir da minha leitura do campo, sem reler o legado. O
usuário pediu a árvore de decisão antes de responder, e foi isso que pegou a
inversão. Regra nova sobre campo herdado: ler o legado primeiro, sempre.

## `U_INO_Update = 'N'` congela o pedido

Confirmado pelo usuário em resposta direta: *"`Update = 'N'` deveria congelar o
pedido nesse caso -> SIM"*.

Até aqui a decisão de atualizar o pedido olhava **só a revisão**: WBC em `C`,
pedido no SAP em `B`, atualiza — independentemente do `U_INO_Update`. Era o
comportamento do legado (`Program.cs`), então não era regressão do porte; mas
significa que a integração reescrevia pedido que ninguém pediu para mexer.

Agora são duas condições: revisão mais nova **e** `U_INO_Update = 'Y'`. Revisão
nova e alteração pendente são coisas diferentes — a revisão pode subir por
motivo que não muda o que o pedido precisa levar, e quem afirma que há alteração
a aplicar é o `U_INO_Update`. Nova regra no acompanhamento:
`pedido_congelado_sem_alteracao`.

**Onde o campo mora** (corrigido pelo usuário depois desta decisão): `U_INO_Update`
é um UDF da *oportunidade no SAP* — `SalesOpportunities` no Service Layer,
`OOPR` no HANA —, e não um campo do WBC. A integração o lê junto com o resto da
oportunidade e o **baixa para `'N'` ao vincular o pedido**, no mesmo PATCH do
estágio, como o legado (`ServiceProcess.cs:198` e `:210`). Isso fecha o ciclo:
alguém marca `'Y'` quando há alteração a aplicar, o pedido é gravado, a marca
cai — e a guarda nova volta a valer na revisão seguinte.

**Três coisas que a guarda deliberadamente não faz:**

1. **Não impede a criação** do pedido. SitCode 60 sem pedido nenhum continua
   criando — congelar é sobre alterar o que existe, e o contrário deixaria o
   orçamento sem pedido para sempre.
2. **Não impede a troca de parceiro.** A guarda roda **depois** de
   `troca_de_parceiro_pendente`, porque troca não é alteração de valores. Exigir
   `alterado` para trocar já foi um engano meu uma vez, e fez a troca deixar de
   acontecer.
3. **Não toca no ramo da cotação.** Em SitCode 40/55 o pedido nem é olhado: lá a
   revisão nova cancela e recria a *cotação*, e isso não mudou.

Com `PN_Correc` preenchido e `Update = 'N'`, quem decide continua sendo
`pedido_corrigido_a_mao` — mensagem mais específica ("já está certo em nome de
outro parceiro"), e por isso vem antes. As duas congelam; o histórico diz qual
foi.

**Medido em produção antes de aplicar**, porque a regra passa a depender de um
campo cujo `'Y'` vem de fora desta integração:

| | |
|---|---|
| Oportunidades com pedido vigente e `Update = 'N'` | 2.566 |
| Oportunidades com pedido vigente e `Update = 'Y'` | **8** |
| Orçamentos em `atualiza_pedido` na janela de hoje | **0** |

O campo está vivo — há `'Y'` no ambiente —, e o efeito imediato é nulo: nenhum
orçamento da janela cai em `atualiza_pedido` hoje. **Risco que fica aberto:** se
nada marcar `Update = 'Y'` na oportunidade quando uma revisão é publicada, a
atualização de pedido deixa de acontecer em silêncio. A pergunta não é "o WBC
grava?" e sim "**quem** grava o `'Y'` nesse UDF do SAP, e em que momento?" — a
integração só o baixa. É a mesma dúvida já registrada em `RETOMADA.md` sobre o
`PN_Correc`, que mora na mesma oportunidade.

## `ORDR.U_INO_Congelado` — a regra que faltava no porte

Pedida pelo usuário depois de identificar o campo no legado. A integração não o
lia, e a lacuna tinha efeito destrutivo esperando o primeiro ciclo.

**O que o legado faz** (`ServiceProcess.cs`, dentro de `UpdatePedido`):

```csharp
DocCot.GetByKey(DocEntryPedido);
DocCot.UserFields.Item("U_INO_ORCAMENTO").Value = ...;
DocCot.UserFields.Item("U_INO_VERSAOWBC").Value = versao;      // 583 — fora do if

Congelado = DocCot.UserFields.Item("U_INO_Congelado").Value;   // 585

if (Congelado != "Y")                                          // 597
{
    ... apaga TODAS as linhas e reconstrói a partir do orçamento ...
}

status = DocCot.Update();                                      // 675 — depois do if
```

A regra é: **`'Y'` protege as linhas do pedido, e nada mais.** O cabeçalho é
atribuído antes do `if` e o `Update()` vem depois dele, então um pedido
congelado continua recebendo o `U_INO_VERSAOWBC` novo.

Esse detalhe não é cosmético. É o que impede o orçamento de ser escolhido de
novo a cada ciclo: sem o carimbo, a revisão do WBC seguiria eternamente mais
nova que a do pedido, e a integração tentaria atualizá-lo para sempre,
consumindo o teto de escrita a cada volta. Foi o motivo de portar a regra como
"manda o cabeçalho, omite as linhas" em vez de "pula a ação".

**Medido em produção antes de implementar** — e o número decidiu o peso da
tarefa:

| `U_INO_Congelado` | `DocStatus` | Pedidos vigentes |
|---|---|---|
| `'Y'` | `'C'` (fechado) | 2.461 |
| `'Y'` | `'O'` (aberto) | **120** |

Não há um único pedido com `'N'`, vazio ou nulo. Ou seja: no legado, o bloco
que refaz as linhas é código morto em produção — **atualização de pedido nunca
refez linha**. Sem esta guarda, a integração destruiria a edição manual dos 120
pedidos abertos no primeiro ciclo que os alcançasse.

**Tradução do campo:** só `'Y'` congela, seguindo `if (Congelado != "Y")` ao pé
da letra. Ao contrário do `DocStatus`, aqui **não** se falha congelado no valor
desconhecido: seria parar de atualizar linhas num caso que o legado atualiza, e
a divergência não apareceria em lugar nenhum.

**Onde ficou:** `esta_congelado` no protocolo de documentos, respondido pelas
duas fontes (Service Layer e a consulta do HANA), e
`ProcessadorDeOrcamento._respeitar_congelamento` removendo `DocumentLines` do
payload. A criação e a troca de PN não consultam o campo — pedido que acabou de
nascer não tem linha a proteger, e o legado também não consulta ali.

O campo viaja **só no pedido**: é UDF do `ORDR`, e dar à cotação uma chave vazia
sugeriria um campo que ela não tem.

## `PAINEL_HOST`: expor o painel na rede é escolha escrita

Pedido do usuário: acessar o painel de outras máquinas, em
`http://192.168.1.70:8501`.

O `--host` já existia, mas o painel roda como serviço — uma escolha que vive só
no parâmetro de invocação se perde no primeiro reinício, e ninguém descobre até
alguém reclamar que a tela não abre. Por isso `PAINEL_HOST` e `PAINEL_PORTA` no
`.env`, com a linha de comando ganhando da configuração para um teste pontual.

O padrão continua `127.0.0.1`. Expor tem de ser escolha escrita, porque **o
painel não tem autenticação**: quem alcança a porta lê tudo, registra pedido de
reprocessamento em nome de quem quiser e dispara os comandos de leitura,
incluindo a simulação de ciclo — que toma a trava de execução única e roda por
minutos. Os comandos que escrevem no SAP continuam indisponíveis enquanto o
alvo for produção, e isso não mudou.

`painel_exposto` testa "não é loopback" em vez de comparar com `"0.0.0.0"`:
fixar o IP da máquina na rede (`192.168.1.70`) expõe do mesmo jeito, e a
comparação por igualdade deixaria esse caso passar calado.

## `204 No Content` não prova que as linhas mudaram

Relatado pelo usuário: as cotações `00125616` e `00125577` ficaram com valores
menores que os do WBC. A investigação levou a um defeito de silêncio, não de
cálculo.

**O que estava errado:** a primeira linha das duas cotações estava a preço zero
— faltavam R$ 1.604,38 e R$ 39.802,60 nos documentos que vão ao cliente.

**O que foi apurado, em ordem:**

1. Nosso montador de linhas está certo. Reconstruído o payload com os dados de
   hoje, sem enviar, sai `Price 1604.38` e `Price 39802.6` nas linhas do
   ORCITM 1.
2. As duas cotações foram **criadas pelo legado em 02/09**; nós só as
   atualizamos em 03/09 às 11:44 (`PATCH Quotations(102196)` e `(102200)`).
   A linha zerada já existia.
3. O padrão é antigo e amplo: **1.328 cotações** da integração têm linha a
   preço zero, de 02/08/2023 até hoje. Nós escrevemos em 11 cotações ao todo.
4. **Toda** linha zerada é `TreeType = 'P'` — linha-pai de árvore de produto —
   e nenhuma linha `'N'` está zerada. Mas `'P'` não zera sozinho: 991 linhas
   `'P'` têm preço.

**O defeito que é nosso:** o `PATCH` foi aceito (`204 No Content`), o cabeçalho
gravou, e **as linhas continuaram como estavam**. Mandamos
`B1S-ReplaceCollectionsOnPatch: true` e ainda assim a coleção não foi
substituída nesses documentos. A integração registrou sucesso, e o erro chegou
ao usuário pelo documento — não pelo painel.

**A prova de que criar funciona:** o usuário excluiu a cotação `77997` à mão; a
integração criou a `78031` no ciclo seguinte, com as duas linhas `'P'` e ambas
com preço — total idêntico ao WBC. A `00125577` continua errada porque nada foi
recriado nela.

**A correção.** Depois de atualizar uma cotação, a integração relê o documento e
soma `Quantity * Price` das linhas (antes do imposto, como `total_do_payload` —
`DocTotal` inclui imposto e não serviria). Divergindo além de um centavo, a
cotação é **cancelada e recriada** com o mesmo payload — o caminho que
comprovadamente funciona.

Quatro decisões dentro disso:

* **Só cotação.** O pedido tem guardas próprias (`U_INO_Congelado`,
  `DocStatus`) e recriá-lo tem outro peso.
* **Um centavo de folga.** O SAP arredonda por linha; as divergências reais
  foram de milhares de reais.
* **A conferência não derruba o ciclo.** Se a releitura falhar, o log diz que a
  atualização ficou sem conferir e o worker segue.
* **O histórico registra o motivo.** Um documento cancelado e refeito sem
  explicação é pior do que o defeito.

Fica em aberto, para o cadastro: `I000001` e `I000003` são árvores de produto no
`OITT`. Se devem mesmo ser, vale entender por que a linha-pai às vezes nasce sem
preço — a correção aqui trata o efeito, não a causa no SAP.

## Horário de trabalho do worker (06:30–19:00) e janela dirigida

Dois pedidos do usuário, na mesma virada operacional.

### `--orcamento` usa uma janela própria, de 12 meses

`ciclo --orcamento` aplicava a janela do ciclo (6 meses). Um orçamento mais
antigo simplesmente não era encontrado, e o comando terminava com
"0 avaliado(s)" e **código 0** — um nada silencioso com cara de sucesso.

A janela existe para limitar o que é varrido *sem ninguém pedir*. Pedir um
orçamento pelo número é o oposto: alguém sabe qual quer e está esperando. Agora
`--orcamento` usa `MESES_DE_JANELA_DIRIGIDA` (12 por padrão), no ciclo **e** na
prévia — uma prévia com janela menor diria "nada a fazer" sobre um orçamento que
o ciclo seguinte vai atualizar, e é a prévia que autoriza rodá-lo.

E quando nada é encontrado com `--orcamento`, os dois comandos **avisam**, com o
número, a janela e o corte. O silêncio era metade do problema.

### O worker só trabalha das 06:30 às 19:00

`WORKER_HORARIO_INICIO` / `WORKER_HORARIO_FIM`. Fora do horário o processo segue
vivo e não executa ciclo.

Quatro decisões:

1. **A guarda fica no ciclo agendado, não em `executar_ciclo`.** `wbcpython
   ciclo` é alguém pedindo, e recusar um pedido explícito por causa do relógio
   seria obstrução, não proteção — foi rodando `ciclo` fora de hora que os
   defeitos desta semana foram investigados.
2. **O primeiro ciclo, que é imediato, passa pela mesma guarda.** Subir o worker
   às 22h não pode ser a porta dos fundos.
3. **As pontas são inclusivas** e a janela decide se o ciclo **começa**. Um
   ciclo iniciado 19:00 vai até o fim: interromper no meio é como se cria
   documento sem vínculo (ver `00125535`).
4. **O aviso sai uma vez por transição.** Com ciclo de 3 minutos, avisar a cada
   volta encheria a noite com ~230 linhas iguais, e log que se repete assim
   deixa de ser lido justamente quando importa.

`fim` antes de `inicio` é lido como turno que cruza a meia-noite. Não é o caso
hoje; está implementado porque a alternativa — um worker que nunca roda e nunca
diz por quê — seria uma armadilha silenciosa.

### E não roda sábado e domingo

`WORKER_DIAS_DE_TRABALHO=1,2,3,4,5` (ISO: 1 = segunda, 7 = domingo). Pedido do
usuário, na mesma linha do horário.

É uma **lista**, e não um `apenas_dias_uteis` booleano: escala de sábado e
feriado são a mesma pergunta com resposta diferente, e a bandeira booleana
obrigaria a mexer em código no dia em que a resposta mudasse.

Três decisões:

1. **O dia é verificado antes da hora.** Num sábado às 10h, "fora do horário"
   estaria tecnicamente errado e mandaria quem lê o log procurar no lugar
   errado.
2. **O estado guardado é o motivo, não um booleano.** Sexta 19:01 para por
   horário; sábado 08:00 para por ser sábado. Com um "está parado" booleano, o
   segundo aviso seria engolido, e quem abrisse o log na segunda não saberia por
   que a integração passou o fim de semana quieta.
3. **Dia fora de 1–7 e lista vazia são recusados na partida.** Um `8` ignorado
   em silêncio viraria um worker que não roda num dia e ninguém sabe por quê;
   uma lista vazia, um worker que nunca roda parecendo estar rodando.

Os nomes dos dias estão escritos no código em vez de virem do `strftime('%A')`:
`%A` depende do locale do processo, e o worker roda como serviço, onde o locale
é o que o sistema deu — o log sairia com "Saturday" no meio do português.

**O defeito que os testes deixaram passar, e o que ele ensina.** O campo nasceu
tipado como `frozenset[int]`. Com esse tipo, o `pydantic-settings` classifica o
campo como complexo e tenta `json.loads("1,2,3,4,5")` **antes** de qualquer
validador — `SettingsError` na partida. Os onze testes passavam porque todos
construíam `Settings(...)` com o valor já pronto, o que não passa pela fonte do
`.env`. Quem pegou foi rodar no ambiente real, na conferência final.

O campo passou a ser **texto**, com a conversão numa propriedade
`dias_de_trabalho`, e entraram três testes que leem um `.env` de verdade —
inclusive um com espaços e fora de ordem (`"5, 1 ,3"`), que é como alguém edita
o arquivo à mão. A lição vale além deste campo: teste de configuração que
constrói o objeto com o valor pronto não exercita o caminho que a produção
usa.

O intervalo entre ciclos foi para **180s** no `.env` de produção (era 300s).
Medido no log: um ciclo sobre 1.650 oportunidades leva de 11 a 16 segundos, então
o worker fica ocioso ~92% do tempo.

## A busca da lista troca a lista, e não o bloco que contém o campo

Relatado pelo usuário: digitando no campo de busca, o cursor saía da posição a
cada tecla.

A causa era o alvo do formulário. Ele fazia `hx-target="#conteudo"`, que é o
bloco inteiro — **inclusive o próprio formulário**. A cada busca o campo era
destruído e recriado, e o navegador não tem como devolver foco a um elemento
que deixou de existir.

Reproduzido em navegador, digitando `00125607` letra a letra no código antigo:

```
digitou 0 → valor="0" cursor=0 focado=false
digitou 0 → valor="0" cursor=0 focado=false     ← nada mais entra
```

Depois do conserto, o mesmo teste:

```
digitou 0 → valor="0"        cursor=1 focado=true linhas=40
digitou 7 → valor="00125607" cursor=8 focado=true linhas=1  (1 orçamento(s))
```

**A correção não restaura o foco — ela evita perdê-lo.** O formulário passou a
mirar `#lista-oportunidades`, um invólucro que contém só a tabela. Fora do alvo,
o campo não é tocado: foco e posição do cursor continuam sendo do navegador, e
não algo que a gente reconstrói depois de cada resposta. Restaurar foco à mão
funciona até o dia em que a resposta demora e a pessoa já digitou mais.

Dois detalhes que o conserto exigiu:

* **O `id` fica no invólucro, não na tabela.** Ele precisa existir também
  quando a busca não acha nada; senão o htmx perderia o alvo e a tecla seguinte
  não filtraria mais.
* **A contagem viaja por `hx-select-oob`.** O "N orçamento(s)" mora no
  cabeçalho, fora do alvo — sem isso a lista filtraria e o número continuaria o
  antigo, um painel se contradizendo na mesma tela.

Os testes de `test_web.py` fixam a estrutura; nenhum deles enxerga foco, porque
foco é posição na tela e quem verifica isso é o navegador.

## Encerramento não se repete: a guarda olhava a cotação sem motivo

Relatado pelo usuário no `00123425`: o log de decisão trazia
`marcar_oportunidade_perdida` a cada ciclo, com a oportunidade **já** marcada.

Estado real, lido em produção:

```
Status = 'L'  (já perdida)      U_INO_StatusWBC = '99'  (já espelhado)
cotação: DocNum 49410 (aberta)  pedido: DocNum 84337 (fechado)
--> regra: marca_perdida   ações: ['marcar_oportunidade_perdida']
```

A guarda existia, mas exigia **duas** condições:

```python
if estado.oportunidade_encerrada and not estado.tem_cotacao:
```

`tem_cotacao` não tinha o que fazer ali. Com cotação vinculada, a guarda não
pegava; e como havia pedido, a cotação também não era cancelada (o
cancelamento no encerramento só vale sem pedido). Sobrava o PATCH repetido: a
cada 3 minutos, a mesma oportunidade recebendo o mesmo `sos_Missed`, gastando
teto de escrita e enchendo o histórico com trabalho que não existe.

A condição passou a ser só `oportunidade_encerrada`. Duas coisas continuam
valendo, e há teste para cada uma:

* **O cancelamento da cotação continua acontecendo** quando cabe — ele é
  registrado antes da guarda, que pula só a marcação.
* **Um `U_INO_StatusWBC` desatualizado ainda é espelhado.** O espelhamento no
  fim de `_ramo_pedido_e_encerramento` só é suprimido quando a marcação
  acontece; sem ela, ele volta a cobrir esse caso.

Medido na janela de produção (1.668 oportunidades): **2** estavam nesse estado
— `00123425` e `00125645`. Pouco em volume e permanente em duração: sem a
correção, essas duas seriam reescritas a cada ciclo, para sempre.

Cinco dos sete testes novos reprovam com a guarda antiga.

## `U_INO_Update = 'Y'` antes de existir pedido: ele nasce no `PN_Correc`

Pedido do Marcelo em 09/09/2026, ao revisar as regras contra o WBCPython original:
"faltou a regra de atualizar o pedido quando `U_INO_Update = 'Y'`: alterar o
parceiro de negócios do pedido". A revisão mostrou duas coisas distintas.

### 1. A troca existe e dispara — quem recusa é o SAP, para o usuário `orcaview`

O caso vivo era o `00125572`: pedido 84357 criado pelo worker às 08:43 no parceiro
da oportunidade (`C007515`), o operador preencheu `PN_Correc = C004584` e
`Update = 'Y'` entre 08:45 e 09:00, e desde 09:00 **a cada ciclo** a decisão é
`troca_de_pn` (cancela e recria) e o SAP responde:

```
HTTP 400 | SAP -1116 | POST Orders(19792)/Cancel
(1996) Cancelamento de Pedido de vendas não permitido para o seu usuário!
```

`-1116` é o código com que o `SBO_SP_TransactionNotification` bloqueia uma
transação; `1996` é o número da regra dentro dele. Não é autorização padrão do
B1: `orcaview` e `financeiro04` são os dois `SUPERUSER = 'Y'` na `OUSR`. É uma
lista de usuários dentro do procedimento — e o `orcaview` (usuário do Service
Layer desde a virada de 08/09) não está nela. Medido no `ORDR`: desde 06/2026,
101 cancelamentos de pedido pelo `financeiro04` (o legado e o worker antigo,
incluindo as duas trocas de 08/09: 84345 e 84347), zero pelo `orcaview`.

A integração está certa; o ambiente mudou de usuário. Saída: incluir o
`orcaview` na regra 1996 (quem mantém o `SBO_SP_TransactionNotification`), ou
voltar o `SL_USERNAME` do bloco WBC para `financeiro04`. Até lá o ciclo repete a
tentativa a cada 3 minutos — inofensivo (cancela **antes** de criar, então nada
é criado) e visível no painel como "Com erro".

### 2. O que faltava de verdade: o pedido que nasce depois da correção

A troca só olha pedido **existente** (`troca_de_parceiro_pendente` exige
`tem_pedido`), e a criação usava sempre o parceiro da oportunidade — como o
legado (`CriaPedido(oRs.Fields.Item(1))`). Se o operador corrige o parceiro
**antes** de o orçamento chegar ao SitCode 60, a sequência era:

1. pedido criado no parceiro antigo;
2. o vínculo do pedido baixa `U_INO_Update` para `'N'` (regra "A baixa de
   `U_INO_Update`, decidida");
3. passada seguinte: `PN_Correc` + `'N'` = `pedido_corrigido_a_mao` — congelado.

Pedido no parceiro errado, para sempre, sem erro nenhum. O legado tinha o mesmo
buraco.

Agora `EstadoIntegracao.nasce_no_parceiro_corrigido` (`not tem_pedido and
alterado and parceiro_novo`) faz o pedido **nascer** no `PN_Correc`, sem
cancelar nada — regra `cria_pedido_no_pn_corrigido` no histórico. É a mesma
frase da troca, dita para o outro caminho: **`Update = 'Y'` manda o pedido para
o `PN_Correc`.** Vale também:

* a guarda `ChecaPN` (`_parceiro_existe`): parceiro corrigido inexistente →
  **não cria** e registra o motivo. Criar "enquanto isso" no parceiro da
  oportunidade reproduziria o buraco acima;
* o contato da oportunidade fica de fora (é do parceiro antigo);
* a cotação continua no parceiro da oportunidade, como sempre;
* `Update = 'N'` com `PN_Correc` na criação continua criando no parceiro da
  oportunidade — é o dado residual das 578 oportunidades.

Sobre "atualizar" o parceiro em vez de cancelar e recriar: o B1 não aceita
trocar o `CardCode` de um pedido já gravado, por isso o legado cancela e recria,
e aqui também. Testes: `TestParceiroDoPedido`, `TestTrocaDeParceiroDeNegocios`
(domínio) e `TestPedidoNasceNoParceiroCorrigido` (processador).

### Complemento (09/09, 10:55): a "regra 1996" é o UDF `OUSR.U_INO_CancelaPedido`

Não é autorização padrão do B1 (o Marcelo deu permissão ao usuário e o erro continuou). A
`OUSR` tem quatro UDFs: `U_INO_RepCod`, `U_INO_CancelaPedido`, `U_INO_LiberaEntrega`,
`U_INO_AlteraPeso`. Lido em produção: `U_INO_CancelaPedido = 'S'` em 4 usuários (`manager`,
`financeiro01`, `financeiro04` e mais um) e `'N'` nos outros 105 — o `orcaview` entre eles. É
essa lista que o `SBO_SP_TransactionNotification` consulta antes de deixar cancelar um pedido.
Correção: `U_INO_CancelaPedido = 'S'` no usuário `orcaview` (tela de Usuários do B1, campos
definidos pelo usuário). Vale já no ciclo seguinte: o worker faz login novo a cada ciclo. Sugerido
também `U_INO_AlteraPeso = 'S'` (o `financeiro04` tinha, e o pedido leva peso nas linhas).
Cancelar **cotação** o `orcaview` já pode (3 canceladas por ele desde 01/09).

**Decisão de negócio confirmada (Marcelo, 09/09):** sem pedido e com `PN_Correc` preenchido, o
pedido novo só nasce no `PN_Correc` com `Update = 'Y'`; com `'N'` nasce no parceiro da
oportunidade. É o que o código já faz (`nasce_no_parceiro_corrigido` exige `alterado`). Caso do
dia: `00125225` — o 84355 (no parceiro corrigido) foi cancelado à mão pelo `financeiro04` às 08:38,
o worker recriou às 10:33 com `Update = 'N'` no parceiro da oportunidade (84360), e a troca pedida
depois esbarrou na regra 1996.

**Aplicado em 09/09 11:40** pelo Service Layer, com o próprio `orcaview` (que, pelo SL, é
`Superuser = tNO` — a autorização de editar o cadastro bastou): `PATCH Users(144)`
`{"U_INO_CancelaPedido":"S","U_INO_AlteraPeso":"S"}` → 204, conferido no `OUSR`. O objeto
`Users` do SL é `OpenType` e expõe os UDFs do `OUSR`; a PATCH funciona como em qualquer
entidade (UDFs no metadata desde o 9.1 PL05). `maintenance/liberar_cancelamento_orcaview.py`
faz o mesmo a partir da .11, se precisar repetir noutro usuário.

## Janela sob demanda: o teto escalonado, e a pergunta quando nem ele basta

**Problema (11/09/2026).** `MESES_DE_JANELA` vivia só no `.env`, lida no arranque e congelada
em `self._meses`. Ir de 6 para 24 meses — o que vendas precisa para alcançar uma oportunidade
antiga — exigia editar o `.env` na .11 e reiniciar o serviço. Pedido do Marcelo: virar um campo
na tela, com padrão de 6, teto de 24, valendo para a próxima passada e voltando sozinho.

**O furo do desenho ingênuo.** "Abre 24 meses por um ciclo e volta" não funciona: o
`LIMITE_DE_ESCRITA_POR_CICLO` (200) corta o ciclo no meio, e com ~1.785 oportunidades já na
janela de 6 meses, 24 meses represa muito mais. O ciclo escreveria 200, a janela voltaria a 6, e
o resto nunca seria escrito — o pedido se gastaria sem ter feito o que foi pedido.

**Decisão do Marcelo.** O teto de escrita cresce por banda, e o ciclo pergunta quando o estoura:

| Janela pedida | Multiplicador | Teto no ciclo |
| --- | --- | --- |
| até 6 meses (padrão) | 1× | 200 |
| 7 a 12 | 3× | 600 |
| 13 a 18 | 6× | 1.200 |
| 19 a 24 | 9× | 1.800 |

Quarta banda em **9×** (progressão aritmética), não 12× (dobra): passo constante mantém o pior
caso previsível para quem mexer nos números depois. Acima de tudo isso há um teto duro
(`TETO_ABSOLUTO_DE_ESCRITA`, 2.000), que é a rede para o dia em que alguém aumentar a
`JANELA_MAXIMA` sem refazer a conta.

**Os três estados** (`domain/janela.py`), e por que são três e não dois: `ARMADO` é "a próxima
passada usa a janela larga"; `AGUARDANDO` é "um ciclo largo já rodou, estourou o teto e espera
resposta". Sem a separação, o worker continuaria varrendo 24 meses a cada 180 s enquanto a
pergunta espera — o ciclo pesado rodando sozinho, que é o que o desenho existe para evitar. Em
`AGUARDANDO` os ciclos automáticos voltam ao padrão.

**Prazo da pergunta: 15 minutos** (Marcelo, 11/09). Vencido, a janela volta ao padrão e o log
registra onde o ciclo parou — expirar calado apagaria justamente o que alguém leria para decidir
se rearma. `ARMADO` **não** expira por tempo: um pedido feito às 19h de sexta só é atendido na
segunda, porque o worker não roda fora do expediente.

**O que não consome o pedido:** o ensaio (`--simular`) e o `--orcamento`. O ensaio **usa** a
janela estendida de propósito — é com ele que se vê quantos documentos o ciclo criaria —, e
consumir ali faria "conferir antes" ser a maneira de perder o pedido. O `--orcamento` roda na
janela dirigida, que é outra coisa. Erro no ciclo também não consome: conta uma tentativa, e na
terceira devolve (queda de rede não pode custar o pedido; erro teimoso não pode virar ciclo
pesado a cada intervalo).

**Onde o pedido mora:** tabela `pedido_de_janela` do acompanhamento, uma linha (`id=1`). Painel e
worker são serviços NSSM separados, sem memória compartilhada, e o acompanhamento é o único
lugar que os dois já tocam — a tabela `travas` ao lado já coordena os dois pelo mesmo caminho. O
`state/wbc_worker.stop`, o outro precedente de sinal painel→worker, carrega um bit; aqui é
preciso valor, dono, data, contador e prazo, e tudo isso precisa aparecer na tela.

**Senha:** armar e "rodar outro ciclo" exigem `PAINEL_SENHA`; **limpar não**. Armar não escreve
no SAP com as próprias mãos, mas é a causa direta de até 1.800 escritas irreversíveis. Frear só
reduz o que o próximo ciclo escreve — exigir senha ali transformaria a proteção em obstáculo no
botão que alguém aperta quando se assustou com o número.
