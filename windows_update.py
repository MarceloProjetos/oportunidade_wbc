"""Windows Update state: pending reboot + pending updates + last patch.

Separate from ``monitoring.py`` on purpose: its own concern, testable in isolation.
``monitoring.py`` only queries it in the ``windows_update`` check of ``/status``.

**This file is a PORT of ``windows_update.py`` in the SAP_RDP repo (the .12) and must stay
diffable against it** -- the logic is the same on both servers, and so is the invariant it
protects. Fix a bug here, carry it over there (and vice versa); only the header, the
consumers and SAP_RDP's periodic recollection (``WU_REFRESH_H``, collection ETA) differ. The
full plan (``PLANO_WINDOWS_UPDATE.md``) was removed from SAP_RDP on 2026-09-29; history is in git.

THE WHOLE DESIGN COMES FROM MEASUREMENTS ON THE REAL MACHINES (phase F0 of the plan):

  pending reboot (winreg)             0.18 ms  -> synchronous, always fresh
  last scan (COM AutoUpdate)          7-17 ms  -> synchronous
  last patch (Get-HotFix)             ~1.0 s   -> cache
  pending updates (COM Search)        3.1 s (.11) · 22.5 s (.12) · 30 s cold -> BACKGROUND

The search varies 10x between the machines and blows the 15 s timeout of whoever queries:
hence the expensive collection runs on a daemon thread fired at API start (~5 min later,
``WU_DELAY_START_S``), and ``/status`` only READS the cache. The boot is the schedule -- both
machines reboot every morning (.11 06:12 / .12 06:17), so there is no scheduler and no JSON
on disk.

THE TRAP THIS MODULE EXISTS TO AVOID: with the agent not scanning, the
``IsInstalled=0`` search ANSWERS -- expensive and confident -- and returns **0**. It is not
a handleable error. On the .12 that would make Mira show "0 updates pendentes ✅" on a server
611 days behind. So ``pendentes`` is only published when ``LastSearchSuccessDate`` is recent
(``WU_VARREDURA_MAX_D``, default 7 days); otherwise it is ``None`` with the reason in view.
Never "0" when it means "I don't know".
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
from datetime import datetime, timezone

from config import WU_DELAY_START_S_DEFAULT, Settings, get_settings

try:  # o monitor é Windows-only; o guard só evita quebrar coleta de teste fora do Windows
    import winreg
except ImportError:  # pragma: no cover
    winreg = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

__all__ = [
    "reboot_pendente",
    "patching_automatico",
    "estado_updates",
    "coletar_updates",
    "iniciar_coletor",
    "reset_cache",
]

# Chaves clássicas de reboot pendente. Nenhuma depende do wuauserv — provado na .12, que
# leu todas com o agente DESABILITADO.
_CHAVES_REBOOT = (
    (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending", "CBS"),
    (r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired", "WindowsUpdate"),
)
_SESSION_MANAGER = r"SYSTEM\CurrentControlSet\Control\Session Manager"
_SVC_WUAUSERV = r"SYSTEM\CurrentControlSet\Services\wuauserv"

_SVC_START_DISABLED = 4  # 2=Automatic 3=Manual 4=Disabled

# PowerShell que faz a parte cara. Roda via subprocess (o projeto não declara pywin32).
#
# A 1ª LINHA NÃO É ENFEITE: sem ela o PS escreve o stdout em **cp850**, não em UTF-8
# (medido: `[Console]::OutputEncoding.CodePage` = 850). O `erro` vem de mensagem do
# Windows, que é LOCALIZADA — na .12 os títulos saem em português mesmo com o SO em
# `en-US`. Sem forçar, `"Atualização"` chega aqui como `"Atualiza??o"`.
#
# ORDEM DE PROPÓSITO: lê a data da varredura ANTES de decidir buscar. Com o agente sem
# varrer, a busca cara é pulada — ela devolveria 0 e o 0 seria mentira.
_PS_COLETA = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'Stop'
$out = @{ ok = $true; erro = $null; ultima_varredura = $null; pendentes = $null;
          pendentes_motivo = $null; ultimo_patch = $null; ultimo_patch_kb = $null }
try {
    $au = New-Object -ComObject Microsoft.Update.AutoUpdate
    $ls = $au.Results.LastSearchSuccessDate
    if ($ls -and $ls.Year -gt 1601) { $out.ultima_varredura = $ls.ToUniversalTime().ToString('o') }
} catch {
    $out.ok = $false
    $out.erro = $_.Exception.Message
}
if ($out.ok) {
    $fresca = $false
    if ($out.ultima_varredura) {
        $idade = ((Get-Date).ToUniversalTime() - [datetime]::Parse($out.ultima_varredura).ToUniversalTime()).TotalDays
        $fresca = ($idade -le __MAX_DIAS__)
    }
    if ($fresca) {
        try {
            $s = (New-Object -ComObject Microsoft.Update.Session).CreateUpdateSearcher()
            $s.Online = $false   # cache local do agente: NAO vai a rede, NAO instala nada
            $out.pendentes = ($s.Search("IsInstalled=0 and IsHidden=0")).Updates.Count
        } catch {
            $out.pendentes_motivo = "busca falhou: $($_.Exception.Message)"
        }
    } else {
        $out.pendentes_motivo = 'o agente nao varre ha tempo demais - a contagem seria mentira'
    }
}
try {
    # Get-HotFix (WMI/CBS) NAO depende do wuauserv - e a unica fonte com o agente morto.
    # LastInstallationSuccessDate parece mais barato mas inclui ruido do Defender: divergiu
    # do Get-HotFix nas DUAS maquinas medidas. Nao trocar.
    $hf = @(Get-HotFix -ErrorAction Stop | Where-Object { $_.InstalledOn } |
            Sort-Object InstalledOn -Descending)
    if ($hf.Count) {
        $out.ultimo_patch    = $hf[0].InstalledOn.ToUniversalTime().ToString('o')
        $out.ultimo_patch_kb = $hf[0].HotFixID
    }
} catch { }
$out | ConvertTo-Json -Compress
"""


def _abrir(caminho: str) -> object:
    return winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, caminho)  # type: ignore[union-attr]


def _chave_existe(caminho: str) -> bool | None:
    """True/False = fact; None = could not tell (permission, winreg missing).

    The tri-state is mandatory: any boolean test that collapses an error into ``False``
    makes the API ASSERT "no pending reboot" on an access denied -- a silent false negative
    on the most critical datum.
    """
    if winreg is None:
        return None
    try:
        winreg.CloseKey(_abrir(caminho))
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return None


def _valor(caminho: str, nome: str) -> object:
    chave = _abrir(caminho)
    try:
        return winreg.QueryValueEx(chave, nome)[0]  # type: ignore[union-attr]
    finally:
        winreg.CloseKey(chave)  # type: ignore[union-attr]


def reboot_pendente() -> dict:
    """Pending reboot + reasons. ~0.2 ms -- fits the synchronous path, always fresh.

    Returns:
        ``{"pendente": True|False|None, "motivos": [...], "erro": str|None}``.
        ``True`` as soon as one reason is confirmed, even if another probe failed.
        ``None`` = unknown (never turns into ``False`` by mistake -- see ``_chave_existe``).
    """
    if winreg is None:
        return {"pendente": None, "motivos": [], "erro": "winreg indisponível (não-Windows)"}

    motivos: list[str] = []
    incerto = False

    for caminho, rotulo in _CHAVES_REBOOT:
        existe = _chave_existe(caminho)
        if existe is None:
            incerto = True
        elif existe:
            motivos.append(rotulo)

    try:
        pendentes = _valor(_SESSION_MANAGER, "PendingFileRenameOperations")
        if pendentes:
            motivos.append(f"PendingFileRenameOperations({len(pendentes)})")
    except FileNotFoundError:
        pass  # ausente = não há renomeação pendente (fato)
    except OSError:
        incerto = True

    # Um motivo confirmado basta: sabemos que HÁ reboot pendente, mesmo que outra sonda
    # tenha falhado. Só devolvemos "não sei" quando nada foi confirmado E algo falhou.
    if motivos:
        return {"pendente": True, "motivos": motivos, "erro": None}
    if incerto:
        return {"pendente": None, "motivos": [], "erro": "não foi possível ler todas as chaves"}
    return {"pendente": False, "motivos": [], "erro": None}


def patching_automatico() -> bool | None:
    """Can Windows Update act on this machine? ``False`` = ``wuauserv`` DISABLED; ``None`` = unknown.

    Worth reporting: ``Stopped/Manual`` is the default (trigger-start) and is NOT a problem,
    but ``Disabled`` means the machine never updates itself -- that was the .12, which went
    610 days without a patch. It is the context without which "0 pending" misleads.
    """
    if winreg is None:
        return None
    try:
        return int(_valor(_SVC_WUAUSERV, "Start")) != _SVC_START_DISABLED
    except (FileNotFoundError, OSError, TypeError, ValueError):
        return None


def _rodar_ps(script: str, timeout_s: float) -> tuple[dict | None, str | None]:
    """Run PowerShell and parse its stdout as a JSON object: ``(data, error)``; never raises.

    Exactly one of the two is ``None``; any output that is not a JSON object is an error.
    """
    try:
        resultado = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return None, f"a coleta passou de {timeout_s:.0f}s"
    except OSError as exc:
        return None, f"falha ao executar o powershell: {exc}"

    if resultado.returncode != 0:
        detalhe = (resultado.stderr or b"").decode("utf-8", errors="replace").strip()
        return None, f"powershell retornou {resultado.returncode}: {detalhe[:200]}"
    try:
        # UTF-8 aqui só é seguro porque o _PS_COLETA força `[Console]::OutputEncoding`
        # na 1ª linha — o default do PS é cp850. Ver o comentário lá.
        dados = json.loads((resultado.stdout or b"").decode("utf-8", errors="replace"))
    except (ValueError, UnicodeDecodeError) as exc:
        return None, f"saída não-JSON do powershell: {exc}"
    # `ConvertTo-Json` de uma lista devolve `[...]`, e de um escalar devolve `3`. Só um
    # objeto serve: sem isto, um `.get()` num não-dict explodiria dentro da thread.
    if not isinstance(dados, dict):
        return None, f"saída inesperada do powershell (esperado objeto): {type(dados).__name__}"
    return dados, None


def _agora_utc() -> datetime:
    """Naive UTC. (`utcnow()` is deprecated since 3.12.)"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso_para_dt(valor: object) -> datetime | None:
    """PowerShell ISO string -> naive UTC datetime (PS already sends `.ToUniversalTime()`).

    ``None`` if empty or unparseable.
    """
    if not valor:
        return None
    try:
        dt = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    except ValueError:
        return None
    # Com offset -> normaliza p/ UTC; sem offset -> o PS já mandou UTC.
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    return dt.replace(tzinfo=None)


def coletar_updates(settings: Settings | None = None) -> dict:
    """The EXPENSIVE part (3-30 s). Only the background thread calls this -- never a request.

    Never raises: a failed PowerShell run returns the same keys with ``pendentes=None`` and
    ``erro``/``pendentes_motivo`` set. The run is capped by ``wu_coleta_timeout_s``.
    """
    s = settings or get_settings()
    # `float()` (não `int()`): truncar fazia o PS usar 7 quando o .env pedia 7,9 — o limite
    # do PS divergiria do daqui em silêncio. E é o que torna o `replace` seguro: o valor
    # sempre vira um literal numérico (o `_env_float` já rejeita lixo e cai no default),
    # então não há como injetar PowerShell pelo .env. Ver test_limite_*.
    script = _PS_COLETA.replace("__MAX_DIAS__", repr(float(s.wu_varredura_max_d)))
    inicio = time.monotonic()
    dados, erro = _rodar_ps(script, s.wu_coleta_timeout_s)
    duracao_ms = round((time.monotonic() - inicio) * 1000)

    if dados is None:
        return {
            "coletado_em": datetime.now().isoformat(timespec="seconds"),
            "coleta_ms": duracao_ms,
            "erro": erro,
            "ultima_varredura": None,
            "varredura_dias": None,
            "pendentes": None,
            "pendentes_motivo": erro,
            "ultimo_patch": None,
            "ultimo_patch_kb": None,
            "dias_sem_patch": None,
        }

    agora = _agora_utc()
    varredura = _iso_para_dt(dados.get("ultima_varredura"))
    patch = _iso_para_dt(dados.get("ultimo_patch"))
    dias_varredura = (agora - varredura).total_seconds() / 86400 if varredura else None

    pendentes, motivo = _filtrar_contagem(
        dados.get("pendentes"), dados.get("pendentes_motivo"), dias_varredura, s
    )
    return {
        "coletado_em": datetime.now().isoformat(timespec="seconds"),
        "coleta_ms": duracao_ms,
        "erro": dados.get("erro"),
        "ultima_varredura": varredura.isoformat(timespec="seconds") if varredura else None,
        "varredura_dias": round(dias_varredura, 1) if dias_varredura is not None else None,
        # None = NÃO SEI. O motivo vem junto justamente para ninguém ler como zero.
        "pendentes": pendentes,
        "pendentes_motivo": motivo,
        "ultimo_patch": patch.date().isoformat() if patch else None,
        "ultimo_patch_kb": dados.get("ultimo_patch_kb"),
        "dias_sem_patch": (agora - patch).days if patch else None,
    }


def _filtrar_contagem(
    pendentes: object, motivo: str | None, dias_varredura: float | None, s: Settings
) -> tuple[int | None, str | None]:
    """Reapply invariant #1 HERE, even though PowerShell already applied it.

    Defence in depth on purpose: the rule "never publish 0 when it means unknown" is the
    reason this module exists, and it cannot have a single decision point on the far side of
    a subprocess. If someone edits `_PS_COLETA` and breaks the freshness check there, the
    count dies here all the same -- and this layer is the one with tests.

    Returns:
        ``(count|None, reason|None)``.
    """
    # A mensagem DAQUI vence a do PowerShell nestes dois ramos, de propósito: a do PS é
    # genérica e ASCII ("o agente nao varre ha tempo demais"), enquanto aqui sabemos o
    # NÚMERO de dias e escrevemos com acento. É o texto que o usuário lê no chat — "não
    # varre há 610 dias" explica; "há tempo demais" faz perguntar quanto.
    # Não perdemos motivo específico: quando a varredura está velha o PS nem tenta a busca,
    # então o motivo dele é sempre o genérico. O "busca falhou: ..." dele vive no ramo
    # `pendentes is None` abaixo, que segue repassando o motivo do PS intacto.
    if dias_varredura is None:
        return None, "o agente nunca varreu — a contagem seria mentira"
    if dias_varredura > s.wu_varredura_max_d:
        return None, f"o agente não varre há {dias_varredura:.0f} dias — a contagem seria mentira"
    if pendentes is None:
        return None, motivo
    try:
        return int(pendentes), motivo  # "3" (string) do JSON não pode virar verdade sem checagem
    except (TypeError, ValueError):
        return None, f"contagem inesperada do coletor: {pendentes!r}"


# ---------------------------------------------------------------- cache + thread


def _delay_valido(delay_s: float) -> float:
    """Sanitize the .env delay. `_env_float` accepts -1/nan/inf -- `time.sleep` does not.

    Garbage, NaN and infinity fall back to ``WU_DELAY_START_S_DEFAULT``; negatives clamp to 0.
    """
    try:
        valor = float(delay_s)
    except (TypeError, ValueError):
        return WU_DELAY_START_S_DEFAULT
    if valor != valor or valor in (float("inf"), float("-inf")):  # NaN / infinito
        return WU_DELAY_START_S_DEFAULT
    return max(0.0, valor)


_cache: dict | None = None
_lock = threading.Lock()
_coletando = False


def reset_cache() -> None:
    """Drop the cache (tests)."""
    global _cache, _coletando
    with _lock:
        _cache = None
        _coletando = False


def _guardar(valor: dict) -> None:
    global _cache, _coletando
    with _lock:
        _cache = valor
        _coletando = False


def estado_updates() -> dict:
    """What goes in the ``windows_update`` block of ``/status``. Only READS the cache -- never collects.

    Before the 1st collection it returns ``estado='coletando'`` (the thread is born at API
    start and sleeps ~5 min), or ``'indisponivel'`` if no collection was ever scheduled
    (``WU_ENABLED=false``); afterwards ``'ok'``, or ``'erro'`` when the cache carries ``erro``.
    The window falls at ~06:1x, right after the daily boot -- with nobody on the system. That
    is why the cache is in memory and needs no file.
    """
    with _lock:
        atual = dict(_cache) if _cache else None
        coletando = _coletando

    base = {"patching_automatico": patching_automatico()}
    if atual is None:
        base.update(
            estado="coletando" if coletando else "indisponivel",
            coletado_em=None,
            pendentes=None,
            pendentes_motivo=(
                "a primeira coleta ainda não terminou" if coletando else "nenhuma coleta rodou ainda"
            ),
            ultima_varredura=None,
            ultimo_patch=None,
            dias_sem_patch=None,
        )
        return base

    base.update(atual)
    base["estado"] = "erro" if atual.get("erro") else "ok"
    return base


def _worker(delay_s: float, settings: Settings) -> None:
    # O sleep fica DENTRO do try: com `WU_DELAY_START_S` negativo/nan/inf ele levanta
    # (ValueError/OverflowError), a thread morre antes do `_guardar` e `_coletando` fica
    # preso em True — o /status diria "coletando…" para SEMPRE. Mentira permanente e
    # silenciosa por causa de um .env torto.
    try:
        time.sleep(_delay_valido(delay_s))
        resultado = coletar_updates(settings)
        _guardar(resultado)
        logger.info(
            "[WU] coleta ok em %sms: pendentes=%s (%s) | último patch=%s (%s dias)",
            resultado.get("coleta_ms"),
            resultado.get("pendentes"),
            resultado.get("pendentes_motivo") or "varredura recente",
            resultado.get("ultimo_patch"),
            resultado.get("dias_sem_patch"),
        )
    except Exception as exc:  # nunca derruba a API por causa disto
        logger.warning("[WU] coleta falhou: %s", exc)
        _guardar(
            {
                "coletado_em": datetime.now().isoformat(timespec="seconds"),
                "erro": str(exc)[:300],
                "pendentes": None,
                "pendentes_motivo": f"a coleta falhou: {str(exc)[:120]}",
                "ultima_varredura": None,
                "varredura_dias": None,
                "ultimo_patch": None,
                "ultimo_patch_kb": None,
                "dias_sem_patch": None,
            }
        )


def iniciar_coletor(settings: Settings | None = None) -> threading.Thread | None:
    """Fire the collection ONCE, in the background, ``wu_delay_start_s`` later.

    Returns the thread, or ``None`` when ``WU_ENABLED=false``. Called from the ENTRYPOINT
    (``api.main()``), never from import -- otherwise the test suite would fire PowerShell. Once
    per process: since the server reboots every day, the boot is the schedule and a deploy
    restart mid-day simply recollects.
    """
    global _coletando
    s = settings or get_settings()
    if not s.wu_enabled:
        logger.info("[WU] coleta desabilitada (WU_ENABLED=false).")
        return None
    with _lock:
        _coletando = True
    thread = threading.Thread(
        target=_worker, args=(s.wu_delay_start_s, s), name="wu-coletor", daemon=True
    )
    thread.start()
    logger.info("[WU] coletor agendado para daqui a %.0fs (em background).", s.wu_delay_start_s)
    return thread
