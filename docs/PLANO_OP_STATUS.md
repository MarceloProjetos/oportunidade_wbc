# Plano — atualizar status de Ordem de Produção (OP) via Service Layer

Endpoint novo no `ServidorIntegracaoSAP` (API 8077) que **escreve no SAP B1** mudando o
status de uma Ordem de Produção. Base de referência: o notebook `production_order_sl.ipynb`
(explora `ProductionOrders` no Service Layer) e os dois clientes SL já provados em produção
no repo irmão (`web_orcaview_V117/backend/services/sap_service_layer.py` e
`compras_sap_service.py`).

**Este é o primeiro caminho de ESCRITA em SAP deste repositório.** Hoje o serviço só lê
(HANA via `hdbcli`) e escreve no Supabase. Todo o plano é construído em torno desse fato.

---

## 0. Decisões travadas (2026-08-07, com o Marcelo)

| Decisão | Valor | Consequência |
| --- | --- | --- |
| Transições aceitas | **Liberar** (`boposReleased`) e **Encerrar** (`boposClosed`) | `boposCancelled` e `boposPlanned` ficam FORA — recusados antes de qualquer chamada ao SAP |
| Identificação da OP | **DocNum e DocEntry** (os dois) | DocNum é resolvido para DocEntry por `$filter` antes do PATCH |
| CompanyDB | **`SBOALTAMIRAPROD`** (produção) | Sem etapa de homolog — daí o kill switch e o pré-voo controlado da Fase 3 |
| Escopo extra | **GET de consulta da OP** | Ficam de fora: lote por Pedido de Venda, log de auditoria no Supabase, tool MCP |

Pré-voo respondido no mesmo dia: a .11 **alcança** `sapbusinessonehana-vm:50000`; o usuário
é o **`financeiro04`**; `requests` foi **pinado**; a OP de teste da Fase 3 é a **129850**.

**Estado: F0, F1, F2 e F4 entregues** (378 testes verdes, ruff em 0). Falta só a **Fase 3**
— o pré-voo em produção, que é com o Marcelo.

---

## 1. Riscos e como cada um é fechado

| Risco | Por que é real aqui | Fechamento |
| --- | --- | --- |
| Mexer em OP de produção por engano | O alvo é `SBOALTAMIRAPROD` desde o dia 1 | `OP_SL_ENABLED` nasce **`false`**. Sem ligar explicitamente no `.env` da .11, a rota devolve 503 e não abre socket |
| Endpoint de escrita aberto | `OS_API_KEY` vazia hoje deixa a API **aberta** (documentado no `api.py`) | A rota de escrita é **fail-closed**: sem `OS_API_KEY` configurada ela devolve **503**, não 200. É a única rota do repo com esse comportamento, e é de propósito |
| Transição inválida / OP terminal | Encerrada e Cancelada são terminais no B1; o SL devolve erro cru e ilegível | Máquina de estados validada **no nosso lado** (§4), com pré-check por GET. O erro do SAP vira fallback, não a primeira linha de defesa |
| Lost update (dois chamadores na mesma OP) | Tela + agente + pessoa podem disparar junto | *Compare-and-swap* opcional (`status_atual` no body) → 409 se divergir. Mais lock de escrita serializando os PATCHes |
| Vazar sessão do Service Layer | O SL tem limite finito de sessões concorrentes; vazar derruba o SL **para todo mundo**, inclusive o cliente B1 | Sessão compartilhada com TTL + relogin em 401/"Invalid session" + logout no shutdown. Nunca um login por request |
| SAP fora do ar travando a API | Sem `timeout=` explícito, `requests` espera **para sempre** — foi o que congelou o servidor web quando o SAP caiu | `TimeoutSession` (connect 5s / read 30s) injetando timeout em **toda** request, inclusive as que forem escritas depois |
| Loop de agente/retry batendo no SAP | Já aconteceu no repo (daí os rate limits existentes) | Bucket de rate limit próprio (`op_status`, default 20/min) + retry **só** no login, nunca no PATCH (erro de PATCH é determinístico) |
| Credencial em texto claro | O notebook tem `financeiro04` / `sap@123` apontando para **produção**, salvo em `~/Downloads` | Credencial só via `.env` (gitignored). Ver §6 — **recomendo rotacionar essa senha** |
| `verify=False` sem rastro | O notebook faz `urllib3.disable_warnings()` no import, e o fato de o TLS estar desligado some | `verify` vira env (`OP_SL_VERIFY_SSL`, default `false` pelo cert self-signed interno). A supressão do aviso **continua global** — o urllib3 não tem chave por sessão — mas acontece **tarde** (no 1º login) e **só depois** de um WARNING nomeando o host ir para o log |

---

## 2. Arquivos

| Arquivo | Ação | Conteúdo |
| --- | --- | --- |
| `ordens_producao_sl.py` | **novo** (~280 linhas) | Módulo único: sessão SL própria + domínio (resolver OP, máquina de estados, GET, PATCH). Auto-contido e **diffável com `compras_sap_service.py`** do repo irmão |
| `config.py` | alterar | Bloco `OP_SL_*` no `Settings` + `op_sl_ready()` |
| `api.py` | alterar | 2 rotas + bucket de rate limit + `_op_lock` |
| `.env.example` | alterar | Bloco novo comentado, com o kill switch em destaque |
| `tests/test_ordens_producao_sl.py` | **novo** | ~26 testes, offline |
| `tests/test_api.py` | alterar | ~17 testes de rota |
| `tests/test_config.py` | alterar | defaults + `op_sl_ready()` |
| `requirements.txt` | alterar | `requests` pinado (ver §9) |
| `API_OS_INTEGRACAO.md`, `README.md`, `CHANGELOG.md`, `CLAUDE.md` | alterar | Contrato + gotchas |

Um módulo só (e não um cliente SL genérico + um de domínio) porque não existe outro
consumidor de SL neste repo — a regra dos "2 arquivos por tarefa" do `CLAUDE.md` vale mais
que a reutilização hipotética. Se aparecer um segundo consumidor, aí sim se extrai a sessão.

---

## 3. Contrato HTTP

### `GET /ordens-producao/<numero>` — consulta

```
GET /ordens-producao/125060                  # DocNum (default)
GET /ordens-producao/126599?chave=docentry   # DocEntry
```

Exige `X-API-Key`. Não escreve nada, não depende de `OS_API_KEY` estar setada (segue a
regra das outras leituras). Resposta:

```json
{
  "ok": true,
  "op": {
    "doc_entry": 126599,
    "doc_num": 125060,
    "item": "PAR000PADRA000000000",
    "quantidade_planejada": 36.0,
    "status": "boposReleased",
    "status_desc": "Liberada",
    "origem": "bopooSalesOrder",
    "origem_numero": 83871,
    "data_entrega": "2026-08-20",
    "transicoes_permitidas": ["encerrada"]
  }
}
```

`transicoes_permitidas` é o que faz a tela conseguir desabilitar o botão errado **antes**
do usuário clicar.

### `POST /ordens-producao/<numero>/status` — escrita

```json
{ "status": "encerrada", "status_atual": "liberada" }
```

- `status` (obrigatório): `"liberada"` / `"encerrada"`, ou o código cru
  (`boposReleased` / `boposClosed`). Qualquer outro valor → 400.
- `status_atual` (opcional): *compare-and-swap*. Se o status real no SAP não for esse,
  responde 409 sem tocar em nada. Recomendado para chamadas vindas de tela.
- `?chave=docentry` também vale aqui.

Resposta de sucesso:

```json
{
  "ok": true, "doc_entry": 126599, "doc_num": 125060,
  "status_anterior": "boposReleased", "status_novo": "boposClosed",
  "ja_estava": false
}
```

### Códigos

| Código | Quando |
| --- | --- |
| `200` | Mudou, **ou** já estava no alvo (`ja_estava: true`, e nenhum PATCH foi enviado) |
| `400` | Número inválido · `status` ausente/desconhecido · status fora da allowlist |
| `401` | `X-API-Key` faltando/errada |
| `404` | OP não existe no SAP |
| `409` | OP terminal (Encerrada/Cancelada) · `status_atual` divergente · outra requisição mexendo na mesma OP |
| `413` | (não se aplica — não há lote nesta entrega) |
| `429` | Rate limit, com header `Retry-After` |
| `502` | SL indisponível, login recusado, ou o SAP recusou o PATCH |
| `503` | `OP_SL_ENABLED=false` · credenciais SL ausentes · `OS_API_KEY` não configurada |

Todo `motivo` de erro sai **sem acento**, como o resto da API (`_sync_one` já segue essa
regra: legível em qualquer console sem depender de escape `\uXXXX`).

---

## 4. Máquina de estados

Validada no nosso lado, com o status atual vindo do GET de pré-check (o mesmo GET que
resolve DocNum → DocEntry — **um** round-trip, não dois).

| Status atual | → `liberada` | → `encerrada` |
| --- | --- | --- |
| `boposPlanned` (Planejada) | ✅ PATCH | ✅ PATCH |
| `boposReleased` (Liberada) | ✅ 200 `ja_estava`, **sem PATCH** | ✅ PATCH |
| `boposClosed` (Encerrada) | ⛔ 409 terminal | ✅ 200 `ja_estava`, **sem PATCH** |
| `boposCancelled` (Cancelada) | ⛔ 409 terminal | ⛔ 409 terminal |

Três invariantes que os testes cravam:

1. **Nada sai de um estado terminal.** Encerrada e Cancelada só respondem `ja_estava` para
   elas mesmas.
2. **Idempotência sem efeito colateral.** Alvo == atual devolve 200 e **não** manda PATCH —
   repetir a chamada é seguro por construção, não por sorte.
3. **A allowlist é checada antes da rede.** Pedir `boposCancelled` falha em 400 sem abrir
   conexão com o SAP.

---

## 5. Configuração nova (`.env`)

```ini
# --- Ordem de Producao: escrita de status via SAP Service Layer ---
# ATENCAO: aponta para PRODUCAO (SBOALTAMIRAPROD). Nasce DESLIGADO de proposito.
OP_SL_ENABLED=false                 # kill switch. false => a rota devolve 503 sem abrir socket
OP_SL_SERVER=sapbusinessonehana-vm
OP_SL_PORT=50000
OP_SL_COMPANY_DB=SBOALTAMIRAPROD
OP_SL_USERNAME=
OP_SL_PASSWORD=                     # OBRIGATORIA quando OP_SL_ENABLED=true
OP_SL_VERIFY_SSL=false              # cert self-signed do SL interno; true quando houver cert valido
# OP_SL_TIMEOUT_CONNECT_S=5
# OP_SL_TIMEOUT_READ_S=30
# OP_SL_SESSION_TTL_S=2700          # 45 min; o SL expira sozinho e o cliente se recupera antes
# OP_STATUS_PERMITIDOS=boposReleased,boposClosed   # allowlist; abrir aqui e decisao explicita
# RATE_OP_STATUS_MAX=20             # escritas de status por minuto (trava anti-loop)
```

`op_sl_ready()` = `enabled and server and company_db and username and password`. Diferente
do `config.py` de hoje do repo irmão, **não** levanta `ValueError` no import se a senha
faltar — isso derrubaria a API inteira (`/health` incluído) por causa de uma feature
opcional. Falta de credencial vira 503 na rota, não serviço morto.

---

## 6. Segurança

**O que já está fechado pelo desenho:**

- Kill switch desligado por default (§5).
- Rota de escrita fail-closed sem `OS_API_KEY` (§1).
- Allowlist de status conferida antes da rede (§4).
- Timeout em toda request; sem ele o SAP caído congela threads do waitress.
- Sessão SL compartilhada com TTL — nunca um login por request.
- O payload de `/Login` **nunca** vai para o log. Erro de login loga o status e a mensagem
  do SAP, jamais usuário/senha.
- `urllib3.disable_warnings` escopado ao módulo (o notebook desliga global).
- Rate limit próprio, separado dos buckets existentes.

**O que preciso de você:**

1. **Rotacionar a senha do `financeiro04`.** Ela está em texto claro no
   `production_order_sl.ipynb` em `~/Downloads`, apontando para **produção**, e foi
   compartilhada nesta conversa. O mesmo usuário é o default do `SL_USERNAME` no
   `web_orcaview_V117/backend/config.py` — a rotação precisa ser coordenada com o `.env`
   do .90, senão derruba o Compras e a criação de oportunidade.
2. **Usuário SL dedicado** (ex.: `integracao_op`) com permissão só de Ordem de Produção,
   em vez do `financeiro04`. Um usuário de escrita compartilhado entre módulos torna
   impossível saber, no log do SAP, quem mudou a OP.
3. **Não commitar o notebook** neste repo com as credenciais dentro.

---

## 7. Testes

Todos **offline** — nenhum teste abre socket. A suíte hoje roda sem rede e continua assim.
Padrão: `monkeypatch` numa `requests.Session` falsa que grava as chamadas recebidas
(mesmo estilo do `client` fixture do `tests/test_api.py`, que mocka `sync_os`).

### `tests/test_ordens_producao_sl.py` (~26)

**Sessão e transporte**
1. login monta `{CompanyDB, UserName, Password}` corretos
2. a senha **não** aparece em nenhum registro de log
3. N requests → **1** login (sessão reusada)
4. TTL vencido → relogin
5. HTTP 401 → relogin automático + replay da request original
6. corpo com `"Invalid session"` e status ≠ 401 → também relogin (erro SL 301)
7. relogin no replay falha → erro tipado, **1** replay só, sem loop
8. login falha 3× → erro de autenticação, com backoff (`time.sleep` mockado)
9. `timeout` default injetado em **toda** request (GET, PATCH e futuras)
10. erro aninhado `{error:{message:{value}}}` extraído
11. corpo não-JSON → não estoura; devolve texto truncado
12. `verify` respeita `OP_SL_VERIFY_SSL`

**Resolução e domínio**
13. DocNum → DocEntry por `$filter=DocumentNumber eq N`
14. DocNum inexistente → "não encontrada" (vira 404), sem PATCH
15. DocNum com **mais de um** resultado → erro explícito (não pega `[0]` calado)
16. `chave=docentry` vai direto no `ProductionOrders(N)`, sem `$filter`
17. resposta usa `AbsoluteEntry`, com fallback para `DocEntry` (o notebook mostra os dois)

**Máquina de estados** (a tabela de §4, uma asserção por célula)
18. Planejada → liberada: PATCH enviado
19. Planejada → encerrada: PATCH enviado
20. Liberada → encerrada: PATCH enviado
21. Liberada → liberada: 200 `ja_estava`, **zero** PATCH
22. Encerrada → liberada: bloqueado (terminal)
23. Cancelada → encerrada: bloqueado (terminal)
24. alvo `boposCancelled` → recusado **sem nenhuma** chamada HTTP
25. `OP_STATUS_PERMITIDOS` do env é respeitada (tirar `boposReleased` bloqueia liberar)
26. compare-and-swap divergente → recusa, **zero** PATCH

**Guardas**
27. `OP_SL_ENABLED=false` → recusa antes de qualquer rede
28. credencial faltando → erro claro, sem tentar conectar
29. PATCH recusado pelo SAP → erro mapeado e **sem retry** (não é transitório)
30. PATCH 204 e PATCH 200 ambos contam como sucesso

### `tests/test_api.py` (~17 novos)

31. `GET /ordens-producao/125060` → 200 com `op.status_desc`
32. GET traz `transicoes_permitidas` coerente com o status
33. GET número inválido (`0`, `-1`, `abc`) → 400 (via `coerce_positive_int`)
34. GET OP inexistente → 404
35. GET sem `X-API-Key` (com chave configurada) → 401
36. `POST .../status` `{"status":"encerrada"}` → 200
37. POST aceita o código cru `boposClosed`
38. POST sem `X-API-Key` → 401
39. **POST sem `OS_API_KEY` configurada → 503** (fail-closed; a regra que só esta rota tem)
40. POST body vazio / sem `status` → 400
41. POST `{"status":"congelada"}` → 400
42. POST `{"status":"cancelada"}` → 400 (fora da allowlist)
43. POST em OP encerrada → 409
44. POST com `status_atual` divergente → 409
45. POST estourando o bucket → 429 com `Retry-After`
46. POST com o SL fora do ar → 502
47. POST idempotente → 200 `ja_estava: true`
48. `OP_SL_ENABLED=false` → 503 nas duas rotas
49. `?chave=docentry` chega no módulo como DocEntry

A varredura `test_toda_rota_nova_exige_chave_ou_e_abertura_declarada`
(`tests/test_api.py:785`) já cobre as duas rotas novas automaticamente — se eu esquecer o
`@requer_chave`, ela quebra sozinha. Não precisa de teste novo para isso.

---

## 8. Fases

| Fase | Entrega | Estado |
| --- | --- | --- |
| **F0** | `config.py` + `.env.example`, tudo desligado | ✅ 9 testes de config |
| **F1** | `ordens_producao_sl.py` + `test_ordens_producao_sl.py` | ✅ 56 testes offline |
| **F2** | rotas em `api.py` + `test_api.py` | ✅ suíte inteira: **378 verdes**, ruff em 0 |
| **F3** | **pré-voo em produção** | ⏳ **com o Marcelo** — ver §9 |
| **F4** | docs: `README.md`, `CHANGELOG.md`, `CLAUDE.md`, `.env.example` | ✅ |

F0-F2 são 100% offline e não tocaram em produção. **F3 é a única fase que escreve no SAP.**

---

## 9. Fase 3 — pré-voo em produção (pendente)

Nada abaixo roda sem você. A OP escolhida é a **129850**.

1. Deploy na .11: `git pull` **+ `pip install -r requirements.txt`** (o `requests` virou
   dependência direta — só o pull não basta).
2. No `.env` da .11: `OP_SL_ENABLED=true`, `OP_SL_USERNAME=financeiro04`,
   `OP_SL_PASSWORD=...`. Conferir que `OS_API_KEY` está definida — sem ela o POST responde
   503 de propósito.
3. Restart do serviço `OrcaView-OS-API`. O log deve trazer o WARNING
   `ESCRITA de status de Ordem de Producao LIGADA — base SBOALTAMIRAPROD ...`. Se ele não
   aparecer, a feature não subiu.
4. **Ler antes de escrever:**
   ```bash
   curl "http://192.168.7.11:8077/ordens-producao/129850" -H "X-API-Key: SUA_CHAVE"
   ```
   Confirmar `doc_num`, `item` e `status_desc` contra a tela do SAP. Anotar o `doc_entry` —
   ele é diferente do 129850.
5. **Primeira escrita**, com compare-and-swap (troque `status_atual` pelo que o passo 4
   devolveu):
   ```bash
   curl -X POST "http://192.168.7.11:8077/ordens-producao/129850/status" \
        -H "X-API-Key: SUA_CHAVE" -H "Content-Type: application/json" \
        -d '{"status":"liberada","status_atual":"planejada"}'
   ```
   Conferir **na tela do SAP** antes de seguir.
6. Repetir o mesmo comando: tem de voltar `200` com `ja_estava: true` — é a prova da
   idempotência em produção.
7. `{"status":"encerrada","status_atual":"liberada"}` e conferir na tela de novo.
8. Tentar `{"status":"liberada"}` na OP já encerrada: tem de voltar **409**.

**Rollback a qualquer momento:** `OP_SL_ENABLED=false` no `.env` + restart. As rotas voltam
a responder 503 e nada mais é escrito.

Depois do pré-voo, decidir com calma se o `OP_SL_ENABLED` fica ligado permanentemente ou só
quando houver consumidor.

---

## 10. Fora de escopo (registrado para não se perder)

- Lote por Pedido de Venda (`get_open_production_orders_by_sales_order` do notebook →
  encerrar todas as OPs de um N_PED de uma vez).
- Log de auditoria no Supabase (quem/quando/de-para). Por ora o rastro é só o
  `logs/api.log`.
- Tool MCP na fachada 8078 (escrita via LLM exigiria confirmação humana explícita).
- `boposCancelled` e volta para `boposPlanned`.
- Criar OP (`POST /ProductionOrders`) — o notebook tem, o plano não.
