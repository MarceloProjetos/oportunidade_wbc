"""Fachada MCP (Fase 0) do ServidorIntegracaoSAP — camada FINA e READ-ONLY.

O que é: um servidor MCP (stdio) que expõe, como *tools*, os endpoints que a API
REST do servidor de integração (porta 8077) já oferece. Um cliente MCP (Claude
Desktop, Claude Code, o assistente Mira) pode então consultar o servidor em
linguagem natural: "o servidor de integração está saudável?", "últimas
sincronizações?", "pedidos com OS disponíveis?".

O que NÃO é: não reimplementa lógica, não fala com SAP/SQL/Supabase direto, não
roda agendador. Cada tool apenas chama um endpoint HTTP existente. Quem fala com o
banco continua sendo a API (service_role), exatamente como hoje.

Fase 0 = fundação + tools de LEITURA. Ações de escrita (sincronizar pedido, forçar
carga de oportunidades) ficam para a Fase 2, com confirmação humana.

Config (via ambiente ou .env ao lado deste arquivo):
    SIS_API_BASE   URL base da API. Default http://192.168.7.11:8077
    SIS_API_KEY    A OS_API_KEY do servidor de integração (fica AQUI, no server MCP,
                   nunca vai para o LLM). Sem ela, só o /status (aberto) funciona.

Rodar: pip install -r requirements.txt && python mcp_server.py
Registrar no cliente MCP: ver README.md.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

import httpx
from mcp.server.fastmcp import FastMCP

try:
    # Carrega um .env ao lado deste arquivo, se python-dotenv estiver instalado.
    from dotenv import load_dotenv

    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

API_BASE = os.environ.get("SIS_API_BASE", "http://192.168.7.11:8077").rstrip("/")
API_KEY = os.environ.get("SIS_API_KEY", "").strip()
HTTP_TIMEOUT = float(os.environ.get("SIS_HTTP_TIMEOUT", "12"))

mcp = FastMCP("ServidorIntegracaoSAP")


def _get(path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """GET num endpoint da API, injetando a X-API-Key server-side.

    Devolve o JSON decodificado. Em qualquer falha (rede, HTTP != 2xx, corpo não
    JSON) devolve ``{"ok": False, "erro": "..."}`` — a tool nunca estoura exceção
    para o cliente MCP, para o modelo receber um erro legível em vez de um crash.
    """
    url = f"{API_BASE}{path}"
    headers = {"X-API-Key": API_KEY} if API_KEY else {}
    try:
        # trust_env=False: NÃO honra proxy do ambiente (HTTP_PROXY/ALL_PROXY/etc). A fachada
        # só fala com a API interna (loopback/LAN); um proxy corporativo herdado pelo serviço
        # (LocalSystem) rotearia até a chamada de 127.0.0.1 pelo proxy → WinError 10061
        # (connection refused) mesmo com a API no ar. Um shell interativo sem proxy funciona.
        resp = httpx.get(url, params=params, headers=headers, timeout=HTTP_TIMEOUT,
                         trust_env=False)
    except httpx.RequestError as exc:
        return {"ok": False, "erro": f"servidor de integração inacessível ({API_BASE}): {exc}"}

    if resp.status_code == 401:
        return {"ok": False, "erro": "não autorizado (401) — SIS_API_KEY ausente ou incorreta"}
    if resp.status_code >= 400:
        # Se a API devolveu um JSON estruturado (ex.: 404 {"ok": false, "error": "pedido sem OS
        # sincronizada"}), repassa-o — o modelo recebe a mensagem real em vez de um "HTTP 404"
        # genérico. Fallback: erro genérico (ex.: 404 HTML do Flask = rota inexistente = servidor
        # de integração ainda não atualizado com o endpoint).
        try:
            body = resp.json()
            if isinstance(body, dict):
                return body
        except ValueError:
            pass
        return {"ok": False, "erro": f"HTTP {resp.status_code} em {path}", "corpo": resp.text[:300]}

    try:
        return resp.json()
    except ValueError:
        return {"ok": False, "erro": f"resposta não-JSON de {path}", "corpo": resp.text[:300]}


@mcp.tool()
def verificar_saude(checks: str = "", strict: bool = False) -> Dict[str, Any]:
    """Diagnóstico de saúde do servidor de integração SAP/WBC (endpoint /status, aberto).

    Retorna conexões (SAP HANA, SQL Server/WBC, Supabase, com latência), o sinal do
    agendador de oportunidades, o estado da tarefa agendada "Integração WBC"
    (scheduled_task) e métricas de sistema (CPU/memória/disco). Use para responder
    "o servidor de integração está saudável?" ou "algum alerta agora?".

    Args:
        checks: subconjunto opcional de checagens (ex.: "sap,sql,tarefa"). Vazio = todas.
        strict: se True, o /status devolve 503 quando degradado (a tool ainda mostra o corpo).
    """
    params: Dict[str, Any] = {}
    if checks:
        params["checks"] = checks
    if strict:
        params["strict"] = 1
    return _get("/status", params or None)


@mcp.tool()
def listar_sincronizacoes_os(limit: int = 20) -> Dict[str, Any]:
    """Últimas sincronizações de Ordens de Serviço (Engenharia) por NPED (endpoint /historico).

    Requer a SIS_API_KEY configurada no server MCP. Use para "teve algum sync de OS
    com falha hoje?" ou "quais os últimos pedidos sincronizados?".

    Args:
        limit: quantos registros trazer (1–100). Default 20.
    """
    return _get("/historico", {"limit": max(1, min(int(limit), 100))})


@mcp.tool()
def listar_sincronizacoes_oportunidades(limit: int = 20) -> Dict[str, Any]:
    """Últimos sincronismos do pipeline de oportunidades (endpoint /oportunidades/historico).

    Requer a SIS_API_KEY. Use para inspecionar a carga agendada de oportunidades
    (status, quantidade, duração, horário).

    Args:
        limit: quantos registros trazer (1–100). Default 20.
    """
    return _get("/oportunidades/historico", {"limit": max(1, min(int(limit), 100))})


@mcp.tool()
def info_oportunidades() -> Dict[str, Any]:
    """Contexto do pipeline de oportunidades (endpoint /oportunidades/info): total de
    linhas na tabela + agenda (intervalo em minutos e janela comercial). Requer a SIS_API_KEY."""
    return _get("/oportunidades/info")


@mcp.tool()
def listar_pedidos_com_os(limit: int = 30) -> Dict[str, Any]:
    """Lista pedidos (NPED) que já têm Ordem de Serviço criada no SAP, com cliente e data
    (endpoint /ordens-servico/disponiveis). Requer a SIS_API_KEY. Use para descobrir quais
    pedidos podem ser sincronizados.

    Args:
        limit: quantos pedidos trazer (1–50). Default 30.
    """
    return _get("/ordens-servico/disponiveis", {"limit": max(1, min(int(limit), 50))})


# ─────────────────────────── Fase 1 — mais leituras ───────────────────────────

@mcp.tool()
def detalhe_pedido_os(nped: int, incluir_linhas: bool = False) -> Dict[str, Any]:
    """Detalhe da OS de UM pedido: resumo com cliente, status (+ descrição), total, nº de
    linhas e de OPs, e quando foi sincronizado pela última vez. Requer a SIS_API_KEY.

    Devolve ``{"ok": false, "error": "pedido sem OS sincronizada"}`` se o pedido ainda não
    foi sincronizado (use `listar_pedidos_com_os` p/ ver os disponíveis, ou peça a sincronização).

    Args:
        nped: número do pedido (ex.: 84080).
        incluir_linhas: se True, traz também as linhas da OS (colunas enxutas, sem os textos NCLOB).
    """
    params = {"linhas": 1} if incluir_linhas else None
    return _get(f"/ordens-servico/{int(nped)}", params)


@mcp.tool()
def estado_tarefa_wbc() -> Dict[str, Any]:
    """Estado só da tarefa agendada "Integração WBC" (bloco scheduled_task do /status).

    Foca no monitor da tarefa do Windows: última execução, resultado e se rodou no prazo.
    Endpoint aberto (não exige chave). Use para "a tarefa WBC rodou hoje?" / "deu erro?".
    """
    data = _get("/status", {"checks": "scheduled_task"})
    # No /status, scheduled_task é chave de TOPO (irmã de `checks`/`alerts`), não fica dentro
    # de `checks` — isola o bloco da tarefa + os alertas relacionados.
    if isinstance(data, dict) and "scheduled_task" in data:
        return {"ok": data.get("ok", True), "scheduled_task": data["scheduled_task"],
                "alerts": data.get("alerts", [])}
    return data


@mcp.tool()
def ultimos_erros(limit: int = 10) -> Dict[str, Any]:
    """Só as sincronizações de OS que FALHARAM, dentre as últimas execuções (filtra o /historico).
    Requer a SIS_API_KEY. Use para "teve falha de sync hoje?" sem ler o histórico inteiro.

    Args:
        limit: quantos registros recentes do histórico examinar (1–100). Default 10.
    """
    data = _get("/historico", {"limit": max(1, min(int(limit), 100))})
    if not isinstance(data, dict) or "items" not in data:
        return data  # repassa o erro do _get (rede, 401, etc.)
    itens = data.get("items") or []
    falhas = [i for i in itens
              if str(i.get("status", "")).strip().lower() not in ("sucesso", "ok", "success")]
    return {"ok": True, "examinados": len(itens), "qtd_falhas": len(falhas), "falhas": falhas}


# ── Resources: contexto de LEITURA que o cliente anexa sem gastar uma tool-call por vez ──

@mcp.resource("sap-integracao://status", mime_type="application/json")
def recurso_status() -> str:
    """Snapshot atual do /status (saúde de SAP/SQL/Supabase, agendador, tarefa WBC, sistema)."""
    return json.dumps(_get("/status"), ensure_ascii=False, indent=2)


@mcp.resource("sap-integracao://historico-os", mime_type="application/json")
def recurso_historico_os() -> str:
    """Snapshot das últimas 20 sincronizações de OS (/historico). Requer a SIS_API_KEY."""
    return json.dumps(_get("/historico", {"limit": 20}), ensure_ascii=False, indent=2)


if __name__ == "__main__":
    # Transporte stdio (padrão) — é como Claude Desktop / Claude Code conectam.
    mcp.run()
