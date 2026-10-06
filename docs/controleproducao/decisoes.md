> **Documento histórico do pacote ControleProducao (montado em 24/09/2026 na máquina do
> Anderson, dono da aplicação).** Desde 28/09/2026 o código mora em `controleproducao/`
> do ServidorIntegracaoSAP: onde se lê `python -m app.cli …`, hoje é
> `python -m controleproducao …` na raiz do repositório; `python_app/app/` é
> `controleproducao/`; o `.env` é o do SIS (mesmos nomes); `.venv`, `scripts/00-06`,
> `config/log_config.json` e as `wheels/` não existem mais (serviço `OrcaView-ControleProducao`,
> `install_wbc_services.bat`, `deploy_update.bat`). Plano: `PLANO_CONTROLE_PRODUCAO_11.md (removido em 2026-10-06; historico no git)`.

# Decisões do Projeto — ControleProducao (Addon SAP B1 → Python)

> Registro enxuto de **decisões tomadas** ao longo do projeto, extraídas do `migration_guide.md`
> (que continua sendo a fonte completa, com todo o raciocínio, código citado e alternativas
> descartadas). Este arquivo é só o resumo "o que foi decidido e quando" — consulte o guia de
> migração para o detalhamento técnico de cada item.

---

## Arquitetura e escopo geral (14/09/2026)

| Decisão | Escolha |
|---|---|
| Leitura no HANA (~~14/09~~ → **revista em 16/09/2026**) | Híbrido, caso a caso, **com o `hdbcli` na frente**: acesso direto via `hdbcli` é a opção **preferencial** para leitura; a Service Layer entra na leitura **somente onde o `hdbcli` não for suficiente** (tipicamente quando é preciso o objeto de negócio montado pelo B1, não linhas de tabela). A **escrita não muda**: continua 100% pela Service Layer. Inverte a decisão original, que punha a Service Layer primeiro e o `hdbcli` como ponte pontual — e com isso cai a necessidade de criar Calculation Views para as queries complexas. |
| Ordem de migração dos módulos | 1) Oportunidades → 2) Pedidos WBC → 3) Manutenção de OP → 4) Romaneio. |
| Fidelidade vs. limpeza de regras | Fase 1: portar 1:1 o comportamento do C# (mesmas regras, mesmas listas fixas, mesmos side-effects); só depois refatorar com testes de regressão. |
| Como disparar as integrações | Botões manuais na web, espelhando o addon — sem job agendado nesta fase. |
| Escrita no SAP | Sai da DI API (COM, só Windows) e passa a usar a **SAP Service Layer** (REST/HTTPS) como principal ponto de integração. |
| Escrita no WBC (SQL Server) | Mantida igual ao legado, via `pyodbc`, reproduzindo exatamente a leitura das tabelas `INTEGRACAO_*` e o único `INSERT` existente (módulo 1). |
| Framework web | FastAPI + Jinja2 (assíncrono, tipagem via Pydantic mapeando os `Models` do C#). |
| Credenciais | Nunca versionar segredos reais no repositório — variáveis de ambiente (`.env`, fora do git), ao contrário do legado que guardava usuário/senha em texto puro no `app.config`. |

## Interface de desenvolvimento/validação (15/09/2026)

- **CLI primeiro**: antes de terminar a UI web, cada módulo ganha comandos de linha de comando (`app/cli.py`, Typer) que chamam exatamente as mesmas funções de `service.py` usadas pela web — nenhuma lógica de negócio duplicada entre as duas interfaces.
- A CLI é a forma de validar cada módulo contra um ambiente real (homologação/produção) sem depender de servidor web/navegador.
- A web continua sendo a interface de uso do dia a dia, mas passa a ser construída **depois** que cada módulo estiver validado via CLI.

## Módulo 2 — Pedidos WBC (15/09/2026)

- Adiada a finalização do módulo 1 (Oportunidades) a pedido do Anderson ("vamos esquecer a parte de oportunidade de vendas por enquanto e partir para pedido de vendas") para avançar direto no módulo mais complexo do sistema.
- Estratégia de extração de código: para não reler ~1900 linhas de C# manualmente com risco de erro, o código-fonte relevante foi extraído **verbatim** (sem interpretação, via subagente) para um arquivo de referência, e só então portado função por função com leitura própria.
- Fase 1 "fiel ao legado" confirmada explicitamente pelo Anderson (opção recomendada, entre "portar tudo de uma vez, fiel ao legado" vs. alternativas de portar por partes).
- Ambiguidade original entre `UpdateTabPedido`/`UpdateTabPedidoCong`/`UpdatePedido` **resolvida por leitura do código-fonte**, não era de fato ambígua — árvore de decisão documentada no guia (seção 7.4).
- Quirks conhecidos do legado (bug de mapeamento posicional de colunas, duas comparações de "solda" diferentes, `preencheTabela` só processando o primeiro registro no fallback, `CriaOP` pulando a criação quando `entregaMultipla == "Y"`) foram **preservados de propósito** na fase 1, mesmo quando parecem bugs — não corrigidos silenciosamente.
- `Resources/Solda.txt` não veio no projeto enviado — decisão: comportar-se como lista vazia até o Anderson enviar o conteúdo real (comportamento seguro, sem risco de item cair em grupo errado por engano).

## `.env` real e projeto "WBCPython" (15/09/2026)

- Escopo desta etapa **explicitamente limitado pelo Anderson** a "adaptar a leitura das credenciais" — nenhuma outra integração com o projeto "WBCPython" (mais maduro, com CLI própria, painel Streamlit, worker agendado e banco de tracking) foi feita.
- Decisão consciente de **não reconciliar** os dois projetos por ora — ficou registrado como pergunta em aberto para decisão futura (seção 9 do guia).
- `app/config.py` reescrito para os nomes de variável reais do `.env` do Anderson; `Settings.is_production` foi criado mas **ainda não está ligado a nenhuma trava de escrita real**.

## Módulo 2 — verificação de reprocessamento / OP duplicada (15/09/2026)

- Identificado um gap real (não um bug do legado): o addon C# original não tinha proteção **em código** contra reprocessamento — a proteção vinha só do filtro da grade da tela ("Pedidos Novos" só listava pedidos com `U_INO_ProcessWBC='N'`). Como a CLI recebe o `opp_id` direto, sem passar pela grade, essa proteção deixou de existir na reescrita.
- Anderson pediu explicitamente a implementação da verificação. Decisão: adicionar duas checagens **novas, sem equivalente no C# original** — bloquear por padrão o reprocessamento de pedido já marcado (`U_INO_ProcessWBC='Y'`) e bloquear por padrão a criação de OP duplicada quando já existe uma Ordem de Produção não cancelada para o mesmo pedido+item — com uma flag `--force` para ignorar as duas quando necessário.
- Deixado claro no código e no guia que essa é uma feature nova pedida pelo Anderson, não uma correção silenciosa de comportamento legado.

## Revisões de decisões anteriores (16/09/2026)

- **Leitura no HANA — `hdbcli` passa a ser o caminho preferencial** (revisa a decisão de 14/09, ver tabela acima e seção 6.4 do guia). Consequências: (a) cai a necessidade de criar Calculation Views para as queries complexas, o que encerrou a pergunta em aberto sobre permissão de criar Views no HANA; (b) em troca, o usuário HANA do `.env` precisa de SELECT nas tabelas lidas; (c) o código já estava alinhado com a regra nova antes dela existir — todo o módulo 2 lê via `hana_reader` —, então a revisão ratifica o que foi construído, sem retrabalho; (d) comentários/TODOs espalhados pelo código que mandavam "substituir por uma View quando possível" eram resquício da regra antiga e foram removidos.
- **Comparação de OPs sempre entre schemas** (`SBOALTAMIRAPROD` = legado, `SBOALTAMIRAHOMOLOG` = porte): o modo alternativo que separava por data de criação no mesmo schema partia da premissa errada de que a homologação seria cópia da produção, e foi removido.
- **OPs canceladas ficam fora da comparação** por padrão (eram incluídas na primeira versão): em produção várias foram canceladas manualmente, o que é ruído operacional. O relatório informa quantas descartou e marca separadamente os itens cuja única OP no legado está cancelada, para não parecerem "OPs a mais" criadas pelo porte.

## Performance (16/09/2026)

- **Medir antes de otimizar.** Diante da lentidão relatada, a primeira ação foi instrumentar (`--perfil`), não mexer no código. Foi o que evitou o erro: a hipótese inicial (conexões) era a segunda causa (20%), não a primeira (Service Layer, 52%).
- **Aplicadas**: reúso da conexão HANA (62 aberturas → 1) e gravação do `U_INO_OP` em lote (6 `PATCH /Orders` → 3). Resultado no orçamento 00125192: 18,9s → 7,0s — ressalvando que ~6s dos 12s foram variação do servidor, não código.
- **Analisadas e ADIADAS a pedido do Anderson** (estudo completo na seção 7.15 do guia): consolidar o "Sucesso" no mesmo PATCH das linhas (risco baixo, ~0,6s por grupo) e agrupar os `POST /ProductionOrders` em `$batch` (ganho de ~6,5s em pedidos grandes, mas exige reestruturar a cascata recursiva de semiacabados).
- **Critério de ordem registrado**: o batch só deve ser feito **depois** da validação das OPs contra produção. Mexer na parte mais complexa e menos validada do porte para ganhar segundos, antes de saber se ela produz o resultado certo, é otimizar código que pode precisar ser reescrito.

## Módulo 3 — Manutenção de OP (16-17/09/2026)

- **A CLI substitui a grade da tela, não a ignora.** No legado o usuário via os status na grade antes de clicar em qualquer botão. Todos os comandos do módulo (`liberar`, `replanejar`, `encerrar`) imprimem a mesma informação — status atual e ação por OP — antes de pedir confirmação. Sem isso, a decisão que o usuário tomava olhando a tela seria tomada às cegas.
- **`mudaStatus("f")` não é exposto avulso.** Encerrar uma OP sem a movimentação de estoque que a antecede deixa o apontamento inconsistente; o `"f"` só é alcançável pelo `encerrar`, que faz a cadeia inteira.
- **Filtros de busca: regra do legado preservada, silêncio do legado não.** Informar só o limite inferior continua virando `=` e os dois continuam virando `between`, como no original. Mas informar só o limite *superior* — que o C# ignorava sem avisar — agora é erro: na tela dava para perceber olhando a grade, numa CLI não daria.
- **Status validado contra `P`/`R`/`L`/`C`** em vez de ir do argumento direto para dentro do literal SQL. Correção pontual do débito 2 da seção 8 do guia, feita no ponto que o porte estava tocando.
- **Três divergências conscientes no encerramento**, todas por correção e todas documentadas no código (seção 7.18 do guia):
  1. `BaseLine` da saída de insumo usa o `LineNum` real da linha da OP, não o índice da linha no recordset (o original dependia de uma ordenação que a query nem pedia).
  2. Linhas de componente já totalmente baixadas são puladas em vez de irem para o SAP recusar.
  3. **Uma OP só é encerrada se a sua própria movimentação tiver ido até o fim.** O legado chamava `mudaStatus("f")` no final sobre todas as linhas marcadas, inclusive as que haviam falhado antes — fechando OPs sem a saída/entrada correspondente. É o bug mais sério encontrado no módulo 3.
- **Precondição do legado mantida**: só são processadas OPs com quantidade apontada menor que a planejada (`if (qtdAD < qtdPD)`).
- **Nada disso foi executado contra ambiente real.** Diferente do módulo 2, o `encerrar` gera lançamentos de estoque irreversíveis e ainda não rodou nem em homologação. Registrado como pendência de validação, não como entrega pronta para produção.

## Trava de escrita em produção (21/09/2026)

- **Modelo de duas chaves, por decisão explícita**: `WBC_BLOCK_PRODUCTION_WRITES=false` no `.env` (chave de ambiente) **e** `--producao` na linha de comando (reconhecimento por execução). Nenhuma libera sozinha — a flag existe para que apontar o `.env` para produção não baste, e o `.env` existe para que a flag não baste. O bloqueio do `.env` é superior: nem `--producao` o vence.
- **`--sim` deixa de pular a confirmação em produção**; lá é preciso digitar o nome da company DB. O cenário que motivou a trava é exatamente esse: um comando com `--sim` dentro de um script, apontado para o ambiente errado, rodando sozinho.
- **A regra mora em `app/core/guardas.py`, não na CLI.** Se morasse na CLI, a camada web nasceria sem trava. A CLI só traduz a exceção em mensagem.
- **Comandos de leitura ficaram de fora de propósito.** Exigir a flag onde não há escrita ensinaria o usuário a digitá-la sem pensar — o oposto do objetivo.
- **`Settings.is_production` saiu de decorativo para load-bearing.** Existia desde 15/09 sem ser lido por ninguém (conferido por busca antes de implementar); agora é a base da decisão.
- **Limite conhecido, registrado como pendência**: a trava olha a company DB da Service Layer. Um `.env` com SAP de homologação e WBC de produção passaria por ela. Não foi resolvido, foi documentado.
- **O que a trava NÃO é**: ela evita gravar no ambiente errado por engano. Não valida que os comandos façam a coisa certa — isso continua dependendo de rodar em homologação primeiro.

## `Solda.txt` (21/09/2026)

- **A lista vazia nunca foi equivalente ao legado, só era a escolha segura.** Com ela, todo item que o addon mandaria para `ItemsGroupCode = 332` ia para `358`. Itens criados pelo porte antes de 21/09 podem estar no grupo errado, sem erro registrado em lugar nenhum.
- **A assimetria `ItemCode` vs `PrdArv` foi preservada.** `CriaItem` compara a Solda com o `ItemCode`; o laço de `UpdateItem` compara com o `PrdArv`. Parece erro do autor original, está nas duas fontes do C#, e foi mantida — registrada para que ninguém "conserte" sem decidir antes se é para consertar.
- **Precedência dos prefixos de 333 mantida**: 1.477 dos 5.015 códigos da Solda contêm um prefixo de 333 e nunca chegam a ser avaliados para 332, porque no C# aquele teste vem primeiro. Há teste travando essa ordem.
- **`frozenset` + `lru_cache` são otimização, não mudança de regra.** O C# varre o array a cada item e relê o arquivo a cada `CriaItem`; nada disso é comportamento a preservar.

## Primeira validação contra produção (21/09/2026)

- **29 OPs contra 29, componentes idênticos em 24 de 26 itens.** A cascata recursiva de semiacabados — a parte de maior risco do porte — reproduziu a estrutura do legado. Dois defeitos reais encontrados; um corrigido.
- **Tipos de banco convertidos na fronteira, não no ponto de uso.** `Decimal` do HANA entrando na aritmética levantou `TypeError` e o `except` fiel ao C# engoliu, criando OP sem a linha de rateio. A resposta é `_num()` nas leituras, não `float(...)` espalhado por cada conta — mesma decisão tomada em 15/09 para o `Decimal is not JSON serializable`.
- **Réplica fiel do tratamento de erro do legado não é réplica fiel da invisibilidade dele.** O `catch` silencioso foi preservado (abortar o pedido por causa do rateio seria pior), mas o `except` ganhou `logger.exception`. Um erro nosso ficou invisível no console e só apareceu na comparação com produção, dias depois.
- **O baseline de produção não é automaticamente a verdade.** A segunda divergência (quantidade do `TPO`) parecia bug do porte e não era: o valor do porte é rastreável até `INTEGRACAO_ORCPRDARV`, o do legado não fecha nem com o próprio código do legado. O documento de produção está Liberado e passou por apontamento — provavelmente foi ajustado à mão. Numa comparação, "divergiu do legado" é hipótese a testar contra a origem do dado, não veredito.
- **A formação de identificadores também depende de cultura decimal.** O código do Recurso cola o valor sem o separador; `str(0.0)` em Python dá "0.0" e o C# dá "0", então os nomes divergiam e o porte não reconhecia o recurso que o legado já havia criado. O débito nº 8 do guia não se limita a cálculos.

## Arredondamento: correção acima de paridade (21/09/2026)

- **Decisão do Anderson: manter o `round()` do Python, não replicar o `Math.Round` do C#.** Os dois discordam quando o valor cai exatamente no empate (`.xx5`): o C# multiplica por 100, arredonda e divide, e o erro de ponto flutuante da multiplicação atravessa o empate; o Python olha o decimal verdadeiro do double. O Python está mais correto.
- **Consequência aceita**: diferenças de ±0,01 em quantidade de componente vão aparecer no `comparar-ops` para sempre. Não são bug. Em auditorias futuras, um centavo num valor de origem terminado em `5` é esperado; qualquer outra magnitude é coisa nova.
- **Recusadas**: replicar o algoritmo do C# (paridade bit-a-bit ao custo de reproduzir um defeito numérico de propósito) e tolerar 0,01 no comparador (esconderia uma divergência real de um centavo com outra causa).
- **Precedente**: é a primeira vez no projeto que a escolha entre fidelidade e correção foi resolvida em favor da correção. A pergunta 1 da seção 9 (numeração mascarada) continua aberta e não fica decidida por esta.

## Validação do módulo 2 — balanço (21/09/2026)

- Dois orçamentos fecharam: 00120634 (29 OPs contra 29) e 00124945 (68 contra 68, 43 de 47 estruturas idênticas, as 4 restantes por arredondamento).
- **Dois bugs reais em toda a validação**, os dois no rateio de custo; **nenhum na cascata recursiva de semiacabados**, que era a parte de maior risco assumido do porte.
- O ciclo do procedimento (7.23) funcionou sem intervenção manual no SAP: `cancelar-ops` barrado por OP liberada → `replanejar` → `cancelar-ops` → `processar-novos` → `comparar-ops`.

## Módulo 3 validado (21/09/2026)

- **`encerrar` executado em homologação e conferido documento a documento.** `BaseType = 202` e `BaseLine` confirmados: cada componente saiu com a quantidade da sua própria linha. A OP de teste foi escolhida por ter quantidades bem distintas (10,09 e 0,55), para que uma troca de linha aparecesse.
- **Lacuna não prevista: `BPL_IDAssignedToInvoice` e `Series`.** A DI API preenchia filial e série a partir do usuário logado; em REST as duas são explícitas. Derivadas do dado (depósito → componentes → pedido → `.env`), com validação de filial ativa, e resolvidas **antes de qualquer escrita** para não deixar a cadeia pela metade.
- **Contra a Service Layer, nome de campo se descobre lendo um registro real** — não da mensagem de erro (que cita o nome da coluna da tabela), não por analogia, e não pela ausência de erro: a SL **ignora propriedade desconhecida em silêncio**. Três das quatro falhas do dia vieram de eu inferir em vez de usar o `diag entidade`.
- **As guardas funcionaram**: cinco tentativas, quatro falhas, nenhum lançamento indevido, nenhum documento órfão, nenhuma OP encerrada sem movimentação. Falhar sempre no ponto seguro era o objetivo do desenho.
- **Resta um item sem evidência**: o `liberar` nunca rodou como comando. A função e o valor estão provados separadamente; a combinação é inferência.

## Regra de negócio: OP só é apontada estando liberada (21/09/2026)

- **Regra informada pelo Anderson**, e é ela que explica por que o `corrigeOP` do legado libera a OP. Até então o projeto registrava só a consequência técnica (backflush/`im_Manual`), não a regra.
- **O código já liberava; o problema era invisibilidade.** A tabela anunciava `saída + entrada + encerrar` e liberava sem avisar. Como a liberação é exatamente o estado que sobra quando a cadeia falha depois dela — ocorreu quatro vezes nos testes —, o passo passou a aparecer na tabela, no aviso de confirmação e no relatório final.
- **OP já liberada não recebe novo status**: reenviar o status que ela já tem é escrita sem efeito, e a Service Layer é o recurso mais caro da execução.
- **O `im_Manual` vai em qualquer caso**, independente do status — é ele que permite a baixa manual dos insumos.

## Encerrar o pedido inteiro: ordem e falha em cascata (21/09/2026)

- **Filha antes da mãe, por razão física**: a saída de insumo de uma OP pai consome o item que a filha produz. A hierarquia é descoberta **pelo item** (produzido por B aparece como componente de A), porque o B1 não tem campo de "OP pai" para esta cascata.
- **Ordem determinística** (desempate por DocNum): ordem variando entre execuções tornaria um problema impossível de reproduzir.
- **Ciclo de dependência = recusa, não chute.** Numa operação irreversível de estoque, encerrar em ordem arbitrária é pior que não encerrar.
- **Exceção consciente ao precedente do projeto.** A regra "uma unidade com erro não impede as demais" vale para unidades independentes; numa cascata não. Quando uma OP falha, as que dependem dela são puladas — tentá-las daria uma fila de "sem estoque" escondendo a causa real. OPs independentes seguem.
- **Não foi acrescentada verificação prévia de custo/estoque**: depende do método de valoração por item e depósito, e replicar isso seria duplicar lógica do SAP para reproduzir uma mensagem que ele já dá com clareza (`10001287 - Item cost not found`).

## Camada web dos módulos 2 e 3 (22/09/2026)

- **Três decisões do Anderson para a web**: sem autenticação por enquanto; operação irreversível exige confirmação; operação longa roda em segundo plano com acompanhamento. As três viraram peças compartilhadas em `app/core/` (`confirmacao.py`, `tarefas.py`, `tarefas_router.py`), não código repetido por módulo.
- **A trava de produção passou a valer na web** (`app/core/web.py`). Enquanto o painel não gravava, a trava viver só na CLI era inofensivo; deixou de ser no momento em que existe uma tela que grava, porque as duas camadas leem o mesmo `.env`.
- **Confirmação em duas etapas, não `confirm()` no navegador.** Uma rota `/conferir` lê e monta o plano; a `/executar` só aceita o token daquele plano. O usuário confirma sobre o que foi calculado e mostrado, e um F5 na tela de resultado não reexecuta nada.
- **Errar o texto de confirmação de produção não queima o token.** Obrigar a refazer a conferência inteira por causa de uma digitação empurra o usuário a clicar mais rápido da próxima vez — o oposto do objetivo.
- **A lista é relida do banco antes de agir.** O que o formulário manda é conferido contra a lista elegível; número que saiu dela é recusado com o motivo. Terceira encarnação da mesma lição: no legado a proteção morava no filtro da grade.
- **Teste de cobertura da trava**: varre as rotas POST e falha se alguma gravar sem `exige_autorizacao`; rota de leitura se declara com o motivo. Análogo web do teste que exige `--producao` em todo comando de escrita da CLI — e já pegou uma rota não declarada na estreia.
- **Uma execução por módulo, não uma global.** Duas do mesmo módulo em paralelo disputariam os mesmos pedidos e OPs; módulos diferentes são independentes e serializar tudo faria esperar sem motivo.
- **Tarefas e tokens em memória, deliberadamente.** Reiniciar apaga o histórico (custo aceito, uso interno e manual) e invalida tokens pendentes (efeito desejado: nenhum token sobrevive para executar depois contra um estado que já mudou). O caminho quando incomodar é persistir, não trocar de arquitetura.
- **Cancelar interrompe entre passos e não desfaz** o que já foi gravado no SAP. A tela diz isso ao lado do botão.
- **O anexo com o layout nunca chegou** — as telas partem das quatro do addon original.

## Módulo 1 (Oportunidades) removido do porte (22/09/2026)

- **Decisão do Anderson**: "remova esta opção de todo o projeto". A tela existe no addon legado e continua funcionando lá — não faz parte do fluxo que a aplicação nova precisa cobrir.
- **Removido de fato, não escondido**: o módulo (`app/modules/oportunidades/`), a tela, os dois comandos de CLI, o item de menu e os testes. Deixar o código morto atrás de uma rota sem link seria pior — alguém o manteria por engano.
- **Conferido antes de apagar, não depois**: nada mais dependia dele (`preenche_log` mora em `core/audit_log.py`, `OportunidadeDoc` em `pedidos_wbc/schemas.py`).
- **A numeração 2/3/4 foi mantida**, começando no 2. Renumerar quebraria a correspondência com o menu do addon legado e com todo o histórico do `migration_guide.md`. A home explica a ausência do 1 em uma linha, para não parecer defeito.
- **A seção 4.3 do guia continua lá**, marcada como não portada: descreve o legado, que segue existindo. Apagá-la esconderia o que o addon faz; mantê-la sem marcação faria alguém tentar portá-la de novo.
- **Fica um órfão conhecido**: `WbcSqlServerClient.execute`, única via de escrita no WBC, era usada só pelo `INSERT` do módulo 1. Mantida à espera do módulo 4 (Romaneio) — se ele não escrever, sai junto.
- **Correção aproveitada**: a ajuda da CLI dizia que `processar-novos` recebe "Números de Oportunidade"; recebe o **nº do orçamento WBC**. Foi esse texto que induziu o erro da tela em 22/09.

## Tema visual do painel (22/09/2026)

- **Referência**: a tela de Usuários de outro sistema da casa, enviada pelo Anderson ("aplique as fontes, cores, ícones etc").
- **Tudo é token em `:root`** — cor, raio, espaçamento, fontes. Uma paleta única serve a status de OP, situação de tarefa e tipo de operação; é o que impede dois vermelhos diferentes para a mesma ideia de risco.
- **Tema claro é a mesma folha com tokens redefinidos**, não um segundo tema paralelo: componente novo nasce funcionando nos dois. A preferência é aplicada antes da primeira pintura, para a página não piscar em branco.
- **Nada carregado de fora**: ícones em SVG inline, fontes do sistema (Inter primeiro na pilha, sem `@import`). A aplicação roda na rede interna, às vezes sem internet — depender de CDN tornaria a página ilegível justamente onde ela precisa funcionar.
- **Cor de status por significado**, não por estética: amarelo Planejada, azul Liberada, verde Encerrada, vermelho Cancelada — a mesma leitura da grade do SAP.
- **A coluna "Status (descrição)" saiu**: `P` e `Planejada` em colunas separadas é a mesma informação duas vezes, e era ela que jogava as datas para fora da tela.

## Guia de estilo OrçaView aplicado ao painel (22/09/2026)

- **Fonte**: o `GUIA_ESTILO_ORCAVIEW.md` enviado pelo Anderson. Seguimos a **seção 5** (o kit para equipe de fora), que é exatamente este caso — tela irmã das três do OrçaView, sem copiar nenhuma.
- **Adotado sem alteração**: paleta `--ov-*` com escuro default e claro por `data-theme`; escala `--fs-*` e espaços `--space-*`; anti-FOUC antes de qualquer `<link>`; acento único coral com estado em cor semântica; relevo de botão em três paradas; KPI em `grid`+`auto-fit`; `zoom` na casca; `translateY` só com mouse; foco de 2px.
- **Regras de build do §6 verificadas por script**: zero `font-size` literal e zero hex fora das três linhas sancionadas de definição da paleta.
- **Três divergências conscientes**, por não termos os assets do OrçaView: ícones em SVG inline (não Bootstrap Icons), botão de tema na barra (não `position:fixed`, porque não há o drawer do `menu.js`), e sem Bootstrap.
- **O guia corrigiu duas escolhas minhas**: a faixa de KPI voltou a `grid`+`auto-fit` (eu havia trocado para `flex`; o guia proíbe explicitamente) e a tabela larga passou a rolar dentro do cartão em vez de quebrar a descrição em várias linhas.
- **Chave de tema `orcaview-theme`**: a convenção da casa, mesmo sem storage compartilhado entre portas — duas grafias para a mesma ideia custariam mais tarde.

## Trava de escrita em produção REMOVIDA (22/09/2026)

> Revoga a decisão de 21/09/2026 ("Trava de escrita em produção"), registrada acima. A entrada anterior fica como histórico — o que vale é esta.

- **Pedido do Anderson**: sem travas, igual a homologação; apenas aviso.
- **Por que faz sentido agora**: a trava nasceu durante a validação, quando cada execução era um teste. Com os módulos 2 e 3 validados e a operação virando rotina, fricção de validação cobrada de quem opera todo dia deixa de proteger e passa a atrapalhar. A decisão é do dono do ambiente.
- **Saiu**: `WBC_BLOCK_PRODUCTION_WRITES`, `--producao` em todos os comandos, a digitação da company DB (CLI e web), `EscritaBloqueada`/`autoriza_escrita`/`texto_de_confirmacao` e o `exige_texto` do plano.
- **Ficou**: a faixa vermelha em toda página, o aviso em log a cada escrita em produção (rastro para auditoria), a ênfase em vermelho na pergunta da CLI e — o mais importante — **a conferência em duas etapas de operação irreversível**, que nunca foi a trava: vale igual em homologação, porque o motivo dela é a operação não ter volta.
- **O que se perdeu, explicitamente**: o cenário que motivou a trava era um `--sim` dentro de script apontado para o ambiente errado. Contra isso só resta o cuidado com o `SL_COMPANY_DB` do `.env`. Registrado no topo de `guardas.py`.
- **O teste de cobertura das rotas continua**: `avisa_escrita` não impede nada, mas toda rota que grava precisa chamá-la — o log é o rastro, e rota de escrita sem ele é o esquecimento que só aparece quando alguém procura o registro e não acha.

## Revisão de legibilidade e portabilidade (23/09/2026)

- **Bug de portabilidade corrigido**: `StaticFiles`/`Jinja2Templates` usavam caminho relativo, então a web só subia se o `uvicorn` fosse chamado de dentro de `python_app/` — um systemd sem `WorkingDirectory` não levantava. Agora derivam de `__file__`, como `config.py` e `listas_fixas.py` já faziam.
- **`opp_id` → `orc_num`** no módulo 2: o parâmetro recebia o nº do orçamento WBC, não a chave da Oportunidade, e foi essa confusão que gerou o bug de 22/09. Idem `oppr_id`/`oppr_id_orig` → `orc_num`/`doc_num`.
- **Constantes de negócio nomeadas** (grupos 332/333/358, depósitos 08/01, prefixo "I", prazo de 20 dias) e duplicação byte a byte extraída (linha do pedido; blocos de relatório da CLI).
- **Dois achados documentados e NÃO corrigidos**, por exigirem decisão: (a) *(corrigido em 05/10/2026 — ver "O pedido segue o Detalhe do Orçamento novo")* `_get_doc_entry_table_valdixon` devolve 0 sempre, o que faz `U_INO_ORCAMENTO` nunca ser gravado — divergência silenciosa do legado em todo pedido; (b) o `GetByKey("SalesOpportunities", doc_num)` do ramo `else` de `_update_pedido`, réplica fiel de um erro que existe no próprio addon (a linha comentada do C# mostra que o autor sabia o valor certo).
- **Fora de escopo por decisão**: dividir `cli.py` em pacote, quebrar as funções longas e parametrizar as queries HANA — cada uma merece ser mudança isolada e revisável sozinha.

## Saída sem OP tem que aparecer na tela (23/09/2026)

- **Motivo**: pedido 84426 — 4 itens no orçamento, OPs de apenas 2, execução "concluída" em verde.
- **Causa**: as três saídas de `_processa_grupo_producao` que terminam sem criar OP gravavam só no `@INO_LOG`, sem nenhum `logger.*`. Como a tela é uma ponte para os loggers do módulo, elas eram invisíveis — e como não entravam em `com_erro`, o resultado da tarefa também não sabia delas.
- **Decisão**: toda saída sem OP emite `logger.*` com o prefixo `SEM OP:` **e** devolve o motivo; `processar_pedidos_novos` acumula em `sem_op`, e tanto a web quanto a CLI passam a mostrar. Um pedido com grupo sem OP não se anuncia mais como "concluído" nem grava "Pedido integrado com Sucesso." no `@INO_LOG`.
- **Regra que fica**: `preenche_log` é registro, não aviso. Toda condição que faz o porte deixar de fazer algo que o usuário pediu tem que chegar à tela — quinta ocorrência do mesmo padrão neste porte.
- **Não fiz**: nenhuma consulta ou execução em produção. As consultas de confirmação estão em `diag84426.sql`, todas só de leitura, para o Anderson rodar.

---

## Checagem de OP existente passa a contar, não só a existir (23/09/2026)

- **Causa real do 84426** (pelo `@INO_LOG` do orçamento 00125540): três itens do orçamento resolvem para o mesmo item SAP `I000003`. O grupo 1 criou a OP 158588; os grupos 2 e 3 a encontraram e pularam como reprocessamento. Regressão introduzida pelo próprio porte em 15/09 — o legado não tem essa checagem e cria uma OP por grupo.
- **Decisão**: o N-ésimo grupo de um item só é reprocessamento se o item já tinha N OPs antes da execução; a foto é tirada uma vez por item, antes de qualquer criação. Mantém a proteção contra duplicar pedido completo e deixa de bloquear grupos irmãos.
- **Rateio (`POST /Resources`) nunca funcionou no porte** — `Data DailyCapacities not found` em todos os grupos. Pedidos de produção processados pelo porte com `GGF_` novo saíram sem a linha de rateio. **Não corrigido** até ler os nomes reais com `diag entidade Resources` (só leitura, homologação); a falha agora aparece no resultado e na tela.
- **Rateio corrigido no mesmo dia**: `diag entidade Resources` mostrou os nomes reais (`VisCode`, `Name`, `ResourceWarehouses[].Warehouse`, `ResourceDailyCapacities[]`). Corpo refeito fiel ao C#: só `VisCode`, devolve o `Code` retornado.
- **Corrige meu diagnóstico anterior**, que apontava falta de mapeamento em `@INO_GRP_PRODUTOS` como causa mais provável.

## DocNum do pedido sai do DocEntry, não da Oportunidade (23/09/2026)

- **Motivo**: no 84425 a busca do DocNum pela Oportunidade (`pegaDocNumPed`, idêntica à do C#: sem filtro de cancelado, sem ordenação) devolveu o **84424, cancelado**. O DocEntry vinha de outra consulta, que filtra e ordena — os dois números apontavam para pedidos diferentes.
- **Efeitos**: a checagem de OP existente olhou o pedido errado (por isso o 84425 criou as duas OPs de I000003 e o 84426 não); e, mais grave, **a quantidade planejada da OP e a base do rateio eram lidas do pedido cancelado**. Defeito herdado do legado.
- **Decisão**: DocNum via `SELECT "DocNum" FROM ORDR WHERE "DocEntry" = …` — mesmo pedido por construção. Divergência deliberada do legado.
- **Levantamento (24/09)**: desde 01/09 só duas OPs planejadas ficaram com quantidade errada, ambas do 84425. O Anderson corrigiu manualmente no SAP.

## Quantidade da OP vem da linha do grupo (24/09/2026)

- **Regra de negócio nova**: a linha do pedido passou a trazer a quantidade de módulos (antes era sempre 1, um conjunto), para melhorar o controle de estoque.
- **Defeito herdado do legado** que a regra nova expôs: `BuscaMAXItemLinha` pegava a linha sem OP de maior número do item, não a do grupo. No 84274 três OPs saíram com 255 (linha 6) para linhas de 8, 1 e 18.
- **Decisão**: restringir a busca às linhas do grupo (`U_INO_ORCITM`), pedido identificado pelo `DocEntry`. Linha não encontrada → 1, como no legado, mas com aviso na tela.
- **Corrige minha hipótese anterior**, de que esses números vinham de um pedido cancelado da mesma oportunidade. A consulta mostrou que vêm de outras linhas do mesmo pedido.

## Romaneio fora do menu (24/09/2026)

- A pedido do Anderson, o Romaneio saiu do menu e da página inicial. O esqueleto do módulo, a rota e o comando da CLI continuam no código, só não aparecem mais na navegação.

## Pacote de implantação para servidor Windows (24/09/2026)

- **Destino**: servidor Windows, montado pelo Claude seguindo o `CLAUDE.md` do pacote. Docs históricas incluídas; C# do legado, não.
- **Dependências congeladas** nas versões exatas da máquina do Anderson (Python 3.14), com as 38 rodas Windows no pacote para instalar offline.
- **Código corrigido na revisão**: `SL_CA_BUNDLE`/`SL_TIMEOUT_SECONDS` passaram a valer; `.env` lido com ou sem BOM; testes independentes do diretório atual.
- **Operação**: um worker só; log em arquivo com rotação; `PYTHONUTF8=1`; serviço por NSSM ou Agendador de Tarefas; firewall restrito à sub-rede local por padrão.
- **Risco aberto, registrado e não resolvido**: a aplicação não tem login. O acesso é controlado só pelo firewall.

## O pedido segue o Detalhe do Orçamento novo (05/10/2026)

- **Decisão do Marcelo** (fecha a D15(b) do `PARA_O_ANDERSON.md`): bug, tratar com severidade.
  Cada Processar/Reprocessar grava um `OrcDetalhe` novo e o `ORDR.U_INO_ORCAMENTO` do pedido
  ("Detalhe do Orçamento") passa a apontar para ele, como no C#.
- **Medição (só leitura, PROD, 05/10):** em 23 de 68 pedidos abertos desde 01/09 o campo não
  apontava para o último Detalhe do orçamento. No 84454, cinco execuções (02/10 e 05/10) criaram
  Detalhes novos e o campo ficou no 546783. Em 5 pedidos (84327, 84353, 84371, 84375, 84391) o
  campo apontava para um Detalhe de **outro** orçamento (00124945, criado pelo addon em 01/10):
  é o `SELECT max("DocEntry") FROM "@INO_ORCAM"` do legado pegando o registro de outro processo.
- **Como:** o `DocEntry` vem da resposta do `POST` da Service Layer (`_doc_entry_do_detalhe`),
  nunca do `max()`. Vai no mesmo PATCH de sempre (`_update_tab_pedido`/`_update_tab_pedido_cong`)
  e é relido no HANA depois: o log diz "Detalhe do Orçamento do pedido: antigo → novo (conferido
  no SAP)" ou um ERRO (vermelho) se não bateu ou se não há Detalhe novo. A releitura depois do
  PATCH nunca interrompe o pedido: `U_INO_ProcessWBC` já foi gravado no mesmo PATCH (a leitura de
  antes pode falhar, mas aí nada foi gravado).
- **Fora do escopo:** os pedidos já errados não foram corrigidos (cada Processar/Reprocessar
  novo os acerta); o `max()` do addon legado continua lá (é do Anderson).

*Ver `migration_guide.md` para o detalhamento técnico completo de cada decisão (queries citadas, linhas do C# original, alternativas consideradas e perguntas em aberto ainda não resolvidas).*
