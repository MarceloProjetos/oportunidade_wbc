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
"""
from __future__ import annotations

import json
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

_trava = threading.Lock()


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
    return conn


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


def _novo_codigo(conn: sqlite3.Connection) -> str:
    """Unique among the requests still pending (a code is only typed while pending)."""
    for _ in range(50):
        codigo = "".join(secrets.choice(ALFABETO_CODIGO) for _ in range(TAMANHO_CODIGO))
        if not conn.execute("SELECT 1 FROM aprovacoes WHERE codigo = ? AND estado = 'pendente'",
                            (codigo,)).fetchone():
            return codigo
    raise RuntimeError("não consegui gerar um código livre")


def criar(acao: str, parametros: dict, previa: dict, *, pedido_por: str, em_nome_de: str | None = None,
          motivo: str | None = None, agora: datetime | None = None, arquivo: Path | None = None) -> dict:
    """Records a pending request. It grants nothing: only :func:`decidir` runs it."""
    agora = agora or _agora()
    with _trava:
        conn = _conectar(arquivo)
        try:
            _expira_vencidos(conn, agora)
            registro = {
                "id": secrets.token_hex(8), "codigo": _novo_codigo(conn), "acao": acao,
                "parametros": json.dumps(parametros, ensure_ascii=False, default=str),
                "previa": json.dumps(previa, ensure_ascii=False, default=str),
                "motivo": _texto(motivo), "pedido_por": _texto(pedido_por) or "?",
                "em_nome_de": _texto(em_nome_de), "criado_em": agora.isoformat(),
                "expira_em": (agora + timedelta(minutes=VALIDADE_MIN)).isoformat(), "estado": "pendente",
            }
            colunas = ", ".join(registro)
            conn.execute(f"INSERT INTO aprovacoes ({colunas}) VALUES ({', '.join('?' * len(registro))})",
                         tuple(registro.values()))
            return _linha(conn.execute("SELECT * FROM aprovacoes WHERE id = ?", (registro["id"],)).fetchone())
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
                "UPDATE aprovacoes SET estado = ?, resultado = ?, concluido_em = ? "
                "WHERE id = ? AND estado = 'executando'",
                ("executado" if ok else "falhou", json.dumps(resultado, ensure_ascii=False, default=str),
                 (agora or _agora()).isoformat(), id_))
            return _linha(conn.execute("SELECT * FROM aprovacoes WHERE id = ?", (id_,)).fetchone())
        finally:
            conn.close()
