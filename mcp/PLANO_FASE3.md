# Fase 3 da Fachada MCP — servidor remoto (HTTP + token) na `.11`

> **Status (2026-07-03):** o **código está IMPLEMENTADO e testado localmente** (§3). Falta a
> **operação na `.11`** (§4) e o **registro dos clientes** (§6). `web_orcaview_V117` (.90): **nada** (§8).
>
> **Objetivo:** a fachada MCP vira um **serviço HTTP central na `.11:8078`** com **token Bearer**.
> Vários clientes-LLM apontam para uma URL só, e a `OS_API_KEY` **nunca sai do servidor**.

---

## 1. Topologia — de Opção A para Opção B

```
[seu micro] Claude ─┐
[.90 / outros] ─────┤ HTTP + Bearer <SIS_MCP_TOKEN>
[futuros LLM] ──────┘        │
                             ▼
                 ┌───────────────────────────┐
                 │  .11  OrcaView-MCP :8078   │  serve_http.py (uvicorn + middleware Bearer)
                 └───────────┬───────────────┘
                             │ X-API-Key via LOOPBACK 127.0.0.1:8077
                             ▼
                 ┌───────────────────────────┐
                 │  .11  OrcaView-OS-API :8077│  API REST — inalterada
                 └───────────────────────────┘
```

## 2. Modelo de segurança

| Fluxo | Autenticação | Onde vive o segredo |
|---|---|---|
| **Entrada** cliente → MCP (8078) | `Authorization: Bearer <SIS_MCP_TOKEN>` | token nos clientes + na `.11` |
| **Saída** MCP → API (8077) | `X-API-Key: <OS_API_KEY>` | **só na `.11`** (loopback) — nunca na LAN nem no LLM |

- **Upgrade vs Opção A:** a `OS_API_KEY` deixa de existir nos clientes; vive só na `.11` e as
  chamadas MCP→API vão por `127.0.0.1` (não trafegam na rede).
- **Decisões travadas:** **sem TLS** (rede interna); bind **`0.0.0.0:8078`** (aberto nos testes,
  depois `remoteip=` restrito — §5); sessão **default** (stateful). Token em cleartext sobre HTTP
  → mitigar restringindo a porta por IP.
- Continua **100% leitura** (Fase 1). Escrita = fase futura, com confirmação humana.

## 3. ✅ Já implementado (código no repo — testado localmente)

| Arquivo | Papel |
|---|---|
| [`serve_http.py`](serve_http.py) | entrypoint HTTP: mesmo FastMCP via *Streamable HTTP* (uvicorn) + **middleware `Authorization: Bearer`**. NÃO usa `mcp.run("streamable-http")` (sobe uvicorn interno sem hook de auth); monta `mcp.streamable_http_app()` + `uvicorn.run` e encaminha o escopo `lifespan`. |
| [`../run_mcp.bat`](../run_mcp.bat) | wrapper de serviço (espelha `run_api.bat`): cwd fixo, venv-ou-sistema, UTF-8 → `python mcp\serve_http.py`. |
| [`../install_mcp_service.bat`](../install_mcp_service.bat) | registra o serviço NSSM `OrcaView-MCP` (boot automático, `DependOnService OrcaView-OS-API`, logs rotativos). |
| `requirements.txt` | + `uvicorn`. |
| `README.md` | seção **Modo remoto (Fase 3)**. |

`mcp_server.py` (stdio) **fica intacto** — o modo local segue funcionando.

**Teste local (feito):** subindo `serve_http.py` e batendo em `/mcp` → sem token **401**,
token errado **401**, token válido **400** (passou pelo auth; 400 = handshake MCP espera POST/SSE).

## 4. 🔜 Falta — deploy na `.11` (operação do Marcelo — [[local-restart-on-request]])

1. `cd C:\Python\ServidorIntegracaoSAP` && `git pull` (traz `serve_http.py`, `run_mcp.bat`, `install_mcp_service.bat`, requirements, docs).
2. Instalar deps **no Python que o `run_mcp.bat` usa** (§7 decisão do venv): `pip install -r mcp\requirements.txt` (inclui `uvicorn`).
3. Editar `mcp\.env` (gitignored):
   ```
   SIS_API_BASE=http://127.0.0.1:8077     # loopback: a OS_API_KEY nunca sai da .11
   SIS_API_KEY=<a mesma OS_API_KEY da API>
   SIS_MCP_TOKEN=<gerar forte: python -c "import secrets;print(secrets.token_urlsafe(32))">
   SIS_MCP_HOST=0.0.0.0
   SIS_MCP_PORT=8078
   ```
4. Teste manual **antes** do serviço: `python mcp\serve_http.py` → deve logar `Uvicorn running on http://0.0.0.0:8078`. Ctrl+C.
5. `install_mcp_service.bat` (registra + inicia `OrcaView-MCP`).
6. Firewall (§5).
7. Validar (§7).

## 5. 🔜 Falta — firewall (passo manual, como a 8077)

**Nos testes (aberto):**
```bat
netsh advfirewall firewall add rule name="OrcaView MCP 8078" dir=in action=allow protocol=TCP localport=8078
```
**Depois de validar (restrito por IP — recomendado):**
```bat
netsh advfirewall firewall delete rule name="OrcaView MCP 8078"
netsh advfirewall firewall add rule name="OrcaView MCP 8078" dir=in action=allow ^
  protocol=TCP localport=8078 remoteip=192.168.0.90,<ip-do-seu-micro>
```

## 6. 🔜 Falta — registro dos clientes (transporte HTTP)

**Claude Code (CLI):**
```bash
claude mcp add --transport http servidor-integracao-sap \
  http://192.168.7.11:8078/mcp \
  --header "Authorization: Bearer <SIS_MCP_TOKEN>" --scope user
claude mcp list      # ✓ Connected
```
> Substitui o registro **stdio** local atual. Remova o antigo: `claude mcp remove servidor-integracao-sap`
> (ou use nomes diferentes p/ conviver stdio-local + http-remoto).

**Claude Desktop:** conector remoto (URL + header) nas versões recentes, ou ponte `mcp-remote`:
```json
{ "mcpServers": { "servidor-integracao-sap": {
  "command": "npx",
  "args": ["-y", "mcp-remote", "http://192.168.7.11:8078/mcp",
           "--header", "Authorization: Bearer <SIS_MCP_TOKEN>"] } } }
```

## 7. Testes & validação (na `.11`)

```powershell
nssm status OrcaView-MCP                 # SERVICE_RUNNING
netstat -ano | findstr :8078             # LISTENING
curl.exe -s -o NUL -w "%{http_code}`n" http://127.0.0.1:8078/mcp                                  # 401 (sem token)
curl.exe -s -o NUL -w "%{http_code}`n" -H "Authorization: Bearer <TOKEN>" http://127.0.0.1:8078/mcp  # != 401 (passou)
```
Depois, do seu micro: registrar o cliente (§6) e perguntar *"mostra a OS do pedido 84080"*.

**Decisão do venv (§4.2):** o `run_mcp.bat` usa `venv\Scripts\python.exe` da **raiz** se existir,
senão o Python do sistema. Garanta que `mcp/requirements.txt` (com `uvicorn`) está instalado NELE.

## 8. `web_orcaview_V117` (.90) — **NENHUMA mudança** ✅

Evidência: a `.90` consome a `.11` **só** via REST (`GET /api/oportunidade-wbc/status` → `.11:8077/status`
com `X-API-Key`; `admin_integrations_routes.py`, `config.py:254`). **Não há cliente MCP** (o `mcp` no
`requirements.lock` é dep transitiva, 0 `import mcp`); a Mira (`llm.py:50-52`) é roteador manual sobre
LM Studio e **não faz tool-calling**. A fachada MCP é camada por cima da API 8077 (não a remove).

**Opcional/futuro (fora de escopo):** um "branch SAP" na Mira chamando a **REST 8077 direto** (não MCP).

## 9. Rollback

```powershell
nssm stop OrcaView-MCP & nssm remove OrcaView-MCP confirm
netsh advfirewall firewall delete rule name="OrcaView MCP 8078"
```
A stdio (`mcp_server.py`) segue funcionando localmente — nada existente é removido.
