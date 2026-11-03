# Plano — docstrings e type hints em inglês (ServidorIntegracaoSAP)

> **Status (03/11/2026):** ✅ **PLANO CONCLUÍDO — E1 a E8.** 116 arquivos da .11 (+ 3 no web e 1 no
> SAP_RDP); ~225 docstrings desatualizados corrigidos, 3 regras do CLAUDE.md da .11 + 1 do CLAUDE.md
> da raiz, 4 textos de tela errados. Entra na .11 no próximo deploy normal (sem restart extra).
> Página: ver o artifact "Docstrings EN" (mesmo conteúdo).

**Começo → Meio → Fim:** núcleo que tudo importa (E1) → regras que escrevem no SAP (E2–E5) → API,
telas, CLI e MCP (E6–E7), com os arquivos-irmãos por último (E8).

## Tamanho (medido por AST em 03/11/2026, sem `tests/`)

| | |
| --- | --- |
| Arquivos `.py` | 153 (45.949 linhas) |
| Funções/métodos | 1.694 |
| Docstrings | 1.280, das quais **691 em português** |
| Funções sem tipagem completa | **214** (90% em `api.py`, routers e CLI) |
| Docstring citando parâmetro que não existe mais | 1 (falso positivo) — o desatualizado só aparece **lendo** contra o código |

## Por que essa ordem

O critério é **o que eu mais leio para desenvolver**: módulos com mais importadores (fan-in) e os
que escrevem em produção primeiro. Um docstring certo em `safety.py` ou `tarefas.py` vale em toda
tarefa; um em `dashboard/` vale só quando mexo na tela.

## Fatos que travam o desenho

- **Docstring que é TEXTO DE TELA fica em português.** Não traduzir:
  - ferramentas MCP (`@mcp.tool` em `mcp/mcp_server.py`): o docstring é a descrição que a Mira lê;
  - comandos Typer (`controleproducao/cli.py`, `wbcpython/cli.py`): o docstring vira o `--help`;
  - ~~rotas FastAPI: o docstring vira o OpenAPI~~ — **corrigido na E7**: o CP sobe com
    `openapi_url=None` e o painel WBC com `docs_url=None`, então o docstring de rota não sai para
    ninguém e foi traduzido. Só a 1ª linha dos módulos que alimentam `argparse(description=__doc__)`
    fica como está.
  Nesses arquivos traduzo só as funções internas (`_*` e helpers).
- **Type hint em rota FastAPI e comando Typer MUDA comportamento** (parâmetro anotado vira query/body
  ou opção de CLI; anotação de retorno vira `response_model`). Nesses arquivos: **nenhuma tipagem nova**.
- **Anotação de classe em dataclass/pydantic é campo** — não se toca (`schemas.py`, `modelos.py`).
- **Arquivos-irmãos** (`ordens_producao_sl.py`, `windows_update.py`, `situacao_pedidos.py`,
  `sap_montagem_labels.py`, `wake_altservidor_ia.py`, `pedidos_bloqueados.py`, `operacao/boots.py`,
  `operacao/travamentos.py`): só mudam junto com o outro repo, no mesmo commit → ficam na E8.
- Comentários `#` em PT não entram neste plano (só se o bloco for reescrito).

## Como cada etapa é feita e provada

1. Ler cada docstring **contra o código**; corrigir o que não bate (nome, regra, retorno), traduzir
   para inglês técnico, só o porquê.
2. Tipar funções sem tipagem fora dos arquivos de framework.
3. **Prova de que nada além de documentação mudou:** `scripts/check_docs_only_diff.py` compara a AST
   de cada arquivo com a do `HEAD` **sem docstrings e sem anotações de função** — tem de dar igual.
4. `ruff check .` = 0 e a suíte inteira verde (o pre-commit já exige).
5. Commit + push; pull na .11 sem restart (docstring e anotação não mudam o que roda; o restart fica
   para o próximo deploy normal).

## Fases

| Etapa | O que | Arquivos | Docstrings PT | Sem tipo | Status |
| --- | --- | --- | --- | --- | --- |
| **E1** | Núcleo: `safety`, configs, `padroes`, clientes do Service Layer, HANA e SQL Server, `tarefas`, `acesso`, `guardas`, `sql_seguro` | 20 | 79 | 3 | **concluída** |
| **E2** | Regra do CP — Pedidos WBC (`service.py`, `acoes.py`, `queries.py`, `listas_fixas.py`) | 5 | 69 | 11 | **concluída** |
| **E3** | Regra do CP — Manutenção de OP | 4 | 17 | 8 | **concluída** |
| **E4** | Worker WBC — `domain/`, `application/`, `host/` | 19 | 125 | 0 | **concluída** |
| **E5** | Worker WBC — `infrastructure/`, `tracking/` | 20 | 115 | 0 | **concluída** |
| **E6** | API 8077 e extrações (`api.py`, `pipeline_core`, `situacao_pedidos_hana`, `extract_*`, `monitoring`) | 11 | 94 | 90 | **concluída** |
| **E7** | Telas, CLI, MCP, `operacao/`, `seguranca/`, `casa/` (só o que não é texto de tela) | 32 | 170 | 95 | **concluída** |
| **E8** | Arquivos-irmãos, junto com SAP_RDP e web | 8 | 21 | 7 | **concluída** |

Cada etapa sai num commit próprio (`docs(en): E<n> …`).

### O que a E1 achou de desatualizado (16)

- `wbcpython/safety.py` e `wbcpython/config.py`: diziam "produção é imutável, só homologação" e
  apontavam para `ai_spec/04`, que não existe mais. A Regra 1 foi revogada em 02/09; desde 28/09
  a escrita em produção é liberada só pelo IP da .11.
- `controleproducao/config.py` `is_production`: dizia "hoje só informa". Fora da .11, um alvo de
  produção **bloqueia** (`ProductionWriteBlocked`).
- `wbcpython/config.py` `_producao_nao_pode_ser_vazia`: descrevia um efeito na trava que não existe
  mais; o efeito real é `targets_production` falso, o que abre a escrita do painel.
- `core/perf.py`: descrevia uma conexão nova a cada consulta; o código reaproveita a conexão.
- `core/exceptions.py`: `WbcDatabaseError` falava em "gravar" (não há escrita; HANA também lança);
  `ServiceLayerAuthError` dizia "dispara novo login" (só `/Login` recusado lança; a expiração é
  tratada por 401 com um único re-login).
- `core/hana_reader.py`: dizia que toda falha reconecta e repete; só repete se a conexão caiu.
- `core/service_layer_client.py`: login preguiçoso e `/Logout` não descritos; `pyodbc` → `pymssql`.
- `wbcpython/.../service_layer/client.py`: re-login "ao receber 401" → também no código SAP `-304`.
- `core/tarefas.py` `criar`: faltava o `ValueError` de `origem` inválida; `core/acesso.py`: faltava
  `/inicio` nas rotas abertas.

<span class="warn">Fica para depois:</span> as **mensagens de erro** do `safety.py` (texto que
roda) ainda citam `ai_spec/04_environment_constraints.md`, que não existe. Corrigir é mudança de
código (e de teste), fora deste plano.

### O que a E2 achou de desatualizado (22)

`service.py` (17), `acoes.py`, `queries.py`, `listas_fixas.py` (2), `schemas.py`. Os que mais
enganavam:

- `processar_pedidos_novos` dizia que `force=True` "serve junto com o reprocessar" — **`force`
  duplica OP**; não citava a falta de rollback nem as recusas `nota_espelho`/`ops_orfas`, e dizia
  devolver 2 listas (são 6).
- `reprocessar_pedidos_integrados` dizia que cancela as OPs "antes de recriar": **nunca recria**.
- `_update_pedido` descrevia dois caminhos funcionando: a 1ª instrução é o `raise` (fechado em 29/09).
- `cria_recurso_rateio`, `preenche_tabela`, `cria_ordem_producao_semi_acabado`: retornos e erros
  engolidos (`return 0`/`-1`) não descritos ou descritos errado.
- `_busca_nova_tabela_quot_com_linhas` dizia uma tabela por linha: devolve `[header]`.
- `_add_pedido_oportunidade`: o TODO de confirmar `SalesOpportunitiesLines` já estava resolvido.
- `acoes.monta_plano` dizia que pedido fora da lista "não entra": ele **recusa o plano inteiro**
  (`409 fora_da_lista`).
- `listas_fixas`: 1.467 códigos (não 1.477) contêm prefixo 333; o `UpdateItem` da solda vale para
  item novo **e** existente.
- `comparar_ops` dizia que o porte roda em HOMOLOG; desde 28/09 escreve onde `SL_COMPANY_DB` aponta.

<span class="warn">Achado fora do escopo:</span> `controleproducao/cli.py` (linhas ~312/345/350)
**dizia ao operador** que pedido integrado é "reprocessável com `--force`" — e `--force` duplica OP.
✅ Corrigido em 03/11/2026 (commit à parte): as 3 mensagens apontam para `reprocessar-integrados`, e
o `--help` do `--force` e do `processar-novos` avisa que ele duplica e diz como retomar.

### O que a E3 achou de desatualizado (6 + o CLAUDE.md)

`service.py`, `acoes.py`, `fechados.py`, `queries.py` (os 4 routers ficam na E7).

- **CLAUDE.md da .11** dizia que Liberar, Replanejar **e** Encerrar recusam OP com saída/entrada
  lançada. Pelo código e pelos testes, **só o Replanejar recusa** (o lote inteiro, 409); o Encerrar
  **pula** OP já apontada e dá saída só no insumo pendente; o Liberar não confere. Corrigido.
- `muda_status`: não dizia que OP terminal, OP já no status-alvo e (no Replanejar) OP com
  movimento vão para `ignoradas` sem chegar ao SAP.
- `finalizar_ops`: não dizia que falha entre a liberação e a saída devolve a OP para Planejada.
- `_valida_status` dizia "impede injeção": o status já vai como parâmetro `?`; ela só dá erro legível.
- `levanta_ops`: por pedido exclui OP Cancelada, por número não.
- `encerra_pedido_com_ajustes`: item inativo sem "ativar inativos" **não** tenta de novo.

### O que a E4 achou de desatualizado (33)

`domain/` (15), `application/processar.py` (10), resto de `application/` + `host/` (8).

- `numeros.normalizar_decimal` descrevia errado a leitura de "3 dígitos depois do separador": só o
  **ponto** vira milhar; a vírgula sozinha é sempre decimal (`"1,234"` → 1,234; `"1.234"` → 1234).
- `pedido.linhas()` dizia que `Weight1` é o peso líquido: é líquido × 1,10 (embalagem).
- `sitcode`: a tabela do legado mapeava `OpprId` para um campo que o estado não tem; o encerramento
  **pula** a marcação se a oportunidade já está fechada; `_decidir_pedido` (SitCode 60 = venda ganha
  no WBC, não "pedido fechado no SAP").
- `processar._gravar_snapshot_orcdetalhe` dizia "antes dos documentos": é **sob demanda**, logo antes
  da 1ª escrita (desde 01/10). Um comentário PT ao lado dizia o mesmo e foi corrigido.
- `_espelhar_status_apos_documento` dizia "três guardas": são quatro + um quinto pulo.
- Intervalo do worker citado como "3 min"/"180 s" em 4 lugares: o padrão é **300 s**
  (`WORKER_INTERVAL_SECONDS`); `MESES_DE_JANELA` citado como `WBC_MESES_DE_JANELA`.
- `rodar_continuamente` não citava a parada por arquivo; `executar_ciclo` não dizia que o ciclo por
  orçamento **espera** a trava e o agendado não.
- `__init__.py`/`numeros.py` citavam `ai_spec/01` e `/02`, que não existem.

<span class="warn">Risco latente achado (não corrigido, é lógica):</span> `normalizar_decimal` lê
`"0.250"` como **250** e `"0.001"` como **1** (ponto + 3 dígitos com parte inteira ≤ 3 = milhar).
Hoje só os campos de texto `Retorno`/`Indice`/`Negociacao` da view do HANA passam texto por ela, e
esses **não** são gravados no SAP (o mapeamento usa os do SQL Server do WBC, que chegam numéricos).
Se um dia um desses campos for gravado, o bug vira real. (E5: o único caminho que lê esses 3
campos, `evolucao_do_orcamento`, **não tem chamador fora dos testes** — o risco hoje é zero.)

### O que a E5 achou de desatualizado (30)

`hana/` (12), `service_layer/` (6; `client.py` já era da E1), `wbc_sql/` + `tracking/` (12).

- `service_layer/documentos.atualizar` vendia o `B1S-ReplaceCollectionsOnPatch` como garantia de troca
  das linhas: o `DECISOES.md` ("204 No Content não prova que as linhas mudaram") registra que foi
  mandado e as linhas **não** mudaram (cotações 00125616 e 00125577). Agora aponta para
  `total_das_linhas`.
- `hana/repository`: citava a Regra 1 (revogada); dizia que `hdbcli` é opcional (está fixado no
  `requirements.txt`); `evolucao_do_orcamento` prometia "a primeira é a mais recente" por uma coluna
  `rn` que o SELECT não lê — e não há `ORDER BY` (sem efeito: a função não é usada).
- `wbc_sql/models`: dizia que os itens vêm de `INTEGRACAO_ORCITM`; vêm de `ORCIMP` (a ORCITM parou de
  receber dados). `a_partir_de` dizia "modo somente leitura" no engine: quem garante é o
  `assert_read_only_sql`.
- `tracking/modelos`: "três tabelas" — são cinco. `limpar_janela`: "quatro chamadores" — são três +
  a saída por `registrar_erro_da_janela`. Citava `janela_do_ciclo` (é `_janela_do_ciclo`).
- `orcdetalhe.historico` dizia "útil para o dashboard": **ninguém chama**.
- 9 docstrings apontavam para `ai_spec/02`, `/03`, `/04`, que não existem.

### O que a E6 achou de desatualizado (44 + o CLAUDE.md)

`api.py` (14 + escopos de 10 rotas), `pipeline_core`/`situacao_pedidos_hana`/`monitoring` (16),
extrações e módulos menores (14). `arquivo_pedidos`, `pedidos_compra_hana`, `controle_montagem`: ok.
Tipagem: 80 anotações no `api.py` (Flask ignora anotação), as 6 travas `@contextmanager` do
`pipeline_core` com `Iterator[None]` e o resto das funções sem tipo.

- **CLAUDE.md da .11**: dizia que o `/status` completo exige `OS_API_KEY` ou `STATUS_ID`; uma chave
  registrada com `leitura` também recebe (`test_status_completo_para_quem_tem_leitura`). Corrigido.
- `api.py`: a lista de status do `os_sincronizar` não tinha 409/429/403; "até 30" é 30 por padrão e
  `?limit=` até 50; `?campos=resumo` tem 11 colunas (não 10); 10 rotas diziam "exige X-API-Key" em vez
  do escopo real (`os:sincronizar`, `oportunidades:carga`, `historico:apagar`…); `aprovacao_pedir` não
  citava o `Idempotency-Key` nem o `ja_existia`.
- `pipeline_core`: "as 4 travas" (são 5); `registrar_rotina` dizia que a .11 não grava e que
  depende de um interruptor de `.env` (grava, e liga pelo IP da máquina).
- `situacao_pedidos_hana.liberacao_e_nf`: "13 campos" (são 12); "3 consultas" (até 4).
- `monitoring`: o sinal do agendador lê os 2 últimos registros, não o último; o ramo "aposentado"
  da tarefa WBC não era citado.
- `extract_vendas_bi`: apontava para `sql/bi_vendas.sql` e uma migração que não existem; não citava
  o filtro de nota espelho (84534).
- `faturamento_pedido.ler_faturamento`: "três consultas" (são até três, duas só com entrega futura).
- `entrega_combinada`: "três chamadas" à tabela (são cinco).

### O que a E7 achou de desatualizado (~60 + 3 textos de tela + o CLAUDE.md)

Painel WBC (16), CLIs (8), routers e núcleo do CP (6), `operacao`/`seguranca`/`mcp`/agendador (6),
mais traduções. Conferido por AST que **nenhuma** assinatura de rota, comando Typer ou ferramenta MCP
mudou.

- **Texto de tela errado, corrigido em PT:** o `--help` do `reprocessar-integrados` dizia "cancela as
  OPs… antes de recriar" — **não recria**; a ferramenta `historico_pedido` da Mira dizia que não dá
  para separar integração de pessoa pela tela (o `casar_tela` separa: `origem: "pessoa"`,
  `pela_tela: true`); a `ultimo_deploy` listava um passo `fetch` que o deploy não grava; o `--porta`
  do painel dizia "padrão 8501" (é 8079).
- **CLAUDE.md**: dizia espelho de orçamentos a cada 2 min; é **1 h, só no expediente**
  (`ORCAMENTOS_ESPELHO_INTERVALO_MIN = 60`). Só o `arquivo_pedidos` é 2 min, 24/7.
- Painel WBC: o módulo dizia "o painel não escreve no SAP; a única ação é `_reprocessar`" — não
  existe `_reprocessar`, e a aba Executar dispara comandos do CLI que escrevem (com a trava
  `painel_pode_escrever`); `executar` dizia "o painel não tem autenticação" (tem, desde 01/10).
- CP: o `deploy_update.bat` **aborta** com o CP ocupado (o docstring dizia "espera"); a
  `ConfirmacaoInvalida` falava de "texto de confirmação", que não existe mais; `main.py` dizia
  "4 módulos" (são 2; o Romaneio saiu em 23/10).
- MCP: `serve_http` citava um `StaticBearerMiddleware` que não existe (é `PortaDoMcp`, um token por
  cliente); `acoes_agente.pedidos_fechados_com_op` chama a 8080, não a 8077.

### O que a E8 achou de desatualizado (arquivos-irmãos, ~15)

Cada par seguiu a regra de igualdade dele: `wake_altservidor_ia.py` (web) e `operacao/travamentos.py`
(SAP_RDP) continuam **idênticos byte a byte** (hash conferido); nas 12 funções do núcleo do
`situacao_pedidos`, o fonte continua idêntico ao do web (`test_situacao_pedidos_diffavel`: 23 passam,
nenhum pulado); `windows_update.py` e `pedidos_bloqueados.py` ficaram com o mesmo texto nos dois
lados onde o código é o mesmo. `ordens_producao_sl.py` e `boots.py` já estavam em inglês.

- `pedidos_bloqueados._como_int` (.11 **e** web) prometia aceitar `84337.0`: o código recusa float
  (devolve `None`).
- `situacao_pedidos.normalizar` (.11 **e** web) não dizia que tira os pedidos da lista de bloqueio.
- `sap_montagem_labels`: dizia que a fonte do SAP "será ligada na F2"; já é ligada na subida da API.
- `windows_update` (.11 e SAP_RDP): apontava para `PLANO_WINDOWS_UPDATE.md`, que não existe; o
  SAP_RDP dizia "a prod roda 3.13.14" (roda **3.14.8 desde 07/10**); `estado_updates` não citava o
  estado `indisponivel`.
- **CLAUDE.md da raiz**: a tabela de máquinas dizia Python 3.13.14 na .12; corrigido para 3.14.8.
- `wake_altservidor_ia.py`: só tipagem, conferida com `feature_version=(3, 8)` (o arquivo tem que
  rodar em Python 3.8+). O gate "só comentário" do web acusa a anotação como código, o que é
  esperado; o `check_docs_only_diff` aceita.

<span class="warn">Fora do escopo, não mexido:</span> o log do `registrar_rotina` ainda diz
"`ROTINAS_ESTADO_SUPABASE` desligada", mas a variável não existe mais (é texto de log).

## Decisões

1. ✅ **Traduzir PT existente** — a regra do CLAUDE.md era "não traduzir retroativo"; o Marcelo
   pediu explicitamente a tradução neste plano (03/11/2026).
2. **Texto de tela (MCP, `--help`, OpenAPI) fica em PT?** <span class="rec">Recomendo: sim</span> —
   traduzir mudaria o que a Mira e a equipe do PCP leem.
3. **E8 (irmãos) entra?** <span class="rec">Recomendo: só se sobrar fôlego</span> — exige mexer em
   2 repos no mesmo commit para 21 docstrings.
