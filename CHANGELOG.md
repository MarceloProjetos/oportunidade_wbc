# Changelog

Mudanças notáveis deste projeto. Formato inspirado em
[Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/).

Meses anteriores em `docs/changelog/AAAA-MM.md` (a raiz guarda só o mês corrente; ao virar
o mês, mova as entradas do mês que fechou para lá).

## [2026-09-24] — F5 do PLANO_DX_AGENTE: SQL do HANA com t-string

⚠️ Vale no proximo deploy (API e agendador leem o HANA por aqui; o worker pelo `batched`).

- **`sql_seguro.py`** (novo): `sql(t"...")` — primeira coisa do repo que so existe no
  Python 3.14 (PEP 750). `{valor}` vira `?` + parametro (o driver manda o dado separado do
  SQL); `{nome:ident}` cola identificador conferido por regex; `{n:int}` cola literal
  inteiro onde o HANA nao aceita `?` (`LIMIT`); qualquer outro formato e' erro.
  `nome_simples()` confere o `SAP_SCHEMA`. 17 testes (`tests/test_sql_seguro.py`).
- **`SAPExtractor.execute_query(sql, params=None)`**: o parametro chega ao `cursor.execute`
  (o `db_utils` ja aceitava; nenhum caller usava).
- **Pipeline de OS** (`extract_ordens_servico_engenharia`): as 5 consultas em que o NPED
  vem da URL passam a mandar o numero como **parametro**, nao colado no texto (antes era
  `coerce_positive_int` + f-string — seguro por disciplina). Conferido **contra o HANA de
  producao, so leitura**: `listar_pedidos_com_os(3)` → 3 linhas (o `LIMIT` literal),
  `extract_os_to_dataframe(84425)` → 44 linhas, `diagnosticar_nped` e
  `consultar_status_pedido` com o pedido certo.
- **`situacao_pedidos_hana._schema()`**: o `SAP_SCHEMA` do `.env` entrava no SQL entre
  aspas **sem validacao**; agora passa por `nome_simples` (invalido → `SAPIndisponivel`).
- **`itertools.batched`** nos 3 lacos que fatiavam lote na mao (upsert e insert do
  `SupabaseLoader`, situacoes do WBC em lotes de 1000). Teste novo do fatiamento real
  (`tests/wbc/infrastructure/test_situacoes_em_lote.py`). ⚠️ O de situacoes nao foi rodado
  contra o SQL Server do WBC: o `pymssql` so existe na .11.
- CLAUDE.md: regra "valor de fora no SQL do HANA = `sql(t...)`" e a nota do
  `python -m pdb -p <PID>` do 3.14 (anexar a processo vivo — nao exercitado ainda).

**Ficaram como estao, de proposito**: Vendas BI e o conferidor (o ano ja e' `int()`, o schema
ja e' validado e os testes cravam o texto do SQL — trocar seria churn); o `IN (...)` de
municipios do `situacao_pedidos_hana` (inteiros vindos de `isdigit()`); `pedidos_bloqueados`
(3 copias em 3 repos); `-{MESES_RETROATIVOS}` (constante do codigo, nao entrada).

Suite: **1856 passed**, 0 falhas; `ruff check .` = 0.

## [2026-09-24] — F4 do PLANO_DX_AGENTE: uma decisao, um lugar

⚠️ Toca o worker, o painel, a API e o agendador — vale no proximo deploy (restart dos 5,
com a parada por arquivo do worker que o `deploy_update.bat` ja faz). Nenhum comportamento
novo de escrita no SAP.

- **Previa e ciclo decidem pelo MESMO caminho** (achado A6). O `wbcpython pendentes`
  remontava a decisao de dois passos por conta propria, importando o `_OrcamentoResumido`
  privado do processador, e carregava o orcamento inteiro por outra porta
  (`ACOES_QUE_ESCREVEM` em vez de `tem_acao` — iguais hoje so porque os dois conjuntos
  coincidem). Agora os dois usam `processar.decidir_pela_situacao`,
  `precisa_do_orcamento` e `decidir_pelo_orcamento`. Na previa, "resultaria em escrita"
  passa a contar pela decisao FINAL (a do orcamento inteiro pode perder acoes — orcamento
  sem item), nao pela do 1o passo. Teste novo: `tests/wbc/application/test_decisao_compartilhada.py`
  (toda `Acao` do dominio conta como escrita na previa; a CLI usa as funcoes do processador).
- **`api.py`**: `_disparar_carga` junta as rotas de carga completa de oportunidades e de
  Vendas BI (eram a mesma funcao escrita duas vezes; mensagens e codigos iguais aos de
  antes); `_inteiro_positivo` + `errorhandler` trocam o `try/except ValueError → 400` que se
  repetia em 7 rotas (mesma resposta); docstring do modulo lista as 24 rotas (listava 13).
- **`pipeline_core`**: os 4 locks de arquivo (oportunidades, vendas, espelho, OS por pedido)
  passam por um `_file_lock` so; os nomes publicos ficam.
- `venda_comum._inteiro` → `_codigo_ou_none`: e' o unico dos 5 `_inteiro` do pacote em que
  **0 vira None**; o nome igual convidava a troca.
- `.env.example`: os 4 tetos anti-loop que faltavam (`RATE_SYNC_OS_MAX`, `RATE_FORCE_OPORT_MAX`,
  `RATE_VENDAS_BI_MAX`, `SYNC_LOTE_MAX`).

**Ficaram de fora, de proposito**: mover os `RATE_*` para o `Settings` (sao lidos no import
e 6 testes os trocam por monkeypatch no modulo — ficaram documentados no `.env.example` e no
CLAUDE.md); anotar as 9 tools MCP antigas como leitura (o proprio `mcp_server.py` registra a
decisao de nao mexer "ate haver motivo", e o `mcp` desta maquina e' 2.1.1 — o arquivo nem
importa aqui, nao haveria como testar).

Suite: **1837 passed**, 0 falhas; `ruff check .` = 0.

## [2026-09-24] — F2 do PLANO_DX_AGENTE: gate antes do commit e sintaxe do 3.14

- **`.githooks/pre-commit`** (novo, versionado): `ruff check` + a suite inteira (~40 s) antes
  de todo commit; qualquer falha barra. E' o unico gate automatico do repo (nao ha CI).
  Ativado nesta maquina com `git config core.hooksPath .githooks`; clone novo precisa do
  mesmo comando. Sem ruff no Python, o hook usa `uvx ruff@0.15.20` (nada instalado no Python).
- **`pyproject.toml`**: `target-version = "py314"` (era `py311`, "dev = 3.12") e a regra
  **`UP`** (pyupgrade). O `ruff --fix` modernizou 355 pontos em 21 arquivos, sem mudar
  comportamento: `Optional[X]` → `X | None`, `List`/`Dict` → `list`/`dict`, `typing` →
  `collections.abc`, `timezone.utc` → `datetime.UTC`, imports ordenados. Os
  **arquivos-irmaos** de outro repo ficam FORA do `UP` (`per-file-ignores`):
  `ordens_producao_sl`, `windows_update` (+ o teste dele), `situacao_pedidos`,
  `sap_montagem_labels`, `pedidos_bloqueados`, `wake_altservidor_ia` (Python 3.8+).
- Os 6 erros de lint que ja existiam (ninguem rodava o ruff: ele nem estava instalado)
  foram corrigidos — 4 de ordem de import, 2 linhas longas em `maintenance/medir_porta_paletes.py`.
  `ruff check .` = **0**.
- `requirements-dev.txt`: `pytest==8.4.2` (a versao que de fato roda aqui).

⚠️ Codigo de producao mudou (so anotacao de tipo e import): vale no proximo deploy, sem
pressa — nenhum comportamento novo. Suite: **1833 passed**, 0 falhas.

## [2026-09-24] — F3 do PLANO_DX_AGENTE: limpeza

Nada do que roda muda de comportamento; o que saiu nao tinha nenhuma referencia.

- **Codigo morto** (conferido por grep no repo inteiro, testes incluidos):
  `extract_orcamentos_espelho._info` e o re-export `orcamentos_espelho_sync_lock`; os
  re-exports "backward compat" do `extract_sap_to_supabase` (`SAP_PORT_DEFAULT`,
  `SQL_ENRICHMENT_VIEW_DEFAULT`, `SYNC_LOG_TABLE_NAME`, `with_retries`);
  `wbcpython.cli._cmd_nao_implementado`; o alias `feriados_br.FERIADOS_ANO_INICIO`.
- **`CHANGELOG.md` partido por mes**: a raiz guarda so setembro (189 KB → 83 KB); junho a
  agosto em `docs/changelog/AAAA-MM.md`, com os links relativos reapontados.
- **`sql/migracoes/`**: as 5 alteracoes ja aplicadas (`alter_vw_os_integracao_*`,
  `alter_bi_vendas_ranking_escopo`, `migracao_bi_vendas_serie_orcamentos`) — movidas, nao
  apagadas. `migracao_bi_vendas_ranking_uf.sql` fica: o codigo a cita.
- **`docs/arquivo/`**: `PLANO_PYTHON_314_NA_11` e `PLANO_PORTA_PALETES_QUANTIDADE`
  (ENCERRADOS, sem citacao no codigo). O `PLANO_STATUS_E_ENDERECO_ENTREGA` **ficou** em
  `docs/`: ainda tem pendencia (A4 e o envio do doc a outra equipe).
- **`docs/wbc/DECISOES.md`**: indice no topo e o `##` que faltava ("Painel, CLI e worker em
  producao") — ~25 decisoes de setembro estavam, sem titulo proprio, dentro da secao da
  comparacao do OrcDetalhe. ⚠️ O plano previa mover essa secao para um apendice por ser
  "evidencia bruta"; lida, ela e' quase toda decisao viva (inclusive a "Virada para producao"
  que o CLAUDE.md cita) — nada foi movido.
- `.ruff_cache/` no `.gitignore`; worktree `.claude/worktrees/sleepy-lovelace-30ef4f` e a
  branch dela removidas (o commit ja estava na master).

## [2026-09-24] — F1 do PLANO_DX_AGENTE: CLAUDE.md so com o que e' verdade hoje

So documentacao e comentario — nenhum comportamento muda.

- **`CLAUDE.md`** (231 → 210 linhas): corrigidas as afirmacoes velhas — pip no **Python 3.14**
  (a linha 168 ainda dizia 3.12 e contradizia a 202), agendador com **4 jobs** (nao so
  oportunidades), os imports reais do `api.py`, "843 testes", "28 linhas", `run_wbc_*.bat`
  (so existe o do painel), o servico `OrcaView-MCP`, a pasta `web_orcaview_V118`.
  Acrescentados ao mapa os modulos que faltavam (`situacao_pedidos*`, `pedidos_bloqueados`
  com as 3 copias, `wake_altservidor_ia` byte-identico, `extract_orcamentos_espelho`,
  `retry`, `API_*.md`, `maintenance/`), linhas da tabela "Tarefa → ler" para Vendas BI,
  `/pedidos/*`, RH e `/usuarios-ativos`, e os gotchas novos: a trava da suite (F0), o
  `.gitignore _*.py`, os defaults do worker em dois configs, o `hdbcli` que derruba o
  processo. **Regra nova: comentarios e docstrings em portugues** (D1 do plano).
- **`docs/INCIDENTES.md`** (novo): a historia que saiu do CLAUDE.md (610 dias da .12, ciclo
  #133, worker meio dia no 3.12, R$ do Power BI, `-1116`…), sem perder fato.
- `web_orcaview_V117/` → `V118/` nos caminhos citados em comentario (`api`, `config`,
  `monitoring`, `ordens_producao_sl`, `mcp_server`, `situacao_pedidos`, `sap_montagem_labels`,
  `requirements.txt`, `API_RH_COLABORADORES.md`). Numeros de versao antigos (`V117.834`)
  ficaram — sao historia.
- README (badge e tabela 3.14, arvore com os ~15 arquivos que faltavam),
  `deploy_update.bat` (comentario Python312), `install_wol_task.ps1` (sem o fallback do
  Python312, desinstalado em 14/09), `run_wbc_painel.bat` (instalador certo).

## [2026-09-24] — F0 do PLANO_DX_AGENTE: a suite nao alcanca mais producao

Plano novo: `docs/PLANO_DX_AGENTE.md` (o que facilita o trabalho do agente neste repo). Esta
e' a F0, a rede de protecao — so testes, config e `requirements.txt`; nada muda no que roda.

- **`tests/conftest.py`**: toda a suite (fora `@pytest.mark.integration`) roda com os destinos
  do `.env` trocados por falsos (`SUPABASE_*`, `SAP_*`, `SQL*`, `OP_SL_*`) e com os drivers
  travados (`hdbcli.dbapi.connect`, `pyodbc.connect`, `pymssql.connect`, `create_client` do
  Supabase): quem tenta conectar FALHA o teste dizendo o destino. Antes, so `tests/wbc` apagava
  `SAP_`/`SQL_`, e ninguem apagava `SUPABASE_*`.
  ⚠️ A trava pegou **12 testes que abriam conexao REAL**: 11 com o HANA de producao (o
  `GET /ordens-servico/<n>` le a ORDR; o caminho do pedido cancelado le o endereco) e 1 que
  relia o Supabase de producao depois de sincronizar. Os `client` de `test_api.py` e
  `test_api_situacao_pedidos.py` agora dublam `consultar_status_pedido`, `_fetch_os_detalhe`
  e `fetch_endereco_do_pedido` por padrao.
  ⚠️ So o endereco falso nao bastava: o **`hdbcli` 2.29.25 no Python 3.14 derruba o processo
  com access violation quando a conexao falha** (medido nesta maquina, 127.0.0.1, `localhost`
  e nome inexistente; com `CONNECTTIMEOUT` menor que o tempo da recusa ele levanta erro
  normal). O pytest morria sem dizer qual teste — dai a trava nos drivers.
- **`wbcpython/config.py`**: o default de `TRACKING_DB_URL` passa a ser o da raiz,
  `sqlite:///./state/wbc_tracking.db` (era `./wbcpython_tracking.db`). Sem a variavel no
  `.env`, o worker gravava num arquivo e o `/status` lia outro. **Sem efeito na .11**: o
  `/status` de la le `state\wbc_tracking.db` e ve o ciclo de 1 minuto atras (conferido pela
  tool `estado_integracao_wbc`), ou seja, o `.env` ja define o caminho.
- **`tests/test_config_paridade_wbc.py`** (novo): os defaults do worker que a raiz copia
  (banco, intervalo, expediente, dias, porta do painel) tem de ser iguais nos dois configs.
- **`requirements.txt`**: `httpx` passa a ser dependencia DIRETA (o worker o importa para
  falar com o Service Layer; chegava so pelo `supabase`); piso do `pymssql` em 2.4.1 (a
  primeira com roda cp314); comentarios do Python 3.12 atualizados. O `deploy_update.bat`
  vai rodar o `pip` (o hash mudou) — nada novo a instalar.

Suite: **1833 passed**, 12 skipped, 0 falhas.

## [2026-09-21] — Pedido 84337 fora dos agregados de Vendas

O pedido 84337 (NAVARRO, R$ 93.531,74, 02/09/2026) esta FECHADO no SAP **sem entrega e sem
nota** (`RDR1.TargetType = -1`): foi fechado na mao. Mesmo assim entrava no total de Pedidos
do mes, no cartao "Mes atual" e no ranking de clientes do celular — uma venda que nao
aconteceu. Decisao do Marcelo (21/09): lista de bloqueio **hardcode**, nao flag no `.env`.

- **`pedidos_bloqueados.py`** (novo): a lista (`DocNum` 84337) e o pedaco de SQL que a aplica.
  E o unico lugar a mexer para acrescentar outro pedido nesta maquina.
- **`extract_vendas_bi.py`**: as duas consultas da `VW_PEDIDO_ALTA` (serie mensal e detalhe
  recente) cortam a lista. Como a carga **poda** o que ela nao reescreveu, a linha antiga do
  ranking sai sozinha na corrida seguinte — nao ha limpeza manual no Supabase.
  ⚠️ A partir daqui **Pedidos nao bate com o Power BI por desenho**: setembro/2026 sai
  R$ 93.531,74 menor, e isso e a correcao. Faturamento nao muda (esse pedido nunca virou nota).
- **`maintenance/conferir_vendas_bi.py`**: o MESMO corte nas tres consultas da view — sem ele o
  conferidor acusaria divergencia contra um Supabase que esta certo.
- **`situacao_pedidos.normalizar`**: mesmo filtro, espelhado no gemeo do web (o
  `test_situacao_pedidos_diffavel` continua verde).

⚠️ Ha copias da lista no web (`web_orcaview_V118/backend/services/pedidos_bloqueados.py`) e no
app (`mobile_orcaview_V4/lib/pedidos/bloqueados.ts`): acrescentar um pedido exige os tres.

## [2026-09-17] — Orcamento sem item para de virar decisao de criar documento

O Marcelo viu no painel: o orcamento 00125188 (SitCode 20, ZERO item) acumulou 100 eventos
num dia — a cada 3 minutos a decisao dizia "cria a cotacao", o executor recusava em
`_exigir_valor` e gravava "Cotacao nao criada: o orcamento esta sem itens". O erro estava na
DECISAO, nao no executor.

- **`sitcode._sem_itens`** (novo filtro, irmao do `_sem_pedido`): com
  `EstadoIntegracao.orcamento_sem_itens`, a decisao sai sem as acoes de documento
  (criar/atualizar/cancelar-e-recriar de cotacao e pedido) e sem o vinculo, com a regra
  `<regra>+sem_itens_no_wbc` e o motivo explicito. **Nao** tira cancelar a cotacao no
  encerramento, marcar a oportunidade perdida nem espelhar status: nenhuma precisa de linha.
- **`montar_estado`** marca o campo com `getattr(orcamento, "itens", None)`: o PRE-FILTRO
  decide com `_OrcamentoResumido`, que ainda nao carregou as linhas — ali "nao sei" nao pode
  virar "nao tem", senao o ciclo deixaria de carregar o orcamento e nunca descobriria que
  ele tem item.
- **A previa (`wbcpython pendentes`) continua avisando**: "documento NAO seria criado: o
  orcamento nao tem item no WBC". Sem isso o orcamento sumiria da previa sem explicacao.
- **`_exigir_valor` fica** no executor: item a preco ZERO e outra coisa, so o payload montado
  revela, e esse evento continua sendo gravado.
- Escala do problema, medida no WBC: **167 orcamentos sem item** na janela de 24 meses (de
  6.260). A janela sob demanda (15/09) foi o que trouxe todos eles para dentro do ciclo.
- tests/wbc: 1.199 passaram (+ `domain/test_sem_itens_no_wbc.py`); suite inteira 1.829.

## [2026-09-17] — Espelho dos orcamentos (VW_EVOL_ORCAMENTO_ALT) no Supabase

A view de orcamentos foi refeita em 09/2026 e passou de 12 para 34 colunas: ganhou o CNAE do
cliente, o bloco de montagem (TipoMontagem/ValorMontagem/Montador) e o de nota fiscal
(NumNF/DataNF/QuitacaoNF). O espelho leva isso ao Supabase para o web e o app lerem sem
depender do HANA.

- **`extract_orcamentos_espelho.py`** — snapshot (carrega-depois-poda) da view inteira,
  de hora em hora dentro da janela comercial, com lock proprio
  (`orcamentos_espelho_sync_lock`) e desfecho gravado em `rotinas_execucao`. View vazia ou
  consulta que falha **nao podam**: no pior caso a tabela fica com o snapshot anterior.
- **`sql/orcamentos_espelho.sql`** — a tabela (34 colunas + controle), indices e RLS. Leitura
  para `authenticated`; nada para `anon` (o fechamento do anon esta em curso) — a policy de
  anon fica comentada no fim do arquivo.
- **`nf_quitada` e booleano de tres estados**: `NULL` quando nao ha nota. Medido em 17/09 na
  view inteira (5.643 linhas): 1.252 `true`, 82 `false`, 4.309 `NULL` — o `QuitacaoNF` cru diz
  "Nao" para 4.310 linhas so porque nota nenhuma foi emitida, e quem lesse isso como "nota em
  aberto" erraria por um fator de tres. Texto vazio (`AcaoContato`, `Lead`,
  `SituacaoCliente`) entra como `NULL`.
- **Pendem**: o DDL no SQL Editor do Supabase e o restart do scheduler da `.11`. Enquanto a
  tabela nao existir, o job registra falha e nao escreve nada.

## [2026-09-17] — Auditoria estatica: tres defeitos no caminho de escrita do worker

Achados por leitura de codigo (sem executar nada), cada um rastreado ate o cenario concreto.

- **Revisao nova ignorada com o SAP em "55"** (`domain/sitcode.py`). No ramo de revisao
  (WBC 40/55 com cotacao) so `U_INO_StatusWBC` "30" e "40" eram tratados; "55" caia no ramo
  final, que apenas espelha status. Como `_espelhar_status_apos_documento` grava 55 depois de
  mexer na cotacao (caso real: oportunidade 14803), toda revisao seguinte em 55 saia como
  `sem_acao` e a cotacao ficava na revisao anterior. Agora "40" e "55" entram juntos em
  `_ramo_revisao_ja_registrada`, como ja entravam no ramo do SitCode 30.
- **Fora da janela padrao, a cotacao de todo SitCode 60 sem pedido era reescrita a cada ciclo
  estendido** (`_sem_pedido`). O `ATUALIZAR_COTACAO` de `cria_pedido` e incondicional e so e
  idempotente porque o pedido nasce em seguida; tirando o pedido, a condicao de parada sumia:
  dois PATCH por orcamento a cada passada, escrita contada no teto, "cotacao atualizada" no
  historico sem mudanca no WBC. Agora, fora da janela, o `ATUALIZAR_COTACAO` herdado de
  `cria_pedido` so fica se a revisao do WBC for mais nova que a da cotacao; sem acao de
  documento, o vinculo sai junto. Dentro da janela nada muda.
- **`U_INO_VERSAOWBC` carimbado so no segundo PATCH** (`service_layer/documentos.py`). Se o
  passo 1 (UnitPrice) gravava e o passo 2 (LineTotal) falhava, o documento ficava com o
  centavo recalculado **e** a revisao nova — e o ciclo seguinte lia "revisao congelada" para
  sempre. Com o carimbo por ultimo, a falha no meio deixa a revisao antiga e o proximo ciclo
  refaz os dois passos.

Fora da lista, por ja constar em `docs/wbc/DECISOES.md` como "fica em aberto": o cancelamento
repetido de cotacao **fechada** no encerramento (um evento de erro por ciclo).

## [2026-09-15] — Atualizacao de documento: UnitPrice antes, LineTotal depois

Incidente em producao no mesmo dia da virada: a cotacao 78264 (00123897, rev. E) saiu com
"unitario R$ 3.088,86 e desconto 46,24%" numa linha de R$ 1.660,66, e desconto negativo em
linhas de uma unidade. O total estava certo; o unitario e o desconto, nao. Causa: no PATCH o
Service Layer mantem o `UnitPrice` da revisao anterior e fecha a conta com `DiscountPercent`
quando so `LineTotal` vai. Medido em homologacao com seis payloads (tabela em
`docs/wbc/DECISOES.md`, "Preco unitario e desconto no PATCH"): se o `UnitPrice` muda, o SAP
recalcula o `LineTotal` a partir dele; se nao muda, respeita o `LineTotal`.

- `RepositorioDocumentosVendaServiceLayer.atualizar` passa a fazer **dois PATCH**: o primeiro
  com `UnitPrice = LineTotal / Quantity` (4 casas) em cada linha, o segundo com as linhas como
  o dominio montou. Criacao (`POST`) nao muda: linha nova ja nasce com unitario derivado e
  desconto zero. Provado com o ciclo real em homologacao (00125058: cotacao atualizada e
  pedido criado, unitario certo, desconto 0, total exato).
- Em producao ficaram **2 cotacoes** com o artefato (78264 e 78285). Antes do reparo o worker
  ja as tinha recriado (78289 e 78291, limpas por construcao — `POST`); o reparo autorizado
  reenviou as linhas das novas e confirmou unitario, desconto 0 e `DocTotal` inalterado.
- Uma terceira (78287) foi tocada pelo codigo antigo as 13:56; o worker a recriou (78292) e o reparo
  confirmou. Varredura final: zero vigentes com o artefato. Worker da .11 reiniciado as 14:11:50
  com a correcao.

## [2026-09-15] — F3: ensaio em homologacao fechou com zero centavos

Ensaio da troca por `LineTotal` em `SBOALTAMIRAHOMOLOG` (trava de producao ativa; rodado da
estacao com o pacote de `master`). Tres orcamentos, tres caminhos: `00125535` cancelar e
recriar cotacao (POST, 16 modulos), `00125442` atualizar cotacao (PATCH que trocou 3 por 6
linhas, 96 e 168 modulos, conferencia `[line-total]` fechou em 722.568,54), `00125516` PATCH +
criacao de pedido (2 e 3 modulos). **17 linhas relidas do SAP, `LineTotal == ORCVAL` em
todas**; unidade UN nas linhas novas. No `00125516` o SL aceitou o PATCH sem trocar as linhas
e a conferencia por `LineTotal` pegou (R$ 11.093,32 de diferenca), cancelou e recriou — a rede
de seguranca continua valendo com o campo novo. Registro em `docs/wbc/DECISOES.md`, "F3".
Nada muda no codigo. O worker da .11 ja roda este codigo em producao desde o restart de 15/09.

## [2026-09-15] — Porta-paletes: quantidade lida do texto, LineTotal e tema no log

F0–F2 de `docs/PLANO_PORTA_PALETES_QUANTIDADE.md`. O item PORTA-PALETES nascia no SAP com
quantidade 1 quando o orcamento dizia "14 Modulos": `ORCPRDQTD` e nula nas 21.447 linhas do WBC.

- **Quantidade lida do `ORCTXT`** (`domain/linhas.py`: `quantidade_no_texto`, `eh_porta_paletes`):
  o inteiro antes da primeira "Modulo(s)" depois de "porta-paletes", em qualquer grafia. Aceita
  rotulo antes do nome ("AREA: SECA PORTA-PALETES 226 Modulos"); recusa acessorio ("STOPS PARA
  PORTA-PALETES") e descricao ja comecada. Medido na base inteira com
  `maintenance/medir_porta_paletes.py`: 6.442 linhas, **6.299 lidas (97,8%)**, 143 sem numero
  (ficam em 1). Precedencia: `ORCPRDQTD` > texto > 1. Estante com "N Modulos" segue em 1.
- **`LineTotal` no lugar de `Price`**: `Price = ORCVAL / qtd` tem 4 casas no SAP e 3 das 18 linhas
  do ensaio ficaram 1 centavo fora; com o total enviado, o SAP deriva o preco e o total bate por
  construcao. Um campo so. `MeasureUnit` nao vai (teste-guarda). Os tres pontos que somavam
  `Quantity x Price` mudaram juntos: `total_do_payload`, `total_das_linhas` (le `LineTotal` do
  SAP) e a previa do CLI.
- **Tema no log e no painel**: as linhas `[porta-paletes] ...` (INFO na leitura, WARNING sem
  numero) e `[line-total] ...` (conferencia pos-PATCH) ganham cor azul-aco na aba Log e um selo
  clicavel que filtra por tema. `logs.LinhaDeLog.tema`/`.texto`; CSS `?v=20260915`.
- `Weight1` continua peso de UMA unidade: com quantidade real, e dividido de fato.
- Suite: 1.800 passando, 12 skips. Pendem F3 (ensaio em homologacao: zero centavos de diferenca,
  conferir o caminho PATCH) e F4 (pull + restart na .11), os dois do Marcelo.

## [2026-09-15] — Plano: porta-paletes com quantidade lida do texto e LineTotal

`docs/PLANO_PORTA_PALETES_QUANTIDADE.md`. Relato do usuario: o item PORTA-PALETES sempre nasce
no SAP com quantidade 1. Nada foi implementado ainda — o plano registra que o relato anterior
("quantidade do texto ficou, CJ revertido") nao corresponde a nada em `master`, define a regra
(inteiro antes de "Modulo(s)", so quando precedido de porta-paletes em qualquer grafia), a troca
de `Price` por `LineTotal` e os tres pontos que somam `Quantity x Price` e mudam junto.

## [2026-09-15] — WOL do .90: segunda chance em 15 min e Python 3.14

Na manha de 15/09 a tarefa `OrcaView-WOL-AltservidorIA` disparou no boot da .11 e morreu com
`0x80070002` (arquivo nao encontrado): a acao estava presa a `C:\Program Files\Python312\python.exe`
e a maquina tinha migrado para o 3.14. O .90 ficou desligado ate alguem apertar o botao as 07:14.
Na vespera, com o 3.12 ainda presente, o magic packet acordou o .90 em 42 s — a primeira prova
real de que o Wake-on-LAN funciona ponta a ponta.

`install_wol_task.ps1`:

- **`-RetryMinutes` (padrao 15).** O gatilho de boot ganha uma repeticao de um unico disparo:
  se a janela de 600 s acabar sem o .90 responder, a tarefa roda de novo 15 min depois do
  primeiro disparo. Com o .90 ja acordado a repeticao so grava "JA responde ao ping" e sai.
  `0` desliga. O instalador recusa `RetryMinutes*60 <= Wait`, porque a repeticao cairia na
  instancia em curso e seria ignorada (`MultipleInstances IgnoreNew`).
- **Fallback do Python cobre `Python314` antes de `Python312`.** O PATH continua sendo a
  primeira fonte. Depois de qualquer troca de Python na .11, rode o instalador de novo.

## [2026-09-14] — Janela estendida nao cria nem altera pedido

Regra de negocio do Marcelo, depois do ensaio de 13 meses em producao: das 144 escritas que
o ciclo faria, varias eram **criar pedido** para oportunidades de 2025 — uma somando R$ 1,26
milhao em linhas, outra R$ 372 mil.

Agora, quando a oportunidade e mais antiga que a janela **padrao**, o ciclo acerta **cotacao e
oportunidade** e nao toca em pedido. Alcancar para tras serve para arrumar proposta e espelho
de status de negocio antigo; abrir pedido de negocio de mais de seis meses e outra decisao, e
alguem pode ja te-lo resolvido a mao no SAP nesse tempo.

Continua valendo fora da janela: criar/atualizar/cancelar **cotacao** — inclusive o
`cancelar_cotacao_no_encerramento`, que e o caso mais comum do ensaio —, alem de
`atualizar_status_oportunidade` e `marcar_oportunidade_perdida`. Sai `criar_pedido`,
`atualizar_pedido` e `cancelar_e_recriar_pedido`: as tres, porque alterar e recriar tambem
mexem num documento de compromisso.

A regra mora no **dominio** (`_sem_pedido`, em `domain/sitcode.py`), e nao no processador: a
previa e o ciclo chamam a mesma `decidir`, entao o ensaio ja mostra o efeito. Um filtro na
execucao deixaria a previa prometendo pedidos que o ciclo nao criaria.

`EstadoIntegracao.fora_da_janela_padrao` e calculado por `montar_estado`, comparando
`OOPR.OpenDate` com o corte da janela padrao. **Sem `OpenDate` a resposta e "dentro"**: na
duvida, o comportamento e o de sempre — data ausente virando bloqueio silencioso faria um
ciclo NORMAL parar de criar pedidos sem ninguem entender por que.

Num ciclo normal a regra nao morde: nada que e lido e anterior ao corte.

Suite do WBC: 1.141 testes (+14).

## [2026-09-14] — Janela de busca: cartao simples, sem senha

Pedido do Marcelo: *"poderia ser um card dentro de Leitura e diagnostico, sem senha e com
menos textos — torne o acesso mais simples"*.

O bloco a parte virou o **primeiro cartao da grade de "Leitura e diagnostico"**, ao lado de
"Verificar pendentes": sao os dois passos do mesmo gesto — ensaiar a janela e depois arma-la.
Como cartao da grade ele herda a forma dos vizinhos e para de parecer tela de configuracao.
Quatro paragrafos viraram um: o que precisa ficar claro e **data de abertura** e **volta
sozinha**; o resto esta no plano.

**A senha saiu e voltou no mesmo dia.** Saiu de manha, em nome da simplicidade; voltou a tarde,
por decisao do Marcelo depois de ver o ensaio de 13 meses (144 escritas, com pedidos de mais de
R$ 1 milhao). Sem o bloqueio de producao na frente, ela e a unica coisa entre um clique e essas
escritas. O **nome** continua obrigatorio ao lado dela: senha autoriza, nome audita.

Tambem nesta leva: "Verificar pendentes" passa a vir ANTES da "Janela de busca" (ensaia, depois
arma), e "Testar conexao com o SAP" + "Testar o HANA" viram UM cartao com dois botoes. A grade
fecha em 6 cartoes.

A dispensa e so do armar e nao vaza: os comandos do catalogo, "Ciclo de integracao" incluido,
seguem exigindo `PAINEL_SENHA` e seguem bloqueados em producao. `Settings.painel_pode_armar_janela`
morreu junto com a senha.

Suite do WBC: 1.107 testes.

## [2026-09-11] — Janela sob demanda: armar vira excecao ao bloqueio de producao

Correcao do deploy de hoje. O card "Janela de busca" subiu na .11 **sem o formulario de
armar**: estava amarrado ao `painel_pode_escrever`, que e falso por desenho quando o painel
aponta para producao. Quem e de vendas via a ajuda e nenhum controle — armar voltava a ser
`wbcpython janela --armar` no terminal, que e o "chamar o TI" que o trabalho existia para
eliminar.

O erro foi juntar duas guardas diferentes. A de producao (`RISCOS_PRODUCAO.md`) existe para
impedir que um clique **dispare** um ciclo de escrita. Armar nao dispara nada: o worker ja
roda sozinho em producao a cada `WORKER_INTERVAL_SECONDS`, e o pedido so muda **quao para
tras** esse ciclo automatico olha.

Novo `Settings.painel_pode_armar_janela`: pede so a senha, e nao olha o alvo. Os comandos do
catalogo (inclusive "Ciclo de integracao") **seguem bloqueados** em producao — ha teste
cravando os dois lados. Em producao o card ganhou aviso proprio de que o proximo ciclo
escreve documentos de verdade e que isso nao se desfaz.

Consequencia que a senha passa a carregar sozinha: sem o bloqueio de producao na frente, ela e
a **unica** guarda do armar. Com `PAINEL_SENHA` vazia, `compare_digest("", "")` e verdadeiro e
quem nao digitasse nada passaria — por isso ela e conferida como pre-condicao (existe?), e nao
apenas comparada.

Suite do WBC: 1.098 testes (+5). Pende um segundo pull + restart na .11.

## [2026-09-11] — Janela de busca sob demanda: a tela arma, o ciclo devolve

`MESES_DE_JANELA` deixa de ser so uma constante do `.env` lida no arranque. Pela aba
"Executar" do painel 8079, alguem de vendas pede ate **24 meses** para a PROXIMA passada do
ciclo, sem restart e sem TI — e a janela volta ao padrao de 6 sozinha. Plano completo em
`docs/PLANO_JANELA_SOB_DEMANDA.md`; decisoes em `docs/wbc/DECISOES.md`.

**O furo que isso resolve.** "Abre 24 meses por um ciclo e volta" nao funcionava: o
`LIMITE_DE_ESCRITA_POR_CICLO` (200) corta o ciclo no meio, e 24 meses represam muito mais
que os ~1.785 da janela de 6. O ciclo escreveria 200 e o resto nunca sairia. Decisao do
Marcelo: o teto **cresce por banda** — 3x ate 12 meses, 6x ate 18, **9x** ate 24 (1.800
escritas), limitado pelo `TETO_ABSOLUTO_DE_ESCRITA` (2.000) — e, se o ciclo estourar o teto,
ele para, grava tudo e **pergunta** na tela se deve rodar outro.

**O que mudou no worker.** A janela era congelada em `self._meses` na construcao; agora e
resolvida a cada ciclo (`_janela_do_ciclo`). Era esse congelamento que fazia toda mudanca
exigir restart. Cada execucao registra com o que rodou: colunas novas `meses_da_janela` e
`teto_de_escrita` em `execucoes` (anulaveis; execucoes antigas ficam `None`, nao 0).

**Tres estados** (`wbcpython/domain/janela.py`, tabela `pedido_de_janela` de uma linha):
`ocioso` -> `armado` -> `aguardando`. Em `aguardando` os ciclos automaticos **voltam ao
padrao** — sem isso o worker varreria 24 meses a cada 180s enquanto ninguem responde. A
pergunta vence em **15 min** (`JANELA_ESPERA_MINUTOS`) e, ao vencer, o log diz onde o ciclo
parou: o orcamento da retomada e quantas ficaram.

**O que NAO consome o pedido:** o ensaio (`--simular`) e o `--orcamento`. O ensaio usa a
janela estendida de proposito — e com ele que se ve o tamanho do estrago —, e consumir ali
faria "conferir antes" ser a maneira de perder o pedido. Erro no ciclo tambem nao consome:
conta tentativa, e na terceira devolve.

**Senha:** armar e "rodar outro ciclo" exigem `PAINEL_SENHA`; **limpar nao** — frear so
reduz o que o proximo ciclo escreve. Restart e deploy sempre devolvem ao padrao.

Novo: `wbcpython janela [--ver|--armar N|--limpar]`. A lista do painel passa a seguir a
janela armada (duas telas sobre o mesmo assunto nao podem dar numeros diferentes), e a aba
"Execucoes" ganhou a coluna Janela. `painel.css?v=20260911`.

Suite do WBC: 1.093 testes (+41). **Nada rodou na .11** — falta `git pull` + restart.

## [2026-09-11] — Plano: janela de busca sob demanda (so documentacao)

`docs/PLANO_JANELA_SOB_DEMANDA.md`: plano para tirar `MESES_DE_JANELA` do `.env` e
transforma-la num pedido feito pela tela do painel 8079 — ate 24 meses, valendo para a
proxima passada e voltando a 6 sozinha. Nenhum codigo alterado.

O ponto que o plano existe para resolver: `LIMITE_DE_ESCRITA_POR_CICLO` (200) corta o
ciclo no meio, entao "abre 24 meses por um ciclo" escreveria 200 e abandonaria o resto.
Decisao do Marcelo: o teto cresce por banda (3x ate 12 meses, 6x ate 18, 9x ate 24) e,
se o ciclo estourar o teto, ele para, grava tudo e **pergunta** se o usuario quer outro
ciclo. Sete decisoes seguem abertas, todas com recomendacao no documento.

## [2026-09-11] — A .11 acorda o .90 no boot (Wake-on-LAN)

Pedido do Marcelo: quando a .11 ligar, ela liga o ALTSERVIDOR-IA (.90), que sobe o
OrcaView sozinho. **O lado do .90 ja existia** e nao foi tocado — ele tem autologon e a
tarefa `OrcaView Stack AtLogon`, que roda `tools\ops\subir_stack_boot.ps1` do
`web_orcaview_V118` 60s depois do logon (com guarda anti-instancia-dupla e backup das
sessoes do WhatsApp). Faltava so o primeiro elo: **acordar a maquina**.

A corrente completa:

```
.11 liga -> tarefa OrcaView-WOL-AltservidorIA (boot + 30s)
         -> wake_altservidor_ia.py manda o magic packet
         -> .90 acorda -> autologon (~16s) -> AtLogon (+60s)
         -> npm run start:all-detailed  = OrcaView no ar
```

**Arquivos novos:**

- `wake_altservidor_ia.py` — so biblioteca padrao (roda no Python 3.12 da .11 sem
  instalar nada). Monta o magic packet de 102 bytes (`0xFF` x6 + MAC x16) e manda em UDP
  nos 2 broadcasts x portas 9/7 x 3 repeticoes, **reenviando a rodada** a cada
  `--reenviar` segundos ate o .90 responder ao ping ou a janela `--wait` acabar. Se o IP
  ja responde, sai na hora dizendo que a maquina esta ligada. `--log` grava em arquivo
  (tarefa agendada nao tem console) e corta em 1 MB.
- `install_wol_task.ps1` — registra a tarefa como SYSTEM, gatilho no boot + 30s, janela
  de 600s reenviando a cada 60s, log em `logs\wol_altservidor_ia.log`. Idempotente;
  `-Desinstalar` remove.

**Dois detalhes que valem mais que o codigo:**

1. O broadcast do .90 e **192.168.7.255**, nao .0.255 — a mascara e /21, a faixa termina
   em .7.255. A .11 (192.168.7.11) esta dentro dessa faixa: mesma rede, o broadcast chega.
2. O reenvio nao e paranoia: no boot a placa da .11 ainda esta subindo, e broadcast que
   sai antes da porta do switch convergir se perde. Uma rodada custa 1,2 KB.

**A .11 reinicia todo dia ~06:12**, entao na pratica isto vira "liga o .90 toda manha".
Se o .90 tiver sido desligado de proposito, o proximo reboot da .11 religa —
`install_wol_task.ps1 -Desinstalar` e a saida.

**PENDENTE, e nao da para testar por software:** os 2 itens da BIOS do .90 —
`Power On By PCI-E` = Enabled e `ErP Ready` = **Disabled**. Com ErP ligado a placa-mae
corta a energia da placa de rede no S5 e o Windows **continua reportando "Wake on Magic
Packet: habilitado" do mesmo jeito**; o diagnostico pelo SO da verde e a maquina nao
acorda. Enquanto esses dois nao forem confirmados, a corrente acima e teoria a partir do
elo 3. (E se o cenario for queda de energia: algumas placas perdem o armamento do WOL
quando falta luz — ai quem resolve e `Restore on AC Power Loss = Power On`, na mesma
visita a BIOS.)

## [2026-09-14] — Faxina de encerramento: 4 arquivos mortos e uma armadilha de deploy

Varredura do repo inteiro (231 arquivos) atras de plano encerrado, script sem dono e
codigo morto. O achado que justificou a faxina **nao era lixo — era risco**:

### ⚠️ Dois instaladores instalavam os MESMOS servicos, e divergiam

`install_services.bat` instalava os quatro (Scheduler, OS-API, WBC-Painel, WBC-Worker) e
`install_wbc_services.bat` instalava os dois do WBC de novo. Nao era so duplicacao: o
primeiro registrava o worker com **`run_wbc_worker.bat`** e o segundo com o **`python.exe`
direto**. Rodar o `install_services.bat` hoje **desfaria calado** a decisao de 08/09 — com o
`.bat` no meio, o Ctrl+C do NSSM morre no "Terminate batch job (Y/N)?" e a parada do worker
so termina quando o NSSM mata a arvore aos 60 s (foi a trava de 30 min do ciclo #133).

Agora cada servico tem UM instalador: o `install_services.bat` cuida do Scheduler e da API,
e aponta para os outros dois (`install_mcp_service.bat`, `install_wbc_services.bat`). Com o
worker sem wrapper, o `run_wbc_worker.bat` ficou orfao e saiu.

### Removidos

| Arquivo | Por que |
| --- | --- |
| `run_wbc_worker.bat` | orfao: o NSSM chama o `python.exe` direto desde 08/09 |
| `sondagem_windows_update.ps1` | sondagem exploratoria de 16/07; virou o `windows_update.py`, zero referencia |
| `maintenance/ajustar_logs_nssm.bat` | one-shot aplicado em 11/09; os instaladores ja gravam `AppRotateFiles 0` + `CreationDisposition 2` |
| `maintenance/liberar_cancelamento_orcaview.py` | one-shot aplicado em 09/09 (PATCH `Users(144)` → 204, `DECISOES.md`). Era **escrita em producao** morando no repo para um uso que nao se repete; a receita ficou na DECISOES |
| `docs/wbc/PROGRESS.md` (87 KB) | decisao dele: o diario sessao a sessao do WBCPython, o unico doc cujo conteudo nao vira invariante. O *porque* de cada escolha ja mora no `DECISOES.md`; o *quando* esta no `git log` e aqui. Saiu com as 6 referencias vivas ajustadas — inclusive uma que o `wbcpython` imprimia na tela |

### O que NAO saiu, e por que

- **Os 7 planos de `docs/`.** Encerrado nao e' morto: sao eles que explicam as invariantes
  que o `CLAUDE.md` cita ("nasce `false`", "fail-closed", "`OCNT` fora do JOIN"). Apagar
  troca 150 KB por alguem refazendo uma decisao ja tomada.
- **`docs/wbc/ai_spec/00_index.md`** — cheguei a apagar e devolvi: 9 arquivos apontam para
  ele, e e' justamente por a spec original NAO existir que o ponteiro tem valor.
- **`maintenance/conferir_vendas_bi.py`** — parece orfao (ninguem importa), mas e' a rede de
  seguranca da fonte dupla de verdade do BI de vendas: numero errado ali nao acende log
  nenhum, so aparece bonito no celular de quem decide. So corrigi a referencia
  (`mobile_orcaview_V3` → `V4`).
- **`monitor_wbc_task.ps1` / `install_monitor_task.ps1`** — a tarefa legada esta desativada,
  mas `WBC_TASK_MONITOR=true` e' o rollback documentado.
- **`wake_altservidor_ia.py` / `install_wol_task.ps1`** — em producao: e' a .11 que acorda o
  .90 no boot.
- **`sql/`** — DDL de referencia, incluindo as migracoes datadas ja aplicadas: e' o historico
  de como o schema chegou onde esta.

Suite depois da faxina: **1754 passed**, 12 skipped.

## [2026-09-14] — A .11 roda Python 3.14.7

Migrada. `system.python` = **3.14.7**, os 5 servicos no ar, e a F3 do
`docs/PLANO_PYTHON_314_NA_11.md` passou inteira em producao:

| O que | Prova |
| --- | --- |
| `hdbcli` (HANA) | check `sap` 24 ms |
| `pyodbc` (SQL Server) | check `sql_server` 8 ms — e' `pyodbc.connect`, nao um ping |
| `pymssql` (WBCCAD) | worker **ciclo 976**: 1733 orcamentos, nenhum erro (o 863 era 3.12 — abaixo) |
| Service Layer | `wbcpython check-sap` → `[ok] Conexao OK (SBOALTAMIRAPROD)` |
| `pandas` 2.3.3 | agendador 07:40:21 `sucesso` (DataFrame → Supabase) |
| `mcp`/`fastmcp` | 8078 respondendo |
| Suite NA .11, no 3.14 | **1749 passed**, 29 skipped, 2 falhas explicadas — hoje **1744 / 0** |

Memoria caiu de 43,5% para **30,6%** depois da virada. O unico pin que bloqueava era o
`pandas==2.2.3` (sem roda cp314), trocado na entrada de 11/09.

### O worker ficou no 3.12 ate as 13:12 — e nada acusou

A virada da manha levou **4** dos 5 servicos. O **worker** ficou no 3.12, escrevendo em
producao, e nenhum sinal denunciou: o `/status` dizia `python: 3.14.7` (e' o do processo da
API), os ciclos corriam sem erro, zero alerta.

Causa, no `install_wbc_services.bat`: o worker e' o unico que **nao passa por um `.bat`** —
o NSSM chama o `python.exe` direto, com o caminho absoluto em `Application`, resolvido por
`where python` **no dia da instalacao** (de proposito: com o `.bat` no meio o Ctrl+C do NSSM
morria no "Terminate batch job (Y/N)?", 08/09). Os outros 4 chamam `python` do PATH e migram
sozinhos no reboot; ele nao migra nunca. Corrigido com
`nssm set OrcaView-WBC-Worker Application "C:\Program Files\Python314\python.exe"`, apos a
parada limpa por arquivo. O instalador e o CLAUDE.md ganharam o aviso.

**Quem responde a verdade e' o processo, nao o `/status`:**
`Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Select ProcessId, ExecutablePath`.

Isso invalidou uma prova dada por boa: o `pymssql` no 3.14 estava creditado ao "ciclo 863",
que era 3.12. A prova real e' o **ciclo 976** (13:14:31, no 3.14.7): 1733 orcamentos lidos do
WBCCAD, zero erro.

### O 3.12 saiu no mesmo dia (D4)

Depois da manha limpa e de um reboot que subiu os 5 no 3.14. O bundle nao estava em nenhum
`Uninstall` de HKLM — estava no **HKCU do `administrador`** (`Package Cache\{b6ce88eb-...}`),
e o 3.14.7 esta no mesmo lugar: e' o padrao desta maquina. Sobraram as 2 entradas orfas do
3.12 no PATH da maquina (assinatura conhecida do desinstalador), removidas lendo o valor
**bruto** do registro e regravando com `-Type ExpandString` — o par
`[Environment]::GetEnvironmentVariable/SetEnvironmentVariable` expandiria `%SystemRoot%` e
gravaria REG_SZ.

**O rollback barato acabou junto:** voltar ao 3.12 agora e' reinstalar Python em producao.

### ⚠️ O PATH nao chega aos servicos sem REBOOT

O achado que vale guardar. Depois de instalar o 3.14 e ve-lo primeiro no
`where.exe python`, **um `deploy_update.bat` inteiro rodou e os servicos continuaram no
3.12** (`system.python: 3.12.10`, `uptime_s: 94`). O Windows so entrega o `PATH` novo aos
**servicos** quando o Gerenciador de Servicos rele o ambiente — no reboot. `nssm restart`
nao basta: o servico herda o ambiente que o SCM ja tinha.

Isso abriu uma janela perigosa: **3.14 primeiro no PATH, `site-packages` vazio, e a .11
reinicia sozinha as 06:12.** Se o reboot tivesse vindo antes do `pip`, os 5 servicos
subiriam sem dependencia nenhuma.

**Ordem segura, para a proxima:** instalar → `del state\deps.sha256` →
`deploy_update.bat` (o `pip` instala no interpretador novo enquanto os servicos ainda
rodam no antigo) → **so entao** reiniciar.

### As 2 falhas da suite nao sao do 3.14

- `test_export_os_json.py`: o modulo e o teste foram **apagados do repo** na faxina
  `842bf02`; continuam em disco na .11 como orfaos (`??` no `git status`). Codigo morto
  testando codigo morto.
- `tests/wbc/test_logs.py::TestRuido`: **passa sozinha** no mesmo 3.14.7; so quebra na
  suite inteira, porque `logging` e' estado global e algum teste anterior deixou um
  handler que engoliu o registro. Os 2 orfaos a mais e' que mudaram a ordem de coleta.

✅ **Resolvidas no mesmo dia:** os 2 orfaos foram apagados da .11 e a suite de la ficou
**1744 passed, 29 skipped, 0 falhas**. A aritmetica confirma o diagnostico: sumiram **7 testes
coletados** (1780 → 1773) — os 6 que passavam e o 1 que falhava do arquivo orfao — e a
`TestRuido` **voltou ao verde sozinha**, sem ninguem tocar nela. Era a ordem de coleta mesmo.

`pytest==8.3.5` e `ruff==0.15.20` foram instalados no 3.14 da .11 (o deploy nunca os
instala — sao do `requirements-dev.txt`): e' o que permitiu rodar a suite la antes do
reboot.

**O 3.12 fica instalado** ate um dia limpo de observacao (D4 do plano). Rollback enquanto
isso: tirar o 3.14 da frente do `PATH` + reboot.

## [2026-09-11] — O NSSM para de renomear: agora ele ZERA o log a cada start

Decisao do Marcelo, depois do inventario da entrada abaixo: *"nao quero NSSM renomeia,
quero que apaga o antigo"*.

**O NSSM nao tem essa opcao.** A rotacao dele e' renomear, e so; nao existe parametro de
retencao ("guardar so N"). O que existe e' **`AppStdoutCreationDisposition`**: o default
`4` (OPEN_ALWAYS) faz o NSSM abrir em modo append, e o `2` (CREATE_ALWAYS) faz ele
**truncar** o arquivo a cada start do servico.

Entao a configuracao dos 4 servicos que capturam stdout passou a ser:

```
AppRotateFiles 0                      (era 1)
AppRotateBytes                        (resetado; era 5000000)
AppStdoutCreationDisposition 2        (novo)
AppStderrCreationDisposition 2        (novo)
```

Nunca mais existe arquivo renomeado, e cada log guarda no maximo o que saiu desde o
ultimo start. **Com o reboot diario da .11 (~06:12) isso e' um dia** — limite natural,
sem faxineiro nenhum.

**Nao se perde historico:** o arquivo do NSSM e' so o stdout/stderr cru. O log que
importa cada servico escreve por conta propria e ja se limpa sozinho — `logs\api.log` e
`logs\scheduled_execution.log` (6 dias), `logs\wbcpython.log` (5 MB x 3). O do NSSM
serve para o que escapa do logger: crash antes do logging subir.

- `install_services.bat` e `install_wbc_services.bat` ajustados (valem em instalacao nova);
- **`maintenance/ajustar_logs_nssm.bat`** (novo) aplica nos servicos **JA instalados**, que
  e' o caso da .11 — o `install_*.bat` nao os alcanca. Ele **nao reinicia nada** de
  proposito: vale no proximo start (reboot diario ou `deploy_update.bat`). Confere o que
  gravou com `nssm get` e lista os renomeados que ja existam.

> ✅ **Aplicado na .11 em 11/09, 08:4x** — o `ajustar_logs_nssm.bat` rodou sem nenhum
> `AVISO` e o `nssm get` confirmou `AppStdoutCreationDisposition = 2` nos quatro
> servicos. (O nome do parametro nao pode ser verificado antes: nao ha `nssm` na maquina
> de desenvolvimento nem WinRM para a .11 — por isso o script foi escrito para tratar a
> recusa e conferir com `nssm get` no fim. A build de la aceita.) Zero renomeados a
> limpar. **Vale no proximo start** de cada servico: o reboot diario das ~06:12 ou o
> `deploy_update.bat` da migracao do Python.

## [2026-09-11] — Os logs que o NSSM renomeia nunca eram apagados

Pergunta do Marcelo: "os logs da .11 apagam sozinhos apos 6 dias, correto?". **So tres
deles.** O inventario que a pergunta rendeu:

| Arquivo | Politica | Apaga sozinho? |
| --- | --- | --- |
| `api.log`, `scheduled_execution.log`, `mcp_service.log` | `TimedRotatingFileHandler`, meia-noite, `backupCount=6` | **sim**, 6 dias |
| `wbcpython.log` (worker) | `RotatingFileHandler`, 5 MB x 3 backups | nao e' por dia |
| `api_service.log`, `scheduler_service.log`, `wbc_painel_service.log`, `wbc_worker_service.log` | NSSM (`AppRotateBytes 5000000`) | **NAO** |

**O NSSM nao apaga: ele RENOMEIA.** Passou de 5 MB, o arquivo vira
`api_service-2026-09-11T07-55-00.log` e comeca outro — o renomeado fica para sempre. E o
faxineiro (`maintenance/disco_limpeza.ps1`) nao pegava nenhum deles: o filtro era
`*.log.*`, que casa com o padrao do Python (`api.log.2026-09-05`) e **nao** com o do NSSM,
que termina em `.log` liso.

**A correcao nao adivinha o formato do nome do NSSM** (ele muda entre versoes): varre
`*.log` **e** `*.log.*` e protege os arquivos VIVOS por NOME (`-Preservar`, novo parametro
de `Apagar-Arquivos`). Duas redes, e a de cima ja bastaria:

- **idade** — log vivo esta sendo escrito agora, entao nunca fica mais velho que o corte;
- **nome** — se um servico estiver PARADO, o log dele congela e envelhece, e e' justamente
  ali que esta a evidencia de por que ele parou. Esse nao se apaga.

Testado sem rodar o script inteiro (ele mexe no cache do Windows Update): a funcao foi
extraida do FONTE por AST e exercitada contra arquivos de mentira — apagou os 4
rotacionados velhos (dos dois padroes) e preservou os 3 vivos-por-nome com 30 dias mais o
1 rotacionado recente. Sintaxe conferida pelo parser e o arquivo segue ASCII puro (o
cabecalho pede, por causa do PowerShell 5.1 sem BOM).

**Segue manual e dry-run por padrao** — so apaga com `-Confirmar`. O
`disco_relatorio.ps1` ja media a pasta `logs/` inteira, entao nao precisou mudar.

**Armadilha de leitura que vale registrar:** o `TimedRotatingFileHandler` so apaga
**quando rotaciona** — nao ha faxineiro rodando. Servico parado = nada rotaciona = nada e'
apagado. (A pasta `logs/` da maquina de desenvolvimento ainda tem
`scheduled_execution.log.2026-06-23`, de junho, exatamente por isso.) E o
`EVENTOS_RETENCAO_DIAS=6` do `.env` **nao e' log**: e' a faxina dos eventos de decisao no
SQLite do worker.

## [2026-09-11] — pandas 2.2.3 -> 2.3.3 (F0 da migracao para o Python 3.14)

A 2.2.3 **nao tem roda para o cp314**, e a .11 vai para o 3.14
(`docs/PLANO_PYTHON_314_NA_11.md`). A 2.3.3 instala no 3.12 de hoje **e** no 3.14 de
amanha — e' o que permite subir o pacote antes do interpretador, em dois passos: se algo
quebrar depois deste deploy e' o pandas; se quebrar depois da troca do interpretador e' o
Python. Juntos custariam um dia de bissecao.

Das 22 dependencias dos dois `requirements.txt`, **este pin era o unico bloqueio** — 20 ja
rodavam no 3.14.7, inclusive `hdbcli==2.29.23` (o pin exato), `pyodbc==5.3.0` e `pydantic`;
`waitress`, `pymssql` e `mcp<2` tem roda cp314 (3.0.2 / 2.4.1 / 1.30.0).

**Provado em producao no mesmo dia:** depois do deploy, o agendador de oportunidades
rodou as 07:55:26 com `last_status: "sucesso"` — e e' ele o maior usuario de pandas do
repo (HANA -> DataFrame -> Supabase). O `wbcpython` **nao importa pandas** em lugar
nenhum; pandas so vive em `db_utils`, `extract_*`, `pipeline_core` e `sap_connection`.

## [2026-09-10] — A Situacao dos Pedidos passa a dizer PARA ONDE a mercadoria vai (B3-B7)

O SAP guarda **dois** enderecos de entrega no mesmo pedido: o Ponto de Entrega (ShipTo,
o cadastro do cliente) e o "Local de Entrega" (o campo BR que a tela marca com o selo
*"difere do ponto de entrega"*). Medido em 10/09 no recorte de 268 pedidos: **38 tem o
Local preenchido e em 24 deles a CIDADE difere** — 24 pedidos em que despachar pelo
cadastro manda a carga para a cidade errada. No 84348, 250 km errada.

**A API resolve; quem consome nao escolhe** (decisao do Marcelo: *"nao posso deixar a
outra equipe tomar a decisao"*).

- **`completo`** ganha `entrega_endereco`: o endereco efetivo no TOPO, e o ShipTo
  aninhado em `ponto_entrega` como referencia cadastral.
- **`resumo`** (o default da lista) ganha `entrega_linha`, `entrega_cidade_uf` e
  `entrega_difere` — resolvidos, e **sem o ShipTo junto**: no default nao ha como
  escolher errado.
- **A propriedade que segura tudo:** quem ignorar `fonte`, ignorar o selo e ler so
  `cidade`/`uf`/`linha` ainda despacha certo. O caminho preguicoso e' o correto.

Tres detalhes que so a medicao revelou:

1. **O CEP vinha em DOIS formatos, na mesma coluna** (`ZipDlvryP`: 4 com hifen, 33 sem;
   `ZipCodeS`: 147 e 117). A API normaliza para `NNNNN-NNN` quando ha 8 digitos e passa
   o resto como veio — nao se inventa CEP.
2. **A caixa da cidade diverge entre as colunas** (`'BELO HORIZONTE'` x `'Belo
   Horizonte'` no 84317), entao `difere_do_ponto_de_entrega` NAO e' comparacao de
   cidade: e' "existe Local de Entrega preenchido", a mesma regra do selo da tela.
   Dos 38, 24 mudam de cidade e 14 sao outro endereco na MESMA cidade.
3. **A regua de "preenchido" e' >= 3 caracteres** (mais estrita que a do
   `_entrega_efetiva` do OrcaView, que aceita qualquer nao-branco). Medido antes de
   aplicar: ZERO das 268 linhas tinha 1-2 caracteres nesses campos, entao a diferenca e'
   teorica e a regua fica so aqui — a tela e o PDF do V118 nao foram tocados.

**Pedido cancelado tambem tem endereco:** o caminho da ORDR le a RDR12 daquele DocNum em
vez de emitir nulos (a chave nao podia faltar so nele; ha teste comparando as chaves dos
dois caminhos). SAP fora nao derruba a resposta — a chave vem vazia.

**Fachada MCP:** as docstrings das 3 tools passam a dizer que o endereco da resposta JA
e' o de despacho e que o `ponto_entrega` e' cadastro. E um furo foi fechado: o
`panorama_pedidos` busca o perfil `completo` quando ha filtro por montador/vendedor e
projetava por NOME — perdia o endereco, entao a MESMA tool respondia com endereco sem
filtro e sem endereco com filtro.

**Smoke em producao (10/09, apos o deploy):** 84348 devolve Juiz de Fora com o ShipTo de
Belo Horizonte aninhado; 84199 cai no padrao (`difere: false`); o cancelado 84282 traz o
endereco lido da RDR12, com o municipio resolvido na OCNT; e `panorama_pedidos` com
filtro traz os 3 campos sem vazar o ShipTo.

> **Custo:** ~0,7 KB por pedido, quase tudo no `ponto_entrega`. A lista foi de ~74 para
> **120 KB** no `resumo` e de ~237 para **435 KB** no `completo`. O `API_SITUACAO_PEDIDOS.md`
> avisa quem consome; para quem so quer despachar, o `resumo` basta.

## [2026-09-10] — Endereco de entrega no recorte da Situacao dos Pedidos (B1) + o teste diffavel que estava pulando calado

O SELECT do recorte ganha `LEFT JOIN RDR12 a ON a."DocEntry" = v."DocEntry"` e 18 colunas
de endereco: 9 do Local de Entrega (`*DlvryP`) e 9 do Ponto de Entrega/ShipTo (`*S`).
Ninguem consome ainda — a regra de precedencia e a publicacao sao as fases B3/B4.

- **Mapa `ENDERECO_COLS` explicito.** `StrNoDlvrP` nao tem o "y" e `BldDlvryP` convive com
  `BuildingS`: um loop de sufixo produz nome errado (armadilha de 13/08 no OrcaView). Ha
  teste cravando cada nome, e que `StrNoDlvryP` **nao** existe.
- **LEFT, nunca INNER.** Em 10/09 nenhum dos 266 pedidos do recorte estava sem linha na
  RDR12 — mas basta um para o INNER apagar um pedido da lista sem erro nenhum.
- **A OCNT continua FORA do join** (`County` vazio mata a consulta com "invalid number").
  O nome do municipio sai de um SELECT a parte, na B2.
- **Custo medido em producao**, 3 rodadas alternadas: mediana **142 ms antes, 107 ms
  depois** — dentro do ruido (a 1a execucao paga o plano: 486 ms). Com o cache de 120 s,
  isto e' pago no maximo 1x a cada 2 min.

### O teste diffavel estava PULANDO calado desde 08/09

`_caminho_v117()` procurava `web_orcaview_V117`, pasta renomeada para **V118** na migracao
de 08/09: os 20 testes que protegem o nucleo portado viraram `skip` em dev — o contrario
do que existem para fazer. Religado (V118 primeiro, V117 depois): a suite saiu de 1616
para **1639 passando**, com os skips caindo de 29 para 9. **O nucleo portado nao tinha
divergido** nesses dois dias.

O guarda religado pegou de imediato que **a lista de colunas do SELECT tambem e' contrato**
entre os dois repos. Decisao: as 18 colunas de endereco sao **extensao legitima da .11**
(`COLUNAS_SO_DA_11`), nao divergencia — a tela mostra o endereco na aba Logistica por outro
caminho (`fetch_pedido_report`, RDR12 por `DocEntry`), e leva-las para o V118 custaria 18
colunas x ~267 linhas em toda abertura da tela, sem ninguem ler. Para a lista de excecoes
nao virar um saco que abafa divergencia de verdade, um teste novo cobra o contrario: coluna
declarada la tem de estar mesmo no SELECT.

## [2026-09-10] — `/status` em dois niveis: o `STATUS_ID` de baixo privilegio (A1 do PLANO_STATUS_E_ENDERECO_ENTREGA)

Medido em producao em 10/09 as 11:43, com um `curl` da maquina do Marcelo e **sem header
nenhum**: o `/status` publicava `SAPBusinessOneI`, `192.168.7.11`,
`Windows-2022Server-10.0.20348`, Python 3.12.10, `SAPBusinessOneHana-vm:30015`,
`192.168.0.1:1433 / WBCCAD`, a URL do projeto Supabase, `C:\Python\...\state\`, disco/CPU
e o nivel de patch (`pendentes: 3`, `dias_sem_patch: 5`). O mapa da integracao para
qualquer um na LAN.

- **Nasce o `STATUS_ID`** (`config.py`, `.env.example`): credencial que abre **so** o
  `/status` completo. E' a que vai para a outra equipe e para o OrcaView — assim ler o
  diagnostico deixa de exigir a `OS_API_KEY`, que tambem escreve no SAP e abre o painel
  8079. Aceita nos MESMOS tres lugares da chave (`X-API-Key`, `Bearer`, `?key=`), entao
  quem ja consome **so troca o valor**: nenhum cliente muda de codigo.
- **Tres niveis na rota:** sem credencial vem a visao MINIMA (`ok`, `healthy`, um booleano
  por check, `alerts` como **contagem**, `restrito: true`); com o `STATUS_ID` ou com a
  `OS_API_KEY` vem o payload inteiro, como antes.
- **O codigo HTTP nao depende da credencial** — e' calculado ANTES da reducao. O
  `mira-watchdog.js` do .90 chama `/status?checks=worker&strict=1` **sem credencial** e
  decide pelo codigo: ele nao muda uma linha. Ha teste cravando isso nos tres cenarios
  (check caido, alerta, saudavel).
- **O `STATUS_ID` nao abre mais nada.** O `_autorizado()` (guard das outras 18 rotas) NAO
  o aceita — teste cravando 401 em `/rh/colaboradores`, `/pedidos/situacao`,
  `/historico`, `/ordens-servico/<n>`, `/oportunidades/info` e na escrita de OP.
- **Fail-closed na credencial nova:** sem `STATUS_ID` no `.env`, credencial qualquer nao
  abre nada. A unica porta que segue aberta e' a antiga — sem `OS_API_KEY` a API inteira
  cai aberta (fail-open documentado) e o `/status` acompanha, em vez de inventar uma
  segunda regra so para esta rota. Quem denuncia esse estado e' o `api_auth` do payload.
- **`monitoring.py` NAO mudou.** `collect_status()` e `SELECTABLE_CHECKS` sao contrato
  entre repos (o card do .90 e a tool MCP leem os blocos pelo nome): a reducao e'
  apresentacao (`_status_publico` no `api.py`), nao coleta.
- **Sem flag no `.env`**: `STATUS_ID` e' credencial, da familia de `OS_API_KEY` e
  `SIS_MCP_TOKEN` — nao ha `STATUS_*_ENABLED`. Rollback = `git revert` + restart.

- **O refactor quebrou um teste antigo, e o conserto e' mais forte que o original.**
  `test_autorizado_usa_compare_digest` inspecionava o FONTE do `_autorizado` atras de
  `compare_digest` — que mudou para o `_confere`. Agora ele olha o `_confere` **e** exige
  que o `_autorizado` delegue: sem essa segunda asserção, a garantia de tempo constante
  se perderia calada no dia em que alguem reintroduzisse um `==` ali.

> Nota de ambiente: `flask` e `apscheduler` foram instalados no Python 3.14 local em
> 10/09 (a pedido do Marcelo) — a suite saiu de **1165 para 1616 testes passando, zero
> falha**. Sobram 42 erros de `ModuleNotFoundError: mcp` nos testes da fachada, que
> continuam intocados de proposito (ver a memoria do `mcp` 2.x local).

## [2026-09-08] — `install_wbc_services.bat` nao rebaixa mais o worker para MANUAL (o boot da .11 o deixaria parado)

Achado ao conferir o boot de amanha: o instalador gravava `Start SERVICE_DEMAND_START` no worker
sempre (regra "parado ate a virada"), e foi rodado DEPOIS da virada — o reboot diario da .11
(~06:12) levantaria API, MCP, agendador e painel, e nao o worker. Agora o script le o tipo de
inicio ANTES do `nssm install` (`sc qc ... | find "AUTO_START"`): servico ja AUTO fica AUTO;
so instalacao nova nasce MANUAL. Na .11 de hoje: `nssm set OrcaView-WBC-Worker Start SERVICE_AUTO_START`.
Plano: `docs/PLANO_INTEGRACAO_WBCPYTHON.md` (8a atualizacao: tudo no ar; 1a escrita real no
ciclo #4; worker parado 48 min pelo deploy das 15:52, corrigido).

## [2026-09-09] — painel WBC: fontes ~10% maiores

Pedido do Marcelo olhando o painel na .11. Todos os `font-size`/`font:` do `painel.css` escalados
por 1,1 e arredondados a meio pixel (11→12, 12→13, 12,5→14, 13→14,5, 14→15,5, 15→16,5, 26→28,5).
Prévia local antes/depois com os fragmentos reais. `painel.css?v=20260909` no `pagina.html` para
furar o cache do navegador.

## [2026-09-09] — wbc: parada por arquivo (`state/wbc_worker.stop`) — o deploy nao mata mais o worker no meio do ciclo

Deploy das 10:14 (o 2o do dia) pegou o worker 3 s dentro do ciclo #133: Event Log do NSSM mostra
STOP 10:12:55, `kill_console()` esperando 60 s pelo Ctrl+C sem resposta, `Killing process tree`
10:13:57. O tratador de Ctrl+C existia mas nunca rodava: `Event.wait()` sem timeout nao e
interrompido por sinal no Windows — e o agendador ainda abriu o #133 no meio da espera. Resultado:
trava em nome do PID morto por 30 min (`Ciclo ignorado ... expira em 10:43:50`), execucao #133
"em andamento" para sempre, e o reboot da .11 (10:26) nao resolveu. Agora: (1) `deploy_update.bat`
grava `state\wbc_worker.stop` antes do `nssm stop`; (2) o worker checa o arquivo entre orcamentos
e entre ciclos (`host/parada.py`, `WorkerIntegracao.parada_solicitada`), nao abre ciclo novo, termina
o atual (trava liberada, execucao fechada, Logout) e sai; apaga o arquivo ao religar; (3) o laco
principal acorda a cada 1 s, entao o Ctrl+C tambem passa a funcionar. `WORKER_ARQUIVO_DE_PARADA`
(padrao `state/wbc_worker.stop`). 11 testes (`tests/wbc/host/test_parada_por_arquivo.py`). Pendente
(proposto): assumir na hora a trava de PID morto na mesma maquina, em vez de esperar 30 min.

## [2026-09-09] — wbc: pedido nasce no `PN_Correc` quando `U_INO_Update = 'Y'` chega antes do pedido

Revisao das regras contra o WBCPython original, a pedido do Marcelo. A troca de parceiro (cancela e
recria) existe e dispara; no `00125572` quem recusa e o SAP: `-1116 (1996) Cancelamento de Pedido de
vendas nao permitido para o seu usuario` — regra do `SBO_SP_TransactionNotification` que deixa o
`financeiro04` cancelar (101 cancelamentos desde 06/2026) e nao o `orcaview`, usuario do SL desde a
virada. Acao dele: incluir o `orcaview` na regra 1996 ou voltar `SL_USERNAME` para `financeiro04`.
O que faltava no codigo: com `Update = 'Y'` + `PN_Correc` ANTES de existir pedido, ele nascia no
parceiro da oportunidade, o vinculo baixava o `Update` e a passada seguinte lia "corrigido a mao"
— parceiro errado para sempre (o legado tinha o mesmo buraco). Agora
`EstadoIntegracao.nasce_no_parceiro_corrigido` cria o pedido ja no `PN_Correc` (regra
`cria_pedido_no_pn_corrigido`), com a guarda `ChecaPN` (parceiro inexistente = nao cria, motivo no
historico) e sem o contato do parceiro antigo. 12 testes novos. `docs/wbc/DECISOES.md`, ultima secao.

## [2026-09-09] — wbc: uma senha por sistema — credenciais do WBC caem nos nomes do SIS quando faltam

Pedido do Marcelo na hora de rotacionar as senhas: o `.env` da .11 descrevia o mesmo HANA
(`SAP_*` e `HANA_*`), o mesmo SQL Server do WBC (`SQL_*` e `WBC_SQL_*`) e o mesmo usuario do
Service Layer (`OP_SL_*` e `SL_*`) duas vezes. Agora `wbcpython/config.py` le primeiro o nome do
WBC e, se ele nao existe, o do SIS (`AliasChoices`): `SL_USERNAME`→`OP_SL_USERNAME`,
`WBC_SQL_*`→`SQL_*`/`SQLSERVER_*`, `HANA_*`→`SAP_*`. O que NAO cai, de proposito: `SL_COMPANY_DB`,
`WBC_ENVIRONMENT` e a trava — apontar a escrita para producao continua explicito no bloco WBC.
`tests/wbc/conftest.py` passa a limpar tambem `SAP_`/`SQL_`/`SQLSERVER_`/`OP_SL_` do ambiente.
12 testes (`tests/wbc/test_config_fallback.py`). Na .11 nada muda ate alguem apagar as linhas
duplicadas do `.env`; vale no proximo deploy.

## [2026-09-08] — wbc: Service Layer so quando ha escrita (login preguicoso; ciclo sem acao nao toca o SL)

Pedido do Marcelo. O `with ServiceLayerClient(...)` fazia `Login` na entrada e `Logout` na saida
em TODO ciclo — ~260 por dia — mesmo quando o ciclo terminava com 0 escritas (a leitura das
oportunidades ja vinha do HANA; o de-para de grupos so e' lido ao montar linhas de documento).
Agora `__enter__` nao autentica; o login acontece na primeira requisicao (`request()` ja fazia
isso) e o `Logout` na saida so se houve login. Ciclo sem escrita = zero requisicoes ao SL.
`check-sap` continua logando de proposito (`testar_conexao`). 2 testes.

## [2026-09-08] — `deploy_update.bat` roda de uma copia em `%TEMP%` (o pull reescrevia o proprio .bat no meio)

O `cmd.exe` le um `.bat` por posicao de byte. Como o `git pull` do deploy reescreve o proprio
`deploy_update.bat`, a partir dali o cmd executava linhas da versao nova em posicoes da antiga —
e' a explicacao do "[git] ja estava atualizado" impresso junto de um fast-forward. Agora o script
se copia para `%TEMP%\deploy_update_run.bat` e reexecuta a copia (`--copia <pasta>`), que nao
muda durante o pull. Nada mais mudou no fluxo.

## [2026-09-08] — wbc: retencao do acompanhamento — decisao repetida nao grava; faxina de 6 dias (D8)

Medido no banco do worker antigo (6 dias em producao, 1.682 oportunidades, ciclo de 3 min):
940.326 eventos, 934.634 deles `decisao` (99,4 %), ~1.300 por ciclo, ~95 MB/dia — o ciclo
regravava "nada a fazer" para cada orcamento a cada passada. So 12.559 eram mudancas de decisao.
Decisao do Marcelo: 6 dias de retencao.

- **`RepositorioTracking.registrar_evento`** deixa de gravar uma DECISAO igual a ultima do
  orcamento (mesma regra, mensagem e detalhes; comparacao com o ULTIMO evento, de qualquer
  tipo — decisao → acao → a mesma decisao continua sendo historia). Devolve `bool`. Acao, erro
  e reprocessamento sempre gravam. Corta ~99 % das gravacoes na origem.
- **`faxina_de_eventos(dias)`**: apaga so `decisao` mais velha que o prazo (acoes/erros ficam)
  e compacta o SQLite (`VACUUM`) quando apagou algo. `EVENTOS_RETENCAO_DIAS` (default **6**;
  `0` desliga). O worker roda a faxina **uma vez por dia, depois do ciclo** (falha vira aviso,
  nunca erro do ciclo). CLI: `python -m wbcpython faxina [--dias N]`.
- 19 testes (`tests/wbc/test_retencao.py`). Docs: `docs/wbc/README.md`, `.env.example`, `CLAUDE.md`.

## [2026-09-08] — parada limpa de verdade: deploy espera o worker parar; servico do worker roda `python.exe` direto

No deploy das 15:52 o worker ficou PARADO: o `nssm stop` entrou em `STOP_PENDING` (o `cmd.exe` do
`run_wbc_worker.bat` segura o Ctrl+C do NSSM no "Terminate batch job (Y/N)?" ate o NSSM matar a
arvore no fim dos 60 s) e o `nssm start` do fim do script chegou durante o `STOP_PENDING` — recusado
em silencio. Duas correcoes:

- **`deploy_update.bat`**: `:esperar_parar` (loop em `sc query` ate `STOPPED`, max 90 s) depois do
  stop do worker e antes do start; se o worker nao subir, avisa com o comando para subir a mao.
- **`install_wbc_services.bat`**: o servico `OrcaView-WBC-Worker` passa a ter `Application` =
  `python.exe` (venv ou o do PATH) e `AppParameters` = `-m wbcpython worker`, com `PYTHONUTF8=1`
  via `AppEnvironmentExtra` — sem `.bat` no meio, o Ctrl+C chega ao Python, que termina o ciclo e
  sai em segundos. `run_wbc_worker.bat` fica para uso manual. Vale no proximo start
  (`nssm restart OrcaView-WBC-Worker`).

## [2026-09-08] — tarefa legada "Integracao WBC" aposentada: o check `scheduled_task` fica, mas nasce `retired` e nunca alarma

Virada feita (F5 do plano): worker antigo parado (13:24), tarefa legada desabilitada (15:47),
`OrcaView-WBC-Worker` na .11 com 3 ciclos limpos (15:41, 15:45, 15:48 — 1.682 avaliados cada, 0
erros). Sem esta mudanca, o proximo retrato do monitor da tarefa diria "desabilitada" em
`problems[]`, viraria alerta, derrubaria `healthy` e faria `?strict=1` responder 503 — para o
watchdog da Mira e o card do `.90`.

- `WBC_TASK_MONITOR` (default **false**): `_scheduled_task_signal` devolve `{available: false,
  retired: true, healthy: null, error: "...desativada em 2026-09-08...", task_name}` e
  `_scheduled_task_alerts` devolve `[]`. A FORMA do bloco nao muda: o card do `.90` (`status.js`,
  `renderWbcTask`) mostra "Indisponivel" com o texto explicando, e a tool MCP `estado_tarefa_wbc`
  segue respondendo (docstring e `instructions` avisam que e' legado e apontam
  `estado_integracao_wbc`). `true` religa o monitor de verdade (rollback para o legado).
- `monitor_wbc_task.ps1` / `install_monitor_task.ps1` ficam no repo (sao o rollback); a tarefa
  `OrcaView-Monitor-WBC-Task` do Task Scheduler pode ser removida na .11 — com o monitor
  desligado, o JSON dela e' ignorado.
- 3 testes novos; os 3 que exercitam o monitor de verdade religam `WBC_TASK_MONITOR`.

## [2026-09-08] — `deploy_update.bat`: pip decide pelo HASH dos requirements, nao pelo pull

Na .11 o `doctor` acusou `fastapi` e `pymssql` ausentes: o pip so rodava quando o `requirements.txt`
mudava NO pull daquele deploy, e o pull com a mudanca ja tinha acontecido antes. Agora o script
compara o SHA-256 de `requirements.txt` + `mcp/requirements.txt` com o gravado em
`state/deps.sha256` (escrito apos um pip bem-sucedido): difere ou nao existe -> instala. O
criterio do git continua valendo como reforco. Primeiro deploy apos esta versao instala (marca
ainda nao existe) — e o que a .11 precisa.

## [2026-09-08] — `GET /` da 8077 vira a entrada: leva ao painel WBC; Painel de Sincronizacao em `/sincronizar`

Pedido do Marcelo ao ver a 8077 no ar: "a interface do WBCPython e a principal; a janela do
ServidorIntegracaoSAP se acessa por um botao". O endereco que todo mundo ja usa
(`192.168.7.11:8077`) passa a cair no painel WBC.

- **`GET /`** serve `web/entrada.html`: sonda o painel (`fetch` `no-cors`, 3 s) e so entao troca
  a `location`; se o painel nao responde (servico `OrcaView-WBC-Painel` parado), a propria raiz
  mostra o aviso com o nome do servico e os botoes para `/sincronizar` e "tentar de novo" — em vez
  da pagina de erro do navegador. Endereco do painel vem da API (`WBC_PAINEL_URL` ou
  host:`PAINEL_PORTA`), escapado para HTML e para JS; `Cache-Control: no-store`.
- **`GET /sincronizar`** serve o Painel de Sincronizacao de sempre (o JS dele usa caminhos
  absolutos; nada mais mudou). O botao "Sincronizacao SAP -> Supabase" do painel WBC passa a
  apontar para `/sincronizar` (apontar para `/` seria um vaivem); `SIS_PAINEL_URL`, se usada,
  deve ir em `/sincronizar`.
- Consumidores conferidos: o `.90` (watchdog da Mira, admin) usa so `/health` e `/status`.
- Rotas abertas declaradas no teste-guarda: `/` e `/sincronizar`. 5 testes novos/ajustados;
  previa da raiz (estado de fallback) conferida no navegador.

## [2026-09-08] — fix: os 17 `__init__.py`/`__main__.py` do wbcpython nao estavam no git (regra `_*.py`)

Na .11, `python -m wbcpython` respondia "No module named wbcpython.__main__": a regra `_*.py`
do `.gitignore` (arquivos temporarios) casa com `__init__.py` e `__main__.py`, e nenhum dos 17
do pacote e de `tests/wbc/` tinha sido commitado — localmente funcionava porque existiam no
disco. `.gitignore` ganha `!__init__.py` / `!__main__.py`; os 17 entram; `tests/test_repo_layout.py`
pergunta ao `git ls-files` (nao ao disco) se toda pasta com `.py` versionado leva o seu
`__init__.py` e se o `__main__.py` esta la. Deploy: `deploy_update.bat` (so pull + restart; o
painel sobe sozinho no fim).

## [2026-09-08] — ops: `install_wbc_services.bat` (so os 2 servicos WBC, idempotente, corrige caminho)

Primeiro F4 na .11: os comandos `nssm` de cmd (`set PROJ=` / `"%PROJ%"`) foram colados no
**PowerShell**, que nao expande `%PROJ%` — os servicos ficaram com `AppDirectory` literal
`%PROJ%` e pararam no start (`SERVICE_STOPPED`). Novo `install_wbc_services.bat`: registra ou
**regrava** so `OrcaView-WBC-Painel` e `OrcaView-WBC-Worker` com o caminho real da pasta
(`Application`, `AppDirectory`, logs), sem tocar nos outros tres servicos; rodar
`.\install_wbc_services.bat` como Administrador, em qualquer shell. Nao inicia nada.

## [2026-09-08] — ops: servicos WBC no NSSM, deploy_update com 5 servicos e pip no sistema, check `wbc_worker`, tool MCP

F3 do `docs/PLANO_INTEGRACAO_WBCPYTHON.md`. Tudo aditivo; nada do que a API 8077 e a fachada MCP
faziam mudou (suite de antes verde: 468 testes; `?checks=wbc` continua sendo o SQL Server).

- **`run_wbc_worker.bat` / `run_wbc_painel.bat`** — wrappers no padrao dos outros (cwd = raiz,
  venv-ou-sistema, UTF-8). **`install_services.bat`** registra tambem `OrcaView-WBC-Painel`
  (auto) e `OrcaView-WBC-Worker` (**MANUAL, nao e iniciado**: so na virada, com o legado
  desligado) com `AppStopMethodConsole 60000` — parada limpa: o NSSM manda Ctrl+C e o worker
  termina o ciclo (9-14 s medidos) antes de sair.
- **`deploy_update.bat`** — para/sobe os 5 servicos (worker por ultimo e **so religa se estava
  rodando**, via `sc query`); **pip no Python 3.12 do sistema quando nao ha venv** (antes pulava
  em silencio — foi o que exigiu pip a mao no deploy do status de OP); confere `/health` da 8077
  e `/entrar` do painel na `PAINEL_PORTA` do `.env`.
- **`/status` ganha o check `wbc_worker`** (`monitoring._wbc_worker_signal`; aliases `worker`,
  `integracao_wbc`): le `execucoes` do SQLite do acompanhamento em modo `ro`, sem tocar SAP/WBC.
  Tres niveis de proposito: `installed=false` (banco nao existe — a .11 antes da virada:
  informacao, **sem alerta**, `?strict=1` segue 200) · `last=null` (tabelas criadas, nenhum
  ciclo) · e so a partir do 1o ciclo registrado alarma por silencio dentro do expediente **do
  worker** (`WORKER_HORARIO_*`/`DIAS` do mesmo `.env`; limite = 2 x `WORKER_INTERVAL_SECONDS`,
  minimo 10 min), ciclo `em_andamento` preso, ou ultimo `falhou`. 7 campos novos em
  `config.py` (mesmos nomes de env do worker). 17 testes.
- **Fachada MCP: `estado_integracao_wbc()`** (17a tool, leitura) sobre `/status?checks=wbc_worker`,
  com a docstring ensinando a ler `installed=false`/`healthy=null`; o servidor passa a se
  apresentar dizendo que a Integracao WBC roda nesta maquina e que nao e a "tarefa WBC" legada.
  `tests/test_mcp_wbc.py` (roda com `mcp<2`; o `mcp` 2.x local ja quebrava os outros).
- `CLAUDE.md`, `README.md` (secao nova, operacao, logs, monitoramento, estrutura) e
  `.env.example` atualizados no mesmo commit.

## [2026-09-08] — wbc: o WBCPython entra no repositorio (pacote `wbcpython/`), painel com chave compartilhada e caminho para a 8077

F1 + F2 do `docs/PLANO_INTEGRACAO_WBCPYTHON.md`. Decisao do Marcelo em 08/09: **um projeto so**
— o WBCPython deixa de existir como repositorio e vira parte deste; a .11 continua com uma
pasta, um `.env`, um `requirements.txt`, um `deploy_update.bat`.

- **Import por copia, SEM historico** (`3aa7ef2` do WBCPython versionava um `.env.bak` com
  senha, e este repo e publico): `src/wbcpython/` → `wbcpython/` (imports absolutos intactos),
  `tests/` → `tests/wbc/` (colidia `test_config.py`; imports `tests.x` viraram `tests.wbc.x`),
  7 docs → `docs/wbc/` (com banner de "historico"), `sql/VW_INO_OPORTUNIDADE_INTEGRACAO.sql` →
  `sql/hana/`. `uv`, `hatchling`, `uv.lock` e `[project.scripts]` **nao entram**: roda com
  `python -m wbcpython` na raiz (le o `.env` do cwd), instala pelo `requirements.txt` (8 deps
  novas: pydantic-settings, sqlalchemy, pymssql, fastapi, uvicorn, jinja2, python-multipart;
  httpx/hdbcli/apscheduler/python-dotenv ja existiam). Sintaxe varrida contra 3.12 (a .11):
  89 arquivos, 0 incompativeis.
- **Suite unica: 1.591 testes verdes** (468 + 843 do WBC + 280 novos/afinados), 29 skipped (os
  de rede, por opcao `--run-integration` — a opcao mora no `tests/conftest.py` da raiz, unico
  lugar em que o pytest a aceita; marker `integration` registrado no `pyproject.toml`).
  `tests/wbc/conftest.py` neutraliza o `.env` da maquina (o `load_dotenv()` do `config` da raiz
  vaza para o `os.environ` da sessao). `ruff` em 0 (uma isencao E501 num fixture SQL).
- **Painel: entrada com a MESMA `OS_API_KEY` da API 8077** (`GET/POST /entrar`, `POST /sair`;
  cookie HttpOnly com HMAC da chave, nunca a chave; `X-API-Key`/`?key=` para script; HTMX sem
  cookie recebe 401 + `HX-Redirect`; `proximo` so aceita caminho local). **Sem `OS_API_KEY` nada
  muda**: o painel continua aberto, como a API. 23 testes em `tests/wbc/dashboard/test_entrada.py`.
- **Links cruzados:** botao "Sincronizacao SAP → Supabase" no topo do painel (`GET /sincronizacao`
  → `SIS_PAINEL_URL` ou mesmo host na `OS_API_PORT`) e `⇄ Integracao WBC` no header do
  `sincronizar.html` (`GET /painel-wbc` da API → `WBC_PAINEL_URL` ou mesmo host na
  `PAINEL_PORTA`, 8079; rota aberta declarada no teste-guarda). Previa conferida no navegador
  (entrada, pagina com os 1.680 orcamentos de producao, botao ao lado de Tema/Sair).
- Retrato da previa (`pendentes --exportar`) e `ARQUIVO_PADRAO` do painel passam a
  `state/wbc_previsao.json` (runtime, ignorado); `.gitignore` ganha `state/*.db*`;
  `PAINEL_PORTA` default 8079; `.env.example` ganha o bloco WBC inteiro com os valores de
  producao anotados (180 s, 07:00-20:00).
- `docs/wbc/README.md` (guia novo, sem uv), `docs/wbc/ai_spec/00_index.md` (onde mora o que
  a spec original — ausente — regia), `CLAUDE.md` com o mapa e os gotchas do pacote.

## [2026-09-08] — docs: plano de integracao do WBCPython (worker + painel) neste repositorio

`docs/PLANO_INTEGRACAO_WBCPYTHON.md` (+ artifact na mesma URL do cabecalho). So documentacao;
nada codado. O WBCPython (reescrita Python do WBCServConsole: cotacao/pedido no SAP a partir
dos orcamentos do WBC; 11.095 linhas, 843 testes) entra como pacote `wbcpython/` na raiz,
`tests/wbc/`, `docs/wbc/`, instalado pelo `requirements.txt` e rodado com `python -m wbcpython`;
vira 2 servicos NSSM na .11 (`OrcaView-WBC-Painel` na 8079 como entrada, `OrcaView-WBC-Worker`);
`deploy_update.bat` passa a cuidar dos 5 servicos e a rodar pip no Python do sistema quando nao
ha venv. Dois fatos medidos em 08/09 mandam no plano: (1) o worker novo ja escreve em producao
ha 6 dias a partir de uma maquina Linux que monta esta pasta por SMB, **enquanto a tarefa legada
"Integracao WBC" segue ligada na .11** (dois integradores pela mesma chave — risco §1 do
RISCOS_PRODUCAO do WBC); (2) o git do WBCPython versiona um `.env.bak` com senha e este repo e
publico — o historico entra sem os commits (`--squash`). 10 decisoes, 6 abertas com recomendacao.

## [2026-09-08] — Manutencao: disco C: da .11 em 84,6 % (relatorio + limpeza conservadora)

O `/status` de 08/09 mostrou o disco em **84,6 %** (19,5 GB livres; o alerta dispara em 90 %). Dois
scripts em `maintenance/`, um por etapa, para rodar NO servidor como Administrador:

- **`disco_relatorio.ps1`** — somente leitura: espaco livre, os "suspeitos de sempre" (cache do
  Windows Update, Temp, CBS, WindowsAzure\Logs, WER, Lixeira, logs/exports/state do app, shadow
  copies) e as maiores pastas do C: em 2 niveis. Demora alguns minutos.
- **`disco_limpeza.ps1`** — **dry-run por padrao**; `-Confirmar` apaga. So o que regenera sozinho:
  SoftwareDistribution\Download (para/religa `wuauserv`), Temp com >2 dias, CBS com >30 dias, WER,
  Lixeira, `logs\*.log.*` rotacionados do app com >30 dias e WindowsAzure\Logs via o
  `clean_azure_logs.ps1` que ja existia. Nao toca shadow copies, backups, exports, state, SAP;
  nunca apaga pasta; arquivo em uso e pulado. Grava `maintenance/disco_limpeza.log`.
- ASCII puro (PS 5.1 sem BOM). Validados localmente: parser sem erro e dry-run executando.
  Item 2 do §5 do `MCPs/SAP_RDP/docs/PLANO_UX_MONITOR_RDP.md`.

## [2026-09-07] — Fachada MCP: panorama com teto e filtros (F1) + o servidor se apresenta (F2)

F1 e F2 do `docs/PLANO_UX_FACHADA_MCP.md`. So' a fachada muda; a API 8077 e a view
continuam iguais.

- **`panorama_pedidos` ganha `limite` (default 40), `montador`, `vendedor` e
  `so_atrasados`.** A lista vem ordenada por atrasado -> mais etapas bloqueadas -> mais
  antigo; `kpis` e `montadores` seguem do recorte inteiro; quando corta, vem `truncado`,
  `mostrando`, `total_filtrado` e `aviso`. `limite=0` (sem teto) so' vale com filtro.
  Medido contra a .11 (259 pedidos): default **13 KB / ~3,4 k tokens** em vez de 73 KB /
  ~18,7 k; `completo` com `limite=10` = 11 KB em vez de 233 KB.
- `montador`/`vendedor` so' existem no `completo` da rota: com esses filtros a fachada
  busca o completo e **projeta de volta** as 11 colunas do resumo (+ `montagem`,
  `vendedor`). Sem filtro, a chamada a API e' a mesma de antes (`campos=resumo`).
- **`FastMCP(..., instructions=...)`**: o servidor se apresenta ao cliente — que maquina
  e' (.11, nao o RDP .12 do `sap-rdp`), o que e' leitura e o que exige `confirmar=True`,
  e o frescor de cada dado (cache de 2 min, carga das 12:40, `null` = nao se sabe).
- **404 HTML de rota inexistente traz `dica` em qualquer tool** (`_tratar_resposta`,
  comum a `_get` e `_post`): antes so' colaboradores traduzia "HTTP 404" em "a .11 esta
  desatualizada"; as outras deixavam o modelo concluir que o dado nao existe.
- 12 testes novos (carteira sintetica de 300 pedidos; `instructions`; 404/500 via
  `httpx` falso). Suite: 403 passed. **Producao:** `git pull` na .11 + restart do
  `OrcaView-MCP`.

## [2026-09-07] — Fachada MCP: pin `mcp<2` (F0) e descricoes afinadas (F3)

F0 e F3 do `docs/PLANO_UX_FACHADA_MCP.md`. Nada muda de comportamento na API; muda o
que o modelo le e o que impede o server de subir.

- **`mcp/requirements.txt`: `mcp>=1.2,<2`.** O 2.x renomeou `FastMCP` -> `MCPServer`; com
  ele instalado o `mcp_server.py` morre no import e o cliente so ve "Connection closed".
  A .11 roda 1.28.1 e nao e afetada; o pin protege quem instala do zero. `mcp/README.md`
  ganhou a secao de diagnostico (versao ativa, `import mcp_server`, alternativa HTTP).
- **Docstrings** (o que o modelo le): `estado_windows_update` no mesmo contrato tri-estado
  sem caixa alta/negrito; `info_oportunidades` diz quando usar e o que nao traz;
  `ultimos_erros` documenta `examinados`/`qtd_falhas`/`falhas` e que `0` em 10 registros
  nao e "sem falha hoje". `CLAUDE.md` perde as contagens que envelhecem (378 testes, 455
  linhas — ja eram 391 e 1895).
- Testes de docstring assertam **semantica** (`sem bloqueio`, `invente "está liberado"`,
  `diverge`), nao caixa alta. Verificado com um `mcp` 1.30 isolado (PYTHONPATH): 391
  passed, 2 skipped. O Python global desta maquina continua com o 2.1.1 — instalar e do
  dono. **Producao:** `git pull` na .11 + restart do `OrcaView-MCP`.

## [2026-09-07] — Plano: experiencia do usuario na fachada MCP (docs)

Prompt-audit das 15 docstrings + medicao do que quem pergunta no Claude sente, contra a
.11. Dois achados bloqueantes que nao estavam em lugar nenhum: a fachada **stdio local
nao sobe** (o `mcp` global e 2.1.1; `mcp/requirements.txt` pede `>=1.2.0` sem teto e o
2.x renomeou `FastMCP`), e o registro stdio (escopo de projeto) tem o mesmo nome do HTTP
(escopo de usuario) e vence — o morto esconde o vivo. `panorama_pedidos` devolve 259
pedidos = ~18,7 k tokens no resumo e ~59,6 k no completo, numa chamada.

- `docs/PLANO_UX_FACHADA_MCP.md`: F0 pin `mcp<2` + escolher HTTP; F1 teto de 40 no
  panorama + filtros; F2 `instructions` no servidor + 404 traduzido em todo `_get`;
  F3 diff do prompt-audit; F4 log de ms/bytes por tool. Nada aplicado ainda.

## [2026-09-03] — Situacao do Pedido: cancelado responde 200 dizendo "Cancelado"

Segunda metade do incidente do mesmo dia. A lista de OS ja sinalizava o cancelamento
(entrada abaixo), mas a tela de quem consome le a **Situacao dos Pedidos**, e ali 84282,
84305 e 84314 continuavam dando **404 mudo** — identico ao de um pedido de 2024 — e o
chip da tela escrevia "sem situacao". Medido na .11 antes de mexer: dos numeros fora da
view que a equipe reclamou, TODOS estavam cancelados na ORDR (84200 e 83500 tambem).
Cancelado **e** uma situacao; quem sabia dize-la era a ORDR, e ninguem perguntava.

- **`GET /pedidos/<n>/situacao` responde `200` para pedido cancelado** (decisao do dono):
  quando o numero nao esta na view, a rota le a ORDR ao vivo e devolve o **mesmo formato**
  de sempre — as tres etapas em `"Cancelado"`, mais `fonte: "ordr"`, `status_pedido`,
  `pedido_cancelado: true` e `aviso` (o mesmo `tipo` dos endpoints de OS). Quem ja desenha
  o chip da etapa passa a escrever "Cancelado" **sem mudar uma linha**.
- O payload sai do proprio `situacao_pedidos.normalizar` (linha crua sintetica), e nao de
  um dict escrito a mao: um teste compara as chaves dos dois caminhos, entao campo novo na
  view nao consegue faltar aqui.
- **O `404` sobrou para o que nao da' para afirmar — e nunca vem mudo:** `motivo` =
  `fora_do_recorte` (existe no SAP, fora do periodo da view; vem com `status_pedido`),
  `pedido_nao_encontrado` (nao ha DocNum assim na ORDR) ou `indeterminado` (SAP nao
  respondeu; `pedido_cancelado` = `null`, que NAO e "nao cancelado").
- Toda resposta da rota traz `status_pedido` + `pedido_cancelado` no topo — o **mesmo
  par**, com o mesmo sentido, de `GET /ordens-servico/<nped>`.
- `?chave=docentry` **nao** pergunta a ORDR (ela so' procura por DocNum): traduzir na
  marra devolveria a situacao de outro pedido, calada.
- `consultar_status_pedido` passa a trazer tambem a identidade do pedido (cliente, data,
  valor, moeda) na MESMA linha da ORDR — sem ela a resposta do cancelado sairia anonima.
- MCP: a docstring de `situacao_pedido` manda dizer **cancelado** (200) e decidir o 404
  pelo `motivo`; teste cravando. Doc `API_SITUACAO_PEDIDOS.md` reescrito nos 7 pontos que
  falavam de 404 (§2.2 e §2.3 novas, tabela de `motivo`, exemplos, FAQ).
- Provado contra o HANA de producao: 84282/84305/84314/84200/83500 → 200 "Cancelado";
  84313/84250 → 200 pela view; 83000/82000 → 404 `fora_do_recorte` com
  `status_pedido: "Fechado"`; 999999 → 404 `pedido_nao_encontrado`. 565 testes, ruff limpo.

## [2026-09-03] — OS de pedido cancelado passa a vir sinalizada (lista + detalhe)

Incidente reportado pela equipe consumidora: pedidos 84282, 84305 e 84314 apareciam na
lista de OS como pedidos normais e "sem situacao" na tela deles. Causa provada no HANA: os
tres estao **cancelados na ORDR** (`CANCELED='Y'`) mas com OPs vivas na OWOR. A
`VW_STATUS_PEDIDO_DDP` exclui cancelados → `GET /pedidos/<n>/situacao` responde 404 (por
desenho); ja a lista/detalhe de OS so filtrava OP cancelada, nunca pedido cancelado, e o
ramo `pedido_cancelado` da sincronizacao so dispara sem OS nenhuma. Ninguem mentia, mas
ninguem dizia "cancelado".

- **`GET /ordens-servico/disponiveis`**: cada item ganha `status_pedido`
  (Aberto|Cancelado|Fechado|null) e `pedido_cancelado` (bool|null), lidos da ORDR no mesmo
  SELECT (`MAX(CANCELED)`, `MAX(DocStatus)`, `COUNT(DocEntry)`). **Cancelado continua na
  lista, sinalizado** — esconder faria as OPs vivas de pedido morto sumirem da vista.
- **`GET /ordens-servico/<nped>`**: os mesmos dois campos no topo, via leitura leve so da
  ORDR (`consultar_status_pedido`, conexao propria), e `aviso: {tipo: 'pedido_cancelado'}`
  quando cancelado — mesmo `tipo` do `POST .../sincronizar`. Best-effort: SAP fora → `null`
  nos dois e o detalhe responde 200 igual.
- `classificar_pedido(canceled, doc_status)` vira a fonte unica da regra (`'Y'`/`'C'` =
  cancelado; `DocStatus='C'` sem cancelamento = fechado); `diagnosticar_nped` passa a usa-la.
- MCP: docstrings de `listar_pedidos_com_os` e `detalhe_pedido_os` instruem a olhar
  `pedido_cancelado` antes de responder sobre a OS; `null` nao e "nao cancelado".
- Doc `API_OS_INTEGRACAO.md` §3.5 com as regras para quem consome. Provado contra o HANA
  de producao a partir do checkout local: 84282/84305/84314 → Cancelado, 84313 → Aberto,
  lista de 30 com 2 cancelados (84314, 84305). 558 testes.

## [2026-09-01] — Guia de colaboradores completo (o .md é a entrega)

O documento é a UNICA coisa que a equipe consumidora recebe, entao ele deixou de ser um
resumo e passou a responder o que faltava. O que entrou saiu de MEDICAO no endpoint em
producao, nao de suposicao:

- **Custo e cache** (§8): 13,6 KB / 0,25 s com filtro de empresa; 21 KB com as tres; 58 KB
  sem filtro. Com isso, a recomendacao explicita de **cachear e reconsultar a cada 30-60
  min** — o dado muda 1x/dia, e chamar por requisicao e desperdicio dos dois lados.
- **Como os dados realmente parecem** (§4): das 251 linhas, `matricula` e `data_admissao`
  nunca vem nulas; **`cargo` vem nulo em 120 — todas de desligados** (entre ativos, zero);
  e todo `desligado` tem `data_desligamento`. Mais uma linha de exemplo de quem saiu.
- ⚠️ **O setor "Principal"** (§9): sem `?somente_ativos=1` aparece um setor com **121
  pessoas, todas desligadas** — e ninguem tinha avisado. Quem montasse um filtro de tela
  a partir da resposta crua ofereceria "Principal" ao usuario. Agora esta documentado, com
  a lista dos setores REAIS por empresa.
- **Corpo dos erros** (§3) com o JSON de verdade do 400 e do 401, e a regra "cheque o `ok`
  antes do conteudo".
- **FAQ** (§14) com as 7 perguntas que a equipe faria: chave estavel, pessoa que troca de
  empresa, paginacao, ferias, campos novos.
- **Uso do dado** (§15): sao pessoas — uso interno, a chave nao vai para front-end, e o
  `status` existe para parar de exibir quem saiu.
- **Estabilidade do contrato** (§16): campos podem ser ACRESCENTADOS sem aviso (foi o que
  aconteceu hoje com `atualizado_em_br` e `carga_esperada_em`); os existentes nao mudam de
  sentido sem falar com quem consome.
- Duas receitas novas: quem saiu nos ultimos 90 dias (jq) e sincronizar com o cadastro do
  lado deles — que e o caso de uso real do campo `status`.

553 linhas, indice conferido (17 ancoras, nenhuma quebrada), fluxo em mermaid e alertas do
GitHub. **Nao ha versao publicada em pagina: o .md e a entrega.**

## [2026-09-01] — Documentação: "você não precisa de acesso ao Supabase"

A equipe que vai consumir pediu à organização o **conector do Supabase** do diretório da
Anthropic para usar este endpoint. Não precisa — e o pedido é caro: aquele conector abre
**SQL no banco**, que é um só para os três aplicativos (122 tabelas), e as tabelas de
espelho têm RLS forçado sem policy nenhuma, então ele **só funcionaria com a service_role**,
a chave que ignora toda a proteção. Ou é inútil, ou é acesso total.

O `API_RH_COLABORADORES.md` foi reescrito para responder isso antes de a pergunta nascer:

- Caixa nova na §1: **a única credencial necessária é a `X-API-Key` da 8077** (ou o token
  do MCP). Quem lê o banco é esta API — é para isso que ela existe.
- §9 ganhou **como registrar o MCP de verdade**: é um *conector personalizado* apontando
  para a `.11`, não o conector "Supabase" do diretório. E o aviso que faltava: **o MCP só
  responde de dentro da rede** — `192.168.7.11` é interno, o Claude na web não alcança;
  pelo navegador, o caminho é a REST chamada pelo código de quem consome.
- De quebra, o documento ficou mais didático: fluxo em **mermaid** (o GitHub renderiza),
  caixas de destaque `[!IMPORTANT]`/`[!CAUTION]`, tabela de campos com tipos, índice
  navegável e as receitas em curl/Python/JavaScript separadas por caso de uso.

## [2026-09-01] — /rh/colaboradores: frescor auditavel (`carga_esperada_em`, `atualizado_em_br`)

Saido do smoke de producao do espelho de colaboradores (endpoint e MCP no ar desde
31/08). Duas asperezas que apareceram lendo a resposta real, as duas ADITIVAS — nenhum
campo mudou de nome ou de significado:

- **`atualizado_em` e UTC, e "19:31" e lido como hora local por quem bate o olho no
  JSON** (e pelo modelo, na fachada MCP, que repassaria o horario errado ao usuario).
  Agora vai junto `atualizado_em_br`, o mesmo instante em horario de Brasilia.
- **`desatualizado` era um booleano magico**: dizia que a carga atrasou, sem dizer
  contra o que comparou. Agora vai `carga_esperada_em` — o slot 12:40 de dia util que
  a conta usou. Quem for depurar "por que isso esta true?" ve a resposta na propria
  resposta, sem abrir codigo.

`_colab_desatualizado` virou `_colab_frescor` (uma passada, tres campos). Doc §6
atualizada; +1 teste (131 no arquivo da API; suite 548 verde), ruff limpo.

**Deploy:** `git pull` na `.11` + restart do **`OrcaView-OS-API`**. O `OrcaView-MCP`
NAO precisa de restart: a fachada repassa o JSON da API como vem.
