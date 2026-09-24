# Plano — DX do agente no ServidorIntegracaoSAP

> **Status (24/09/2026): F0–F3 concluídas** (commit por fase, tudo no `origin/master`).
> Nada exige restart; o próximo `deploy_update.bat` leva junto (roda o `pip` — nada novo).
> F4–F5 não autorizadas ainda. Pendem com o Marcelo: copiar o `settings.json` proposto,
> apagar `exports/`/logs de junho (D6) e conferir o A7 (`hdbcli`) na `.11`.

Objetivo: deixar o repo mais barato de entender e de mudar para um agente — sem mexer no
que roda em produção na `.11` além do necessário.

**Resposta curta:** o código está em bom estado (0 módulo órfão, 97% das funções tipadas,
1838 testes em 5,4 s). O que custa ao agente é **documentação desatualizada** e **falta de
rede de proteção** (nenhum gate automático, suíte da raiz sem isolamento do `.env`). O
Python 3.14 traz **um** ganho real aqui (SQL parametrizado com t-strings); o resto é
cosmético.

---

## 1. Achados que importam (verificados)

| # | Achado | Onde | Gravidade |
|---|---|---|---|
| A1 | Suíte da **raiz** lê o `.env` real (`load_dotenv()` no import do `config`). O `tests/conftest.py` só zera o cache; só `tests/wbc/conftest.py` apaga `SAP_*`/`SQL_*`, e **nenhum** apaga `SUPABASE_*`. Teste que esquecer um stub fala com produção — `DELETE /historico` faz `.delete().neq('id', 0)` | `tests/conftest.py`, `api.py:238-242` | ⚠️ alta |
| A2 | `httpx` é usado direto pelo worker (cliente do Service Layer) mas **não está no `requirements.txt`** — chega só porque o `supabase` depende dele. O comentário da linha 27 diz o contrário | `requirements.txt:27`, `wbcpython/infrastructure/service_layer/client.py:32` | média |
| A3 | Default do banco de acompanhamento **diverge** entre os dois configs: raiz `state/wbc_tracking.db`, `wbcpython` `./wbcpython_tracking.db`. Se o `.env` da `.11` perder `TRACKING_DB_URL`, o worker grava num e o `/status` lê o outro | `config.py:113`, `wbcpython/config.py:105` | média |
| A4 | **Nenhum gate roda sozinho**: sem `.github/`, sem pre-commit, e o `ruff` nem está instalado no Python 3.14 local (`No module named ruff`) | — | média |
| A5 | CLAUDE.md contradiz a si mesmo: linha 168 "pip no Python 3.12", linha 202 "migrou para 3.14.7" (o 3.12 foi desinstalado em 14/09) | `CLAUDE.md:168` | média |
| A7 | **`hdbcli` 2.29.25 no Python 3.14 derruba o processo (access violation) quando a conexão falha** — recusa, nome inexistente. Com `CONNECTTIMEOUT` menor que o tempo da recusa, levanta erro normal. Achado na F0 (o pytest morria). Na `.11` o pin é 2.29.23: **não conferido lá** | `sap_connection.py:92`, `wbcpython/infrastructure/hana/repository.py:98` | ⚠️ alta, a confirmar |
| A6 | `pendentes` da CLI (354 linhas) **recopia** a decisão de dois passos do worker e importa `_OrcamentoResumido` privado. Se derivar, a prévia mente sobre o que o worker vai gravar — sem erro nenhum | `wbcpython/cli.py:386-739`, `processar.py:249-341` | média |

---

## 2. Fatos que travam o desenho

1. **Produção escreve no SAP** (worker WBC 06:30–19:00 e status de OP). Tudo que exige
   restart do worker vai para uma janela fora do expediente, com parada por arquivo.
2. **pip, restart e deploy são do Marcelo.** Mudar `requirements.txt` faz o
   `deploy_update.bat` rodar `pip` (hash muda) — inofensivo para o `httpx`, que já está lá.
3. **Arquivos-irmãos de outros repos não podem ser reescritos de um lado só:**
   `ordens_producao_sl.py`, `windows_update.py`, `situacao_pedidos.py`,
   `sap_montagem_labels.py` (teste de diff), `wake_altservidor_ia.py` (byte-idêntico),
   `pedidos_bloqueados.py` (3 cópias). Ficam fora de `ruff --fix UP` e de tradução.
4. **Repo público.** `API_*.md` da raiz podem já ter sido enviados por link a outras equipes.
5. **`collect_status`/`SELECTABLE_CHECKS` são contrato entre repos** (o `.90` lê).

---

## 3. Fases (numeradas por frente; a ordem de execução está em D7)

### F0 — Rede de proteção · sem restart · agente — ✅ concluída 24/09
*Goal: rodar `pytest` deixa de poder tocar produção, e as duas divergências silenciosas ganham teste.*

> ⚠️ **O que mordeu:** a trava pegou **12 testes que abriam conexão REAL** (11 no HANA de
> produção, 1 relendo o Supabase de produção). E o endereço falso sozinho derrubava o pytest:
> o `hdbcli` morre com access violation na conexão recusada (A7) — por isso a trava é nos
> **drivers** (`pytest.fail`, que atravessa os `except Exception`), não só no `.env`.
> O default de `TRACKING_DB_URL` foi alinhado já aqui (não na F4): a `.11` lê
> `state\wbc_tracking.db` e vê o ciclo de 1 min atrás, então o `.env` de lá já define o
> caminho — sem efeito em produção. Suíte: **1833 passed, 0 falhas**.

- Fixture `autouse` no `tests/conftest.py` com valores **falsos** (não apagados) para
  `SUPABASE_URL`, `SUPABASE_*KEY`, `SAP_HOST`, `SQL*_HOST`, `OP_SL_*` (ex.: `http://127.0.0.1:9`).
- Teste de paridade: `WBC_*_DEFAULT` da raiz == defaults do `Settings` do `wbcpython`
  (vai nascer vermelho no A3 — e é para isso).
- `requirements.txt`: `httpx>=0.27,<1` explícito; piso do `pymssql` para `>=2.4.1` (o
  primeiro com roda cp314); corrigir os 3 comentários que falam em 3.12.

### F1 — CLAUDE.md e docs de agente · sem restart · agente — ✅ concluída 24/09
*Goal: o CLAUDE.md diz só o que é verdade hoje, em ~150 linhas em vez de 231.*

> Fechou em **210 linhas**, não 150: o mapa ganhou os ~10 módulos que faltavam (é o que o
> agente mais consulta), e a história foi para `docs/INCIDENTES.md`. O `.claude/settings.json`
> do repo **não foi tocado**: config do agente é do Marcelo (no web o classificador barrou a
> auto-modificação em 24/09) — a proposta ficou no scratchpad para ele copiar.

- **Corrigir** (§4.1): 3.12→3.14, V117→V118 (também em 9 docstrings), agendador com 4 jobs,
  imports reais do `api.py`, `run_wbc_*.bat` (só existe o do painel), "843 testes",
  "28 linhas", serviço `OrcaView-MCP`.
- **Acrescentar**: `pedidos_bloqueados.py` (3 cópias), `wake_altservidor_ia.py`
  (byte-idêntico), `situacao_pedidos*` (diffável), `extract_orcamentos_espelho.py`,
  linhas da tabela "Tarefa → ler" para RH, Vendas BI, `/pedidos/*`, `/usuarios-ativos`;
  gotcha do `test_repo_layout` (`.gitignore _*.py`); regra de idioma dos comentários (D1).
- **Mover** ~65 linhas de narrativa de incidente (datas, medições, "610 dias", ciclo #133,
  virada do 3.12) para `docs/INCIDENTES.md`, deixando 1 linha de regra + link.
- `.claude/settings.json` do repo: trocar as 3 permissões mortas por leitura útil
  (`python -m pytest`, `python -m ruff`, `git status/diff/log`).
- README (badge 3.12, árvore sem ~15 arquivos), `deploy_update.bat:112`,
  `install_wol_task.ps1:106` (fallback Python312), `run_wbc_painel.bat:4`.

### F2 — Gate automático · sem restart · agente — ✅ concluída 24/09
*Goal: ruff e pytest rodam antes de todo commit, sem depender de lembrar.*

> Sem instalar nada no Python: o ruff roda por `uvx ruff@0.15.20` (o hook tenta `python -m ruff`
> primeiro). `ruff --fix` com `UP` modernizou **355 pontos em 21 arquivos**, e zerou os 6 erros
> que já existiam. ⚠️ **O que mordeu:** o `tests/test_windows_update.py` também é irmão do
> SAP_RDP — o `UP` o reescreveu e foi revertido e excluído. O hook leva **~40 s**, não os 6 s
> estimados (5 s era só a coleta).

- `pip install -r requirements-dev.txt` no desktop (ruff ausente; pytest 8.4.2 × pin 8.3.5 → alinhar o pin).
- `pyproject.toml`: `target-version = "py314"`, adicionar `"UP"` com `per-file-ignores`
  para os arquivos-irmãos do Fato 3. Um `ruff --fix` único moderniza ~218 `Optional/List/Dict`.
- Hook `pre-commit` local (ruff + `pytest -q`, ~6 s) — D2.

### F3 — Limpeza · sem restart · agente — ✅ concluída 24/09
*Goal: o que o agente encontra ao listar o repo é o que está vivo.*

> ⚠️ **O que mordeu:** a "comparação 514421×514706" do `DECISOES.md` **não era evidência
> bruta** — só as primeiras ~45 linhas eram; o resto são ~25 decisões de setembro sem `##`
> próprio (inclusive a "Virada para produção" que o CLAUDE.md cita). Mover para apêndice
> esconderia decisão viva: ganharam o `##` e o arquivo ganhou índice, nada saiu do lugar.
> O `PLANO_STATUS_E_ENDERECO_ENTREGA` **não** foi arquivado: ainda tem pendência sua.
> `exports/` e os logs de junho **não** foram apagados (D6): apagar arquivo seu, com dado de
> cliente, fica com você.

| Item | Evidência | Risco |
|---|---|---|
| Worktree `sleepy-lovelace-30ef4f` + branch | em `5b3e6c9`, já contido na master (130 atrás), sem mudança | nenhum |
| 6 trechos mortos: `extract_orcamentos_espelho._info`, 2 re-exports `noqa: F401`, 2 constantes "backward compat" em `extract_sap_to_supabase`, `cli._cmd_nao_implementado`, `FERIADOS_ANO_INICIO` | nenhuma referência | muito baixo |
| 5 migrações SQL aplicadas → `sql/migracoes/` | colunas já estão em `vw_os_integracao.sql` | baixo |
| 3 planos encerrados sem link no código → `docs/arquivo/` (`PYTHON_314`, `PORTA_PALETES`, `STATUS_E_ENDERECO`) | status "ENCERRADO" | baixo |
| `CHANGELOG.md` (189 KB) partido por mês em `docs/changelog/` | nenhum gate o lê | baixo — D4 |
| `docs/wbc/DECISOES.md` (110 KB, 95 headings, sem índice): índice no topo + a comparação 514421×514706 (~670 linhas) para apêndice | 1/3 do arquivo é evidência bruta | baixo |
| `.ruff_cache/` no `.gitignore`; `exports/`, `logs/` de junho e `__pycache__` cpython-312 locais | ignorados, nunca commitados | nenhum — D6 |

### F4 — Refatoração pequena · **exige restart** · agente codifica, ele reinicia
*Goal: um lugar só para cada decisão que hoje existe duplicada.*

| Item | Ganho | Risco | Restart |
|---|---|---|---|
| `pendentes` → `application/previsao.prever()`, com `avaliar()` compartilhado com `processar` (A6) | médio | baixo se só o lado da CLI mudar | worker + painel |
| Alinhar default de `TRACKING_DB_URL` (A3) | médio | baixo (o `.env` da `.11` deve ter a linha — conferir) | worker |
| `api.py`: helper de gatilho (oport ≡ vendas-bi), helper "int positivo ou 400" (6×), `RATE_*`/`SYNC_LOTE_MAX` para o `Settings`, docstring de rotas | baixo-médio | baixo | API |
| 4 file-locks quase iguais em `pipeline_core` → um `_file_lock()` | baixo-médio | baixo | API + agendador |
| `venda_comum._inteiro` (0→None) renomeado — há 5 `_inteiro` com semânticas diferentes | baixo | baixo | worker |
| MCP: `annotations=_ANOTACAO_LEITURA` nas 9 tools antigas + helper de `limit` | baixo | baixo | MCP |

### F5 — Python 3.14 de verdade · restart API/agendador · agente
*Goal: SQL do HANA sem interpolação de valor, com o identificador validado.*

- `SAPExtractor.execute_query(query, params=None)` — hoje não aceita parâmetros; o
  `db_utils.read_dbapi_query` já aceita.
- Helper `sql(t"...")` (PEP 750): `{valor}` vira `?` + parâmetro; `{schema:id}` vira
  identificador validado e citado. Alvos: 12 pontos em `extract_ordens_servico_engenharia`,
  `extract_vendas_bi`, `situacao_pedidos_hana` (inclui `"{schema}"` vindo do `.env` sem validação).
  Hoje todos são `int()` ou constante — é endurecimento, não furo aberto.
- `itertools.batched` nos 3 loops de lote (`pipeline_core:507, 624`, `wbc_sql/repository:293`).
- Operação: `python -m pdb -p <PID>` (3.14) para inspecionar worker/API travados sob NSSM — só documentar.

### Não fazer (avaliado e descartado)

| Proposta | Por quê |
|---|---|
| Partir `api.py` em Blueprints | ~99 `monkeypatch.setattr(apimod, …)` passariam a patchar o nome errado **em silêncio** → teste rodando código real (ver A1). Agente acha rota por grep |
| Traduzir comentários para inglês | ~7.300 linhas; tentativa de jul/26 parou na onda 3 de 5 e o código novo voltou ao PT (67% dos comentários, 85% das docstrings, 100% do `wbcpython`). Logs, telas, tools MCP e irmãos são PT |
| Tirar `from __future__ import annotations` (63 arquivos) | redundante no 3.14, mas inofensivo; tirar só expõe pydantic/FastAPI ao PEP 649 |
| PEP 695, TaskGroup, subinterpreters, free-threading, zstd, uuid7 | nada no código se beneficia (I/O-bound, sem generics, sem asyncio próprio) |
| Registro de checks no `monitoring.py` | 7 checks, mudam pouco, contrato entre repos |
| Mexer em `processar.py`, `sitcode.py`, `worker._ciclo` | grandes mas coesos e com 94+ testes |

---

## 4. Referência

### 4.1 CLAUDE.md — afirmações desatualizadas

| Linha | Diz | Verdade hoje |
|---|---|---|
| 168 | pip no Python 3.12 | 3.14.7 em `C:\Program Files\Python314` |
| 14, 43 | agendador = carga de oportunidades, janela 7-18 | 4 jobs: oportunidades, Vendas BI 15 min, Vendas BI 60 min 24/7, espelho de orçamentos |
| 41 | `db_utils.py` (28 linhas) | 35 — tirar o número |
| 52-53 | `api.py` importa 2 pipelines | importa 8 módulos |
| 133 | `web_orcaview_V117/...` | V118 (V117 não existe mais) |
| 173 | "os `run_wbc_*.bat`" | só `run_wbc_painel.bat`; o worker usa `AppDirectory` do NSSM |
| 220 | 843 testes em `tests/wbc` | ~1238 — tirar o número |

### 4.2 Idioma hoje

| Pasta | Comentários PT/EN | Docstrings PT/EN |
|---|---|---|
| raiz | 34% / 50% | 45% / 54% |
| `wbcpython/` | 84% / 0% | 100% / 0% |
| `mcp/` | 84% / 0% | 99% / 0% |
| `tests/` | 68–81% / 0% | 96–99% / ~0% |

---

## 5. Decisões

> Em 24/09 o Marcelo liberou F0–F3 "seguindo as recomendações": D1, D2, D3, D4 e D7 foram
> aplicadas assim; D5 segue "não agora"; D6 (apagar arquivos dele) ficou com ele.

| # | Assunto | Recomendação |
|---|---|---|
| **D1** | Idioma dos comentários | **Português como regra**, 1 linha no CLAUDE.md; os 11 arquivos já traduzidos ficam como estão. Encerra a tradução de jul/26 |
| **D2** | Hook `pre-commit` local (ruff + pytest, ~6 s) | **Sim** — é o único gate possível sem CI; é config persistente, por isso pergunto |
| **D3** | `API_*.md` da raiz | **Ficam na raiz** (links externos, repo público); só entram no mapa do CLAUDE.md |
| **D4** | Partir o `CHANGELOG.md` por mês | **Sim**, `docs/changelog/AAAA-MM.md`; o da raiz guarda o mês corrente |
| **D5** | Blueprints no `api.py` | **Não** agora; se um dia, começar pelo RH (1511-1695) e depois da F0 |
| **D6** | Apagar `exports/` local (JSON de cliente, 25/06) e logs de junho | **Sim** — são seus arquivos locais, por isso pergunto |
| **D7** | Ordem de execução | **F0 → F1 → F3 → F2 → F5 → F4**: tudo sem restart primeiro; F4 junta os restarts numa janela só |

---

*Fonte da verdade deste plano: este arquivo. A página publicada espelha o mesmo conteúdo.*
