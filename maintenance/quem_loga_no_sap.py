"""Quem faz login no SAP B1, de onde e com que processo — SOMENTE LEITURA.

Fonte: ``USR5`` (Access Log do B1). Cada login/logout de cliente SAP, DI API ou Service
Layer grava ali o IP, o nome da maquina, o usuario do Windows, o executavel e o PID. Foi
assim que, em 07/10/2026, o "financeiro04 a cada 4 min" virou ``WBCServConsole.exe`` na
.12 (DI API, sem usuario Windows = tarefa/servico), sem precisar de tcpdump na VM.

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
import statistics
import sys
from datetime import date
from pathlib import Path

_RAIZ = Path(__file__).resolve().parents[1]
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

from config import get_settings  # noqa: E402
from sap_connection import SAPExtractor  # noqa: E402
from sql_seguro import nome_simples, sql  # noqa: E402

# Documents an integration user typically touches; (table, label).
_TABELAS_ESCRITA = (("OQUT", "cotacoes"), ("ORDR", "pedidos"), ("OOPR", "oportunidades"), ("OWOR", "OPs"))


def _hora(hhmmss: int | None) -> str:
    """USR5.Time / CreateTS are HHMMSS integers (94003 = 09:40:03)."""
    if hhmmss is None:
        return "--:--:--"
    v = int(hhmmss)
    return f"{v // 10000:02d}:{v // 100 % 100:02d}:{v % 100:02d}"


def _segundos(hhmmss: int) -> int:
    v = int(hhmmss)
    return v // 10000 * 3600 + v // 100 % 100 * 60 + v % 100


def _ritmo(horas: list[int]) -> tuple[str, bool]:
    """Median gap between consecutive logins and whether it looks scheduled (low spread)."""
    if len(horas) < 3:
        return "", False
    seg = sorted(_segundos(h) for h in horas)
    gaps = [b - a for a, b in zip(seg, seg[1:]) if b > a]
    if len(gaps) < 2:
        return "", False
    med = statistics.median(gaps)
    # Scheduled jobs hit the same second; humans and add-ons do not.
    regular = statistics.pstdev(gaps) <= max(5.0, med * 0.1)
    return f"{int(med) // 60}m{int(med) % 60:02d}s", regular


def _df(ex: SAPExtractor, consulta: tuple[str, list]) -> list[dict]:
    df = ex.execute_query(*consulta)
    return [] if df is None else df.to_dict("records")


def resumo(ex: SAPExtractor, schema: str, dia: date) -> None:
    usr5 = f'"{schema}"."USR5"'
    linhas = _df(
        ex,
        sql(t'''SELECT "UserCode", "ProcName", "ClientIP", "ClientName", "WinUsrName",
               "Action", "Time" FROM {usr5:ident} WHERE "Date" = {dia} ORDER BY "Time"'''),
    )
    grupos: dict[tuple, dict] = {}
    for r in linhas:
        chave = (r["UserCode"], r["ProcName"] or "", r["ClientIP"] or "", r["ClientName"] or "", r["WinUsrName"] or "")
        g = grupos.setdefault(chave, {"I": [], "O": 0, "F": 0})
        if r["Action"] == "I":
            g["I"].append(r["Time"])
        elif r["Action"] == "O":
            g["O"] += 1
        elif r["Action"] == "F":
            g["F"] += 1
    print(f"\nLogins no SAP em {dia.isoformat()} ({len(linhas)} eventos no USR5)\n")
    cab = (
        f"{'usuario SAP':<14} {'processo':<24} {'IP':<15} {'maquina':<22} {'usuario Windows':<16} "
        f"{'login':>5} {'logout':>6} {'s/logout':>8} {'falha':>5} {'1o':>8} {'ultimo':>8} {'ritmo':>7}"
    )
    print(cab)
    print("-" * len(cab))
    ordem = sorted(grupos.items(), key=lambda kv: len(kv[1]["I"]), reverse=True)
    for (usuario, proc, ip, maquina, winusr), g in ordem:
        logins = g["I"]
        ritmo, regular = _ritmo(logins)
        robo = regular and len(logins) >= 6 and not winusr
        print(
            f"{usuario:<14.14} {proc:<24.24} {ip:<15.15} {maquina:<22.22} {winusr or '(nenhum)':<16.16} "
            f"{len(logins):>5} {g['O']:>6} {max(len(logins) - g['O'], 0):>8} {g['F']:>5} "
            f"{_hora(min(logins)) if logins else '':>8} {_hora(max(logins)) if logins else '':>8} "
            f"{ritmo:>7}{'  <- ROBO' if robo else ''}"
        )


def eventos(ex: SAPExtractor, schema: str, usuario: str, limite: int) -> None:
    usr5 = f'"{schema}"."USR5"'
    linhas = _df(
        ex,
        sql(t"""SELECT "Date", "Time", "Action", "ClientIP", "ClientName", "WinUsrName",
               "WinSessnID", "ProcName", "ProcessID", "Source" FROM {usr5:ident}
               WHERE LOWER("UserCode") = LOWER({usuario}) ORDER BY "Date" DESC, "Time" DESC
               LIMIT {limite:int}"""),
    )
    print(f"\nUltimos {len(linhas)} eventos de {usuario} (I=login, O=logout, F=falha)\n")
    for r in linhas:
        print(
            f"{str(r['Date'])[:10]} {_hora(r['Time'])} {r['Action']} {r['ClientIP'] or '':<15} "
            f"{r['ClientName'] or '':<20.20} win={r['WinUsrName'] or '-':<14.14} sessao={r['WinSessnID']} "
            f"{r['ProcName'] or '':<24.24} pid={r['ProcessID']} {r['Source'] or ''}"
        )


def escritas(ex: SAPExtractor, schema: str, usuario: str, desde: date) -> None:
    ousr = f'"{schema}"."OUSR"'
    achou = _df(ex, sql(t'SELECT "USERID" FROM {ousr:ident} WHERE LOWER("USER_CODE") = LOWER({usuario})'))
    if not achou:
        print(f"usuario {usuario} nao existe na OUSR")
        return
    uid = int(achou[0]["USERID"])
    print(f"\nDocumentos criados/alterados por {usuario} (USERID {uid}) desde {desde.isoformat()}\n")
    for tabela, rotulo in _TABELAS_ESCRITA:
        alvo = f'"{schema}"."{tabela}"'
        linhas = _df(
            ex,
            sql(t"""SELECT TO_VARCHAR("UpdateDate", 'YYYY-MM-DD') AS "Dia",
                   SUM(CASE WHEN "UserSign" = {uid} AND "CreateDate" = "UpdateDate" THEN 1 ELSE 0 END) AS "Criados",
                   SUM(CASE WHEN "UserSign2" = {uid} THEN 1 ELSE 0 END) AS "Alterados"
                   FROM {alvo:ident} WHERE "UpdateDate" >= {desde} AND ("UserSign" = {uid} OR "UserSign2" = {uid})
                   GROUP BY "UpdateDate" ORDER BY "Dia" DESC"""),
        )
        dias = [r for r in linhas if r["Criados"] or r["Alterados"]]
        print(f"{rotulo} ({tabela}): " + ("nenhum" if not dias else ""))
        for r in dias:
            print(f"   {r['Dia']}  criados={r['Criados']:>4}  alterados={r['Alterados']:>4}")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")  # cp850 console on the servers
    ap = argparse.ArgumentParser(description="Quem faz login no SAP (USR5), somente leitura.")
    ap.add_argument("--dia", type=date.fromisoformat, default=date.today())
    ap.add_argument("--usuario", help="lista os ultimos eventos deste usuario SAP")
    ap.add_argument("--limite", type=int, default=40)
    ap.add_argument("--escritas", metavar="USUARIO", help="o que este usuario criou/alterou")
    ap.add_argument("--desde", type=date.fromisoformat, default=None)
    args = ap.parse_args()

    s = get_settings()
    if not s.sap_ready():
        print("ERRO: credenciais SAP ausentes no .env")
        return 2
    schema = nome_simples(s.sap_schema or s.sap_database or "", what="SAP_SCHEMA")
    ex = SAPExtractor(s.sap_host, s.sap_port, s.sap_user, s.sap_password, s.sap_database)
    if not ex.connect():
        print("ERRO: nao conectou no HANA")
        return 2
    try:
        if args.usuario:
            eventos(ex, schema, args.usuario, args.limite)
        elif args.escritas:
            escritas(ex, schema, args.escritas, args.desde or args.dia)
        else:
            resumo(ex, schema, args.dia)
    finally:
        ex.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
