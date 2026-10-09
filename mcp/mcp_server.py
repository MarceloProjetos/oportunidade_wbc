"""Fachada MCP (Fase 0) do ServidorIntegracaoSAP — camada FINA e READ-ONLY.

O que é: um servidor MCP (stdio) que expõe, como *tools*, os endpoints que a API
REST do servidor de integração (porta 8077) já oferece. Um cliente MCP (Claude
Desktop, Claude Code, o assistente Mira) pode então consultar o servidor em
linguagem natural: "o servidor de integração está saudável?", "últimas
sincronizações?", "pedidos com OS disponíveis?", "o pedido 84260 está preso onde?".

O que NÃO é: não reimplementa lógica, não fala com SAP/SQL/Supabase direto, não
roda agendador. Cada tool apenas chama um endpoint HTTP existente. Quem fala com o
banco continua sendo a API (service_role), exatamente como hoje.

Fase 0 = fundação + tools de LEITURA. As duas ações de escrita (sincronizar pedido,
forçar carga de oportunidades) vieram depois, com confirmação humana (fim do arquivo).

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
from typing import Any

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

_INSTRUCOES = """\
Servidor de integração SAP B1 → Supabase da Altamira, na máquina 192.168.7.11 (API 8077).
Responde sobre: saúde do servidor e da tarefa WBC, sincronizações de Ordens de Serviço,
situação de pedidos no SAP (liberado/bloqueado em Financeiro, Produção e Entrega) e o
quadro de colaboradores das 3 empresas (Altamira, Tecnequip, Proalta).

A Integração WBC → SAP (cotações e pedidos criados no SAP a partir dos orçamentos do
WBC; worker + painel na porta 8079) roda nesta mesma máquina: `estado_integracao_wbc`
diz se o worker está ciclando, e `estado_orcamento_wbc` o que ele sabe de um orçamento. A
"tarefa WBC" de `estado_tarefa_wbc` é a tarefa agendada
LEGADA do Windows, desativada em 2026-09-08: vem `retired=true`, e isso não é falha.
Também roda aqui, desde 2026-09-28, o Controle de Produção (Pedidos WBC → Ordens de
Produção e Manutenção de OP; tela na porta 8080, serviço `OrcaView-ControleProducao`):
`verificar_saude(checks="cp")` diz se ele responde.

Não é o servidor RDP do SAP (192.168.7.12): esse é o servidor MCP `sap-rdp`, com tools
próprias. Não confunda as respostas de Windows Update das duas máquinas.

Diagnóstico da própria .11 (só leitura): `estado_servicos` (os 6 serviços do Windows e
desde quando), `testar_conexao` (DNS, ping e porta a partir da .11, só destinos de uma
lista fechada), `ultimo_deploy` (versão no ar e como foi o último deploy),
`historico_pedido` (o que mudou num pedido e quem mudou — pessoa ou integração),
`log_orcamento_wbc` (o log do worker sobre um orçamento) e `boots` (por que a .11 reiniciou:
cada vez que ligou nos últimos 7 dias e como a vida anterior acabou).

Tudo é leitura, exceto `sincronizar_pedido_os` e `forcar_carga_oportunidades`: essas
devolvem um preview com `confirmar=False` e só executam com `confirmar=True`, depois do
"sim" explícito do usuário — para PESSOAS. Para um agente, escrever é sempre PEDIR:
`pedir_sincronizar_os`, `pedir_forcar_carga`, `pedir_processar_pedido` e
`pedir_reiniciar_servico` criam um pedido com um código de 4 dígitos e NÃO executam nada;
uma pessoa aprova (na Central da .11, ou respondendo "aprovar <código>" no canal da Mira) e
`acompanhar_aprovacao` diz o resultado. Você nunca aprova.

Frescor dos dados: situação de pedidos tem cache de 2 minutos (`cache_idade_s` diz a
idade); colaboradores é uma carga diária das 12:40 em dias úteis (`desatualizado=true`
quando a do dia não chegou). Quando um campo vier `null`, é "não foi possível saber",
não zero nem falso.
"""

mcp = FastMCP("ServidorIntegracaoSAP", instructions=_INSTRUCOES)

_DICA_ROTA_INEXISTENTE = (
    "esta rota não existe na API do servidor de integração: ele ainda não foi atualizado "
    "(git pull na .11 + restart do serviço OrcaView-OS-API). O erro é de versão, não "
    "significa que o dado não existe."
)


def _headers() -> dict[str, str]:
    return {"X-API-Key": API_KEY} if API_KEY else {}


def _tratar_resposta(path: str, resp: httpx.Response) -> dict[str, Any]:
    """Traduz uma resposta HTTP da API num dict que o modelo consegue ler.

    Nunca estoura exceção. Em erro, prefere o JSON estruturado da própria API (ex.: 404
    ``{"ok": false, "error": "pedido sem OS sincronizada"}``, 409 ``{"tipo": "ocupado"}``),
    para o modelo receber a mensagem real em vez de um "HTTP 404" genérico. Um 404 **sem**
    JSON é o HTML do Flask para rota inexistente — servidor ainda sem o endpoint — e vem
    com ``dica`` dizendo isso, senão o modelo conclui que "não há dado".
    """
    if resp.status_code == 401:
        return {"ok": False, "erro": "não autorizado (401) — SIS_API_KEY ausente ou incorreta"}
    if resp.status_code >= 400:
        try:
            body = resp.json()
            if isinstance(body, dict):
                return body
        except ValueError:
            pass
        erro = {"ok": False, "erro": f"HTTP {resp.status_code} em {path}", "corpo": resp.text[:300]}
        if resp.status_code == 404:
            erro["dica"] = _DICA_ROTA_INEXISTENTE
        return erro
    try:
        return resp.json()
    except ValueError:
        return {"ok": False, "erro": f"resposta não-JSON de {path}", "corpo": resp.text[:300]}


#: How long each kind of call may take. One 12 s budget for everything made a slow API look
#: DOWN (01/10/2026 review): the full /status opens SAP, SQL Server and Supabase in sequence
#: (15 s connect timeouts each), and the forced load runs inside the request (~5.6k rows; the
#: web uses 120 s for it). A timeout now says "slow", never "unreachable".
_TEMPO_STATUS = 60.0
_TEMPO_LEITURA_HANA = 45.0       # /pedidos/* and /ordens-servico/*: live HANA reads
_TEMPO_SYNC_OS = 120.0           # POST /ordens-servico/<n>/sincronizar
_TEMPO_CARGA_OPORTUNIDADES = 180.0
_TEMPO_CONEXAO = 30.0            # ping (2 x 1 s) + up to 4 TCP connects of 2 s, from the .11
_TEMPO_BOOTS = 30.0              # /operacao/boots: one Get-WinEvent of 7 days (~1 s; 25 s ceiling there)


def _tempo_limite(metodo: str, path: str) -> float:
    """The timeout of one call: the route's own budget, never below ``SIS_HTTP_TIMEOUT``."""
    if metodo == "POST":
        proprio = (_TEMPO_CARGA_OPORTUNIDADES if path == "/oportunidades/sincronizar"
                   else _TEMPO_SYNC_OS if path.startswith("/ordens-servico/") else HTTP_TIMEOUT)
    elif path == "/status":
        proprio = _TEMPO_STATUS
    elif path.startswith(("/pedidos/", "/ordens-servico/", "/ordens-producao/")):
        proprio = _TEMPO_LEITURA_HANA
    elif path.startswith("/operacao/conexoes/"):
        proprio = _TEMPO_CONEXAO
    elif path == "/operacao/boots":
        proprio = _TEMPO_BOOTS
    else:
        proprio = HTTP_TIMEOUT
    return max(HTTP_TIMEOUT, proprio)


def _demorou(metodo: str, path: str, tempo: float) -> dict[str, Any]:
    """A timeout is "the API is slow", not "the API is down" — and a write may have run."""
    erro = {"ok": False, "erro": f"a API demorou mais de {tempo:.0f} s para responder ({metodo} {path})"}
    if metodo == "POST":
        erro["dica"] = ("a operação pode ter seguido no servidor: confira o histórico "
                        "(listar_sincronizacoes_*) antes de repetir.")
    else:
        erro["dica"] = ("o servidor está no ar, mas lento — normalmente um banco que não "
                        "responde. Tente de novo em instantes ou peça só um check.")
    return erro


def _get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """GET num endpoint da API, injetando a X-API-Key server-side.

    Devolve o JSON decodificado. Em qualquer falha (rede, HTTP != 2xx, corpo não
    JSON) devolve ``{"ok": False, "erro": "..."}`` — a tool nunca estoura exceção
    para o cliente MCP, para o modelo receber um erro legível em vez de um crash.
    """
    tempo = _tempo_limite("GET", path)
    try:
        # trust_env=False: NÃO honra proxy do ambiente (HTTP_PROXY/ALL_PROXY/etc). A fachada
        # só fala com a API interna (loopback/LAN); um proxy corporativo herdado pelo serviço
        # (LocalSystem) rotearia até a chamada de 127.0.0.1 pelo proxy → WinError 10061
        # (connection refused) mesmo com a API no ar. Um shell interativo sem proxy funciona.
        resp = httpx.get(f"{API_BASE}{path}", params=params, headers=_headers(),
                         timeout=tempo, trust_env=False)
    except httpx.TimeoutException:
        return _demorou("GET", path, tempo)
    except httpx.RequestError as exc:
        return {"ok": False, "erro": f"servidor de integração inacessível ({API_BASE}): {exc}"}
    return _tratar_resposta(path, resp)


def _post(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """POST num endpoint da API (ESCRITA), injetando a X-API-Key server-side.

    Mesmo tratamento de erro do ``_get`` (``_tratar_resposta``).
    """
    tempo = _tempo_limite("POST", path)
    try:
        resp = httpx.post(f"{API_BASE}{path}", params=params, headers=_headers(),
                          timeout=tempo, trust_env=False)
    except httpx.TimeoutException:
        return _demorou("POST", path, tempo)
    except httpx.RequestError as exc:
        return {"ok": False, "erro": f"servidor de integração inacessível ({API_BASE}): {exc}"}
    return _tratar_resposta(path, resp)


@mcp.tool()
def verificar_saude(checks: str = "", strict: bool = False) -> dict[str, Any]:
    """Diagnóstico de saúde do servidor de integração SAP/WBC (endpoint /status).

    Retorna conexões (SAP HANA, SQL Server/WBC, Supabase, com latência), o sinal do
    agendador de oportunidades, o worker WBC → SAP (``wbc_worker``), a tela do Controle
    de Produção (``controle_producao``: sonda ``127.0.0.1:CP_PORTA/health``; sem alerta
    antes da 1ª subida), o estado da tarefa agendada LEGADA "Integração WBC"
    (``scheduled_task``, ``retired=true`` desde 2026-09-08), o Windows Update
    (``windows_update``) e métricas de sistema (CPU/memória/disco). Use para responder
    "o servidor de integração está saudável?" ou "algum alerta agora?".

    Desde 10/09/2026 o ``/status`` responde em dois níveis. Esta tool manda a
    ``SIS_API_KEY``, então recebe o payload **completo**. Se a resposta vier com
    ``restrito: true`` — só ``ok``, ``healthy``, um booleano por check e ``alerts`` como
    número —, é porque a chave não chegou: diga que o diagnóstico veio **reduzido por
    falta de credencial**, não que o servidor parou de informar.

    Args:
        checks: subconjunto opcional de checagens, separadas por vírgula. Aceitos: sap,
            sql_server (sql, wbc — é o SQL Server, NÃO o worker), supabase, scheduler
            (agendador), scheduled_task (tarefa), wbc_worker (worker, integracao_wbc),
            windows_update (update, reboot), controle_producao (cp, producao). Nome fora
            dessa lista → 400 com a lista `aceitos`. Vazio = todas.
        strict: se True, o /status devolve 503 quando degradado (a tool ainda mostra o corpo).
    """
    params: dict[str, Any] = {}
    if checks:
        params["checks"] = checks
    if strict:
        params["strict"] = 1
    return _get("/status", params or None)


@mcp.tool()
def listar_sincronizacoes_os(limit: int = 20) -> dict[str, Any]:
    """Últimas sincronizações de Ordens de Serviço (Engenharia) por NPED (endpoint /historico).

    Requer a SIS_API_KEY configurada no server MCP. Use para "teve algum sync de OS
    com falha hoje?" ou "quais os últimos pedidos sincronizados?".

    Args:
        limit: quantos registros trazer (1–100). Default 20.
    """
    return _get("/historico", {"limit": max(1, min(int(limit), 100))})


@mcp.tool()
def listar_sincronizacoes_oportunidades(limit: int = 20) -> dict[str, Any]:
    """Últimos sincronismos do pipeline de oportunidades (endpoint /oportunidades/historico).

    Requer a SIS_API_KEY. Use para inspecionar a carga agendada de oportunidades
    (status, quantidade, duração, horário).

    Args:
        limit: quantos registros trazer (1–100). Default 20.
    """
    return _get("/oportunidades/historico", {"limit": max(1, min(int(limit), 100))})


@mcp.tool()
def info_oportunidades() -> dict[str, Any]:
    """Contexto do pipeline de oportunidades (endpoint /oportunidades/info): total de
    linhas na tabela + agenda (intervalo em minutos e janela comercial). Requer a SIS_API_KEY.

    Use para "de quanto em quanto tempo roda a carga?", "quantas oportunidades tem na
    base?" ou antes de `forcar_carga_oportunidades`, para dimensionar o que será
    recarregado. Não traz histórico de execuções — para isso,
    `listar_sincronizacoes_oportunidades`.
    """
    return _get("/oportunidades/info")


@mcp.tool()
def listar_pedidos_com_os(limit: int = 30) -> dict[str, Any]:
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
def detalhe_pedido_os(nped: int, incluir_linhas: bool = False) -> dict[str, Any]:
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


def _restrito(data: Any) -> bool:
    return isinstance(data, dict) and bool(data.get("restrito"))


def _sem_credencial(bloco: str) -> dict[str, Any]:
    """The /status came back reduced (no key, or a rotated one): the block is not in it.

    Since 10/09/2026 the anonymous /status keeps only ``ok``/``healthy``/one boolean per check;
    these tools used to pass that through as ``{restrito: true, alerts: <int>}`` with no hint,
    and the model read it as "the server stopped reporting" (01/10/2026 review)."""
    return {"ok": False, "restrito": True,
            "erro": (f"diagnóstico reduzido: a SIS_API_KEY não chegou ou foi recusada, e o "
                     f"bloco '{bloco}' só sai no /status completo. Não é falha do servidor.")}


@mcp.tool()
def estado_tarefa_wbc() -> dict[str, Any]:
    """Estado da tarefa agendada LEGADA "Integração WBC" (bloco scheduled_task do /status).

    **Desde 2026-09-08 essa tarefa está desativada de propósito**: a integração WBC → SAP
    passou a ser o worker do ServidorIntegracaoSAP nesta mesma máquina. Por isso o bloco
    normalmente vem com ``retired=true`` e ``available=false`` — não é falha, é o desenho.
    Para "a integração WBC está rodando?" use ``estado_integracao_wbc``. Esta tool só volta
    a trazer o monitor de verdade se a .11 religar ``WBC_TASK_MONITOR=true`` (rollback).
    Requer a SIS_API_KEY (sem ela o /status vem reduzido e a tool diz isso).
    """
    data = _get("/status", {"checks": "scheduled_task"})
    if _restrito(data):
        return _sem_credencial("scheduled_task")
    # No /status, scheduled_task é chave de TOPO (irmã de `checks`/`alerts`), não fica dentro
    # de `checks` — isola o bloco da tarefa + os alertas relacionados.
    if isinstance(data, dict) and "scheduled_task" in data:
        return {"ok": data.get("ok", True), "scheduled_task": data["scheduled_task"],
                "alerts": data.get("alerts", [])}
    return data


@mcp.tool()
def estado_integracao_wbc() -> dict[str, Any]:
    """Estado do worker da Integração WBC → SAP (bloco ``wbc_worker`` do /status).

    O worker lê os orçamentos do WBC e cria/atualiza/cancela cotação e pedido no SAP a
    cada poucos minutos, dentro do expediente dele. Use para "a integração WBC está
    rodando?", "quando foi o último ciclo?", "o ciclo deu erro?". Requer a SIS_API_KEY
    (sem ela o /status vem reduzido e a tool diz isso); lê só o banco de acompanhamento do
    worker — não toca SAP nem WBC.

    Como ler: ``installed=false`` = a integração nunca rodou nesta máquina (não é falha);
    ``last=null`` = tabelas criadas mas nenhum ciclo ainda; ``stale=true`` = silêncio além
    de ``threshold_min`` DENTRO do expediente do worker (fora dele silêncio é normal e
    ``in_window=false``); ``stuck=true`` = ciclo em andamento há tempo demais; ``last.status``
    ``falhou`` = último ciclo falhou (``last.detalhe`` diz o quê). ``healthy=null`` é "não
    há o que avaliar", não "doente". Os ``alerts`` já vêm em texto legível.
    """
    data = _get("/status", {"checks": "wbc_worker"})
    if _restrito(data):
        return _sem_credencial("wbc_worker")
    if isinstance(data, dict) and "wbc_worker" in data:
        return {"ok": data.get("ok", True), "wbc_worker": data["wbc_worker"],
                "alerts": data.get("alerts", [])}
    return data


@mcp.tool()
def estado_windows_update() -> dict[str, Any]:
    """Windows Update do servidor de integração (192.168.7.11): updates pendentes, último
    patch e reboot pendente.

    Use para "o servidor de integração está atualizado?", "tem update pendente?", "quando
    foi o último patch?", "precisa reiniciar?". Requer a SIS_API_KEY (sem ela o /status
    vem reduzido e a tool diz isso); é o bloco ``windows_update`` do /status, pedido
    isolado (não abre as conexões de teste com SAP/SQL/Supabase).

    Esta é a máquina da integração (API 8077, agendador WBC). O servidor RDP do SAP
    (192.168.7.12) é outra máquina, com tools próprias.

    Campos tri-estado — ``null`` significa "não foi possível saber", não zero nem falso:

    - ``pendentes``: vem ``null`` + ``pendentes_motivo`` quando o agente do Windows Update
      não varre há tempo demais (a busca responderia 0 com o cache vazio, e esse 0 seria
      falso). Nesse caso relate que não é possível saber e mostre o motivo.
    - ``reboot_pendente.pendente``: ``true``/``false`` são fatos; ``null`` = não foi
      possível ler (``erro`` explica). ``motivos`` diz de onde veio o sinal (CBS,
      WindowsUpdate, PendingFileRenameOperations).
    - ``patching_automatico: false``: o serviço de Windows Update está desabilitado e a
      máquina não se atualiza sozinha — contexto necessário para interpretar "0 pendentes".
    - ``dias_sem_patch``: o dado mais útil quando o resto está indisponível.
    - ``estado: "coletando"``: a API subiu há pouco e a 1ª coleta (~3 s) ainda não
      terminou; ela roda em background para não travar as consultas.
    """
    data = _get("/status", {"checks": "windows_update"})
    if _restrito(data):
        return _sem_credencial("windows_update")
    # Como em `estado_tarefa_wbc`: no /status, `windows_update` é chave de TOPO (irmã de
    # `checks`/`alerts`), não fica dentro de `checks`.
    if isinstance(data, dict) and "windows_update" in data:
        return {"ok": data.get("ok", True), "windows_update": data["windows_update"],
                "alerts": data.get("alerts", [])}
    return data


@mcp.tool()
def ultimos_erros(limit: int = 10) -> dict[str, Any]:
    """Só as sincronizações de OS que FALHARAM, dentre as últimas execuções (filtra o /historico).
    Requer a SIS_API_KEY. Use para "teve falha de sync hoje?" sem ler o histórico inteiro.

    Devolve ``{"ok": true, "examinados": N, "qtd_falhas": N, "falhas": [...]}``; "falha" é
    qualquer registro cujo ``status`` não seja sucesso. ``qtd_falhas: 0`` só diz que não
    houve falha nos últimos ``examinados`` registros, não necessariamente "hoje" — aumente
    ``limit`` se a pergunta for sobre um período.

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
# As três consultas de PLANO_SITUACAO_PEDIDOS_MCP.md (removido em 2026-09-29), sobre a MESMA view que
# desenha a tela "Situação dos Pedidos" do OrçaView. Continuam finas: quem lê o HANA é
# a API 8077, e a normalização é um porte do núcleo do V117 (com teste comparando os
# dois fontes) — por isso a resposta aqui e a tela não divergem.
#
# `readOnlyHint` explícito nestas três: o cliente MCP mostra ao usuário que são consulta,
# não ação. As 12 tools de leitura anteriores não o declaram — retrofitá-las é mexer em
# coisa que funciona, e fica para quando houver motivo.

_ANOTACAO_LEITURA = ToolAnnotations(readOnlyHint=True, openWorldHint=True)


@mcp.tool(annotations=_ANOTACAO_LEITURA)
def situacao_op(op: int, chave: str = "docnum") -> dict[str, Any]:
    """Status e identificação de UMA Ordem de Produção no SAP. Requer a SIS_API_KEY. Só leitura.

    Use para "a OP 129850 está liberada?", "de que pedido é a OP 157630?", "o que dá para
    fazer com essa OP?". Traz item, quantidade planejada, status (``status_desc`` legível),
    origem (o pedido de venda) e ``transicoes_permitidas`` — o que a API aceitaria mudar
    agora. Mudar status de OP é na tela do Controle de Produção (Manutenção de OP), não aqui.

    ``op`` é o **número que aparece na tela** (DocNum). O DocEntry é outro número: só passe
    ``chave="docentry"`` se souber que o número em mãos é esse.

    Args:
        op: número da OP (ex.: 129850).
        chave: ``docnum`` (default) ou ``docentry``.
    """
    params = {"chave": "docentry"} if str(chave).strip().lower() == "docentry" else None
    return _get(f"/ordens-producao/{int(op)}", params)


@mcp.tool(annotations=_ANOTACAO_LEITURA)
def estado_orcamento_wbc(orcamento: str, eventos: int = 15) -> dict[str, Any]:
    """O que a Integração WBC → SAP sabe de UM orçamento. Requer a SIS_API_KEY. Só leitura.

    Use para "o que aconteceu com o orçamento 00123304?", "o worker já criou a cotação do
    00125640?", "por que o 00125572 está com erro?". Vem do acompanhamento do worker (não
    consulta o SAP nem o WBC): ``orcamento.status`` (ex.: ``cotacao_criada``,
    ``pedido_criado``, ``sem_acao``, ``erro``), a ``regra_aplicada``, a cotação e o pedido que
    ele tem (DocEntry/DocNum/valor), ``ultimo_erro`` e ``ultima_verificacao``; e os
    ``eventos`` mais recentes (o histórico do orçamento, mais novo primeiro).

    **404 com ``motivo: "fora_do_acompanhamento"``** quer dizer que o worker nunca avaliou o
    orçamento — em geral porque ele é mais antigo que a janela do worker. **Não** quer dizer
    que o orçamento não existe; diga isso. Processar um orçamento fora da janela é no painel
    WBC (aba Executar → "Processar um orçamento"), por uma pessoa.

    Args:
        orcamento: número do orçamento WBC (ex.: ``00123304`` ou ``123304``).
        eventos: quantos eventos do histórico trazer (1–100). Default 15.
    """
    numero = "".join(ch for ch in str(orcamento) if ch.isdigit())
    return _get(f"/wbc/orcamentos/{numero or '0'}",
                {"eventos": max(1, min(int(eventos), 100))})


@mcp.tool(annotations=_ANOTACAO_LEITURA)
def log_orcamento_wbc(orcamento: str, linhas: int = 40) -> dict[str, Any]:
    """As linhas do log do worker WBC que citam UM orçamento, mais novas primeiro. Só leitura.

    Use depois de ``estado_orcamento_wbc`` quando o estado não explica o porquê: "por que o
    00125442 deu erro?", "o que o worker fez com o 00125348 hoje?". É o mesmo log da aba
    "Log" do painel WBC (o arquivo atual e o anterior da rotação). ``graves`` conta as
    linhas ERROR/CRITICAL. Lista vazia = o worker não escreveu nada sobre ele nesses
    arquivos (pode ser antigo demais), não que o orçamento não existe.

    Args:
        orcamento: número do orçamento WBC (ex.: ``00125442`` ou ``125442``).
        linhas: quantas linhas trazer (1–200). Default 40.
    """
    numero = "".join(ch for ch in str(orcamento) if ch.isdigit())
    return _get(f"/wbc/orcamentos/{numero or '0'}/log", {"linhas": max(1, min(int(linhas), 200))})


@mcp.tool(annotations=_ANOTACAO_LEITURA)
def historico_pedido(pedido: int, versoes: int = 20, chave: str = "docnum") -> dict[str, Any]:
    """O que mudou em UM pedido de venda no SAP, versão por versão, e QUEM salvou cada uma.

    Use para "quem mudou o pedido 84453?", "o peso do 84444 mudou quando?", "alguém mexeu
    na quantidade?". Lê o histórico de alterações do próprio SAP (ADOC/ADO1). Cada versão
    traz ``momento``, ``usuario`` e ``pela_integracao`` (true = gravado pelo usuário da
    integração/worker; false = uma pessoa no SAP) e as ``mudancas`` (cabeçalho: qualquer
    campo, inclusive contato, endereço, condição de pagamento, liberação financeira,
    montador; linhas: item, quantidade, peso, preço, desconto, situação, entrega,
    depósito, OPs; linha incluída/removida). Mais nova primeiro. "Salvo sem mudança de
    conteúdo" = a pessoa abriu e salvou sem alterar nada que importe.

    Ao responder, deixe claro quem fez cada mudança: quando ``pela_integracao`` é false, a
    mudança foi feita por uma pessoa no SAP, não pelo software.

    Args:
        pedido: número que aparece na tela (DocNum, ex.: 84453).
        versoes: quantas versões trazer (1–60), as mais novas. Default 20.
        chave: ``docnum`` (padrão) ou ``docentry`` se o número em mãos for o interno.
    """
    params: dict[str, Any] = {"versoes": max(1, min(int(versoes), 60))}
    if (chave or "").strip().lower() == "docentry":
        params["chave"] = "docentry"
    return _get(f"/pedidos/{int(pedido)}/historico", params)


@mcp.tool(annotations=_ANOTACAO_LEITURA)
def estado_servicos() -> dict[str, Any]:
    """Os 6 serviços do Windows na .11 (API, MCP, agendador, painel WBC, Controle de
    Produção, worker WBC): rodando ou não, início automático e DESDE QUANDO. Só leitura.

    Use para "os serviços da .11 estão no ar?", "o worker reiniciou?", "desde quando a API
    está rodando?". ``fora_do_ar`` lista os que não estão ``running``. Esta ferramenta não
    liga nem reinicia nada.

    ``causa_do_inicio`` diz POR QUE cada um subiu naquela hora: ``deploy`` (com a versão),
    ``reinicio_aprovado`` (código e quem aprovou), ``boot`` da máquina (a .11 reinicia todo
    dia ~06:12) ou ``desconhecido`` (o NSSM religou depois de uma queda, ou alguém reiniciou
    à mão). Repita essa causa; não suponha outra.
    """
    return _get("/operacao/servicos")


@mcp.tool(annotations=_ANOTACAO_LEITURA)
def testar_conexao(destino: str = "", porta: int = 0) -> dict[str, Any]:
    """Testa, A PARTIR DA .11, se um destino responde: DNS, ping e as portas. Só leitura.

    Use para "a .11 alcança o HANA?", "o .90 responde?", "o deploy falhou por rede?". Só
    destinos de uma lista fechada, pelo nome — sem ``destino`` a ferramenta devolve a lista
    (esta-maquina, orcaview-90, altamira-view, sap-rdp-12, github, sap-hana, service-layer,
    sql-server-wbc, supabase, gateway = pfSense, dns-casa = ALTSERVIDOR, internet = 8.8.8.8; esses
    três só na porta 53). ``conclusao`` resume em uma frase. Ping sem resposta com a
    porta aberta é normal em alguns hosts (bloqueiam ping de propósito).

    Args:
        destino: nome da lista (ex.: ``sap-hana``). Vazio = só mostra a lista.
        porta: uma das portas do destino; 0 = todas as dele.
    """
    nome = (destino or "").strip().lower()
    if not nome:
        return _get("/operacao/conexoes")
    return _get(f"/operacao/conexoes/{nome}", {"porta": int(porta)} if porta else None)


@mcp.tool(annotations=_ANOTACAO_LEITURA)
def ultimo_deploy() -> dict[str, Any]:
    """Qual versão (commit) está rodando na .11 e como foi o último deploy. Só leitura.

    Use para "o deploy deu certo?", "o que aconteceu no deploy?", "qual versão está no
    ar?". ``versao.reinicio_pendente`` = true quer dizer que o código no disco é mais novo
    que o processo da API (atualizado, mas não reiniciado). ``ultimo_deploy.etapas`` traz
    cada passo (fetch, git, pip, health, fim); ``resultado`` diz como terminou. ``deploys``
    lista os últimos 10, do mais novo ao mais velho (início, versão de→para, resultado).
    Deploys anteriores a 02/10/2026 não deixaram registro.
    """
    return _get("/operacao/deploy")


@mcp.tool(annotations=_ANOTACAO_LEITURA)
def boots() -> dict[str, Any]:
    """POR QUE A .11 REINICIOU — cada vez que ela ligou nos últimos 7 dias, da mais nova para a
    mais velha. Use para "a .11 reiniciou?", "por que caiu às 09:53?", "quem desligou a .11?". Só
    leitura; 1-3 s (cache de 60 s).

    ``ultimo_boot`` = ligada desde quando. Em ``boots[]``: ``ligou``; ``como`` a vida anterior
    acabou — ``pedido`` (alguém PEDIU ao Windows: ``pedido.categoria`` = atualizacao, hyperv,
    tarefa, sistema ou usuario; ``pedido.tipo`` reiniciar/desligar; ``motivo`` e ``comentario``
    como o Windows gravou), ``normal`` (desligou limpo sem pedido registrado), ``forcado``
    (desligada SEM aviso ao Windows: numa VM = desligada à força no Hyper-V, travou ou faltou
    energia), ``pedido_travou`` (pediram, não terminou e foi forçada), ``tela_azul`` (com o
    código) ou ``sem_registro``; ``desligou_em`` e ``fora_min``; ``frase`` pronta, sem conta nem
    caminho. ``pedido.conta`` é a conta do Windows: dado pessoal, cite só a quem precisa saber. A
    .11 reinicia sozinha todo dia por volta de 06:12 (Windows Update + tarefa de religar).
    """
    return _get("/operacao/boots")


@mcp.tool(annotations=_ANOTACAO_LEITURA)
def situacao_pedido(pedido: int, chave: str = "docnum") -> dict[str, Any]:
    """Situação de UM pedido no SAP: liberado ou bloqueado em Financeiro, Produção e
    Entrega, com prazo de entrega, sinal, condição de pagamento, montador, vendedor,
    valor e cotação WBC. Requer a SIS_API_KEY.

    Use para "o pedido 84260 está preso onde?", "o 84293 já liberou no financeiro?",
    "qual o prazo do 83832?".

    ``pedido`` é o **número que aparece na tela** (DocNum, ex.: 84260). O DocEntry é
    outro número, interno — só passe ``chave="docentry"`` se souber que o número em mãos
    é esse; confundir os dois traz o pedido errado sem erro nenhum.

    **Pedido cancelado no SAP responde 200 com ``status_pedido: "Cancelado"``**,
    ``pedido_cancelado: true`` e as três etapas em ``"Cancelado"`` (a situação vem da
    ORDR, ``fonte: "ordr"``). Diga que o pedido foi **cancelado** — não "está liberado"
    nem "não achei": não há etapa a liberar, e ele não deve ser produzido nem entregue.

    Devolve ``{"ok": false, ...}`` com **404** quando não dá para afirmar a situação. O
    campo ``motivo`` diz qual caso é: ``fora_do_recorte`` (o pedido existe no SAP, mas
    está fora do recorte da view — ela carrega só os pedidos correntes; veja
    ``status_pedido``), ``pedido_nao_encontrado`` (não existe DocNum assim) ou
    ``indeterminado`` (o SAP não respondeu agora). Nenhum dos três quer dizer que o
    pedido esteja sem bloqueio: quer dizer que **NÃO** dá para responder por aqui. Não
    invente "está liberado" nesses casos.

    O campo ``alerta_liberacao`` traz o texto "Mais de 10 dias preso no financeiro (N
    dias)" quando o pedido estourou o limite, e ``null`` quando não estourou.

    **"Quando foi liberado?"** Responda com ``lib_fin_em`` (Financeiro) e
    ``lib_producao_em`` / ``lib_entrega_em`` (Produção e Entrega, sempre iguais no SAP):
    são data e hora reais. **Nunca** responda com ``data_lib_prod`` (é uma estimativa:
    liberação do Financeiro ou emissão do sinal + 3 dias), nem trate ``data_pagto`` como
    pagamento (é a emissão da solicitação do sinal). ``null`` num campo ``*_em`` = não dá
    para afirmar a hora; diga isso em vez de estimar. ``primeira_nf_emitida`` diz se a
    primeira nota fiscal já saiu; ``nf_numero_fiscal`` é o número da DANFE.

    **Frete:** ``incoterms`` é a modalidade de frete do pedido, em texto: ``"CIF -
    Remetente"`` (a Altamira paga e contrata), ``"FOB - Destinatário"`` (o cliente paga e
    retira/contrata), ``"Terceiros"``, ``"Próprio Remetente"``, ``"Próprio Destinatário"``
    ou ``"Sem Frete"``. ``null`` = não preenchido no pedido — diga isso, não suponha CIF.

    **Endereço de entrega:** ``entrega_endereco`` traz o endereço de **despacho JÁ
    RESOLVIDO** — é para lá que a mercadoria vai. Responda com ele. O
    ``entrega_endereco.ponto_entrega`` aninhado é o cadastro do cliente, **não** o
    destino: em 24 dos pedidos de hoje ele aponta para outra cidade, e responder por ele
    manda a carga para o lugar errado. ``difere_do_ponto_de_entrega: true`` serve para
    você AVISAR que há um local de entrega separado, não para escolher entre os dois.

    Args:
        pedido: número do pedido (DocNum, ex.: 84260).
        chave: ``"docnum"`` (default) ou ``"docentry"``.
    """
    params = {"chave": "docentry"} if str(chave).strip().lower() == "docentry" else None
    return _get(f"/pedidos/{int(pedido)}/situacao", params)


@mcp.tool(annotations=_ANOTACAO_LEITURA)
def pedidos_bloqueados(bloqueio: str = "qualquer", status: str = "aberto",
                       limite: int = 40) -> dict[str, Any]:
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

    Cada pedido traz ``entrega_linha`` e ``entrega_cidade_uf``: é o endereço de despacho
    **já resolvido**. ``entrega_difere: true`` diz que o pedido tem um local de entrega
    separado do cadastro do cliente — é informação para avisar, não escolha a fazer.

    A lista ``pedidos`` respeita ``limite`` (default 40). Quando cortou, vem ``truncado:
    true`` e ``mostrando`` — diga que a lista é parcial; ``total_filtrado`` e os ``kpis``
    continuam sendo do recorte inteiro.

    Args:
        bloqueio: ``qualquer`` (default, travado em pelo menos uma etapa), ``financeiro``,
            ``producao``, ``entrega``, ou ``nenhum`` (as três liberadas).
        status: ``aberto`` (default), ``todos`` ou ``fechado``.
        limite: teto de pedidos na lista (default 40; 0 = sem teto).
    """
    data = _get("/pedidos/situacao", {"bloqueio": bloqueio, "status": status})
    # The whole cut could reach ~74 KB (~18k tokens) with bloqueio="nenhum" + status="todos"
    # (01/10/2026 review); panorama_pedidos already had a ceiling, this one did not.
    if not isinstance(data, dict) or not isinstance(data.get("pedidos"), list):
        return data
    limite = int(limite)
    if limite <= 0 or len(data["pedidos"]) <= limite:
        return data
    return {**data, "pedidos": data["pedidos"][:limite], "truncado": True, "mostrando": limite,
            "aviso": (f"lista cortada em {limite} de {len(data['pedidos'])} pedidos; kpis e "
                      "total_filtrado são do recorte inteiro. Filtre por bloqueio ou aumente "
                      "o limite para ver o resto.")}


def _norm(texto: Any) -> str:
    """Minúsculas e sem acento — "producao" casa com "PRODUÇÃO"."""
    bruto = unicodedata.normalize("NFD", str(texto or ""))
    return "".join(c for c in bruto if not unicodedata.combining(c)).casefold().strip()


_PANORAMA_LIMITE_PADRAO = 40
# As 10 colunas da tela + o alerta dos 10 dias: é o que `campos=resumo` da API devolve.
_PANORAMA_CAMPOS_RESUMO = (
    "data_pedido", "card_name", "doc_num", "sinal", "financeiro", "producao", "entrega",
    "prazo_entrega", "atrasado", "pymnt_group", "alerta_liberacao",
)
_BLOQUEADO = "bloqueado"


def _panorama_bloqueios(p: dict[str, Any]) -> int:
    return sum(1 for e in ("financeiro", "producao", "entrega") if _norm(p.get(e)) == _BLOQUEADO)


def _panorama_ordenar(pedidos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Quem importa primeiro: atrasados, depois quem tem mais etapas bloqueadas, depois
    o pedido mais antigo. Estável — empate mantém a ordem da API."""
    return sorted(pedidos, key=lambda p: (not bool(p.get("atrasado")),
                                          -_panorama_bloqueios(p),
                                          str(p.get("data_pedido") or "")))


def _panorama_filtrar(pedidos: list[dict[str, Any]], montador: str, vendedor: str,
                      so_atrasados: bool) -> list[dict[str, Any]]:
    """Filtros de conversa (substring, sem acento nem caixa) sobre os campos do completo."""
    m, v = _norm(montador), _norm(vendedor)
    saida = []
    for p in pedidos:
        if m and m not in _norm(p.get("montagem")):
            continue
        if v and v not in _norm(p.get("vendedor")):
            continue
        if so_atrasados and not p.get("atrasado"):
            continue
        saida.append(p)
    return saida


def _endereco_resumido(p: dict[str, Any]) -> dict[str, Any]:
    """Os 3 campos de entrega do perfil ``resumo``, venha o pedido de qual perfil vier.

    A API entrega o endereço em duas formas: o ``resumo`` já traz ``entrega_linha`` /
    ``entrega_cidade_uf`` / ``entrega_difere``; o ``completo`` traz o objeto
    ``entrega_endereco``. O panorama busca ora um, ora outro (um filtro por
    montador/vendedor obriga o completo, porque esses campos só existem lá) — **sem esta
    função, pedir ``campos="resumo"`` COM filtro devolveria a lista sem endereço nenhum**,
    e a mesma tool responderia coisas diferentes conforme o usuário tivesse filtrado.
    """
    if "entrega_linha" in p:
        return {k: p.get(k) for k in ("entrega_linha", "entrega_cidade_uf", "entrega_difere")}
    e = p.get("entrega_endereco") or {}
    cidade_uf = "-".join(x for x in (e.get("cidade"), e.get("uf")) if x) or None
    return {"entrega_linha": e.get("linha"), "entrega_cidade_uf": cidade_uf,
            "entrega_difere": bool(e.get("difere_do_ponto_de_entrega"))}


def _panorama_projetar(pedidos: list[dict[str, Any]], extras: tuple) -> list[dict[str, Any]]:
    """Reduz cada pedido do completo às colunas do resumo (+ os campos filtrados).

    O endereço vem por :func:`_endereco_resumido`, e não pela lista de chaves: no
    ``completo`` ele é um objeto com outro nome, então copiar por nome o perderia.
    """
    chaves = _PANORAMA_CAMPOS_RESUMO + extras
    return [{**{k: p.get(k) for k in chaves if k in p}, **_endereco_resumido(p)}
            for p in pedidos]


@mcp.tool(annotations=_ANOTACAO_LEITURA)
def panorama_pedidos(campos: str = "resumo", limite: int = _PANORAMA_LIMITE_PADRAO,
                     montador: str = "", vendedor: str = "",
                     so_atrasados: bool = False) -> dict[str, Any]:
    """Panorama da carteira: os 5 indicadores + a lista de montadores do recorte inteiro,
    mais os pedidos que importam primeiro (atrasados, depois os com mais etapas
    bloqueadas, depois os mais antigos), até um teto. Requer a SIS_API_KEY.

    Use para "como está a carteira?", "quantos pedidos estão atrasados?", "quais
    montadores têm pedido em aberto?", "o que a Barros Montagens tem em aberto?". Para um
    pedido específico prefira `situacao_pedido`; para "o que está travado",
    `pedidos_bloqueados`.

    ``kpis`` = ``{total, atrasados, financeiro_bloqueado, producao_bloqueada,
    entrega_bloqueada}`` e ``montadores`` são sempre do **recorte inteiro** (centenas de
    pedidos), independentemente de filtro ou teto. ``atrasados`` conta só pedido **em
    aberto**: um pedido fechado que foi entregue com atraso não aparece aí (mas guarda
    ``atrasado_sap=true``).

    A lista ``pedidos`` respeita ``limite`` (default 40). Quando cortou, vem ``truncado:
    true``, ``mostrando`` e ``total_filtrado`` (quantos casaram com o filtro) — diga que
    a lista é parcial e ofereça filtrar por montador/vendedor ou só atrasados. Os
    indicadores continuam certos mesmo com a lista cortada.

    Cada pedido traz o endereço de despacho **já resolvido** (``entrega_linha``,
    ``entrega_cidade_uf``, ``entrega_difere`` no resumo; o objeto ``entrega_endereco`` no
    completo). Nunca responda pelo ``ponto_entrega``: ele é o cadastro do cliente, e em
    24 dos pedidos de hoje aponta para outra cidade.

    ``cache_idade_s`` diz há quantos segundos o retrato foi tirado (o serviço guarda a
    consulta por 2 minutos). Se precisar de dado do instante, diga isso ao usuário em vez
    de fingir que é tempo real.

    Args:
        campos: ``resumo`` (default — as 10 colunas da tela, o alerta dos 10 dias e o
            endereço de entrega resolvido) ou ``completo`` (~40 campos por pedido, ~4×
            maior; pede o teto baixo ou um filtro).
        limite: teto de pedidos na lista (default 40; 0 = sem teto, só com filtro).
        montador: filtra pelo nome do montador (pedaço, sem acento). Vazio = todos.
        vendedor: filtra pelo nome do vendedor (pedaço, sem acento). Vazio = todos.
        so_atrasados: ``True`` traz só pedidos atrasados.
    """
    completo = str(campos).strip().lower() == "completo"
    filtra_por_nome = bool(str(montador).strip() or str(vendedor).strip())
    # montador/vendedor só existem no `completo`; se o usuário pediu `resumo` com um desses
    # filtros, busca-se o completo e a resposta é projetada de volta às colunas do resumo.
    pede_completo = completo or filtra_por_nome
    data = _get("/pedidos/situacao", {"campos": "completo" if pede_completo else "resumo"})
    if not isinstance(data, dict) or not isinstance(data.get("pedidos"), list):
        return data  # erro do _get (rede, 401, versão da API) passa inteiro

    pedidos = _panorama_filtrar(data["pedidos"], str(montador), str(vendedor), bool(so_atrasados))
    total_filtrado = len(pedidos)
    pedidos = _panorama_ordenar(pedidos)
    if pede_completo and not completo:
        pedidos = _panorama_projetar(pedidos, ("montagem", "vendedor"))

    limite = int(limite)
    tem_filtro = filtra_por_nome or bool(so_atrasados)
    if limite <= 0 and not tem_filtro:
        limite = _PANORAMA_LIMITE_PADRAO  # sem teto só com filtro: a carteira inteira não cabe
    saida = {**data, "pedidos": pedidos[:limite] if limite > 0 else pedidos,
             "total_filtrado": total_filtrado}
    if montador or vendedor or so_atrasados:
        saida["filtro"] = {"montador": montador or None, "vendedor": vendedor or None,
                           "so_atrasados": bool(so_atrasados)}
    if limite > 0 and total_filtrado > limite:
        saida.update({
            "truncado": True, "mostrando": limite,
            "aviso": (f"lista cortada em {limite} de {total_filtrado} pedidos (atrasados e "
                      "bloqueados primeiro); kpis e montadores são do recorte inteiro. Para "
                      "ver o resto, filtre por montador/vendedor, use so_atrasados=True ou "
                      "aumente o limite."),
        })
    return saida


# ──────────── Colaboradores (F5) — o espelho do quadro do Kairos ────────────
# O espelho é gravado pelo web_orcaview_V118 (.90) às 12:40 em dias úteis; a API 8077
# só lê, e estas tools só chamam a API. O filtro por setor e o teto de pessoas moram
# AQUI, não no endpoint: são cuidados de conversa (caber no contexto do modelo, dar
# ao usuário o setor certo quando ele erra o nome), não regra de negócio.

_COLAB_LIMITE_PADRAO = 200


def _colab_dica_404(resposta: dict[str, Any]) -> dict[str, Any]:
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


def _colab_setores(payload: dict[str, Any]) -> list[str]:
    """Todos os setores presentes na resposta, ordenados."""
    return sorted({
        s.get("setor") for e in payload.get("empresas", [])
        for s in e.get("setores", []) if s.get("setor")
    })


def _colab_filtrar_setor(payload: dict[str, Any], setor: str) -> dict[str, Any]:
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


def _colab_buscar(empresa: str, somente_ativos: bool) -> dict[str, Any]:
    """A única chamada HTTP das duas tools (e do resource) de colaboradores."""
    params: dict[str, Any] = {}
    if str(empresa).strip():
        params["empresa"] = str(empresa).strip().lower()
    if somente_ativos:
        params["somente_ativos"] = 1
    return _colab_dica_404(_get("/rh/colaboradores", params or None))


def _colab_resumir(resposta: dict[str, Any]) -> dict[str, Any]:
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


def _colab_aplicar_teto(payload: dict[str, Any], limite: int) -> dict[str, Any]:
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
                         limite: int = _COLAB_LIMITE_PADRAO) -> dict[str, Any]:
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
        limite: teto de PESSOAS na resposta (default 200). O quadro ativo das 3 empresas
            já passa de 200, então sem filtro a lista vem cortada (``truncado: true``);
            as contagens continuam certas mesmo assim. Para "quantos", use
            `resumo_colaboradores`.
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
def resumo_colaboradores(empresa: str = "", somente_ativos: bool = True) -> dict[str, Any]:
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
def sincronizar_pedido_os(nped: int, confirmar: bool = False) -> dict[str, Any]:
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
def forcar_carga_oportunidades(confirmar: bool = False) -> dict[str, Any]:
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


# --- F3/F4: writes by approval (PLANO_MIRA_AGENTE_11.md (removed 2026-10-06)) ---------------------------------
# The agent only REQUESTS; a person approves on the Central's screen (/inicio) or, later, by
# replying "aprovar <código>" in the Mira's channel. These tools never execute anything.
_ANOTACAO_PEDIDO = ToolAnnotations(readOnlyHint=False, idempotentHint=False, openWorldHint=True)
_INSTRUCAO_PEDIDO = ("NADA foi executado. Diga ao usuário o que foi pedido (a 'previa'), o 'codigo' e "
                     "que uma pessoa precisa aprovar — na Central da .11 ou respondendo 'aprovar <codigo>'. "
                     "Você não aprova; use acompanhar_aprovacao para saber o resultado.")


def _pedir(acao: str, parametros: dict[str, Any], motivo: str, pedido_por: str, em_nome_de: str) -> dict[str, Any]:
    """POST /aprovacoes. ``pedido_por``/``em_nome_de`` are stamped by the 8078 door (acesso_mcp)
    with the real client and person; whatever the model sends in them is replaced there."""
    def ascii_(texto: str) -> str:   # httpx refuses non-ASCII header values ("João")
        return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")

    cabecalhos = {**_headers(), "X-SIS-Pedido-Por": ascii_(pedido_por or "mcp-stdio")[:40]}
    if em_nome_de:
        cabecalhos["X-SIS-Usuario"] = ascii_(em_nome_de)[:80]
    tempo = 120.0   # the preview of "processar" reads the CP's list and its plan
    try:
        resp = httpx.post(f"{API_BASE}/aprovacoes", json={"acao": acao, "parametros": parametros,
                                                          "motivo": (motivo or "")[:300]},
                          headers=cabecalhos, timeout=tempo, trust_env=False)
    except httpx.TimeoutException:
        return _demorou("POST", "/aprovacoes", tempo)
    except httpx.RequestError as exc:
        return {"ok": False, "erro": f"servidor de integração inacessível ({API_BASE}): {exc}"}
    resposta = _tratar_resposta("/aprovacoes", resp)
    if resposta.get("ok"):
        resposta["instrucao"] = _INSTRUCAO_PEDIDO
    return resposta


@mcp.tool(annotations=_ANOTACAO_PEDIDO)
def pedir_sincronizar_os(nped: int, motivo: str = "", pedido_por: str = "", em_nome_de: str = "") -> dict[str, Any]:
    """PEDE aprovação para sincronizar (SAP → Supabase) as OS de um pedido. NÃO executa.

    Uma pessoa identificada aprova (Central da .11 ou "aprovar <codigo>" no canal da Mira).

    Args:
        nped: número do pedido (ex.: 84080).
        motivo: por que (aparece para quem aprova).
        pedido_por: preenchido pelo servidor — não informe.
        em_nome_de: preenchido pelo servidor — não informe.
    """
    return _pedir("sincronizar_os", {"nped": int(nped)}, motivo, pedido_por, em_nome_de)


@mcp.tool(annotations=_ANOTACAO_PEDIDO)
def pedir_forcar_carga(motivo: str = "", pedido_por: str = "", em_nome_de: str = "") -> dict[str, Any]:
    """PEDE aprovação para a carga completa de oportunidades (SAP → Supabase). NÃO executa.

    Args:
        motivo: por que (aparece para quem aprova).
        pedido_por: preenchido pelo servidor — não informe.
        em_nome_de: preenchido pelo servidor — não informe.
    """
    return _pedir("forcar_carga", {}, motivo, pedido_por, em_nome_de)


@mcp.tool(annotations=_ANOTACAO_PEDIDO)
def pedir_processar_pedido(pedido: int, motivo: str = "", pedido_por: str = "",
                           em_nome_de: str = "") -> dict[str, Any]:
    """PEDE aprovação para PROCESSAR um pedido no Controle de Produção (cria as Ordens de
    Produção, itens e recursos no SAP — irreversível). NÃO executa.

    Só pedidos em "Pedidos novos". Aprova: PCP ou administrador. Reprocessar e "forçar" não
    existem para o agente. A prévia traz cliente, valor e os itens que serão processados; se
    o plano mudar até a aprovação, a execução é recusada e é preciso pedir de novo.

    Args:
        pedido: número do pedido no SAP (DocNum, ex.: 84453).
        motivo: por que (aparece para quem aprova).
        pedido_por: preenchido pelo servidor — não informe.
        em_nome_de: preenchido pelo servidor — não informe.
    """
    return _pedir("processar_pedido", {"pedido": int(pedido)}, motivo, pedido_por, em_nome_de)


@mcp.tool(annotations=_ANOTACAO_PEDIDO)
def pedir_reiniciar_servico(servico: str, motivo: str = "", pedido_por: str = "",
                            em_nome_de: str = "") -> dict[str, Any]:
    """PEDE aprovação para reiniciar UM dos 6 serviços da .11. NÃO executa.

    Serviços: OrcaView-OS-API, OrcaView-MCP, OrcaView-Scheduler, OrcaView-WBC-Painel,
    OrcaView-ControleProducao, OrcaView-WBC-Worker. Aprova: só administrador. O Controle de
    Produção com execução em andamento é recusado; o worker para por arquivo (termina o
    orçamento em mãos) e, se estiver parado, não é ligado por aqui. Use estado_servicos antes.

    Args:
        servico: nome exato do serviço (ex.: OrcaView-WBC-Painel).
        motivo: por que (aparece para quem aprova).
        pedido_por: preenchido pelo servidor — não informe.
        em_nome_de: preenchido pelo servidor — não informe.
    """
    return _pedir("reiniciar_servico", {"servico": str(servico)}, motivo, pedido_por, em_nome_de)


@mcp.tool(annotations=_ANOTACAO_LEITURA)
def acompanhar_aprovacao(codigo: str = "") -> dict[str, Any]:
    """O estado de um pedido de aprovação (pelo ``codigo`` de 4 dígitos ou pelo id). Só leitura.

    Sem código: lista os pendentes. Estados: pendente, recusado (com o motivo), expirado,
    executando, executado, falhou (com o ``resultado``). Num pedido processado vem também a
    ``execucao_atual`` do Controle de Produção.
    """
    chave = "".join(ch for ch in str(codigo) if ch.isalnum())
    if not chave:
        return _get("/aprovacoes", {"estado": "pendente"})
    return _get(f"/aprovacoes/{chave}")


if __name__ == "__main__":
    # Transporte stdio (padrão) — é como Claude Desktop / Claude Code conectam.
    mcp.run()
