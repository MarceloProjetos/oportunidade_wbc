# Aprendizados sobre os ambientes reais

> **Documento histórico do WBCPython standalone (até 08/09/2026).** O projeto virou o pacote
> `wbcpython/` do ServidorIntegracaoSAP: onde se lê `uv run wbcpython …`, hoje é
> `python -m wbcpython …` na raiz do repositório; `uv sync` é `pip install -r requirements.txt`;
> `python/WBCPython` é esta raiz; `ai_spec/` está explicado em `ai_spec/00_index.md`.
> Como rodar hoje: [`README.md`](README.md) desta pasta.

Fatos descobertos **rodando contra SAP e WBC de verdade**, não lendo
documentação. Cada um custou uma execução que falhou; estão aqui para não
custarem duas vezes.

---

## SAP Service Layer

### Nomes de propriedade não são nomes de coluna

A entidade expõe nomes próprios. Usar o nome da coluna do banco devolve
`HTTP 400 — Property X is invalid`.

| Coluna no banco | Propriedade no Service Layer |
|---|---|
| `OpprId` | **`SequentialNo`** |
| `OpenDate` | **`StartDate`** |

### Tabela de usuário precisa do prefixo `U_`

O de-para `@INO_GRP_PRODUTOS` é lido em `/b1s/v1/**U_**INO_GRP_PRODUTOS`.
Sem o prefixo: `-1002 Service Not Found`. É a convenção para tabelas de usuário
não registradas como UDO.

### O que uma Cotação exige para nascer

| Campo | Erro se faltar |
|---|---|
| `CardCode` | `-2028 Customer record not found` |
| `DocumentLines` (≥ 1) | `-5002 Document total value must be zero or greater than zero` |
| `BPL_IDAssignedToInvoice` | `-5002 Specify an active branch [OQUT.BPLId]` |

**Atenção:** dois erros completamente diferentes compartilham o código `-5002`.
Ler só o número engana; a mensagem é que diz o que falta.

### Vínculo documento ↔ oportunidade

O vínculo vive em `SalesOpportunitiesLines`, e o Service Layer **substitui a
coleção inteira** num PATCH — a coleção existente precisa ser lida e o novo
estágio **acrescentado**, nunca enviado sozinho.

Três detalhes:

* `DocumentNumber` recebe o **`DocEntry`**, não o `DocNum`. O nome do campo diz
  o contrário. Confirmado nos vínculos reais: a oportunidade `5681` aponta para
  `9486`, que é o `DocEntry` da cotação — cujo `DocNum` é `4948`.
* `MaxLocalTotal` precisa ser **maior que zero**, senão
  `-5002 In "Potential Amount" field, enter number greater than 0 [OOPR.MaxSumLoc]`.
* `PercentageRate` vai zerado.

### Filiais em homologação

| BPLID | Nome | Situação |
|---|---|---|
| 1 | Altamira Indústria Metalúrgica Ltda. | ativa |
| 2 | Principal | desabilitada |

Por isso `FILIAL_PADRAO = 1` — o mesmo valor que o legado fixava no código.

### Peso da linha vem do cadastro do item

`Weight1 = 1.0` não é enviado pela integração: o SAP copia do item
(`SalesUnitWeight = 1.0` nos itens `I00000x`). As cotações do legado têm o
mesmo valor.

### Sessão e erros

* Um `200` que não seja JSON é sintoma de `SL_BASE_URL` errado, proxy ou portal
  cativo — não de bug no parser.
* O código SAP `-304` (sessão expirada) pode vir **sem** HTTP 401. A renovação
  de sessão precisa ser decidida pelo código do erro, não só pelo status HTTP.
* O certificado é autoassinado; a conexão exige `SL_VERIFY_SSL=false` ou um
  `SL_CA_BUNDLE` próprio.

---

## Banco do WBC (SQL Server, `WBCCAD`)

### `INTEGRACAO_ORCITM` está morta — não use

O nome diz "itens" e a estrutura é convincente, mas a tabela **parou de receber
dados**: o maior `ORCNUM` nela é `00125478`, enquanto os orçamentos em
processamento já passam de `00125535`.

Lendo dela, todo orçamento recente vem **sem nenhuma linha** — e o SAP recusa o
documento com um erro que não aponta para a causa. Foi o bug mais caro desta
reescrita.

**As linhas vivem em `INTEGRACAO_ORCIMP`**, que é desnormalizada: os campos de
cabeçalho se repetem em cada linha. É de onde o legado sempre leu
(`NovaTabelaQuotLinha`).

Há teste de regressão: o esquema de teste **não tem** `INTEGRACAO_ORCITM`.

### A situação real da proposta está em `INTEGRACAO_ORCSIT`

`INTEGRACAO_ORCLST.SITCOD` é uma cópia que diverge: **8.560 dos 59.265
orçamentos (14%)** têm valor diferente entre as duas tabelas. `ORCSIT` guarda
uma linha por mudança de situação — a atual é a mais recente.

O **sistema legado não conhece essa tabela**: não há uma referência a ela em
todo o C#. Quem seguir só o legado herda o erro.

Dois detalhes que a implementação precisa tratar:

* **1.660 orçamentos não têm linha em `ORCSIT`** — precisam cair para
  `ORCLST.SITCOD`, e não para zero, que os faria parecer "abaixo do mínimo" e
  serem ignorados em silêncio.
* **Há um registro com duas linhas idênticas** (`0000T061`, de 2017): sem
  desempate determinístico, qual delas vence fica a critério do plano de
  execução do banco.

### Colunas que enganam

| Coluna | Realidade |
|---|---|
| `ORCVAL` | é o **total da linha**, não o preço unitário |
| `ORCPRDQTD` | **nula em 100%** das 20.997 linhas |
| `ORCPRDCOD` | vem vazia nos dados reais |
| `REVISAO` | letras `A`–`P`, frequentemente nula |
| `idIntegracao_OrcImp` | é o identificador da linha (não `_OrcItm`) |

### Distribuição de `GRPCOD`

`2` (16.067), `1` (2.904), `8` (700), `7` (572), `6` (491), `9` (170),
`16` (50), `3` (20), `4` (17), `10` (5), `11` (1).

Os 11 códigos estão todos no de-para — cobertura de 100%.

### A árvore de produtos só existe depois que o orçamento vira pedido

`INTEGRACAO_ORCPRDARV` é o detalhamento de engenharia, e é dela que saem as
linhas do snapshot do OrcDetalhe. Ela **não é preenchida na fase de cotação**.
Medido sobre os orçamentos alterados em 2026:

| SitCode | orçamentos | com árvore |
|---|---|---|
| 40 (cotação emitida) | 1.255 | 1 — **0%** |
| 60 (pedido) | 600 | 600 — **100%** |

Daí os dois caminhos de linha do OrcDetalhe: com árvore, o snapshot leva
código, cor, nível, peso e preço unitário; sem árvore, leva só sequência e
texto. O código escolhe **pela existência da árvore, não pelo SitCode** — a
regra é do WBC, e duplicá-la do lado da integração criaria uma segunda fonte de
verdade.

### `INTEGRACAO_ORCPRD` tem peso, mas é esparsa

Tem `ORCPES` preenchido em 92% das suas linhas, mas só cobre alguns orçamentos
(parece ser o detalhamento de engenharia). Nenhum dos pendentes está lá.

### `ApplicationIntent=ReadOnly` não funciona com `pymssql`

O SQLAlchemy repassa o parâmetro ao driver, que rejeita com
`TypeError: connect() got an unexpected keyword argument`. Funciona com
`pyodbc`, não com este driver. A garantia de somente-leitura está no código; a
camada extra recomendada é um usuário `db_datareader` no servidor.

### Senha na URL de conexão

Monte a URL com `URL.create()`, nunca por interpolação. Com string, um `@` na
senha faz o trecho seguinte virar **hostname** (conecta no servidor errado e
vaza parte da senha em qualquer log) e um `%` é lido como escape de URL,
autenticando com senha diferente da real — em silêncio.

---

## HANA (hdbcli, porta 30015)

* `VW_CLIENTE_MUNICIPIO_ALTA` e `VW_EVOL_OPORTUNIDADE_ALT` **só existem em
  `SBOALTAMIRAPROD`**. Em homologação nem privilégio de leitura há.
* Colunas reais de `VW_CLIENTE_MUNICIPIO_ALTA`: `AbsId, Code, Country, State,
  Name, IbgeCode, Name_N`. A busca usa **`Name_N`** — a forma sem acento —
  porque o WBC envia nomes sem acentuação.
* Em `VW_EVOL_OPORTUNIDADE_ALT`, `Retorno`, `Indice` e `Negociacao` são
  **NVARCHAR**, não numéricos. Precisam de normalização.
* O nome do schema não pode ser parâmetro de bind: é validado contra uma lista
  branca e citado (`infrastructure/hana/identificadores.py`).

---

## Legado C# — onde estão as respostas

Quando faltar uma regra, o legado costuma ter a resposta. Pontos úteis:

| Pergunta | Onde |
|---|---|
| Qual item do SAP usar | `Querys.resx → GetItensSAP` (de-para por `GRPCOD`) |
| De onde vêm as linhas | `Querys.resx → NovaTabelaQuotLinha` (lê `INTEGRACAO_ORCIMP`) |
| Como vincular à oportunidade | `ServiceProcess.cs:126 AddCotacaoOportunidade` |
| Filial | `ServiceProcess.cs:298` e `:1149` (`BPL_IDAssignedToInvoice = 1`) |
| Composição da linha | `ServiceProcess.cs:~385` (corta `OrcTxt` no primeiro "Valor") |

O binário compilado (`bin/x64/Debug/WBCServConsole.exe`) pode ser inspecionado
com `strings` — foi assim que se confirmou que o filtro fixo `= '00121819'`
está embutido na versão construída.

---

## Sobre o processo de trabalho

* **Erro em ambiente real não se adivinha.** Quatro exigências do SAP e a
  tabela morta do WBC só apareceram executando. Rodar cedo contra homologação
  vale mais que qualquer leitura de especificação.
* **Antes de escrever, prever.** `wbcpython pendentes` mostra o que um ciclo
  faria usando só leituras. É o passo que separa "achei que ia criar 12
  cotações" de "vi que ia criar 12 cotações".
* **Conferir a premissa nos dados.** A inferência de que o item viria do texto
  livre bloqueou o projeto por duas sessões. Uma consulta de cinco linhas em
  `INTEGRACAO_ORCIMP` teria mostrado que `GRPCOD` estava preenchido.

## O OrcDetalhe já bate com produção — e num campo está melhor

Comparação do orçamento `00125535`: snapshot 513902 (homologação, nossa
solução) contra 513985 (produção, legado). **37 dos 39 campos `U_` do cabeçalho
são idênticos, e as duas linhas batem em todos os 11 campos.**

As duas diferenças:

1. **`U_INO_DATA`** — 26/08 contra 25/08. É a data da captura, e as capturas
   foram em dias diferentes. Não é divergência.

2. **`U_ORCIMP_NEGOCIACAO`** — nós gravamos `'2.0000'`, produção deixou vazio.
   Aqui **produção é que está errada**, não nós.

O segundo caso merece registro porque contraria a intuição de que produção é a
referência. Os números:

* O WBC entrega o dado: `INTEGRACAO_ORCIMP.ORCIMP_NEGOCIACAO = 2.0000` para
  esse orçamento.
* Produção gravou o campo por anos, no mesmo formato de quatro casas — 81.713
  snapshots com valor (`'3.2000'`, `'2.0000'`, `'13.0000'`…).
* E parou de gravar de repente:

  | mês | snapshots | com negociação |
  |---|---|---|
  | 2026-06 | 12.812 | 12.544 |
  | 2026-07 | 5.042 | **169** |
  | 2026-08 | 2.218 | **129** |

De 98% para 3% de um mês para o outro, com o dado continuando disponível na
origem. Isso é regressão em produção, com data aproximada — julho de 2026 —, e
não uma decisão de projeto que precisaríamos copiar.

**Conclusão: nada a ajustar na nossa solução.** A migração restaura um campo
que o legado perdeu pelo caminho. Vale avisar quem cuida do build atual.

Fica também o método, que serve para as próximas comparações: divergência
contra produção **não significa** defeito nosso. Antes de "corrigir" para
igualar, vale perguntar se o dado existe na origem e desde quando produção
mudou de comportamento. Duas consultas respondem, e evitam copiar um defeito.

---

## Medir o raio de impacto antes de virar a chave

Antes de responder "podemos ligar em produção?", vale transformar a pergunta em
número. Deu para fazer sem escrever nada e sem tocar no `.env`: a máquina de
estados é uma função pura, e as leituras (HANA e WBC) aceitam o schema por
parâmetro. Rodando o caminho de decisão em memória contra `SBOALTAMIRAPROD`:

| Janela | Avaliadas | O ciclo agiria | Toca documento |
|---|---|---|---|
| 3 meses | 810 | 33 | 32 |
| 6 meses | 1.631 | 104 | 94 |
| 9 meses | 2.288 | 149 | 129 |

E, dentro dos 33 da janela de 3 meses: **27 cotações canceladas** (R$ 10.363,82)
e **4 pedidos criados** (R$ 170.973,41).

Duas lições:

**A resposta útil não é "sim" ou "não", é a lista do que aconteceria.** "27
cotações canceladas, R$ 171 mil em pedidos" é uma frase que o negócio consegue
aprovar ou recusar. "A integração está pronta" não é.

**A janela é o botão de raio de impacto.** O mesmo código, com o mesmo dado,
escreve 33 ou 149 vezes conforme um número no `.env`. Isso torna
`MESES_DE_JANELA` — que está em aberto desde o começo — uma decisão de risco, e
não de configuração.

## O legado ainda está rodando, e isso muda tudo

A medição respondeu de passagem uma pergunta que ninguém tinha feito: **o
sistema legado continua criando documentos em produção, hoje.** 26 cotações em
01/09/2026, 20 a 30 por dia útil ao longo de agosto.

Isso reordena a lista de riscos inteira. O maior problema de ligar a integração
nova não é nenhum defeito dela — é que passariam a existir **dois processos
escrevendo nos mesmos documentos com a mesma chave** (`U_INO_COTWBC`), com uma
janela de segundos entre a guarda "já existe cotação?" e o `POST`. A trava de
execução única protege contra dois workers nossos; ela não tem como saber do
legado.

O método que revelou isso é barato e vale para qualquer migração: **contar os
documentos criados por dia no sistema antigo**. Se o número não zerou, o sistema
antigo não parou — independentemente do que digam sobre ele.

## O ambiente de homologação é uma cópia da produção

Descoberto ao investigar o peso: o pedido 84112 existe com **`DocNum` e valores
idênticos** nos dois ambientes. Homologação foi restaurada de produção.

Consequência prática que já mordeu: um documento "de homologação" pode na
verdade ser um documento da produção que veio junto no restore. Ao comparar
comportamento, olhar a data de criação — se for anterior ao restore, o documento
é do legado, não nosso.

## Um `<span>` ignora `height`: as barras do painel saíam vazias

O trilho do gráfico aparecia e o preenchimento não. Nenhum erro, nenhum aviso no
console — as barras simplesmente não estavam lá, e num painel de contagens isso
lê como "todas as categorias têm o mesmo valor", que é pior do que não ter
gráfico.

Causa: `.trilho` e `.preenche` são `<span>`, e uma caixa inline ignora `height`.
`display: block` nos dois resolveu.

O aprendizado não é sobre CSS: é que **defeito visual não aparece em teste de
função pura nem em requisição HTTP**. O HTML estava correto, o teste passava, o
fragmento devolvia 200. Só apareceu ao abrir a página e olhar. Vale uma
captura de tela por aba antes de dar por pronto qualquer tela.

## Jinja2: `{% with %}` não propaga escopo para dentro de um `include`

Os dois gráficos da aba "Próximo ciclo" saíam com os dados do primeiro. O
`include` recebe o contexto do template, mas não as variáveis criadas por um
`{% with %}` local.

Macro com argumento nomeado (`{% import %}` + `g.grafico(itens)`) não tem esse
problema, e o teste que garante isso compara um rótulo exclusivo de cada gráfico
— não a contagem de elementos, que passaria mesmo com os dois iguais.

## Starlette exige `python-multipart` mesmo para formulário urlencoded

`await request.form()` levanta `AssertionError` pedindo a biblioteca, ainda que o
corpo seja `application/x-www-form-urlencoded` e não `multipart/form-data` — que
é o que o HTMX envia por padrão. É dependência do extra `dashboard`, não opção.

## `""` casa com `""`: a senha ausente que liberava tudo

A guarda do painel começou como "a senha informada bate com a configurada?".
Numa instalação sem `PAINEL_SENHA` no `.env`, isso vira `"" == ""` — e libera
justamente quem esqueceu de configurar.

O conserto é a guarda de configuração vir **antes** da comparação: sem senha
definida, os comandos de escrita nem existem na tela. É o mesmo princípio de
`safety.py` (configuração incompleta bloqueia, nunca libera), e vale como regra
geral: toda comparação com um segredo precisa primeiro provar que existe um.

## Interromper um comando é `terminate`, nunca `kill`

O ciclo trata o sinal e libera a trava de execução única ao sair. Um `kill`
deixa a trava presa até expirar — 30 minutos — e o worker fica parado esse tempo
todo por causa de um clique num botão de interromper.

Regra que fica: onde há trava com validade, matar o processo é pior do que
esperar. `SIGTERM` dá ao processo a chance de arrumar a casa.

## Teste de concorrência com processo real é teste que às vezes passa

"O primeiro comando ainda está rodando quando o segundo chega" dependia de o
comando real ser mais lento que a linha seguinte do teste. Com `env`, que roda
em um segundo, passava — por sorte.

Um `Popen` de mentira que só termina quando mandam tornou o teste determinístico.
O processo de verdade ficou num teste só, com uma opção que a CLI recusa: prova
processo, arquivo de saída e código de retorno sem sair da máquina.

## O campo que sempre viaja: `CardCode` no PATCH desfazia a correção de PN

`campos_comuns` monta cotação e pedido, e sempre inclui `CardCode` — tem de
incluir, porque na **criação** o SAP recusa sem ele (`-2028 Customer record not
found`). O efeito colateral só aparece na **atualização**: um PATCH de pedido
carrega o `CardCode` junto, mesmo quando a intenção era só atualizar valores.

Com a correção de PN já aplicada, `troca_de_parceiro_pendente` é falsa,
`parceiro_do_pedido` devolve o parceiro da oportunidade, e o PATCH devolve o
pedido ao parceiro errado. O defeito não estava no ramo da troca — estava no
ramo que ninguém associava a parceiro nenhum.

Fica a pergunta a fazer sempre que um payload for compartilhado entre criar e
atualizar: **quais campos são obrigatórios na criação e destrutivos na
atualização?**

## Contar não basta: os 4 casos "pendentes" eram todos estrago antigo

A regra nova pararia 4 trocas que a antiga faria, e parar trabalho legítimo
seria ruim. Foi por isso que valeu **olhar as 4**, e não só contá-las: todas de
2024–2025, com o pedido vivo num terceiro parceiro — nem o da oportunidade, nem
o `PN_Correc`. Resolvidas à mão.

E uma delas mostrou o estrago que a regra antiga é capaz de fazer: o orçamento
`00117039` tem **7 pedidos cancelados** no `PN_Correc` (80502–80508), todos do
mesmo dia, além do original também cancelado. É o ciclo de cancelar-e-recriar
rodando em looping. Ver `DEFEITOS_LEGADO.md`.

Um número agregado teria dito "4 casos deixariam de ser tratados" e escondido
que tratá-los é que era o problema.

## 95% dos pedidos no SAP estão fechados — e a integração não sabia

`DocStatus` não era lido em lugar nenhum, e a integração tentava alterar pedido
fechado sempre que a revisão do WBC fosse mais nova. Parecia caso de borda; ao
contar, **2.452 dos 2.568** pedidos vigentes em produção estão fechados. É o
estado normal de um pedido que foi entregue ou faturado.

A lição não é sobre esse campo: é que **um campo que nunca foi lido é um campo
sobre o qual não se tem hipótese nenhuma**. Não havia como o defeito aparecer
em revisão de código, porque não havia código errado — havia código ausente. Ao
portar um sistema, vale listar as colunas que o legado consultava e conferir uma
por uma se a versão nova as consulta também.

## `bost_Close` e `'C'`: o mesmo fato com dois nomes

O Service Layer chama de `DocumentStatus` e devolve `bost_Open`/`bost_Close`; a
tabela `ORDR` chama de `DocStatus` e guarda `'O'`/`'C'`. A integração lê pelas
duas vias, então o método entrou no **protocolo** de documentos, com uma
implementação para cada fonte — e não como uma comparação dentro da regra, que
teria de conhecer as duas convenções.

Os valores foram conferidos contra o SAP real, não deduzidos: `bost_Close` no
pedido `83988`, junto com `Cancelled = 'tNO'` — fechado e não cancelado são
coisas diferentes, e a consulta já filtrava só a segunda.

## Teste de concorrência que segura a trava por tempo é teste que às vezes falha

`test_concorrencia_real_entre_threads` segurava a trava por `sleep(0.05)` e
exigia que as outras três threads fossem recusadas. Isso só vale se as quatro
disputarem dentro dessa janela. Com a máquina rodando quatro processos ao mesmo
tempo (lint, suíte e duas prévias contra HANA e SAP), a vencedora liberou antes
de as outras chegarem, uma segunda entrou — legitimamente — e o teste acusou um
defeito que não existia.

Duas correções, e a segunda é a que importa:

1. A vencedora só solta a trava depois que as quatro resolveram, o que torna a
   disputa determinística.
2. A asserção passou a ser a **propriedade** — exclusão mútua, medida pelo pico
   de threads simultâneas dentro da trava — e não a coincidência de tempo.

É o mesmo erro do teste de "uma execução por vez" do painel, e a mesma lição:
num teste de concorrência, `sleep` é hipótese sobre o escalonador. Ou se usa um
evento para tornar a ordem determinística, ou se afirma o invariante em vez do
tempo.

## Guarda que falha aberta protege só até o dia em que importa

As duas guardas do pedido nasceram de defeitos reais e foram medidas contra
produção. Ainda assim, a revisão do próprio código achou nelas o mesmo padrão
que causou o defeito original: `esta_fechado` devolvia `False` para status
desconhecido, e `False` libera a escrita.

O ponto não é o valor padrão. É que **a guarda protegia contra o passado e não
contra o futuro**: ela consertava a coluna que faltava, mas continuaria calada se
a coluna faltasse de novo. Numa guarda de escrita, o desconhecido tem de bloquear
— e emitir aviso, senão troca um defeito silencioso por outro.

## Propriedade que depende da ordem do `if` acima dela não é propriedade

`pedido_corrigido_a_mao` devolvia `True` sem pedido nenhum. Não causava defeito
porque o `_decidir_pedido` já tinha saído antes, no ramo da criação — mas isso é
ordem de statement, não invariante. As duas propriedades irmãs verificavam
`tem_pedido`; só ela não.

O teste que eu tinha escrito (`test_sem_pedido_a_guarda_nao_impede_a_criacao`)
validava a **decisão**, e por isso passava. Testar o resultado do fluxo não
substitui testar a propriedade: o fluxo mascarava o defeito.

## Três testes ficaram vermelhos sem uma linha de código mudar

Aconteceu no minuto seguinte à virada para produção. `test_config.py` passou a
falhar na máquina do usuário e continuava verde na nuvem — a diferença era só o
`.env` da máquina ter passado a apontar para `SBOALTAMIRAPROD`.

A causa: `Settings(_env_file=None)` isolava o `Settings` de cima, mas as
configurações **aninhadas** (`ServiceLayerSettings`, `HanaSettings`, …) nascem de
`default_factory`, sem argumento nenhum, e continuavam lendo o `.env` do
diretório. O `_env_file=None` era uma promessa que o código não cumpria.

Passou anos despercebido porque todo `.env` de máquina apontava para
homologação: **o valor que vazava era igual ao esperado**. O teste parecia
isolado e nunca esteve — só nunca tinha sido posto à prova.

Duas lições. A primeira: um teste que muda de resultado conforme a configuração
da máquina é pior do que um teste ausente, porque fica vermelho justamente
quando o ambiente muda, que é quando a suíte mais precisa ser confiável. A
segunda, mais geral: **um vazamento cujo valor coincide com o esperado é
invisível até o dia em que deixa de coincidir**. Vale desconfiar de todo
isolamento que nunca foi testado com um valor diferente — foi o que o teste de
regressão passou a fazer.

