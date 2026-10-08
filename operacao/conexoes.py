"""Does a host/port answer FROM the .11? (DNS, ping, TCP) — closed list of destinations.

"Is the HANA down or is it the network?", "can the .11 reach GitHub?" (the deploy that
failed on DNS, 01/10/2026). The agent picks a destination BY NAME from :func:`destinos`;
host and ports come from here or from the `.env` the services already use, never from the
caller. That keeps it from becoming a network scanner (rule 5 of the plan): no free host,
no free port, no port range.
"""
from __future__ import annotations

import os
import re
import socket
import subprocess
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from config import get_settings

#: Seconds per TCP connect; a LAN service answers in milliseconds, so 2 s is already "no".
TEMPO_PORTA_S = 2.0
#: Two echo requests, 1 s each — enough to tell "answers" from "silent" without a long wait.
PING_PACOTES = 2
PING_ESPERA_MS = 1000


class DestinoInvalido(ValueError):
    """Unknown destination name, or a port that destination does not list."""


@dataclass(frozen=True)
class Destino:
    nome: str
    descricao: str
    host: str
    portas: tuple[int, ...]

    def publico(self) -> dict[str, Any]:
        return {"nome": self.nome, "descricao": self.descricao, "host": self.host,
                "portas": list(self.portas)}


#: Fixed hosts. The .90 is the OrçaView/Mira box; the .12 the SAP RDP terminal server.
_FIXOS = (
    Destino("esta-maquina", "os serviços da própria .11 (API, MCP, painel WBC, Controle de Produção)",
            "127.0.0.1", (8077, 8078, 8079, 8080)),
    Destino("orcaview-90", "OrçaView e Mira no .90 (https 443 + http 8000)", "192.168.0.90", (443, 8000)),
    Destino("altamira-view", "Altamira View (equipe GLMiranda) no .90", "192.168.0.90", (8095,)),
    Destino("sap-rdp-12", "servidor RDP do SAP (.12)", "192.168.7.12", (3389,)),
    Destino("github", "GitHub (o deploy baixa o código de lá)", "github.com", (443,)),
    # PLANO_TEO_REDE_E_ROTINAS F3 (web): the network seen from here, to tell "the .90's path" from
    # "the network". Port 53 (TCP) only: these answer DNS, nothing else is asked of them.
    Destino("gateway", "pfSense (gateway, firewall e DNS encaminhador)", "192.168.0.10", (53,)),
    Destino("dns-casa", "DNS da casa (ALTSERVIDOR, o controlador de domínio)", "192.168.0.1", (53,)),
    Destino("internet", "internet (DNS público do Google, 8.8.8.8)", "8.8.8.8", (53,)),
)


def destinos() -> dict[str, Destino]:
    """Every destination the agent may test, by name. The `.env` ones only when configured."""
    s = get_settings()
    lista = list(_FIXOS)
    if s.sap_host:
        lista.append(Destino("sap-hana", "banco SAP HANA (leituras da API e do worker)",
                             s.sap_host, (int(s.sap_port),)))
    if s.op_sl_server:
        lista.append(Destino("service-layer", "Service Layer do SAP (o que grava no SAP)",
                             s.op_sl_server, (int(s.op_sl_port),)))
    if s.sql_host:
        lista.append(Destino("sql-server-wbc", "SQL Server do WBC (orçamentos)",
                             s.sql_host, (int(s.sql_port),)))
    supabase = urlsplit(s.supabase_url or "").hostname
    if supabase:
        lista.append(Destino("supabase", "Supabase (onde a .11 carrega OS, oportunidades, BI)",
                             supabase, (443,)))
    return {d.nome: d for d in lista}


def _resolver(host: str) -> tuple[str | None, float, str | None]:
    inicio = time.perf_counter()
    try:
        ip = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
    except OSError as exc:
        return None, _ms(inicio), str(exc)[:200]
    return ip, _ms(inicio), None


def _ms(inicio: float) -> float:
    return round((time.perf_counter() - inicio) * 1000, 1)


_TEMPO_DO_PING = re.compile(r"[=<](\d+)\s*ms", re.IGNORECASE)


def _pingar(ip: str) -> dict[str, Any]:
    """ICMP echo through the system ``ping`` with fixed arguments (no shell, IP from DNS).

    Windows answers exit code 0 even for "destination host unreachable" coming from a
    router, so a reply only counts with ``TTL=`` in it — the same in every Windows locale.
    """
    if os.name == "nt":
        comando = ["ping", "-n", str(PING_PACOTES), "-w", str(PING_ESPERA_MS), ip]
    else:
        comando = ["ping", "-c", str(PING_PACOTES), "-W", str(PING_ESPERA_MS // 1000), ip]
    try:
        proc = subprocess.run(comando, capture_output=True, timeout=PING_PACOTES * 3 + 2,
                              encoding="cp850" if os.name == "nt" else "utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"responde": None, "motivo": f"ping não rodou: {exc}"[:200]}
    saida = proc.stdout or ""
    respostas = [linha for linha in saida.splitlines() if "TTL=" in linha.upper()]
    tempos = [int(t) for linha in respostas for t in _TEMPO_DO_PING.findall(linha)[:1]]
    return {
        "responde": bool(respostas),
        "respostas": f"{len(respostas)} de {PING_PACOTES}",
        "ms_medio": round(sum(tempos) / len(tempos), 1) if tempos else None,
    }


def _abrir(ip: str, porta: int) -> dict[str, Any]:
    inicio = time.perf_counter()
    try:
        with socket.create_connection((ip, porta), timeout=TEMPO_PORTA_S):
            pass
    except OSError as exc:
        return {"porta": porta, "aberta": False, "ms": _ms(inicio), "erro": str(exc)[:200]}
    return {"porta": porta, "aberta": True, "ms": _ms(inicio)}


def _conclusao(d: Destino, ip: str | None, ping: dict, portas: list[dict]) -> str:
    if ip is None:
        return f"a .11 não resolve o nome {d.host} (DNS) — nada adiante foi testado"
    abertas = [str(p["porta"]) for p in portas if p["aberta"]]
    fechadas = [str(p["porta"]) for p in portas if not p["aberta"]]
    if abertas and not fechadas:
        return f"a .11 alcança {d.host} na(s) porta(s) {', '.join(abertas)}"
    if abertas:
        return (f"{d.host} aceita a(s) porta(s) {', '.join(abertas)}, mas não a(s) "
                f"{', '.join(fechadas)} (serviço parado ou firewall)")
    if ping.get("responde"):
        return (f"{d.host} responde ao ping, mas a(s) porta(s) {', '.join(fechadas)} não aceita(m) "
                "conexão — serviço parado ou firewall")
    return (f"{d.host} não responde nem ao ping nem na(s) porta(s) {', '.join(fechadas)} — "
            "desligada, fora da rede ou bloqueada (alguns hosts recusam ping de propósito)")


def testar(nome: str, porta: int | None = None) -> dict[str, Any]:
    """DNS → ping → TCP on the destination's ports (or the one asked, if it lists it)."""
    todos = destinos()
    d = todos.get((nome or "").strip().lower())
    if d is None:
        raise DestinoInvalido(f"Destino desconhecido: {nome!r}. Use um destes: {', '.join(todos)}.")
    if porta is not None and porta not in d.portas:
        raise DestinoInvalido(
            f"A porta {porta} não está na lista de {d.nome} ({', '.join(map(str, d.portas))}).")
    ip, dns_ms, dns_erro = _resolver(d.host)
    resultado: dict[str, Any] = {
        "destino": d.publico(),
        "dns": {"resolve": ip is not None, "ip": ip, "ms": dns_ms, "erro": dns_erro},
        "ping": {}, "portas": [],
    }
    if ip is not None:
        resultado["ping"] = _pingar(ip)
        resultado["portas"] = [_abrir(ip, p) for p in ((porta,) if porta is not None else d.portas)]
    resultado["conclusao"] = _conclusao(d, ip, resultado["ping"], resultado["portas"])
    return resultado
