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

## Segurança

- A `SIS_API_KEY` fica **no servidor MCP**, injetada server-side no header `X-API-Key`
  — **nunca** é enviada ao modelo.
- Fase 0 é **100% leitura**. Nenhuma tool dispara ação no SAP.
