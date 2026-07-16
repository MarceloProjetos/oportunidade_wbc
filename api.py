"""Minimal HTTP API to trigger the OS sync per NPED (on demand).

Designed to be called **by the app** (web/desktop) when a user asks to sync a pedido.
Writing remains the backend's job (service_role); this service only exposes an HTTP
trigger.

Endpoints
---------
- ``GET  /health``                          → ``{"status": "ok"}``
- ``GET  /ordens-servico/<nped>``           → detail (summary) of a pedido's OS
- ``POST /ordens-servico/<nped>/sincronizar`` → syncs + returns the summary (GET's pair)
- ``POST /sync/ordens-servico/<nped>``      → syncs **one** pedido
- ``POST /sync/ordens-servico``             → body ``{"nped": N}`` or ``{"npeds": [...]}``

Authentication (optional, **recommended in production**)
--------------------------------------------------------
Set ``OS_API_KEY`` in ``.env``. The client must send the ``X-API-Key: <key>`` header
(or ``Authorization: Bearer <key>``). Without ``OS_API_KEY`` the endpoint is **open**
(use only on a trusted internal network / development).

How to run
----------
- Dev/Production: ``python api.py`` (the supported form — serves via waitress if
                  installed, otherwise Flask dev, and configures file logging to
                  ``logs/api.log``).
- Alternative:    ``waitress-serve --listen=0.0.0.0:8077 api:app`` — imports only ``app``,
                  so it does **not** go through ``main()``: logging uses the default
                  (no file).

Example call::

    curl -X POST http://localhost:8077/sync/ordens-servico/84080 \\
         -H "X-API-Key: YOUR_KEY"
"""

from __future__ import annotations

import hmac
import logging
import os
import sys
import threading
import time
from functools import wraps
from logging.handlers import TimedRotatingFileHandler
from typing import Any, List, Optional, Tuple

from flask import Flask, jsonify, request, send_from_directory

import windows_update
from config import get_settings
from extract_ordens_servico_engenharia import (
    diagnosticar_nped,
    listar_pedidos_com_os,
)
from extract_ordens_servico_engenharia import (
    main as sync_os,
)
from extract_sap_to_supabase import main as sync_oportunidades
from monitoring import SELECTABLE_CHECKS, collect_status
from pipeline_core import FileLockTimeout, coerce_positive_int, oportunidades_sync_lock

# UTF-8 console on Windows
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except AttributeError:
    pass

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    """Configure logging (rotating file + console).

    Called only by the entrypoint (``main``), **not on import** — that way importing the
    module in tests does not redirect the suite's log into ``logs/api.log``. With a file,
    the API log survives closing the window / running as a service.
    """
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
    os.makedirs(log_dir, exist_ok=True)
    file_handler = TimedRotatingFileHandler(
        os.path.join(log_dir, 'api.log'),
        when='midnight', interval=1, backupCount=6, encoding='utf-8',
    )
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[file_handler, logging.StreamHandler()],
        force=True,
    )
    logging.getLogger('httpx').setLevel(logging.WARNING)

app = Flask(__name__)
_WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web')

# Serializes the loads: never two concurrent syncs (avoids multiple SAP connections and
# races in replace_nped). Volume is low (on-demand trigger).
_sync_lock = threading.Lock()

# ── Rate limit on WRITES (anti-loop guard) ────────────────────────────────────────
# In-process sliding window per "bucket". GENEROUS on purpose: it catches an agent
# runaway/loop without getting in the way of normal use (a person's ordinary use stays
# far below). Configurable by env: RATE_SYNC_OS_MAX (OS syncs/min) and
# RATE_FORCE_OPORT_MAX (full loads/min).
_RATE_WINDOW_S = 60.0
_RATE_SYNC_OS_MAX = int(os.getenv('RATE_SYNC_OS_MAX', '60'))
_RATE_FORCE_OPORT_MAX = int(os.getenv('RATE_FORCE_OPORT_MAX', '6'))

# Cap on pedidos per request in POST /sync/ordens-servico. The batch runs SERIALIZED
# inside _sync_lock (2 HANA connections per pedido), so a huge list would become an
# hours-long request holding the whole queue. 50 is roomy for real use (the screen offers
# up to 30 in "Buscar na Lista") and still caps a request at ~a few minutes.
_SYNC_LOTE_MAX = int(os.getenv('SYNC_LOTE_MAX', '50'))


class _RateLimiter:
    """Thread-safe sliding window: counts calls per 'bucket' and says if the limit is past."""

    def __init__(self) -> None:
        self._hits: dict = {}
        self._lock = threading.Lock()

    def check(self, bucket: str, limite: int, janela_s: float) -> Tuple[bool, float]:
        """Record a hit; returns ``(allowed, seconds_until_release)``."""
        agora = time.monotonic()
        corte = agora - janela_s
        with self._lock:
            hits = [t for t in self._hits.get(bucket, ()) if t > corte]
            if len(hits) >= limite:
                self._hits[bucket] = hits
                return False, max(0.0, janela_s - (agora - hits[0]))
            hits.append(agora)
            self._hits[bucket] = hits
            return True, 0.0

    def reset(self) -> None:
        """Clear the state (used by the tests)."""
        with self._lock:
            self._hits.clear()


_rate_limiter = _RateLimiter()


def _checar_rate(bucket: str, limite: int):
    """If the rate limit is blown, returns ``(response_429, 429)`` to return; else ``None``."""
    permitido, retry = _rate_limiter.check(bucket, limite, _RATE_WINDOW_S)
    if permitido:
        return None
    espera = int(retry) + 1
    resp = jsonify(
        ok=False, error='rate_limited', retry_after_s=espera,
        motivo=(f'Trava anti-loop: muitas escritas em menos de {int(_RATE_WINDOW_S)}s '
                f'(limite {limite}). Aguarde ~{espera}s e tente de novo.'),
    )
    resp.headers['Retry-After'] = str(espera)
    logger.warning("Rate-limit '%s' estourado (limite %s/%ss)", bucket, limite, int(_RATE_WINDOW_S))
    return resp, 429


# Supabase client (service_role) for reading the log — created on demand and reused.
_supabase_client = None


def _supabase():
    global _supabase_client
    if _supabase_client is None:
        from supabase import create_client
        from supabase.client import ClientOptions
        s = get_settings()
        _supabase_client = create_client(
            s.supabase_url, s.supabase_write_key,
            ClientOptions(postgrest_client_timeout=s.supabase_timeout_s),
        )
    return _supabase_client


def _fetch_log(table: str, limit: int) -> List[dict]:
    """The last ``limit`` syncs (newest first) from table ``table``."""
    res = (
        _supabase().table(table)
        .select('*').order('id', desc=True).limit(limit).execute()
    )
    return res.data or []


def _clear_log(table: str) -> int:
    """Delete every record from the log table ``table``. Returns how many were removed."""
    # PostgREST requires a filter on delete; 'id <> 0' matches every row (id starts at 1).
    res = _supabase().table(table).delete().neq('id', 0).execute()
    return len(res.data or [])


def _count_rows(table: str) -> Optional[int]:
    """Total rows in table ``table`` (via PostgREST's exact count)."""
    res = _supabase().table(table).select('id', count='exact').limit(1).execute()
    return res.count


# OS Status translation. The VW_OS_INTEGRACAO view carries the raw code (P/R/L/C); the
# old status_ordens_servico_eng lookup table was retired in the consolidation.
_OS_STATUS_DESC = {'P': 'Planejado', 'R': 'Liberado', 'L': 'Encerrado', 'C': 'Cancelado'}

# Columns for the OS detail/summary — deliberately lean (the VW_OS_INTEGRACAO view has
# 54 columns; we pull only what the summary uses). Includes the EXPEDIÇÃO fields
# (ObsPedido/DtLiber/DtEntregaPED) that used to require a 2nd query on the separate mirror.
_OS_DETALHE_COLS = (
    'id,N_PED,N_OP,DescItemPED,DescItemEstrut,DtPedido,'
    'CodClien,NomeClien,Status,TotalOrcam,ObsPedido,DtLiber,DtEntregaPED,'
    'Solda,Pintura,Almox,Exped,id_execucao,data_hora_extracao'
)

# PROCESS flags (columns 50-53 of the view): 1 = the item goes through the process,
# 0 = it does not. These 4 columns replace the 4 TABLES dropped in the 07-14
# consolidation (vw_os_solda/vw_os_pintura_v0/vw_os_almox_impressao/
# vw_os_exped_impressao_v2) — the process used to be identified by WHICH TABLE the row
# showed up in.
_OS_PROCESSOS = ('Solda', 'Pintura', 'Almox', 'Exped')


def _flag_ligada(valor: object) -> bool:
    """True if the row's process flag is on (1).

    The view returns an integer (1/0), but we tolerate text/decimal/None — an unexpected
    value must never take the summary down, it just does not count.
    """
    try:
        return int(valor) == 1
    except (TypeError, ValueError):
        return False


def _resumo_processos(linhas: List[dict]) -> dict:
    """Aggregate the process flags: ``{process: {'tem': bool, 'linhas': int}}``.

    The flags are PER ITEM — a pedido normally has mixed items (some go to welding, some
    do not), so a header-level boolean would be misleading. We give both answers: *does it
    go through the process?* and *how many items*.
    """
    processos = {}
    for proc in _OS_PROCESSOS:
        n = sum(1 for linha in linhas if _flag_ligada(linha.get(proc)))
        processos[proc.lower()] = {'tem': n > 0, 'linhas': n}
    return processos


def _fetch_os_detalhe(nped: int) -> List[dict]:
    """Rows (lean columns) of an N_PED's OS, ordered by id. Empty if there is no OS."""
    res = (
        _supabase().table(get_settings().os_table_name)
        .select(_OS_DETALHE_COLS).eq('N_PED', nped).order('id').execute()
    )
    return res.data or []


def _soma_total_orcamento(linhas: List[dict]) -> Optional[float]:
    """Sum the rows' ``TotalOrcam`` (goods value, taxes excluded).

    ``TotalOrcam`` is PER ROW in the view (350 distinct values on a real pedido), not a
    repeated header — taking ``linhas[0]`` returned a random item's value (row order is
    not stable across loads). Incident 2026-07-06: pedido 84080 showed "96.78" for a quote
    of ~R$ 3.05M.
    """
    total = 0.0
    achou = False
    for linha in linhas:
        valor = linha.get('TotalOrcam')
        if valor is None:
            continue
        try:
            total += float(valor)
        except (TypeError, ValueError):
            continue
        achou = True
    return round(total, 2) if achou else None


def _resumo_os(linhas: List[dict]) -> dict:
    """Summary of the pedido from rows already read (no extra query).

    The VW_OS_INTEGRACAO view is denormalized per item: the HEADER fields (customer,
    status, dates) repeat on every row — we take them from the first. The OPs (``N_OP``)
    are aggregated and ``total_orcamento`` is the SUM of the rows (see
    ``_soma_total_orcamento``).

    The EXPEDIÇÃO fields (``data_entrega``, ``data_liberacao``, ``obs``) now come from the
    SAME row (they used to come from a separate mirror). ``obs`` = ``ObsPedido`` (the
    PEDIDO's note; the view also has ``Obs``, from the OP, which is NOT the one wanted
    here). ``exped_disponivel`` is hardcoded ``True`` for compatibility with the web app —
    there is no separate mirror left that could be missing.

    The process flags (Solda/Pintura/Almox/Exped) are PER ITEM, not per pedido — they go
    aggregated into ``processos`` (see ``_resumo_processos``), not as header booleans,
    which would be misleading on a pedido with mixed items.
    """
    primeira = linhas[0]
    status = primeira.get('Status')
    ops = sorted({l['N_OP'] for l in linhas if l.get('N_OP') is not None})
    return {
        'cliente': primeira.get('NomeClien'),
        'cod_cliente': primeira.get('CodClien'),
        'descricao': primeira.get('DescItemPED'),
        'data_pedido': primeira.get('DtPedido'),
        'status': status,
        'status_desc': _OS_STATUS_DESC.get(status, status),
        'total_orcamento': _soma_total_orcamento(linhas),
        'num_linhas': len(linhas),
        'num_ops': len(ops),
        'ops': ops,
        'data_entrega': primeira.get('DtEntregaPED'),
        'data_liberacao': primeira.get('DtLiber'),
        'obs': primeira.get('ObsPedido'),
        'exped_disponivel': True,
        'processos': _resumo_processos(linhas),
        'id_execucao': primeira.get('id_execucao'),
        'ultima_sincronizacao': primeira.get('data_hora_extracao'),
    }


def _autorizado() -> bool:
    """True if ``OS_API_KEY`` is unset (open) or if the key matches.

    Accepts the key via: the ``X-API-Key`` header, ``Authorization: Bearer <key>``, or the
    query string ``?key=`` / ``?api_key=`` (the query param allows testing from a browser,
    which sends no headers; with the caveat that the key then appears in the URL/browser
    history).

    The comparison uses ``compare_digest`` (constant time): ``==`` short-circuits on the
    1st differing byte, and the response time leaks how many bytes the guess got right —
    enough to recover the key byte by byte. Made worse by accepting the key on the query
    string, so the attack is a plain GET in a loop, with no rate limit on reads.
    ``mcp/serve_http.py`` already did this right; the API did not.
    """
    chave = get_settings().os_api_key
    if not chave:
        return True
    enviado = request.headers.get('X-API-Key')
    if not enviado:
        auth = request.headers.get('Authorization', '')
        if auth.startswith('Bearer '):
            enviado = auth[len('Bearer '):]
    if not enviado:
        enviado = request.args.get('key') or request.args.get('api_key')
    if not enviado:
        return False   # compare_digest does not accept None
    # encode: compare_digest requires bytes or an ASCII-only str — a key with an accent
    # would raise TypeError and turn into a 500 instead of a 401.
    return hmac.compare_digest(enviado.encode('utf-8'), chave.encode('utf-8'))


def requer_chave(fn):
    """Require ``X-API-Key`` on the route (see ``_autorizado``); missing/wrong → **401**.

    Replaces the ``if not _autorizado(): return 401`` that was pasted into 11 routes. The
    gain is not lines: it kills the **"new route without a guard"** bug class — the old
    pattern relied on remembering, and the repo keeps growing. Now forgetting the
    decorator leaves the route visibly without it, instead of looking like all the others.

    Deliberately WITHOUT it (see CLAUDE.md): ``/``, ``/favicon.ico``, ``/health`` and
    ``/status`` — monitoring and browser use.

    Order matters: ``@app.get(...)`` **on top**, ``@requer_chave`` right below — otherwise
    Flask registers the wrapper as the endpoint and the guard never runs on the request.
    """
    @wraps(fn)   # without this, Flask uses the wrapper's name as the endpoint and collides
    def _wrapper(*args, **kwargs):
        if not _autorizado():
            return jsonify(ok=False, error='unauthorized'), 401
        return fn(*args, **kwargs)
    return _wrapper


def _sync_one(nped: int) -> dict:
    """Sync one NPED. First it diagnoses via OWOR + ORDR: if there is no OS yet
    (telling an open, cancelled or non-existent pedido apart), or if the OS is cancelled,
    it returns a notice **without** attempting the sync (no failure log is written).

    The responses' ``motivo`` are deliberately accent-free — readable on any
    terminal/console without depending on JSON's ``\\uXXXX`` escaping.
    """
    try:
        diag = diagnosticar_nped(nped)
    except Exception as exc:
        logger.error("Erro ao diagnosticar NPED %s: %s", nped, exc)
        diag = {'erro': str(exc)}

    status_pedido = diag.get('pedido_status')
    if diag.get('tem_os') is False:
        base = {'nped': nped, 'ok': False, 'status_pedido': status_pedido}
        if diag.get('pedido_existe') is False:
            return {**base, 'tipo': 'pedido_nao_encontrado',
                    'motivo': 'Pedido nao encontrado no SAP.'}
        if diag.get('pedido_cancelado'):
            return {**base, 'tipo': 'pedido_cancelado',
                    'motivo': 'Pedido cancelado no SAP - nao ha OS a sincronizar.'}
        sufixo = f' (pedido {status_pedido.lower()})' if status_pedido else ''
        return {**base, 'tipo': 'sem_os',
                'motivo': f'OS ainda nao gerada para este pedido{sufixo}.'}
    if diag.get('cancelada'):
        return {'nped': nped, 'ok': False, 'tipo': 'cancelada',
                'status_pedido': status_pedido,
                'motivo': 'A OS deste pedido esta cancelada.'}

    # OS exists (or could not be diagnosed) → try to sync
    try:
        ok = bool(sync_os(nped))
    except FileLockTimeout:
        # Another PROCESS (e.g. the CLI) is syncing this same pedido. Not an error:
        # nothing was changed and the other one finishes the load. 'ocupado' → 409, like
        # the oportunidades sibling — telling this apart from 'erro' avoids hunting a
        # problem that does not exist.
        logger.warning("NPED %s já está sendo sincronizado por outro processo.", nped)
        return {'nped': nped, 'ok': False, 'tipo': 'ocupado', 'status_pedido': status_pedido,
                'motivo': 'Este pedido ja esta sendo sincronizado por outro processo.'}
    except Exception as exc:  # never let the request blow up as a silent 500
        logger.error("Erro ao sincronizar NPED %s: %s", nped, exc)
        ok = False

    if ok:
        # Single load: the VW_OS_INTEGRACAO view already brings OS + tree/structure +
        # quote in one table — there are no more WBC tree or print-view sub-syncs.
        return {'nped': nped, 'ok': True, 'tipo': None, 'motivo': None,
                'status_pedido': status_pedido}
    return {'nped': nped, 'ok': False, 'tipo': 'erro', 'status_pedido': status_pedido,
            'motivo': 'Nao foi possivel sincronizar.'}


def _sincronizar(npeds: List[int]) -> Tuple[Any, int]:
    """Sync the NPEDs (serialized) and return ``(json, http_status)``."""
    resultados = []
    with _sync_lock:
        for n in npeds:
            resultados.append(_sync_one(n))

    sucesso = sum(1 for r in resultados if r['ok'])
    total = len(resultados)
    payload = {
        'ok': sucesso == total,
        'results': resultados,
        'summary': {'total': total, 'sucesso': sucesso, 'falha': total - sucesso},
    }
    http = 200 if sucesso == total else 207  # 207 = some did not sync (partial)
    return jsonify(payload), http


@app.get('/')
def ui():
    """Friendly page (pedido field + key + Sincronizar button)."""
    return send_from_directory(_WEB_DIR, 'sincronizar.html')


@app.get('/favicon.ico')
def favicon():
    return ('', 204)  # avoids a noisy 404 in the log


@app.get('/health')
def health():
    """Light liveness (is the API up?). No key, no external check — fast and always
    available. For the deep diagnosis, use ``/status``."""
    return jsonify(status='ok', service='ordens-servico-engenharia')


# aliases accepted in ?checks= → canonical check name
_CHECK_ALIASES = {
    'sql': 'sql_server', 'sqlserver': 'sql_server', 'wbc': 'sql_server',
    'hana': 'sap', 'agendador': 'scheduler', 'sched': 'scheduler',
    'task': 'scheduled_task', 'tarefa': 'scheduled_task', 'wbc_task': 'scheduled_task',
    'wu': 'windows_update', 'windowsupdate': 'windows_update', 'update': 'windows_update',
    'updates': 'windows_update', 'patch': 'windows_update', 'reboot': 'windows_update',
}


@app.get('/status')
def status_detalhado():
    """**On-demand** diagnosis: SAP, SQL Server (WBC), Supabase (with latency), scheduler
    signal and system (CPU/memory/disk/IP/uptime).

    **Open** (no key) — meant for monitoring and for opening straight in a browser. Runs
    only when called (no polling). Parameters:
    - ``?checks=sap,sql`` — runs only the listed checks (sap, sql/sql_server, supabase,
      scheduler/agendador, scheduled_task/tarefa, windows_update/update/reboot). Omitted =
      all of them. ``system`` always comes. An invalid name → **400** with the list of what
      is accepted (see ``collect_status``: a typo used to return ``healthy: true`` without
      checking anything).
    - ``?strict=1`` — returns **HTTP 503** if any connection fails **or** there are alerts
      (low disk, scheduler possibly stopped, pending reboot). Useful for monitors that key
      off the status code.
    """
    raw = request.args.get('checks')
    only = None
    if raw:
        only = {_CHECK_ALIASES.get(c.strip().lower(), c.strip().lower())
                for c in raw.split(',') if c.strip()}

    try:
        data = collect_status(only)
    except ValueError as exc:
        # Invalid check name. 400 BEFORE the generic 500: it is a client error, and
        # answering with what is accepted saves the next blind attempt.
        aceitos = sorted(set(SELECTABLE_CHECKS) | set(_CHECK_ALIASES))
        return jsonify(ok=False, error=str(exc), aceitos=aceitos), 400
    except Exception as exc:
        logger.error("Erro ao coletar status: %s", exc)
        return jsonify(ok=False, error='falha ao coletar status'), 500

    strict = request.args.get('strict') in ('1', 'true', 'yes')
    degraded = (not data['ok']) or bool(data.get('alerts'))
    return jsonify(data), (503 if strict and degraded else 200)


@app.get('/historico')
@requer_chave
def historico():
    """Latest syncs (reads the log table). Requires X-API-Key."""
    try:
        limit = int(request.args.get('limit', 20))
    except (TypeError, ValueError):
        limit = 20
    limit = max(1, min(limit, 100))
    try:
        itens = _fetch_log(get_settings().os_sync_log_table, limit)
    except Exception as exc:
        logger.error("Erro ao ler histórico: %s", exc)
        return jsonify(ok=False, error='falha ao ler o historico'), 502
    return jsonify(ok=True, items=itens)


@app.delete('/historico')
@requer_chave
def historico_limpar():
    """Clear the OS history (empties the log table). Requires X-API-Key."""
    try:
        removidos = _clear_log(get_settings().os_sync_log_table)
    except Exception as exc:
        logger.error("Erro ao limpar histórico: %s", exc)
        return jsonify(ok=False, error='falha ao limpar o historico'), 502
    return jsonify(ok=True, removed=removidos)


@app.get('/ordens-servico/disponiveis')
@requer_chave
def os_disponiveis():
    """List up to 30 pedidos with an OS created in SAP (NPED + customer + date).
    Requires X-API-Key.

    Feeds the panel's "Buscar na Lista" button: the user picks pedidos without having to
    type the NPEDs.
    """
    limit = _limit_arg(default=30, maximo=50)
    try:
        pedidos = listar_pedidos_com_os(limit)
    except Exception as exc:
        logger.error("Erro ao listar pedidos com OS: %s", exc)
        return jsonify(ok=False, error='falha ao listar a lista de pedidos'), 502
    if pedidos is None:
        return jsonify(ok=False, error='SAP indisponivel'), 502
    return jsonify(ok=True, items=pedidos)


@app.get('/ordens-servico/<nped>')
@requer_chave
def os_detalhe(nped: str):
    """Detail of ONE pedido's OS (reads the single Supabase table). Requires X-API-Key.

    Returns a ``resumo`` (customer, status, total, row and OP counts, last sync, delivery
    and release dates, and the pedido's note — all from the same ``vw_os_integracao``
    table). With ``?linhas=1`` it also includes the ``linhas`` (lean columns). Responds
    **404** if the pedido has no synced OS.

    Note: the static route ``/ordens-servico/disponiveis`` has priority in Werkzeug's
    router, so it is not captured by this ``<nped>``.
    """
    try:
        n = coerce_positive_int(nped, what='NPED')
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400
    try:
        linhas = _fetch_os_detalhe(n)
    except Exception as exc:
        logger.error("Erro ao ler a OS do NPED %s: %s", n, exc)
        return jsonify(ok=False, error='falha ao ler a OS'), 502
    if not linhas:
        return jsonify(ok=False, error='pedido sem OS sincronizada', nped=n), 404
    payload = {'ok': True, 'nped': n, 'resumo': _resumo_os(linhas)}
    if request.args.get('linhas') in ('1', 'true', 'yes'):
        payload['linhas'] = linhas
    return jsonify(payload)


@app.post('/ordens-servico/<nped>/sincronizar')
@requer_chave
def os_sincronizar(nped: str):
    """Sync (SAP → Supabase) ONE pedido's OS and return the resulting ``resumo``.
    Requires X-API-Key. The **write pair** of ``GET /ordens-servico/<nped>``.

    Reuses ``_sync_one`` (serialized under ``_sync_lock``): it diagnoses OWOR + ORDR first
    — if the pedido **has no OS generated** (types ``sem_os``, ``pedido_cancelado``,
    ``pedido_nao_encontrado``, according to the pedido's status in ORDR) or the OS is
    **cancelled**, it returns the notice **without syncing**. Responses include
    ``status_pedido`` (Aberto/Cancelado/Fechado). It is idempotent (``replace_nped``
    replaces, does not duplicate) — the single load already brings OS + tree + quote. On
    success it re-reads the table and includes a fresh ``resumo`` (customer, status,
    row/OP counts, last sync).

    Status: ``200`` (synced **or** a business notice sem_os/cancelada) · ``502`` (sync
    failure) · ``400`` invalid NPED · ``401`` missing/bad X-API-Key.
    """
    try:
        n = coerce_positive_int(nped, what='NPED')
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400

    limitado = _checar_rate('sync_os', _RATE_SYNC_OS_MAX)
    if limitado is not None:
        return limitado

    with _sync_lock:
        resultado = _sync_one(n)

    payload = {'ok': bool(resultado.get('ok')), 'nped': n, 'resultado': resultado}
    if resultado.get('ok'):
        try:
            linhas = _fetch_os_detalhe(n)
            if linhas:
                # The fresh summary already carries dates/obs — it all comes from the
                # single table.
                payload['resumo'] = _resumo_os(linhas)
        except Exception as exc:  # the sync happened; we just could not re-read the summary
            logger.error("Sync OK mas falha ao reler o resumo do NPED %s: %s", n, exc)

    # business notices (sem_os / cancelada / pedido_cancelado / pedido_nao_encontrado)
    # respond 200; 'ocupado' = another process is already syncing this pedido (409, same
    # semantics as the oportunidades sibling); 'erro' = a real sync failure (502).
    http = {'erro': 502, 'ocupado': 409}.get(resultado.get('tipo'), 200)
    return jsonify(payload), http


# ===================== Oportunidades (scheduled pipeline) =====================

def _limit_arg(default: int = 20, maximo: int = 100) -> int:
    try:
        limit = int(request.args.get('limit', default))
    except (TypeError, ValueError):
        limit = default
    return max(1, min(limit, maximo))


@app.get('/oportunidades/historico')
@requer_chave
def oport_historico():
    """Latest oportunidades syncs (reads sincronizacao_log). Requires X-API-Key."""
    try:
        itens = _fetch_log(get_settings().sync_log_table_name, _limit_arg())
    except Exception as exc:
        logger.error("Erro ao ler histórico de oportunidades: %s", exc)
        return jsonify(ok=False, error='falha ao ler o historico'), 502
    return jsonify(ok=True, items=itens)


@app.delete('/oportunidades/historico')
@requer_chave
def oport_historico_limpar():
    """Clear the oportunidades log. Requires X-API-Key."""
    try:
        removidos = _clear_log(get_settings().sync_log_table_name)
    except Exception as exc:
        logger.error("Erro ao limpar histórico de oportunidades: %s", exc)
        return jsonify(ok=False, error='falha ao limpar o historico'), 502
    return jsonify(ok=True, removed=removidos)


@app.get('/oportunidades/info')
@requer_chave
def oport_info():
    """Context for the oportunidades pipeline: total rows + schedule. Requires X-API-Key."""
    s = get_settings()
    total = None
    try:
        total = _count_rows(s.table_name)
    except Exception as exc:
        logger.error("Erro ao contar oportunidades: %s", exc)
    return jsonify(
        ok=True,
        total=total,
        intervalo_minutos=s.intervalo_minutos,
        janela_horas=s.janela_horas,
    )


@app.post('/oportunidades/sincronizar')
@requer_chave
def oport_sincronizar():
    """Force the FULL oportunidades load (the scheduler's own). Requires X-API-Key.

    Uses a cross-process file lock: if the scheduler (or another trigger) is already
    running, it responds 409 instead of running two snapshot loads at once.
    """
    limitado = _checar_rate('force_oport', _RATE_FORCE_OPORT_MAX)
    if limitado is not None:
        return limitado

    try:
        with oportunidades_sync_lock(timeout=0):
            ok = bool(sync_oportunidades())
    except FileLockTimeout:
        return jsonify(ok=False, tipo='ocupado',
                       motivo='Ja ha uma sincronizacao de oportunidades em andamento.'), 409
    except Exception as exc:
        logger.error("Erro ao sincronizar oportunidades: %s", exc)
        return jsonify(ok=False, tipo='erro', motivo='Nao foi possivel sincronizar.'), 502
    if ok:
        return jsonify(ok=True)
    # 502 like the except above: it is the SAME class of problem (the load did not
    # happen). Without the status code, Flask returned 200 and any monitor that decides by
    # code — the norm — read the failure as a success.
    return jsonify(ok=False, tipo='erro',
                   motivo='Nao foi possivel sincronizar (0 registros?).'), 502


@app.post('/sync/ordens-servico/<nped>')
@requer_chave
def sync_um(nped: str):
    """Sync **one** pedido. Requires X-API-Key. Same anti-loop guard as its pair
    ``/ordens-servico/<nped>/sincronizar`` (bucket ``sync_os``)."""
    try:
        n = coerce_positive_int(nped, what='NPED')
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400

    limitado = _checar_rate('sync_os', _RATE_SYNC_OS_MAX)
    if limitado is not None:
        return limitado
    return _sincronizar([n])


@app.post('/sync/ordens-servico')
@requer_chave
def sync_varios():
    """Sync several pedidos: ``{"nped": N}`` or ``{"npeds": [...]}``. Requires X-API-Key.

    Limits: at most ``SYNC_LOTE_MAX`` pedidos per request, plus the anti-loop guard
    (bucket ``sync_os``) — see ``_SYNC_LOTE_MAX``.
    """
    body = request.get_json(silent=True) or {}
    bruto = body.get('npeds')
    if bruto is None and body.get('nped') is not None:
        bruto = [body['nped']]
    if not bruto:
        return jsonify(ok=False, error="informe 'nped' (int) ou 'npeds' (lista)"), 400
    if not isinstance(bruto, list):
        bruto = [bruto]
    if len(bruto) > _SYNC_LOTE_MAX:
        # Without the cap, `{"npeds": [1..5000]}` was 1 request holding _sync_lock for
        # HOURS (2 HANA connections per pedido, all inside the lock): no other sync could
        # get in and every attempt burned a waitress thread waiting → the pool drains and
        # the whole API stops responding, /health included.
        return jsonify(
            ok=False, error=f'lote grande demais: {len(bruto)} pedidos (max {_SYNC_LOTE_MAX})',
            motivo='Divida em requests menores — o lote roda serializado e segura a fila.',
        ), 413
    try:
        npeds = [coerce_positive_int(n, what='NPED') for n in bruto]
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400

    limitado = _checar_rate('sync_os', _RATE_SYNC_OS_MAX)
    if limitado is not None:
        return limitado
    return _sincronizar(npeds)


def main() -> None:
    """Start the server (waitress in production; Flask dev as fallback)."""
    _configure_logging()
    s = get_settings()
    if not s.os_api_key:
        logger.warning(
            "OS_API_KEY não definido — endpoint SEM autenticação "
            "(ok p/ rede interna/dev; defina OS_API_KEY em produção)."
        )
    # The update search costs 3.1s here (measured; 30s cold) and would blow the timeout of
    # whoever calls /status — hence it runs on a daemon thread, off the request path.
    # Here in the entrypoint and NOT on import: otherwise the test suite would fire
    # PowerShell.
    windows_update.iniciar_coletor(s)
    host, port = s.os_api_host, s.os_api_port
    try:
        from waitress import serve
        logger.info("Servindo via waitress em http://%s:%s", host, port)
        serve(app, host=host, port=port)
    except ImportError:
        logger.warning("waitress não instalado — usando o servidor de DEV do Flask.")
        app.run(host=host, port=port)


if __name__ == '__main__':
    main()
