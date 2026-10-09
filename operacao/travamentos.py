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

One fixed query: Service Control Manager 7011/7046/7022 (hung), 7031/7034 (crashed), 7000/7009 (did
not start) of the last :data:`JANELA_MIN` minutes. ~0.3 s; cached :data:`CACHE_S`; read only. The
grouping and the cascade follow SAP_RDP's ``login_rdp`` (V1.21/V1.22), which reads 24 h for the
logon diagnosis; this one is the light, frequent read.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from typing import Any

import windows_update

TIMEOUT_S = 15
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

# ASCII only and no double quotes (PS 5.1 through -Command). The first line forces UTF-8 output.
_PS = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'Stop'
function Ler($ids, $max) {
  try {
    @(Get-WinEvent -FilterHashtable @{ LogName = 'System'; ProviderName = 'Service Control Manager'
                                       Id = $ids; StartTime = (Get-Date).AddMinutes(-60) } -MaxEvents $max |
      ForEach-Object {
      $p = @($_.Properties | Select-Object -First 2 | ForEach-Object {
        $v = [string]$_.Value
        if ($v.Length -gt 120) { $v = $v.Substring(0, 120) }
        $v
      })
      [pscustomobject]@{ quando = $_.TimeCreated.ToString('s'); id = $_.Id; p = $p }
    })
  } catch {
    if ($_.Exception.Message -match 'No events were found|Nenhum evento') { @() }
    else { @{ erro = $_.Exception.Message } }
  }
}
$boot = $null
try { $boot = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToString('s') } catch { $boot = $null }
[ordered]@{ boot = $boot; travados = Ler @(7011, 7046, 7022) 300; outros = Ler @(7031, 7034, 7000, 7009) 100 } |
  ConvertTo-Json -Depth 4 -Compress
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
    with _trava:
        if _cache["dados"] is not None and time.monotonic() - _cache["em"] < CACHE_S:
            return {**_cache["dados"], "cache": True}
        dados, erro = windows_update._rodar_ps(_PS, TIMEOUT_S)
        if erro:
            resposta: dict[str, Any] = {"disponivel": False, "motivo": erro}
        else:
            travados, outros = _lista(dados.get("travados")), _lista(dados.get("outros"))
            erro_log = next((x for x in (travados, outros) if isinstance(x, dict)), None)
            if erro_log is not None:
                resposta = {"disponivel": False, "motivo": str(erro_log.get("erro"))[:LIMITE_TEXTO]}
            else:
                grupos = agrupar([*travados, *outros])
                # ``ligou``: hangs right after a boot are the boot's own (slow start), not a warning.
                resposta = {"disponivel": True, "cache": False, "janela_min": JANELA_MIN,
                            "agora": datetime.now().isoformat(timespec="seconds"), "ligou": dados.get("boot"),
                            "servicos": grupos, "cascata": cascata(grupos)}
        _cache.update(em=time.monotonic(), dados=resposta)
        return resposta
