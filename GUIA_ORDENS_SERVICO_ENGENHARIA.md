# Guia — Ordens de Serviço de Engenharia (sincronização + exportação)

Guia **didático e prático** do pipeline que traz Ordens de Serviço do SAP para o
Supabase e exporta para JSON. Feito para quem nunca mexeu no projeto conseguir
operar do zero.

> **TL;DR**
> ```bash
> # 1) (uma vez) criar as tabelas no Supabase: rode sql/ordens_servico_engenharia.sql
> # 2) sincronizar um pedido:
> python extract_ordens_servico_engenharia.py 84080
> # 3) exportar para JSON:
> python export_os_json.py 84080
> ```

---

## Sumário

- [1. O que é e por que existe](#1-o-que-é-e-por-que-existe)
- [2. Conceitos-chave (leia primeiro)](#2-conceitos-chave-leia-primeiro)
- [3. Como os dados fluem](#3-como-os-dados-fluem)
- [4. Pré-requisitos](#4-pré-requisitos)
- [5. Passo 1 — Criar as tabelas (uma vez)](#5-passo-1--criar-as-tabelas-uma-vez)
- [6. Passo 2 — Sincronizar um pedido](#6-passo-2--sincronizar-um-pedido)
  - [6.1 Disparar pela API (do app)](#61-disparar-pela-api-do-app)
- [7. Como funciona o `replace_nped` (exemplo passo a passo)](#7-como-funciona-o-replace_nped-exemplo-passo-a-passo)
- [8. Passo 3 — Exportar para JSON](#8-passo-3--exportar-para-json)
- [9. Anatomia do JSON exportado](#9-anatomia-do-json-exportado)
- [10. Campos de controle (auditoria)](#10-campos-de-controle-auditoria)
- [11. Status da OS (P/R/L/C)](#11-status-da-os-prlc)
- [12. Segurança (por que ninguém lê via anon)](#12-segurança-por-que-ninguém-lê-via-anon)
- [13. Consultas SQL úteis](#13-consultas-sql-úteis)
- [14. Solução de problemas (FAQ)](#14-solução-de-problemas-faq)
- [15. Cola de comandos](#15-cola-de-comandos)

---

## 1. O que é e por que existe

A engenharia precisa de uma cópia das **Ordens de Serviço** (estrutura/produção de
um pedido) fora do SAP — para consultar no Supabase e **exportar para JSON**. Este
pipeline faz isso **sob demanda, um pedido (`NPED`) por vez**:

1. **Lê** a view SAP `VW_EXPORT_ORDENS_SERVICO_1` filtrando pelo `NPED`;
2. **Grava** numa tabela do Supabase (`ordens_servico_engenharia`) com os mesmos campos;
3. **Exporta** para JSON quando você quiser.

É **separado** do pipeline de `oportunidades` (que roda agendado) — não interfere
nele, mas reaproveita o mesmo "motor" (`pipeline_core.py`).

---

## 2. Conceitos-chave (leia primeiro)

| Conceito | O que significa na prática |
|---|---|
| **`NPED`** | Número do **pedido**. É a "unidade" que você sincroniza. Ex.: `84080`. |
| **Linha** | A view é **desnormalizada por item de estrutura/OP**: um pedido tem **muitas linhas** (média ~13; o pedido 84080 tem **383**). Cada linha é uma peça/componente da OP. |
| **`replace_nped`** | Estratégia de carga: ao sincronizar um pedido, ele **substitui** as linhas daquele pedido (não duplica) e **não toca** nos outros pedidos. |
| **`service_role`** | A chave do Supabase que o pipeline usa para escrever/ler. Tem acesso total (ignora as travas de segurança RLS). Fica no `.env`, **nunca** vai pro git. |
| **Sob demanda** | Não há agendamento. Você roda o comando quando precisa daquele pedido. |

---

## 3. Como os dados fluem

```text
   SAP HANA (view)                 Pipeline (Python)              Supabase (Postgres)
 ┌────────────────────┐    NPED   ┌───────────────────────┐     ┌──────────────────────────┐
 │ VW_EXPORT_ORDENS_  │  ───────► │ extract_ordens_       │ ──► │ ordens_servico_engenharia │
 │ SERVICO_1          │  WHERE    │ servico_engenharia.py │     │  (espelho do pedido)      │
 │ (58 colunas)       │  NPED=x   │  + replace_nped       │     │ status_ordens_servico_eng │
 └────────────────────┘           └───────────────────────┘     │ sincronizacao_log_os_eng  │
                                            │                    └────────────┬─────────────┘
                                            │ export_os_json.py               │ lê (service_role)
                                            └─────────────────────────────────┴──► arquivo .json
```

---

## 4. Pré-requisitos

1. **Python 3.12** com as dependências: `pip install -r requirements.txt`.
2. **Arquivo `.env`** preenchido (copie de `.env.example`). O que importa aqui:
   - `SAP_HOST`, `SAP_PORT`, `SAP_USER`, `SAP_PASSWORD`, `SAP_SCHEMA` — mesma conexão SAP do projeto.
   - `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` — destino e chave de escrita/leitura.
   - As `OS_*` são **opcionais** (têm default). Veja `.env.example`.
3. **Tabelas criadas** no Supabase (passo 1, abaixo).

> 💡 A conexão SAP é a **mesma** do pipeline de oportunidades. Se aquele já roda, aqui também roda.

---

## 5. Passo 1 — Criar as tabelas (uma vez)

No **Supabase → SQL Editor**, cole e rode o arquivo
[sql/ordens_servico_engenharia.sql](sql/ordens_servico_engenharia.sql). Ele cria:

| Tabela | Para quê |
|---|---|
| `ordens_servico_engenharia` | Os dados das OS (58 colunas + controle). |
| `status_ordens_servico_eng` | Tradução do Status (`P`→Planejado, etc.). Já vem preenchida. |
| `sincronizacao_log_os_eng` | Log de cada sincronização (quando, qual NPED, quantas linhas). |

**Como saber que deu certo?** Rode o bloco de verificação no fim do `.sql`. Você deve ver:

```text
relname                      | rls  | force_rls
-----------------------------+------+----------
ordens_servico_engenharia    | true | true
status_ordens_servico_eng    | true | true
sincronizacao_log_os_eng     | true | true
```

E o lookup de status preenchido:

```text
codigo | descricao
-------+-----------
 C     | Cancelado
 L     | Encerrado
 P     | Planejado
 R     | Liberado
```

---

## 6. Passo 2 — Sincronizar um pedido

```bash
# Um pedido
python extract_ordens_servico_engenharia.py 84080

# Vários pedidos de uma vez
python extract_ordens_servico_engenharia.py 84080 84095 84100
```

**Saída esperada** (resumida) ao sincronizar o `84080`:

```text
... Extraindo OS do NPED 84080...
... Query OK: 383 rows
... OS extraídas do SAP (NPED 84080): 383 linhas
... Lote 1: 200 registro(s) inseridos (200/383)
... Lote 2: 183 registro(s) inseridos (383/383)
... 383 registro(s) inseridos com sucesso na tabela 'ordens_servico_engenharia'
... Execuções anteriores removidas da tabela 'ordens_servico_engenharia' (filtro {'NPED': 84080})
... ✓ NPED 84080 sincronizado (id_execucao: b3a8bdc3-...)
... Concluído: 1/1 NPED(s) sincronizado(s) com sucesso
```

Forma **programática** (de dentro de outro script Python):

```python
from extract_ordens_servico_engenharia import main, run_npeds

main(84080)                      # um NPED
run_npeds([84080, 84095, 84100]) # vários → retorna {84080: True, ...}
```

> ⚠️ **Pedido inexistente:** se a view retornar **0 linhas** para o NPED, o pipeline
> **não apaga** o que já existe — ele avisa e encerra. Isso evita zerar por engano um
> pedido válido já carregado.

---

### 6.1 Disparar pela API (do app)

Para o **app** disparar a sincronização (ex.: um botão "Atualizar OS deste pedido"),
há uma API HTTP em [api.py](api.py). O app só faz um `POST`; a escrita continua sendo
do backend (`service_role`).

**Subir o serviço:**

```bash
# Produção (Windows) — via waitress:
waitress-serve --listen=0.0.0.0:8077 api:app

# Dev:
python api.py
```

**Rotas:**

| Rota | Método | Descrição |
|---|---|---|
| `/health` | GET | Verifica se está no ar (`{"status":"ok"}`). |
| `/sync/ordens-servico/<nped>` | POST | Sincroniza **um** pedido. |
| `/sync/ordens-servico` | POST | Corpo `{"nped": N}` ou `{"npeds": [...]}`. |

**Disparar (curl):**

```bash
curl -X POST http://localhost:8077/sync/ordens-servico/84080 \
     -H "X-API-Key: SUA_CHAVE"
```

**Resposta (200):**

```json
{
  "ok": true,
  "results": [{ "nped": 84080, "ok": true }],
  "summary": { "total": 1, "sucesso": 1, "falha": 0 }
}
```

**Códigos de status:** `200` todos OK · `207` parcial (vários NPEDs, alguns falharam) ·
`400` NPED inválido / corpo ausente · `401` sem/má `X-API-Key` · `502` nenhum sincronizou.

**No front (exemplo `fetch`):**

```js
async function sincronizarOS(nped) {
  const resp = await fetch(`http://SERVIDOR:8077/sync/ordens-servico/${nped}`, {
    method: 'POST',
    headers: { 'X-API-Key': API_KEY },
  });
  const data = await resp.json();
  if (data.ok) {
    alert(`Pedido ${nped} sincronizado!`);
  } else {
    alert(`Falha ao sincronizar ${nped}`);
  }
}
```

**Autenticação:** defina `OS_API_KEY` no `.env` para exigir o header
`X-API-Key: <chave>` (ou `Authorization: Bearer <chave>`). Sem ela, o endpoint fica
**aberto** — use só em rede interna/dev.

> ⏳ As cargas são **serializadas** (um lock interno): se dois disparos chegarem juntos,
> rodam em sequência (evita duas conexões SAP simultâneas). Cada sync leva ~3–4 s — o app
> pode mostrar um "carregando" enquanto aguarda a resposta.

---

## 7. Como funciona o `replace_nped` (exemplo passo a passo)

A grande sacada: **rodar o mesmo pedido de novo NÃO duplica** — ele atualiza.

Imagine a tabela já com o pedido `84080` (383 linhas) e o pedido `84095` (120 linhas):

```text
Estado inicial:   84080 → 383 linhas (id_execucao A)   |   84095 → 120 linhas (id_execucao B)
```

Você roda de novo só o `84080`:

```bash
python extract_ordens_servico_engenharia.py 84080
```

O que acontece, em ordem (estratégia **carrega-depois-poda**):

1. Extrai as 383 linhas atuais do `84080` no SAP.
2. **Insere** essas 383 com um **novo** `id_execucao C`. (Agora o 84080 tem 766 linhas: 383 antigas + 383 novas — momentâneo.)
3. **Apaga** só as linhas do `84080` que **não** são da execução atual (`WHERE NPED=84080 AND id_execucao <> C`) → remove as 383 antigas.

```text
Estado final:     84080 → 383 linhas (id_execucao C)   |   84095 → 120 linhas (id_execucao B)  ← intacto
```

**Por que inserir antes de apagar?** Se a inserção falhar no meio, as linhas antigas
**continuam lá** — o pedido nunca fica vazio. Segurança em primeiro lugar.

> ✅ **Comprovado na prática:** rodamos o `84080` duas vezes seguidas. Resultado: a tabela
> ficou com **383 linhas** e **1 único `id_execucao`** (o da última execução). Sem duplicação.

---

## 8. Passo 3 — Exportar para JSON

O exportador lê a tabela **já sincronizada** (não vai ao SAP) e gera um `.json`.

### 8.1 Exemplos

```bash
# Um pedido → gera exports/ordens_servico_84080_<timestamp>.json
python export_os_json.py 84080

# Vários pedidos no mesmo arquivo
python export_os_json.py 84080 84095 84100

# Tabela inteira
python export_os_json.py --all

# Enxuto (sem os textos NCLOB gigantes) e num caminho específico
python export_os_json.py 84080 --slim -o exports/84080_slim.json

# Compacto (uma linha) + só o array de linhas (sem metadados), no stdout p/ pipe
python export_os_json.py 84080 --stdout --compact --array > saida.json
```

### 8.2 Opções

| Opção | Efeito |
|---|---|
| `--all` | Exporta a tabela inteira (ignora os NPEDs informados). |
| `-o, --output <arquivo>` | Caminho do arquivo. Sem isso, gera em `exports/` com timestamp. |
| `--stdout` | Escreve no stdout em vez de arquivo (bom para `|` pipe). |
| `--slim` | Remove as 7 colunas de texto grande (NCLOB) — arquivo ~46% menor. |
| `--compact` | JSON numa linha só (sem indentação). |
| `--array` | Só a lista de linhas, sem o envelope de metadados. |
| `--no-status` | Não adiciona o campo `status_desc`. |

> 📏 **Tamanho real (pedido 84080, 383 linhas):** completo ≈ **1.4 MB**; com `--slim` ≈ **0.76 MB**.
> Os 7 campos NCLOB (descrições técnicas) são repetidos em cada linha — por isso o `--slim` ajuda tanto.

### 8.3 Programático

```python
from export_os_json import export_os

payload = export_os(npeds=[84080])                 # dict com metadados + rows
rows    = export_os(npeds=[84080], as_array=True)  # só a lista de linhas
todas   = export_os(all_rows=True, slim=True)      # tabela inteira, enxuta
```

---

## 9. Anatomia do JSON exportado

Por padrão (sem `--array`), o arquivo é um **envelope** com metadados + as linhas:

```json
{
  "exported_at": "2026-06-25T16:44:20.013294",
  "source_table": "ordens_servico_engenharia",
  "filter": { "nped": [84080] },
  "count": 383,
  "rows": [
    {
      "id": 384,
      "N_OP": 138757,
      "NPED": 84080,
      "DescItemPED": "Estantes",
      "DtPedido": "2026-06-24T00:00:00",
      "CodClien": "C011627",
      "NomeClien": "ARAUCO CELULOSE DO BRASIL S.A.",
      "Status": "R",
      "TotalOrcam": 20640.0,
      "id_execucao": "b3a8bdc3-2bb4-41a9-81d4-a75f4318b978",
      "data_hora_extracao": "2026-06-25T16:38:20.123456",
      "status_desc": "Liberado"
    }
  ]
}
```

Pontos que tratamos para o JSON sair **correto**:

- **Acentos legíveis** — gravado em UTF-8 com `ensure_ascii=False` (ex.: `Orçamento`, não `Orçamento`).
- **Vazios viram `null`** (e não `NaN`, que não é JSON válido).
- **Textos com `\par`, aspas, quebras** são escapados automaticamente — **sempre** consuma com um parser JSON, nunca "lendo string na mão".
- **`status_desc`** é adicionado juntando o código com o lookup (a menos que use `--no-status`).

> 💡 **Precisão de decimais:** valores `numeric` saem como número JSON. Para os nossos
> (preço, peso) isso é suficiente. Se algum dia precisar de exatidão "centavo a centavo"
> num consumidor sensível, a gente passa a exportar esses campos como string.

---

## 10. Campos de controle (auditoria)

Toda linha gravada ganha 4 campos além dos 58 da view:

| Campo | O que diz |
|---|---|
| `id_execucao` | UUID da carga. Todas as linhas de uma mesma sincronização compartilham o mesmo. É o que o `replace_nped` usa para podar. |
| `data_hora_extracao` | Quando aquele pedido foi sincronizado pela última vez (hora do servidor que rodou o ETL). |
| `origem_view` | De onde veio (`VW_EXPORT_ORDENS_SERVICO_1`). |
| `inserted_at` | Carimbo do **banco** no momento do INSERT. |

---

## 11. Status da OS (P/R/L/C)

A coluna `Status` guarda 1 letra. O lookup `status_ordens_servico_eng` traduz:

| Código | Descrição | Observação |
|---|---|---|
| `P` | Planejado | OP planejada |
| `R` | Liberado | Released — em produção |
| `L` | Encerrado | Closed / fechado |
| `C` | Cancelado | **Não aparece** na view hoje (semeado para o futuro) |

Na base toda, a distribuição é dominada por `L` (encerrados): ~28k linhas `L`, ~2k `R`, ~650 `P`.

---

## 12. Segurança (por que ninguém lê via `anon`)

As 3 tabelas têm **RLS `ENABLE` + `FORCE` e nenhuma policy**. Tradução:

- **Só o `service_role`** (chave do backend) lê e escreve. O pipeline e o exportador usam ela.
- A chave **`anon`** (pública) e usuários `authenticated` **não têm acesso nenhum** — nem leitura.
- Isso é **proposital** e segue as lições do Security Advisor do projeto:
  - sem policy de escrita "sempre true" → sem alerta *"RLS Policy Always True"*;
  - sem `SELECT using(true)` → não expõe dados de cliente à chave pública;
  - sem extensões/funções → sem alertas de *Extension in Public* / *SECURITY DEFINER*.

> O Advisor pode mostrar um **INFO** "RLS enabled, no policy" — é **intencional** (tabela só-backend).
> Se um dia um app precisar ler, adiciona-se uma policy de `SELECT` para a role certa.

Detalhes da decisão: [PLANO_SYNC_ORDENS_SERVICO.md §5.5](PLANO_SYNC_ORDENS_SERVICO.md).

---

## 13. Consultas SQL úteis

No **SQL Editor** do Supabase (que roda como `postgres`, com bypass de RLS):

```sql
-- Quantas linhas e pedidos já sincronizados?
select count(*) as linhas, count(distinct "NPED") as pedidos
from public.ordens_servico_engenharia;

-- Um pedido, com a descrição do status:
select o."NPED", o."N_OP", o."DescItemEstrut", o."Status", s.descricao as status_desc,
       o."TotalOrcam", o.data_hora_extracao
from public.ordens_servico_engenharia o
left join public.status_ordens_servico_eng s on s.codigo = o."Status"
where o."NPED" = 84080
order by o.id;

-- Histórico de sincronizações:
select * from public.sincronizacao_log_os_eng order by id desc;

-- Apagar um pedido específico (ex.: limpar um teste):
delete from public.ordens_servico_engenharia where "NPED" = 84080;
```

---

## 14. Solução de problemas (FAQ)

**"Faltam variáveis de ambiente obrigatórias do SAP/Supabase"**
→ O `.env` não foi carregado ou está incompleto. Rode os comandos **de dentro da pasta
`oportunidade_wbc/`** e confira `SAP_*` / `SUPABASE_*`.

**"Could not find the table 'public.ordens_servico_engenharia'"**
→ As tabelas ainda não foram criadas. Rode o passo 1 (o `.sql` no SQL Editor).

**Rodei o mesmo NPED 2x — duplicou?**
→ Não. O `replace_nped` poda as linhas antigas daquele NPED. Confira:
`select count(*), count(distinct id_execucao) from ordens_servico_engenharia where "NPED"=84080;`
(deve dar a contagem do pedido e **1** id_execucao).

**O pedido não trouxe nada (0 linhas).**
→ O NPED pode não existir na view, ou estar fora do escopo dela. A tabela é mantida
intacta de propósito. Confira no SAP/HANA se aquele NPED existe na view.

**Acentos aparecem como `ç` no JSON.**
→ Use o exportador deste projeto (já grava com `ensure_ascii=False`). Se for outro
consumidor, abra o arquivo como **UTF-8**.

**O JSON ficou grande demais.**
→ Use `--slim` (remove os textos NCLOB) e/ou `--compact`. Para a tabela inteira,
prefira `--slim`.

---

## 15. Cola de comandos

```bash
# ── Sincronizar (SAP → Supabase) ──
python extract_ordens_servico_engenharia.py 84080            # um pedido
python extract_ordens_servico_engenharia.py 84080 84095      # vários

# ── Exportar (Supabase → JSON) ──
python export_os_json.py 84080                               # 1 pedido, arquivo em exports/
python export_os_json.py 84080 84095 --slim                  # vários, enxuto
python export_os_json.py --all -o exports/todas.json         # tudo, caminho fixo
python export_os_json.py 84080 --stdout --array --compact    # p/ pipe

# ── API (disparo pelo app) ──
waitress-serve --listen=0.0.0.0:8077 api:app                 # subir (produção)
python api.py                                                # subir (dev)
curl -X POST http://localhost:8077/sync/ordens-servico/84080 -H "X-API-Key: SUA_CHAVE"

# ── Testes (sem credenciais) ──
pytest -q
```

---

Mais contexto e decisões de projeto: [PLANO_SYNC_ORDENS_SERVICO.md](PLANO_SYNC_ORDENS_SERVICO.md) ·
visão geral do repositório: [README.md](README.md).
