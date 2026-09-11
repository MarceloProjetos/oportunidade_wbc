#!/usr/bin/env python3
r"""Liga o ALTSERVIDOR-IA (o .90) pela rede, com Wake-on-LAN.

Rode de QUALQUER máquina da rede: ``python wake_altservidor_ia.py``.
Só biblioteca padrão (Python 3.8+) — nada de ``pip install``.

⚠️ Este arquivo existe em DOIS repositórios, byte a byte igual:

* ``web_orcaview_V118/tools/wake_altservidor_ia.py`` — uso manual, de qualquer máquina.
* ``ServidorIntegracaoSAP/wake_altservidor_ia.py``  — o que roda sozinho no boot da .11
  (tarefa ``OrcaView-WOL-AltservidorIA``, instalada pelo ``install_wol_task.ps1``).

Mexeu num, copie para o outro. São máquinas e deploys separados; não há pacote comum.

O que é Wake-on-LAN
-------------------
A placa de rede continua alimentada com a máquina desligada (estado S5). Ela não
enxerga IP nem TCP nesse estado — só olha o tráfego cru que passa pelo cabo
procurando um padrão fixo: o **magic packet**. São 102 bytes — seis ``0xFF``
seguidos do MAC da própria placa repetido 16 vezes. Reconhecido o padrão, a
placa aciona o pino de power da placa-mãe e a máquina liga. É isso, o protocolo
inteiro.

Consequências práticas, que explicam cada escolha deste script:

* **Não tem resposta.** O magic packet vai em UDP e a máquina desligada não
  responde nada. "Deu certo?" só se responde pingando depois — é o que o modo
  de espera faz.
* **Não tem garantia de entrega.** UDP é o que é; por isso o pacote vai repetido
  dentro da rodada, e a rodada se repete (``--reenviar``). São 102 bytes.
* **Endereço é o MAC, não o IP.** A máquina desligada não tem IP (o DHCP nem
  está de pé), então o pacote vai num **broadcast** e a placa se reconhece pelo
  MAC lá dentro.
* **Porta não importa muito.** A placa filtra pelo conteúdo, não pela porta;
  9 e 7 são as convencionais. Mandamos nas duas.
* **Não atravessa roteador.** Broadcast morre no primeiro roteamento: isto tem
  de rodar na MESMA rede física do .90 (ou por uma VPN em bridge/camada 2).
  A .11 (192.168.7.11) está dentro do /21 do .90 — mesma rede, funciona.

Cada rodada = 2 broadcasts × 2 portas × 3 repetições = **12 pacotes**, ~1,2 KB.

A máquina alvo
--------------
======================  ===========================================
Hostname                ALTSERVIDOR-IA
Placa alvo              Realtek PCIe 2.5GbE (adaptador ``Ethernet``)
MAC                     BC:FC:E7:09:D9:7D
IP                      192.168.0.90 /21
Gateway                 192.168.0.10
Broadcast               192.168.7.255 e 255.255.255.255
======================  ===========================================

O broadcast é **.7.255**, não .0.255: a máscara é /21 (255.255.248.0), então a
faixa vai de 192.168.0.0 a 192.168.7.255 e o último endereço dela é o broadcast.
Errar isso é o motivo mais comum de "mandei e não acordou" nesta rede.

O que acontece depois que ele acorda
------------------------------------
Nada precisa ser feito daqui: o .90 tem autologon (~16 s após o boot) e a tarefa
``OrcaView Stack AtLogon`` sobe o OrçaView 60 s depois, com
``npm run start:all-detailed`` — é o ``tools/ops/subir_stack_boot.ps1`` do
``web_orcaview_V118``, com guarda anti-instância-dupla e backup das sessões do
WhatsApp. Este script acorda a máquina; o stack é problema dela.

Se não acordar: o roteiro
-------------------------
1. **O pacote saiu?** ``--dry-run`` imprime os 102 bytes sem enviar. Se o
   hexdump está certo e nada acontece, o problema não é aqui.
2. **Está na mesma rede?** Broadcast não passa por roteador (ver acima).
3. **Windows do .90** — os pré-requisitos foram conferidos e estavam TODOS ok em
   set/2026 (Wake on Magic Packet ligado, "só magic packet", fast startup fora do
   caminho). Não volte a perder tempo aqui antes do item 4.
4. **BIOS — é aqui que costuma estar, e nunca foi confirmado:**

   * ``Power On By PCI-E`` = **Enabled**
   * ``ErP Ready`` = **Disabled**  <- o traiçoeiro

   Com ``ErP Ready`` ligado a placa-mãe corta a energia da NIC no S5 para cumprir
   o limite de consumo em standby. A placa fica morta, mas **o Windows continua
   reportando "Wake on Magic Packet: habilitado" exatamente igual** — o
   diagnóstico pelo SO dá verde e a máquina não acorda assim mesmo. Se chegou
   até aqui, é BIOS, não é software.
5. **Faltou energia?** Algumas placas ARMAM o WOL no desligamento do Windows e
   PERDEM o armamento se a energia da tomada cair. Nesse cenário o que resolve
   não é este script, é ``Restore on AC Power Loss = Power On`` na mesma BIOS.

O que este script NÃO prova
---------------------------
O envio é testável e foi testado; **acordar de verdade, não** — isso só se
verifica no dia em que a máquina estiver realmente desligada e o pacote vier de
fora. Rodar com a máquina ligada confirma o caminho de rede e o ping, nada mais.

Uso
---
    python wake_altservidor_ia.py                    # manda e espera o ping
    python wake_altservidor_ia.py --dry-run          # só mostra o pacote
    python wake_altservidor_ia.py --no-wait          # manda e sai
    python wake_altservidor_ia.py --wait 600 --reenviar 60 --log D:\wol.log
    python wake_altservidor_ia.py --mac AA:BB:CC:DD:EE:FF --ip 192.168.0.42 \
        --broadcast 192.168.7.255                    # outra máquina

Códigos de saída: 0 = acordou (ou já estava ligada, ou pacote enviado com
--no-wait/--dry-run), 1 = não respondeu dentro do prazo, 2 = erro de uso.
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

# --- A máquina alvo (ver o cabeçalho) ---------------------------------------
HOSTNAME = "ALTSERVIDOR-IA"
MAC = "BC:FC:E7:09:D9:7D"
IP = "192.168.0.90"
BROADCASTS = ["192.168.7.255", "255.255.255.255"]

# --- Como o pacote é disparado ----------------------------------------------
PORTS = [9, 7]          # as duas convencionais; a placa não filtra por porta
REPEATS = 3             # UDP não garante entrega e o pacote tem 102 bytes
WAIT_DEFAULT = 180      # janela total de espera pelo ping, em segundos
REENVIAR_DEFAULT = 0    # 0 = uma rodada só (o padrão do uso manual)
POLL = 5                # intervalo entre pings dentro da janela

_LOG_PATH = None        # definido por --log; ver log()


def log(msg: str = "") -> None:
    """Imprime e, se ``--log`` foi passado, anexa ao arquivo com timestamp.

    Tarefa agendada não tem console: sem arquivo, o que este script descobriu
    morre com o processo. Falha de escrita nunca derruba a execução — o trabalho
    é acordar a máquina, não registrar.

    O corte em 1 MB é a lição de 11/09/2026 na .11: log sem faxineiro cresce para
    sempre. São poucas linhas por boot, mas "poucas linhas por boot" é exatamente
    o que ninguém revisita durante anos.
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
    """Aceita ``:``, ``-``, ``.`` ou nada e devolve 12 dígitos hex maiúsculos."""
    cleaned = raw.strip().replace(":", "").replace("-", "").replace(".", "").replace(" ", "")
    if len(cleaned) != 12:
        raise ValueError(f"MAC deve ter 12 digitos hex, veio {len(cleaned)}: {raw!r}")
    try:
        bytes.fromhex(cleaned)
    except ValueError:
        raise ValueError(f"MAC tem caractere que nao e hex: {raw!r}") from None
    return cleaned.upper()


def build_magic_packet(mac: str) -> bytes:
    """Os 102 bytes: seis ``0xFF`` de sincronismo + o MAC repetido 16 vezes."""
    return b"\xff" * 6 + bytes.fromhex(normalize_mac(mac)) * 16


def hexdump(data: bytes, width: int = 16) -> str:
    linhas = []
    for off in range(0, len(data), width):
        chunk = data[off:off + width]
        linhas.append(f"  {off:04x}  " + " ".join(f"{b:02x}" for b in chunk))
    return "\n".join(linhas)


def send_packet(packet: bytes, broadcast: str, port: int) -> None:
    """Um datagrama UDP com ``SO_BROADCAST`` — sem isso o kernel recusa o envio."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(packet, (broadcast, port))


def ping(host: str) -> bool:
    """Um ping, timeout de 1 s. ``True`` só se veio resposta DO host.

    O ``ping`` do Windows sai com código 0 até quando o que voltou foi
    "Host de destino inacessível" (o erro vem do roteador, e para o ``ping``
    isso é uma resposta). Por isso conferimos o TTL na saída, não o código.
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
    """Uma rodada: o pacote em cada broadcast × porta × REPEATS."""
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
    """Manda rodadas de magic packet até o host responder ou a janela acabar.

    O reenvio existe por causa do boot da .11: quando ela sobe depois de uma
    queda de energia, a porta do switch pode ainda não ter convergido e a
    primeira rodada se perde no vazio. Uma rodada custa 1,2 KB.
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
            # reenviar=0 -> uma rodada só: joga a próxima para depois do limite.
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
