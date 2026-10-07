"""Is the .90 up, seen FROM the .11? — a second point of view (F6 of PLANO_AGENTE_TI, web repo).

Every watcher of the OrçaView lives on the .90 itself (the Zelador, the vigia, the watchdog):
when the .90 goes down, nobody writes down when. This one does, from the .11: every
:data:`INTERVALO_S` it tests the closed-list destination ``orcaview-90`` (DNS, ping, TCP 443
and 8000, through :func:`operacao.conexoes.testar`) and keeps, in ``state/ronda_90.json``, the
current state and the periods it saw the .90 down, with the real times. The Zelador reads them
when it wakes up (``GET /operacao/ronda-90``) and writes in its diary what it missed asleep.

Read only, like the rest of ``operacao/``: no restart, no Wake-on-LAN (the WoL task already
exists), no message. On only on the .11 (machine identity, never a ``.env`` switch); started
from the API's entrypoint, never on import.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from operacao import conexoes

logger = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parents[1]
ARQUIVO_PADRAO = RAIZ / "state" / "ronda_90.json"
DESTINO = "orcaview-90"
INTERVALO_S = 300
#: Periods older than this are dropped (the Zelador reads the last days only).
DIAS_GUARDADOS = 14

_trava = threading.Lock()
_thread: threading.Thread | None = None


def _agora() -> datetime:
    return datetime.now().astimezone()


def _vazio() -> dict[str, Any]:
    return {"estado": None, "desde": None, "como": None, "ultima_leitura": None, "periodos": [],
            "suspeita": None, "desde_apos": None}


def ler(arquivo: Path = ARQUIVO_PADRAO) -> dict[str, Any]:
    """The state as stored; an unreadable file is an empty state (never a crash)."""
    try:
        dados = json.loads(arquivo.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _vazio()
    return {**_vazio(), **dados} if isinstance(dados, dict) else _vazio()


def _gravar(dados: dict[str, Any], arquivo: Path) -> None:
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    temporario = arquivo.with_suffix(".tmp")
    temporario.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
    # On Windows the replace fails (WinError 5/32) while another handle holds the target: the
    # route reading it, the antivirus, the indexer. A short retry covers those.
    for tentativa in range(3):
        try:
            temporario.replace(arquivo)
            return
        except PermissionError:
            if tentativa == 2:
                raise
            time.sleep(0.2)


def classificar(resultado: dict[str, Any]) -> tuple[str, str | None]:
    """``("ok" | "fora", como)`` from one :func:`conexoes.testar`: up when any port opened;
    down as "desligado" (no ping either) or "app fora" (the machine answers, the ports do not).
    A ping that did not even run (``responde`` None) says nothing: ``como`` None, never "desligado"."""
    if any(p.get("aberta") for p in resultado.get("portas") or []):
        return "ok", None
    responde = (resultado.get("ping") or {}).get("responde")
    if responde is None:
        return "fora", None
    return "fora", "app fora" if responde else "desligado"


_GRAVIDADE = {None: 0, "app fora": 1, "desligado": 2}


def _pior(a: str | None, b: str | None) -> str | None:
    """The worst seen wins: at boot the machine answers ping before the app opens its ports, so
    "desligado" must never turn back into "app fora" for the same period."""
    return a if _GRAVIDADE.get(a, 0) >= _GRAVIDADE.get(b, 0) else b


def _lacuna(antes: str | None, quando: datetime) -> bool:
    """True when the .11 itself missed readings (its own restart, a deploy): the time it writes
    for a change is then only "somewhere after" the previous reading."""
    if not antes:
        return False
    try:
        return (quando - datetime.fromisoformat(antes)).total_seconds() > 2 * INTERVALO_S
    except (TypeError, ValueError):
        return False


def aplicar(dados: dict[str, Any], estado: str, como: str | None, quando: datetime) -> dict[str, Any]:
    """One reading folded into the state (pure but for its arguments).

    A period opens only on the SECOND "fora" in a row (one network hiccup is no period), dated
    by the first one; a change back to "ok" closes it with its start and end. When the .11 missed
    readings around a change, the period also says after which reading it started/ended
    (``inicio_apos``/``fim_apos``). Old periods are dropped."""
    agora_iso = quando.isoformat(timespec="seconds")
    antes = dados.get("ultima_leitura")
    novo = {**_vazio(), **dados, "ultima_leitura": agora_iso, "periodos": list(dados.get("periodos") or [])}
    suspeita = dados.get("suspeita")
    if estado == "fora":
        if dados.get("estado") == "fora":
            novo["como"] = _pior(dados.get("como"), como)
        elif suspeita:
            novo.update(estado="fora", desde=suspeita.get("em"), como=_pior(suspeita.get("como"), como),
                        desde_apos=suspeita.get("antes"), suspeita=None)
        else:
            novo["suspeita"] = {"em": agora_iso, "como": como, "antes": antes if _lacuna(antes, quando) else None}
    else:
        novo["suspeita"] = None
        if dados.get("estado") == "fora" and dados.get("desde"):
            periodo = {"inicio": dados["desde"], "fim": agora_iso, "como": dados.get("como")}
            if dados.get("desde_apos"):
                periodo["inicio_apos"] = dados["desde_apos"]
            if _lacuna(antes, quando):
                periodo["fim_apos"] = antes
            novo["periodos"].append(periodo)
        if dados.get("estado") != "ok":
            novo.update(estado="ok", desde=agora_iso, como=None, desde_apos=None)
    corte = (quando - timedelta(days=DIAS_GUARDADOS)).isoformat(timespec="seconds")
    novo["periodos"] = [p for p in novo["periodos"] if str(p.get("fim") or "") >= corte]
    return novo


def rodar_uma_vez(arquivo: Path = ARQUIVO_PADRAO) -> dict[str, Any]:
    """Test the .90 once and store the result. Never raises (the loop must not die)."""
    try:
        estado, como = classificar(conexoes.testar(DESTINO))
    except Exception as exc:  # a broken test is no reading: the state stays as it was
        logger.warning("[RONDA-90] teste não rodou: %s", exc)
        return ler(arquivo)
    with _trava:
        dados = aplicar(ler(arquivo), estado, como, _agora())
        try:
            _gravar(dados, arquivo)
        except OSError as exc:  # disk full, a handle that never let go: this reading is lost, not the loop
            logger.warning("[RONDA-90] não gravou o estado: %s", exc)
    return dados


def _laco(arquivo: Path) -> None:
    while True:
        try:
            rodar_uma_vez(arquivo)
        except Exception:  # the thread must never die in silence (the .90 would read a frozen state)
            logger.exception("[RONDA-90] passada falhou")
        time.sleep(INTERVALO_S)


def iniciar(arquivo: Path = ARQUIVO_PADRAO) -> threading.Thread | None:
    """Start the round on a daemon thread, once per process, only on the .11 (machine identity).
    Called from the API's entrypoint, never on import (the test suite must not ping the .90)."""
    global _thread
    from wbcpython.safety import is_production_machine

    if not is_production_machine():
        logger.info("[RONDA-90] fora da .11: ronda do .90 desligada.")
        return None
    if _thread is not None and _thread.is_alive():
        return _thread
    _thread = threading.Thread(target=_laco, args=(arquivo,), name="ronda-90", daemon=True)
    _thread.start()
    logger.info("[RONDA-90] ronda do .90 a cada %ss (só leitura).", INTERVALO_S)
    return _thread


def publico(arquivo: Path = ARQUIVO_PADRAO) -> dict[str, Any]:
    """What ``GET /operacao/ronda-90`` answers: the stored state plus the round's own settings."""
    return {**ler(arquivo), "intervalo_s": INTERVALO_S, "destino": DESTINO}
