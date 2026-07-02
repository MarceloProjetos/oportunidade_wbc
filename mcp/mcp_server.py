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
        resp = httpx.get(url, params=params, headers=headers, timeout=HTTP_TIMEOUT)
    except httpx.RequestError as exc:
        return {"ok": False, "erro": f"servidor de integração inacessível ({API_BASE}): {exc}"}

    if resp.status_code == 401:
        return {"ok": False, "erro": "não autorizado (401) — SIS_API_KEY ausente ou incorreta"}
    if resp.status_code >= 400:
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


if __name__ == "__main__":
    # Transporte stdio (padrão) — é como Claude Desktop / Claude Code conectam.
    mcp.run()
