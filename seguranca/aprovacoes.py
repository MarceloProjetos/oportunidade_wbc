"""Approval requests: the agent asks, a person decides, the .11 executes (F3 of
PLANO_MIRA_AGENTE_11.md (removed 2026-10-06), rule 1 — "the model never approves a write").

Before this, the MCP's two writes ran when the MODEL sent ``confirmar=True``: a text that
reached the model (a customer name, an order note, a web page) could fire a write. Now an
agent only creates a request here; it carries no power by itself. A person — on the
Central's screen, or later through the Mira (her card, or a reply in the private WhatsApp
group: "aprovar 4821", the same 4-digit shape as the .90's existing "confirmar 1234",
which never reaches the model and survives voice transcription) — approves it, and only that approval runs
the action, with exactly the parameters that were shown.

Kept in SQLite (``state/aprovacoes.db``): the API's threads and the CLI write to it, and a
decision must be atomic — ``pendente`` → ``executando`` happens in ONE UPDATE that also
checks the expiry, so two approvals of the same request (two clicks, screen and WhatsApp)
cannot both run it.

One owner per action (F4 of web_orcaview_V118/docs/PLANO_AGENTES_INDEPENDENTES.md): the
CREATION is atomic too. ``criar`` runs in one ``BEGIN IMMEDIATE`` transaction, in this order:
a known ``chave_idem`` returns its request (a retry after a timeout or a restart never makes a
second one, whatever its state); an OPEN request for the same ``(acao, alvo)`` is returned
(the Mira, the Téo and a person asking the same restart get ONE request and all follow it);
only then the hourly cap and the INSERT. A partial unique index on ``(acao, alvo)`` over the
open states holds it even against a second process on the same file. Only the .11 sees everyone's
requests, so this is where the reservation lives.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parents[1]
ARQUIVO_PADRAO = RAIZ / "state" / "aprovacoes.db"
#: Long enough to answer from the phone; short enough that an old request is not approved
#: on a state that changed meanwhile.
VALIDADE_MIN = 30
#: Short code for a typed or spoken reply ("aprovar 4821"): digits only, like the .90's
#: deterministic "confirmar 1234" — letters come out wrong from voice transcription.
ALFABETO_CODIGO = "0123456789"
TAMANHO_CODIGO = 4
LIMITE_TEXTO = 300

ESTADOS = ("pendente", "recusado", "expirado", "executando", "executado", "falhou")
#: The states that hold the target: a second request for it is the same request.
ABERTOS = ("pendente", "executando")
#: An ``executando`` older than this never got its outcome (the API died mid-action): it
#: would hold the target forever, so it closes ``falhou`` ("sem desfecho"). The longest
#: action today answers in ~150 s (sincronizar_os).
ORFA_MIN = 15

logger = logging.getLogger(__name__)
_trava = threading.Lock()
#: Database files already migrated by this process (columns + indexes, once each).
_migrados: set[str] = set()


class AprovacaoInvalida(ValueError):
    """A decision that cannot be honoured. ``tipo``: nao_encontrado, ja_decidido, expirado."""

    def __init__(self, tipo: str, motivo: str) -> None:
        super().__init__(motivo)
        self.tipo = tipo


def _arquivo(arquivo: Path | None) -> Path:
    if arquivo is not None:
        return arquivo
    return Path(os.environ.get("SIS_APROVACOES_ARQUIVO") or ARQUIVO_PADRAO)


def _conectar(arquivo: Path | None) -> sqlite3.Connection:
    caminho = _arquivo(arquivo)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(caminho, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS aprovacoes (
        id TEXT PRIMARY KEY, codigo TEXT NOT NULL, acao TEXT NOT NULL, parametros TEXT NOT NULL,
        previa TEXT NOT NULL, motivo TEXT, pedido_por TEXT NOT NULL, em_nome_de TEXT,
        criado_em TEXT NOT NULL, expira_em TEXT NOT NULL, estado TEXT NOT NULL,
        decidido_por TEXT, decidido_canal TEXT, decidido_papel TEXT, decidido_cliente TEXT,
        decidido_em TEXT, motivo_recusa TEXT, resultado TEXT, concluido_em TEXT)""")
    conn.execute("CREATE INDEX IF NOT EXISTS aprovacoes_estado ON aprovacoes (estado, criado_em)")
    _garantir_colunas(conn)  # every time: cheap, and a file recreated in the same process gets them
    if str(caminho) not in _migrados:
        _migrar(conn)
        _migrados.add(str(caminho))
    return conn


def _garantir_colunas(conn: sqlite3.Connection) -> None:
    colunas = {r["name"] for r in conn.execute("PRAGMA table_info(aprovacoes)")}
    for coluna in ("alvo", "chave_idem"):
        if coluna not in colunas:
            conn.execute(f"ALTER TABLE aprovacoes ADD COLUMN {coluna} TEXT")


def _migrar(conn: sqlite3.Connection) -> None:
    """The F4 columns and indexes, idempotent. A database that already holds two OPEN
    requests for one target cannot take the unique index: it is skipped with an ERROR and
    tried again on the next start (they expire in 30 min); a request is never deleted."""
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS aprovacoes_chave_idem ON aprovacoes (chave_idem) "
                 "WHERE chave_idem IS NOT NULL")
    try:
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS aprovacoes_um_aberto ON aprovacoes (acao, alvo) "
                     "WHERE estado IN ('pendente', 'executando') AND alvo IS NOT NULL")
    except sqlite3.IntegrityError as exc:
        logger.error("Indice aprovacoes_um_aberto NAO criado (ha pedidos abertos duplicados): %s. "
                     "A transacao do criar continua impedindo novos; tento de novo no proximo inicio.", exc)


def _agora() -> datetime:
    return datetime.now().replace(microsecond=0)


def _texto(valor: Any) -> str | None:
    if valor is None:
        return None
    texto = "".join(c if ord(c) >= 32 else " " for c in str(valor)).strip()
    return texto[:LIMITE_TEXTO] or None


def _linha(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    d = dict(row)
    for campo in ("parametros", "previa", "resultado"):
        d[campo] = json.loads(d[campo]) if d.get(campo) else None
    return d


def _expira_vencidos(conn: sqlite3.Connection, agora: datetime) -> None:
    conn.execute("UPDATE aprovacoes SET estado = 'expirado' WHERE estado = 'pendente' AND expira_em <= ?",
                 (agora.isoformat(),))


#: The API restarting ITSELF: the approved action is the API's own death, so at the next start
#: the request is the proof it ran (``reinicio`` hands it to a detached process).
_REINICIO_DA_API = json.dumps({"servico": "OrcaView-OS-API"}, ensure_ascii=False)


def _varre_orfaos(conn: sqlite3.Connection, agora: datetime, minutos: int = ORFA_MIN) -> None:
    """``executando`` with no outcome after ``minutos`` ends ``falhou`` ("sem desfecho") -- the
    API's own restart ends ``executado`` instead: that it is answering again is its outcome."""
    limite = (agora - timedelta(minutes=minutos)).isoformat()
    conn.execute(
        "UPDATE aprovacoes SET estado = 'executado', resultado = ?, concluido_em = ? "
        "WHERE estado = 'executando' AND acao = 'reiniciar_servico' AND parametros = ? AND decidido_em <= ?",
        (json.dumps({"ok": True, "motivo": "a API voltou: o reinício dela foi concluído"}), agora.isoformat(),
         _REINICIO_DA_API, limite))
    n = conn.execute(
        "UPDATE aprovacoes SET estado = 'falhou', resultado = ?, concluido_em = ? "
        "WHERE estado = 'executando' AND decidido_em <= ?",
        (json.dumps({"motivo": "sem desfecho"}), agora.isoformat(), limite)).rowcount
    if n:
        logger.error("%d pedido(s) 'executando' sem desfecho marcados como falhou", n)


def varrer_orfaos(*, agora: datetime | None = None, arquivo: Path | None = None) -> None:
    """At the API's start EVERY ``executando`` is an orphan: the approved actions run on the
    API's own (daemon) threads, which died with the process that is starting again."""
    with _trava:
        conn = _conectar(arquivo)
        try:
            _varre_orfaos(conn, agora or _agora(), minutos=0)
        finally:
            conn.close()


def _novo_codigo(conn: sqlite3.Connection) -> str:
    """Unique among the requests still pending (a code is only typed while pending)."""
    for _ in range(50):
        codigo = "".join(secrets.choice(ALFABETO_CODIGO) for _ in range(TAMANHO_CODIGO))
        if not conn.execute("SELECT 1 FROM aprovacoes WHERE codigo = ? AND estado = 'pendente'",
                            (codigo,)).fetchone():
            return codigo
    raise RuntimeError("não consegui gerar um código livre")


def _ja_existe(conn: sqlite3.Connection, acao: str, alvo: str | None, chave_idem: str | None) -> dict | None:
    """The request this one would repeat: same idempotency key (any state), or an OPEN one
    for the same target."""
    row = None
    if chave_idem:
        row = conn.execute("SELECT * FROM aprovacoes WHERE chave_idem = ?", (chave_idem,)).fetchone()
        if row is not None and (row["acao"] != acao or (alvo is not None and row["alvo"] != alvo)):
            raise AprovacaoInvalida("chave_reusada", "Idempotency-Key já usada por outro pedido.")
    if row is None and alvo is not None:
        row = conn.execute(
            "SELECT * FROM aprovacoes WHERE acao = ? AND alvo = ? AND estado IN ('pendente', 'executando') "
            "ORDER BY criado_em LIMIT 1", (acao, alvo)).fetchone()
    return _linha(row)


def existente(acao: str, *, alvo: str | None = None, chave_idem: str | None = None,
              agora: datetime | None = None, arquivo: Path | None = None) -> dict | None:
    """Read-only pre-check (the route answers a repeat without building a new preview). The
    decision that counts is the one inside :func:`criar`'s transaction."""
    with _trava:
        conn = _conectar(arquivo)
        try:
            _expira_vencidos(conn, agora or _agora())
            achado = _ja_existe(conn, acao, alvo, chave_idem)
            return {**achado, "ja_existia": True} if achado else None
        finally:
            conn.close()


def criar(acao: str, parametros: dict, previa: dict, *, pedido_por: str, em_nome_de: str | None = None,
          motivo: str | None = None, alvo: str | None = None, chave_idem: str | None = None,
          por_hora: int | None = None, agora: datetime | None = None, arquivo: Path | None = None) -> dict:
    """Records a pending request, or returns the one it repeats (``ja_existia`` True). It grants
    nothing: only :func:`decidir` runs it. Raises ``AprovacaoInvalida('limite')`` past
    ``por_hora`` requests of ``acao`` in the last hour -- counted AFTER the repeat check, so a
    retry never becomes a 429."""
    agora = agora or _agora()
    with _trava:
        conn = _conectar(arquivo)
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                _expira_vencidos(conn, agora)
                _varre_orfaos(conn, agora)
                achado = _ja_existe(conn, acao, alvo, chave_idem)
                if achado is not None:
                    conn.execute("COMMIT")
                    return {**achado, "ja_existia": True}
                if por_hora is not None:
                    recentes = conn.execute("SELECT COUNT(*) FROM aprovacoes WHERE acao = ? AND criado_em > ?",
                                            (acao, (agora - timedelta(minutes=60)).isoformat())).fetchone()[0]
                    if recentes >= por_hora:
                        raise AprovacaoInvalida("limite", f"Limite de {por_hora} pedido(s) por hora.")
                registro = {
                    "id": secrets.token_hex(8), "codigo": _novo_codigo(conn), "acao": acao,
                    "parametros": json.dumps(parametros, ensure_ascii=False, default=str),
                    "previa": json.dumps(previa, ensure_ascii=False, default=str),
                    "motivo": _texto(motivo), "pedido_por": _texto(pedido_por) or "?",
                    "em_nome_de": _texto(em_nome_de), "criado_em": agora.isoformat(),
                    "expira_em": (agora + timedelta(minutes=VALIDADE_MIN)).isoformat(), "estado": "pendente",
                    "alvo": alvo, "chave_idem": chave_idem,
                }
                colunas = ", ".join(registro)
                conn.execute(f"INSERT INTO aprovacoes ({colunas}) VALUES ({', '.join('?' * len(registro))})",
                             tuple(registro.values()))
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            return {**_linha(conn.execute("SELECT * FROM aprovacoes WHERE id = ?", (registro["id"],)).fetchone()),
                    "ja_existia": False}
        finally:
            conn.close()


def obter(chave: str, *, agora: datetime | None = None, arquivo: Path | None = None) -> dict | None:
    """By id, or by the short code of a PENDING request."""
    chave = (chave or "").strip()
    if not chave:
        return None
    with _trava:
        conn = _conectar(arquivo)
        try:
            _expira_vencidos(conn, agora or _agora())
            row = conn.execute("SELECT * FROM aprovacoes WHERE id = ?", (chave,)).fetchone()
            if row is None and len(chave) == TAMANHO_CODIGO:
                row = conn.execute("SELECT * FROM aprovacoes WHERE codigo = ? AND estado = 'pendente'",
                                   (chave,)).fetchone()
            return _linha(row)
        finally:
            conn.close()


def listar(estado: str | None = None, *, limite: int = 30, agora: datetime | None = None,
           arquivo: Path | None = None) -> list[dict]:
    """Newest first; ``estado`` filters (``pendente`` = what is waiting for a person)."""
    with _trava:
        conn = _conectar(arquivo)
        try:
            _expira_vencidos(conn, agora or _agora())
            if estado:
                rows = conn.execute("SELECT * FROM aprovacoes WHERE estado = ? ORDER BY criado_em DESC LIMIT ?",
                                    (estado, int(limite))).fetchall()
            else:
                rows = conn.execute("SELECT * FROM aprovacoes ORDER BY criado_em DESC LIMIT ?",
                                    (int(limite),)).fetchall()
            return [_linha(r) for r in rows]
        finally:
            conn.close()


def contar_recentes(acao: str, minutos: int = 60, *, agora: datetime | None = None,
                    arquivo: Path | None = None) -> int:
    """Requests of ``acao`` created in the last ``minutos`` — the hourly cap (rule 6)."""
    agora = agora or _agora()
    with _trava:
        conn = _conectar(arquivo)
        try:
            return conn.execute("SELECT COUNT(*) FROM aprovacoes WHERE acao = ? AND criado_em > ?",
                                (acao, (agora - timedelta(minutes=minutos)).isoformat())).fetchone()[0]
        finally:
            conn.close()


def decidir(chave: str, *, aprovar: bool, pessoa: str, canal: str, papel: str | None = None,
            cliente: str | None = None, motivo: str | None = None, agora: datetime | None = None,
            arquivo: Path | None = None) -> dict:
    """``pendente`` → ``executando`` (approved) or ``recusado``, once. The caller runs the action
    after an approval and reports back with :func:`concluir`."""
    agora = agora or _agora()
    atual = obter(chave, agora=agora, arquivo=arquivo)
    if atual is None:
        raise AprovacaoInvalida("nao_encontrado", "Pedido de aprovação não encontrado (id ou código).")
    if atual["estado"] == "expirado":
        raise AprovacaoInvalida("expirado", f"O pedido {atual['codigo']} expirou; o agente precisa pedir de novo.")
    if atual["estado"] != "pendente":
        raise AprovacaoInvalida("ja_decidido", f"O pedido {atual['codigo']} já está '{atual['estado']}'.")
    with _trava:
        conn = _conectar(arquivo)
        try:
            mudou = conn.execute(
                "UPDATE aprovacoes SET estado = ?, decidido_por = ?, decidido_canal = ?, decidido_papel = ?, "
                "decidido_cliente = ?, decidido_em = ?, motivo_recusa = ? "
                "WHERE id = ? AND estado = 'pendente' AND expira_em > ?",
                ("executando" if aprovar else "recusado", _texto(pessoa), _texto(canal), _texto(papel),
                 _texto(cliente), agora.isoformat(), None if aprovar else _texto(motivo),
                 atual["id"], agora.isoformat()),
            ).rowcount
            if not mudou:   # another decision (or the expiry) won between the read and here
                raise AprovacaoInvalida("ja_decidido", f"O pedido {atual['codigo']} acabou de ser decidido.")
            return _linha(conn.execute("SELECT * FROM aprovacoes WHERE id = ?", (atual["id"],)).fetchone())
        finally:
            conn.close()


def concluir(id_: str, *, ok: bool, resultado: dict, agora: datetime | None = None,
             arquivo: Path | None = None) -> dict | None:
    """``executando`` → ``executado`` / ``falhou``, with what the action returned."""
    with _trava:
        conn = _conectar(arquivo)
        try:
            conn.execute(
                # Also over a "sem desfecho" the sweeper wrote: a slow action's real outcome wins.
                "UPDATE aprovacoes SET estado = ?, resultado = ?, concluido_em = ? "
                "WHERE id = ? AND (estado = 'executando' OR (estado = 'falhou' AND resultado LIKE '%sem desfecho%'))",
                ("executado" if ok else "falhou", json.dumps(resultado, ensure_ascii=False, default=str),
                 (agora or _agora()).isoformat(), id_))
            return _linha(conn.execute("SELECT * FROM aprovacoes WHERE id = ?", (id_,)).fetchone())
        finally:
            conn.close()
