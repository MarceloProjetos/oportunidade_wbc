"""Last known status of each order, so the API never sends an existing order without one.

SAP is the source. This file only remembers what SAP last said: when it cannot answer, or a row
comes with an empty status, the API answers with the remembered value and says so
(``status_origem``). It is an **output** concern: no decision of the API may read it, because a
remembered "Aberto" must never gate a write.

Kept in SQLite on the .11 (``state/ultimo_status_pedido.db``), the same way as the approvals: it
has to survive a restart, which an in-memory cache would not, and it needs no Supabase object.

Nothing here raises: a broken file must cost the fallback, never the answer that was on its way.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from collections.abc import Iterable
from pathlib import Path

from situacao_pedidos import now_br

logger = logging.getLogger(__name__)

ARQUIVO_PADRAO = Path(__file__).resolve().parent / "state" / "ultimo_status_pedido.db"

#: How often an unchanged status is written again. The list runs through here on every request,
#: and ``visto_em`` does not need more precision than this.
REFRESCO_S = 600.0

_escritos: dict[tuple[str, int], tuple[str, float]] = {}
_escritos_lock = threading.Lock()


def _arquivo() -> Path:
    return Path(os.environ.get("SIS_ULTIMO_STATUS_ARQUIVO") or ARQUIVO_PADRAO)


def _conectar(arquivo: Path) -> sqlite3.Connection:
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(arquivo, timeout=10)
    conn.execute("""CREATE TABLE IF NOT EXISTS ultimo_status (
        doc_num INTEGER PRIMARY KEY, doc_entry INTEGER, status TEXT NOT NULL, visto_em TEXT NOT NULL)""")
    conn.execute("CREATE INDEX IF NOT EXISTS ultimo_status_entry ON ultimo_status (doc_entry)")
    return conn


def _limpos(itens: Iterable[tuple[object, object, object]]) -> list[tuple[int, int | None, str]]:
    """Valid ``(doc_num, doc_entry, status)`` rows only: an empty status is not knowledge."""
    limpos = []
    for doc_num, doc_entry, status in itens:
        texto = str(status or "").strip()
        if doc_num is None or not texto:
            continue
        limpos.append((int(doc_num), int(doc_entry) if doc_entry is not None else None, texto))
    return limpos


def registrar_muitos(itens: Iterable[tuple[object, object, object]]) -> None:
    """Remember what SAP just said. Unchanged rows are written at most every :data:`REFRESCO_S`."""
    arquivo = _arquivo()
    agora = time.monotonic()
    novos = []
    with _escritos_lock:
        for doc_num, doc_entry, status in _limpos(itens):
            chave = (str(arquivo), doc_num)
            anterior = _escritos.get(chave)
            if anterior and anterior[0] == status and agora - anterior[1] < REFRESCO_S:
                continue
            _escritos[chave] = (status, agora)
            novos.append((doc_num, doc_entry, status))
    if not novos:
        return
    visto_em = now_br().isoformat(timespec="seconds")
    try:
        with _conectar(arquivo) as conn:
            conn.executemany(
                """INSERT INTO ultimo_status (doc_num, doc_entry, status, visto_em) VALUES (?, ?, ?, ?)
                   ON CONFLICT (doc_num) DO UPDATE SET status = excluded.status, visto_em = excluded.visto_em,
                   doc_entry = COALESCE(excluded.doc_entry, doc_entry)""",
                [(d, e, s, visto_em) for d, e, s in novos],
            )
    except (sqlite3.Error, OSError) as exc:
        logger.error("[ULTIMO_STATUS] falha ao gravar %d pedido(s): %s", len(novos), exc)
        with _escritos_lock:  # not written: the next read must try again
            for doc_num, _, _ in novos:
                _escritos.pop((str(arquivo), doc_num), None)


def registrar(doc_num: object, status: object, doc_entry: object = None) -> None:
    """:func:`registrar_muitos` for one order."""
    registrar_muitos([(doc_num, doc_entry, status)])


def semear(itens: Iterable[tuple[object, object, object]]) -> int:
    """Add orders never seen, keeping what is already remembered. Returns how many were offered."""
    limpos = _limpos(itens)
    if not limpos:
        return 0
    visto_em = now_br().isoformat(timespec="seconds")
    try:
        with _conectar(_arquivo()) as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO ultimo_status (doc_num, doc_entry, status, visto_em) VALUES (?, ?, ?, ?)",
                [(d, e, s, visto_em) for d, e, s in limpos],
            )
    except (sqlite3.Error, OSError) as exc:
        logger.error("[ULTIMO_STATUS] falha na carga inicial: %s", exc)
        return 0
    return len(limpos)


def ler(numero: int, campo: str = "doc_num") -> dict[str, str] | None:
    """``{status, visto_em}`` of an order by ``doc_num`` or ``doc_entry``; ``None`` if never seen."""
    coluna = "doc_entry" if campo == "doc_entry" else "doc_num"
    try:
        with _conectar(_arquivo()) as conn:
            linha = conn.execute(
                f"SELECT status, visto_em FROM ultimo_status WHERE {coluna} = ?", (int(numero),)
            ).fetchone()
    except (sqlite3.Error, OSError) as exc:
        logger.error("[ULTIMO_STATUS] falha ao ler o pedido %s: %s", numero, exc)
        return None
    return {"status": linha[0], "visto_em": linha[1]} if linha else None
