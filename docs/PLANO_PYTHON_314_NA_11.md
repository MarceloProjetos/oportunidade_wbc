# Plano — Python 3.14 na .11

> **Status: MIGRADA em 14/09/2026.** Os 5 serviços da `.11` rodam em **Python 3.14.7**,
> e a F3 passou inteira em produção (§7). Aberta só a **D4**: quando desinstalar o 3.12,
> que segue instalado como rollback.
>
> Plano escrito em 2026-09-11. Decisão do Marcelo: **em dois passos** (pacotes primeiro,
> Python depois) e **sem venv** — as duas se provaram certas.

A `.11` **rodava** Python 3.12.10 (medido no `/status`), com os 5 serviços no Python do
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
   `PATH`.

   > ❌ **Este item estava ERRADO, e foi o erro que mais custou.** Eu escrevi que
   > "mudar o `PATH` vale depois de reiniciar os serviços (o que o deploy já faz)".
   > **Não vale.** Quem entrega o ambiente ao serviço é o Gerenciador de Serviços, e
   > ele só relê o `PATH` do sistema **no reboot** — um `deploy_update.bat` inteiro
   > rodou e os serviços continuaram no 3.12. Ver §7.
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
| **F0** | ✅ **11/09** — `pandas` `2.2.3` → `2.3.3`, deploy no 3.12. Provado no mesmo dia: o agendador rodou às 07:55:26 com `sucesso` | 11/09 | feito |
| **F1** | ✅ **14/09** — instalado **for all users** em `C:\Program Files\Python314`, 3.12 mantido. `where.exe python` com o 3.14 na frente | 14/09 | dele |
| **F2** | ✅ **14/09** — `del state\deps.sha256` + deploy. O `pip` foi para o `Python314`, conferido por um `import` de todas as 19 dependências, `mcp.server.fastmcp` incluído | 14/09 | dele |
| **F3** | ✅ **14/09** — os três passaram em produção, mais a suíte na própria `.11` (§7) | 14/09 | meu |
| **F4** | ⏳ **em observação desde 14/09** — o 3.12 fica instalado até um dia limpo | depois | dele |

### Por que F0 separada

Se algo quebrar depois da F0, é **o pandas** — não o Python. Se quebrar depois da F1, é
**o Python** — não o pandas. Fazer os dois juntos custaria um dia de bisseção para
descobrir qual dos dois foi.

### F3 — o smoke que importa

| O quê | Como | Prova |
| --- | --- | --- |
| `hdbcli` | tool MCP `situacao_pedido 84348` | lê o HANA e resolve o endereço |
| `pyodbc` | `GET /status?checks=sql_server` | é `pyodbc.connect` de verdade, não um ping |
| `pymssql` | o ciclo do worker no `/status` | lê o WBCCAD sem escrever |
| Service Layer | `python -m wbcpython check-sap` | autentica no SL sem criar documento. **`doctor` NÃO serve** — ele não acessa a rede |
| Os 5 serviços | `/health`, `/status` com o `STATUS_ID`, `:8078` → 401, `:8079` → 303 | sobem e respondem |
| Suíte | `python -m pytest -q` na `.11` | verde **no Python novo** — deu **1749 passed** (§7) |

---

## 4. Rollback

Sem venv, o rollback é o `PATH`:

1. Voltar o **3.12** para a frente do `PATH` (por isso ele **não** pode ser desinstalado na
   virada);
2. `del state\deps.sha256`;
3. `deploy_update.bat` — reinstala no 3.12;
4. **reiniciar a máquina** — sem isso os serviços seguem no 3.14 (§7).

Conferir qual está valendo: **`where.exe python`** diz o do console; **quem manda nos
serviços é o `system.python` do `/status`**, e os dois discordam entre o `PATH` novo e o
reboot.

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

Tudo cumprido em 11 e 14/09. Resta **uma** decisão, sem pressa:

- **D4 — desinstalar o 3.12**, depois de um dia limpo de observação. Enquanto ele
  estiver na máquina, o rollback custa um `PATH` e um reboot.


---

## 7. Resultado (14/09/2026)

**Migrada.** `system.python` da `.11` = **3.14.7**, os 5 serviços no ar.

### A F3, provada em produção

| O quê | Prova |
| --- | --- |
| **hdbcli** (HANA) | check `sap` ✅ 24 ms |
| **pyodbc** (SQL Server) | check `sql_server` ✅ 8 ms — é `pyodbc.connect`, não um ping |
| **pymssql** (WBCCAD) | worker ciclo 863: 1729 orçamentos, **nenhum erro** |
| **Service Layer** | `python -m wbcpython check-sap` → `[ok] Conexão OK (company_db=SBOALTAMIRAPROD)` |
| **pandas** | agendador 07:40:21 `sucesso` — DataFrame → Supabase |
| **mcp / fastmcp** | 8078 respondendo; o próprio diagnóstico veio por ele |
| Painel WBC (FastAPI) | 8079 → 303 |
| Suíte na `.11`, no 3.14 | **1749 passed**, 29 skipped, 2 falhas explicadas (abaixo) — hoje **1744 / 0**, sem os órfãos |

Memória caiu de 43.5% para **30.6%** depois da virada.

### ⚠️ O que ninguém tinha previsto: o PATH não chega aos serviços sem reboot

Depois de instalar o 3.14 e vê-lo primeiro no `where.exe python`, **um `deploy_update.bat`
inteiro rodou e os serviços continuaram no 3.12** (`system.python: 3.12.10`, com
`uptime_s: 94`). O Windows só entrega o `PATH` novo aos **serviços** quando o Gerenciador
de Serviços relê o ambiente — ou seja, **no reboot**. `nssm restart` não basta: o serviço
herda o ambiente que o SCM já tinha.

Isso criou uma janela perigosa que passou despercebida por minutos: **3.14 primeiro no
PATH, `site-packages` vazio, e a `.11` reinicia sozinha às 06:12.** Se o reboot tivesse
vindo antes do `pip`, os 5 serviços subiriam num Python sem dependência nenhuma.

**A ordem segura, para a próxima:** instalar → `del state\deps.sha256` → `deploy_update.bat`
(o `pip` instala no interpretador novo enquanto os serviços ainda rodam no antigo) →
**só então** reiniciar. O deploy do meio é o que fecha a janela.

### As 2 falhas da suíte — nenhuma é do 3.14

1. **`test_export_os_json.py`** — `export_os_json.py` e o teste dele foram **apagados do
   repo** na faxina `842bf02` ("JSON export CLI no longer used"). Continuam em disco na
   `.11` como órfãos (`??` no `git status`), o pytest os coleta e eles quebram contra um
   `Settings` que não tem mais `os_status_table`. Código morto testando código morto.
2. **`tests/wbc/test_logs.py::TestRuido`** — **passa sozinha** no mesmo 3.14.7. Só quebra
   dentro da suíte inteira: `logging` é estado global e algum teste anterior deixou um
   handler que engoliu o registro do `httpx`. E o que mudou a ordem de coleta na `.11`
   foram justamente os 2 órfãos a mais. Fragilidade antiga, não migração.


✅ **Resolvidas no mesmo dia.** Os 2 órfãos foram apagados da `.11` e a suíte de lá ficou
**1744 passed, 29 skipped, 0 falhas**. A aritmética confirma o diagnóstico sem deixar dúvida:
sumiram **7 testes coletados** (1780 → 1773) — os 6 que passavam e o 1 que falhava do arquivo
órfão — e a `TestRuido` **voltou ao verde sozinha**, sem ninguém tocar nela. Era a ordem de
coleta mesmo.

### Uma coisa a mais na `.11`

`pytest==8.3.5` e `ruff==0.15.20` foram instalados no 3.14 de lá (o deploy nunca os
instalou — são do `requirements-dev.txt`). A máquina passou a conseguir se autotestar, que
é o que permitiu rodar a suíte antes do reboot.

---

*Fonte da verdade deste plano: este arquivo. A página publicada espelha o mesmo conteúdo.*
