"""Fachada MCP (Fase 0) do ServidorIntegracaoSAP — camada FINA e READ-ONLY.

O que é: um servidor MCP (stdio) que expõe, como *tools*, os endpoints que a API
REST do servidor de integração (porta 8077) já oferece. Um cliente MCP (Claude
Desktop, Claude Code, o assistente Mira) pode então consultar o servidor em
linguagem natural: "o servidor de integração está saudável?", "últimas
sincronizações?", "pedidos com OS disponíveis?", "o pedido 84260 está preso onde?".

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
import unicodedata
from typing import Any, Dict, List, Optional

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

    Cada item traz ``status_pedido`` (``Aberto`` | ``Cancelado`` | ``Fechado``) e
    ``pedido_cancelado`` (bool). **Pedido cancelado no SAP continua na lista** quando as
    OPs dele ainda estão vivas — ele NÃO é escondido de propósito, para que dê para agir
    (cancelar as OPs, parar de oferecer "Liberar"). Filtre por ``pedido_cancelado`` se a
    pergunta for "o que dá para produzir". ``null`` nos dois = OS sem pedido na ORDR.

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

    **Olhe ``pedido_cancelado`` antes de responder sobre a OS.** ``status_pedido`` e
    ``pedido_cancelado`` (nível de topo) vêm da ORDR ao vivo; a OS sincronizada de um
    pedido cancelado continua existindo, com OPs e ``exped_disponivel: true``. Quando
    cancelado, vem também ``aviso: {"tipo": "pedido_cancelado", "motivo": ...}`` — diga
    que o pedido está cancelado, não que "vai para solda". ``null`` nos dois = SAP não
    respondeu; não conclua nada sobre cancelamento nesse caso.

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


# ────────────────── Situação dos Pedidos (F4) — a view DDP do SAP ──────────────────
# As três consultas de docs/PLANO_SITUACAO_PEDIDOS_MCP.md, sobre a MESMA view que
# desenha a tela "Situação dos Pedidos" do OrçaView. Continuam finas: quem lê o HANA é
# a API 8077, e a normalização é um porte do núcleo do V117 (com teste comparando os
# dois fontes) — por isso a resposta aqui e a tela não divergem.
#
# `readOnlyHint` explícito nestas três: o cliente MCP mostra ao usuário que são consulta,
# não ação. As 12 tools de leitura anteriores não o declaram — retrofitá-las é mexer em
# coisa que funciona, e fica para quando houver motivo.

_ANOTACAO_LEITURA = ToolAnnotations(readOnlyHint=True, openWorldHint=True)


@mcp.tool(annotations=_ANOTACAO_LEITURA)
def situacao_pedido(pedido: int, chave: str = "docnum") -> Dict[str, Any]:
    """Situação de UM pedido no SAP: liberado ou bloqueado em Financeiro, Produção e
    Entrega, com prazo de entrega, sinal, condição de pagamento, montador, vendedor,
    valor e cotação WBC. Requer a SIS_API_KEY.

    Use para "o pedido 84260 está preso onde?", "o 84293 já liberou no financeiro?",
    "qual o prazo do 83832?".

    ``pedido`` é o **número que aparece na tela** (DocNum, ex.: 84260). O DocEntry é
    outro número, interno — só passe ``chave="docentry"`` se souber que o número em mãos
    é esse; confundir os dois traz o pedido errado sem erro nenhum.

    Devolve ``{"ok": false, ...}`` com **404** quando o pedido está **fora do recorte da
    view** — ela carrega só os pedidos correntes. Isso **NÃO** quer dizer que o pedido
    esteja sem bloqueio: quer dizer que não dá para responder por aqui. Não invente
    "está liberado" nesse caso.

    O campo ``alerta_liberacao`` traz o texto "Mais de 10 dias preso no financeiro (N
    dias)" quando o pedido estourou o limite, e ``null`` quando não estourou.

    Args:
        pedido: número do pedido (DocNum, ex.: 84260).
        chave: ``"docnum"`` (default) ou ``"docentry"``.
    """
    params = {"chave": "docentry"} if str(chave).strip().lower() == "docentry" else None
    return _get(f"/pedidos/{int(pedido)}/situacao", params)


@mcp.tool(annotations=_ANOTACAO_LEITURA)
def pedidos_bloqueados(bloqueio: str = "qualquer", status: str = "aberto") -> Dict[str, Any]:
    """Pedidos TRAVADOS no SAP: os que estão bloqueados em Financeiro, Produção ou
    Entrega. Requer a SIS_API_KEY.

    Use para "o que está travado?", "quais pedidos estão bloqueados no financeiro?",
    "tem alguma coisa presa na produção?".

    **Para "o que está preso há tempo demais": chame com ``bloqueio="financeiro"`` e
    olhe o campo ``alerta_liberacao``** de cada pedido — ele traz "Mais de 10 dias preso
    no financeiro (N dias)" ou ``null``.

    ⚠️ **O default é ``status="aberto"``, e isso DIVERGE da tela de propósito.** A tela
    mostra ``todos`` porque espelha o Power BI; aqui, quem pergunta "o que está travado?"
    quer o que trava **hoje** — pedido fechado que esteve bloqueado é história. Se o
    número tiver de bater com a tela, passe ``status="todos"``.

    Os ``kpis`` e a lista de ``montadores`` da resposta são sempre do **recorte inteiro**,
    não do filtro — quantos pedidos voltaram está em ``total_filtrado``.

    Args:
        bloqueio: ``qualquer`` (default, travado em pelo menos uma etapa), ``financeiro``,
            ``producao``, ``entrega``, ou ``nenhum`` (as três liberadas).
        status: ``aberto`` (default), ``todos`` ou ``fechado``.
    """
    return _get("/pedidos/situacao", {"bloqueio": bloqueio, "status": status})


@mcp.tool(annotations=_ANOTACAO_LEITURA)
def panorama_pedidos(campos: str = "resumo") -> Dict[str, Any]:
    """Panorama da carteira: TODOS os pedidos do recorte da view + os 5 indicadores + a
    lista de montadores, numa chamada só. Requer a SIS_API_KEY.

    Use para "como está a carteira?", "quantos pedidos estão atrasados?", "quais
    montadores têm pedido em aberto?". Para uma pergunta sobre um pedido específico
    prefira `situacao_pedido`; para "o que está travado", `pedidos_bloqueados` — este
    aqui traz a carteira inteira (centenas de pedidos) e é o mais caro dos três.

    ``kpis`` = ``{total, atrasados, financeiro_bloqueado, producao_bloqueada,
    entrega_bloqueada}``. ``atrasados`` conta só pedido **em aberto**: um pedido fechado
    que foi entregue com atraso não aparece aí (mas guarda ``atrasado_sap=true``).

    ``cache_idade_s`` diz há quantos segundos o retrato foi tirado (o serviço guarda a
    consulta por 2 minutos). Se precisar de dado do instante, diga isso ao usuário em vez
    de fingir que é tempo real.

    Args:
        campos: ``resumo`` (default — as 10 colunas da tela + o alerta dos 10 dias) ou
            ``completo`` (~40 campos por pedido; **use só se realmente precisar**, a
            resposta fica grande).
    """
    return _get("/pedidos/situacao", {"campos": campos})


# ──────────── Colaboradores (F5) — o espelho do quadro do Kairos ────────────
# O espelho é gravado pelo web_orcaview_V117 (.90) às 12:40 em dias úteis; a API 8077
# só lê, e estas tools só chamam a API. O filtro por setor e o teto de pessoas moram
# AQUI, não no endpoint: são cuidados de conversa (caber no contexto do modelo, dar
# ao usuário o setor certo quando ele erra o nome), não regra de negócio.

_COLAB_LIMITE_PADRAO = 200


def _norm(texto: Any) -> str:
    """Minúsculas e sem acento — "producao" casa com "PRODUÇÃO"."""
    bruto = unicodedata.normalize("NFD", str(texto or ""))
    return "".join(c for c in bruto if not unicodedata.combining(c)).casefold().strip()


def _colab_dica_404(resposta: Dict[str, Any]) -> Dict[str, Any]:
    """Traduz o 404 de rota inexistente: a .11 ainda não foi atualizada.

    Sem isto o modelo recebe "HTTP 404 em /rh/colaboradores" e conclui que não há
    colaboradores — que é o contrário do que aconteceu.
    """
    if not resposta.get("ok", True) and "HTTP 404" in str(resposta.get("erro", "")):
        return {**resposta, "dica": (
            "a rota /rh/colaboradores não existe nesta API: o servidor de integração "
            "ainda não foi atualizado (git pull na .11 + restart do serviço "
            "OrcaView-OS-API). Isto NÃO quer dizer que não há colaboradores."
        )}
    return resposta


def _colab_setores(payload: Dict[str, Any]) -> List[str]:
    """Todos os setores presentes na resposta, ordenados."""
    return sorted({
        s.get("setor") for e in payload.get("empresas", [])
        for s in e.get("setores", []) if s.get("setor")
    })


def _colab_filtrar_setor(payload: Dict[str, Any], setor: str) -> Dict[str, Any]:
    """Mantém só os setores cujo nome CONTÉM ``setor`` (sem acento, sem caixa)."""
    alvo = _norm(setor)
    empresas = []
    for emp in payload.get("empresas", []):
        setores = [s for s in emp.get("setores", []) if alvo in _norm(s.get("setor"))]
        if setores:
            empresas.append({
                **emp,
                "total": sum(s.get("total", 0) for s in setores),
                "setores": setores,
            })
    return {
        **payload,
        "empresas": empresas,
        "total": sum(e["total"] for e in empresas),
        "filtro_setor": setor,
    }


def _colab_buscar(empresa: str, somente_ativos: bool) -> Dict[str, Any]:
    """A única chamada HTTP das duas tools (e do resource) de colaboradores."""
    params: Dict[str, Any] = {}
    if str(empresa).strip():
        params["empresa"] = str(empresa).strip().lower()
    if somente_ativos:
        params["somente_ativos"] = 1
    return _colab_dica_404(_get("/rh/colaboradores", params or None))


def _colab_resumir(resposta: Dict[str, Any]) -> Dict[str, Any]:
    """Troca as listas de pessoas por ``{setor: quantidade}``."""
    if not resposta.get("ok", False):
        return resposta
    return {
        **resposta,
        "empresas": [
            {
                "empresa": emp.get("empresa"),
                "total": emp.get("total"),
                "setores": {s.get("setor"): s.get("total") for s in emp.get("setores", [])},
            }
            for emp in resposta.get("empresas", [])
        ],
    }


def _colab_aplicar_teto(payload: Dict[str, Any], limite: int) -> Dict[str, Any]:
    """Corta a lista de PESSOAS no teto — dizendo que cortou e como ver o resto.

    As contagens (``total`` de cada empresa/setor) ficam intactas: o modelo continua
    sabendo o tamanho real do quadro mesmo quando não recebe todos os nomes.
    """
    total = payload.get("total", 0)
    if limite <= 0 or total <= limite:
        return payload
    restante = limite
    empresas = []
    for emp in payload.get("empresas", []):
        setores = []
        for s in emp.get("setores", []):
            pessoas = s.get("colaboradores", [])
            cabe = pessoas[:restante] if restante > 0 else []
            restante -= len(cabe)
            setores.append({**s, "colaboradores": cabe, "omitidos": len(pessoas) - len(cabe)})
        empresas.append({**emp, "setores": setores})
    return {
        **payload, "empresas": empresas, "truncado": True,
        "mostrando": limite, "total": total,
        "aviso": ("lista de nomes cortada no teto: filtre por empresa/setor, aumente o "
                  "limite, ou use resumo_colaboradores para o quadro inteiro em contagens."),
    }


@mcp.tool(annotations=_ANOTACAO_LEITURA)
def listar_colaboradores(empresa: str = "", setor: str = "", somente_ativos: bool = True,
                         limite: int = _COLAB_LIMITE_PADRAO) -> Dict[str, Any]:
    """Quem trabalha nas 3 empresas (Altamira, Tecnequip, Proalta), agrupado por
    empresa e setor, com cargo, matrícula e situação. Requer a SIS_API_KEY.

    Use para "quem está na produção da Tecnequip?", "lista dos funcionários por
    setor", "fulano ainda trabalha aqui?", "quem entrou este ano?". Para só contar
    gente ("quantos na expedição?") prefira `resumo_colaboradores` — é a mesma
    consulta sem os nomes, e cabe muito melhor na conversa.

    **O default é ``somente_ativos=True``** (quem está na ativa). Passe
    ``somente_ativos=False`` para ver também quem saiu: a linha do desligado **nunca
    some** do espelho — ela muda de ``status`` (``ativo`` / ``desligado`` / ``ausente``,
    este último = sumiu do Kairos sem registro de desligamento).

    ``em_ferias_ou_afastado=true`` quer dizer **sem expediente** há pelo menos 3 dias
    úteis processados. O Kairos **não distingue férias de afastamento/atestado** — não
    diga "está de férias", diga "está sem expediente". ``sem_expediente_desde=null``
    com a flag ligada quer dizer que começou antes da janela de 30 dias e não se sabe
    desde quando.

    ⚠️ O dado vem de uma carga diária (12:40, dias úteis), não do Kairos ao vivo: uma
    admissão de hoje de manhã só aparece depois disso. Se ``desatualizado`` vier
    ``true``, a carga do dia não chegou — o dado ainda é o último bom conhecido, mas
    avise o usuário em vez de apresentá-lo como de hoje.

    Errar o nome do setor não devolve lista vazia calada: a resposta traz
    ``setores_disponiveis`` para você tentar de novo com o nome certo.

    Args:
        empresa: ``altamira``, ``tecnequip`` ou ``proalta``. Vazio = as três.
        setor: filtra pelo nome do setor, sem acento e sem caixa, por pedaço
            ("producao" acha "PRODUÇÃO"). Vazio = todos.
        somente_ativos: ``True`` (default) traz só quem está na ativa.
        limite: teto de PESSOAS na resposta (default 200). As contagens continuam
            certas mesmo quando a lista de nomes é cortada.
    """
    resposta = _colab_buscar(empresa, somente_ativos)
    if not resposta.get("ok", False):
        return resposta

    if str(setor).strip():
        disponiveis = _colab_setores(resposta)
        resposta = _colab_filtrar_setor(resposta, str(setor).strip())
        if not resposta["empresas"]:
            return {**resposta, "setores_disponiveis": disponiveis,
                    "aviso": (f"nenhum setor casa com {setor!r} — tente um dos "
                              f"listados em setores_disponiveis.")}
    return _colab_aplicar_teto(resposta, int(limite))


@mcp.tool(annotations=_ANOTACAO_LEITURA)
def resumo_colaboradores(empresa: str = "", somente_ativos: bool = True) -> Dict[str, Any]:
    """Quantas pessoas por empresa e por setor — o mesmo quadro do Kairos, só em
    contagens, sem os nomes. Requer a SIS_API_KEY.

    Use para "quantos funcionários tem a Tecnequip?", "quantos na produção?", "como o
    quadro está distribuído entre os setores?". É a versão barata de
    `listar_colaboradores`: cabe na conversa mesmo com o quadro inteiro.

    Mesmas ressalvas da outra tool: ``somente_ativos=True`` por default (passe
    ``False`` para contar também desligados e ausentes), o dado é da carga das 12:40
    e ``desatualizado=true`` significa que a carga do dia não chegou.

    Args:
        empresa: ``altamira``, ``tecnequip`` ou ``proalta``. Vazio = as três.
        somente_ativos: ``True`` (default) conta só quem está na ativa.
    """
    return _colab_resumir(_colab_buscar(empresa, somente_ativos))


# ── Resources: contexto de LEITURA que o cliente anexa sem gastar uma tool-call por vez ──

@mcp.resource("sap-integracao://status", mime_type="application/json")
def recurso_status() -> str:
    """Snapshot atual do /status (saúde de SAP/SQL/Supabase, agendador, tarefa WBC, sistema)."""
    return json.dumps(_get("/status"), ensure_ascii=False, indent=2)


@mcp.resource("sap-integracao://historico-os", mime_type="application/json")
def recurso_historico_os() -> str:
    """Snapshot das últimas 20 sincronizações de OS (/historico). Requer a SIS_API_KEY."""
    return json.dumps(_get("/historico", {"limit": 20}), ensure_ascii=False, indent=2)


@mcp.resource("sap-integracao://colaboradores", mime_type="application/json")
def recurso_colaboradores() -> str:
    """Quadro ATIVO das 3 empresas em contagens por setor (sem nomes). Requer a SIS_API_KEY.

    De propósito o resumo, não a lista: como contexto anexado, 251 pessoas custariam caro
    em toda conversa. Para os nomes existe a tool `listar_colaboradores`.
    """
    return json.dumps(
        _colab_resumir(_colab_buscar("", somente_ativos=True)), ensure_ascii=False, indent=2
    )


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
