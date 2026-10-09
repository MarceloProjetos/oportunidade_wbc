"""Windows services that stopped answering in the last hour (F5b of
web_orcaview_V118/docs/PLANO_TEO_REDE_E_ROTINAS.md): the early warning before a machine hangs.

Sibling file, IDENTICAL in MCPs/SAP_RDP (.12) and MCPs/ServidorIntegracaoSAP (.11): change both in
the same commit.

On 08/10 the .12's services stopped answering their controls one after the other -- IP Helper at
16:07:37, then NlaSvc, the Task Scheduler, the RDP port redirector, RasMan, the Spooler -- minutes
BEFORE its monitor API hung (16:11) and long before nobody could log in (16:26). The Téo reads this
every round, in a task of its own, while the machine's API still answers: three or more services
hanging in a row is a cascade, and a cascade starting with a network service points at the VM's
network or the host's virtual switch, not at a program.

Two fixed queries through ``wevtutil`` (F5c1 of the same plan): Service Control Manager 7011/7046/7022
(hung), 7031/7034 (crashed), 7000/7009 (did not start) of the last :data:`JANELA_MIN` minutes. ~0.05 s,
cached :data:`CACHE_S`; read only. ``wevtutil`` is the native reader: no PowerShell, no .NET start, no
WMI -- on 09/10 at 13:06 the PowerShell version took more than 15 s with the .12 hanging (and again
right after its boot), so the early warning never fired when it mattered. The boot comes from
``psutil`` for the same reason. The grouping and the cascade follow SAP_RDP's ``login_rdp``
(V1.21/V1.22), which reads 24 h for the logon diagnosis; this one is the light, frequent read.
"""
from __future__ import annotations

import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from typing import Any

#: wevtutil answers in ~0.05 s; a machine that takes this long is the answer itself. Per query: the
#: two together stay under the Téo's 15 s (review of F5c1).
TIMEOUT_S = 6
CACHE_S = 30.0
JANELA_MIN = 60
SCM_TRAVADO = frozenset({7011, 7046, 7022})
SCM_CAIU = frozenset({7031, 7034})
SCM_NAO_INICIOU = frozenset({7000, 7009})
#: Where the service name sits in each event's properties (7011/7009 carry the timeout first; 7046,
#: 7022, 7034 and 7000 carry the localized DISPLAY name, 7011 the KEY name).
_INDICE_DO_NOME = {7011: 1, 7009: 1}
#: The cascade counts 7011 only: one identifier per service (a 7046 "Agendador de Tarefas" next to
#: a 7011 "Schedule" would be two services and inflate it; review of F5b).
SCM_CASCATA = 7011
#: Hangs no more than this apart belong to one cascade; this many services make one.
ELO_CASCATA = timedelta(minutes=15)
CASCATA_MINIMA = 3
#: Network services: a cascade that starts with one of them points at the VM's network.
SERVICOS_DE_REDE = frozenset({"iphlpsvc", "NlaSvc", "RasMan", "Dnscache", "Dhcp", "netprofm",
                              "LanmanWorkstation", "nsi"})
LIMITE_TEXTO = 120

_EV = "{http://schemas.microsoft.com/win/2004/08/events/event}"
#: No console window flashes on the server when the service spawns wevtutil.
_SEM_JANELA = 0x08000000 if sys.platform == "win32" else 0


def _consulta(ids: frozenset[int]) -> str:
    """The XPath of one query: SCM, these ids, the last :data:`JANELA_MIN` minutes."""
    filtro = " or ".join(f"EventID={i}" for i in sorted(ids))
    return (f"*[System[Provider[@Name='Service Control Manager'] and ({filtro}) and "
            f"TimeCreated[timediff(@SystemTime) <= {JANELA_MIN * 60_000}]]]")


def eventos_do_xml(texto: str) -> list[dict]:
    """wevtutil's ``/f:xml`` output (Event elements one after the other, no root) as
    ``{quando (local, seconds), id, p: [first two Data values]}``. Raises ``ET.ParseError``."""
    raiz = ET.fromstring("<eventos>" + texto.lstrip(chr(0xFEFF)) + "</eventos>")
    saida = []
    for ev in raiz.iter(f"{_EV}Event"):
        sistema = ev.find(f"{_EV}System")
        if sistema is None:
            continue
        id_ = sistema.findtext(f"{_EV}EventID") or ""
        criado = sistema.find(f"{_EV}TimeCreated")
        utc = (criado.get("SystemTime") or "") if criado is not None else ""
        try:
            quando = (datetime.fromisoformat(utc[:19]).replace(tzinfo=UTC).astimezone()
                      .replace(tzinfo=None).isoformat(timespec="seconds"))
        except ValueError:
            continue
        dados = [(d.text or "")[:LIMITE_TEXTO] for d in ev.iter(f"{_EV}Data")][:2]
        if id_.isdigit():
            saida.append({"quando": quando, "id": int(id_), "p": dados})
    return saida


def _ler(ids: frozenset[int], maximo: int) -> tuple[list[dict] | None, str]:
    """One wevtutil query, newest first: ``(events, "")`` or ``(None, why)``."""
    try:
        r = subprocess.run(["wevtutil", "qe", "System", "/q:" + _consulta(ids), f"/c:{maximo}", "/rd:true",
                            "/f:xml", "/uni:true"], capture_output=True, timeout=TIMEOUT_S,
                           creationflags=_SEM_JANELA)
    except subprocess.TimeoutExpired:
        return None, f"a leitura do log passou de {TIMEOUT_S}s"
    except OSError as e:
        return None, f"wevtutil indisponível ({type(e).__name__})"
    if r.returncode != 0:
        bom = r.stderr[:2] == bytes((0xFF, 0xFE))
        erro = r.stderr.decode("utf-16-le" if bom else "mbcs" if sys.platform == "win32" else "utf-8",
                               errors="replace")
        return None, (" ".join(erro.split()) or f"wevtutil saiu com {r.returncode}")[:LIMITE_TEXTO]
    try:
        return eventos_do_xml(r.stdout.decode("utf-16-le", errors="replace")), ""
    except ET.ParseError:
        return None, "o log devolveu XML ilegível"


def _boot() -> str | None:
    try:
        import psutil

        return datetime.fromtimestamp(psutil.boot_time()).replace(microsecond=0).isoformat()
    except Exception:  # noqa: BLE001 - no psutil, or the counter unreadable: the read still goes
        return None


_trava = threading.Lock()
_cache: dict[str, Any] = {"em": 0.0, "dados": None}


def _dt(texto: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(texto)) if texto else None
    except ValueError:
        return None


def _nome(e: dict) -> str:
    props = e.get("p") or []
    if isinstance(props, str):  # ConvertTo-Json flattens a 1-item array
        props = [props]
    i = _INDICE_DO_NOME.get(e.get("id"), 0)
    return str(props[i])[:LIMITE_TEXTO] if len(props) > i and props[i] else "?"


def agrupar(eventos: list[dict]) -> dict[str, dict[str, list[str]]]:
    """SCM events by service: ``{nome: {travado, caiu, nao_iniciou, travado_chave: [times, newest
    first]}}``; ``travado_chave`` = the 7011 hangs only (key name), what the cascade counts."""
    grupos: dict[str, dict[str, list[str]]] = {}
    for e in eventos:
        tipo = ("travado" if e.get("id") in SCM_TRAVADO else "caiu" if e.get("id") in SCM_CAIU
                else "nao_iniciou" if e.get("id") in SCM_NAO_INICIOU else None)
        if tipo and _dt(e.get("quando")):
            g = grupos.setdefault(_nome(e), {"travado": [], "caiu": [], "nao_iniciou": [], "travado_chave": []})
            g[tipo].append(str(e["quando"]))
            if e.get("id") == SCM_CASCATA:
                g["travado_chave"].append(str(e["quando"]))
    for g in grupos.values():
        for lista in g.values():
            lista.sort(reverse=True)
    return grupos


def cascata(grupos: dict[str, dict[str, list[str]]]) -> dict[str, Any] | None:
    """The latest run of hangs whose consecutive events are at most :data:`ELO_CASCATA` apart, with
    its services in the order they FIRST hung; ``None`` below :data:`CASCATA_MINIMA` services."""
    pares = sorted((q, nome) for nome, g in grupos.items() for q in g.get("travado_chave") or [] if _dt(q))
    if not pares:
        return None
    bloco = [pares[-1]]
    for q, nome in reversed(pares[:-1]):
        if _dt(bloco[-1][0]) - _dt(q) > ELO_CASCATA:
            break
        bloco.append((q, nome))
    ordem: list[tuple[str, str]] = []
    for q, nome in sorted(bloco):
        if nome not in (n for _, n in ordem):
            ordem.append((q, nome))
    if len(ordem) < CASCATA_MINIMA:
        return None
    return {"inicio": ordem[0][0], "fim": max(q for q, _ in bloco), "rede": ordem[0][1] in SERVICOS_DE_REDE,
            "servicos": [{"nome": n, "primeiro": q} for q, n in ordem]}


def travamentos() -> dict[str, Any]:
    # A read already running answers the others at once (the last data, or "em andamento"): a caller
    # waiting on the lock holds a server thread, and threads running out is how the monitor's /health
    # died on 09/10 (review of F5c1).
    if not _trava.acquire(blocking=False):
        if _cache["dados"] is not None:
            return {**_cache["dados"], "cache": True}
        return {"disponivel": False, "motivo": "leitura em andamento"}
    try:
        return _ler_com_cache()
    finally:
        _trava.release()


def _ler_com_cache() -> dict[str, Any]:
    if _cache["dados"] is not None and time.monotonic() - _cache["em"] < CACHE_S:
        return {**_cache["dados"], "cache": True}
    # Two queries: a flood of crashes must not push the hangs out of the count.
    travados, erro = _ler(SCM_TRAVADO, 300)
    outros, erro_outros = _ler(SCM_CAIU | SCM_NAO_INICIOU, 100) if travados is not None else ([], "")
    if travados is None:
        resposta: dict[str, Any] = {"disponivel": False, "motivo": erro}
    else:
        grupos = agrupar([*travados, *(outros or [])])
        # ``ligou``: hangs right after a boot are the boot's own (slow start), not a warning.
        resposta = {"disponivel": True, "cache": False, "janela_min": JANELA_MIN,
                    "agora": datetime.now().isoformat(timespec="seconds"), "ligou": _boot(),
                    "servicos": grupos, "cascata": cascata(grupos)}
        if outros is None:  # the hangs are what the warning needs: partial, and said so
            resposta["parcial"] = erro_outros
    _cache.update(em=time.monotonic(), dados=resposta)
    return resposta
