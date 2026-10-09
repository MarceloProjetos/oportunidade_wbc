"""One credential per client, each with its scopes (rule 2 of PLANO_MIRA_AGENTE_11.md (removed 2026-10-06)).

Until 02/10/2026 a single ``OS_API_KEY`` opened everything — the whole API (deleting history
and releasing OPs included), the three screens and the Controle de Produção API — and it sat
in five places. A leak anywhere meant SAP writes for whoever held it.

Now each client (the Mira agent, the other team, the .90 web, the MCP) gets its own key with
only the scopes it needs, kept in ``state/credenciais.json`` as a **SHA-256 digest**: the file
never holds a usable key. Keys are 256-bit random, so a plain digest is enough (no slow hash
needed). Revoking one client does not touch the others.

The ``OS_API_KEY`` keeps working as the client "chave-mestra" with ``admin``: current clients
move to their own key one at a time, and nothing breaks on the day this goes live.

Managed on the .11 with ``python -m seguranca`` (create, list, revoke).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
ARQUIVO_PADRAO = RAIZ / "state" / "credenciais.json"

#: Every scope a credential may hold. A route or tool names exactly one of them.
ESCOPOS: dict[str, str] = {
    "leitura": "consultas (status completo, pedidos, OS, OP, orçamentos WBC, históricos)",
    "rh": "dados de colaboradores (RH) — dados pessoais",
    "os:sincronizar": "sincronizar as Ordens de Serviço de um pedido (grava no Supabase)",
    "oportunidades:carga": "forçar a carga completa de oportunidades (grava no Supabase)",
    "vendas_bi:carga": "forçar a carga do Vendas BI (grava no Supabase)",
    "op:status": "mudar o status de uma Ordem de Produção (grava no SAP)",
    "historico:apagar": "apagar os históricos de sincronização",
    "pedidos_wbc": "API dos Pedidos WBC do Controle de Produção (lê e grava no SAP)",
    "manutencao_op": "API da Manutenção de OP do Controle de Produção (lê e grava no SAP)",
    "servico:reiniciar": "pedir o reinício de um dos 6 serviços da .11 (só com aprovação de uma pessoa)",
    "aprovar": "aprovar ou recusar o que o agente pediu — pessoa ou backend que fala por uma, NUNCA o agente",
    "mcp": "conectar ao MCP (8078); as ferramentas seguem os outros escopos",
    "backup:relatar": "o ALTHOST manda o estado dos backups do Veeam (só grava o próprio relato)",
    "hyperv:relatar": "um host Hyper-V manda o estado das VMs dele (só grava o próprio relato)",
    "admin": "tudo — só a chave-mestra e quem administra",
}

LIMITE_NOME = 40
#: Never held by an agent credential: ``admin`` opens everything, ``aprovar`` would let the
#: model approve its own request (rule 1 of the plan).
PROIBIDOS_AO_AGENTE = frozenset({"admin", "aprovar"})


class CredencialInvalida(ValueError):
    """A create/revoke request that cannot be honoured (bad name, unknown scope, duplicate)."""


@dataclass(frozen=True)
class Cliente:
    """Who is calling, as far as the .11 can tell, and what it may do."""

    nome: str
    escopos: frozenset[str] = field(default_factory=frozenset)
    #: An AI agent: the off switch and the business-hours rule apply (``seguranca.agente``).
    agente: bool = False
    #: May say on whose behalf it calls (``X-SIS-Usuario``) — only trusted backends.
    declara_usuario: bool = False

    def pode(self, escopo: str) -> bool:
        return "admin" in self.escopos or escopo in self.escopos


#: The ``OS_API_KEY`` and the screens' login cookie: everything, as before 02/10/2026.
def chave_mestra() -> Cliente:
    return Cliente("chave-mestra", frozenset({"admin"}), declara_usuario=True)


def resumo(chave: str) -> str:
    return hashlib.sha256(chave.encode("utf-8")).hexdigest()


def _arquivo(arquivo: Path | None) -> Path:
    if arquivo is not None:
        return arquivo
    return Path(os.environ.get("SIS_CREDENCIAIS_ARQUIVO") or ARQUIVO_PADRAO)


# Re-read only when the file changes: every request asks, and the file changes rarely.
_cache: dict[str, tuple[float, list[dict]]] = {}
_trava = threading.Lock()


def carregar(arquivo: Path | None = None) -> list[dict]:
    """The registered clients (active or not). Missing or unreadable file → none."""
    caminho = _arquivo(arquivo)
    try:
        marca = caminho.stat().st_mtime
    except OSError:
        return []
    with _trava:
        guardado = _cache.get(str(caminho))
        if guardado and guardado[0] == marca:
            return guardado[1]
        try:
            dados = json.loads(caminho.read_text(encoding="utf-8"))
            clientes = [c for c in dados.get("clientes", []) if isinstance(c, dict)]
        except (OSError, ValueError, AttributeError):
            clientes = []
        _cache[str(caminho)] = (marca, clientes)
        return clientes


def _cliente_de(registro: dict) -> Cliente:
    return Cliente(
        nome=str(registro.get("nome", "?")),
        escopos=frozenset(e for e in registro.get("escopos", []) if e in ESCOPOS),
        agente=bool(registro.get("agente")),
        declara_usuario=bool(registro.get("declara_usuario")),
    )


def identificar(chave: str | None, *, mestra: str | None = None, arquivo: Path | None = None) -> Cliente | None:
    """The client a key belongs to, or ``None``.

    Constant-time comparisons all the way (``compare_digest``), and every registered digest
    is compared — the time does not tell which client matched or how many exist.
    """
    if not chave:
        return None
    if mestra and hmac.compare_digest(chave.encode("utf-8"), mestra.encode("utf-8")):
        return chave_mestra()
    procurado = resumo(chave)
    achado = None
    for registro in carregar(arquivo):
        if hmac.compare_digest(str(registro.get("hash", "")), procurado) and registro.get("ativo", True):
            achado = registro
    return _cliente_de(achado) if achado else None


def _gravar(clientes: list[dict], arquivo: Path | None) -> None:
    caminho = _arquivo(arquivo)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    temporario = caminho.with_suffix(".tmp")
    temporario.write_text(json.dumps({"clientes": clientes}, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporario, caminho)
    with _trava:
        _cache.pop(str(caminho), None)    # the next read must see this write, mtime aside


def criar(nome: str, escopos: list[str], *, agente: bool = False, declara_usuario: bool = False,
          arquivo: Path | None = None) -> str:
    """Registers a client and returns its key — the only moment the key exists in clear."""
    nome = (nome or "").strip()
    if not nome or len(nome) > LIMITE_NOME or not all(ch.isalnum() or ch in "-_." for ch in nome):
        raise CredencialInvalida(f"Nome inválido: use letras, números, '-', '_' ou '.', até {LIMITE_NOME}.")
    desconhecidos = [e for e in escopos if e not in ESCOPOS]
    if desconhecidos or not escopos:
        raise CredencialInvalida(
            f"Escopo(s) inválido(s): {', '.join(desconhecidos) or '(nenhum)'}. Válidos: {', '.join(ESCOPOS)}."
        )
    if agente and PROIBIDOS_AO_AGENTE & set(escopos):
        raise CredencialInvalida("Um agente nunca recebe 'admin' nem 'aprovar' (regras 1 e 12 do plano).")
    clientes = list(carregar(arquivo))
    # A revoked name may be reused (02/10/2026: a key pasted into a chat had to be replaced
    # right away); the revoked entry stays in the file, for the record.
    if any(c.get("nome") == nome and c.get("ativo", True) for c in clientes):
        raise CredencialInvalida(f"Já existe um cliente ativo chamado '{nome}'. Revogue-o antes ou use outro nome.")
    chave = secrets.token_urlsafe(32)
    clientes.append({
        "nome": nome, "hash": resumo(chave), "escopos": sorted(set(escopos)), "agente": agente,
        "declara_usuario": declara_usuario, "ativo": True,
        "criado_em": datetime.now().isoformat(timespec="seconds"),
    })
    _gravar(clientes, arquivo)
    return chave


def acrescentar_escopos(nome: str, escopos: list[str], *, declara_usuario: bool = False,
                        arquivo: Path | None = None) -> list[str]:
    """Adds scopes to an active client, keeping its key (02/10/2026: the Altamira View got 403
    on a scope it used before the migration — replacing the key would have broken it again).
    Returns the client's scopes after the change. Never removes a scope; ``declara_usuario``
    only turns the flag on."""
    desconhecidos = [e for e in escopos if e not in ESCOPOS]
    if desconhecidos or (not escopos and not declara_usuario):
        raise CredencialInvalida(
            f"Escopo(s) inválido(s): {', '.join(desconhecidos) or '(nenhum)'}. Válidos: {', '.join(ESCOPOS)}."
        )
    clientes = [dict(c) for c in carregar(arquivo)]
    alvo = next((c for c in clientes if c.get("nome") == nome and c.get("ativo", True)), None)
    if alvo is None:
        raise CredencialInvalida(f"Nenhum cliente ativo chamado '{nome}'.")
    if alvo.get("agente") and PROIBIDOS_AO_AGENTE & set(escopos):
        raise CredencialInvalida("Um agente nunca recebe 'admin' nem 'aprovar' (regras 1 e 12 do plano).")
    alvo["escopos"] = sorted(set(alvo.get("escopos", [])) | set(escopos))
    if declara_usuario:
        alvo["declara_usuario"] = True
    alvo["alterado_em"] = datetime.now().isoformat(timespec="seconds")
    _gravar(clientes, arquivo)
    return alvo["escopos"]


def revogar(nome: str, *, arquivo: Path | None = None) -> bool:
    """Deactivates a client (kept in the file, for the record). ``False`` if not found."""
    clientes = [dict(c) for c in carregar(arquivo)]
    achou = False
    for c in clientes:
        if c.get("nome") == nome and c.get("ativo", True):
            c["ativo"] = False
            c["revogado_em"] = datetime.now().isoformat(timespec="seconds")
            achou = True
    if achou:
        _gravar(clientes, arquivo)
    return achou
