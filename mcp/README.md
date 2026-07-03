# Fachada MCP — ServidorIntegracaoSAP (Fases 0–1)

Camada **fina e read-only** que expõe, como *tools* MCP, os endpoints que a API REST
(porta 8077) já oferece. Um cliente MCP (Claude Desktop, Claude Code, o assistente Mira)
passa a operar/consultar o servidor de integração **em linguagem natural**.

> **Não altera nada do que roda hoje.** Não fala com SAP/SQL/Supabase direto, não roda
> agendador — só chama a API existente (que continua sendo a única a tocar o banco).

## Tools (leitura)

| Tool | Endpoint | Chave? | Fase |
|---|---|---|---|
| `verificar_saude(checks?, strict?)` | `GET /status` | não (aberto) | 0 |
| `listar_sincronizacoes_os(limit?)` | `GET /historico` | sim | 0 |
| `listar_sincronizacoes_oportunidades(limit?)` | `GET /oportunidades/historico` | sim | 0 |
| `info_oportunidades()` | `GET /oportunidades/info` | sim | 0 |
| `listar_pedidos_com_os(limit?)` | `GET /ordens-servico/disponiveis` | sim | 0 |
| `detalhe_pedido_os(nped, incluir_linhas?)` | `GET /ordens-servico/<nped>` | sim | 1 |
| `estado_tarefa_wbc()` | `GET /status?checks=scheduled_task` | não (aberto) | 1 |
| `ultimos_erros(limit?)` | `GET /historico` (filtra falhas) | sim | 1 |

## Resources (Fase 1 — contexto anexável)

Recursos que o cliente MCP lê como "arquivos de contexto", **sem gastar uma tool-call**:

| Resource (URI) | Conteúdo |
|---|---|
| `sap-integracao://status` | snapshot do `/status` (JSON) |
| `sap-integracao://historico-os` | últimas 20 sincronizações de OS (JSON) |

Ações de escrita (sincronizar pedido, forçar carga) ficam para a **Fase 2**, com confirmação humana.

## Onde roda (topologia recomendada — Opção A)

**Do lado do cliente** (a máquina onde o Claude Desktop/Code roda), alcançando a `8077`
pela LAN. Assim **não há exposição nova** no servidor `.11` e a `SIS_API_KEY` fica só aqui.

## Instalação

```bash
cd mcp
python -m venv .venv && .venv\Scripts\activate      # Windows (ou: source .venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env                               # e preencha SIS_API_KEY
```

Teste rápido (o server fica aguardando no stdio; Ctrl+C para sair):

```bash
python mcp_server.py
```

## Registrar no cliente MCP

### Claude Code (CLI)

```bash
claude mcp add servidor-integracao-sap -- python D:\ProjetoAltamira\ServidorIntegracaoSAP\mcp\mcp_server.py
```

> Use o Python do venv se criou um (ex.: `...\mcp\.venv\Scripts\python.exe`).

### Claude Desktop

Edite `claude_desktop_config.json` (Windows: `%APPDATA%\Claude\claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "servidor-integracao-sap": {
      "command": "python",
      "args": ["D:\\ProjetoAltamira\\ServidorIntegracaoSAP\\mcp\\mcp_server.py"],
      "env": {
        "SIS_API_BASE": "http://192.168.7.11:8077",
        "SIS_API_KEY": "COLOQUE_A_CHAVE_AQUI"
      }
    }
  }
}
```

Reinicie o Claude Desktop. Depois é só perguntar: *"o servidor de integração está saudável?"*,
*"últimas sincronizações de OS?"*, *"quais pedidos têm OS disponível?"*, *"mostra a OS do pedido 84080"*,
*"a tarefa WBC rodou hoje?"*, *"teve falha de sync hoje?"*.

## Modo remoto (Fase 3 — serviço HTTP na `.11`)

Em vez do stdio-por-cliente (acima), a fachada pode rodar como **serviço HTTP central na
`.11`** (porta **8078**), e os clientes apontam para **uma URL só**, autenticando com um
**token estático**. Plano completo em [PLANO_FASE3.md](PLANO_FASE3.md).

**Entrypoint:** [serve_http.py](serve_http.py) — serve o mesmo FastMCP via *Streamable HTTP*
(uvicorn) atrás de um middleware que exige `Authorization: Bearer <SIS_MCP_TOKEN>`.

**Config no `mcp/.env` da `.11`:**

```
SIS_API_BASE=http://127.0.0.1:8077   # loopback: a OS_API_KEY nunca sai do servidor
SIS_API_KEY=<a OS_API_KEY>
SIS_MCP_TOKEN=<token forte p/ os clientes>
SIS_MCP_HOST=0.0.0.0
SIS_MCP_PORT=8078
```

**Subir** (na `.11`): `run_mcp.bat` diretamente, ou como serviço via `install_mcp_service.bat`
(NSSM `OrcaView-MCP`, boot automático). Libere a porta no firewall — **restringindo por IP**:

```bat
netsh advfirewall firewall add rule name="OrcaView MCP 8078" dir=in action=allow ^
  protocol=TCP localport=8078 remoteip=192.168.0.90,<ip-do-cliente>
```

**Registrar o cliente (transporte HTTP):**

```bash
claude mcp add --transport http servidor-integracao-sap \
  http://192.168.7.11:8078/mcp \
  --header "Authorization: Bearer <SIS_MCP_TOKEN>" --scope user
```

> Vantagem: a `OS_API_KEY` passa a viver **só na `.11`** (o MCP chama a API por loopback),
> em vez de estar no `.env` de cada cliente.

## Segurança

- **Saída (MCP → API):** a `SIS_API_KEY` fica **no servidor MCP**, injetada server-side no
  header `X-API-Key` — **nunca** é enviada ao modelo. No modo remoto (Fase 3), roda na `.11`
  e chama a API por `127.0.0.1`, então a chave **não trafega na LAN**.
- **Entrada (cliente → MCP, só no modo remoto):** `Authorization: Bearer <SIS_MCP_TOKEN>`.
  Sobre HTTP puro na LAN o token vai em cleartext — **restrinja a porta 8078 por IP** no
  firewall (TLS via reverse-proxy fica como hardening futuro).
- Fases 0–1 são **100% leitura**. Nenhuma tool dispara ação no SAP (escrita = Fase futura,
  com confirmação humana).
