"""Restart ``sapb1servertools`` on the SAP VM over SSH -- only after a person approved it.

PLANO_RESET_SAP_TEO (web repo, 12/10/2026). On 12/10 the Service Layer login failed for hours
while its ``/ping`` still answered "pong"; port 40000 (Control Center, SLD, License Manager)
refused connections, and ``systemctl restart sapb1servertools`` alone brought everything back.

Guards, every one in code:
- runs only on the .11 (by its IP, ``wbcpython.safety``) -- the key lives there, outside the repo
  (this repo is public): ``state/ssh/``;
- refuses when port 40000 already answers: a late approval never restarts a healthy SAP;
- ``BatchMode`` (never a password prompt) and a pinned host key (``StrictHostKeyChecking=yes``);
- on the VM the key itself is restricted (``restrict,from=<.11>,command=...``): whatever is sent,
  only this restart runs. :func:`preparar` prints that line.

CLI (on the .11, as an administrator): ``python -m operacao.sap_servertools --estado``,
``--preparar`` (key + known_hosts + the authorized_keys line) and ``--ensaio`` (a real restart,
measured; run it off-peak).
"""
from __future__ import annotations

import argparse
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from config import get_settings

RAIZ = Path(__file__).resolve().parents[1]
PASTA = RAIZ / "state" / "ssh"
CHAVE = PASTA / "teo_sap_ed25519"
CONHECIDOS = PASTA / "known_hosts"
LINHA = PASTA / "linha_authorized_keys.txt"
SERVICO = "sapb1servertools"
PORTA = 40000
USUARIO_REMOTO = "root"
COMANDO = f"/usr/bin/systemctl restart {SERVICO}"
TEMPO_SSH_S = 120
ESPERA_VOLTA_S = 180
INTERVALO_S = 10
#: Well-known SIDs: Windows in Portuguese names the group "Administradores", icacls by name would miss.
_SIDS = ("*S-1-5-18", "*S-1-5-32-544")

AVISO = ("Reinicia o serviço do servidor do SAP (sapb1servertools) na VM do SAP: Control Center, licença e "
         "login do Service Layer voltam em 1 a 4 min. Requisições ao Service Layer nesse intervalo falham "
         "(o worker WBC tenta de novo no próximo ciclo). Recusado se a porta 40000 já responder.")


class Recusado(RuntimeError):
    """The restart must not run now (not the .11, no key, SAP already answering...)."""


def host() -> str:
    h = (get_settings().sap_host or "").strip()
    if not h:
        raise Recusado("SAP_HOST vazio no .env da .11: não sei onde fica a VM do SAP.")
    return h


def porta_aberta(destino: str, porta: int = PORTA, tempo: float = 5) -> bool:
    try:
        with socket.create_connection((destino, porta), timeout=tempo):
            return True
    except OSError:
        return False


def _na_11() -> bool:
    from wbcpython import safety

    return safety.is_production_machine()


def _ssh() -> str:
    caminho = shutil.which("ssh")
    if not caminho:
        raise Recusado("ssh.exe não encontrado (OpenSSH Client do Windows).")
    return caminho


def _comando_ssh(destino: str) -> list[str]:
    return [_ssh(), "-i", str(CHAVE), "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={CONHECIDOS}",
            "-o", "ConnectTimeout=10", f"{USUARIO_REMOTO}@{destino}", COMANDO]


def previa() -> dict[str, Any]:
    """What a person reads before approving. Refuses up front when the SAP already answers."""
    destino = host()
    if porta_aberta(destino):
        raise Recusado("A porta 40000 da VM do SAP responde: o servidor do SAP está de pé, nada a reiniciar.")
    return {"texto": AVISO, "servico": SERVICO, "porta_40000": False}


def reiniciar(*, ensaio: bool = False, relogio: Any = None) -> dict[str, Any]:
    """Run the restart and wait for port 40000. ``ensaio`` skips only the "already answering"
    guard (the off-peak drill restarts a healthy SAP on purpose)."""
    relogio = relogio or time
    if not _na_11():
        raise Recusado("Só a .11 reinicia o servidor do SAP (a chave mora lá).")
    if not CHAVE.exists() or not CONHECIDOS.exists():
        raise Recusado("Falta a chave SSH na .11: rode 'python -m operacao.sap_servertools --preparar'.")
    destino = host()
    if not ensaio and porta_aberta(destino):
        raise Recusado("A porta 40000 já responde: o servidor do SAP voltou sozinho; nada foi reiniciado.")
    inicio = relogio.monotonic()
    try:
        r = subprocess.run(_comando_ssh(destino), capture_output=True, text=True, timeout=TEMPO_SSH_S,
                           encoding="utf-8", errors="replace", check=False)
    except subprocess.TimeoutExpired:
        return {"ok": False, "motivo": f"o SSH não terminou em {TEMPO_SSH_S} s"}
    saida = (r.stderr or r.stdout or "").strip()[-300:]
    if r.returncode != 0:
        motivo = ("a VM recusou a chave ou a conexão" if r.returncode == 255
                  else f"o systemctl terminou com código {r.returncode}")
        return {"ok": False, "motivo": motivo, "codigo": r.returncode, "saida": saida}
    while relogio.monotonic() - inicio < ESPERA_VOLTA_S:
        if porta_aberta(destino):
            return {"ok": True, "segundos": round(relogio.monotonic() - inicio), "porta_40000": True}
        relogio.sleep(INTERVALO_S)
    return {"ok": False, "motivo": f"reiniciado, mas a porta 40000 não abriu em {ESPERA_VOLTA_S} s",
            "porta_40000": False}


def _rodar(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, check=False, encoding="utf-8", errors="replace")


def preparar() -> str:
    """Key (once), the VM's host key (pinned) and the authorized_keys line. Idempotent."""
    from wbcpython import safety

    PASTA.mkdir(parents=True, exist_ok=True)
    if not CHAVE.exists():
        keygen = shutil.which("ssh-keygen") or "ssh-keygen"
        r = _rodar([keygen, "-t", "ed25519", "-N", "", "-C", "teo-11-sap", "-f", str(CHAVE), "-q"])
        if r.returncode != 0:
            raise Recusado(f"ssh-keygen falhou: {r.stderr.strip()[:200]}")
    # OpenSSH refuses a private key others can read: SYSTEM and Administrators only.
    _rodar(["icacls", str(CHAVE), "/inheritance:r", "/grant:r", *[f"{s}:F" for s in _SIDS]])
    destino = host()
    scan = _rodar([shutil.which("ssh-keyscan") or "ssh-keyscan", "-t", "ed25519,ecdsa,rsa", destino])
    if not scan.stdout.strip():
        raise Recusado(f"ssh-keyscan não leu a VM {destino}: {scan.stderr.strip()[:200]}")
    CONHECIDOS.write_text(scan.stdout, encoding="utf-8", newline="\n")
    publica = Path(f"{CHAVE}.pub").read_text(encoding="utf-8").strip()
    linha = f'restrict,from="{safety.PRODUCTION_MACHINE_IP}",command="{COMANDO}" {publica}'
    LINHA.write_text(linha + "\n", encoding="utf-8", newline="\n")
    digitais = _rodar([shutil.which("ssh-keygen") or "ssh-keygen", "-lf", str(CONHECIDOS)]).stdout.strip()
    return (f"Impressões digitais da VM ({destino}) gravadas em known_hosts:\n{digitais}\n\n"
            "Na VM do SAP, como root, cole (uma linha só):\n"
            f"mkdir -p /root/.ssh && chmod 700 /root/.ssh && echo '{linha}' >> /root/.ssh/authorized_keys "
            "&& chmod 600 /root/.ssh/authorized_keys\n\n"
            f"A mesma linha ficou em {LINHA}.")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m operacao.sap_servertools", description=__doc__.split("\n")[0])
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--estado", action="store_true", help="a porta 40000 responde? (só lê)")
    g.add_argument("--preparar", action="store_true", help="gera a chave e imprime a linha do authorized_keys")
    g.add_argument("--ensaio", action="store_true", help="reinicia DE VERDADE e mede a volta (fora do pico)")
    a = p.parse_args(argv)
    try:
        if a.estado:
            aberta = porta_aberta(host())
            print(f"porta 40000: {'responde' if aberta else 'NÃO responde'}")
            return 0 if aberta else 1
        if a.preparar:
            print(preparar())
            return 0
        resultado = reiniciar(ensaio=True)
    except Recusado as exc:
        print(f"recusado: {exc}", file=sys.stderr)
        return 2
    print(resultado)
    return 0 if resultado.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
