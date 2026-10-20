"""arquivo_pedidos — keep who changed each sales order before the SAP drops it.

F2 of the web's ``docs/PLANO_PEDIDOS_QUEM_MEXEU.md``. The SAP change log (ADOC/ADO1) keeps
only the last 99 versions of a document (``OADM.MaxHistory``), and an OP burst fills that in
~7 min (72 versions in 5 min on 84080, 27/08/2026). Every ``INTERVALO_MIN``, on the .11 only,
this copies to the Supabase table ``pedidos_sap_alteracoes``:

* ``sap_adoc`` — the new versions of each order, classified and folded exactly as
  ``operacao.historico_pedido`` shows them (one code path for the live and the archived view);
* ``worker`` — the WBC worker's events that prove a manual change in SAP, before the worker's
  own 6-day cleanup deletes them.

The .90 writes the third source, ``tela``, before each PATCH from the Pedidos screen: the
screen saves as the integration user, so that row is the only record of the person. An
``orcaview`` version saved in the same minutes is attributed to it here (``casar_tela``).

Writes only to Supabase (never to SAP) and only on the .11 (machine identity, no ``.env``
switch). ``python -m arquivo_pedidos --simular`` runs a read-only cycle anywhere.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import situacao_pedidos_hana as hana
from operacao import historico_pedido as hp

logger = logging.getLogger(__name__)

TABELA = "pedidos_sap_alteracoes"
#: Between two cycles at most ~29 versions arrive at the worst burst measured (F0): far
#: from the 99 the SAP keeps. The probe costs 0.02-0.04 s (4,622 orders).
INTERVALO_MIN = 2
#: Orders seen for the first time per cycle (first run = every order with history, most
#: recently changed first). Orders already followed are always all processed.
NOVOS_POR_CICLO = 40
#: Worker rules that prove someone changed the order by hand in SAP.
REGRAS_WORKER: dict[str, list[str]] = {
    "pedido_corrigido_a_mao": ["cliente"],
    "troca_de_pn": ["cliente"],
    "troca_de_pn_impossivel_pedido_fechado": ["cliente"],
    "troca_de_pn_cancela_ops": ["cliente"],
    "troca_de_pn_bloqueada_op_em_producao": ["cliente"],
    "troca_de_pn_ops_desconhecidas": ["cliente"],
    "cria_pedido_no_pn_corrigido": ["cliente"],
    "peso_reescalado": ["quantidade"],
}
WORKER_POR_CICLO = 500
UPSERT_LOTE = 200
RAIZ = Path(__file__).resolve().parent
ESTADO_PADRAO = RAIZ / "state" / "arquivo_pedidos.json"
_FUSO = ZoneInfo("America/Sao_Paulo")


# ── State (the cursors) ─────────────────────────────────────────────────────────

def ler_estado(caminho: Path) -> dict[str, Any] | None:
    """``{"adoc": {doc_entry: last archived LogInstanc}, "worker_evento_id": n}``; ``None`` =
    lost or never written (the caller rebuilds the ADOC cursor from the table)."""
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(dados, dict):
        return None
    adoc = {int(k): int(v) for k, v in (dados.get("adoc") or {}).items()}
    return {"adoc": adoc, "worker_evento_id": int(dados.get("worker_evento_id") or 0)}


def gravar_estado(caminho: Path, estado: dict[str, Any]) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    tmp = caminho.with_suffix(".tmp")
    tmp.write_text(json.dumps({"adoc": {str(k): v for k, v in estado["adoc"].items()},
                               "worker_evento_id": estado["worker_evento_id"]}), encoding="utf-8")
    os.replace(tmp, caminho)


def cursor_do_banco(cliente: Any) -> dict[int, int]:
    """Rebuild the ADOC cursor from the table itself (state file lost)."""
    cursor: dict[int, int] = {}
    inicio, passo = 0, 1000
    while True:
        res = (cliente.table(TABELA).select("doc_entry,log_instanc_ate").eq("fonte", "sap_adoc")
               .order("id").range(inicio, inicio + passo - 1).execute())
        dados = res.data or []
        for r in dados:
            de, ate = int(r["doc_entry"]), int(r["log_instanc_ate"])
            cursor[de] = max(cursor.get(de, 0), ate)
        if len(dados) < passo:
            return cursor
        inicio += passo


# ── HANA ─────────────────────────────────────────────────────────────────────

def _sql_sonda(schema: str) -> str:
    return (f'SELECT "DocEntry", MAX("DocNum") AS "DocNum", MIN("LogInstanc") AS "Min", '
            f'MAX("LogInstanc") AS "Max", MAX("UpdateDate") AS "Ultima" '
            f'FROM "{schema}"."ADOC" WHERE "ObjType" = \'17\' GROUP BY "DocEntry"')


def planejar(sonda: list[dict], cursor: dict[int, int], novos: int = NOVOS_POR_CICLO) -> list[dict]:
    """Which orders to read this cycle: every followed order with a newer version, then up to
    ``novos`` never-seen orders (most recently changed first)."""
    seguidos, nunca = [], []
    for s in sonda:
        de, mx = int(s["DocEntry"]), int(s["Max"])
        item = {"doc_entry": de, "doc_num": int(s["DocNum"]), "min": int(s["Min"]), "max": mx,
                "desde": cursor.get(de)}
        if de in cursor:
            if mx > cursor[de]:
                seguidos.append(item)
        else:
            nunca.append((str(s.get("Ultima") or ""), item))
    nunca.sort(key=lambda t: t[0], reverse=True)
    return seguidos + [item for _, item in nunca[:max(0, novos)]]


# ── Rows ─────────────────────────────────────────────────────────────────────

def _linha_lacuna(doc_entry: int, doc_num: int, de: int, ate: int, momento: Any) -> dict[str, Any]:
    n = ate - de + 1
    return {"fonte": "sap_adoc", "doc_entry": doc_entry, "doc_num": doc_num,
            "log_instanc": de, "log_instanc_ate": ate, "momento": momento, "lacuna": True,
            "repeticoes": n, "sem_mudanca": 0, "mudancas": [],
            "resumo": f"{n} versão(ões) apagadas pelo SAP antes de serem lidas (versões {de} a {ate})"}


def _linha_da_entrada(doc_entry: int, doc_num: int, e: dict) -> dict[str, Any]:
    return {
        "fonte": "sap_adoc", "doc_entry": doc_entry, "doc_num": doc_num,
        "log_instanc": int(e.get("versao_de") or e["versao"]), "log_instanc_ate": int(e["versao"]),
        "momento": e["momento"], "momento_de": e.get("momento_de"),
        "usuario_codigo": e.get("usuario_codigo"), "usuario_nome": e.get("usuario"),
        "pessoa": e.get("pessoa"), "tela_id": e.get("tela_id"),
        "origem": e.get("origem"), "categorias": e.get("categorias") or [],
        "quebra": bool(e.get("quebra")), "quebra_em": e.get("quebra_em") or [],
        "resumo": (e.get("resumo") or "—")[:500], "mudancas": e.get("mudancas") or [],
        "detalhe": e.get("detalhe"), "repeticoes": int(e.get("repeticoes") or 1),
        "sem_mudanca": int(e.get("sem_mudanca") or 0),
    }


def linhas_do_pedido(plano: dict, versoes: list[dict], linhas: list[dict],
                     telas: list[dict]) -> tuple[list[dict], int]:
    """Pure: the ADOC rows read for one order → table rows, and how many versions the SAP
    dropped before this cycle could read them (an alarm when the order was followed)."""
    de, dn, desde = plano["doc_entry"], plano["doc_num"], plano["desde"]
    if not versoes:
        return [], 0
    menor = int(versoes[0]["LogInstanc"])
    entradas = hp.entradas(versoes, linhas)
    saida: list[dict] = []
    perdidas = 0
    inicio_esperado = 1 if desde is None else desde + 1
    if desde is not None and menor <= desde:
        # The base version is still there: it was archived last cycle, drop it.
        if entradas and entradas[0]["versao"] == desde and "versao_de" not in entradas[0]:
            entradas = entradas[1:]
    elif menor > inicio_esperado:
        perdidas = menor - inicio_esperado
        saida.append(_linha_lacuna(de, dn, inicio_esperado, menor - 1, entradas[0]["momento"]))
    for e in entradas:
        saida.append(_linha_da_entrada(de, dn, hp.casar_tela(e, telas)))
    return saida, perdidas


def linhas_do_worker(eventos: list[dict]) -> list[dict]:
    """Pure: worker events (tracking SQLite) → table rows."""
    saida = []
    for ev in eventos:
        momento = ev["momento"]
        if isinstance(momento, str):
            momento = datetime.fromisoformat(momento)
        if momento.tzinfo is None:
            momento = momento.replace(tzinfo=_FUSO)  # the worker writes the .11's local time
        saida.append({
            "fonte": "worker", "doc_entry": int(ev["pedido_docentry"]),
            "doc_num": int(ev["pedido_docnum"] or ev["pedido_docentry"]),
            "momento": momento.isoformat(timespec="seconds"), "acao": ev["regra"],
            "evento_id": f"{ev['id']}@{momento.isoformat(timespec='seconds')}",
            "orcamento": ev["orcnum"], "categorias": REGRAS_WORKER.get(ev["regra"], []),
            "resumo": (ev.get("mensagem") or ev["regra"])[:500], "mudancas": [],
        })
    return saida


def ler_eventos_worker(caminho: str | None, depois_de: int, limite: int = WORKER_POR_CICLO) -> list[dict]:
    """Read-only on the worker's tracking SQLite (same file ``/wbc/orcamentos/<n>`` reads)."""
    if not caminho or not os.path.exists(caminho):
        return []
    regras = list(REGRAS_WORKER)
    marcas = ", ".join("?" for _ in regras)
    con = sqlite3.connect(f"file:{caminho}?mode=ro", uri=True, timeout=2)
    try:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT e.id, e.momento, e.regra, e.mensagem, e.orcnum, a.pedido_docentry, a.pedido_docnum "
            "FROM eventos e JOIN acompanhamento a ON a.orcnum = e.orcnum "
            f"WHERE e.id > ? AND e.regra IN ({marcas}) AND a.pedido_docentry IS NOT NULL "
            "ORDER BY e.id LIMIT ?",
            (int(depois_de), *regras, int(limite))).fetchall()
    finally:
        con.close()
    return [dict(r) for r in rows]


# ── Supabase ─────────────────────────────────────────────────────────────────

#: Every row carries every column: PostgREST refuses a bulk insert whose objects have
#: different keys (PGRST102 "All object keys must match"). A gap row among normal rows stopped
#: the first real cycle on order 84377 (15/10/2026) -- and every cycle after it.
_PADRAO: dict[str, Any] = {
    "fonte": None, "doc_entry": None, "doc_num": None, "log_instanc": None, "log_instanc_ate": None,
    "momento": None, "momento_de": None, "usuario_codigo": None, "usuario_nome": None, "pessoa": None,
    "tela_id": None, "origem": None, "categorias": [], "quebra": False, "quebra_em": [], "lacuna": False,
    "resumo": None, "mudancas": [], "detalhe": None, "repeticoes": 1, "sem_mudanca": 0, "acao": None,
    "evento_id": None, "orcamento": None,
}


def completar(linha: dict[str, Any]) -> dict[str, Any]:
    """The row with every column of ``_PADRAO`` (defaults where absent)."""
    sobra = set(linha) - set(_PADRAO)
    if sobra:
        raise ValueError(f"coluna fora do arquivo: {sorted(sobra)}")
    return _PADRAO | linha


def gravar(cliente: Any, linhas: list[dict], conflito: str) -> None:
    """Upsert ignoring duplicates: a re-run after a lost cursor inserts nothing twice."""
    completas = [completar(r) for r in linhas]
    for i in range(0, len(completas), UPSERT_LOTE):
        (cliente.table(TABELA)
         .upsert(completas[i:i + UPSERT_LOTE], on_conflict=conflito, ignore_duplicates=True)
         .execute())


def ler_telas(cliente: Any, doc_entry: int) -> list[dict]:
    res = (cliente.table(TABELA).select("id,pessoa,momento,acao,resultado")
           .eq("fonte", "tela").eq("doc_entry", int(doc_entry))
           .order("momento", desc=True).limit(200).execute())
    return list(res.data or [])


def ler_do_pedido(cliente: Any, doc_entry: int) -> list[dict]:
    """Every archived row of one order (the ``/pedidos/<n>/historico`` merge)."""
    res = (cliente.table(TABELA).select("*").eq("doc_entry", int(doc_entry))
           .order("momento").limit(5000).execute())
    return list(res.data or [])


# ── The cycle ────────────────────────────────────────────────────────────────

def ciclo(conn: Any, schema: str, cliente: Any | None, *, estado_path: Path = ESTADO_PADRAO,
          tracking_db: str | None = None, simular: bool = False,
          novos: int = NOVOS_POR_CICLO) -> dict[str, Any]:
    """One pass. ``simular`` = read everything, write nothing (no cursor either)."""
    estado = ler_estado(estado_path)
    if estado is None:
        cursor: dict[int, int] = {}
        if cliente is not None:
            try:
                cursor = cursor_do_banco(cliente)
            except Exception:
                if not simular:
                    raise
                logger.warning("[ARQUIVO-PEDIDOS] simulação: tabela ilegível, cursor vazio.")
        estado = {"adoc": cursor, "worker_evento_id": 0}
    sonda = hana._linhas(conn, _sql_sonda(schema))
    plano = planejar(sonda, estado["adoc"], novos)
    resumo: dict[str, Any] = {"pedidos_com_historico": len(sonda), "lidos": 0, "linhas": 0,
                              "versoes_perdidas": 0, "quebras": 0, "worker": 0, "exemplo": []}
    for p in plano:
        desde = p["desde"]
        params: tuple = (p["doc_entry"], desde) if desde is not None else (p["doc_entry"],)
        versoes = hana._linhas(conn, hp._sql_versoes(schema, desde=desde is not None), params)
        linhas = hana._linhas(conn, hp._sql_linhas(schema, desde=desde is not None), params)
        precisa_tela = cliente is not None and any(
            (v.get("_USER_CODE") or "").strip().lower() in hp._usuarios_da_integracao() for v in versoes)
        telas: list[dict] = []
        if precisa_tela:
            try:
                telas = ler_telas(cliente, p["doc_entry"])
            except Exception:
                if not simular:
                    raise
        novas, perdidas = linhas_do_pedido(p, versoes, linhas, telas)
        resumo["lidos"] += 1
        resumo["linhas"] += len(novas)
        resumo["quebras"] += sum(1 for r in novas if r.get("quebra"))
        if perdidas:
            resumo["versoes_perdidas"] += perdidas
            if desde is not None:
                # Followed order and still lost: the cycle ran too late (service down?).
                logger.error("[ARQUIVO-PEDIDOS] pedido %s: %s versão(ões) apagadas pelo SAP antes de "
                             "serem lidas (%s a %s).", p["doc_num"], perdidas, desde + 1, desde + perdidas)
        if simular:
            if len(resumo["exemplo"]) < 3 and novas:
                resumo["exemplo"].append({"pedido": p["doc_num"], "linhas": novas[-3:]})
            continue
        if novas:
            gravar(cliente, novas, "fonte,doc_entry,log_instanc")
        estado["adoc"][p["doc_entry"]] = p["max"]
        gravar_estado(estado_path, estado)
    eventos = ler_eventos_worker(tracking_db, estado["worker_evento_id"])
    if eventos:
        resumo["worker"] = len(eventos)
        if not simular:
            gravar(cliente, linhas_do_worker(eventos), "fonte,evento_id")
            estado["worker_evento_id"] = max(int(e["id"]) for e in eventos)
            gravar_estado(estado_path, estado)
    resumo["acompanhados"] = len(estado["adoc"])
    return resumo


def _cliente_supabase() -> Any | None:
    """service_role only: the table has RLS forced and no policy, so anon would read [] and
    every insert would fail."""
    from config import get_settings
    s = get_settings()
    if not (s.supabase_url and s.supabase_service_role_key):
        return None
    from supabase import create_client
    from supabase.client import ClientOptions
    return create_client(s.supabase_url, s.supabase_service_role_key,
                         ClientOptions(postgrest_client_timeout=s.supabase_timeout_s))


def _tracking_db() -> str | None:
    from monitoring import _wbc_tracking_db_path
    return _wbc_tracking_db_path()


def job() -> dict[str, Any] | None:
    """The scheduler's entry: only on the .11, one at a time (file lock)."""
    from pipeline_core import FileLockTimeout, arquivo_pedidos_lock
    from wbcpython.safety import is_production_machine

    if not is_production_machine():
        logger.debug("[ARQUIVO-PEDIDOS] fora da .11: arquivo desligado.")
        return None
    cliente = _cliente_supabase()
    if cliente is None:
        logger.error("[ARQUIVO-PEDIDOS] sem SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY: nada arquivado.")
        return None
    try:
        with arquivo_pedidos_lock():
            conn = hana._conectar()
            try:
                resumo = ciclo(conn, hana._schema(), cliente, tracking_db=_tracking_db())
            finally:
                try:
                    conn.close()
                except Exception:  # already gone with the failure being reported
                    pass
    except FileLockTimeout:
        logger.info("[ARQUIVO-PEDIDOS] ciclo anterior ainda rodando: pulado.")
        return None
    if resumo["lidos"] or resumo["worker"]:
        logger.info("[ARQUIVO-PEDIDOS] %s pedido(s) lido(s), %s linha(s), %s quebra(s), %s evento(s) do "
                    "worker; %s acompanhado(s).", resumo["lidos"], resumo["linhas"], resumo["quebras"],
                    resumo["worker"], resumo["acompanhados"])
    return resumo


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m arquivo_pedidos",
                                 description="Arquiva quem mexeu nos pedidos (ADOC) no Supabase.")
    ap.add_argument("--simular", action="store_true",
                    help="só lê (SAP e base); não grava nada nem avança o cursor")
    ap.add_argument("--novos", type=int, default=NOVOS_POR_CICLO,
                    help="pedidos nunca vistos a ler neste ciclo")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not args.simular:
        resumo = job()
        print(json.dumps(resumo, ensure_ascii=False, default=str, indent=1))
        return 0 if resumo is not None else 2
    conn = hana._conectar()
    try:
        resumo = ciclo(conn, hana._schema(), _cliente_supabase(), estado_path=RAIZ / "state" / "_simulacao.json",
                       tracking_db=_tracking_db(), simular=True, novos=args.novos)
    finally:
        conn.close()
    print(json.dumps(resumo, ensure_ascii=False, default=str, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
