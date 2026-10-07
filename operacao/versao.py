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

import re
from datetime import datetime
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parents[1]
LOG_DO_DEPLOY = RAIZ / "logs" / "deploy.log"
#: A run's lines are capped: a deploy writes ~15 of them; more means a garbled file.
LINHAS_POR_DEPLOY = 60
#: How many runs ``deploys_recentes`` returns: a week of deploys at the current pace.
DEPLOYS_RECENTES = 10

# ``%DATE% %TIME%`` follows the machine's locale: "Wed 10/07/2026 14:00:11.03" on the .11
# (en-US, month first, weekday prefix), "02/10/2026 11:01:00,00" on a pt-BR machine.
_MOMENTO = re.compile(r"^(?:(?P<dia_semana>[A-Za-z]{2,4})\.?\s+)?(?P<a>\d{1,2})/(?P<b>\d{1,2})/(?P<ano>\d{4})"
                      r"\s+(?P<h>\d{1,2}):(?P<m>\d{2}):(?P<s>\d{2})")
_DIAS_EN = frozenset({"mon", "tue", "wed", "thu", "fri", "sat", "sun"})
_DE_PARA = re.compile(r"de\s+([0-9a-f]{7,40})\s+para\s+([0-9a-f]{7,40})", re.IGNORECASE)


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


def momento_iso(texto: str) -> str | None:
    """A ``%DATE% %TIME%`` stamp as ISO (local time), or ``None`` when it cannot be read.

    Month first with an English weekday prefix (en-US, the .11), day first otherwise (pt-BR);
    a number above 12 settles the order either way.
    """
    m = _MOMENTO.match((texto or "").strip())
    if not m:
        return None
    a, b = int(m["a"]), int(m["b"])
    # An English weekday means an en-US date; a pt-BR one ("qua 07/10/2026") stays day first.
    mes_primeiro = (m["dia_semana"] or "").lower() in _DIAS_EN
    if a > 12:
        mes_primeiro = False
    elif b > 12:
        mes_primeiro = True
    mes, dia = (a, b) if mes_primeiro else (b, a)
    try:
        return datetime(int(m["ano"]), mes, dia, int(m["h"]), int(m["m"]), int(m["s"])).isoformat()
    except ValueError:
        return None


def _execucoes(arquivo: Path) -> list[list[dict[str, str]]] | None:
    """Every run in the file, oldest first (``None`` = no file)."""
    try:
        brutas = arquivo.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    linhas = [_separar(b) for b in brutas if b.strip()]
    inicios = [i for i, linha in enumerate(linhas) if linha["etapa"] == "inicio"]
    fins = [*inicios[1:], len(linhas)]
    return [linhas[i:f][:LINHAS_POR_DEPLOY] for i, f in zip(inicios, fins, strict=True)]


def _resumo(etapas: list[dict[str, str]]) -> dict[str, Any]:
    final = next((e for e in reversed(etapas) if e["etapa"] in ("fim", "abortado")), None)
    git = next((e["texto"] for e in etapas if e["etapa"] == "git"), "")
    de_para = _DE_PARA.search(git)
    return {
        "inicio": etapas[0]["momento"],
        "inicio_iso": momento_iso(etapas[0]["momento"]),
        "quem": etapas[0]["texto"],
        "resultado": (f"{final['etapa']}: {final['texto']}" if final
                      else "em_andamento_ou_interrompido"),
        "de": de_para[1][:7] if de_para else None,
        "para": de_para[2][:7] if de_para else None,
    }


def deploys_recentes(arquivo: Path = LOG_DO_DEPLOY, limite: int = DEPLOYS_RECENTES) -> list[dict[str, Any]]:
    """The last runs, newest first, without their steps (``ultimo_deploy`` has those).

    ``/operacao/deploy`` used to show only the last run, so a restart at 10:42 could no
    longer be tied to its deploy once another one ran at 14:00.
    """
    execucoes = _execucoes(arquivo) or []
    return [_resumo(e) for e in reversed(execucoes[-limite:])]


def ultimo_deploy(arquivo: Path = LOG_DO_DEPLOY) -> dict[str, Any]:
    """The last run of ``deploy_update.bat``: its steps and how it ended.

    ``resultado`` is the text of its ``fim``/``abortado`` line; with neither, the run is
    still going or the window was closed in the middle (``em_andamento_ou_interrompido``).
    """
    execucoes = _execucoes(arquivo)
    if execucoes is None:
        return {"registrado": False,
                "motivo": "nenhum deploy registrado ainda (logs/deploy.log não existe)"}
    if not execucoes:
        return {"registrado": False, "motivo": "logs/deploy.log sem nenhuma linha de início"}
    resumo = _resumo(execucoes[-1])
    return {
        "registrado": True,
        "inicio": resumo["inicio"],
        "inicio_iso": resumo["inicio_iso"],
        "quem": resumo["quem"],
        "resultado": resumo["resultado"],
        "deploys_registrados": len(execucoes),
        "etapas": execucoes[-1],
    }
