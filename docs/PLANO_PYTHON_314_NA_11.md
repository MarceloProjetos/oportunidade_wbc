# Plano — Python 3.14 na .11

> **Status: nada feito.** Plano escrito em 2026-09-11 a partir de medições do mesmo dia.
> Decisão do Marcelo: **em dois passos** (pacotes hoje, Python amanhã sem ninguém) e
> **sem venv**.

A `.11` roda **Python 3.12.10** (medido no `/status`), com os 5 serviços no Python do
sistema — não há venv lá, de propósito. A pergunta era o que quebra ao ir para o 3.14.7.

**Resposta curta: um pin.**

---

## 1. O que foi medido (2026-09-11)

Das 22 dependências dos dois `requirements.txt`, rodando no 3.14.7 desta máquina:

| Medida | Resultado |
| --- | --- |
| Instalam no 3.14 | **20 de 22** — inclusive `hdbcli==2.29.23` (o pin exato), `pyodbc==5.3.0`, `pydantic`, `supabase==2.31.0` |
| `waitress` · `pymssql` · `mcp<2` | ✅ têm roda cp314 (3.0.2 · 2.4.1 · **1.30.0**) — não estavam instalados aqui, só por isso apareceram como falta |
| **`pandas==2.2.3`** | ❌ **não existe roda cp314.** Só a partir da **2.3.3** |
| `pandas==2.3.3` no **3.12** | ✅ instala — é o que permite separar os dois passos |

**A suíte inteira da `.11` (1661 testes) já passa no 3.14.7**, com pandas 2.3.3 — é o
Python desta máquina desde 08/09. E as medições da B0/B3 de ontem consultaram o **HANA de
produção** pelo `hdbcli` nesse mesmo Python.

### O que as medições NÃO provam

Três caminhos que nenhum teste exercita e só a produção mostra — e são os caros:

- **`pyodbc`** contra o SQL Server (pipeline de oportunidades);
- **`pymssql`** contra o WBCCAD (o worker lê os orçamentos);
- as **escritas no Service Layer** (status de OP, e o worker criando cotação/pedido).

Instalar ≠ conectar. Se um desses falhar, a `.11` **para de criar pedido no SAP** — não é
erro cosmético.

---

## 2. Fatos que travam o desenho

1. **Sem venv (decisão dele).** Então o rollback não é apagar uma pasta: é **manter o
   3.12 instalado** e voltar a ordem do `PATH`. Desinstalar o 3.12 no mesmo dia tira a
   rede de segurança.
2. ⚠️ **O `deploy_update.bat` NÃO reinstala se o `requirements.txt` não mudou.** Ele
   compara o SHA-256 dos dois arquivos com o que está em `state\deps.sha256` e pula o
   `pip` quando são iguais. **Num Python recém-instalado o `site-packages` está vazio** —
   e o deploy passaria batido, deixando os 5 serviços sem dependência nenhuma. **Apagar o
   `state\deps.sha256` antes** (ou rodar o `pip` à mão) é obrigatório no dia da virada.
3. **Os 5 serviços pegam o `python` do `PATH`.** Os `run_*.bat` fazem
   `if exist "venv\Scripts\python.exe" … else set "PY=python"`. Sem venv, quem decide é o
   `PATH` — e o NSSM lê o ambiente **no start do serviço**, então mudar o `PATH` só vale
   depois de reiniciar os serviços (o que o deploy já faz).
4. **Duas coisas ali escrevem em PRODUÇÃO no SAP:** `ordens_producao_sl.py` (status de OP)
   e o **worker do `wbcpython`** (cotação, pedido, oportunidade). O worker roda 06:30–19:00
   em dias úteis.
5. **Parada limpa do worker é por ARQUIVO**, não Ctrl+C: gravar `state\wbc_worker.stop` e
   só então `nssm stop`. Matar no meio de um POST deixa cotação criada sem vínculo.
6. **`mcp` está pinado `<2`** no `mcp/requirements.txt`, com comentário explicando por quê
   (o 2.x renomeou `FastMCP` → `MCPServer`). O pin resolve para **1.30.0** no 3.14 — está
   coberto, não precisa de ação.

---

## 3. Fases

| Fase | O quê | Quando | Dono |
| --- | --- | --- | --- |
| **F0** | **Bump do pandas** `2.2.3` → `2.3.3` no `requirements.txt`, commit, deploy normal. Roda **no 3.12 de hoje** | agora | eu escrevo, deploy dele |
| **F1** | Instalar o **3.14.7** na `.11`, **mantendo o 3.12**, e pôr o 3.14 primeiro no `PATH` | amanhã, sem ninguém | dele |
| **F2** | `del state\deps.sha256` + `deploy_update.bat` (ou `pip install -r` nos dois requirements) e subir os 5 serviços | junto da F1 | dele |
| **F3** | **Smoke dos 3 caminhos que os testes não cobrem** | logo depois | meu |
| **F4** | 1 dia de observação e, só então, decidir se desinstala o 3.12 | depois | dele |

### Por que F0 separada

Se algo quebrar depois da F0, é **o pandas** — não o Python. Se quebrar depois da F1, é
**o Python** — não o pandas. Fazer os dois juntos custaria um dia de bisseção para
descobrir qual dos dois foi.

### F3 — o smoke que importa

| O quê | Como | Prova |
| --- | --- | --- |
| `hdbcli` | tool MCP `situacao_pedido 84348` | lê o HANA e resolve o endereço |
| `pyodbc` | `GET /oportunidades/info` | conta as linhas do SQL Server |
| `pymssql` | `python -m wbcpython pendentes` (**só leitura**) | lê o WBCCAD sem escrever |
| Service Layer | `python -m wbcpython doctor` | autentica no SL sem criar documento |
| Os 5 serviços | `/health`, `/status` com o `STATUS_ID`, `:8078` → 401, `:8079` → 303 | sobem e respondem |
| Suíte | `python -m pytest -q` na `.11` | 1661 verdes **no Python novo** |

---

## 4. Rollback

Sem venv, o rollback é o `PATH`:

1. Voltar o **3.12** para a frente do `PATH` (por isso ele **não** pode ser desinstalado na
   virada);
2. `del state\deps.sha256`;
3. `deploy_update.bat` — reinstala no 3.12 e reinicia os 5.

Conferir qual está valendo: **`where.exe python`** (o primeiro da lista é o que vale).

---

## 5. Decisões

| # | Assunto | Estado |
| --- | --- | --- |
| **D1** | Dois passos em vez de um | ✅ **dele, 11/09** — pacotes hoje no 3.12, Python amanhã. Isola as variáveis |
| **D2** | Sem venv | ✅ **dele, 11/09.** Consequência aceita: o rollback passa a ser o `PATH`, e o 3.12 **fica instalado** até a F4 |
| **D3** | Virar fora do expediente | ✅ decidido — o worker escreve no SAP das 06:30 às 19:00 |
| **D4** | Desinstalar o 3.12 | **Aberta.** Recomendado: **só depois de 1 dia limpo** (F4). É a rede de segurança inteira |

---

## 6. O que eu preciso do Marcelo

1. **Deploy da F0** (o bump do pandas) — eu commito, ele roda o `deploy_update.bat`.
2. **F1 + F2 amanhã**: instalar o 3.14.7, ajustar o `PATH`, apagar o `state\deps.sha256` e
   rodar o deploy. **Não desinstalar o 3.12.**
3. Me avisar quando subir, para eu rodar a F3.

---

*Fonte da verdade deste plano: este arquivo. A página publicada espelha o mesmo conteúdo.*
