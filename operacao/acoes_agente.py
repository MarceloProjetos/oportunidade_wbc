"""The closed catalog of writes an agent may REQUEST (F3/F4 of docs/PLANO_MIRA_AGENTE_11.md).

Each action says which scope the requester needs, who may approve it (decision 4: process an
order = PCP or admin; restart a service = admin; sync OS and forced load = any identified
person), its hourly cap (rule 6), how to build the preview a person reads before deciding,
and how to run it once approved.

Execution goes through the SAME routes people use (loopback to the API 8077 and to the
Controle de Produção 8080), with the master key and ``X-SIS-Usuario`` = the person who
approved: the locks, rate limits, refusals and audit of those routes all apply, and the
audit line names the person, not the agent. Nothing here runs a command, SQL or file of the
caller's choosing (rule 5).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import httpx

from config import get_settings
from operacao import reinicio

#: Who may approve, by role. ``None`` = any identified person.
PAPEIS_ADMIN = frozenset({"admin"})
PAPEIS_PCP = frozenset({"pcp", "admin"})
#: The CP's "Pedidos novos" list is paged (15 per page); an order is looked up page by page.
PAGINAS_MAX = 20


class AcaoInvalida(ValueError):
    """Unknown action, bad parameter, or a preview that shows it cannot run."""


@dataclass(frozen=True)
class Contexto:
    """Who approved, for the routes' audit and the CP's ``solicitante``."""

    pessoa: str
    codigo: str
    pedido_por: str


@dataclass(frozen=True)
class Acao:
    nome: str
    titulo: str
    escopo: str
    papeis: frozenset[str] | None
    por_hora: int
    validar: Callable[[dict], dict]
    previa: Callable[[dict], dict]
    executar: Callable[[dict, dict, Contexto], tuple[bool, dict]]


# ---------------------------------------------------------------------------
# Loopback to the routes people use
# ---------------------------------------------------------------------------
def _usuario(ctx: Contexto | None) -> str:
    if ctx is None:
        return "agente (prévia)"
    return f"{ctx.pessoa} · aprovação {ctx.codigo}"[:80]


def _chamar(base: str, metodo: str, caminho: str, *, corpo: dict | None = None,
            ctx: Contexto | None = None, tempo: float = 60) -> tuple[int, dict]:
    s = get_settings()
    cabecalhos = {"X-API-Key": s.os_api_key or "", "X-SIS-Usuario": _usuario(ctx)}
    try:
        r = httpx.request(metodo, f"{base}{caminho}", json=corpo, headers=cabecalhos,
                          timeout=tempo, trust_env=False)
    except httpx.HTTPError as exc:
        return 0, {"ok": False, "erro": f"sem resposta de {base}{caminho}: {exc}"[:300]}
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, {"ok": False, "erro": r.text[:300]}


def _api() -> str:
    return f"http://127.0.0.1:{get_settings().os_api_port}"


def _cp() -> str:
    s = get_settings()
    return (s.cp_url or f"http://127.0.0.1:{s.cp_porta}").rstrip("/")


def _inteiro(parametros: dict, campo: str) -> int:
    valor = parametros.get(campo)
    if isinstance(valor, bool) or not str(valor or "").strip().isdigit() or int(valor) <= 0:
        raise AcaoInvalida(f"'{campo}' deve ser um número positivo.")
    return int(valor)


# ---------------------------------------------------------------------------
# sincronizar_os
# ---------------------------------------------------------------------------
def _validar_os(p: dict) -> dict:
    return {"nped": _inteiro(p, "nped")}


def _previa_os(p: dict) -> dict:
    return {"texto": f"Sincronizar as Ordens de Serviço do pedido {p['nped']} (SAP → Supabase). "
                     "Idempotente: substitui as linhas desse pedido, não duplica."}


def _executar_os(p: dict, _previa: dict, ctx: Contexto) -> tuple[bool, dict]:
    http, corpo = _chamar(_api(), "POST", f"/ordens-servico/{p['nped']}/sincronizar", ctx=ctx, tempo=150)
    return http == 200 and bool(corpo.get("ok")), {"http": http, **corpo}


# ---------------------------------------------------------------------------
# forcar_carga (oportunidades)
# ---------------------------------------------------------------------------
def _previa_carga(_p: dict) -> dict:
    return {"texto": "Carga completa de oportunidades (SAP → Supabase, ~5,6 mil linhas, alguns minutos). "
                     "Se o agendador já estiver carregando, a API recusa (409) e nada é repetido."}


def _executar_carga(_p: dict, _previa: dict, ctx: Contexto) -> tuple[bool, dict]:
    http, corpo = _chamar(_api(), "POST", "/oportunidades/sincronizar", ctx=ctx, tempo=300)
    return http == 200 and bool(corpo.get("ok", True)), {"http": http, **corpo}


# ---------------------------------------------------------------------------
# processar_pedido (Controle de Produção, /api/pedidos-wbc — never Reprocessar, never "forçar")
# ---------------------------------------------------------------------------
def _validar_pedido(p: dict) -> dict:
    return {"pedido": _inteiro(p, "pedido")}


def _achar_na_lista(pedido: int) -> dict:
    for pagina in range(1, PAGINAS_MAX + 1):
        http, corpo = _chamar(_cp(), "GET", f"/api/pedidos-wbc/pedidos?modo=novos&pagina={pagina}", tempo=90)
        if http != 200:
            raise AcaoInvalida(f"Não consegui ler 'Pedidos novos' no Controle de Produção (HTTP {http}): "
                               f"{corpo.get('motivo') or corpo.get('erro') or ''}".strip())
        for item in corpo.get("pedidos", []):
            if int(item.get("pedido") or 0) == pedido:
                return item
        if pagina >= int(corpo.get("paginas") or 1):
            break
    raise AcaoInvalida(f"O pedido {pedido} não está em 'Pedidos novos' do Controle de Produção "
                       "(já processado, cancelado, ou não veio do WBC). Nada a processar.")


def _conferir(oportunidade: str) -> dict:
    http, corpo = _chamar(_cp(), "POST", "/api/pedidos-wbc/processar/conferir",
                          corpo={"oportunidades": [oportunidade]}, tempo=90)
    if http != 200 or not corpo.get("ok"):
        raise AcaoInvalida(f"O Controle de Produção recusou conferir: {corpo.get('motivo') or corpo.get('erro')}")
    return corpo["plano"]


def _essencial(plano: dict) -> list[tuple]:
    return [(i.get("pedido"), i.get("oportunidade"), i.get("total")) for i in plano.get("itens", [])]


def _previa_pedido(p: dict) -> dict:
    item = _achar_na_lista(p["pedido"])
    plano = _conferir(str(item["oportunidade"]))
    return {
        "texto": (f"Processar o pedido {p['pedido']} no Controle de Produção: cria as Ordens de Produção, "
                  f"itens e recursos no SAP. IRREVERSÍVEL. {plano.get('aviso', {}).get('texto') or ''}").strip(),
        "pedido": p["pedido"], "oportunidade": str(item["oportunidade"]), "wbc": item.get("wbc"),
        "cliente": item.get("cliente"), "total": item.get("total"),
        "itens": _essencial(plano),
    }


def _executar_pedido(p: dict, previa: dict, ctx: Contexto) -> tuple[bool, dict]:
    try:
        plano = _conferir(previa["oportunidade"])
    except AcaoInvalida as exc:
        return False, {"motivo": str(exc)}
    if [list(t) for t in _essencial(plano)] != [list(t) for t in previa.get("itens", [])]:
        return False, {"motivo": "O plano mudou desde o pedido de aprovação (pedido, oportunidade ou "
                                 "valor diferente). Nada foi feito; o agente precisa pedir de novo."}
    solicitante = f"{ctx.pessoa} (aprovou {ctx.codigo}, pedido de {ctx.pedido_por})"[:80]
    http, corpo = _chamar(_cp(), "POST", "/api/pedidos-wbc/processar/executar",
                          corpo={"token": plano["token"], "solicitante": solicitante}, ctx=ctx, tempo=90)
    return http == 202 and bool(corpo.get("ok")), {"http": http, **corpo}


# ---------------------------------------------------------------------------
# reiniciar_servico
# ---------------------------------------------------------------------------
def _validar_servico(p: dict) -> dict:
    try:
        return {"servico": reinicio.validar(str(p.get("servico") or ""))}
    except reinicio.ReinicioRecusado as exc:
        raise AcaoInvalida(str(exc)) from exc


def _previa_servico(p: dict) -> dict:
    return reinicio.previa(p["servico"])


def _executar_servico(p: dict, _previa: dict, _ctx: Contexto) -> tuple[bool, dict]:
    try:
        resultado = reinicio.reiniciar(p["servico"])
    except reinicio.ReinicioRecusado as exc:
        return False, {"motivo": str(exc)}
    return bool(resultado.get("ok")), resultado


CATALOGO: dict[str, Acao] = {a.nome: a for a in (
    Acao("sincronizar_os", "Sincronizar as OS de um pedido", "os:sincronizar", None, 30,
         _validar_os, _previa_os, _executar_os),
    Acao("forcar_carga", "Forçar a carga de oportunidades", "oportunidades:carga", None, 4,
         lambda _p: {}, _previa_carga, _executar_carga),
    Acao("processar_pedido", "Processar um pedido (cria OPs no SAP)", "pedidos_wbc", PAPEIS_PCP, 10,
         _validar_pedido, _previa_pedido, _executar_pedido),
    Acao("reiniciar_servico", "Reiniciar um serviço da .11", "servico:reiniciar", PAPEIS_ADMIN, 3,
         _validar_servico, _previa_servico, _executar_servico),
)}


def pode_aprovar(acao: Acao, papel: str | None) -> bool:
    return acao.papeis is None or (papel or "").lower() in acao.papeis
