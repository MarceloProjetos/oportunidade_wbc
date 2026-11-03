# Plano — docstrings e type hints em inglês (ServidorIntegracaoSAP)

> **Status (03/11/2026):** **E1 e E2 concluídas** (25 arquivos; 38 docstrings desatualizados
> corrigidos); E3–E8 abertas.
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
  - rotas FastAPI (`router.py`, `api_router.py`): o docstring vira a descrição do OpenAPI que a
    equipe do PCP lê.
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
| **E3** | Regra do CP — Manutenção de OP | 7 | 17 | 8 | aberta |
| **E4** | Worker WBC — `domain/`, `application/`, `host/` | 19 | 125 | 0 | aberta |
| **E5** | Worker WBC — `infrastructure/`, `tracking/` | 24 | 115 | 0 | aberta |
| **E6** | API 8077 e extrações (`api.py`, `pipeline_core`, `situacao_pedidos_hana`, `extract_*`, `monitoring`) | 14 | 94 | 90 | aberta |
| **E7** | Telas, CLI, MCP, `operacao/`, `seguranca/`, `casa/` (só o que não é texto de tela) | 45 | 170 | 95 | aberta |
| **E8** | Arquivos-irmãos, junto com SAP_RDP e web | 8 | 21 | 7 | aberta (opcional) |

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
**diz ao operador** que pedido integrado é "reprocessável com `--force`" — e `--force` duplica OP.
É texto de tela (código), não docstring.

## Decisões

1. ✅ **Traduzir PT existente** — a regra do CLAUDE.md era "não traduzir retroativo"; o Marcelo
   pediu explicitamente a tradução neste plano (03/11/2026).
2. **Texto de tela (MCP, `--help`, OpenAPI) fica em PT?** <span class="rec">Recomendo: sim</span> —
   traduzir mudaria o que a Mira e a equipe do PCP leem.
3. **E8 (irmãos) entra?** <span class="rec">Recomendo: só se sobrar fôlego</span> — exige mexer em
   2 repos no mesmo commit para 21 docstrings.
