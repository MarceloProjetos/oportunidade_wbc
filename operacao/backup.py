"""The backups' state, as the ALTHOST reports it (F7 of the web repo's docs/PLANO_TEO_REDE_E_ROTINAS.md).

The Veeam console lives on the ALTHOST (192.168.7.250); the replicas on ALTHOSTBKP (.251) and ALTBKP2
(.252). No agent holds a password there (WinRM to them is refused anyway), so the ALTHOST PUSHES: a
scheduled task runs ``maintenance/estado_backup.ps1`` every hour as SYSTEM, reads the Veeam module (last
session of each job, newest restore point of each replica, free space of the repositories, the Veeam
services) and POSTs the JSON to ``/operacao/backup/estado`` with a key of scope ``backup:relatar``. The
Téo (and the Mira) read ``GET /operacao/backup``.

The body is untrusted text from another machine: :func:`validar` keeps only known keys, with types and
sizes checked, cuts every string and drops the rest. The .11's own receive time is what counts (the
ALTHOST's clock may be wrong: its ``gerado_em`` goes along as information only).
"""
from __future__ import annotations

import json
import math
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parents[1]
ARQUIVO = RAIZ / "state" / "backup_althost.json"

#: The request body cap (the route checks it before reading; waitress's own default is 1 GiB).
CORPO_MAX = 64 * 1024
#: Where a report may come from (second lock: the key is the first). The ALTHOST is a Hyper-V host
#: and may leave through another address of the /21 -- confirm the real one in the audit on the
#: first test and add it here.
ORIGENS = frozenset({"192.168.7.250", "127.0.0.1"})
TEXTO_MAX = 120
MENSAGEM_MAX = 300
ITENS_MAX = 60
_DATA = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")

_trava = threading.Lock()


class RelatoInvalido(ValueError):
    """The body is not a backup report of the expected shape."""


def _texto(v: Any, limite: int = TEXTO_MAX) -> str | None:
    if v is None:
        return None
    if not isinstance(v, (str, int, float, bool)):
        raise RelatoInvalido("campo de texto com tipo errado")
    s = " ".join(str(v).split())
    return s[:limite] or None


def _data(v: Any) -> str | None:
    if v in (None, ""):
        return None
    if not isinstance(v, str) or not _DATA.match(v[:19]):
        raise RelatoInvalido("data fora do formato AAAA-MM-DDTHH:MM:SS")
    return v[:19]


def _numero(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
        raise RelatoInvalido("número inválido")
    return round(float(v), 1)


def _lista(v: Any, nome: str, cortes: list[str]) -> list[dict[str, Any]]:
    if v is None:
        return []
    if not isinstance(v, list):
        raise RelatoInvalido(f"'{nome}' tem de ser uma lista")
    if len(v) > ITENS_MAX:
        # One VM too many must not cost the whole report: keep the first ones and say so.
        cortes.append(f"{nome}: {len(v)} itens, só os primeiros {ITENS_MAX} foram guardados")
        v = v[:ITENS_MAX]
    if any(not isinstance(i, dict) for i in v):
        raise RelatoInvalido(f"'{nome}' tem de ser uma lista de objetos")
    return v


def validar(corpo: Any) -> dict[str, Any]:
    """The report reduced to the known shape. Raises :class:`RelatoInvalido`."""
    if not isinstance(corpo, dict):
        raise RelatoInvalido("o corpo tem de ser um objeto JSON")
    versao = corpo.get("versao_do_script")
    if not isinstance(versao, int) or isinstance(versao, bool) or not 0 < versao < 1000:
        raise RelatoInvalido("'versao_do_script' ausente ou inválida")
    erros = corpo.get("erros")
    if erros is not None and not isinstance(erros, list):
        raise RelatoInvalido("'erros' tem de ser uma lista")
    cortes: list[str] = []
    relato = {
        "versao_do_script": versao,
        "gerado_em": _data(corpo.get("gerado_em")),
        "maquina": _texto(corpo.get("host")),
        "jobs": [{"nome": _texto(j.get("nome")), "tipo": _texto(j.get("tipo"), 40),
                  "habilitado": bool(j.get("habilitado")) if j.get("habilitado") is not None else None,
                  "resultado": _texto(j.get("resultado"), 40), "estado": _texto(j.get("estado"), 40),
                  "inicio": _data(j.get("inicio")), "fim": _data(j.get("fim"))}
                 for j in _lista(corpo.get("jobs"), "jobs", cortes)],
        "replicas": [{"job": _texto(r.get("job")), "vm": _texto(r.get("vm")),
                      "ultimo_ponto": _data(r.get("ultimo_ponto"))}
                     for r in _lista(corpo.get("replicas"), "replicas", cortes)],
        "repositorios": [{"nome": _texto(r.get("nome")), "total_gb": _numero(r.get("total_gb")),
                          "livre_gb": _numero(r.get("livre_gb"))}
                         for r in _lista(corpo.get("repositorios"), "repositorios", cortes)],
        "servicos": [{"nome": _texto(s.get("nome"), 60), "estado": _texto(s.get("estado"), 30)}
                     for s in _lista(corpo.get("servicos"), "servicos", cortes)],
        "erros": [_texto(e, MENSAGEM_MAX) for e in (erros or [])[:10] if isinstance(e, (str, int, float))],
    }
    relato["erros"] += cortes
    return relato


def gravar(relato: dict[str, Any], origem: str, agora: datetime | None = None, arquivo: Path = ARQUIVO) -> None:
    """Keep the last report with the .11's receive time (atomic replace, as ``ronda_90``)."""
    dados = {**relato, "recebido_em": (agora or datetime.now()).isoformat(timespec="seconds"), "origem": origem}
    with _trava:
        arquivo.parent.mkdir(parents=True, exist_ok=True)
        temporario = arquivo.with_suffix(".tmp")
        temporario.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
        for tentativa in range(3):
            try:
                temporario.replace(arquivo)
                return
            except PermissionError:  # another handle on the target (the reading route, the antivirus)
                if tentativa == 2:
                    raise
                time.sleep(0.2)


def ler(agora: datetime | None = None, arquivo: Path = ARQUIVO) -> dict[str, Any]:
    """The last report and its age by the .11's receive time; ``relato: None`` before the first one."""
    try:
        dados = json.loads(arquivo.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"relato": None, "idade_min": None,
                "motivo": "o ALTHOST ainda não mandou nenhum relato (a tarefa de hora em hora não está instalada?)"}
    except (OSError, ValueError):
        return {"relato": None, "idade_min": None, "motivo": "o último relato guardado está ilegível"}
    try:
        recebido = datetime.fromisoformat(str(dados.get("recebido_em")))
        idade = max(0, round(((agora or datetime.now()) - recebido).total_seconds() / 60))
    except ValueError:
        idade = None
    return {"relato": dados, "idade_min": idade}
