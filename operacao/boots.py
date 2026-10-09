"""Why did this machine restart? (F5 of web_orcaview_V118/docs/PLANO_TEO_REDE_E_ROTINAS.md)

Sibling file, IDENTICAL in MCPs/SAP_RDP (.12) and MCPs/ServidorIntegracaoSAP (.11): change both in
the same commit.

One fixed query, never one of the caller's choosing: ``LastBootUpTime`` plus the System log events
of the last 7 days that tell how each life of the machine ended --

- Kernel-General 12 (the OS started) and 13 (the OS stopped cleanly);
- User32 1074 (somebody or something ASKED for the shutdown: process, reason, type, comment,
  account) and 1076 (the reason typed after an unexpected one);
- EventLog 6005/6006 (event log started/stopped) and 6008 ("the previous shutdown was
  unexpected", with the time it went down);
- Kernel-Power 41 (rebooted without a clean shutdown) and the BugCheck 1001 (blue screen).

Grouped per boot, newest first: when it came up, how the previous life ended (``como``), when it
went down and for how long, and who asked (the 1074). On a VM, a 41/6008 with no 1074 means the
Windows was never asked: the VM was turned off from the host, hung, or lost power -- on 08/10 the
.12's two 41s were people turning it off in Hyper-V after it hung.

``Get-WinEvent`` takes ~1 s for 7 days filtered by id; the answer is cached for 60 s and the call
has a ceiling. Read only.
"""
from __future__ import annotations

import ntpath
import threading
import time
from datetime import datetime, timedelta
from typing import Any

import windows_update

TIMEOUT_S = 25
CACHE_S = 60.0
JANELA_DIAS = 7
LIMITE_BOOTS = 15
LIMITE_TEXTO = 200
#: 6008, 41 and 1001 about the previous life are written right after the next boot.
DEPOIS_DO_BOOT = timedelta(minutes=15)
#: ... and some are stamped seconds BEFORE the boot's Kernel-General 12 (or the 6005 standing for it).
ANTES_DO_BOOT = timedelta(minutes=1)
#: A 6005 this close after a Kernel-General 12 is the same boot.
MESMO_BOOT = timedelta(minutes=5)
#: A 1074 counts only this close to the end of the life: one asked (and cancelled) hours before a
#: forced power-off is not why it went down (review of F5a).
PEDIDO_VALE = timedelta(hours=2)
#: A failed read is kept this long: a hung event log must not cost 25 s to every caller.
CACHE_FALHA_S = 30.0

KERNEL_GENERAL = "Microsoft-Windows-Kernel-General"
#: (provider, id) of each event read; another provider with the same id is ignored (Wininit also
#: writes a 12, Windows Error Reporting also writes a 1001).
EVENTOS: dict[tuple[str, int], str] = {
    (KERNEL_GENERAL, 12): "ligou",
    (KERNEL_GENERAL, 13): "parou",
    ("User32", 1074): "pedido",
    ("User32", 1076): "motivo_inesperado",
    ("EventLog", 6005): "log_ligou",
    ("EventLog", 6006): "log_parou",
    ("EventLog", 6008): "inesperado",
    ("Microsoft-Windows-Kernel-Power", 41): "forcado",
    ("Microsoft-Windows-WER-SystemErrorReporting", 1001): "tela_azul",
}

#: How the previous life ended, worst first.
COMO = ("tela_azul", "pedido_travou", "forcado", "pedido", "normal", "sem_registro")

#: Who asked for a shutdown (1074), as a category: the account itself is personal data and stays
#: in ``pedido.conta`` for technical screens only (decision 20 of the plan).
CATEGORIAS = {"atualizacao": "atualização do Windows", "hyperv": "Hyper-V (host)", "tarefa": "tarefa agendada",
              "sistema": "o próprio Windows", "usuario": "conta de usuário"}
#: The same, as the agent of a sentence ("reiniciada por ...").
_POR = {"atualizacao": "por uma atualização do Windows", "hyperv": "pelo Hyper-V (host)",
        "tarefa": "por uma tarefa agendada", "sistema": "pelo próprio Windows", "usuario": "por uma conta de usuário"}
_PROCESSOS_DE_ATUALIZACAO = ("trustedinstaller", "tiworker", "mousocoreworker", "musnotification", "usoclient",
                             "wuauclt", "updateorchestrator", "sihclient")
_MOTIVOS_DE_ATUALIZACAO = ("atualiza", "update", "hotfix", "service pack")
_CONTAS_DO_SISTEMA = ("SYSTEM", "SISTEMA", "LOCAL SERVICE", "NETWORK SERVICE", "SERVIÇO LOCAL", "SERVIÇO DE REDE",
                      "")

# ASCII only and no double quotes (PS 5.1 through -Command). The first line forces UTF-8 output.
# 6008 carries the time and date it went down as two strings in the system's culture (with
# left-to-right marks): parsed here, in that same culture, never in Python.
_PS = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'Stop'
$lrm = [string][char]0x200E
$semana = (Get-Date).AddDays(-7)
function Ler {
  try {
    @(Get-WinEvent -FilterHashtable @{ LogName = 'System'; Id = 12, 13, 41, 1001, 1074, 1076, 6005, 6006, 6008
                                       ProviderName = 'Microsoft-Windows-Kernel-General', 'User32', 'EventLog',
                                                      'Microsoft-Windows-Kernel-Power',
                                                      'Microsoft-Windows-WER-SystemErrorReporting'
                                       StartTime = $semana } -MaxEvents 400 | ForEach-Object {
      $p = @($_.Properties | Select-Object -First 7 | ForEach-Object {
        $v = [string]$_.Value
        if ($v.Length -gt 200) { $v = $v.Substring(0, 200) }
        $v
      })
      $caiu = $null
      if ($_.Id -eq 6008 -and $p.Count -ge 2) {
        try { $caiu = ([datetime]::Parse(($p[1] -replace $lrm, '') + ' ' + ($p[0] -replace $lrm, ''))).ToString('s') }
        catch { $caiu = $null }
      }
      [pscustomobject]@{ quando = $_.TimeCreated.ToString('s'); id = $_.Id; fonte = $_.ProviderName
                         p = $p; caiu = $caiu }
    })
  } catch {
    if ($_.Exception.Message -match 'No events were found|Nenhum evento') { @() }
    else { @{ erro = $_.Exception.Message } }
  }
}
$boot = $null
try { $boot = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToString('s') } catch { $boot = $null }
[ordered]@{ boot = $boot; eventos = Ler } | ConvertTo-Json -Depth 4 -Compress
"""

_trava = threading.Lock()
_cache: dict[str, Any] = {"em": 0.0, "dados": None}


def _lista(valor: Any) -> list[dict] | dict:
    """``ConvertTo-Json`` turns a 1-item array into an object, an empty one into ``{}`` and an
    error into ``{erro}``."""
    if isinstance(valor, dict):
        if "erro" in valor:
            return valor
        valor = [valor]
    return [v for v in (valor or []) if isinstance(v, dict) and v.get("quando")]


def _dt(texto: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(texto)) if texto else None
    except ValueError:
        return None


def _props(e: dict) -> list[str]:
    p = e.get("p")
    if isinstance(p, str):  # ConvertTo-Json flattens a 1-item array
        return [p]
    return [str(v) if v is not None else "" for v in (p or [])]


def categoria(processo: str, motivo: str, comentario: str, conta: str) -> str:
    """The 1074's requester as one of :data:`CATEGORIAS`."""
    proc, mot, com = processo.lower(), motivo.lower(), comentario.lower()
    if any(k in proc for k in _PROCESSOS_DE_ATUALIZACAO) or any(k in mot for k in _MOTIVOS_DE_ATUALIZACAO):
        return "atualizacao"
    if "vmic" in proc or "hyper-v" in com:
        return "hyperv"
    if conta.rsplit("\\", 1)[-1].strip().upper() in _CONTAS_DO_SISTEMA:
        return "tarefa" if ntpath.basename(proc) == "shutdown.exe" else "sistema"
    return "usuario"


def pedido(e: dict) -> dict[str, Any]:
    """A User32 1074 -> who asked, why and what. Properties: process (computer), computer, reason
    title, reason code, type, comment, account."""
    p = _props(e) + [""] * 7
    caminho = p[0].split(" (", 1)[0].strip()
    tipo_txt = p[4].lower()
    tipo = ("reiniciar" if "reinic" in tipo_txt or "restart" in tipo_txt
            else "desligar" if tipo_txt else None)
    conta = p[6].strip()
    return {"quando": e.get("quando"), "processo": ntpath.basename(caminho) or None, "tipo": tipo,
            "motivo": p[2].strip() or None, "comentario": p[5].strip()[:LIMITE_TEXTO] or None,
            "conta": conta or None, "categoria": categoria(caminho, p[2], p[5], conta)}


def frase(como: str, ped: dict[str, Any] | None = None, codigo: str | None = None) -> str:
    """Public sentence for how a life ended: no account, no path, no IP."""
    por = _POR.get((ped or {}).get("categoria") or "", "por alguém")
    if como == "tela_azul":
        return f"o Windows travou com tela azul ({codigo})" if codigo else "o Windows travou com tela azul"
    if como == "pedido_travou":
        return f"o desligamento foi pedido {por}, mas não terminou: a máquina foi desligada à força"
    if como == "forcado":
        return ("desligada sem aviso ao Windows: desligamento à força (no Hyper-V, numa VM), travamento "
                "ou falta de energia")
    if como == "pedido":
        verbo = "reiniciada" if (ped or {}).get("tipo") == "reiniciar" else "desligada"
        return f"{verbo} {por}"
    if como == "normal":
        return "desligamento normal, sem pedido registrado"
    return f"sem registro do desligamento (antes da janela de {JANELA_DIAS} dias ou log apagado)"


def _codigo_tela_azul(e: dict) -> str | None:
    p = _props(e)
    texto = p[0].strip() if p else ""
    return texto.split()[0][:24] if texto else None


def por_boot(eventos: list[dict], ultimo_boot: str | None) -> list[dict[str, Any]]:
    """Raw events -> one entry per boot, newest first (pure; the tests feed it real readings).

    A boot is a Kernel-General 12 (or a 6005 with no 12 next to it; ``ultimo_boot`` when the log
    has neither). The previous life ended between the boot before and this one (1074, 13, 6006)
    or is told right after this one (6008, 41, 1001 within :data:`DEPOIS_DO_BOOT`)."""
    tipados: list[tuple[datetime, str, dict]] = []
    for e in eventos:
        papel = EVENTOS.get((str(e.get("fonte") or ""), int(e.get("id") or 0)))
        quando = _dt(e.get("quando"))
        if papel and quando:
            tipados.append((quando, papel, e))
    tipados.sort(key=lambda t: t[0])
    inicios = [q for q, papel, _e in tipados if papel == "ligou"]
    for q, papel, _e in tipados:
        if papel == "log_ligou" and not any(timedelta(0) <= q - i <= MESMO_BOOT for i in inicios):
            inicios.append(q)
    ultimo = _dt(ultimo_boot)
    if ultimo and not any(abs(ultimo - i) <= MESMO_BOOT for i in inicios):
        inicios.append(ultimo)
    inicios.sort()

    boots: list[dict[str, Any]] = []
    for n, ligou in enumerate(inicios):
        antes = inicios[n - 1] if n else None
        # Up to the next boot's own margin: a 41/6008 belongs to one boot only (a restart loop).
        depois = (min(ligou + DEPOIS_DO_BOOT, inicios[n + 1] - ANTES_DO_BOOT) if n + 1 < len(inicios)
                  else ligou + DEPOIS_DO_BOOT)
        vida = [(q, papel, e) for q, papel, e in tipados if (antes is None or q >= antes) and q < ligou]
        logo = [(q, papel, e) for q, papel, e in tipados if ligou - ANTES_DO_BOOT <= q < depois]
        paradas = [q for q, papel, _e in vida if papel in ("parou", "log_parou")]
        inesperados = [e for _q, papel, e in logo if papel == "inesperado"]
        forcados = [e for _q, papel, e in logo if papel == "forcado"]
        azuis = [e for _q, papel, e in logo if papel == "tela_azul"]
        caiu = next((c for c in (_dt(e.get("caiu")) for e in inesperados)
                     if c and (antes is None or c >= antes) and c <= ligou), None)
        fim = caiu or (paradas[-1] if paradas else None) or ligou
        pedidos = [e for q, papel, e in vida if papel == "pedido" and fim - PEDIDO_VALE <= q <= fim]
        ped = pedido(pedidos[-1]) if pedidos else None
        codigo = _codigo_tela_azul(azuis[0]) if azuis else None
        if azuis:
            como = "tela_azul"
        elif (inesperados or forcados) and ped:
            como = "pedido_travou"
        elif inesperados or forcados:
            como = "forcado"
        elif ped:
            como = "pedido"
        elif paradas:
            como = "normal"
        else:
            como = "sem_registro"
        desligou = caiu or (paradas[-1] if paradas else None) or _dt((ped or {}).get("quando"))
        boots.append({"ligou": ligou.isoformat(timespec="seconds"), "como": como, "frase": frase(como, ped, codigo),
                      "desligou_em": desligou.isoformat(timespec="seconds") if desligou else None,
                      "fora_min": round((ligou - desligou).total_seconds() / 60) if desligou else None,
                      "pedido": ped, "tela_azul": codigo})
    boots.reverse()
    return boots[:LIMITE_BOOTS]


def boots() -> dict[str, Any]:
    with _trava:
        if _cache["dados"] is not None:
            vale = CACHE_S if _cache["dados"].get("disponivel") else CACHE_FALHA_S
            if time.monotonic() - _cache["em"] < vale:
                return {**_cache["dados"], "cache": True}
        dados, erro = windows_update._rodar_ps(_PS, TIMEOUT_S)
        if erro:
            falha = {"disponivel": False, "motivo": erro}
            _cache.update(em=time.monotonic(), dados=falha)
            return falha
        eventos = _lista(dados.get("eventos"))
        resposta: dict[str, Any] = {"disponivel": True, "cache": False, "ultimo_boot": dados.get("boot"),
                                    "janela_dias": JANELA_DIAS}
        if isinstance(eventos, dict):
            # The log could not be read; the boot time still answers "since when is it up".
            resposta.update(boots=[], motivo_eventos=str(eventos.get("erro"))[:LIMITE_TEXTO])
        else:
            resposta.update(boots=por_boot(eventos, dados.get("boot")), eventos_lidos=len(eventos))
        _cache.update(em=time.monotonic(), dados=resposta)
        return resposta
