"""Quem faz login no SAP B1, de onde e com que processo — SOMENTE LEITURA.

Fonte: ``USR5`` (Access Log do B1). Cada login/logout de cliente SAP, DI API ou Service
Layer grava ali o IP, o nome da maquina, o usuario do Windows, o executavel e o PID. Foi
assim que, em 07/10/2026, o "financeiro04 a cada 4 min" virou ``WBCServConsole.exe`` na
.12 (DI API, sem usuario Windows = tarefa/servico), sem precisar de tcpdump na VM.

Since F6 of the web repo's PLANO_TEO_REDE_E_ROTINAS this is a thin CLI over
``operacao/logins_sap.py`` (the same reading as ``GET /operacao/logins-sap`` and the MCP tool
``quem_loga_no_sap``): same numbers in the three places.

Uso (na raiz do SIS, na .11 ou no notebook):

    python maintenance/quem_loga_no_sap.py                       # resumo de hoje
    python maintenance/quem_loga_no_sap.py --dia 2026-10-06
    python maintenance/quem_loga_no_sap.py --usuario financeiro04  # ultimos eventos dele
    python maintenance/quem_loga_no_sap.py --escritas financeiro04 --desde 2026-09-25

Como ler o resumo: ``sem_logout`` alto = sessao que o programa abre e nao fecha;
``ritmo`` = intervalo tipico entre logins (``4m00s`` cravado = agendado); ``ROBO`` =
muitos logins em ritmo regular sem usuario do Windows (tarefa agendada ou servico).
``libB1_Engine.so`` vindo do IP da VM (192.168.7.10) e o proprio Service Layer.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

_RAIZ = Path(__file__).resolve().parents[1]
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

import situacao_pedidos_hana as hana  # noqa: E402
from operacao import logins_sap  # noqa: E402


def imprimir_resumo(dados: dict) -> None:
    corte = " -- CORTADO: o dia tem mais linhas" if dados.get("truncado") else ""
    print(f"\nLogins no SAP em {dados['dia']} ({dados['eventos']} eventos no USR5{corte})\n")
    cab = (
        f"{'usuario SAP':<14} {'processo':<24} {'IP':<15} {'maquina':<22} {'usuario Windows':<16} "
        f"{'login':>5} {'logout':>6} {'s/logout':>8} {'falha':>5} {'1o':>8} {'ultimo':>8} {'ritmo':>7}"
    )
    print(cab)
    print("-" * len(cab))
    for g in dados["grupos"]:
        print(
            f"{g['usuario']:<14.14} {g['processo'] or '':<24.24} {g['ip'] or '':<15.15} {g['maquina'] or '':<22.22} "
            f"{g['usuario_windows'] or '(nenhum)':<16.16} {g['logins']:>5} {g['logouts']:>6} {g['sem_logout']:>8} "
            f"{g['falhas']:>5} {g['primeiro'] or '':>8} {g['ultimo'] or '':>8} {g['ritmo'] or '':>7}"
            f"{'  <- PARECE ROBO' if g['parece_robo'] else ''}"
        )


def imprimir_eventos(dados: dict) -> None:
    print(f"\nUltimos {len(dados['eventos'])} eventos de {dados['usuario']}\n")
    for e in dados["eventos"]:
        print(
            f"{e['dia']} {e['hora'] or '--:--:--'} {e['acao']:<6} {e['ip'] or '':<15} {e['maquina'] or '':<20.20} "
            f"win={e['usuario_windows'] or '-':<14.14} sessao={e['sessao_windows']} {e['processo'] or '':<24.24} "
            f"pid={e['pid']} {e['fonte'] or ''}"
        )


def imprimir_escritas(dados: dict) -> None:
    print(f"\nDocumentos criados/alterados por {dados['usuario']} desde {dados['desde']} ({dados['aviso']})\n")
    for rotulo, dias in dados["tabelas"].items():
        print(f"{rotulo}: " + ("nenhum" if not dias else ""))
        for d in dias:
            print(f"   {d['dia']}  criados={d['criados']:>4}  alterados={d['alterados']:>4}")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")  # cp850 console on the servers
    ap = argparse.ArgumentParser(description="Quem faz login no SAP (USR5), somente leitura.")
    ap.add_argument("--dia", default="")
    ap.add_argument("--usuario", help="lista os ultimos eventos deste usuario SAP")
    ap.add_argument("--limite", type=int, default=logins_sap.EVENTOS_PADRAO)
    ap.add_argument("--escritas", metavar="USUARIO", help="o que este usuario criou/alterou")
    ap.add_argument("--desde", default="")
    args = ap.parse_args()
    try:
        if args.usuario:
            imprimir_eventos(logins_sap.ler("eventos", usuario=args.usuario, limite=args.limite))
        elif args.escritas:
            imprimir_escritas(logins_sap.ler("escritas", usuario=args.escritas, desde=args.desde or args.dia
                                             or date.today().isoformat()))
        else:
            imprimir_resumo(logins_sap.ler("resumo", dia=args.dia))
    except (logins_sap.PedidoInvalido, logins_sap.UsuarioNaoEncontrado, hana.SAPIndisponivel) as exc:
        print(f"ERRO: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
