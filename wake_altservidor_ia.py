#!/usr/bin/env python3
r"""Wakes ALTSERVIDOR-IA (the .90) over the network, with Wake-on-LAN.

Run it from ANY machine on the network: ``python wake_altservidor_ia.py``.
Standard library only (Python 3.8+) -- no ``pip install``.

⚠️ This file lives in TWO repositories, byte for byte the same:

* ``web_orcaview_V118/tools/wake_altservidor_ia.py`` -- manual use, from any machine.
* ``ServidorIntegracaoSAP/wake_altservidor_ia.py``  -- the one that runs by itself on the .11 boot
  (task ``OrcaView-WOL-AltservidorIA``, installed by ``install_wol_task.ps1``).

Change one, copy it to the other. Separate machines and deploys; there is no shared package.

What Wake-on-LAN is
-------------------
The NIC stays powered while the machine is off (state S5). In that state it sees
neither IP nor TCP -- it only watches the raw traffic on the wire for one fixed
pattern: the **magic packet**. That is 102 bytes -- six ``0xFF`` followed by the
NIC's own MAC repeated 16 times. Once it matches, the NIC drives the motherboard
power pin and the machine turns on. That is the whole protocol.

Practical consequences, which explain every choice in this script:

* **There is no reply.** The magic packet goes over UDP and the powered-off
  machine answers nothing. "Did it work?" is only answered by pinging afterwards --
  which is what the wait mode does.
* **There is no delivery guarantee.** UDP is what it is; so the packet is repeated
  within a round, and the round itself repeats (``--reenviar``). It is 102 bytes.
* **The address is the MAC, not the IP.** A powered-off machine has no IP (DHCP is
  not even up), so the packet goes to a **broadcast** and the NIC recognises its
  own MAC inside it.
* **The port barely matters.** The NIC filters on content, not on port;
  9 and 7 are the conventional ones. We send to both.
* **It does not cross a router.** Broadcast dies at the first routing hop: this has
  to run on the SAME physical network as the .90 (or over a bridged/layer-2 VPN).
  The .11 (192.168.7.11) is inside the .90's /21 -- same network, it works.

Each round = 2 broadcasts × 2 ports × 3 repeats = **12 packets**, ~1.2 KB.

The target machine
------------------
======================  ===========================================
Hostname                ALTSERVIDOR-IA
Target NIC              Realtek PCIe 2.5GbE (adapter ``Ethernet``)
MAC                     BC:FC:E7:09:D9:7D
IP                      192.168.0.90 /21
Gateway                 192.168.0.10
Broadcast               192.168.7.255 and 255.255.255.255
======================  ===========================================

The broadcast is **.7.255**, not .0.255: the mask is /21 (255.255.248.0), so the
range runs from 192.168.0.0 to 192.168.7.255 and its last address is the broadcast.
Getting this wrong is the most common cause of "I sent it and it did not wake" here.

What happens after it wakes
---------------------------
Nothing needs to be done from here: the .90 has autologon (~16 s after boot) and the
``OrcaView Stack AtLogon`` task brings OrçaView up 60 s later with
``npm run start:all-detailed`` -- that is ``tools/ops/subir_stack_boot.ps1`` in
``web_orcaview_V118``, with a double-instance guard and a backup of the WhatsApp
sessions. This script wakes the machine; the stack is the machine's business.

If it does not wake: the checklist
----------------------------------
1. **Did the packet go out?** ``--dry-run`` prints the 102 bytes without sending. If
   the hexdump is right and nothing happens, the problem is not here.
2. **Same network?** Broadcast does not cross a router (see above).
3. **Windows on the .90** -- the prerequisites were checked and were ALL fine in
   Sep/2026 (Wake on Magic Packet on, "magic packet only", fast startup out of the
   way). Do not lose time here again before item 4.
4. **BIOS -- this is where it usually is, and it was never confirmed:**

   * ``Power On By PCI-E`` = **Enabled**
   * ``ErP Ready`` = **Disabled**  <- the treacherous one

   With ``ErP Ready`` on, the motherboard cuts the NIC's power in S5 to meet the
   standby consumption limit. The NIC is dead, yet **Windows keeps reporting
   "Wake on Magic Packet: enabled" exactly the same** -- the OS-side diagnosis is
   green and the machine still does not wake. If you got this far, it is the BIOS,
   not software.
5. **Power outage?** Some boards ARM WOL when Windows shuts down and LOSE the
   arming if mains power drops. In that case the fix is not this script, it is
   ``Restore on AC Power Loss = Power On`` in the same BIOS.

What this script does NOT prove
-------------------------------
Sending is testable and was tested; **actually waking up is not** -- that is only
verified on the day the machine is really off and the packet comes from outside.
Running it with the machine on confirms the network path and the ping, nothing more.

Usage
-----
    python wake_altservidor_ia.py                    # send and wait for the ping
    python wake_altservidor_ia.py --dry-run          # only show the packet
    python wake_altservidor_ia.py --no-wait          # send and exit
    python wake_altservidor_ia.py --wait 600 --reenviar 60 --log D:\wol.log
    python wake_altservidor_ia.py --mac AA:BB:CC:DD:EE:FF --ip 192.168.0.42 \
        --broadcast 192.168.7.255                    # another machine

Exit codes: 0 = woke up (or was already on, or packet sent with
--no-wait/--dry-run), 1 = no reply within the window, 2 = usage error.
"""
from __future__ import annotations

import argparse
import os
import platform
import socket
import subprocess
import sys
import time
from datetime import datetime

# --- The target machine (see the header) -----------------------------------
HOSTNAME = "ALTSERVIDOR-IA"
MAC = "BC:FC:E7:09:D9:7D"
IP = "192.168.0.90"
BROADCASTS = ["192.168.7.255", "255.255.255.255"]

# --- How the packet is fired -----------------------------------------------
PORTS = [9, 7]          # the two conventional ones; the NIC does not filter by port
REPEATS = 3             # UDP does not guarantee delivery and the packet is 102 bytes
WAIT_DEFAULT = 180      # total window waiting for the ping, in seconds
REENVIAR_DEFAULT = 0    # 0 = a single round (the manual-use default)
POLL = 5                # interval between pings inside the window

_LOG_PATH = None        # set by --log; see log()


def log(msg: str = "") -> None:
    """Print and, when ``--log`` was given, append to that file with a timestamp.

    A scheduled task has no console: without a file, whatever this script found out
    dies with the process. A write failure never aborts the run -- the job is to
    wake the machine, not to log.

    The 1 MB cut is the lesson of 2026-09-11 on the .11: a log nobody trims grows
    forever. It is a few lines per boot, but "a few lines per boot" is exactly what
    nobody revisits for years.
    """
    print(msg)
    if not _LOG_PATH:
        return
    try:
        modo = "a"
        if os.path.exists(_LOG_PATH) and os.path.getsize(_LOG_PATH) > 1_000_000:
            modo = "w"
        with open(_LOG_PATH, modo, encoding="utf-8") as fh:
            carimbo = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            fh.write(f"{carimbo} {msg}\n")
    except OSError:
        pass


def normalize_mac(raw: str) -> str:
    """Accept ``:``, ``-``, ``.`` or no separator and return 12 uppercase hex digits."""
    cleaned = raw.strip().replace(":", "").replace("-", "").replace(".", "").replace(" ", "")
    if len(cleaned) != 12:
        raise ValueError(f"MAC deve ter 12 digitos hex, veio {len(cleaned)}: {raw!r}")
    try:
        bytes.fromhex(cleaned)
    except ValueError:
        raise ValueError(f"MAC tem caractere que nao e hex: {raw!r}") from None
    return cleaned.upper()


def build_magic_packet(mac: str) -> bytes:
    """The 102 bytes: six sync ``0xFF`` + the MAC repeated 16 times."""
    return b"\xff" * 6 + bytes.fromhex(normalize_mac(mac)) * 16


def hexdump(data: bytes, width: int = 16) -> str:
    linhas = []
    for off in range(0, len(data), width):
        chunk = data[off:off + width]
        linhas.append(f"  {off:04x}  " + " ".join(f"{b:02x}" for b in chunk))
    return "\n".join(linhas)


def send_packet(packet: bytes, broadcast: str, port: int) -> None:
    """One UDP datagram with ``SO_BROADCAST`` -- without it the kernel refuses to send."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(packet, (broadcast, port))


def ping(host: str) -> bool:
    """One ping, 1 s timeout. ``True`` only when the reply came FROM the host.

    Windows ``ping`` exits 0 even when what came back was "Destination host
    unreachable" (the error comes from the router, and to ``ping`` that is a
    reply). So we check for the TTL in the output, not the exit code.
    """
    win = platform.system().lower().startswith("win")
    cmd = ["ping", "-n", "1", "-w", "1000", host] if win else ["ping", "-c", "1", "-W", "1", host]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=5)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False
    saida = proc.stdout.decode("utf-8", errors="replace").lower()
    return proc.returncode == 0 and ("ttl=" in saida or "ttl =" in saida)


def burst(packet: bytes, broadcasts) -> int:
    """One round: the packet to each broadcast × port × REPEATS."""
    enviados = 0
    for broadcast in broadcasts:
        for port in PORTS:
            for _ in range(REPEATS):
                try:
                    send_packet(packet, broadcast, port)
                    enviados += 1
                except OSError as exc:
                    log(f"  FALHOU {broadcast}:{port} -- {exc}")
                    break
    return enviados


def acordar(mac: str, ip: str, broadcasts, wait: int, reenviar: int) -> bool:
    """Send magic-packet rounds until the host replies or the window ends.

    Resending exists because of the .11 boot: when it comes up after a power
    outage, the switch port may not have converged yet and the first round is
    lost. A round costs 1.2 KB.
    """
    packet = build_magic_packet(mac)
    limite = time.monotonic() + wait
    proxima_rodada = 0.0
    rodadas = 0

    while True:
        agora = time.monotonic()
        if agora >= proxima_rodada:
            rodadas += 1
            enviados = burst(packet, broadcasts)
            log(f"  rodada {rodadas}: {enviados} pacotes enviados.")
            # reenviar=0 -> a single round: push the next one past the deadline.
            proxima_rodada = agora + (reenviar if reenviar > 0 else wait + 1)

        if ping(ip):
            gasto = int(wait - (limite - time.monotonic()))
            log(f"  {ip} RESPONDEU (depois de ~{gasto}s, {rodadas} rodada(s)).")
            return True

        restante = limite - time.monotonic()
        if restante <= 0:
            return False
        log(f"  ... {int(restante)}s restantes")
        time.sleep(min(POLL, max(restante, 0.1)))


def main(argv=None) -> int:
    global _LOG_PATH

    parser = argparse.ArgumentParser(
        description=f"Wake-on-LAN para o {HOSTNAME} ({IP}).",
        epilog="Se nao acordar, o problema quase sempre e ErP Ready na BIOS -- leia o cabecalho do arquivo.",
    )
    parser.add_argument("--mac", default=MAC, help=f"MAC alvo (padrao: {MAC})")
    parser.add_argument("--ip", default=IP, help=f"IP para pingar depois (padrao: {IP})")
    parser.add_argument("--broadcast", action="append", metavar="ADDR",
                        help="Broadcast de destino; pode repetir. Padrao: " + ", ".join(BROADCASTS))
    parser.add_argument("--dry-run", action="store_true", help="Mostra o pacote em hexa e NAO envia")
    parser.add_argument("--no-wait", action="store_true", help="Envia uma rodada e sai, sem esperar o ping")
    parser.add_argument("--wait", type=int, default=WAIT_DEFAULT, metavar="SEG",
                        help=f"Janela total de espera pelo ping (padrao: {WAIT_DEFAULT})")
    parser.add_argument("--reenviar", type=int, default=REENVIAR_DEFAULT, metavar="SEG",
                        help=f"Reenvia a rodada a cada SEG; 0 = rodada unica (padrao: {REENVIAR_DEFAULT})")
    parser.add_argument("--log", metavar="ARQ", help="Anexa a saida neste arquivo (tarefa agendada nao tem console)")
    args = parser.parse_args(argv)

    _LOG_PATH = args.log

    try:
        mac = normalize_mac(args.mac)
    except ValueError as exc:
        parser.error(str(exc))

    if args.log:
        log("--- inicio ---")

    if args.dry_run:
        log(f"Magic packet para {mac} -- 102 bytes")
        print(hexdump(build_magic_packet(args.mac)))
        print("\n--dry-run: nada foi enviado.")
        return 0

    if ping(args.ip):
        log(f"{args.ip} JA responde ao ping -- a maquina esta ligada. Nada a fazer.")
        return 0

    broadcasts = args.broadcast or BROADCASTS
    log(f"Magic packet para {mac} -> {', '.join(broadcasts)} (portas {PORTS}, x{REPEATS})")

    if args.no_wait:
        enviados = burst(build_magic_packet(args.mac), broadcasts)
        log(f"  {enviados} pacotes enviados. Saindo sem esperar (--no-wait).")
        return 0

    if acordar(args.mac, args.ip, broadcasts, args.wait, args.reenviar):
        log("A maquina acordou. O OrcaView sobe sozinho nela (autologon + tarefa AtLogon).")
        return 0

    log("Nao respondeu no prazo. Antes de repetir o envio, confira a BIOS:")
    log("  Power On By PCI-E = Enabled   |   ErP Ready = Disabled")
    log("(o Windows reporta WOL habilitado mesmo com ErP ligado -- ver o cabecalho)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
