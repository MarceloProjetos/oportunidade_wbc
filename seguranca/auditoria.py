"""One JSON line per call, per service, kept 30 days (rule 4 of PLANO_MIRA_AGENTE_11.md (removed 2026-10-06)).

Why: on 29/09/2026 a caller of the OP status route closed >=552 OPs without stock movements
and nothing identified it — the API had no access log. An agent calling the .11 on its own
makes that more likely, not less.

``logs/auditoria/<servico>-AAAA-MM-DD.jsonl``: one file per service (the API, the Controle de
Produção and the MCP are separate processes — no shared file, no lock) and per day (retention
is "delete the files older than 30 days", decided by the owner on 02/10/2026). No route and no
credential deletes these files; only this retention does.

Recording never raises and never changes the answer: an audit problem is logged and the
request goes on. Whoever calls passes the path WITHOUT the key (``?key=`` masked).
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parents[1]
PASTA_PADRAO = RAIZ / "logs" / "auditoria"
RETENCAO_DIAS = 30
LIMITE_TEXTO = 500

_trava = threading.Lock()
_limpo_em: dict[str, date] = {}


def _pasta() -> Path:
    return Path(os.environ.get("SIS_AUDITORIA_PASTA") or PASTA_PADRAO)


def _curto(valor: Any) -> Any:
    if isinstance(valor, str) and len(valor) > LIMITE_TEXTO:
        return valor[:LIMITE_TEXTO] + "…"
    return valor


def limpar_antigos(pasta: Path | None = None, hoje: date | None = None) -> int:
    """Deletes the files older than ``RETENCAO_DIAS``. Returns how many went."""
    pasta = pasta or _pasta()
    hoje = hoje or date.today()
    limite = hoje - timedelta(days=RETENCAO_DIAS)
    apagados = 0
    for arquivo in pasta.glob("*-????-??-??.jsonl"):
        try:
            dia = date.fromisoformat(arquivo.stem[-10:])
        except ValueError:
            continue
        if dia < limite:
            try:
                arquivo.unlink()
                apagados += 1
            except OSError as exc:
                logger.warning("Auditoria: não consegui apagar %s: %s", arquivo, exc)
    return apagados


def registrar(servico: str, **campos: Any) -> None:
    """Appends one line. Never raises."""
    try:
        agora = datetime.now()
        pasta = _pasta()
        linha = {"ts": agora.isoformat(timespec="milliseconds"), "servico": servico,
                 **{k: _curto(v) for k, v in campos.items()}}
        texto = json.dumps(linha, ensure_ascii=False, default=str) + "\n"
        with _trava:
            pasta.mkdir(parents=True, exist_ok=True)
            with open(pasta / f"{servico}-{agora:%Y-%m-%d}.jsonl", "a", encoding="utf-8", newline="\n") as f:
                f.write(texto)
            if _limpo_em.get(servico) != agora.date():
                _limpo_em[servico] = agora.date()
                limpar_antigos(pasta, agora.date())
    except Exception as exc:  # noqa: BLE001 - auditing must not break the request
        logger.warning("Auditoria: falha ao registrar (%s): %s", servico, exc)


def ler(servico: str, dia: date | None = None, pasta: Path | None = None) -> list[dict]:
    """The lines of one service on one day (tests and the CLI)."""
    pasta = pasta or _pasta()
    arquivo = pasta / f"{servico}-{(dia or date.today()):%Y-%m-%d}.jsonl"
    if not arquivo.exists():
        return []
    return [json.loads(l) for l in arquivo.read_text(encoding="utf-8").splitlines() if l.strip()]
