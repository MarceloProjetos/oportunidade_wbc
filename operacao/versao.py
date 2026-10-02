"""Which code is running, and how the last deploy went.

Until 02/10/2026 the deploy left nothing on disk — only the ``cmd`` window — so "the deploy
failed, what happened?" had no answer once the window closed. ``deploy_update.bat`` now
appends one line per step to ``logs/deploy.log``; this module reads the last run.

The commit is read from ``.git`` directly (no ``git`` process: the services run under NSSM
and their PATH is not guaranteed). It is taken once at import — when the process started —
and compared with the one on disk now: different means the code was updated but this
process was not restarted.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parents[1]
LOG_DO_DEPLOY = RAIZ / "logs" / "deploy.log"
#: A run's lines are capped: a deploy writes ~15 of them; more means a garbled file.
LINHAS_POR_DEPLOY = 60


def commit_no_disco(raiz: Path = RAIZ) -> str | None:
    """The full hash of HEAD, or ``None`` outside a git checkout."""
    git = raiz / ".git"
    try:
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not head.startswith("ref: "):
        return head or None
    ref = head[5:].strip()
    try:
        return (git / ref).read_text(encoding="utf-8").strip() or None
    except OSError:
        pass
    try:  # after `git gc` the branch lives only in packed-refs
        for linha in (git / "packed-refs").read_text(encoding="utf-8").splitlines():
            partes = linha.split()
            if len(partes) == 2 and partes[1] == ref:
                return partes[0]
    except OSError:
        pass
    return None


COMMIT_NO_INICIO = commit_no_disco()


def versao() -> dict[str, Any]:
    agora = commit_no_disco()
    return {
        "commit": (COMMIT_NO_INICIO or "")[:7] or None,
        "commit_no_disco": (agora or "")[:7] or None,
        "reinicio_pendente": bool(COMMIT_NO_INICIO and agora and agora != COMMIT_NO_INICIO),
    }


def _separar(linha: str) -> dict[str, str]:
    """``02/10/2026 11:02:08,12 | etapa | texto`` → campos (a line out of format stays whole)."""
    partes = [p.strip() for p in linha.split("|", 2)]
    if len(partes) < 3:
        return {"momento": "", "etapa": "", "texto": linha.strip()}
    return {"momento": partes[0], "etapa": partes[1], "texto": partes[2]}


def ultimo_deploy(arquivo: Path = LOG_DO_DEPLOY) -> dict[str, Any]:
    """The last run of ``deploy_update.bat``: its steps and how it ended.

    ``resultado`` is the text of its ``fim``/``abortado`` line; with neither, the run is
    still going or the window was closed in the middle (``em_andamento_ou_interrompido``).
    """
    try:
        brutas = arquivo.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {"registrado": False,
                "motivo": "nenhum deploy registrado ainda (logs/deploy.log não existe)"}
    linhas = [_separar(b) for b in brutas if b.strip()]
    inicios = [i for i, linha in enumerate(linhas) if linha["etapa"] == "inicio"]
    if not inicios:
        return {"registrado": False, "motivo": "logs/deploy.log sem nenhuma linha de início"}
    etapas = linhas[inicios[-1]:][:LINHAS_POR_DEPLOY]
    final = next((e for e in reversed(etapas) if e["etapa"] in ("fim", "abortado")), None)
    return {
        "registrado": True,
        "inicio": etapas[0]["momento"],
        "quem": etapas[0]["texto"],
        "resultado": (f"{final['etapa']}: {final['texto']}" if final
                      else "em_andamento_ou_interrompido"),
        "deploys_registrados": len(inicios),
        "etapas": etapas,
    }
