"""Who logs into the SAP B1, from where and with which process (F6 of the web repo's
docs/PLANO_TEO_REDE_E_ROTINAS.md). Read only.

Source: ``USR5``, the B1 access log. Every login/logout of an SAP client, the DI API or the Service
Layer writes the IP, the machine name, the Windows user, the executable and the PID there. That is
how, on 07/10/2026, "financeiro04 every 4 min" became ``WBCServConsole.exe`` on the .12 (DI API, no
Windows user = a task or a service) without a packet capture. The ``ALTAMIRA`` user cannot read
``M_CONNECTIONS`` -- hence USR5.

Three readings, each one short HANA connection (``situacao_pedidos_hana``: the same breaker,
``SAPIndisponivel`` -> 503) and a 120 s cache:

- :func:`resumo` -- one day grouped by (SAP user, process, IP, machine, Windows user): logins,
  logouts, sessions never closed, failures, first/last, the typical gap and whether it is a ROBOT
  (regular gap, many logins, no Windows user);
- :func:`eventos` -- the last events of one SAP user;
- :func:`escritas` -- what one SAP user created/changed in quotes, orders, opportunities and
  production orders since a day (at most :data:`ESCRITAS_DIAS_MAX` back).

``maintenance/quem_loga_no_sap.py`` is a thin CLI over this module. ``USR5.Time`` is an HHMMSS
integer (94003 = 09:40:03).
"""
from __future__ import annotations

import re
import statistics
import threading
import time
from datetime import date, timedelta
from typing import Any

import situacao_pedidos_hana as hana
from sql_seguro import sql

CACHE_S = 120.0
EVENTOS_PADRAO = 40
EVENTOS_MAX = 200
ESCRITAS_DIAS_MAX = 31
#: Looks like a robot: at least this many logins in a day, a regular gap, and no Windows user. A
#: heuristic: the flag is ``parece_robo`` and nobody should read its absence as "a person".
ROBO_MIN_LOGINS = 6
#: The day's rows read for the summary at most (a login loop writes tens of thousands).
RESUMO_MAX_LINHAS = 20000
ESCRITAS_AVISO = ("contagem mínima: o SAP guarda só a ÚLTIMA alteração de cada documento; zero não prova que o "
                  "usuário não gravou")
#: Documents an integration user typically touches; (table, label).
TABELAS_ESCRITA = (("OQUT", "cotacoes"), ("ORDR", "pedidos"), ("OOPR", "oportunidades"), ("OWOR", "OPs"))
ACOES = {"I": "login", "O": "logout", "F": "falha"}
#: An SAP user code as the B1 allows it (letters, digits, dot, dash, underscore; max 25).
_USUARIO = re.compile(r"^[A-Za-z0-9._\-]{1,25}$")

_trava = threading.Lock()
_cache: dict[tuple, tuple[float, dict[str, Any]]] = {}


class PedidoInvalido(ValueError):
    """A parameter out of its range (unknown mode, bad user code, a day too far back)."""


class UsuarioNaoEncontrado(LookupError):
    """No SAP user with that code (OUSR)."""


def hora(hhmmss: Any) -> str | None:
    """USR5.Time / CreateTS are HHMMSS integers (94003 = 09:40:03)."""
    if hhmmss is None:
        return None
    v = int(hhmmss)
    return f"{v // 10000:02d}:{v // 100 % 100:02d}:{v % 100:02d}"


def _segundos(hhmmss: Any) -> int:
    v = int(hhmmss)
    return v // 10000 * 3600 + v // 100 % 100 * 60 + v % 100


#: Scheduled: at least this share of the gaps within :data:`TOLERANCIA_RITMO` of the median gap,
#: and a median of at least :data:`RITMO_MINIMO_S` (a burst of logins in seconds is not a schedule)
#: -- unless there are :data:`LACO_MINIMO` gaps or more: a login loop every 20-30 s is exactly the
#: kind that drains the Service Layer's sessions.
REGULARES_MIN = 0.8
TOLERANCIA_RITMO = 0.1
RITMO_MINIMO_S = 60
LACO_MINIMO = 20


def ritmo(horas: list[Any]) -> tuple[str, bool]:
    """Median gap between consecutive logins ("4m00s") and whether it looks scheduled: most gaps
    within 10 % (or 5 s) of the median. A share, not a standard deviation: on 07/10 the real robot
    (financeiro04 / WBCServConsole.exe every 4 min) had ONE 8-min gap at 06:20 that pushed the
    deviation to 31 s and hid it (first real reading of F6b)."""
    if len(horas) < 3:
        return "", False
    seg = sorted(_segundos(h) for h in horas)
    gaps = [b - a for a, b in zip(seg, seg[1:]) if b > a]
    if len(gaps) < 2:
        return "", False
    med = statistics.median(gaps)
    tolerancia = max(5.0, med * TOLERANCIA_RITMO)
    dentro = sum(1 for g in gaps if abs(g - med) <= tolerancia)
    regular = (med >= RITMO_MINIMO_S or len(gaps) >= LACO_MINIMO) and dentro / len(gaps) >= REGULARES_MIN
    return f"{int(med) // 60}m{int(med) % 60:02d}s", regular


def agrupar(linhas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """USR5 rows of one day -> groups by (SAP user, process, IP, machine, Windows user), most
    logins first (pure; the tests feed it real rows)."""
    grupos: dict[tuple, dict[str, Any]] = {}
    for r in linhas:
        chave = (str(r.get("UserCode") or ""), str(r.get("ProcName") or ""), str(r.get("ClientIP") or ""),
                 str(r.get("ClientName") or ""), str(r.get("WinUsrName") or ""))
        g = grupos.setdefault(chave, {"I": [], "O": 0, "F": 0})
        acao = r.get("Action")
        if acao == "I" and r.get("Time") is not None:
            g["I"].append(r["Time"])
        elif acao == "O":
            g["O"] += 1
        elif acao == "F":
            g["F"] += 1
    saida = []
    for (usuario, proc, ip, maquina, winusr), g in sorted(grupos.items(), key=lambda kv: len(kv[1]["I"]), reverse=True):
        logins = g["I"]
        tipico, regular = ritmo(logins)
        saida.append({
            "usuario": usuario, "processo": proc or None, "ip": ip or None, "maquina": maquina or None,
            "usuario_windows": winusr or None, "logins": len(logins), "logouts": g["O"],
            "sem_logout": max(len(logins) - g["O"], 0), "falhas": g["F"],
            "primeiro": hora(min(logins)) if logins else None, "ultimo": hora(max(logins)) if logins else None,
            "ritmo": tipico or None, "parece_robo": regular and len(logins) >= ROBO_MIN_LOGINS and not winusr,
        })
    return saida


def _consultar(conn, consulta: tuple[str, list[Any]]) -> list[dict[str, Any]]:
    """One ``sql(t"...")`` query on an open connection; a failing query is ``SAPIndisponivel``
    with the exception's type only (no driver text reaches the caller)."""
    texto, params = consulta
    try:
        return hana._linhas(conn, texto, tuple(params))
    except hana.SAPIndisponivel:
        raise
    except Exception as e:  # the query itself failed: the HANA answered but not this
        raise hana.SAPIndisponivel(f"Leitura do log de acesso do SAP falhou: {type(e).__name__}") from e


def _numa_conexao(fazer):
    """``fazer(conn, schema)`` inside ONE short HANA connection (review of F6a: ``escritas`` ran
    five queries in five connections)."""
    schema = hana._schema()
    conn = hana._conectar()
    try:
        return fazer(conn, schema)
    finally:
        try:
            conn.close()
        except Exception:  # connection already gone with the failure being reported
            pass


def _em_cache(chave: tuple, ler) -> dict[str, Any]:
    with _trava:
        achado = _cache.get(chave)
        if achado and time.monotonic() - achado[0] < CACHE_S:
            return {**achado[1], "cache": True}
    dados = ler()
    with _trava:
        _cache[chave] = (time.monotonic(), dados)
        for velha in [k for k, (em, _d) in _cache.items() if time.monotonic() - em >= CACHE_S]:
            _cache.pop(velha, None)
    return {**dados, "cache": False}


def _usuario(usuario: str) -> str:
    usuario = (usuario or "").strip()
    if not _USUARIO.match(usuario):
        raise PedidoInvalido("usuário do SAP inválido (letras, números, ponto, hífen ou _; até 25)")
    return usuario


def _do_sap(conn, schema: str, usuario: str) -> tuple[int, str]:
    """``(USERID, USER_CODE as the SAP stores it)`` from OUSR -- a small table, so the LOWER() is
    cheap there; the big tables are then filtered by the exact code/id. Unknown -> 404."""
    ousr = f'"{schema}"."OUSR"'
    achou = _consultar(conn, sql(t'SELECT "USERID", "USER_CODE" FROM {ousr:ident} '
                                 t'WHERE LOWER("USER_CODE") = LOWER({usuario})'))
    if not achou:
        raise UsuarioNaoEncontrado(f"o usuário {usuario} não existe no SAP")
    return int(achou[0]["USERID"]), str(achou[0]["USER_CODE"]).strip()


def resumo(dia: date) -> dict[str, Any]:
    """One day of logins, grouped (see :func:`agrupar`), and the ones that look like robots. At
    most :data:`RESUMO_MAX_LINHAS` rows come back (``truncado``): a login loop can write tens of
    thousands in a day."""
    def ler() -> dict[str, Any]:
        def fazer(conn, schema: str) -> list[dict[str, Any]]:
            usr5 = f'"{schema}"."USR5"'
            return _consultar(conn, sql(t'''SELECT "UserCode", "ProcName", "ClientIP", "ClientName", "WinUsrName",
                                 "Action", "Time" FROM {usr5:ident} WHERE "Date" = {dia}
                                 ORDER BY "Time" LIMIT {RESUMO_MAX_LINHAS + 1:int}'''))
        linhas = _numa_conexao(fazer)
        truncado = len(linhas) > RESUMO_MAX_LINHAS
        grupos = agrupar(linhas[:RESUMO_MAX_LINHAS])
        return {"modo": "resumo", "dia": dia.isoformat(), "eventos": min(len(linhas), RESUMO_MAX_LINHAS),
                "truncado": truncado, "grupos": grupos, "parecem_robos": [g for g in grupos if g["parece_robo"]]}
    return _em_cache(("resumo", dia.isoformat()), ler)


def eventos(usuario: str, limite: int = EVENTOS_PADRAO, *, hoje: date | None = None) -> dict[str, Any]:
    """The last events of one SAP user in the last :data:`ESCRITAS_DIAS_MAX` days, newest first
    (review of F6a: no date bound meant a sort of the whole USR5 on every call)."""
    usuario = _usuario(usuario)
    limite = max(1, min(int(limite or EVENTOS_PADRAO), EVENTOS_MAX))
    desde = (hoje or date.today()) - timedelta(days=ESCRITAS_DIAS_MAX)

    def ler() -> dict[str, Any]:
        def fazer(conn, schema: str) -> dict[str, Any]:
            _uid, codigo = _do_sap(conn, schema, usuario)
            usr5 = f'"{schema}"."USR5"'
            linhas = _consultar(conn, sql(t'''SELECT "Date", "Time", "Action", "ClientIP", "ClientName", "WinUsrName",
                                 "WinSessnID", "ProcName", "ProcessID", "Source" FROM {usr5:ident}
                                 WHERE "UserCode" = {codigo} AND "Date" >= {desde}
                                 ORDER BY "Date" DESC, "Time" DESC LIMIT {limite:int}'''))
            return {"modo": "eventos", "usuario": codigo, "desde": desde.isoformat(), "eventos": [{
                "dia": str(r.get("Date") or "")[:10] or None, "hora": hora(r.get("Time")),
                "acao": ACOES.get(str(r.get("Action") or ""), r.get("Action")), "ip": r.get("ClientIP") or None,
                "maquina": r.get("ClientName") or None, "usuario_windows": r.get("WinUsrName") or None,
                "sessao_windows": r.get("WinSessnID"), "processo": r.get("ProcName") or None,
                "pid": r.get("ProcessID"), "fonte": r.get("Source") or None} for r in linhas]}
        return _numa_conexao(fazer)
    return _em_cache(("eventos", usuario.lower(), limite, desde.isoformat()), ler)


def escritas(usuario: str, desde: date, *, hoje: date | None = None) -> dict[str, Any]:
    """Per day, how many quotes, orders, opportunities and production orders one SAP user
    created or changed since ``desde`` (at most :data:`ESCRITAS_DIAS_MAX` days back).

    A FLOOR, not a full count: the SAP keeps only the LAST change of a document (``UserSign2`` /
    ``UpdateDate``) -- a document he created and someone else changed later leaves his count, and
    zero does not prove he wrote nothing (review of F6a)."""
    usuario = _usuario(usuario)
    hoje = hoje or date.today()
    if desde > hoje or (hoje - desde).days > ESCRITAS_DIAS_MAX:
        raise PedidoInvalido(f"'desde' tem de estar nos últimos {ESCRITAS_DIAS_MAX} dias")

    def ler() -> dict[str, Any]:
        def fazer(conn, schema: str) -> dict[str, Any]:
            uid, codigo = _do_sap(conn, schema, usuario)
            tabelas = {}
            for tabela, rotulo in TABELAS_ESCRITA:
                alvo = f'"{schema}"."{tabela}"'
                linhas = _consultar(conn, sql(t"""SELECT TO_VARCHAR("UpdateDate", 'YYYY-MM-DD') AS "Dia",
                       SUM(CASE WHEN "UserSign" = {uid} AND "CreateDate" = "UpdateDate" THEN 1 ELSE 0 END) AS "Criados",
                       SUM(CASE WHEN "UserSign2" = {uid} THEN 1 ELSE 0 END) AS "Alterados"
                       FROM {alvo:ident} WHERE "UpdateDate" >= {desde} AND ("UserSign" = {uid} OR "UserSign2" = {uid})
                       GROUP BY "UpdateDate" ORDER BY "Dia" DESC"""))
                tabelas[rotulo] = [{"dia": r["Dia"], "criados": int(r["Criados"] or 0),
                                    "alterados": int(r["Alterados"] or 0)}
                                   for r in linhas if r.get("Criados") or r.get("Alterados")]
            return {"modo": "escritas", "usuario": codigo, "desde": desde.isoformat(), "tabelas": tabelas,
                    "aviso": ESCRITAS_AVISO}
        return _numa_conexao(fazer)
    return _em_cache(("escritas", usuario.lower(), desde.isoformat()), ler)


def limpar_cache() -> None:
    with _trava:
        _cache.clear()


def ler(modo: str, *, dia: str = "", usuario: str = "", limite: int | str = "", desde: str = "") -> dict[str, Any]:
    """The route's entry: ``modo`` resumo | eventos | escritas with their parameters as text.

    Raises:
        PedidoInvalido: unknown mode or a parameter out of range (-> 400).
        UsuarioNaoEncontrado: no such SAP user (-> 404).
        situacao_pedidos_hana.SAPIndisponivel: HANA down or the query failed (-> 503).
    """
    def _dia(texto: str, nome: str) -> date:
        try:
            return date.fromisoformat(texto) if texto else date.today()
        except ValueError as e:
            raise PedidoInvalido(f"'{nome}' tem de ser AAAA-MM-DD") from e

    modo = (modo or "resumo").strip().lower()
    if modo == "resumo":
        d = _dia(dia, "dia")
        if d > date.today() or (date.today() - d).days > ESCRITAS_DIAS_MAX:
            raise PedidoInvalido(f"'dia' tem de estar nos últimos {ESCRITAS_DIAS_MAX} dias")
        return resumo(d)
    if modo == "eventos":
        try:
            n = int(limite) if str(limite).strip() else EVENTOS_PADRAO
        except ValueError as e:
            raise PedidoInvalido("'limite' tem de ser um número") from e
        return eventos(usuario, n)
    if modo == "escritas":
        return escritas(usuario, _dia(desde, "desde") if desde else date.today() - timedelta(days=7))
    raise PedidoInvalido("'modo' tem de ser resumo, eventos ou escritas")
