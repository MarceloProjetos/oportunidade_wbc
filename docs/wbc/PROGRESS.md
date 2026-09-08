# PROGRESS.md — diário de bordo do WBCPython

> **Documento histórico do WBCPython standalone (até 08/09/2026).** O projeto virou o pacote
> `wbcpython/` do ServidorIntegracaoSAP: onde se lê `uv run wbcpython …`, hoje é
> `python -m wbcpython …` na raiz do repositório; `uv sync` é `pip install -r requirements.txt`;
> `python/WBCPython` é esta raiz; `ai_spec/` está explicado em `ai_spec/00_index.md`.
> Como rodar hoje: [`README.md`](README.md) desta pasta.

> Este arquivo é o mecanismo de continuidade do projeto. **No início de cada
> sessão**, leia-o do início ao fim antes de escrever qualquer código. **Ao final
> de cada sessão**, acrescente uma nova entrada e faça commit.
>
> Ordem de leitura ao retomar: este arquivo → `git log --oneline` → `git status`
> → `uv run pytest` → só então continuar implementando.

---

## Estado atual (resumo executivo)

| Item | Situação |
|---|---|
| Fase atual | **Fases 0–10 implementadas e validadas contra o ambiente real.** Falta uma decisão de negócio (ver abaixo) para a criação de documentos de venda funcionar ponta a ponta |
| Testes | 424 passando + 9 de integração pulados; os de integração passam contra o SAP real |
| Credenciais | ✅ `.env` criado pelo usuário, apontando para `SBOALTAMIRAHOMOLOG` em tudo (Service Layer, SQL Server, HANA) |
| Ambiente-alvo | `SBOALTAMIRAHOMOLOG` (trava de escrita em produção **ativa**) |
| Bloqueio atual | **Decisão comercial:** qual item do SAP usar nas linhas dos documentos (ver "Pendência final") |

### Regras de segurança em vigor (ambas implementadas em código)

| Regra | Escopo | Desligável? |
|---|---|---|
| Não escrever em `SBOALTAMIRAPROD` | Service Layer e SQL/HANA | Sim, por `WBC_BLOCK_PRODUCTION_WRITES` — apenas com autorização humana explícita |
| **Não escrever no SQL Server do WBC** | Todo o `WBCCAD`, em qualquer ambiente | **Não.** Sem variável de ambiente, sem parâmetro. Testes verificam essa propriedade por inspeção de AST |

---

## Restrições operacionais descobertas (importante para as próximas sessões)

Estas restrições não estavam previstas no prompt de desenvolvimento e afetam
como o trabalho pode ser conduzido:

**Estas restrições caíram na Sessão 7.** Passou a existir uma ferramenta de
shell remoto (Desktop Commander), então a IA consegue rodar `uv`, `git`,
`pytest` e os comandos da própria aplicação **na máquina do usuário**, dentro da
rede da Altamira. Foi isso que permitiu validar tudo contra o ambiente real.

O que continua valendo: a sessão em nuvem, por si só, não alcança a rede
interna; todo acesso ao SAP/WBC/HANA passa pela máquina do usuário.

---

## Sessão 1 — 2026-08-24

### O que foi feito

**Fase 0 — Scaffolding (concluída)**

- Estrutura de pacote em camadas criada (arquitetura hexagonal, conforme
  `ai_spec/03_architecture.md`):
  `domain/`, `application/`, `infrastructure/{service_layer,wbc_sql,hana}/`,
  `tracking/`, `host/`, `dashboard/`.
- `pyproject.toml` configurado para `uv`, com:
  - dependências base: `httpx`, `pydantic`, `pydantic-settings`, `python-dotenv`,
    `sqlalchemy`, `apscheduler`;
  - extras **opcionais**: `mssql` (`pymssql`), `hana` (`hdbcli`),
    `dashboard` (`streamlit`, `pandas`) — deixados opcionais de propósito, para
    que a suite de testes de domínio rode em qualquer máquina, mesmo sem os
    drivers nativos;
  - grupo `dev`: `pytest`, `pytest-cov`, `ruff`.
- `.gitignore` (com `.env` explicitamente ignorado), `.env.example`, `README.md`.

**Configuração e trava de segurança (concluída — era o item de maior risco)**

- `src/wbcpython/config.py`: configuração via variáveis de ambiente/`.env`
  usando `pydantic-settings`, dividida em `ServiceLayerSettings`,
  `WbcSqlSettings`, `HanaSettings`, `TrackingSettings` e a raiz `Settings`.
  - Nenhum nome de company DB ou schema HANA fica fixo no código — tudo é
    configurável, inclusive **qual** nome é considerado "produção"
    (`WBC_PRODUCTION_COMPANY_DB`), conforme Regra 4 de
    `ai_spec/04_environment_constraints.md`.
  - Senhas usam `SecretStr` (não aparecem em `repr`/logs).
  - `Settings.describe_environment()` produz um resumo legível do ambiente para
    logar no arranque do worker, **sem vazar credenciais** (há teste garantindo).
- `src/wbcpython/safety.py`: a regra de ambiente implementada **em código
  executável**, não só em documentação:
  - `ProductionWriteBlocked` — exceção específica, que não deve ser capturada e
    silenciada por código de aplicação;
  - `assert_http_call_allowed()` — bloqueia `POST`/`PATCH`/`PUT`/`DELETE`/`MERGE`
    contra a produção; **permite `GET`** (a regra proíbe alterar produção, não
    consultá-la);
  - `assert_sql_allowed()` + `looks_like_write_sql()` — bloqueia SQL de escrita
    contra o schema de produção, permitindo `SELECT`. Isso é o que viabiliza o
    caso real das views HANA, que podem residir no schema de produção
    (ver `ai_spec/02_data_model.md`, seção 3);
  - a heurística de detecção de SQL de escrita é **deliberadamente
    conservadora**: na dúvida classifica como escrita, porque um `SELECT`
    recusado custa muito menos que uma escrita em produção.

**Testes**

- `tests/test_safety.py` — 40+ casos cobrindo a trava: variações de caixa e
  espaços no nome da company DB, prefixo parecido que **não** é produção
  (`SBOALTAMIRAPRODUCAO_ANTIGO`), todos os métodos HTTP, detecção de SQL de
  escrita (incluindo CTE que termina em `INSERT`, e comentário de linha que
  não deve confundir a detecção).
- `tests/test_config.py` — garante padrões seguros (homologação + trava ligada),
  detecção correta de produção, configurabilidade do nome da produção, e que o
  resumo de ambiente não vaza usuário nem senha.
- `tests/conftest.py` — marcador `integration` registrado e **desativado por
  padrão**; testes de integração só rodam com `uv run pytest --run-integration`.
  Isso mantém `uv run pytest` rápido e executável em qualquer máquina.

**Ponto de entrada e guia de inicialização (acrescentado na mesma sessão)**

- `src/wbcpython/cli.py` + `src/wbcpython/__main__.py`: CLI do projeto, exposto
  como `wbcpython` (via `[project.scripts]`) e também como `python -m wbcpython`.
  - `wbcpython env` — mostra o ambiente atual sem expor credenciais; **falha com
    código 1** se a aplicação estiver apontada para produção com a trava
    desligada (combinação proibida pela regra do projeto);
  - `wbcpython doctor` — diagnóstico local: ambiente, credenciais presentes,
    TLS, e quais extras opcionais (`pymssql`, `hdbcli`, `streamlit`) faltam,
    já indicando a fase em que cada um será necessário;
  - `wbcpython worker` / `wbcpython dashboard` — registrados, mas retornam
    código 2 informando que pertencem às Fases 7 e 9. Ficam declarados desde já
    para que o guia de inicialização tenha comandos estáveis.
  - Nenhum comando acessa rede — são todos seguros de rodar em qualquer máquina.
- `COMO_INICIAR.md`: guia passo a passo (pré-requisitos, instalação,
  configuração, verificação, testes de integração, como iniciar cada componente,
  fluxo do dia a dia e tabela de problemas comuns), separando explicitamente o
  que já roda hoje do que depende das fases seguintes.
- `tests/test_cli.py`: 10 testes cobrindo o CLI, incluindo os casos de produção
  com/sem trava e a garantia de que usuário e senha não vazam na saída.

**Verificação realizada nesta sessão**

- `uv sync --all-groups` → OK
- `uv run pytest` → **65 passed**
- `wbcpython env` e `wbcpython doctor` executados de verdade, com e sem `.env`:
  saída conferida, senha não aparece, códigos de saída corretos (0 / 1).
- `uv run ruff check .` → **All checks passed**
- Carga de `.env` real testada num diretório temporário: valores lidos
  corretamente, senha mascarada como `SecretStr('**********')`.
- Aliases de variáveis de ambiente (`HANA_SCHEMA`, `WORKER_INTERVAL_SECONDS`,
  `LOG_LEVEL`) verificados empiricamente — funcionam apesar dos prefixos.

### O que ficou pendente

- **Git ainda não inicializado** na pasta do usuário (a IA não consegue executar
  `git` lá — ver "Restrições operacionais" acima).
- `.env` real não existe (aguardando credenciais de homologação).
- Nenhuma lógica de negócio implementada ainda — `domain/`, `application/`,
  `infrastructure/`, `tracking/`, `host/` e `dashboard/` estão como pacotes
  vazios (só `__init__.py`).

### Próximos passos (Fase 1)

1. **Usuário**: inicializar o git local, uma única vez:
   ```bash
   cd /home/amarques/Downloads/vstudio/WBCServConsoleNew/python/WBCPython
   git init
   git add -A
   git commit -m "Fase 0: scaffolding, configuração e trava de escrita em produção"
   ```
2. **Usuário**: instalar e validar o ambiente (guia completo em `COMO_INICIAR.md`):
   ```bash
   uv sync --all-groups
   uv run pytest            # esperado: 65 passed
   uv run wbcpython doctor  # deve apontar só a falta do .env
   ```
3. **Usuário**: fornecer as credenciais reais de `SBOALTAMIRAHOMOLOG`
   (Service Layer, SQL Server do WBC, HANA) para criação do `.env`.
4. **IA**: implementar `infrastructure/service_layer/client.py` — login/logout,
   gestão de sessão (cookie `B1SESSION`), renovação em caso de expiração,
   tratamento de erro do Service Layer, e a trava de `safety.py` aplicada em
   **um único ponto** de saída HTTP.
5. **Usuário**: rodar o primeiro smoke test de conexão
   (`GET /b1s/v1/OrcDetalhe?$top=1`) com `uv run pytest --run-integration`,
   e trazer o resultado.

---

## Sessão 2 — 2026-08-24

### Nova diretiva recebida

> "nunca escreva no SQL SERVER. somente leitura"

Registrada como **Regra 5** em `ai_spec/04_environment_constraints.md` e
implementada em código. Diferença importante em relação à regra de produção: a
regra de produção é *escopada por ambiente* e tem uma válvula de escape
documentada (para um eventual corte autorizado para produção); esta é
**absoluta** — não existe ambiente em que gravar no WBC seja correto. Por isso a
trava correspondente foi feita deliberadamente sem nenhuma chave de
desligamento.

### O que foi feito

**Diretiva de somente leitura (concluída)**

- `safety.py` reorganizado:
  - nova hierarquia de exceções: `SafetyViolation` como base, com
    `ProductionWriteBlocked` e `ReadOnlyViolation` como subclasses — permite
    tratar/observar os dois tipos de violação separadamente;
  - `assert_read_only_sql(sql, fonte=...)` levanta `ReadOnlyViolation` diante de
    qualquer comando de escrita. Assinatura sem parâmetro de bypass, por decisão
    de projeto;
  - `looks_like_write_sql()` reescrito e endurecido: agora remove comentários de
    bloco e de linha **e literais de texto** antes de analisar, e avalia cada
    comando separado por `;` de forma independente.
- Casos que a versão anterior errava e que agora estão cobertos por teste:
  - `SELECT * FROM T WHERE OBS = 'favor update urgente'` → **leitura** (a palavra
    estava só dentro do literal; antes era bloqueado indevidamente);
  - `SELECT update_date, create_user FROM ...` → **leitura** (nome de coluna);
  - `SELECT 1; DROP TABLE T` → **escrita** (comando escondido após `;`);
  - `SELECT * INTO nova_tabela FROM ...` → **escrita** (cria tabela).
- `tests/test_safety_readonly.py`: 28 testes, incluindo duas garantias
  *estruturais* — por inspeção de AST — de que a trava não importa `os` nem a
  configuração, e de que existe um único ramo condicional na função. São elas
  que impedem que a propriedade "não tem como desligar" regrida em silêncio.
- Regra propagada para `ai_spec/02_data_model.md`, `ai_spec/03_architecture.md`,
  `.env.example`, `README.md` e `COMO_INICIAR.md`.
- Consequência de arquitetura registrada: `WbcOrcamentoRepository` (Fase 3)
  expõe **apenas métodos de leitura**. Estado de processamento — inclusive
  "este orçamento já foi processado" — vai para o banco de tracking próprio,
  chaveado pela chave de negócio do WBC, nunca de volta para o WBCCAD.

**Fase 1 — Cliente do Service Layer (implementada; falta o smoke test real)**

- `infrastructure/service_layer/errors.py`: traduz o envelope de erro do SAP
  (`{"error": {"code": ..., "message": {"value": ...}}}`) numa exceção legível
  que preserva o código numérico do SAP — é ele que permite distinguir
  programaticamente "CNPJ já existe" (-5002) de "sessão expirada" (-304).
  Tolera respostas que não são JSON (HTML de proxy, corpo vazio).
- `infrastructure/service_layer/client.py`: `ServiceLayerClient` com
  - login/logout e uso como gerenciador de contexto (garante o logout);
  - **porta de saída única**: todas as requisições passam por `request()`, e é
    lá — e só lá — que a trava de segurança é aplicada. Nenhum outro módulo
    precisa lembrar de verificar o ambiente antes de gravar;
  - renovação automática de sessão: ao receber 401, refaz o login uma vez e
    repete a requisição;
  - helper `listar()` para consultas OData (`$filter`/`$orderby`/`$top`/`$select`);
  - `testar_conexao()` — smoke test de conectividade.
- `wbcpython check-sap`: novo comando de CLI que faz login e uma leitura mínima
  do `OrcDetalhe`. É o único comando que acessa a rede, e faz apenas leitura.
- `tests/infrastructure/test_service_layer_client.py`: 22 testes **offline**,
  usando `httpx.MockTransport` — cobrem login, credenciais ausentes, expiração
  e renovação de sessão, 401 persistente, tradução de erro do SAP, resposta
  não-JSON, parâmetros OData, e a trava de segurança. Ponto mais importante
  coberto: ao tentar `POST` apontado para produção, **nenhuma requisição chega a
  sair da máquina** — nem o login.
- `tests/infrastructure/test_service_layer_integration.py`: 5 testes de
  integração (pulados por padrão) para o usuário rodar na rede da Altamira.
  Todos de leitura. Um deles tenta confirmar empiricamente o achado de que
  `OrcDetalhe` é um histórico append-only; se homologação tiver poucos dados,
  ele se marca como *skip* em vez de falhar — ausência de evidência não é
  evidência de ausência.

### Bug encontrado e corrigido nesta sessão

`wbcpython check-sap` capturava `OSError` para tratar falha de rede, mas
`httpx.ConnectError` **não** é `OSError` — deriva de `httpx.HTTPError`. Efeito
prático: quem rodasse o comando fora da rede da Altamira (o caso mais provável
na primeira tentativa) receberia um traceback de ~40 linhas em vez de uma
mensagem útil. Descoberto ao executar o comando de verdade, corrigido, e travado
por um teste de regressão (`test_falha_de_rede_nao_vira_traceback`).

### O que ficou pendente

- Git continua não inicializado na pasta do usuário (a IA não executa `git` lá).
- `.env` real não existe (aguardando credenciais de homologação).
- Fase 1 só estará *concluída* quando o smoke test rodar contra o SAP real.

### Próximos passos

1. **Usuário**: `git init` + commit, `uv sync --all-groups`, `uv run pytest`
   (esperado: 117 passed, 5 skipped).
2. **Usuário**: preencher o `.env` com as credenciais de homologação e rodar:
   ```bash
   uv run wbcpython check-sap
   uv run pytest --run-integration -k service_layer_integration -v
   ```
   e trazer o resultado — inclusive se falhar, porque a mensagem de erro já foi
   preparada para ser diagnóstica.
3. **IA**: Fase 2 — máquina de estados do `SitCode` como funções puras, com
   testes cobrindo cada linha da tabela de `ai_spec/01_business_rules.md`.
   Esta fase **não depende de credenciais nem de rede**, então pode ser feita em
   paralelo enquanto o smoke test não acontece.

---

## Sessão 3 — 2026-08-24

### O que foi feito

**Fase 2 — Máquina de estados do SitCode (concluída)**

Antes de implementar, reli o `Program.cs` e o `Querys.resx` do projeto legado.
Foi a decisão certa: **a tabela de SitCode que estava em `ai_spec/01` estava
errada** — ela fora inferida e assumia "uma ação por SitCode", quando a lógica
real é um encadeamento aninhado sobre vários campos, cujo ramo final acumula
várias ações. `ai_spec/01_business_rules.md` foi reescrito por completo, agora
marcado como `[CONFIRMED]`.

Correções de entendimento que a leitura do código trouxe:

- `GetIntIdWBCOrcamentos` e `GetIdOrcamentosPedido` são `SELECT COUNT(*)` — o
  `"0"` comparado no legado significa "não existe cotação/pedido não-cancelado",
  e **não** um DocEntry, como eu havia registrado antes.
- `oRs.Fields.Item(3)` é `U_INO_StatusWBC`: o SitCode **como espelhado no SAP**,
  comparado como string. A decisão depende do par (SitCode do WBC, SitCode do
  SAP), não só do primeiro.
- O SitCode 61 **não tem tratamento próprio em lugar nenhum** — só chega ao
  espelhamento de status.
- O encerramento (70/90/99) só é alcançável **se já existir cotação**, porque
  está dentro do ramo final. Sem cotação, o ramo anterior cria uma cotação para
  um orçamento já encerrado. Comportamento preservado (é o que roda hoje), mas
  registrado como questionável.

Implementado em:

- `domain/revisao.py` — comparação de revisões. A fórmula do legado
  (`ASCII - 64`) produz negativos para dígitos, o que parece bug mas gera a
  ordem correta para os dados reais (`"0" < "9" < "A" < "B"`), já que `"0"` é a
  versão inicial. **Preservada de propósito**: "corrigi-la" mudaria em silêncio
  a comparação de milhares de registros históricos. O que foi corrigido: o
  legado quebra (`char.Parse`) com revisão de mais de um caractere; aqui degrada
  para o primeiro caractere, com modo estrito opcional.
- `domain/sitcode.py` — `decidir(EstadoIntegracao) -> Decisao`, função pura.
  Devolve uma **lista ordenada de ações** (não uma ação única), porque no legado
  as ações do ramo final se acumulam. Cada decisão carrega `motivos` legíveis e
  um nome de `regra` — insumo direto para o dashboard e para o log, algo que o
  sistema atual não tem.
- 64 testes novos, um por ramo, com nomes que descrevem a regra.

**Melhorias deliberadas sobre o legado, na troca de PN:** duas guardas que o
legado não tem — não agir se o PN novo vier vazio, e não recriar o pedido se o
PN novo for igual ao atual (idempotência). Ambas cobertas por teste.

### Defeitos do legado encontrados — `DEFEITOS_LEGADO.md` (novo)

Dois deles podem estar afetando a operação **hoje**:

1. 🔴 `GetDocNumOportunidades` termina com `and T0."U_ORCNUM_WBC" = '00121819'`
   — a integração inteira está filtrada para **um único orçamento**. Tem cara de
   código de depuração versionado (há blocos vazios de *breakpoint* para esse
   mesmo número no `Program.cs`). **Vale conferir qual versão está publicada no
   servidor**, independentemente da reescrita.
2. 🔴 Orçamento com número vazio dispara `return` no `Main` — aborta a execução
   inteira em vez de pular o registro, e sem log.

Mais seis defeitos catalogados (tautologias sempre verdadeiras, exceção fixa por
número de oportunidade, janela de datas misturando -6 e -9 meses, `AddLog`
vazio, `catch` que desreferencia `ex.InnerException` sem checar, código morto).

Decisão sobre as tautologias: implementei o comportamento **efetivo** (o que
elas de fato produzem hoje), não a intenção aparente — mudar isso alteraria o
comportamento em produção sem que ninguém tivesse pedido. Ramos nomeados e
cobertos por teste, para que a decisão fique visível e possa ser revista.

### O que ficou pendente

- Git continua não inicializado na pasta do usuário.
- `.env` real não existe; smoke test da Fase 1 ainda não rodou.
- Fase 3 (repositório do WBC) ainda não começou.

### Próximos passos

1. **Usuário**: ler `DEFEITOS_LEGADO.md` e responder as 4 perguntas do resumo
   final — principalmente a nº 1 (filtro fixo em produção), que é a mais urgente
   e independe da reescrita.
2. **Usuário**: `git init` + commit; `.env`; `uv run wbcpython check-sap`.
3. **IA**: Fase 3 — `WbcOrcamentoRepository` **somente leitura** (Regra 5),
   com consultas parametrizadas. Não depende de rede: dá para implementar e
   testar com um banco SQLite em memória simulando o esquema.

---

## Sessão 4 — 2026-08-24

### Correções aplicadas ao sistema legado (C#)

A pedido do usuário, os defeitos do legado foram corrigidos **no próprio código
C#**, não apenas evitados na reescrita. Arquivos alterados: `Program.cs` e
`Querys.resx`; originais preservados como `*.original`; registro completo em
`WBCServConsole/CORRECOES_LEGADO.md`.

**Evidência nova, importante:** inspecionei o executável compilado
`bin/x64/Debug/WBCServConsole.exe` (dez/2025) e **confirmei que o filtro fixo
`and T0."U_ORCNUM_WBC" = '00121819'` está embutido nele**. Se for esse o binário
publicado, a integração está restrita a um único orçamento desde dezembro.

Corrigidos: o filtro fixo (defeito 1), o `return` que abortava a execução
inteira (2), o `catch` que mascarava o erro original (6) e o código morto (8).

Detalhe que quase passou batido no defeito 2: o avanço do cursor (`oRs.MoveNext()`)
está no **fim** do laço. Trocar `return` por `continue` sem chamar `MoveNext()`
antes trocaria uma parada silenciosa por um **laço infinito**. A correção chama
`MoveNext()` explicitamente antes do `continue`.

Não corrigidos, por dependerem de decisão de negócio: as tautologias (3), a
exceção do orçamento `00118376` (4) e a janela de datas (5). A refatoração do SQL
concatenado (7) foi descartada — risco alto num sistema que será substituído.

⚠️ **Alerta registrado para o usuário:** remover o filtro faz a primeira execução
processar todo o acumulado desde dezembro, o que pode criar muitas cotações e
pedidos de uma vez no SAP. O `CORRECOES_LEGADO.md` traz uma consulta de
diagnóstico (somente leitura) para medir o volume antes de publicar.

**Não consegui compilar** — não há compilador C# neste ambiente e o projeto é
.NET Framework 4.5.2. As alterações foram verificadas por análise estrutural
(balanceamento de chaves, ausência de referências órfãs, XML do `.resx` válido),
mas precisam ser compiladas no Visual Studio antes de qualquer teste.

### Fase 3 — Repositório do WBC, somente leitura (concluída)

- `wbc_sql/models.py` — `OrcamentoWbc` e `ItemOrcamentoWbc` (pydantic, imutáveis).
  O legado devolve o join achatado, com o cabeçalho repetido em cada item; aqui a
  estrutura é reconstituída como cabeçalho + itens.
- `wbc_sql/queries.py` — consultas parametrizadas. Três mudanças deliberadas em
  relação ao SQL legado:
  1. parâmetros nomeados (`:orcnum`) no lugar de `String.Format`;
  2. `COALESCE` no lugar de `ISNULL` — `ISNULL` só existe no SQL Server, e
     `COALESCE` é padrão, o que torna as consultas **testáveis contra SQLite**
     sem manter duas versões do SQL;
  3. sem o `GROUP BY` sobre todas as colunas do legado, que só servia para
     deduplicar; o agrupamento passa a ser feito em Python, explícito e testável.
- `wbc_sql/repository.py` — `RepositorioOrcamentosWbc` (Protocol) e a
  implementação SQL. A promessa de somente leitura é sustentada em três camadas:
  a interface **não oferece método de escrita**; toda consulta passa por um
  ponto único que chama `assert_read_only_sql()`; e a URL de conexão pede
  `ApplicationIntent=ReadOnly` como defesa adicional.
- Consulta nova, sem equivalente no legado: `orcamentos_alterados_desde()`, para
  varredura incremental pelo lado do WBC — hoje a lista de trabalho depende
  inteiramente do que vem do SAP.

**Testes (19 novos):** rodam contra **SQLite em memória** com o mesmo esquema do
WBC, exercitando o SQL de verdade (nomes de coluna, joins, ordenação) em vez de
usar mocks. Cobrem, entre outros: agrupamento de itens sob um cabeçalho,
ordenação por sequência (com dados inseridos fora de ordem de propósito),
orçamento sem itens que **não** pode virar um item fantasma vindo do `LEFT JOIN`,
ausência de dados comerciais virando zero, e três garantias de somente leitura —
inclusive uma que varre o módulo de consultas verificando que nenhuma delas é de
escrita.

### Próximos passos

1. **Usuário**: compilar o legado corrigido no Visual Studio; rodar a consulta de
   diagnóstico; decidir sobre a publicação. E responder as 3 perguntas de
   negócio que restam (`DEFEITOS_LEGADO.md`, defeitos 3, 4 e 5).
2. **Usuário**: `git init` + commit; `.env`; `uv run wbcpython check-sap`.
3. **IA**: Fase 4 — `HanaViewsRepository` (`hdbcli`), com schema parametrizado e
   o smoke test que resolve a dúvida em aberto sobre em qual schema as views
   existem.

---

## Sessão 5 — 2026-08-25 — revisão de código

Sessão dedicada a revisar o que já existia, antes de avançar para a Fase 4.
Além da minha própria leitura, rodei uma revisão **adversarial independente**
(um segundo agente, instruído a tentar burlar as travas e a verificar cada
hipótese executando código). Ela encontrou 11 problemas; confirmei os
principais reproduzindo cada um antes de corrigir.

### Estado encontrado no início da sessão

O usuário já havia rodado `uv sync` e `pytest`, e criado o `.env` — conferido:
aponta para `SBOALTAMIRAHOMOLOG` em Service Layer, SQL Server e HANA, com a
trava de produção ativa. O `git init` continua pendente.

### Defeitos corrigidos (todos com teste de regressão)

**1. 🔴 A trava de produção tinha uma chave de desligamento silenciosa.**
`production_company_db` era um `str` livre e `is_production()` compara por
igualdade. Com `WBC_PRODUCTION_COMPANY_DB` vazio — ou com um simples erro de
digitação — **nenhum** destino era reconhecido como produção: um
`POST /Quotations` contra `SBOALTAMIRAPROD` passava, e o `wbcpython env` ainda
reportava "homologação, trava ATIVA" com código de saída 0. As próprias
ferramentas de diagnóstico mentiam.
Correção em duas frentes: validação no `Settings` recusando o valor vazio já no
arranque, e a trava passando a **falhar fechada** — destino desconhecido ou
nome de produção não configurado agora bloqueiam, em vez de liberar.

**2. 🔴 A trava de SQL era burlável por literal.** `_strip_noise` removia
comentários **antes** dos literais, então um `--`, `''` ou `/*` dentro de uma
string apagava o resto do comando. Verificado:
`SELECT 'a--b' FROM t; DROP TABLE Alvo` era classificado como leitura.
Também passava um `SELECT ... INTO NovaTabela` (que cria tabela no SQL Server).
Correção: as expressões regulares deram lugar a um **scanner de estado**, que
percorre o texto uma vez sabendo a cada caractere se está dentro de literal,
comentário de linha, comentário de bloco ou identificador citado. Novo arquivo
`tests/test_safety_bypass.py` guarda cada bypass como regressão.

**3. 🟠 O `LEFT JOIN` duplicava os itens do orçamento.** O join com `ORCIMP` e
`ORCCAB` é por `ORCNUM`, e nada garante uma única linha nessas tabelas. Com
duas, o produto cartesiano repetia **cada item** — e `quantidade_total_itens`,
que alimenta a criação de cotação e pedido no SAP, saía dobrada. Verificado:
1 item de quantidade 5 virava 2 itens e total 10. Era justamente isso que o
`GROUP BY` do legado escondia, e minha docstring afirmava (errado) que o
agrupamento já era feito em Python. Correção: deduplicação por
`(id_integracao, orcitm)`.

**4. 🟠 Revisão ausente disparava ação destrutiva.** `SEM_REVISAO` era 0, mas a
fórmula ASCII-64 dá -16 para `"0"` — então a ausência caía *entre* os dígitos e
as letras, e um orçamento sem revisão no WBC era considerado **mais novo** que
uma cotação com revisão `"0"` no SAP, entrando no ramo de cancelar-e-recriar.
Dado faltando provocava cancelamento de documento. O legado tem o mesmo defeito;
aqui foi corrigido de propósito (a ausência agora ordena abaixo de tudo), e a
divergência está documentada no módulo.

**5. 🟠 A URL do SQL Server não escapava a senha.** Interpolada crua: uma senha
com `@` fazia o trecho seguinte virar **hostname** (conectando em outro
servidor, e vazando parte da senha no `repr()` "mascarado" da URL); uma senha
com `%` era lida como escape de URL e autenticava com senha diferente, em
silêncio. Trocado por `URL.create()`, que trata a senha como dado.

**6. 🟠 Resposta `200` não-JSON estourava traceback cru** — o sintoma clássico
de `SL_BASE_URL` errado ou proxy no caminho. Agora vira erro legível apontando
o que verificar.

**7. 🟠 O retry de sessão ignorava o código SAP `-304`.** A docstring dizia que
algumas versões sinalizam sessão inválida com `-304` sob outro status, mas o
código só testava `401`. A renovação passa a ser decidida pelo erro, não pelo
status — e um erro de negócio (ex.: `-5002`, CNPJ duplicado) continua **não**
disparando novo login, que seria inútil e mascararia a causa.

**8. 🟠 `SL_CA_BUNDLE` inexistente derrubava o `check-sap`** com traceback,
porque o cliente era construído fora do `try` e o `httpx` valida o certificado
já no construtor. O `doctor` agora também confere se o arquivo existe.

**9. 🟡 `TRACKING_DB_URL` virou `SecretStr`** — inofensivo com SQLite, mas
passará a conter usuário e senha quando o tracking migrar para PostgreSQL.

**10. 🟡 Docstrings corrigidas** onde prometiam mais do que o código entregava
(a "única porta de saída HTTP" tem duas exceções legítimas: `login` e `logout`).

**11. 🟡 Documentação desatualizada** — `COMO_INICIAR.md` ainda dizia "Fase 0
concluída" e "117 passed".

### Um falso positivo da revisão, que vale registrar

A revisão apontou `SELECT 'a''; DROP TABLE T; SELECT ''b' FROM t` como bypass.
Não é: em SQL, `''` dentro de um literal é o escape de uma aspas simples, então
o texto inteiro é **um único literal** e o `DROP` nunca executa — é só conteúdo
de string. Classificá-lo como escrita seria um falso positivo, recusando uma
consulta legítima. O scanner acompanha o escape corretamente, e o caso virou um
teste documentando por quê. Foi o motivo de reproduzir cada achado antes de
corrigir, em vez de aceitar a lista de plano.

### Categorias sem achado

Sem vazamento de credenciais em `repr`/`model_dump`/log/CLI (fora dos itens 5 e
9, já corrigidos). A Regra 5 (WBC somente leitura) foi confirmada **sem** chave
de desligamento. A máquina de estados não apresentou defeito de correção.

### Próximos passos

1. **Usuário**: `git init` + commit (segue pendente); rodar
   `uv run wbcpython check-sap` e `uv run pytest --run-integration` na rede da
   Altamira e trazer o resultado.
2. **Usuário**: as 4 perguntas de negócio de `DEFEITOS_LEGADO.md` (defeitos 3,
   4 e 5) e a decisão sobre publicar o legado corrigido.
3. **IA**: Fase 4 — `HanaViewsRepository` via `hdbcli`, com schema
   parametrizado e o smoke test que resolve em qual schema as views existem.

---

## Sessão 6 — 2026-08-25

### 1. Correções no C# revertidas, a pedido

O usuário decidiu não mexer no build C# atual. Restaurei `Program.cs` e
`Querys.resx` originais em `WBCServConsole/`, e movi as versões corrigidas para
`correcoes_propostas_csharp/` — ficam disponíveis se um dia forem usadas, sem
risco de alguém compilar mudanças não verificadas por engano.

Observação: sobraram em `WBCServConsole/` os arquivos `Program.cs.original`,
`Querys.resx.original` e `CORRECOES_LEGADO.md`, que agora são redundantes. Não
consigo apagar arquivos na máquina do usuário; dá para removê-los à mão.

### 2. Teste de acesso ao SAP de homologação — feito pelo Chrome

Como a sessão em nuvem não alcança a rede da Altamira, usei o **Chrome do
usuário**, que está nela. Não deu para fazer o login autenticado (ver limitação
abaixo), mas o caminho de rede foi medido e o resultado é conclusivo.

Medi a latência de falha de várias portas, com 7 repetições cada, comparando com
portas sabidamente fechadas e com um host inexistente:

| Alvo | Mediana | Leitura |
|---|---|---|
| `sapbusinessonehana-vm:50000` (Service Layer) | 7 ms | **porta aberta** |
| `sapbusinessonehana-vm:30013` (SLD) | 9 ms | porta aberta |
| `sapbusinessonehana-vm:30015` (HANA SQL) | 7 ms | porta aberta |
| `sapbusinessonehana-vm:49999` / `:44444` | 2–3 ms | portas fechadas |
| host inexistente | 6000 ms (timeout) | DNS não resolve |

Conclusões confirmadas:

- **DNS resolve** para `sapbusinessonehana-vm` (o host inexistente pendura até o
  timeout; este não).
- **A porta 50000 está aberta e aceitando conexão** — consistentemente ~3× mais
  lenta para falhar que uma porta fechada no mesmo host, o que só acontece se o
  TCP conecta e a falha vem depois, no TLS.
- A falha em si é do **certificado autoassinado**, que o Chrome recusa. Bate
  exatamente com o `SL_VERIFY_SSL=false` da configuração.

Ou seja: `SL_BASE_URL`, porta e DNS estão corretos, e o servidor responde.

**Limitação do método:** o Chrome não deixa uma extensão ler nem interagir com a
página de erro de certificado, e não seria adequado colocar credenciais dentro
de JavaScript de navegador. Então o **login autenticado continua pendente** e
precisa de `uv run wbcpython check-sap`, rodado pelo usuário. O que ficou
comprovado é toda a camada abaixo do login.

### 3. Fase 4 — Repositório das views do HANA (concluída)

- `hana/models.py` — `EvolucaoOportunidade` e `MunicipioCliente`. Os campos
  `pct_comissao`/`retorno`/`indice` são opcionais porque, em dados reais, só vêm
  preenchidos em parte dos status.
- `hana/identificadores.py` — **o ponto delicado desta fase.** O nome do schema
  não pode ser parâmetro de bind: em SQL, parâmetro é *valor*, e schema é
  *identificador*. Este é o único lugar do projeto em que algo vindo de
  configuração é interpolado no texto de uma consulta, então o nome é validado
  contra lista branca de caracteres e citado. Sem isso, um `HANA_SCHEMA` como
  `X"."Y` mudaria o objeto consultado.
- `hana/repository.py` — `RepositorioViewsHanaSql`, somente leitura, com ponto
  único de execução aplicando **as duas travas**: a de leitura (Regra 5) e a de
  produção (Regra 1). `hdbcli` fica como import adiado, para o projeto todo
  continuar funcionando em máquinas sem o driver nativo.
- `views_existem()` consulta o **catálogo** (`SYS.VIEWS`), não as views — assim
  responde "onde elas estão" mesmo sem permissão de leitura nelas.
- `wbcpython check-hana` — novo comando de CLI que roda esse diagnóstico.
- 40 testes novos, com conexão falsa (sem driver, sem rede), verificando o
  **texto exato do SQL emitido**: que o schema aparece citado, que o número do
  orçamento vai como parâmetro de bind e nunca concatenado, e que trocar
  `HANA_SCHEMA` troca de fato a consulta.

### Dúvida em aberto que a Fase 4 instrumentou

O projeto não sabia se as views existem sob o schema de homologação ou apenas
sob o de produção. Não dava para descobrir isso daqui — mas agora existe o
comando que responde:

```bash
uv sync --all-groups --extra hana
uv run wbcpython check-hana
```

Se a resposta for "apenas em produção", basta apontar `HANA_SCHEMA` para lá: a
leitura é permitida pela regra do projeto, e o repositório já foi construído
para funcionar nos dois casos sem mudança de código.

### Próximos passos

1. **Usuário**, na rede da Altamira: `uv run wbcpython check-sap`,
   `uv run wbcpython check-hana` e `uv run pytest --run-integration`.
2. **Usuário**: `git init` + commit (segue pendente desde a Sessão 1).
3. **IA**: Fase 5 — leitura e escrita do `OrcDetalhe`, com o smoke test de
   `POST` em `SBOALTAMIRAHOMOLOG`. A escrita depende do resultado do item 1.

---

## Sessão 7 — 2026-08-25 — Fases 5 a 10 e validação no ambiente real

Sessão longa. Duas coisas mudaram o jogo: o usuário pediu para seguir até o fim,
e passou a existir shell remoto na máquina dele — o que permitiu, pela primeira
vez, **validar tudo contra o SAP de verdade**.

### Ajuste de ordem

A Fase 8 (tracking) foi feita **antes** da 7 (worker), porque o worker grava no
banco de acompanhamento. Registrado aqui conforme o prompt permite.

### O que foi implementado

**Fase 5 — OrcDetalhe.** `domain/numeros.py` resolve o achado de separador
decimal inconsistente: reconhece a convenção por registro (a regra é "o último
separador é o decimal", que acerta `1.234,56` e `1,234.56`) e **avisa em log**
em vez de devolver zero calado. `orcdetalhe.py` deliberadamente **não expõe
atualização** — o UDO é histórico append-only, e um `atualizar()` ali seria
convite a corromper o registro anterior.

**Fase 6 — Documentos e oportunidades.** Cotações/Pedidos com criar, atualizar,
cancelar (ação dedicada) e `cancelar_e_recriar` — nessa ordem, para nunca haver
dois documentos vigentes para o mesmo orçamento. O vínculo do documento à
oportunidade lê a coleção existente e **acrescenta**: o PATCH do Service Layer
substitui a coleção inteira, e enviar só o novo apagaria os anteriores.

**Fase 8 — Tracking.** Três tabelas (acompanhamento, eventos, execuções) mais a
trava. Um teste de concorrência com threads pegou uma **corrida real** na trava:
"consulta se existe, senão insere" deixava dois processos inserirem. Reescrito
para a chave primária arbitrar — quem consegue gravar levou a trava.

**Fase 7 — Worker.** Trava de execução única, isolamento de falha por registro
(um orçamento problemático não derruba o ciclo), agendamento em processo e
histórico de execuções. Comando `ciclo` para uma passada só, e
`ciclo --orcamento X` para reprocessar um caso — o que no legado exigia editar
a consulta no código.

**Fase 9 — Dashboard.** Streamlit com KPIs, lista filtrável, detalhe com o
histórico de decisões (a regra aplicada e o motivo) e reprocessamento manual
auditado. Lê só o banco de tracking.

### Validação no ambiente real — e os 4 erros que só ela pegaria

| # | Erro | Como apareceu |
|---|---|---|
| 1 | `OpenDate` não existe na entidade `SalesOpportunities` — o nome é **`StartDate`** | `HTTP 400 — Property 'OpenDate' is invalid` |
| 2 | `OpprId` também não — a chave é **`SequentialNo`** | `HTTP 400 — Property 'OpprId' is invalid` |
| 3 | Colunas das views HANA estavam **chutadas** | `invalid column name: Municipio` |
| 4 | `pymssql` **não aceita** `ApplicationIntent` | `TypeError: connect() got an unexpected keyword argument` |

Os dois primeiros são a mesma armadilha: o legado consulta o **banco**, onde as
colunas se chamam `OpenDate`/`OpprId`; o Service Layer expõe a **entidade**, com
outros nomes. Nenhum teste offline pegaria isso.

O nº 3 rendeu uma correção maior: consultei o catálogo (`SYS.VIEW_COLUMNS`) e
descobri que `VW_CLIENTE_MUNICIPIO_ALTA` tem `AbsId, Code, Country, State,
Name, IbgeCode, Name_N`. A busca passou a usar `Name_N` (nome sem acento) em vez
de `Name` — o município vem do WBC sem acentuação, então comparar com o nome
acentuado erraria em toda cidade com acento. E `VW_EVOL_OPORTUNIDADE_ALT` tem 29
colunas, sendo `Retorno`, `Indice` e `Negociacao` **NVARCHAR** — o mesmo problema
de separador decimal, agora também tratado ali.

O nº 4: `ApplicationIntent=ReadOnly` era defesa em profundidade, mas o driver o
rejeita e derrubava toda leitura do WBC. Removido. A garantia de somente leitura
não dependia dele; como camada extra **no servidor**, o certo é um usuário de
banco `db_datareader`.

### 🎯 Dúvida do HANA: RESOLVIDA

`uv run wbcpython check-hana` respondeu de forma definitiva:

* em `SBOALTAMIRAHOMOLOG`: **nenhuma** das duas views existe;
* em `SBOALTAMIRAPROD`: **as duas** existem.

Então `HANA_SCHEMA=SBOALTAMIRAPROD` é o valor correto, inclusive em
desenvolvimento. Isso **não** viola a Regra 1: ela proíbe *alterar* a produção,
não consultá-la, e todo o acesso ao HANA aqui é somente leitura, garantido em
código. O `.env` do usuário e o `.env.example` já foram ajustados.

Leituras reais confirmadas: orçamento `00123316` → status "Calculo financeiro",
PctComissao 5, Retorno 9.0000; município Cotia/SP → AbsId 4925.

### Validações que passaram contra o ambiente real

* `uv run wbcpython check-sap` → conexão OK com `SBOALTAMIRAHOMOLOG`
* 5/5 testes de integração do Service Layer
* 4/4 testes de integração do HANA
* Leitura do WBC (SQL Server): orçamento `00125401` → 6 itens, comissão 3%,
  retorno 8,1 — tudo lido e ordenado corretamente
* **Ciclo completo ponta a ponta** (`wbcpython ciclo --orcamento 00125536`):
  login → lista oportunidades → lê o WBC → decide → registra → logout.
  1 processado, 1 sucesso, 0 erros. Escolhi de propósito um orçamento com
  SitCode 5 (abaixo do mínimo) para exercitar todo o caminho **sem criar
  documentos**. O acompanhamento registrou o motivo: *"SitCode 5 não é
  processado (mínimo: acima de 10)"*.
* 424 testes passando na máquina do usuário (Python 3.14)
* `git init` + commit feitos; 65 arquivos versionados; `.env` **não** versionado

### ⚠️ Pendência final — é decisão de negócio, não técnica

A criação de Cotação/Pedido ainda não fecha. Descobri o requisito exato
provocando o erro do SAP em homologação, em dois passos (nenhum documento foi
criado):

1. sem `CardCode` → `-2028 Customer record not found`. **Corrigido**: o
   `CardCode` vem da oportunidade, e na troca de PN vale o parceiro novo.
2. com `CardCode`, sem linhas → `-5002 Document total value must be zero or
   greater than zero`. **Falta `DocumentLines`.**

E aqui está o problema real: montar as linhas exige saber **qual item do
cadastro do SAP** corresponde a cada linha do orçamento. A consulta ao WBC traz
`ORCPRDCOD`, mas nos dados reais esse campo vem **vazio** — as linhas são
descritas só por texto livre (`ORCTXT`), como
*"ALMOXARIFADO 02 Módulos modelo A, de estruturas..."*.

**Pergunta para a área comercial:** qual item do SAP deve ser usado? Um item
genérico de serviço/estrutura para todas as linhas? Um por família de produto?
É a última coisa que falta para a integração criar documentos, e não dá para
inferir do código legado — ele monta as linhas em `ServiceProcess.CriaCotacao`,
que seria o próximo lugar a investigar caso se queira reproduzir exatamente o
comportamento atual.

### Próximos passos

1. **Usuário/comercial**: responder a pergunta acima sobre o item do SAP.
2. **IA**: implementar `DocumentLines` conforme a resposta e rodar o smoke test
   de criação em homologação.
3. Depois disso: rodar um ciclo completo sobre as 20 oportunidades de
   homologação e conferir os documentos gerados.

### Decisões e dúvidas em aberto

| # | Item | Situação |
|---|---|---|
| 1 | Schema HANA das views | ✅ **RESOLVIDO na Sessão 7** — existem apenas em `SBOALTAMIRAPROD`. Leitura do schema de produção é o padrão confirmado. |
| 2 | Endereço/porta do Service Layer | ✅ **CONFIRMADO na prática** — `sapbusinessonehana-vm:50000` funciona. Falta apenas a TI confirmar que vale para todos os ambientes. |
| 3 | Certificado TLS: aceitar autoassinado (`SL_VERIFY_SSL=false`) ou emitir certificado válido? | **Em aberto** — decisão para antes da produção. Já há suporte a `SL_CA_BUNDLE` para quando houver certificado próprio. |
| 4 | Significado exato dos valores de `SitCode` | **Resolvido na Sessão 3** — a lógica foi extraída do código real e `ai_spec/01` reescrito. Falta apenas confirmar o *nome de negócio* de cada SitCode com a equipe do WBC (a mecânica já está correta). |
| 5 | 🔴 A versão publicada em produção tem o filtro fixo `= '00121819'`? A integração está processando todos os orçamentos? | **Em aberto — urgente.** Ver `DEFEITOS_LEGADO.md` nº 1. |
| 6 | As tautologias do legado deveriam ser `&&`? Qual era a intenção? | **Em aberto** — implementado o comportamento efetivo atual. Ver `DEFEITOS_LEGADO.md` nº 3. |
| 7 | O orçamento `00118376` ainda precisa de tratamento especial? | **Em aberto** — exceção não portada. Ver `DEFEITOS_LEGADO.md` nº 4. |
| 8 | Janela de busca de oportunidades: 6 meses, 9 meses, outra? | **Em aberto** — a expressão do legado mistura as duas. Ver `DEFEITOS_LEGADO.md` nº 5. |


---

## Sessão 8 — a prévia (`pendentes`) e o roteiro de homologação

**O que mudou.** Faltava uma peça de segurança óbvia em retrospecto: não havia
como saber *o que* um ciclo faria antes de deixá-lo fazer. O comando
`wbcpython pendentes` preenche essa lacuna — lista, orçamento a orçamento, a
regra que o domínio aplicaria e as ações que sairiam dela, usando apenas
leituras (GET no Service Layer, SELECT no WBC). Não abre transação de tracking
e não instancia o processador — que é quem escreve. Há teste garantindo
justamente isso: a garantia **negativa**, de que nada é escrito.

Para que a prévia e o ciclo real nunca divirjam, a montagem do estado da
decisão saiu de dentro do processador e virou a função de módulo
`application.processar.montar_estado()`. Os dois passam a enxergar exatamente o
mesmo retrato.

**Rodado contra o ambiente real de homologação**, com resultado limpo:
20 oportunidades na janela, 12 resultariam em escrita no SAP —
8 `criar_cotacao` (regras `sem_cotacao_cria` e `emitido_sem_cotacao`) e
6 `atualizar_cotacao` (`revisao_sobre_cotacao_emitida`); as demais caem em
`sitcode_abaixo_do_minimo` ou `revisao_congelada` e não fariam nada.

Detalhe que confirma uma decisão anterior: **todas as 20 vêm com revisão
vazia**. Era exatamente o caso que o `SEM_REVISAO = -1000` protege — na
ordenação ingênua do legado, revisão vazia caía *entre* dígitos e letras e
podia disparar cancelamento e recriação de documento sobre dado ausente.

Também foi escrito `COMO_TESTAR_HOMOLOGACAO.md`: roteiro em 10 passos ordenados
por risco, em que nada escreve até o passo 6.

**Ambiente:** `uv sync --all-groups --all-extras` passou a ser o comando de
instalação recomendado — sem os extras, faltava `pymssql` e a prévia parava no
acesso ao WBC.

**Estado:** 429 testes passando, lint limpo, tudo versionado.

---

## Sessão 9 — o item do SAP, e a integração criando documentos de verdade

**A pergunta que destravou tudo:** "existe no C# uma tabela que escolhe o
produto certo a partir do texto em `ORCTXT`?" A resposta é que **não é pelo
texto** — e essa era exatamente a inferência errada que vinha bloqueando o
projeto desde a Sessão 7.

O item do SAP vem do **grupo de produto** (`GRPCOD`), através do de-para
`@INO_GRP_PRODUTOS` (`Querys.resx → GetItensSAP`). O `ORCTXT` vai para o UDF de
linha `U_INO_D_Adicionais` e, cortado no primeiro "Valor", para
`U_INO_Composicao`. Nunca decide item nenhum.

A tabela tem 11 grupos e existe idêntica em produção e homologação. Os 11
códigos **cobrem 100% das 20.997 linhas** da tabela de integração — o fallback
nunca dispara com os dados de hoje.

### O erro maior: estávamos lendo a tabela errada

Ao conferir os dados, `INTEGRACAO_ORCITM` — a tabela cujo nome diz "itens" e da
qual nossa consulta lia — apareceu **vazia** para todos os orçamentos
pendentes. Ela parou de receber dados: o maior `ORCNUM` nela é `00125478`,
enquanto os orçamentos em processamento já passam de `00125535`.

As linhas vivem em `INTEGRACAO_ORCIMP`, desnormalizada, que é de onde o legado
sempre leu. Lendo da tabela errada, **todo orçamento recente vinha sem linha
nenhuma** — e era essa a causa real do `-5002 Document total value must be zero`
que parecia uma decisão de negócio pendente. Havia um teste de regressão
possível e agora ele existe: o esquema de teste **não tem** `INTEGRACAO_ORCITM`.

### Regras implementadas (decididas pelo negócio)

1. **Quantidade 1** quando `ORCPRDQTD` for vazia, nula, inconsistente ou zero —
   hoje é o caminho único, já que a coluna é nula em 100% das linhas, mas a
   regra está escrita para quando o WBC passar a preenchê-la. `ORCVAL` é o
   total da linha, então o preço enviado é o unitário.
2. **Fallback para Porta-Paletes** (grupo `2`) quando o grupo não está no
   de-para, **com aviso** no log e no acompanhamento. O legado tratava esse caso
   de duas formas incompatíveis: caía no grupo 2 em silêncio (`:1223`, `:1256`)
   ou **descartava a linha inteira** (`:356`, `:626`) — esta última fazia o
   documento chegar ao SAP com valor menor que o do WBC, sem ninguém saber.
3. **Depósito `08`** em toda linha.

### Mais três exigências do SAP, descobertas rodando

| Erro | Causa | Correção |
|---|---|---|
| `-5002 Specify an active branch [OQUT.BPLId]` | faltava a filial | `BPL_IDAssignedToInvoice = 1` (única filial ativa; o legado fixava o mesmo valor) |
| `-5002 enter number greater than 0 [OOPR.MaxSumLoc]` | vínculo sem valor | `MaxLocalTotal` = total do documento (ou 1) |
| vínculo aceito mas apontando para o documento errado | `DocumentNumber` recebe o **DocEntry**, não o DocNum | confirmado nos vínculos reais em homologação e no legado |

### Resultado

Executado contra o ambiente real de homologação:

* `00125533` — cotação **atualizada**, snapshot gravado. 1 sucesso, 0 erros.
* `00125531` — cotação **criada** (DocEntry 101892, total 53.722,63),
  **vinculada** à oportunidade 15145, snapshot gravado. 1 sucesso, 0 erros.

**Estado:** 466 testes passando, lint limpo.

### Ponto de atenção aberto

Se a criação do documento der certo e o vínculo falhar, o documento fica criado
e **não vinculado**, e reexecutar não corrige — na execução seguinte a cotação
já existe e a regra passa a ser `espelha_status`. Aconteceu com o `00125535`
durante os testes desta sessão. Falta decidir se a integração deve detectar
vínculos pendentes e completá-los.

---

## Sessão 10 — três ajustes de payload

**1. `U_INO_StatusWBC` não estava sendo espelhado após criar documento.** As
regras que criam ou atualizam documento (`sem_cotacao_cria`,
`emitido_sem_cotacao`, `revisao_sobre_cotacao_emitida`) não trazem
`atualizar_status_oportunidade` entre as ações — no legado o espelhamento vinha
embutido dentro do `AddCotacaoOportunidade`, que grava o status junto com o
vínculo. Traduzindo a máquina de estados sem traduzir esse efeito colateral, a
oportunidade ficava com o status antigo.

Foi observado no ambiente real: a oportunidade 15145 continuou com
`U_INO_StatusWBC = '0'` depois da cotação criada e vinculada. O efeito não é
cosmético — na execução seguinte o SAP ainda diria `'0'`, a decisão voltaria a
ser "criar", e a integração criaria **outra** cotação para o mesmo orçamento, a
cada ciclo.

Agora é um passo explícito (`_espelhar_status_apos_documento`) aplicado após
qualquer ação de documento, com três guardas: não repetir o que a decisão já
pediu, não escrever quando o SAP já reflete o SitCode, e não reabrir status de
oportunidade recém-encerrada.

**2. `U_INO_VERSAOWBC` — revisão normalizada.** Passa a sair sempre em
maiúscula e sem espaços, ou vazia quando não há letra. A normalização ficou na
**leitura** do WBC, não no payload, porque a mesma revisão alimenta
`ordem_revisao()`: um `'b'` minúsculo produziria um número maior que qualquer
maiúscula, fazendo uma revisão antiga parecer mais nova e disparando
cancelamento e recriação de documento.

**3. Peso de 1 kg na cotação — decidido manter.** O `Weight1 = 1.0` vem do
cadastro do item no SAP (`SalesUnitWeight` dos itens `I00000x`), não do nosso
payload; as cotações geradas pelo legado trazem o mesmo valor. O WBC não tem
peso por linha nas tabelas integradas: `INTEGRACAO_ORCIMP` não tem a coluna, e
o `ORCPES` de `INTEGRACAO_ORCPRD` só cobre uma fração dos orçamentos (nenhum
dos pendentes). **Decisão do usuário: manter como está.** Registrado em
`domain/linhas.py` para que ninguém "corrija" isso depois — se o peso precisar
estar certo, o lugar é o cadastro do item no SAP.

**Verificado em homologação:** `00125530` — cotação 101894 criada, vinculada à
oportunidade 15144 e status espelhado para 40. 1 sucesso, 0 erros.

**Estado:** 477 testes passando, lint limpo.

---

## Sessão 11 — `U_INO_ORCAMENTO`: o vínculo do documento com o snapshot

O campo saía vazio nas cotações criadas por esta integração, enquanto as do
legado o traziam preenchido. Ele guarda o **`DocEntry` do snapshot do
OrcDetalhe** (`@INO_ORCAM`) — é o vínculo do documento de volta para o retrato
do orçamento que o originou (`ServiceProcess.cs:1134`, alimentado por
`DocEntryValdixonTable`).

**Isso impôs uma inversão de ordem.** O snapshot era gravado *depois* dos
documentos ("um retrato do que foi aplicado"); agora é gravado **antes**,
porque o seu `DocEntry` precisa existir para entrar no payload. O legado sempre
fez nessa ordem.

Duas propriedades foram preservadas na inversão:

* **O snapshot continua auxiliar.** Se falhar, o documento é criado assim
  mesmo, com o campo omitido — que é o que o legado faz quando não tem o valor.
  Perder o histórico é ruim; perder a cotação é pior.
* **O campo é numérico.** Filtrar por `''` devolve
  `SAP 205 — the given value('') of property 'U_INO_ORCAMENTO' is not a NUMBER`.
  Por isso é **omitido** quando não há valor, nunca enviado vazio ou zerado.

Melhoria sobre o legado: ele recupera o `DocEntry` com
`SELECT max("DocEntry") FROM "@INO_ORCAM"`, sujeito a devolver o registro de
outra execução concorrente. Aqui o valor vem da resposta do próprio POST.

**Verificado em homologação:** `00125523` — snapshot 513893 gravado, cotação
101897 criada com `U_INO_ORCAMENTO = 513893`, vinculada à oportunidade 15137,
status espelhado para 40.

### Duas regras exercitadas pela primeira vez no ambiente real

O histórico do `00125528` registrou, sem intervenção:

1. `sem_cotacao_cria` → cotação 101895 criada com revisão vazia;
2. a revisão do WBC mudou para `A`; na execução seguinte,
   `revisao_mais_nova_recria_cotacao` **cancelou a 101895 e criou a 101896**,
   agora com `U_INO_VERSAOWBC = 'A'`;
3. na terceira execução, `revisao_congelada` — nada a fazer.

É a máquina de estados do SitCode funcionando ponta a ponta contra dados reais,
incluindo o caminho destrutivo (cancelar e recriar), que até então só tinha
teste de unidade. E confirma a normalização da revisão chegando ao SAP.

**Estado:** 482 testes passando, lint limpo.

---

## Sessão 12 — OrcDetalhe: cabeçalho completo e linhas da tabela certa

O snapshot estava saindo pela metade. Comparando um registro nosso (`513893`)
com um do legado (`513880`), 17 campos de cabeçalho vinham vazios e as linhas
tinham forma diferente.

### O cabeçalho

Todos os campos faltantes vinham de `INTEGRACAO_ORCIMP` — tabela que a
integração **já lia**; a consulta apenas não trazia as colunas. Foram
acrescentados 30 campos (`ORCVALVND`, `ORCVALLST`, `ORCVALINV`, `ORCVALLUC`,
`ORCVALTRP`, `ORCPERCOM`, `ORCVALCOM`, `PGTCOD`, `ORCPGT`, `PRZENT`,
`TIPMONCOD`, `TABELA_PRECO`, `ORCIMP_EMAIL`, `ORCIMP_FONE`,
`ORCIMP_TIPO_VENDA`, `ORCIMP_TRANSPORTE`, `ORCIMP_ACABAMENTO`,
`ORCIMP_MONTAGEM`, `CLICON` e os demais), agrupados no modelo
`DadosImpressaoWbc` — têm um destino só, o UDO.

Detalhe que estava errado e não parecia: `U_ORCIMP_REVISAO` recebia a revisão
do **cabeçalho** (`ORCLST.REVISAO`), que é a que dirige a máquina de estados.
A do snapshot é `ORCIMP_REVISAO`, outra coluna. São dois campos distintos no
WBC e o legado usa cada um no seu lugar.

### As linhas vinham da tabela errada

O legado monta as linhas do OrcDetalhe a partir de **`INTEGRACAO_ORCPRDARV`**,
a árvore de produtos — 20 linhas com código, cor, nível, descrição, peso e
preço unitário para um orçamento de dois itens. Só quando não há árvore é que
ele cai para linhas com **apenas sequência e texto**.

Esta implementação usava sempre o segundo caminho, e ainda gravava nele
quantidade, preço e total — o que fazia o registro *parecer* detalhado sem ser
(`ORCPRDQTD` é nula em todas as linhas e `ORCVAL` é o total, não o unitário).
Agora os dois caminhos existem, e o segundo grava só o que o legado grava.

A consulta da árvore reproduz três coisas não óbvias do `GetTableValdixson`:
resolver o código do produto por descrição contra `INTEGRACAO_ORCPRD`,
sobrescrever o total pelo de `ORCPRD` quando casa `(ORCNUM, PRDDSC, ORCQTD)`, e
zerar o `ORCITM` quando o produto aparece lá com `ORCITM = 0`. Duas melhorias
deliberadas: o `TOP 1` sem `ORDER BY` do legado virou `MAX` (determinístico), e
a consulta por linha (`getDocTot` dentro do laço) virou subconsulta, eliminando
o N+1 num orçamento com dezenas de linhas.

### `[CR]` e `[TAB]`

Os campos longos do WBC trazem esses marcadores **como texto literal**. Sem
removê-los, o acabamento chegava ao SAP como
`"[CR][CR][CR][CR]Cinza Padrão Altamira..."`. O legado os troca por espaço na
própria consulta; aqui é feito na leitura, para valer em qualquer consulta.

### Validação em homologação — confirmada

O Service Layer voltou e o ciclo rodou. Comparando o snapshot `513896` (nosso,
`00125476`) com o `513880` (legado, **mesmo orçamento**):

* **37 de 39 campos idênticos**;
* **20 linhas de árvore, zero divergências** — `ORCITM`, `CODIGO`, `PROD`,
  `COR`, `NIVEL`, `Qtde`, `PESO`, `PRECO` e `TOTAL` batem em todas;
* as duas diferenças são explicadas e corretas:
  `U_INO_DATA` (26/08 contra 24/08 — é a data da captura, e as execuções foram
  em dias diferentes) e `U_ORCIMP_NEGOCIACAO` (`"0.0000"` contra `None` — o
  valor no WBC é zero, e gravá-lo explícito é mais honesto que deixar nulo).

Também foi corrigido, nessa verificação, o **significado de `U_INO_DATA`**: era
gravada a data do *orçamento*, quando o legado grava a data da *captura*. Como
o OrcDetalhe é append-only, o mesmo orçamento gera vários registros ao longo do
tempo e a data é o que os distingue — gravar a do orçamento deixava todos os
retratos com a mesma data.

Rodado também no `00125519` (caminho sem árvore): snapshot `513894` criado,
cotação 101866 atualizada, status espelhado para 40.

### Nota sobre a verificação anterior

O Service Layer esteve fora durante parte da verificação
(`SAP 312 — Fail to connect to SLD`, serviço do servidor), então a conferência
foi feita montando o payload a partir do WBC real e comparando com o registro
`513880` do legado: **todos os campos batem**, e a primeira linha da árvore sai
idêntica (`PPLLOZCJ087000142300`, `LONGARINA Z87 CH14 MED. 2300 MM`, `LA-LB`,
nível 1, qtd 20, peso 165,27, preço 121,29). Os 7 campos que continuam vazios
(`U_CLICOD`, `U_CLICONCOD`, `U_ORCBAS1/2/3`, `U_ORCVALEXP`, `U_ORCVALMON`)
também estão vazios no registro do legado.

**Estado:** 516 testes passando, lint limpo.

---

## Sessão 13 — o SitCode vem de `INTEGRACAO_ORCSIT`

Informação do usuário: a situação real da proposta está em
`INTEGRACAO_ORCSIT`, não em `INTEGRACAO_ORCLST.SITCOD`. **O sistema legado não
conhece essa tabela** — não há uma única referência a ela no C#. Ou seja, é
correção sobre o legado, não reprodução dele.

Como o SitCode dirige toda a máquina de estados — inclusive o encerramento da
oportunidade —, a troca foi medida antes de ser feita:

| medida | resultado |
|---|---|
| divergência entre as duas tabelas (base inteira) | 8.560 de 59.265 — **14%** |
| divergência na janela recente (2026) | 4 de 2.019 — 0,2% |
| orçamentos sem linha em `ORCSIT` | 1.660 |
| **decisões que mudariam nos 20 orçamentos da janela de integração** | **0** |

A troca é segura hoje e corrige um erro latente: 14% da base está com situação
diferente da real.

Três cuidados na implementação:

1. **Vale a linha mais recente.** `ORCSIT` guarda uma linha por mudança de
   situação. Critério: maior `ORCALTDTH` e, entre elas, maior
   `idIntegracao_OrcSit` — há um registro de 2017 com duas linhas idênticas, e
   sem desempate determinístico o resultado ficaria a critério do plano de
   execução do banco.
2. **`ORCLST.SITCOD` fica como reserva.** Os 1.660 orçamentos sem linha em
   `ORCSIT` precisam de um valor; cair para zero os faria parecer "abaixo do
   mínimo" e a integração os ignoraria em silêncio.
3. **As duas consultas usam a mesma fonte.** `situacao_atual` e a consulta
   completa precisam concordar, senão a decisão mudaria conforme o caminho que
   carregou o dado.

**Estado:** 521 testes passando, lint limpo.

---

## Sessão 14 — UDFs da cotação que vinham da impressão

Quatro campos da cotação estavam vazios em relação ao legado. Todos têm a mesma
origem — `INTEGRACAO_ORCIMP`, que a solução **já lia** para montar o OrcDetalhe.
Não faltava dado: faltava escrevê-lo no documento.

| UDF | Origem no C# | Origem no WBC |
|---|---|---|
| `U_INO_PrazoEntrega` | `ServiceProcess.cs:1177` | `PRZENT` |
| `U_INO_ValorTransp` | `:1178` | `ORCVALTRP` |
| `U_INO_ValorEmbalagem` | `:1179` | `ORCVALEMB` |
| `U_INO_ACAB` (por linha) | `:1217`, `:1231`, `:1435`, `:1449` | `ORCIMP_ACABAMENTO` |

**Formato conferido, não deduzido.** Três cotações puramente legadas em
homologação (101875, 101879, 101881) mostram os três campos de cabeçalho como
**texto**: o prazo como inteiro puro (`'42'`) e os dois valores com quatro casas
(`'0.0000'`, `'113026.8800'`) — o mesmo formato de `formatar_para_sap`.

`U_INO_ACAB` é de cabeçalho na origem e **de linha no destino**: o legado repete
o mesmo texto em todas as linhas do documento, e as cotações legadas confirmam.

### `LineNum` não é da integração

O pedido incluía "`LineNum` está vindo 0 e deveria ser 1". Não há nenhuma
atribuição de `LineNum` em todo o `ServiceProcess.cs` — quem numera é o SAP, e a
numeração é **base zero**. As cotações do legado provam: 101881 começa em 0,
101875 começa em 0 e 101879 começa em 4. Forçar 1 aqui criaria uma divergência
que o legado nunca teve.

**Validado em homologação:** cotação 101901 (orçamento `00125532`) nasceu com
`U_INO_PrazoEntrega='38'`, `U_INO_ValorTransp='0.0000'`,
`U_INO_ValorEmbalagem='0.0000'`, `U_INO_ORCAMENTO=513898` e o acabamento
repetido nas seis linhas.

**Estado:** 525 testes passando, lint limpo.

---

## Sessão 15 — o resto dos UDFs de cabeçalho

`U_INO_Montagem` estava faltando — e, ao procurá-lo, apareceu que não era o
único. A varredura completa (`grep -o 'DocCot.UserFields...'` sobre o
`ServiceProcess.cs`) mostrou **dois conjuntos distintos**, um por tipo de
documento, e sete campos que nunca chegavam ao SAP.

### Cotação (`:1173-1189` ao criar, `:1374-1388` ao atualizar)

`U_INO_PessoaContato` (`CLICON`), `U_INO_CondPag` (`PGTCOD`, com `|` e `;`
virando quebra de linha e corte em 254), `U_INO_PrazoEntrega`,
`U_INO_ValorTransp`, `U_INO_ValorEmbalagem`, `U_INO_VL_MT` (`ORCVALMON`),
`U_INO_TIPO_MT` (`TIPMONCOD`) e `U_INO_Montagem`/`U_INO_Montagem2`
(`ORCIMP_MONTAGEM`, partido em 200).

### Pedido (`:338-344`)

`U_INO_TIPO_MT`, `U_INO_VL_MT`, `U_INO_COM` (`ORCPERCOM`) e `U_INO_VL_COM`
(`ORCVALCOM`). O pedido **não** leva prazo, transporte, embalagem, contato,
condição de pagamento nem montagem — nada disso está no bloco do pedido.

### A divergência que foi preservada

`U_INO_VL_MT` sai de `ORCVALMON` na cotação e de **`ORCBAS3`** no pedido
(`:281`). São colunas diferentes do mesmo registro. Não há como saber se é
intenção ou descuido de quinze anos atrás, então os dois comportamentos ficaram
como estão e a pergunta foi para `RETOMADA.md`.

### Os sete que não existem

`U_INO_UpdateDate`, `U_INO_PctCom`, `U_INO_VlrCom`, `U_INO_Retorno`,
`U_INO_Negociacao`, `U_INO_IndVend` e `U_INO_Embalagem` aparecem **quatro
vezes** no C# — e nas quatro estão dentro de comentários (`:347-353`,
`:588-594`, `:1138-1144`, `:1354-1360`). O legado nunca os gravou. Há teste
fixando a ausência, para que não sejam "consertados" por engano.

### Tipo e tamanho, conferidos no `UserFieldsMD`

Foi a parte que quase passou batido. `U_INO_VL_MT` e `U_INO_VL_COM` são
`db_Float` — mandar texto neles é o mesmo erro do `U_INO_ORCAMENTO`, só que
mais silencioso. E os campos de texto têm tamanhos apertados:

| Campo | Tipo | Tamanho |
|---|---|---|
| `U_INO_VL_MT`, `U_INO_VL_COM` | `db_Float` | — |
| `U_INO_PessoaContato` | `db_Alpha` | 20 |
| `U_INO_COM`, `U_INO_PrazoEntrega`, `U_INO_ValorEmbalagem` | `db_Alpha` | 10 |
| `U_INO_ValorTransp` | `db_Alpha` | 20 |
| `U_INO_Montagem` | `db_Alpha` | 200 |
| `U_INO_Montagem2` | `db_Memo` | — |
| `U_INO_CondPag` | `db_Alpha` | 254 |

`U_INO_ValorEmbalagem` com 10 caracteres é uma bomba-relógio: uma embalagem de
R$ 100.000,00 daria `'100000.0000'`, com 11, e o SAP recusaria a **cotação
inteira** por causa de um campo informativo. `_valor_texto` corta as casas
decimais até caber — perde-se precisão de exibição, nunca o documento.

**Validado em homologação:** cotação 101884 (orçamento `00125533`) atualizada
com contato, condição de pagamento, prazo 42, transporte `1023.3600`,
`U_INO_VL_MT` numérico e a montagem completa.

**Estado:** 541 testes passando, lint limpo.

---

## Sessão 16 — PROD contra HOMOLOG, campo a campo

Comparar a view `VW_ORCAMENTO_IMPRESSAO` nos dois ambientes para o mesmo
orçamento (`00125533`) apontou três diferenças. Investigar as três levou a um
defeito bem maior, que não estava na lista.

### 1. `U_INO_Composicao` e `U_INO_Id_IntWBC` não são da cotação

A view mostrava `U_INO_Composicao` preenchido em homologação e **nulo** em
produção. A conferência nas tabelas fechou a questão:

| | linhas | `Composicao` vazia | `ACAB` vazio | `Id_IntWBC` vazio |
|---|---|---|---|---|
| Cotação (`QUT1`) | 579.088 | **579.088** | 2.443 | 578.817 |
| Pedido (`RDR1`) | 16.242 | 154 | **16.236** | 76 |

Os dois documentos levam conjuntos diferentes de UDFs de linha, e o C# confirma:
`ServiceProcess.cs:378-395` (pedido) contra `:1214-1231` (cotação). Estávamos
mandando tudo em todo documento.

Não era inofensivo: `U_INO_Composicao` **aparece na view de impressão**.
Preenchê-lo na cotação mudaria o que o cliente recebe, sem ninguém ter pedido.

### 2. O pedido perdia `DocDueDate` e `Comments`

Nenhum dos dois é UDF, e por isso passaram batido nas varreduras anteriores.
Nos 4.432 pedidos do WBC em produção, `DocDueDate - DocDate` é **sempre** o
prazo de entrega, e `Comments` (a condição de pagamento) está preenchido em
4.328.

### 3. `U_INO_COM` sai sem casas decimais

Produção grava `'3'`, `'4'`, `'4.5'`. Estávamos formatando com quatro casas,
como nos campos de valor — mas este é outro campo, com outro formato.

### 4. O defeito grande: `PATCH` não substituía as linhas

Ao validar o pedido novo em homologação, a cotação 101857 (orçamento
`00125527`) apareceu com **três** linhas e R$ 69.656,20, para um orçamento de
duas linhas e R$ 52.079,03. A linha sobrando era de uma revisão anterior.

O `PATCH` do Service Layer **mescla** coleções; não troca. Linhas enviadas sem
`LineNum` são acrescentadas, e as antigas ficam. A docstring do
`atualizar` afirmava o contrário — estava simplesmente errada.

A correção é o cabeçalho `B1S-ReplaceCollectionsOnPatch: true`, que o Service
Layer expõe para exatamente isso. Validado na cotação 101872 (`00125524`), que
tinha uma linha vazia de R$ 0,00 e passou a ter a linha certa do orçamento, com
total de R$ 7.098,64.

Este é o tipo de defeito que não aparece em teste: precisa de um documento com
histórico. Passou perto de ir para produção somando valor errado em cotação de
cliente.

### O que ficou igual

Tudo o mais bate: contato, condição de pagamento, prazo, transporte, embalagem,
montagem, tipo de montagem, `U_INO_ORCAMENTO`, item, depósito, quantidade,
preço, `DocTotal`, `VisOrder`. As duas diferenças restantes na view são
esperadas: `U_INO_DATA` (data da captura, e as capturas foram em dias
diferentes) e o espaço em branco em volta do `U_INO_ACAB` (produção grava com
espaços à esquerda; nós limpamos).

### Um alerta sobre o binário de produção

`Comments` em produção é o `PGTCOD` **cru**. O código-fonte que temos corta o
texto em palavras e aplica `Distinct()` (`:332-334`), o que teria comido o "da"
repetido em "da mercadoria"/"da emissão" — e não comeu, no pedido 19500 e nos
demais conferidos. Ou seja: **o binário que roda em produção não é este
código-fonte**. Isso reforça a pergunta nº 1 de `RETOMADA.md` (o filtro fixo
`= '00121819'`) e vale para qualquer decisão futura tomada só pela leitura do C#.

**Estado:** 549 testes passando, lint limpo.

---

## Sessão 17 — o worker lê no HANA, e a janela inteira passa a ser vista

O ciclo enxergava 20 oportunidades de uma janela com 1.785. A causa está na
sessão anterior; esta troca a fonte de leitura.

### O que mudou

**Antes**, por ciclo: ~90 requisições paginadas ao Service Layer para listar,
mais quatro por orçamento só para responder "já tem cotação? qual revisão?"
(`existe` e `revisao_aplicada` chamam `buscar`, para cada tipo de documento).
Cerca de 900 idas ao SAP num ciclo de 200.

**Agora**: uma consulta no HANA — `OOPR` com `OQUT` e `ORDR` juntados por
`U_INO_COTWBC`, escolhendo o mais recente não cancelado com `ROW_NUMBER` sobre
`DocEntry desc`, que é o mesmo critério do Service Layer.

Medido em homologação: **1.785 oportunidades em 0,07 s**, com **zero
divergências** contra o Service Layer em 40 amostras — incluindo casos sem
documento, com cotação, com pedido e com revisão aplicada.

### O gargalo mudou de lugar — e foi tratado

Com o SAP barato, o custo virou o WBC: `situacao_atual` a 29 ms e
`buscar_orcamento` a 279 ms, vezes 1.785, dariam **8,3 minutos por ciclo** —
mais que o intervalo de 300 s.

Duas medidas:

* `situacoes_atuais(orcnums)`: uma consulta para a janela inteira, em lotes de
  mil. O texto é idêntico ao da consulta unitária (há teste comparando letra a
  letra), porque as duas **precisam** concordar sobre o SitCode.
* O processador decide pela situação e **só carrega o orçamento completo quando
  há ação** — e então refaz estado e decisão sobre ele, para o documento nascer
  do mesmo retrato que autorizou a ação.

Resultado: **1.785 avaliadas em 1m21s**.

### O que apareceu quando a janela ficou visível

| Regra | Qtd |
|---|---|
| `encerramento+espelha_status` | 78 |
| `cria_pedido+espelha_status` | 16 |
| `sem_cotacao_cria` | 14 |
| `revisao_sobre_cotacao_emitida` | 14 |
| `atualiza_pedido+espelha_status` | 14 |
| `revisao_mais_nova_recria_cotacao` | 7 |
| `emitido_sem_cotacao` | 3 |
| `emitido_apos_revisao_no_sap` | 2 |

**148 escritas represadas**, contra 1 que o ciclo enxergava. Nenhum aviso de
grupo fora do de-para. Com o teto de 200 escritas, um único ciclo dá conta.

### Decisões tomadas com o usuário

* **Sem view.** O SQL fica versionado no repositório — e o usuário do HANA só
  tem `SELECT`, então não poderia criar view de qualquer forma.
* **Documentos no mesmo `SELECT`**, em vez de perguntar ao SAP por orçamento.
* **Sem queda para o Service Layer** se o HANA falhar: o ciclo falha e registra
  o erro. Um fallback silencioso traria de volta o corte em 20 com a integração
  parecendo saudável.
* **O teto passou a limitar escrita**, não leitura (`WBC_LIMITE_DE_ESCRITA_POR_CICLO`).

### A armadilha que o código evita explicitamente

O schema da consulta é o da **company que recebe a escrita** (`SL_COMPANY_DB`),
nunca o `HANA_SCHEMA` — que aponta para produção mesmo em homologação, porque é
lá que vivem as views de relatório. Ler o estado de uma company e escrever
noutra criaria documentos em homologação com base no que produção já tem, sem
nenhum sintoma visível. Há teste fixando essa escolha.

A escrita continua toda pelo Service Layer.

**Estado:** 585 testes passando, lint limpo.

---

## Sessão 18 — cancelamento da cotação no encerramento

Defeito relatado pelo usuário: oportunidade cancelada, cotação seguia aberta
(orçamento `00124744`, cotação `68438`).

A investigação mostrou que **não é regressão do porte** — o legado nunca
cancelou documento no encerramento, e 96% das oportunidades encerradas em
produção estão nesse estado. A correção é, portanto, uma divergência deliberada
do legado, registrada em `DECISOES.md`.

* Nova ação `Acao.CANCELAR_COTACAO_NO_ENCERRAMENTO`, decidida em
  `_decidir_encerramento` quando há cotação e **não** há pedido.
* Executada antes de `MARCAR_OPORTUNIDADE_PERDIDA` (que na época se chamava
  `FECHAR_CANCELAR_OPORTUNIDADE`); recusa do SAP vira aviso
  no tracking e não derruba o ciclo.
* Entra em `ACOES_DE_ESCRITA` (conta no teto por ciclo) e na lista do
  `pendentes`.

Impacto na janela de homologação: **91** oportunidades encerradas, **83**
cotações abertas, **0** pedidos — as 83 cabem numa execução do teto de 200.

**Estado:** 597 testes passando, lint limpo.

---

## Sessão 18 (cont.) — o que a primeira execução real revelou

O ciclo em homologação sobre o 00124619 quebrou no encerramento e destravou três
defeitos, dois deles anteriores a esta sessão. Todos em `DECISOES.md`:

1. **`sos_Lost` não existe** no `BoSoOsStatus` (`sos_Open`/`sos_Missed`/`sos_Sold`).
   Nenhum encerramento jamais funcionou. O teste afirmava o valor errado.
2. **O ramo "sem cotação → cria" ficou alcançável** ao passarmos a cancelar a
   cotação. Encerramento agora é avaliado antes dele.
3. **`U_INO_StatusWBC` não é prova de encerramento** — é espelhado a cada ciclo.
   A prova é `OOPR."Status"`.
4. **A leitura do HANA era posicional** e uma coluna nova no meio do `SELECT`
   deslocou tudo em silêncio. Agora é por nome, com dois testes cobrindo.

Verificado no 00124619: cotação 89231 cancelada, oportunidade 13853 com
`Status='L'`, e a execução seguinte não escreve nada — a decisão converge.

**Estado:** 603 testes passando, lint limpo. Nuvem `9f741ed`, máquina `d3fb18b`.

### Próximo passo

Rodar `wbcpython pendentes` para a lista atualizada (a contagem de 41 é anterior
às correções) e depois o `ciclo` sem `--orcamento`. O teto de 200 comporta.

---

## 01/09/2026 — Peso do pedido, e o relatório de riscos da virada

### Peso das linhas do pedido (`Weight1`)

O usuário pediu o peso correto nas linhas do pedido, com a regra: somar `ORCPES`
de `INTEGRACAO_ORCPRDARV` por `ORCITM`. Investigar antes de codificar mudou a
regra três vezes — e o legado já respondia tudo, num trecho difícil de achar
(`GetPesoPedido` em `Querys.resx`, `ServiceProcess.cs:640`):

1. **Só o nível 1.** A árvore é uma estrutura de produto e cada nível repõe a
   mesma massa decomposta. Somar tudo dá 1.818,70 kg no lugar de 760,65 no
   `00124853`. O legado filtra `U_INO_NIVEL = 1`.
2. **Peso unitário** — o SAP multiplica pela quantidade.
3. **Só no pedido** — na cotação a linha do legado está comentada.

E aí, rodando o comando novo contra um pedido real, o número não bateu: o pedido
84112 tem `Weight1 = 836`, a árvore soma 760,65. Medindo 1.271 linhas de pedido
de 2026 em produção: razão mediana **1,099**, 1.056 de 1.061 são inteiros
redondos, e o melhor fator varrendo 1,000–1,300 é exatamente **1,100**. O campo é
peso de **embarque**. `floor(760,65 × 1,10) = 836` — na unidade.

A reprodução não é exata e não pode ser: só 36,8% batem com a fórmula. O campo é
preenchido à mão em produção. O que a regra garante é ordem de grandeza e
critério único, no lugar de 1 kg em 127 linhas e 0 em 64.

Comando novo: `wbcpython pesos --pedido N [--simular]`, que corrige um pedido já
criado sem refazê-lo — `PATCH` **sem** `ReplaceCollectionsOnPatch`, casando as
linhas por `U_INO_ORCITM`.

### Relatório de riscos de produção

`RISCOS_PRODUCAO.md`, com todo número medido contra `SBOALTAMIRAPROD` em leitura.
Os dois achados que reordenam a lista:

**O legado está rodando em produção hoje** — 26 cotações em 01/09/2026, 20 a 30
por dia útil em agosto. Ligar o novo sem desligar o velho põe dois processos
escrevendo nos mesmos documentos com a mesma chave.

**O primeiro ciclo (janela de 3 meses) cancelaria 27 cotações** (R$ 10.363,82) e
**criaria 4 pedidos** (R$ 170.973,41) — os dois efeitos irreversíveis. Com 6
meses seriam 79 cancelamentos; com 9, 96.

O documento traz ainda: o banco de acompanhamento sendo o mesmo arquivo nos dois
ambientes, as três variáveis que precisam mudar juntas, o TLS sem validação, as
cinco divergências deliberadas em relação ao legado, e um roteiro de virada em
oito passos.

### Virada para produção (worker ainda parado)

Decisão do usuário. `SL_COMPANY_DB` passou a `SBOALTAMIRAPROD` — o
`HANA_SCHEMA` já apontava para lá, então a configuração estava **meio virada**:
lia produção e escrevia em homologação, o cenário que `DECISOES.md` diz que não
pode existir. Todas as prévias desta sessão estavam lendo produção.

As duas recusas do `cli.py` foram removidas (worker e `env`), substituídas por
aviso na tela e no log. A trava `WBC_BLOCK_PRODUCTION_WRITES` já estava `false`
no `.env` — a proteção que o usuário pediu para remover já estava removida; o
que segurava era o `SL_COMPANY_DB`. Log zerado.

**O worker não foi iniciado.** O legado continuava escrevendo em produção na
data da virada: 25 cotações e 4 pedidos em 02/09/2026, e todo dia útil das duas
semanas anteriores. O usuário desliga o legado primeiro; dois processos gravando
pela mesma chave criam documento duplicado, e a trava de execução única não vê o
legado.

Raio medido para o primeiro ciclo, contra produção, com as guardas novas: 1.635
oportunidades na janela de 6 meses, **99 escritas** — 79 cancelamentos de cotação
com encerramento de oportunidade, 8 criações de pedido, 12 espelhamentos de
status.

Logo após a virada, três testes de configuração ficaram vermelhos na máquina do
usuário sem nenhuma mudança de código: `Settings(_env_file=None)` isolava só o
topo, e as configurações aninhadas continuavam lendo o `.env` do diretório. O
vazamento existia desde sempre e era invisível porque o valor que vazava
(homologação) coincidia com o esperado. Corrigido no `config.py`, com teste de
regressão.

**Estado:** 857 testes passando, lint limpo.

### Revisão das duas guardas do pedido, e três correções

Revisão do próprio código dos commits anteriores, a pedido do usuário. Três
defeitos reais, verificados rodando o código antes de reportar:

1. **`esta_fechado` falhava aberta** (🔴): `DocStatus` vazio, nulo ou
   inesperado devolvia `False`, e `False` faz o ciclo escrever. Era o oposto do
   `safety.py`, e a mesma classe de falha que originou a guarda — uma coluna que
   não era lida. Agora congela e avisa; a política ficou num lugar só
   (`esta_fechado_pelo_status`), que é também o único ponto que conhece os dois
   vocabulários (`bost_Close` e `'C'`).
2. **`pedido_corrigido_a_mao` devolvia `True` sem pedido nenhum** (🟠): as duas
   propriedades irmãs verificavam `tem_pedido`, só ela não. Não causava defeito
   por causa da ordem dos `if`, o que é ordem de statement e não invariante.
3. **Troca de PN sobre pedido fechado congelava em silêncio** (🟠): pedido de
   negócio impossível de atender, indistinguível dos 243 congelamentos benignos.
   Agora tem regra própria e motivo escrito.

Mais três menores: o alias `'c'` na implementação do Service Layer contradizia o
próprio docstring; o docstring do `documentos_lidos` dizia "quatro requisições"
quando já eram seis; e o `COT_STATUS` foi documentado como dado
pré-posicionado — `esta_fechado(tipo, ...)` já responde sobre a cotação, a regra
é que ainda não pergunta.

As duas guardas de congelamento passaram a registrar **motivo**, que antes
faltava: o operador via a regra sem explicação, na tela que existe para explicar.

Três testes afirmavam o comportamento antigo e foram reescritos.

**Estado:** 853 testes passando, lint limpo.

### Pedido fechado no SAP deixou de ser alterado e cancelado

Reportado pelo usuário no orçamento `00124268`: o script tentou salvar alteração
num pedido já fechado.

O `DocStatus` **não era lido em lugar nenhum** — nem na consulta do HANA, nem no
Service Layer. Não havia código errado; havia código ausente. A integração caía
em `atualiza_pedido` sempre que a revisão do WBC fosse mais nova, e o SAP
recusava.

Ao medir, o tamanho surpreendeu: **2.452 dos 2.568** pedidos vigentes em
produção estão fechados. É o estado normal de um pedido entregue ou faturado — a
guarda trata a maioria, não um caso de borda.

- `DocStatus` entrou na consulta do HANA (pedido e cotação) e virou
  `esta_fechado` no **protocolo** de documentos, com uma implementação por
  fonte: no Service Layer o campo é `DocumentStatus` (`bost_Open`/`bost_Close`),
  na tabela é `DocStatus` (`'O'`/`'C'`). Valores conferidos contra o SAP real no
  pedido `83988`, não deduzidos.
- `EstadoIntegracao.pedido_fechado_no_sap` congela o pedido no início do ramo,
  **antes** de `pedido_corrigido_a_mao`: as outras guardas são regra de negócio,
  esta é o SAP recusando a escrita, e quem investiga precisa ver a razão certa.
- "Fechado" não é "inexistente": `tem_pedido` continua valendo, e sem pedido a
  marca não impede a criação do primeiro.

**Em aberto:** existem **3 cotações fechadas** em cada ambiente, com o mesmo
defeito. Não foram tratadas porque o pedido era sobre o pedido; o dado já viaja
na consulta.

**Estado:** 845 testes passando, lint limpo.

### Pedido corrigido à mão deixou de ser tocado pelo ciclo

Reportado pelo usuário no orçamento `00124045`: o script tentava atualizar o PN
do pedido de forma errada. Regra do negócio: `U_INO_Update = 'N'` com
`U_INO_PN_Correc` preenchido significa que o pedido **já está correto em nome de
outro PN**, e nada deve ser executado sobre ele.

O dano vinha por um caminho que a leitura do código não sugeria. A correção do
`00124045` já estava aplicada — pedido vigente `84022` em `C011608`, o antigo
`83949` cancelado —, então `troca_de_parceiro_pendente` era **falsa**, o fluxo
caía na comparação de revisão e chamava `atualizar_pedido`. Como `campos_comuns`
envia `CardCode` sempre e `parceiro_do_pedido` devolve o parceiro da
*oportunidade* fora da troca, o PATCH desfazia a correção. Por isso a guarda
entra no início do ramo do pedido, e não dentro do ramo da troca.

Medido em produção (somente leitura): 592 oportunidades têm a combinação; das
que a regra antiga ainda consideraria com troca pendente, **todas as 4** são
casos de 2024–2025 com o pedido vivo num terceiro parceiro, resolvidos à mão.
Investigá-las revelou que o `00117039` tem **7 pedidos cancelados** no
`PN_Correc`, todos do mesmo dia — o cancelar-e-recriar do legado em looping
(`DEFEITOS_LEGADO.md`).

Escopo escolhido pelo usuário: congela o pedido, não a oportunidade. O
espelhamento de status continua, porque não toca no documento nem no `CardCode`.

O dano evitado foi medido rodando a prévia sobre a janela inteira (1.540
oportunidades) com as duas regras: `atualiza_pedido` cai de **6 para 3**, e são
esses 3 que teriam o `CardCode` revertido na próxima passada. `troca_de_pn` fica
em **zero nas duas** — nenhuma troca legítima foi perdida. 64 orçamentos passam
a cair em `pedido_corrigido_a_mao`.

Cinco testes afirmavam o comportamento antigo e foram reescritos para a regra
nova, com o motivo no docstring. Conferido no ambiente real:
`pendentes --orcamento 00124045` agora dá `regra: pedido_corrigido_a_mao`,
`ações: (nenhuma)`.

**Em aberto:** a troca de PN passa a exigir `U_INO_Update = 'Y'` — um UDF da
oportunidade no SAP (`SalesOpportunities`/`OOPR`), não um campo do WBC. Se nada
marcar esse campo ao preencher o `PN_Correc`, nenhuma troca futura acontece —
pergunta para a equipe do WBC.

**Estado:** 827 testes passando, lint limpo.

### Indicadores do topo viraram filtros da lista

Pedido do usuário: clicar em "Com erro" tem de mostrar os orçamentos com erro.

Cada indicador virou um `Recorte` (`dashboard/dados.py`) — avaliados, com ação,
sem ação, encerradas, com erro, com cotação, com pedido — e o recorte é a
**mesma definição** usada para contar, reaproveitada para listar. "Com ação"
usa a constante `STATUS_COM_ACAO` que os KPIs já usavam, em vez de repetir a
lista de status.

O filtro é aplicado em Python sobre as mesmas linhas que os indicadores
contaram, e não numa consulta separada: é o que garante que o indicador dizendo
8 abra uma lista de 8. Verificado no navegador nos seis indicadores — todos
batem — e há teste comparando, para cada recorte, o valor exibido com o tamanho
da lista.

- `<button>` com `aria-pressed`, não `div` clicável: acionável pelo teclado.
- O recorte vive no endereço (`/?aba=oportunidades&recorte=com_erro`), o que
  também mantém o indicador marcado quando o topo repinta aos 30 s.
- Recorte desconhecido cai em "Avaliados" em vez de estourar.
- A lista mostra o recorte ativo como etiqueta removível, e o "nada encontrado"
  nomeia o recorte.

**Estado:** 823 testes passando, lint limpo.

### Aba "Executar": os comandos da CLI no painel

Pedido do usuário. A aba roda a **CLI de verdade** em subprocesso
(`python -m wbcpython ...`) e mostra a saída ao vivo, repintando a cada segundo
enquanto o comando roda.

- **Leitura e diagnóstico**, sem proteção: `pendentes` (com `--exportar` ligado
  por padrão, atualizando a aba "Próximo ciclo"), `env`, `doctor`, `check-sap`,
  `check-hana`.
- **Escrita**, com `PAINEL_SENHA` do `.env` **e** nome de quem executa:
  `ciclo` (janela inteira, um orçamento ou só os cancelados), `pesos` (prévia
  obrigatória do mesmo pedido antes de aplicar) e `datas-de-abertura`.
- **Em produção nada que escreva fica disponível** — a tela diz qual guarda
  fechou a porta, e a leitura continua liberada.

A decisão de que "o painel não escreve no SAP" continua valendo no que
importava: quem escreve é a CLI, em processo próprio, e `executar_ciclo` já toma
a **trava de execução única**, que vale entre processos — painel e worker
disputam a mesma. `terminate` e não `kill` ao interromper, para a trava ser
liberada.

Três guardas que valem registro: `PAINEL_SENHA` vazia **desabilita** a escrita
(senão `"" == ""` liberaria a instalação mal configurada); a comparação é
`secrets.compare_digest`; e a lista de argumentos sai do catálogo, nunca do
formulário — sem shell, um campo de texto não vira comando na máquina.

36 testes novos, quase todos sobre **o que a tela recusa**.

**Estado:** 806 testes passando, lint limpo.

### Painel reescrito em HTMX, no lugar do Streamlit

Pedido do usuário. O painel virou um monitor — fica aberto enquanto o worker
roda sozinho —, e o Streamlit repinta a página inteira a cada interação: o log
pisca, a rolagem volta ao topo e o campo de filtro se recria embaixo de quem
está digitando.

Agora é HTML servido por FastAPI + Jinja2, com HTMX pedindo fragmentos. Cada
bloco se atualiza no seu ritmo (log a cada 5 s, números a cada 30 s) e troca só
o pedaço que mudou. O acompanhamento do log é pausável.

- `dashboard/web.py` — a casca (`GET /`) e sete rotas de fragmento.
- `dashboard/templates/` — Jinja2; `dashboard/static/` — CSS e o
  `htmx.min.js` **versionado** (0BSD): a máquina do worker não tem internet.
- `dashboard/app.py` (Streamlit) removido; o extra `dashboard` trocou
  `streamlit`+`pandas` por `fastapi`+`uvicorn`+`jinja2`+`python-multipart`.
- `wbcpython dashboard` ganhou `--host` e `--porta`, e escuta em `127.0.0.1`
  por padrão: o painel não tem autenticação.
- Sem CDN, sem fonte de web e sem biblioteca de gráfico — barras em CSS.
- 25 testes novos com o `TestClient` (o HTML de verdade, sem navegador), no
  lugar dos 8 que subiam o runtime do Streamlit.

Dois defeitos que só apareceram ao abrir a página: as barras saíam vazias
(`<span>` ignora `height`) e os dois gráficos mostravam os mesmos dados
(`{% with %}` não propaga escopo para dentro de um `include`). Ambos em
`APRENDIZADOS.md`.

**Estado:** 770 testes passando, lint limpo.

### Antes disso, no mesmo dia

- **Painel** com a prévia do ciclo, e o **log em arquivo** que
  aparece na tela do comando e na aba "Log" do painel — uma fonte, duas telas.
- **Painel passou a respeitar a janela do worker**: o acompanhamento nunca
  esquecia, e 167 dos 886 orçamentos eram de maio, de quando a janela era 6
  meses. Coluna `data_abertura` + `wbcpython datas-de-abertura`.
- **Resumo do ciclo separa avaliado de com ação** — contava 719 como
  "processados com sucesso" quando o ciclo não tocara em nenhum.
- **Espelhamento do SitCode só quando o SAP discorda**: eram 160 ações, 154
  regravando o mesmo valor. Caiu para 8.

**Estado:** 745 testes passando, lint limpo. Nuvem e máquina em `633075f`.

### Próximo passo

Decidir a janela (`MESES_DE_JANELA`) — é ela que define 33, 104 ou 149 escritas
no primeiro ciclo. Depois, os passos 2 a 8 do roteiro em `RISCOS_PRODUCAO.md`.

---
