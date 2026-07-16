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
from mcp.types import ToolAnnotations

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


def _post(path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """POST num endpoint da API (ESCRITA), injetando a X-API-Key server-side.

    Mesmo tratamento de erro do ``_get`` (nunca estoura exceção; repassa corpo JSON de erro,
    ex.: 409 ``{"ok": false, "tipo": "ocupado"}`` da carga de oportunidades).
    """
    url = f"{API_BASE}{path}"
    headers = {"X-API-Key": API_KEY} if API_KEY else {}
    try:
        resp = httpx.post(url, params=params, headers=headers, timeout=HTTP_TIMEOUT, trust_env=False)
    except httpx.RequestError as exc:
        return {"ok": False, "erro": f"servidor de integração inacessível ({API_BASE}): {exc}"}

    if resp.status_code == 401:
        return {"ok": False, "erro": "não autorizado (401) — SIS_API_KEY ausente ou incorreta"}
    if resp.status_code >= 400:
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
    linhas e de OPs, datas de entrega/liberação, observação do pedido, e quando foi
    sincronizado pela última vez. Requer a SIS_API_KEY.

    Responde também **por quais processos o pedido passa**: o bloco ``resumo.processos``
    traz ``{"solda"|"pintura"|"almox"|"exped": {"tem": bool, "linhas": int}}``. Use para
    "o pedido 84080 vai para solda?" → ``processos.solda.tem`` (e ``.linhas`` diz quantos
    itens). As flags são **por item**: um pedido costuma ter itens mistos (parte vai para
    solda, parte não), então ``tem`` = "algum item passa", não "o pedido inteiro".

    Devolve ``{"ok": false, "error": "pedido sem OS sincronizada"}`` se o pedido ainda não
    foi sincronizado (use `listar_pedidos_com_os` p/ ver os disponíveis, ou peça a sincronização).

    Args:
        nped: número do pedido (ex.: 84080).
        incluir_linhas: se True, traz também as linhas da OS (colunas enxutas da tabela única).
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
def estado_windows_update() -> Dict[str, Any]:
    """Windows Update do SERVIDOR DE INTEGRAÇÃO (192.168.7.11): updates pendentes, último
    patch e se há REBOOT PENDENTE.

    Use para "o servidor de integração está atualizado?", "tem update pendente?", "quando
    foi o último patch?", "precisa reiniciar?". Endpoint aberto (não exige chave); é o
    bloco ``windows_update`` do /status, pedido ISOLADO (não abre as conexões de teste
    com SAP/SQL/Supabase).

    Atenção: esta é a máquina da INTEGRAÇÃO (API 8077, agendador WBC). O servidor RDP do
    SAP (192.168.7.12) é outra máquina, com tools próprias — não confunda as respostas.

    LEIA O ``pendentes`` COM ATENÇÃO — ele pode ser ``null``, e ``null`` NÃO é zero:

    - ``pendentes: null`` + ``pendentes_motivo`` = **não sabemos**. Acontece quando o
      agente do Windows Update não varre há tempo demais: nesse caso a busca até responde,
      mas responde 0 porque o cache dela está vazio — e esse 0 seria mentira. **Nunca
      relate "0 updates pendentes" quando o valor vier null; diga que não é possível saber
      e mostre o motivo.**
    - ``reboot_pendente.pendente`` é tri-estado: ``true``/``false`` são fatos; **``null`` =
      não foi possível ler** (aí ``erro`` explica). Nunca relate ``null`` como "sem reboot
      pendente". ``motivos`` diz de onde veio o sinal (CBS, WindowsUpdate,
      PendingFileRenameOperations).
    - ``patching_automatico: false`` significa que o serviço de Windows Update está
      DESABILITADO — a máquina não se atualiza sozinha. É contexto essencial: sem ele,
      "0 pendentes" engana.
    - ``dias_sem_patch`` é o dado mais útil quando o resto está indisponível.
    - ``estado: "coletando"`` = a API subiu há pouco e a 1ª coleta (~3 s) ainda não
      terminou; ela roda em background para não travar as consultas.
    """
    data = _get("/status", {"checks": "windows_update"})
    # Como em `estado_tarefa_wbc`: no /status, `windows_update` é chave de TOPO (irmã de
    # `checks`/`alerts`), não fica dentro de `checks`.
    if isinstance(data, dict) and "windows_update" in data:
        return {"ok": data.get("ok", True), "windows_update": data["windows_update"],
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


# ─────────────────── Fase 4 — ESCRITA (com confirmação humana) ───────────────────
# Padrão: confirmar=False (default) devolve um PREVIEW e NÃO escreve; o modelo mostra ao
# usuário e só chama de novo com confirmar=True após o "sim". As annotations
# (readOnlyHint=False, …) fazem o cliente MCP também sinalizar que é ação de escrita.

_ANOTACAO_ESCRITA = ToolAnnotations(readOnlyHint=False, idempotentHint=True, openWorldHint=True)

_INSTRUCAO_CONFIRMAR = ("Mostre este preview ao usuário e só chame esta tool de novo com "
                        "confirmar=True depois que ele confirmar explicitamente.")


@mcp.tool(annotations=_ANOTACAO_ESCRITA)
def sincronizar_pedido_os(nped: int, confirmar: bool = False) -> Dict[str, Any]:
    """ESCRITA: sincroniza (SAP → Supabase) a OS de um pedido. Idempotente (replace_nped).

    **Requer confirmação humana.** Com ``confirmar=False`` (default) NÃO sincroniza — devolve um
    preview do estado atual; mostre ao usuário e obtenha um "sim". Só então chame com
    ``confirmar=True`` para executar. Requer a SIS_API_KEY.

    Args:
        nped: número do pedido (ex.: 84080).
        confirmar: False = preview (não escreve); True = executa a sincronização.
    """
    n = int(nped)
    if not confirmar:
        atual = _get(f"/ordens-servico/{n}")
        if isinstance(atual, dict) and atual.get("ok"):
            r = atual.get("resumo") or {}
            estado = {"sincronizado": True, "cliente": r.get("cliente"),
                      "status_desc": r.get("status_desc"), "num_linhas": r.get("num_linhas"),
                      "ultima_sincronizacao": r.get("ultima_sincronizacao")}
            efeito = "Re-sincroniza (atualiza) a OS deste pedido no Supabase — idempotente."
        else:
            motivo = atual.get("error") or atual.get("erro") if isinstance(atual, dict) else None
            estado = {"sincronizado": False, "detalhe": motivo}
            efeito = "Sincroniza a OS deste pedido pela 1ª vez (se houver OS gerada no SAP)."
        return {"preview": True, "acao": "sincronizar_pedido_os", "nped": n,
                "estado_atual": estado, "efeito": efeito, "instrucao": _INSTRUCAO_CONFIRMAR}
    return _post(f"/ordens-servico/{n}/sincronizar")


@mcp.tool(annotations=_ANOTACAO_ESCRITA)
def forcar_carga_oportunidades(confirmar: bool = False) -> Dict[str, Any]:
    """ESCRITA: força a carga COMPLETA de oportunidades (a mesma do agendador). Operação pesada.

    **Requer confirmação humana.** Com ``confirmar=False`` (default) devolve um preview (total atual
    + intervalo agendado) e NÃO dispara; mostre ao usuário e obtenha um "sim". Só então
    ``confirmar=True`` executa. Responde ``tipo: "ocupado"`` (HTTP 409) se já houver carga em
    andamento. Requer a SIS_API_KEY.

    Args:
        confirmar: False = preview (não escreve); True = dispara a carga completa.
    """
    if not confirmar:
        info = _get("/oportunidades/info")
        total = info.get("total") if isinstance(info, dict) else None
        intervalo = info.get("intervalo_minutos") if isinstance(info, dict) else None
        return {"preview": True, "acao": "forcar_carga_oportunidades",
                "estado_atual": {"total_linhas": total, "intervalo_agendado_min": intervalo},
                "efeito": ("Recarrega a base INTEIRA de oportunidades (snapshot completo). O agendador "
                           "já roda periodicamente — force só se precisar AGORA."),
                "instrucao": _INSTRUCAO_CONFIRMAR}
    return _post("/oportunidades/sincronizar")


if __name__ == "__main__":
    # Transporte stdio (padrão) — é como Claude Desktop / Claude Code conectam.
    mcp.run()
