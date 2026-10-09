"""HTTP API of the ServidorIntegracaoSAP (port 8077): on-demand triggers + read routes.

Designed to be called **by the app** (web/desktop) when a user asks to sync a pedido.
Writing remains the backend's job (service_role); this service only exposes an HTTP
trigger.

Endpoints
---------
- ``GET  /``                                → entrance: sends the browser to the WBC painel
                                              (``web/entrada.html``; falls back to ``/sincronizar``
                                              when the painel does not answer)
- ``GET  /sincronizar``                     → the Painel de Sincronização (OS · Oportunidades),
                                              in the shared shell (casa/); behind the shared login
- ``GET  /inicio``                          → the Central's home page: state of the three screens
- ``GET|POST /entrar`` · ``POST /sair``     → the key prompt (same cookie as the other screens)
- ``GET  /orcaview``                        → 302 to the OrçaView home (``ORCAVIEW_URL``)
- ``GET  /health``                          → ``{"status": "ok"}``
- ``GET  /status``                          → diagnosis (two levels, see below); ``?checks=``, ``?strict=1``
- ``GET|DELETE /historico``                 → OS sync log (read / clear)
- ``GET  /usuarios-ativos``                 → active app profiles (cached 10 min)
- ``GET  /ordens-servico/disponiveis``      → up to 30 pedidos with an OS created in SAP
- ``GET  /ordens-servico/<nped>``           → detail (summary) of a pedido's OS
- ``POST /ordens-servico/<nped>/sincronizar`` → syncs + returns the summary (GET's pair)
- ``POST /sync/ordens-servico/<nped>``      → syncs **one** pedido
- ``POST /sync/ordens-servico``             → body ``{"nped": N}`` or ``{"npeds": [...]}``
- ``GET|DELETE /oportunidades/historico``   → oportunidades load log (read / clear)
- ``GET  /oportunidades/info``              → oportunidades context: total rows + schedule
- ``POST /oportunidades/sincronizar``       → full oportunidades load (409 if one is running)
- ``POST /vendas-bi/sincronizar``           → Vendas BI aggregates (409 if one is running)
- ``GET  /ordens-producao/<numero>``        → one Production Order (status + transitions)
- ``POST /ordens-producao/<numero>/status`` → **writes into SAP**: Liberada / Encerrada
- ``GET  /pedidos/situacao``                → order-status cut (blocked / everything)
- ``GET  /pedidos/<numero>/situacao``       → status of ONE order
- ``GET  /rh/colaboradores``                → Kairos roster mirror (company → sector → people)
- ``GET  /painel-wbc``                      → 302 to the WBC integration painel (FastAPI, PAINEL_PORTA)
- ``GET  /controle-producao[/<tela>]``      → 302 to the Controle de Produção screen (FastAPI, CP_PORTA)

Authentication (optional, **recommended in production**)
--------------------------------------------------------
Set ``OS_API_KEY`` in ``.env``. The client must send the ``X-API-Key: <key>`` header
(or ``Authorization: Bearer <key>``). Without ``OS_API_KEY`` the endpoint is **open**
(use only on a trusted internal network / development). A browser on the Sincronização
page uses instead the shared login cookie of the .11 screens (``casa/acesso.py``); a write
by cookie must come from this same origin (CSRF).

``GET /status`` is the one route with **two** levels. It stays open — the .90's watchdog
polls it with no credential and decides by the HTTP status code — but a caller without a
credential now gets a **minimal** view (``ok``, ``healthy``, one boolean per check, the
alert **count**, ``restrito: true``). The full payload needs either the ``OS_API_KEY`` or
the low-privilege ``STATUS_ID``, which is the one handed to the other team and to the
OrçaView: it opens the diagnosis and **nothing else** — on every other route it is a 401.
Reason: the full payload publishes the HANA and SQL Server ``host:port``, the Supabase
URL, hostname/IP/OS/Python, the install path and the patch level — a map of the
integration for anyone on the LAN.

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
import json
import logging
import os
import re
import sys
import threading
import time
from functools import wraps
from logging.handlers import TimedRotatingFileHandler
from typing import Any
from urllib.parse import quote, urlsplit

from flask import Flask, Response, g, jsonify, redirect, request, send_from_directory
from jinja2 import Environment, FileSystemLoader, select_autoescape

import casa
import feriados_br
import ordens_producao_sl as op_sl
import situacao_pedidos as sit_ped
import situacao_pedidos_hana as sit_ped_hana
import windows_update
from casa import acesso as casa_acesso
from casa import destinos
from config import get_settings
from extract_ordens_servico_engenharia import (
    consultar_status_pedido,
    diagnosticar_nped,
    listar_pedidos_com_os,
)
from extract_ordens_servico_engenharia import (
    main as sync_os,
)
from extract_sap_to_supabase import main as sync_oportunidades
from extract_vendas_bi import main as sync_vendas_bi
from monitoring import SELECTABLE_CHECKS, AcompanhamentoIndisponivel, collect_status, wbc_orcamento
from operacao import acoes_agente, boots, conexoes, historico_pedido, log_worker, reinicio, ronda_90, versao
from operacao import servicos as operacao_servicos_mod
from pipeline_core import (
    FileLockTimeout,
    coerce_positive_int,
    oportunidades_sync_lock,
    vendas_bi_sync_lock,
)
from seguranca import agente as seguranca_agente
from seguranca import aprovacoes, auditoria, credenciais
from seguranca.credenciais import Cliente

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

# The browser pages of this API (Sincronização, key prompt) wear the shared shell of the .11
# screens, "Central Integração SAP" (casa/, PLANO_CASA_COMUM_11 F3). A Jinja environment of
# their own, not Flask's: the shell's macros are plain Jinja and nothing else here renders.
_PAGINAS = Environment(loader=FileSystemLoader(_WEB_DIR), autoescape=select_autoescape(('html',)))
casa.instalar(_PAGINAS)
# Where each screen of the shared bar lives, seen from this API: itself, and the redirect
# routes below for the other two processes (they resolve host and port from the .env).
_PAGINAS.globals['CASA_HREFS'] = {
    'integracao': '/painel-wbc',
    'pedidos': '/controle-producao/pedidos',
    'ops': '/controle-producao/ops',
    'sincronizacao': '/sincronizar',
    'tarefas': '/controle-producao/tarefas',
}
#: The Controle de Produção screens behind the bar's links: path here → path there.
_TELAS_DO_CONTROLE_DE_PRODUCAO = {'pedidos': 'pedidos-wbc', 'ops': 'manutencao-op', 'tarefas': 'tarefas'}
#: The company the HANA reads of this API point at in production (the pill turns red).
_COMPANY_DE_PRODUCAO = 'SBOALTAMIRAPROD'

# Serializes the loads: never two concurrent syncs (avoids multiple SAP connections and
# races in replace_nped). Volume is low (on-demand trigger).
_sync_lock = threading.Lock()

# Serializes Production Order status writes. Global rather than per-OP because volume is
# low (a person clicking a button) and it also keeps the shared Service Layer session from
# being renewed by two threads at once. Per-OP locking is the upgrade if volume ever grows.
_op_lock = threading.Lock()

# ── Rate limit on WRITES (anti-loop guard) ────────────────────────────────────────
# In-process sliding window per "bucket". GENEROUS on purpose: it catches an agent
# runaway/loop without getting in the way of normal use (a person's ordinary use stays
# far below). Configurable by env: RATE_SYNC_OS_MAX (OS syncs/min) and
# RATE_FORCE_OPORT_MAX (full loads/min).
#: waitress worker threads (its default is 4) — see `main`.
API_THREADS = 8

_RATE_WINDOW_S = 60.0
_RATE_SYNC_OS_MAX = int(os.getenv('RATE_SYNC_OS_MAX', '60'))
_RATE_FORCE_OPORT_MAX = int(os.getenv('RATE_FORCE_OPORT_MAX', '6'))
# Agregados do dashboard Vendas: 4 consultas agregadas e menos de mil linhas de
# upsert, mas ainda são 4 viagens ao HANA — o teto evita o loop de um agente.
_RATE_VENDAS_BI_MAX = int(os.getenv('RATE_VENDAS_BI_MAX', '6'))
# Production Order status writes. TIGHTER than the others on purpose: this is the only
# bucket in front of a write into SAP PRODUCTION, and no legitimate use changes 20 order
# statuses in a minute by hand.
_RATE_OP_STATUS_MAX = int(os.getenv('RATE_OP_STATUS_MAX', '20'))

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

    def check(self, bucket: str, limite: int, janela_s: float) -> tuple[bool, float]:
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

#: Cache da lista de perfis ativos: ``(momento, nomes)``. A lista muda em meses;
#: o painel a pede a cada repintura do cartao. Ver ``usuarios_ativos``.
_usuarios_cache: tuple[float, list[str]] | None = None
USUARIOS_CACHE_S = 600


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


def _fetch_log(table: str, limit: int) -> list[dict]:
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


def _count_rows(table: str) -> int | None:
    """Total rows in table ``table`` (via PostgREST's exact count)."""
    res = _supabase().table(table).select('id', count='exact').limit(1).execute()
    return res.count


# OS Status translation. The VW_OS_INTEGRACAO view carries the raw code (P/R/L/C); the
# old status_ordens_servico_eng lookup table was retired in the consolidation.
_OS_STATUS_DESC = {'P': 'Planejado', 'R': 'Liberado', 'L': 'Encerrado', 'C': 'Cancelado'}

# Columns for the OS detail/summary — deliberately lean (the VW_OS_INTEGRACAO view has
# 56 columns; we pull only what the summary uses). Includes the EXPEDIÇÃO fields
# (ObsPedido/DtLiber/DtEntregaPED) that used to require a 2nd query on the separate mirror.
_OS_DETALHE_COLS = (
    'id,N_PED,N_OP,DescItemPED,DescItemEstrut,DtPedido,'
    'CodClien,NomeClien,Status,TotalOrcam,ObsPedido,DtLiber,DtEntregaPED,'
    'Solda,Pintura,Almox,Exped,Compras,id_execucao,data_hora_extracao'
)

# "Dados Adicionais" of the LINE (view column 6, NVARCHAR(5000)) — deliberately OUT of the
# lean set and only fetched on request. It is PER ITEM and up to 5.000 chars: on a real
# pedido (344 rows) it adds up to ~1,7 MB to a call that usually only wants the summary,
# and the MCP facade would dump all of it into the model's context. Consumers that need it
# read the mirror table directly (the PCP does) or ask for `?linhas=1&adicionais=1`.
_OS_COL_ADICIONAIS = 'U_INO_D_Adicionais'

# PROCESS flags (the view's last columns): 1 = the item goes through the process, 0 = it
# does not. The first four replace the 4 TABLES dropped in the 07-14 consolidation
# (vw_os_solda/vw_os_pintura_v0/vw_os_almox_impressao/vw_os_exped_impressao_v2) — the
# process used to be identified by WHICH TABLE the row showed up in. ``Compras`` was added
# to the view on 08-05 and never had a table of its own.
#
# Adding a name here is a CONTRACT CHANGE: it adds a key to ``resumo.processos``. Growing
# is safe (the block was documented from day one as "always the 4 keys, even zeroed", and
# consumers read by name) — but a name must never be REMOVED or renamed without warning
# whoever reads the 8077.
_OS_PROCESSOS = ('Solda', 'Pintura', 'Almox', 'Exped', 'Compras')


def _flag_ligada(valor: object) -> bool:
    """True if the row's process flag is on (1).

    The view returns an integer (1/0), but we tolerate text/decimal/None — an unexpected
    value must never take the summary down, it just does not count.
    """
    try:
        return int(valor) == 1
    except (TypeError, ValueError):
        return False


def _flag_query(nome: str) -> bool:
    """True if the ``nome`` query param is on (``1``/``true``/``yes``)."""
    return request.args.get(nome) in ('1', 'true', 'yes')


def _resumo_processos(linhas: list[dict]) -> dict:
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


def _fetch_os_detalhe(nped: int, incluir_adicionais: bool = False) -> list[dict]:
    """Rows (lean columns) of an N_PED's OS, ordered by id. Empty if there is no OS.

    ``incluir_adicionais`` adds ``U_INO_D_Adicionais`` (see ``_OS_COL_ADICIONAIS``) to the
    projection — opt-in because it is a per-item field of up to 5.000 chars.
    """
    cols = _OS_DETALHE_COLS
    if incluir_adicionais:
        cols = f'{cols},{_OS_COL_ADICIONAIS}'
    res = (
        _supabase().table(get_settings().os_table_name)
        .select(cols).eq('N_PED', nped).order('id').execute()
    )
    return res.data or []


def _soma_total_orcamento(linhas: list[dict]) -> float | None:
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


def _resumo_os(linhas: list[dict]) -> dict:
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

    The process flags (Solda/Pintura/Almox/Exped/Compras) are PER ITEM, not per pedido — they go
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


def _credencial_enviada() -> str | None:
    """The credential that came with the request, or ``None``.

    Accepted in three places: the ``X-API-Key`` header, ``Authorization: Bearer <key>``,
    or the query string ``?key=`` / ``?api_key=`` (the query param allows testing from a
    browser, which sends no headers; with the caveat that the key then appears in the
    URL/browser history).

    Extracted from ``_autorizado`` so that ``/status`` can check a **second**, weaker
    credential (``STATUS_ID``) in the very same places — which is what lets a client
    switch from the strong key to the weak one by changing the value alone, with no code
    change on their side.
    """
    enviado = request.headers.get('X-API-Key')
    if not enviado:
        auth = request.headers.get('Authorization', '')
        if auth.startswith('Bearer '):
            enviado = auth[len('Bearer '):]
    if not enviado:
        enviado = request.args.get('key') or request.args.get('api_key')
    return enviado or None


def _confere(enviado: str | None, esperado: str | None) -> bool:
    """Constant-time comparison; ``False`` when either side is missing.

    ``compare_digest``: ``==`` short-circuits on the 1st differing byte, and the response
    time leaks how many bytes the guess got right — enough to recover the key byte by
    byte. Made worse by accepting the key on the query string, so the attack is a plain
    GET in a loop, with no rate limit on reads. ``mcp/serve_http.py`` already did this
    right; the API did not.

    ``encode``: ``compare_digest`` requires bytes or an ASCII-only str — a key with an
    accent would raise TypeError and turn into a 500 instead of a 401.
    """
    if not enviado or not esperado:
        return False
    return hmac.compare_digest(enviado.encode('utf-8'), esperado.encode('utf-8'))


def _por_cookie() -> bool:
    """The browser carries the shared login cookie of the .11 screens (``casa/acesso.py``):
    an HMAC of ``OS_API_KEY``, the same one the painel WBC and the Controle de Produção issue
    (owner's decision 6, 01/10/2026 — one login for the three)."""
    chave = get_settings().os_api_key
    cookie = request.cookies.get(casa_acesso.COOKIE_DE_ACESSO, '')
    return bool(chave) and bool(cookie) and casa_acesso.igual(cookie, casa_acesso.token_da_chave(chave))


def _mesma_origem() -> bool:
    """The request comes from a page of THIS host:port (``Origin``, else ``Referer``).

    A cookie rides along on any request the browser makes to this host, including one a
    foreign page fires; a write authenticated only by the cookie must prove it came from
    the Sincronização itself (CSRF). Same rule as the Controle de Produção."""
    origem = request.headers.get('Origin') or request.headers.get('Referer') or ''
    return bool(origem) and urlsplit(origem).netloc == request.host


def _autorizado() -> bool:
    """True if ``OS_API_KEY`` is unset (open), if the key matches, or — for the
    Sincronização page in a browser — if the shared login cookie is valid (a write by
    cookie must also come from this same origin).

    **The ``STATUS_ID`` is deliberately NOT accepted here.** It is a low-privilege
    credential that opens the full ``/status`` and nothing else; letting it through this
    guard would hand the other team the roster, the orders and the SAP write. There is a
    test nailing that ``STATUS_ID`` on ``/rh/colaboradores`` answers **401**.
    """
    chave = get_settings().os_api_key
    if not chave:
        return True
    if _confere(_credencial_enviada(), chave):
        return True
    if _por_cookie():
        return request.method in ('GET', 'HEAD', 'OPTIONS') or _mesma_origem()
    return False


def _status_completo_autorizado() -> bool:
    """Who may see the **whole** ``/status``: the ``OS_API_KEY`` or the ``STATUS_ID``.

    Everyone else gets :func:`_status_publico`. Reusing ``_autorizado()`` keeps a single
    fail-open rule in the service: with no ``OS_API_KEY`` configured the API is open as a
    whole (documented at the top of this file), and the diagnosis follows it rather than
    inventing a second, stricter rule for one route.
    """
    if _autorizado() or _confere(_credencial_enviada(), get_settings().status_id):
        return True
    cliente = _cliente()
    return bool(cliente and cliente.pode('leitura') and not seguranca_agente.recusa(cliente, escrita=False))


def _status_publico(data: dict) -> dict:
    """The **minimal** view of ``/status`` — what goes out with no credential.

    Keeps what serves to MONITOR (is it up? did a check fail? how many alerts?) and drops
    what serves to MAP: the HANA and SQL Server ``host:port`` and the Supabase URL (in
    ``checks.*.detail``), hostname/IP/OS/Python and the disk sizes (``system``), the
    tracking DB path (``wbc_worker.db``), the patch level (``windows_update``) and whether
    the API even requires a key (``api_auth``). ``checks.*.error`` goes too: connection
    errors quote DSNs and user names.

    ``alerts`` comes as a **count**, not the list of texts — the texts carry numbers
    (free GB, minutes stale) and, more importantly, whoever needs to read them now has the
    ``STATUS_ID``. ``restrito: true`` is there so the consumer can tell this shape apart
    from the full one instead of guessing why a key is missing.

    The HTTP status code is computed by the caller **before** this reduction, so
    ``?strict=1`` answers exactly what it answered before for everybody — that is what
    keeps the .90's watchdog (which polls with no credential and decides by the code)
    working untouched.
    """
    checks = {nome: {'ok': bool(c.get('ok'))}
              for nome, c in (data.get('checks') or {}).items()}
    return {
        'ok': data.get('ok'),
        'healthy': data.get('healthy'),
        'service': data.get('service'),
        'timestamp': data.get('timestamp'),
        'uptime_s': data.get('uptime_s'),
        'checks': checks,
        'alerts': len(data.get('alerts') or []),
        'restrito': True,
    }


_METODOS_SEGUROS = ('GET', 'HEAD', 'OPTIONS')


def _cabecalho_da_chave() -> str | None:
    """The key from ``X-API-Key`` or ``Authorization: Bearer`` — never the query string."""
    enviado = request.headers.get('X-API-Key')
    if not enviado:
        auth = request.headers.get('Authorization', '')
        if auth.startswith('Bearer '):
            enviado = auth[len('Bearer '):]
    return enviado or None


def _cliente() -> Cliente | None:
    """Who is calling (F1 of PLANO_MIRA_AGENTE_11.md (removed 2026-10-06), 02/10/2026), once per request.

    - no ``OS_API_KEY`` configured → open, as documented at the top of this file;
    - the ``OS_API_KEY`` (header or ``?key=``, as before) or the screens' login cookie →
      "chave-mestra"/"tela", with everything — no current client breaks;
    - a key registered with ``python -m seguranca criar`` → that client and its scopes. Only
      in the header: a registered key in a URL would end up in logs and browser history.
    """
    if 'sis_cliente' in g:
        return g.sis_cliente
    mestra = get_settings().os_api_key
    if not mestra:
        cliente = Cliente('aberto', frozenset({'admin'}))
    elif _confere(_credencial_enviada(), mestra):
        cliente = credenciais.chave_mestra()
    elif _por_cookie():
        cliente = Cliente('tela', frozenset({'admin'}))
    else:
        cliente = credenciais.identificar(_cabecalho_da_chave())
    g.sis_cliente = cliente
    return cliente


def requer_chave(escopo: str):
    """Require a credential that holds ``escopo`` on the route — 401 without one, 403 without
    the scope or when an agent rule refuses (``seguranca.agente``).

    Was a plain "the key matches" guard until 02/10/2026; the master key and the screens'
    cookie still pass everywhere (they hold ``admin``). The scope is declared next to the route
    (``@requer_chave('op:status')``): a route without it is visibly unguarded, and a new
    write route cannot be reached by a read-only client by accident.

    Deliberately WITHOUT it (see CLAUDE.md): ``/``, ``/favicon.ico``, ``/health`` and
    ``/status`` — monitoring and browser use; ``/status`` has its own two-level rule.

    Order matters: ``@app.get(...)`` **on top**, ``@requer_chave(...)`` right below — otherwise
    Flask registers the wrapper as the endpoint and the guard never runs on the request.
    """
    if escopo not in credenciais.ESCOPOS:
        raise ValueError(f'escopo desconhecido: {escopo!r}')

    def decorador(fn):
        @wraps(fn)   # without this, Flask uses the wrapper's name as the endpoint and collides
        def _wrapper(*args, **kwargs):
            cliente = _cliente()
            escrita = request.method not in _METODOS_SEGUROS
            if cliente is None or (cliente.nome == 'tela' and escrita and not _mesma_origem()):
                return jsonify(ok=False, error='unauthorized'), 401
            g.sis_escopo = escopo
            if not cliente.pode(escopo):
                return jsonify(ok=False, error='forbidden', tipo='sem_permissao',
                               motivo=f"A credencial '{cliente.nome}' nao tem o escopo '{escopo}'."), 403
            motivo = seguranca_agente.recusa(cliente, escrita=escrita)
            if motivo:
                return jsonify(ok=False, error='forbidden', tipo='agente_bloqueado', motivo=motivo), 403
            return fn(*args, **kwargs)
        _wrapper.escopo = escopo
        return _wrapper
    return decorador


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


def _sincronizar(npeds: list[int]) -> tuple[Any, int]:
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


def _host_sem_porta() -> str:
    return request.host.rsplit(':', 1)[0]


def _url_painel_wbc() -> str:
    """Where the WBC integration painel lives: ``WBC_PAINEL_URL`` (verbatim) or this host
    on ``PAINEL_PORTA`` (the .11 case, all screens on one machine — ``casa/destinos.py``)."""
    s = get_settings()
    return destinos.endereco(s.wbc_painel_url, request.scheme, _host_sem_porta(), s.wbc_painel_porta)


@app.get('/')
def ui():
    """The entrance of the server: the **WBC integration painel is the main screen**
    (Marcelo, 2026-09-08), so the address everybody already uses (``:8077``) lands there.

    Not a bare 302: the painel is another process (``OrcaView-WBC-Painel``), and a redirect
    to a stopped service would leave the visitor on the browser's error page. The tiny
    ``web/entrada.html`` probes the painel first (``fetch`` in ``no-cors`` mode, 3 s) and only
    then replaces the location; when the painel does not answer it shows the service name
    and the button to ``/sincronizar``. The Painel de Sincronização itself moved to
    ``GET /sincronizar`` — its JS uses absolute paths, so nothing else changed.
    """
    url = _url_painel_wbc()
    # The JSON goes into the script as is (`| safe`): it comes from the .env, not from a
    # visitor; the href copies are autoescaped by the template.
    return _pagina('entrada.html', painel_url=url, painel_json=json.dumps(url))


def _ambiente_da_pagina() -> dict:
    """The environment pill of the shared bar. This API READS the SAP (``SAP_SCHEMA``) and
    writes the Supabase, so the pill names the schema and its tooltip says so."""
    schema = get_settings().sap_schema or ''
    producao = schema.upper() == _COMPANY_DE_PRODUCAO
    return {
        'em_producao': producao,
        'company_db': schema or 'SAP_SCHEMA não definido',
        'ambiente': f"{'produção' if producao else 'fora de produção'} — schema SAP '{schema or '?'}'",
        'dica': f'Lê o SAP de produção ({schema}) e grava no Supabase: as cargas são reais',
    }


def _pagina(nome: str, status: int = 200, **contexto: Any) -> Response:
    """A browser page of this API, in the shared shell. ``no-store``: without it Chrome
    keeps serving a stale HTML after a deploy (heuristic cache, seen on the .90)."""
    html = _PAGINAS.get_template(nome).render(
        request=request, ambiente=_ambiente_da_pagina(),
        exige_chave=bool(get_settings().os_api_key), **contexto,
    )
    return Response(html, status=status, mimetype='text/html', headers={'Cache-Control': 'no-store'})


@app.get('/sincronizar')
def sincronizar():
    """Painel de Sincronização (OS by pedido · Oportunidades load). Was ``GET /`` until
    2026-09-08; the root now leads to the WBC painel.

    Since 01/10/2026 (PLANO_CASA_COMUM_11 F3) it opens behind the same login as the other two
    screens: with ``OS_API_KEY`` set and no valid cookie (or key), the key prompt first. The
    page's own calls then ride on that cookie — the key is no longer pasted into the page
    nor kept in ``localStorage``.
    """
    if get_settings().os_api_key and not _autorizado():
        return redirect(f"/entrar?proximo={quote('/sincronizar', safe='')}", code=303)
    return _pagina('sincronizar.html')


@app.get('/inicio')
def inicio():
    """The Central's home page (PLANO_CASA_COMUM_11, F5): one card per screen with the state
    of its service, and the connections below — all read in the browser from ``/status``
    and the sync logs, the same data the monitors use. Reached by the brand of the shared
    bar on the three screens. ``/`` keeps leading to the painel WBC (owner, 2026-09-08)."""
    if get_settings().os_api_key and not _autorizado():
        return redirect(f"/entrar?proximo={quote('/inicio', safe='')}", code=303)
    return _pagina('inicio.html')


@app.get('/entrar')
def entrar():
    """The key prompt of this API's pages — the same key and the same cookie as the painel
    WBC and the Controle de Produção: whoever entered one of them never sees this."""
    if not get_settings().os_api_key:
        return redirect('/sincronizar', code=303)
    return _pagina('entrar.html', erro=None,
                   proximo=casa_acesso.destino_local(request.args.get('proximo', '/sincronizar')))


@app.post('/entrar')
def entrar_conferir():
    chave = get_settings().os_api_key
    if not chave:
        return redirect('/sincronizar', code=303)
    proximo = casa_acesso.destino_local(request.form.get('proximo', '/sincronizar'))
    if not casa_acesso.igual(request.form.get('chave', ''), chave):
        return _pagina('entrar.html', status=401, erro='Chave incorreta.', proximo=proximo)
    resposta = redirect(proximo, code=303)
    resposta.set_cookie(casa_acesso.COOKIE_DE_ACESSO, casa_acesso.token_da_chave(chave),
                        max_age=30 * 24 * 3600, httponly=True, samesite='Lax')
    return resposta


@app.post('/sair')
def sair():
    """Forgets the key in this browser — on the three screens, the cookie is one."""
    resposta = redirect('/entrar', code=303)
    resposta.delete_cookie(casa_acesso.COOKIE_DE_ACESSO)
    return resposta


@app.get('/orcaview')
def orcaview():
    """The "← OrçaView" link of the shared bar (``ORCAVIEW_URL``, the .90 home). Open: it
    only redirects, and someone stuck at the key prompt must still be able to leave."""
    return redirect(get_settings().orcaview_url, code=302)


@app.get('/favicon.ico')
def favicon():
    return ('', 204)  # avoids a noisy 404 in the log


@app.get('/casa/<path:arquivo>')
def casa_estatico(arquivo: str):
    """The shared shell's CSS/JS (``casa/static``) — the very files the painel WBC and the
    Controle de Produção serve. Open: the key prompt wears them too."""
    return send_from_directory(casa.STATIC_DIR, arquivo, max_age=3600)


@app.get('/painel-wbc')
def painel_wbc():
    """302 to the **WBC → SAP integration painel** (``python -m wbcpython dashboard``, a
    separate FastAPI process on ``PAINEL_PORTA``, 8079 by default).

    A redirect instead of a URL baked into ``sincronizar.html``: the ``.env`` decides where
    the painel lives (``WBC_PAINEL_URL``), and with nothing configured it is the same host
    as this request, on ``PAINEL_PORTA`` — the .11 case, where both pages share a machine.
    Open (no key): the painel asks for the same ``OS_API_KEY`` itself.
    """
    return redirect(_url_painel_wbc(), code=302)


def _url_controle_producao() -> str:
    """Where the Controle de Produção screen lives: ``CP_URL`` (verbatim) or this host on
    ``CP_PORTA`` — the same rule as the painel."""
    s = get_settings()
    return destinos.endereco(s.cp_url, request.scheme, _host_sem_porta(), s.cp_porta)


@app.get('/controle-producao')
def controle_producao():
    """302 to the **Controle de Produção** screen (``python -m controleproducao web``, a
    separate FastAPI process on ``CP_PORTA``, 8080 by default): Pedidos WBC → Ordens de
    Produção and Manutenção de OP. Open (no key): the screen asks for the same
    ``OS_API_KEY`` itself — and the painel's cookie already opens it."""
    return redirect(_url_controle_producao(), code=302)


@app.get('/controle-producao/<tela>')
def controle_producao_tela(tela: str):
    """The shared bar's links to one Controle de Produção screen: ``pedidos``, ``ops`` and
    ``tarefas``; anything else lands on its home. Open, like the root redirect."""
    caminho = _TELAS_DO_CONTROLE_DE_PRODUCAO.get(tela, '')
    return redirect(destinos.na_tela(_url_controle_producao(), caminho), code=302)


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
    # Integração WBC → SAP (worker). NOTE: ``wbc`` stays the SQL Server alias — it predates
    # the worker and monitors already use it; renaming it would silently change what they
    # check. The worker gets its own names.
    'worker': 'wbc_worker', 'wbcworker': 'wbc_worker', 'wbc_worker': 'wbc_worker',
    'integracao_wbc': 'wbc_worker', 'integracao': 'wbc_worker',
    # Controle de Produção (the ``OrcaView-ControleProducao`` screen, probed on CP_PORTA).
    'cp': 'controle_producao', 'controleproducao': 'controle_producao',
    'controle_producao': 'controle_producao', 'producao': 'controle_producao',
}


@app.get('/status')
def status_detalhado():
    """**On-demand** diagnosis: SAP, SQL Server (WBC), Supabase (with latency), scheduler
    signal, WBC worker, Controle de Produção (``/health`` on ``CP_PORTA``), Windows Update
    and system (CPU/memory/disk/IP/uptime).

    **Open, in two levels.** With no credential the answer is the **minimal** view
    (:func:`_status_publico`): ``ok``, ``healthy``, one boolean per check, the alert
    **count** and ``restrito: true``. The full payload needs the ``OS_API_KEY`` or the
    low-privilege ``STATUS_ID`` — the one handed to the other team and to the OrçaView,
    which opens this route and **nothing else**.

    The **HTTP status code does not depend on the credential**: ``?strict=1`` answers 503
    for a degraded server either way. That is deliberate — the .90's watchdog polls
    ``?checks=worker&strict=1`` with no credential and decides by the code alone.

    Runs only when called (no polling). Parameters:
    - ``?checks=sap,sql`` — runs only the listed checks (sap, sql/sql_server, supabase,
      scheduler/agendador, scheduled_task/tarefa, windows_update/update/reboot,
      wbc_worker/worker/integracao_wbc, controle_producao/cp/controleproducao/producao).
      Omitted =
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
    # Computed BEFORE the reduction, on the full data: the code is the same for everyone.
    degraded = (not data['ok']) or bool(data.get('alerts'))
    http = 503 if strict and degraded else 200
    if not _status_completo_autorizado():
        data = _status_publico(data)
    else:
        # Which code answers — added here, not in collect_status (a contract between repos).
        data['versao'] = versao.versao()
    return jsonify(data), http


@app.get('/historico')
@requer_chave('leitura')
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
@requer_chave('historico:apagar')
def historico_limpar():
    """Clear the OS history (empties the log table). Requires X-API-Key."""
    try:
        removidos = _clear_log(get_settings().os_sync_log_table)
    except Exception as exc:
        logger.error("Erro ao limpar histórico: %s", exc)
        return jsonify(ok=False, error='falha ao limpar o historico'), 502
    return jsonify(ok=True, removed=removidos)


@app.get('/usuarios-ativos')
@requer_chave('rh')
def usuarios_ativos():
    """Nomes dos perfis ATIVOS do OrcaView (``app_profiles``). Exige X-API-Key.

    Existe para o painel WBC oferecer uma lista em vez de um campo de texto livre
    no "Seu nome" -- o campo que audita quem armou a janela de busca. Texto livre
    virava "j", "joana" e "Joana Silva" no mesmo historico, o que estraga
    justamente a pergunta que o campo existe para responder.

    Mora aqui, e nao no painel, de proposito: o ``wbcpython`` nao conhece o
    Supabase e nao deve passar a conhecer (``wbcpython/dashboard/__init__``). A
    API ja tem o cliente e a credencial; o painel chama por localhost.

    So o nome sai. Email, papel e ``slp_code`` nao servem para preencher um campo
    de auditoria, e uma lista de e-mails da equipe atras de uma chave
    compartilhada e exposicao sem contrapartida.

    O cache existe porque a lista muda em meses e a tela e repintada o tempo
    todo; sem ele, cada recarga do painel custaria uma ida ao Supabase (1,2 s
    medidos na .11 em 14/09/2026).
    """
    global _usuarios_cache
    agora = time.time()
    if _usuarios_cache and agora - _usuarios_cache[0] < USUARIOS_CACHE_S:
        return jsonify(ok=True, items=_usuarios_cache[1], cache_idade_s=int(agora - _usuarios_cache[0]))
    try:
        res = (
            _supabase().table('app_profiles')
            .select('full_name').eq('active', True).order('full_name').execute()
        )
    except Exception as exc:
        logger.error("Erro ao listar perfis ativos: %s", exc)
        # 502 e nao 500: quem falhou foi o Supabase, e o painel trata isto caindo
        # para o historico local dele em vez de mostrar erro na tela.
        return jsonify(ok=False, error='falha ao consultar os perfis'), 502
    nomes = [
        (linha.get('full_name') or '').strip()
        for linha in (res.data or [])
        if (linha.get('full_name') or '').strip()
    ]
    _usuarios_cache = (agora, nomes)
    return jsonify(ok=True, items=nomes, cache_idade_s=0)


@app.get('/ordens-servico/disponiveis')
@requer_chave('leitura')
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
@requer_chave('leitura')
def os_detalhe(nped: str):
    """Detail of ONE pedido's OS (reads the single Supabase table). Requires X-API-Key.

    Returns a ``resumo`` (customer, status, total, row and OP counts, last sync, delivery
    and release dates, and the pedido's note — all from the same ``vw_os_integracao``
    table). With ``?linhas=1`` it also includes the ``linhas`` (lean columns); adding
    ``&adicionais=1`` puts ``U_INO_D_Adicionais`` (Dados Adicionais, per item, up to 5.000
    chars) in each row. Responds **404** if the pedido has no synced OS.

    ``adicionais`` only takes effect together with ``linhas`` — the field is per item and
    has no place in the summary, so asking for it alone would just make the query heavier
    for nothing.

    **The pedido's own status comes from ORDR, live** (``status_pedido`` +
    ``pedido_cancelado`` at the top level). The synced rows never say "cancelled": a
    pedido cancelled in SAP keeps its OS rows, its OPs and ``exped_disponivel: true``, so
    without this the consumer sees a cancelled pedido as a normal one (pedidos
    84282/84305/84314, 2026-09-03). When it IS cancelled the payload also carries
    ``aviso: {tipo: 'pedido_cancelado', ...}``, the same ``tipo`` the sync route uses.
    Best-effort: SAP down → both keys ``null``, the detail still answers.

    Note: the static route ``/ordens-servico/disponiveis`` has priority in Werkzeug's
    router, so it is not captured by this ``<nped>``.
    """
    n = _inteiro_positivo(nped, 'NPED')
    quer_linhas = _flag_query('linhas')
    try:
        linhas = _fetch_os_detalhe(
            n, incluir_adicionais=quer_linhas and _flag_query('adicionais')
        )
    except Exception as exc:
        logger.error("Erro ao ler a OS do NPED %s: %s", n, exc)
        return jsonify(ok=False, error='falha ao ler a OS'), 502
    if not linhas:
        return jsonify(ok=False, error='pedido sem OS sincronizada', nped=n), 404
    payload = {'ok': True, 'nped': n, 'resumo': _resumo_os(linhas)}
    payload.update(_status_pedido_ordr(n))
    if quer_linhas:
        payload['linhas'] = linhas
    return jsonify(payload)


class _ParametroInvalido(Exception):
    """Parâmetro de rota inválido — vira 400 no ``errorhandler`` abaixo."""


def _inteiro_positivo(valor: object, what: str) -> int:
    """``coerce_positive_int`` que responde **400** sozinho (via ``_ParametroInvalido``).

    Troca o ``try/except ValueError → 400`` que se repetia em 7 rotas. A resposta é a mesma
    de antes: ``{"ok": false, "error": "<mensagem>"}``.
    """
    try:
        return coerce_positive_int(valor, what=what)
    except ValueError as exc:
        raise _ParametroInvalido(str(exc)) from exc


@app.errorhandler(_ParametroInvalido)
def _responder_parametro_invalido(exc: _ParametroInvalido):
    return jsonify(ok=False, error=str(exc)), 400


def _status_pedido_ordr(nped: int) -> dict:
    """``status_pedido``/``pedido_cancelado`` (+ ``aviso`` when cancelled) from ORDR.

    Never raises: the OS detail must answer even with SAP down (keys come ``None``).
    """
    try:
        info = consultar_status_pedido(nped)
    except Exception as exc:
        logger.error("Erro ao consultar o status do pedido %s na ORDR: %s", nped, exc)
        info = None
    if info is None:
        return {'status_pedido': None, 'pedido_cancelado': None}
    out = {'status_pedido': info.get('pedido_status'),
           'pedido_cancelado': info.get('pedido_cancelado')}
    if info.get('pedido_cancelado'):
        out['aviso'] = {'tipo': 'pedido_cancelado',
                        'motivo': 'Pedido cancelado no SAP - a OS sincronizada e historico, '
                                  'nao libere nem produza por ela.'}
    return out


@app.post('/ordens-servico/<nped>/sincronizar')
@requer_chave('os:sincronizar')
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
    n = _inteiro_positivo(nped, 'NPED')

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
@requer_chave('leitura')
def oport_historico():
    """Latest oportunidades syncs (reads sincronizacao_log). Requires X-API-Key."""
    try:
        itens = _fetch_log(get_settings().sync_log_table_name, _limit_arg())
    except Exception as exc:
        logger.error("Erro ao ler histórico de oportunidades: %s", exc)
        return jsonify(ok=False, error='falha ao ler o historico'), 502
    return jsonify(ok=True, items=itens)


@app.delete('/oportunidades/historico')
@requer_chave('historico:apagar')
def oport_historico_limpar():
    """Clear the oportunidades log. Requires X-API-Key."""
    try:
        removidos = _clear_log(get_settings().sync_log_table_name)
    except Exception as exc:
        logger.error("Erro ao limpar histórico de oportunidades: %s", exc)
        return jsonify(ok=False, error='falha ao limpar o historico'), 502
    return jsonify(ok=True, removed=removidos)


@app.get('/oportunidades/info')
@requer_chave('leitura')
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


def _disparar_carga(bucket: str, limite: int, trava, carga, *, rotulo: str, ocupado: str, falhou: str):
    """Carga completa disparada por HTTP: rate-limit + lock de arquivo + resposta por código.

    Oportunidades e Vendas BI eram a mesma função escrita duas vezes. Quem chama passa os
    nomes do módulo NA HORA da chamada (os testes os trocam por monkeypatch).

    Status: ``200`` ok · ``409`` outra carga já está rodando (o lock é cross-process: o
    agendador conta) · ``429`` rate-limit · ``502`` a carga falhou ou não carregou nada.
    O 502 da carga "vazia" é o MESMO problema da exceção (a carga não aconteceu): com 200,
    todo monitor que decide pelo código — a norma — lia a falha como sucesso.
    """
    limitado = _checar_rate(bucket, limite)
    if limitado is not None:
        return limitado
    try:
        with trava(timeout=0):
            ok = bool(carga())
    except FileLockTimeout:
        return jsonify(ok=False, tipo='ocupado', motivo=ocupado), 409
    except Exception as exc:
        logger.error("Erro ao sincronizar %s: %s", rotulo, exc)
        return jsonify(ok=False, tipo='erro', motivo='Nao foi possivel sincronizar.'), 502
    if ok:
        return jsonify(ok=True)
    return jsonify(ok=False, tipo='erro', motivo=falhou), 502


@app.post('/oportunidades/sincronizar')
@requer_chave('oportunidades:carga')
def oport_sincronizar():
    """Force the FULL oportunidades load (the scheduler's own). Requires X-API-Key.

    Uses a cross-process file lock: if the scheduler (or another trigger) is already
    running, it responds 409 instead of running two snapshot loads at once.
    """
    return _disparar_carga(
        'force_oport', _RATE_FORCE_OPORT_MAX, oportunidades_sync_lock, sync_oportunidades,
        rotulo='oportunidades',
        ocupado='Ja ha uma sincronizacao de oportunidades em andamento.',
        falhou='Nao foi possivel sincronizar (0 registros?).',
    )


@app.post('/vendas-bi/sincronizar')
@requer_chave('vendas_bi:carga')
def vendas_bi_sincronizar():
    """Recalcula os agregados do dashboard Vendas do app. Requer X-API-Key.

    Mesmo desenho do gatilho de oportunidades: rate-limit próprio e lock de
    arquivo — duas cargas simultâneas fariam upsert da mesma chave e a última a
    terminar venceria, o que é inofensivo mas desperdiça duas viagens ao HANA.
    """
    return _disparar_carga(
        'vendas_bi', _RATE_VENDAS_BI_MAX, vendas_bi_sync_lock, sync_vendas_bi,
        rotulo='vendas BI',
        ocupado='Ja ha uma carga de vendas em andamento.',
        falhou='Nao foi possivel sincronizar.',
    )


@app.post('/sync/ordens-servico/<nped>')
@requer_chave('os:sincronizar')
def sync_um(nped: str):
    """Sync **one** pedido. Requires X-API-Key. Same anti-loop guard as its pair
    ``/ordens-servico/<nped>/sincronizar`` (bucket ``sync_os``)."""
    n = _inteiro_positivo(nped, 'NPED')

    limitado = _checar_rate('sync_os', _RATE_SYNC_OS_MAX)
    if limitado is not None:
        return limitado
    return _sincronizar([n])


@app.post('/sync/ordens-servico')
@requer_chave('os:sincronizar')
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
    npeds = [_inteiro_positivo(n, 'NPED') for n in bruto]

    limitado = _checar_rate('sync_os', _RATE_SYNC_OS_MAX)
    if limitado is not None:
        return limitado
    return _sincronizar(npeds)


# ===================== Ordens de Produção (ESCRITA no SAP) =====================
# The only routes in this file that WRITE into SAP. Everything else reads SAP and writes
# to Supabase. The domain logic (state machine, allowlist, Service Layer session) lives in
# ``ordens_producao_sl``; here there is only HTTP.


def _chave_docentry() -> bool:
    """True when ``?chave=docentry`` — the ``<numero>`` is the DocEntry, not the DocNum."""
    return (request.args.get('chave') or '').strip().lower() in ('docentry', 'entry', 'absentry')


_PARAMETROS_SECRETOS = frozenset({'key', 'api_key'})


def _caminho_sem_chave() -> str:
    """The path and query for the log, with ``?key=`` / ``?api_key=`` masked.

    ``_credencial_enviada`` accepts the key in the query string; logging ``full_path`` wrote
    the SAP-writing key in plain text to ``api.log`` (01/10/2026 review)."""
    if not request.query_string:
        return request.path
    partes = [
        f"{nome}=***" if nome.lower() in _PARAMETROS_SECRETOS else f"{nome}={valor}"
        for nome, valor in request.args.items(multi=True)
    ]
    return f"{request.path}?{'&'.join(partes)}"


_SEM_AUDITORIA = ('/health', '/favicon.ico', '/casa/')
LIMITE_USUARIO = 80


@app.before_request
def _marca_inicio() -> None:
    g.sis_inicio = time.monotonic()


def _usuario_declarado(cliente: Cliente | None) -> str | None:
    """``X-SIS-Usuario`` — on whose behalf a trusted backend calls (rule 3). Ignored from any
    other client; control characters would forge lines in the audit."""
    if not cliente or not cliente.declara_usuario:
        return None
    valor = (request.headers.get('X-SIS-Usuario') or '').strip()
    if not valor or len(valor) > LIMITE_USUARIO or not valor.isprintable():
        return None
    return valor


@app.after_request
def _audita(resposta: Response) -> Response:
    """One line per call in ``logs/auditoria/api-*.jsonl`` (rule 4, 30 days).

    Skipped: liveness and static files, and the anonymous ``/`` and ``/status`` (the .90's
    watchdog polls them all day, with no credential). Everything with a credential — or a
    refused one — is recorded, reads included. Never raises."""
    try:
        caminho = request.path
        if caminho.startswith(_SEM_AUDITORIA):
            return resposta
        # Routes without @requer_chave (/status, the screens) never resolved the caller: resolve
        # it here, or a /status read with the master key was recorded as anonymous and skipped
        # (seen on the .11, 02/10/2026).
        cliente = g.get('sis_cliente') if 'sis_cliente' in g else _cliente()
        if cliente is None and caminho in ('/', '/status') and resposta.status_code < 400:
            return resposta
        inicio = g.get('sis_inicio')
        gravar, extra = auditoria.registrar, {}
        if cliente is None:
            # Rate-limited per IP and route rule (bounded: an unknown path has no rule), or a
            # LAN caller could fill the disk.
            gravar = auditoria.registrar_recusa_anonima
            regra = request.url_rule.rule if request.url_rule else '-'
            extra = {'alvo': f'{request.method} {regra}'}
        gravar(
            'api',
            **extra,
            cliente=cliente.nome if cliente else None,
            usuario=_usuario_declarado(cliente),
            metodo=request.method,
            rota=_caminho_sem_chave(),
            escopo=g.get('sis_escopo'),
            status=resposta.status_code,
            ip=request.remote_addr,
            ms=round((time.monotonic() - inicio) * 1000) if inicio else None,
        )
    except Exception as exc:  # noqa: BLE001 - see the docstring
        logger.warning('Auditoria: falha ao registrar a chamada: %s', exc)
    return resposta


@app.after_request
def _registra_chamada_de_op(resposta: Response) -> Response:
    """One INFO line per call to the OP routes: who called, what was asked, what came back.

    Added on 2026-09-29 (F0 of PLANO_API_MANUTENCAO_OP.md (removed 2026-10-06)): the status route had a real
    caller that closed >=552 OPs without stock movements, and nothing identified it — this
    API has no access log, and a refusal raised before the network (the D9 400 for
    ``encerrada``, a 401, a 429) left no trace at all. Runs after every response, so those
    refusals are logged too. Never raises: a logging problem must not change the answer.
    """
    if not request.path.startswith('/ordens-producao/'):
        return resposta
    try:
        corpo = request.get_json(silent=True) if request.method == 'POST' else None
        corpo = corpo if isinstance(corpo, dict) else {}
        devolvido = resposta.get_json(silent=True) if resposta.is_json else None
        tipo = devolvido.get('tipo') if isinstance(devolvido, dict) else None
        logger.info(
            'Rota de OP: %s %s -> %s | origem %s | agente %s | status pedido %s | status_atual %s | tipo %s',
            request.method, _caminho_sem_chave(), resposta.status_code,
            request.remote_addr or '-', (request.user_agent.string or '-')[:120],
            corpo.get('status', '-'), corpo.get('status_atual', '-'), tipo or '-',
        )
    except Exception as exc:  # noqa: BLE001 - see the docstring
        logger.warning('Rota de OP: falha ao registrar a chamada: %s', exc)
    return resposta


def _resposta_op_erro(exc: op_sl.OPError) -> tuple[Any, int]:
    """Turn a domain error into its HTTP answer.

    Each ``OPError`` subclass carries its own ``tipo``/``http``, so a new error type in the
    domain module lands correctly here with no change — the alternative was a ladder of
    isinstance checks that someone would forget to extend.
    """
    corpo = {'ok': False, 'tipo': exc.tipo, 'motivo': exc.motivo}
    corpo.update(exc.extra)
    return jsonify(corpo), exc.http


@app.get('/ordens-producao/<numero>')
@requer_chave('leitura')
def op_detalhe(numero: str):
    """Status and identification of ONE Production Order. Requires X-API-Key.

    ``<numero>`` is the **DocNum** (the number on the SAP screen); ``?chave=docentry``
    reads by DocEntry instead. Read-only — it does not change anything in the SAP.

    The answer includes ``transicoes_permitidas`` (already filtered by
    ``OP_STATUS_PERMITIDOS`` and by the order's own status), which is what lets a screen
    grey out the wrong button instead of finding out on the POST.
    """
    n = _inteiro_positivo(numero, 'numero da OP')
    try:
        op = op_sl.consultar_op(n, por_docentry=_chave_docentry())
    except op_sl.OPError as exc:
        return _resposta_op_erro(exc)
    except Exception as exc:  # never let the request blow up as a silent 500
        logger.error("Erro ao consultar a OP %s: %s", n, exc)
        return jsonify(ok=False, tipo='erro',
                       motivo='Nao foi possivel consultar a ordem de producao.'), 502
    return jsonify(ok=True, op=op)


@app.get('/wbc/orcamentos/<orcnum>')
@requer_chave('leitura')
def wbc_orcamento_rota(orcnum: str):
    """What the WBC worker knows about ONE quote. Requires X-API-Key. Read-only.

    From the worker's tracking DB (no SAP, no WBC): status, the rule it applied, the
    quotation and order it holds, the last error and the latest events. ``404`` with
    ``motivo: "fora_do_acompanhamento"`` = the worker never evaluated this quote — usually
    because it is older than the worker's window, not because it does not exist.
    """
    numero = (orcnum or '').strip()
    if not numero.isdigit() or len(numero) > 8:
        return jsonify(ok=False, error='orcamento deve ter so numeros (ate 8 digitos)'), 400
    numero = numero.zfill(8)
    try:
        dados = wbc_orcamento(numero, eventos=request.args.get('eventos', 15, type=int))
    except AcompanhamentoIndisponivel as exc:
        return jsonify(ok=False, error=str(exc)), 503
    if dados is None:
        return jsonify(
            ok=False, orcamento=numero, motivo='fora_do_acompanhamento',
            error='O worker nunca avaliou este orcamento (em geral: mais antigo que a janela dele).',
        ), 404
    return jsonify(ok=True, **dados)


@app.get('/wbc/orcamentos/<orcnum>/log')
@requer_chave('leitura')
def wbc_orcamento_log(orcnum: str):
    """The worker log lines that mention ONE quote, newest first (``?linhas=``, max 200).
    Read-only; the same file the painel's "Log" tab reads (F2 of PLANO_MIRA_AGENTE_11)."""
    numero = (orcnum or '').strip()
    if not numero.isdigit() or len(numero) > 8:
        return jsonify(ok=False, error='orcamento deve ter so numeros (ate 8 digitos)'), 400
    return jsonify(ok=True, **log_worker.log_do_orcamento(
        numero, request.args.get('linhas', log_worker.LINHAS_PADRAO, type=int)))


# --- F2 of PLANO_MIRA_AGENTE_11.md (removed 2026-10-06): read-only views of the .11 itself -----------------
# Testing a connection runs ping + TCP from the server: cheap, but a loop of an agent should
# not turn it into a probe. Same sliding window as the writes.
_RATE_CONEXAO_MAX = 20


@app.get('/operacao/servicos')
@requer_chave('leitura')
def operacao_servicos():
    """The six NSSM services of the .11: state, automatic start, since when. Read-only."""
    return jsonify(ok=True, **operacao_servicos_mod.estado_servicos())


@app.get('/operacao/conexoes')
@requer_chave('leitura')
def operacao_conexoes():
    """The closed list of destinations ``/operacao/conexoes/<destino>`` can test."""
    return jsonify(ok=True, destinos=[d.publico() for d in conexoes.destinos().values()])


@app.get('/operacao/conexoes/<destino>')
@requer_chave('leitura')
def operacao_testar_conexao(destino: str):
    """DNS, ping and TCP from the .11 to ONE listed destination (``?porta=`` one of its ports).
    Never a free host or port: an unknown name or port is 400 with the list."""
    bloqueio = _checar_rate('conexao', _RATE_CONEXAO_MAX)
    if bloqueio:
        return bloqueio
    try:
        return jsonify(ok=True, **conexoes.testar(destino, request.args.get('porta', type=int)))
    except conexoes.DestinoInvalido as exc:
        return jsonify(ok=False, error=str(exc),
                       destinos=sorted(conexoes.destinos())), 400


@app.get('/operacao/deploy')
@requer_chave('leitura')
def operacao_deploy():
    """The running commit (and whether a restart is pending), the last deploy's steps and the
    last runs without steps (``deploys``, newest first)."""
    return jsonify(ok=True, versao=versao.versao(), ultimo_deploy=versao.ultimo_deploy(),
                   deploys=versao.deploys_recentes())


@app.get('/operacao/boots')
@requer_chave('leitura')
def operacao_boots():
    """Why the .11 restarted (F5 of PLANO_TEO_REDE_E_ROTINAS, web repo): each boot of the last 7
    days, newest first, and how the life before it ended. Read only; cached 60 s."""
    return jsonify(ok=True, **boots.boots())


@app.get('/operacao/ronda-90')
@requer_chave('leitura')
def operacao_ronda_90():
    """The .90 seen from the .11 (F6 of PLANO_AGENTE_TI, web repo): its state now and the periods
    it was down, with the real times. Read from the round's file; nothing is tested here."""
    return jsonify(ok=True, **ronda_90.publico())


# --- F3/F4 of PLANO_MIRA_AGENTE_11.md (removed 2026-10-06): the agent asks, a person approves -----------------
_CANAIS = ('tela', 'whatsapp', 'mira', 'api')
_LIMITE_PESSOA = 80


def _papel_do_aprovador(cliente: Cliente) -> str | None:
    """The screens' cookie and the master key are the administrator; a trusted backend (the
    Mira on the .90) says the person's role in ``X-SIS-Papel``; anyone else has no role."""
    if cliente.nome in ('tela', 'chave-mestra', 'aberto'):
        return 'admin'
    if cliente.declara_usuario:
        papel = (request.headers.get('X-SIS-Papel') or '').strip().lower()
        return papel if papel.isalnum() and len(papel) <= 20 else None
    return None


def _pessoa_que_decide(cliente: Cliente, corpo: dict) -> str | None:
    """A backend that speaks for people names them in ``X-SIS-Usuario``; on the screen the
    person types the name (the cookie proves the key, not who holds it)."""
    if cliente.declara_usuario and cliente.nome not in ('tela', 'chave-mestra', 'aberto'):
        return _usuario_declarado(cliente)
    valor = corpo.get('pessoa')
    texto = valor.strip() if isinstance(valor, str) else ''
    if len(texto) < 2 or len(texto) > _LIMITE_PESSOA or any(ord(c) < 32 or ord(c) == 127 for c in texto):
        return None
    return texto


def _aprovacao_publica(r: dict) -> dict:
    acao = acoes_agente.CATALOGO.get(r['acao'])
    return {
        **{k: r.get(k) for k in ('id', 'codigo', 'acao', 'parametros', 'previa', 'motivo', 'pedido_por',
                                 'em_nome_de', 'criado_em', 'expira_em', 'estado', 'decidido_por',
                                 'decidido_canal', 'decidido_em', 'motivo_recusa', 'resultado', 'concluido_em')},
        'titulo': acao.titulo if acao else r['acao'],
        'quem_aprova': ('qualquer pessoa identificada' if acao is None or acao.papeis is None
                        else ' ou '.join(sorted(acao.papeis))),
    }


@app.post('/aprovacoes')
@requer_chave('leitura')
def aprovacao_pedir():
    """An agent REQUESTS a write; nothing runs. Body ``{acao, parametros, motivo}``. The caller
    needs the action's own scope (``acoes_agente.CATALOGO``); the answer is the preview a person
    will read and the ``codigo`` to approve it. Valid for ``aprovacoes.VALIDADE_MIN`` minutes."""
    cliente = _cliente()
    corpo = request.get_json(silent=True) or {}
    acao = acoes_agente.CATALOGO.get(str(corpo.get('acao') or ''))
    if acao is None:
        return jsonify(ok=False, error='acao_desconhecida', acoes=sorted(acoes_agente.CATALOGO)), 400
    if not cliente.pode(acao.escopo):
        return jsonify(ok=False, error='forbidden', tipo='sem_permissao',
                       motivo=f"A credencial '{cliente.nome}' nao tem o escopo '{acao.escopo}'."), 403
    chave_idem = (request.headers.get('Idempotency-Key') or '').strip() or None
    if chave_idem is not None and not _CHAVE_IDEM.fullmatch(chave_idem):
        return jsonify(ok=False, error='idempotency_key_invalida',
                       motivo='Idempotency-Key: 8 a 64 letras, digitos, - ou _.'), 400
    parametros = corpo.get('parametros') if isinstance(corpo.get('parametros'), dict) else {}
    try:
        parametros = acao.validar(parametros)
    except acoes_agente.AcaoInvalida as exc:
        return jsonify(ok=False, error='recusado', motivo=str(exc)), 400
    alvo = acao.alvo(parametros)
    # F4 (one owner per action): a repeat is answered with the request it repeats -- before the
    # cap (a retry is never a 429) and before building a new preview. criar() decides again,
    # atomically; this read only spares the work.
    try:
        repetido = aprovacoes.existente(acao.nome, alvo=alvo, chave_idem=chave_idem)
    except aprovacoes.AprovacaoInvalida as exc:
        return jsonify(ok=False, error=exc.tipo, motivo=str(exc)), 409
    if repetido is not None:
        return _aprovacao_criada(repetido)
    if aprovacoes.contar_recentes(acao.nome) >= acao.por_hora:
        return jsonify(ok=False, error='rate_limited',
                       motivo=f'Limite de {acao.por_hora} pedido(s) de "{acao.titulo}" por hora.'), 429
    try:
        previa = acao.previa(parametros)
    except acoes_agente.AcaoInvalida as exc:
        return jsonify(ok=False, error='recusado', motivo=str(exc)), 400
    except Exception as exc:  # a preview that could not be read is never a silent 500
        logger.error("Previa de %s falhou: %s", acao.nome, exc)
        return jsonify(ok=False, error='previa_indisponivel', motivo=str(exc)[:300]), 502
    pedido_por = cliente.nome
    if cliente.declara_usuario:
        declarado = (request.headers.get('X-SIS-Pedido-Por') or '').strip()
        if declarado and declarado.replace('-', '').replace('_', '').replace('.', '').isalnum():
            pedido_por = declarado[:40]
    try:
        r = aprovacoes.criar(acao.nome, parametros, previa, pedido_por=pedido_por,
                             em_nome_de=_usuario_declarado(cliente), motivo=corpo.get('motivo'),
                             alvo=alvo, chave_idem=chave_idem, por_hora=acao.por_hora)
    except aprovacoes.AprovacaoInvalida as exc:
        if exc.tipo == 'chave_reusada':
            return jsonify(ok=False, error=exc.tipo, motivo=str(exc)), 409
        if exc.tipo != 'limite':
            raise
        return jsonify(ok=False, error='rate_limited',
                       motivo=f'Limite de {acao.por_hora} pedido(s) de "{acao.titulo}" por hora.'), 429
    return _aprovacao_criada(r)


#: An agent's retry key: the same key returns the same request, whatever its state.
_CHAVE_IDEM = re.compile(r'[A-Za-z0-9_-]{8,64}')


def _aprovacao_criada(r: dict):
    """201 for a new request; 200 + ``ja_existia`` for the one a repeat returns (F4)."""
    if r.get('ja_existia'):
        return jsonify(ok=True, ja_existia=True, aprovacao=_aprovacao_publica(r),
                       como_aprovar=(f"Ja existia um pedido igual ({r['codigo']}, {r['estado']}); "
                                     "nada novo foi criado. Nada foi executado por este pedido.")), 200
    return jsonify(ok=True, ja_existia=False, aprovacao=_aprovacao_publica(r),
                   como_aprovar=(f"Uma pessoa aprova na Central da .11 (/inicio) ou responde "
                                 f"'aprovar {r['codigo']}' no canal da Mira. Nada foi executado.")), 201


@app.get('/aprovacoes')
@requer_chave('leitura')
def aprovacao_listar():
    """Requests, newest first (``?estado=pendente`` = waiting for a person)."""
    estado = request.args.get('estado') or None
    if estado and estado not in aprovacoes.ESTADOS:
        return jsonify(ok=False, error='estado invalido', estados=list(aprovacoes.ESTADOS)), 400
    linhas = aprovacoes.listar(estado, limite=max(1, min(request.args.get('limite', 30, type=int), 100)))
    return jsonify(ok=True, aprovacoes=[_aprovacao_publica(r) for r in linhas])


@app.get('/aprovacoes/<chave>')
@requer_chave('leitura')
def aprovacao_ver(chave: str):
    """One request by id or pending code; a processed order also brings the live execution."""
    r = aprovacoes.obter(chave)
    if r is None:
        return jsonify(ok=False, error='nao_encontrado'), 404
    publico = _aprovacao_publica(r)
    execucao = ((r.get('resultado') or {}).get('execucao') or {}).get('id')
    if r['acao'] == 'processar_pedido' and execucao:
        _, publico['execucao_atual'] = acoes_agente._chamar(
            acoes_agente._cp(), 'GET', f'/api/pedidos-wbc/execucoes/{execucao}', tempo=30)
    return jsonify(ok=True, aprovacao=publico)


def _executa_aprovada(r: dict, acao, ctx) -> None:
    try:
        ok, resultado = acao.executar(r['parametros'], r['previa'], ctx)
    except Exception as exc:  # the record must end, whatever the action did
        logger.error("Execucao aprovada %s (%s) falhou: %s", r['codigo'], acao.nome, exc)
        ok, resultado = False, {'erro': str(exc)[:300]}
    aprovacoes.concluir(r['id'], ok=ok, resultado=resultado)


def _decisao(chave: str, aprovar: bool):
    cliente = _cliente()
    corpo = request.get_json(silent=True) or {}
    pessoa = _pessoa_que_decide(cliente, corpo)
    if not pessoa:
        return jsonify(ok=False, error='sem_pessoa',
                       motivo='Informe quem decide (campo "pessoa", 2 a 80 caracteres).'), 400
    canal = str(corpo.get('canal') or ('tela' if cliente.nome == 'tela' else 'api')).lower()
    if canal not in _CANAIS or (canal in ('whatsapp', 'mira') and not cliente.declara_usuario):
        canal = 'api'
    atual = aprovacoes.obter(chave)
    if atual is None:
        return jsonify(ok=False, error='nao_encontrado', motivo='Pedido de aprovacao nao encontrado.'), 404
    acao = acoes_agente.CATALOGO.get(atual['acao'])
    papel = _papel_do_aprovador(cliente)
    if aprovar and acao is not None and not acoes_agente.pode_aprovar(acao, papel):
        return jsonify(ok=False, error='forbidden', tipo='papel',
                       motivo=f'"{acao.titulo}" so pode ser aprovado por: {" ou ".join(sorted(acao.papeis))}.'), 403
    if (aprovar and atual['acao'] == 'reiniciar_servico' and atual['parametros'].get('servico') == reinicio.API
            and aprovacoes.listar('executando', limite=1)):
        return jsonify(ok=False, error='ocupado',
                       motivo='Ha outra acao aprovada em execucao; reiniciar a API agora a interromperia.'), 409
    try:
        r = aprovacoes.decidir(chave, aprovar=aprovar, pessoa=pessoa, canal=canal, papel=papel,
                               cliente=cliente.nome, motivo=corpo.get('motivo'))
    except aprovacoes.AprovacaoInvalida as exc:
        return jsonify(ok=False, error=exc.tipo, motivo=str(exc)), 404 if exc.tipo == 'nao_encontrado' else 409
    if not aprovar:
        return jsonify(ok=True, aprovacao=_aprovacao_publica(r))
    ctx = acoes_agente.Contexto(pessoa=pessoa, codigo=r['codigo'], pedido_por=r['pedido_por'])
    threading.Thread(target=_executa_aprovada, args=(r, acao, ctx), daemon=True,
                     name=f"aprovacao-{r['codigo']}").start()
    return jsonify(ok=True, aprovacao=_aprovacao_publica(r),
                   acompanhar=f"/aprovacoes/{r['id']}"), 202


@app.post('/aprovacoes/<chave>/aprovar')
@requer_chave('aprovar')
def aprovacao_aprovar(chave: str):
    """A PERSON approves (screen, or the Mira for a person) and the action runs in background.
    The agent never holds ``aprovar`` (``credenciais.PROIBIDOS_AO_AGENTE``)."""
    return _decisao(chave, aprovar=True)


@app.post('/aprovacoes/<chave>/recusar')
@requer_chave('aprovar')
def aprovacao_recusar(chave: str):
    """A person refuses; nothing runs. Body ``{pessoa, motivo}``."""
    return _decisao(chave, aprovar=False)


@app.get('/pedidos/<numero>/historico')
@requer_chave('leitura')
def pedido_historico(numero: str):
    """What changed in ONE sales order, version by version, and who saved each one (the
    SAP change log, ADOC/ADO1). ``<numero>`` is the DocNum (``?chave=docentry`` for the
    DocEntry); ``?versoes=`` caps how many, newest first (default 20, max 60). Read-only."""
    n = _inteiro_positivo(numero, 'numero do pedido')
    try:
        dados = historico_pedido.historico(
            n, por_docentry=_chave_docentry(),
            limite=request.args.get('versoes', historico_pedido.VERSOES_PADRAO, type=int))
    except historico_pedido.PedidoNaoEncontrado as exc:
        return jsonify(ok=False, motivo='pedido_nao_encontrado', error=str(exc)), 404
    except sit_ped_hana.SAPIndisponivel as exc:
        return jsonify(ok=False, error=str(exc)), 503
    return jsonify(ok=True, **dados)


@app.post('/ordens-producao/<numero>/status')
@requer_chave('op:status')
def op_status(numero: str):
    """Change the status of ONE Production Order **in the SAP**. Requires X-API-Key.

    Body: ``{"status": "liberada", "status_atual": "<opcional>"}``. ``status`` also accepts
    the raw code (``boposReleased``). ``encerrada``/``boposClosed`` → ``400`` since D9
    (2026-09-28): closing an OP is the ``controleproducao`` package's job (Manutenção de OP,
    with the stock movements); ``OP_STATUS_PERMITIDOS`` in the .env can widen the allowlist
    (rollback). ``status_atual`` is a compare-and-swap: sent and divergent → 409, nothing
    written.

    Status: ``200`` changed (or ``ja_estava: true``, with no PATCH sent) · ``400`` invalid
    number/status or status outside the allowlist · ``401`` missing X-API-Key · ``404`` no
    such OP · ``409`` terminal status, ambiguous DocNum or compare-and-swap mismatch ·
    ``429`` anti-loop guard · ``502`` Service Layer down or the SAP refused · ``503``
    feature off or this API has no key configured.
    """
    # FAIL-CLOSED, unlike every other route here. Elsewhere an unset OS_API_KEY leaves the
    # API open (documented at the top of this file) — acceptable for reads and for writes
    # into Supabase, which we can undo. This one writes into SAP PRODUCTION, so "no key
    # configured" must mean "refuse", never "let anyone through".
    if not get_settings().os_api_key:
        return jsonify(
            ok=False, tipo='sem_chave',
            motivo=('Escrita em OP exige OS_API_KEY configurada no servidor. '
                    'Defina OS_API_KEY no .env e reinicie a API.'),
        ), 503

    n = _inteiro_positivo(numero, 'numero da OP')

    body = request.get_json(silent=True) or {}
    if not body.get('status'):
        return jsonify(
            ok=False, error="informe 'status'",
            motivo=("Corpo esperado: {'status': 'liberada'}. Encerrar uma OP é pela tela "
                    'Manutenção de OP do Controle de Produção, não por esta rota.'),
        ), 400

    # After validating the body: a malformed request never reaches the SAP, so it should
    # not burn a token of the guard that protects the SAP.
    limitado = _checar_rate('op_status', _RATE_OP_STATUS_MAX)
    if limitado is not None:
        return limitado

    try:
        with _op_lock:
            resultado = op_sl.atualizar_status(
                n, body['status'],
                por_docentry=_chave_docentry(),
                status_atual=body.get('status_atual'),
            )
    except op_sl.OPError as exc:
        return _resposta_op_erro(exc)
    except Exception as exc:
        logger.error("Erro ao atualizar o status da OP %s: %s", n, exc)
        return jsonify(ok=False, tipo='erro',
                       motivo='Nao foi possivel atualizar o status da OP.'), 502
    return jsonify(ok=True, **resultado)


# --------------------- Situacao dos Pedidos (VW_STATUS_PEDIDO_DDP) ---------------------
# A MESMA view que desenha a tela do OrcaView, com o MESMO nucleo de normalizacao
# (`situacao_pedidos.py` e' porte, e um teste compara os dois fontes). O que muda e' o
# consumidor: aqui e' a fachada MCP, nao um navegador.
#
# Estas sao as duas UNICAS rotas deste servico que leem o HANA ao vivo -- todas as outras
# leituras batem no Supabase. O que segura o SAP e' o cache de 120s do
# `situacao_pedidos_hana`, nao um rate-limit: dois clientes MCP conversando ao mesmo
# tempo compartilham o mesmo retrato.
#
# Contrato congelado em 2026-08-24: PLANO_SITUACAO_PEDIDOS_MCP.md (removido em 2026-09-29) secao 5.


def _resposta_situacao_erro(exc: Exception) -> tuple[Any, int]:
    """Erro de dominio -> resposta HTTP. **503 e 422 nao se misturam.**

    ``SAPIndisponivel`` e' 503 (nao e' culpa de quem chamou, e vale tentar de novo);
    ``ValidationError`` e' 422 (parametro fora do dominio -- tentar de novo nao adianta).
    A mensagem vai inteira no corpo: e' ela que a fachada MCP mostra ao modelo, em vez de
    um "HTTP 503" generico.
    """
    if isinstance(exc, sit_ped_hana.SAPIndisponivel):
        logger.warning("[SIT_PED] indisponivel: %s", exc)
        return jsonify(ok=False, error=str(exc)), 503
    logger.info("[SIT_PED] parametro recusado: %s", exc)
    return jsonify(ok=False, error=str(exc)), 422


def _aplicar_endereco(pedidos: list, linhas: list) -> None:
    """Poe ``entrega_endereco`` em cada pedido normalizado, **no lugar**.

    Aqui, e nao dentro do ``situacao_pedidos.py``: aquele arquivo e' PORTE do
    ``situacao_pedidos_service.py`` do OrcaView e um teste compara os dois funcao por
    funcao. Endereco e' consumo desta API (e da fachada MCP); a tela mostra o dela por
    outro caminho. Este modulo e o ``situacao_pedidos_hana`` sao os dois lugares deste
    repo que nao sao portados — e' por isso que a decoracao mora num deles.

    Casa por ``DocEntry`` (a chave interna, 1:1 com a linha da view); pedido sem linha
    crua correspondente recebe o endereco vazio, com as MESMAS chaves — nunca ``None``
    solto, que viraria ``KeyError`` em quem le.
    """
    por_docentry = {r.get('DocEntry'): r for r in linhas}
    for p in pedidos:
        crua = por_docentry.get(p.get('doc_entry')) or {}
        p['entrega_endereco'] = sit_ped_hana.endereco_entrega_efetivo(crua)


def _aplicar_liberacao_e_nf(pedidos: list, linhas: list) -> None:
    """Poe os 11 campos de liberacao real + primeira NF + Incoterms em cada pedido, **no lugar**.

    Mesmo desenho do :func:`_aplicar_endereco` (e fora do nucleo portado pelo mesmo
    motivo): casa por ``DocEntry`` e pedido sem linha crua recebe as MESMAS chaves, com
    ``None`` ("nao foi possivel saber"). So o perfil ``completo`` os mostra — o ``resumo``
    corta para ``CAMPOS_RESUMO``. Plano: ``PLANO_DATAS_LIBERACAO_NF.md (removed 2026-10-06)``.
    """
    por_docentry = {r.get('DocEntry'): r for r in linhas}
    for p in pedidos:
        crua = por_docentry.get(p.get('doc_entry')) or {}
        p.update(sit_ped_hana.liberacao_e_nf(crua))


def _resumir(pedidos: list) -> list:
    """``resumir()`` do nucleo + os 3 campos de entrega, ja resolvidos.

    O ``resumir`` corta para as colunas da tela (``CAMPOS_RESUMO``, que e' do porte e
    nao pode ganhar campo daqui). Os tres entram DEPOIS, nesta camada.

    **Sao os tres ja resolvidos, de proposito** (D4, decisao do Marcelo em 10/09): quem
    esta no perfil ``resumo`` — o default da lista — nao recebe o ShipTo, entao nao tem
    como escolher errado. ``entrega_difere`` vai junto porque desenha o selo da tela,
    **nao** porque alguem deva decidir com ele.
    """
    resumidos = sit_ped.resumir(pedidos)
    for r, p in zip(resumidos, pedidos):
        e = p.get('entrega_endereco') or {}
        r['entrega_linha'] = e.get('linha')
        r['entrega_cidade_uf'] = sit_ped_hana.cidade_uf(e)
        r['entrega_difere'] = bool(e.get('difere_do_ponto_de_entrega'))
    return resumidos


def _situacao_recorte() -> tuple[dict, list]:
    """Recorte inteiro da view: ``(dashboard, pedidos_com_alerta)``.

    Uma unica ida ao :func:`fetch_status_pedidos` alimenta os dois -- e ela mesma passa
    pelo cache, entao as tres consultas do plano respondem sobre o MESMO retrato. O
    endereco de entrega entra aqui, sobre as mesmas linhas cruas.
    """
    linhas = sit_ped_hana.fetch_status_pedidos(recarregar=_flag_query('recarregar'))
    dashboard = sit_ped.montar_dashboard(linhas)
    _aplicar_endereco(dashboard['pedidos'], linhas)
    _aplicar_liberacao_e_nf(dashboard['pedidos'], linhas)
    return dashboard, sit_ped.com_alerta(dashboard['pedidos'])


def _campos_resumo(padrao_resumo: bool) -> bool:
    """``?campos=resumo|completo``. O default e' por rota -- ver a D4 do plano."""
    pedido = (request.args.get('campos') or '').strip().lower()
    if pedido == 'resumo':
        return True
    if pedido in ('completo', 'full'):
        return False
    return padrao_resumo


@app.get('/pedidos/situacao')
@requer_chave('leitura')
def situacao_pedidos_lista():
    """Recorte da Situacao dos Pedidos -- as consultas 2 e 3 do plano. Requer X-API-Key.

    Sem parametro nenhum devolve o recorte inteiro da view (**consulta 3**: "todos os
    dados, incluindo montadores"). Com ``?bloqueio=qualquer`` devolve so o que esta
    travado em pelo menos uma etapa (**consulta 2**).

    **Parametros:** ``bloqueio`` (``qualquer`` | ``financeiro`` | ``producao`` |
    ``entrega`` | ``nenhum``), ``status`` (``todos`` | ``aberto`` | ``fechado``),
    ``montador`` (CNPJ ou ``__sem__``), ``busca`` (cliente, codigo, numero do pedido ou
    cotacao WBC), ``so_atrasados_fin=1`` (so quem passou dos 10 dias no financeiro),
    ``campos`` (``resumo`` -- o default AQUI -- ou ``completo``) e ``recarregar=1``.

    **Os KPIs e a lista de montadores sao sempre do recorte INTEIRO**, nunca do
    filtrado: e' assim na tela (o card diz quantos existem, o filtro diz quais aparecem),
    e trocar isso faria o mesmo numero significar coisas diferentes nos dois lugares.
    Quantos voltaram esta em ``total_filtrado``.

    **404 nao existe aqui:** filtro que nao casa com nada devolve **200** com a lista
    vazia. E' resposta legitima ("nao ha nada bloqueado"), nao pedido inexistente.
    """
    try:
        recorte, pedidos = _situacao_recorte()
        filtrados = sit_ped.filtrar(
            pedidos,
            status=(request.args.get('status') or None),
            montador=(request.args.get('montador') or None),
            busca=(request.args.get('busca') or None),
        )
        filtrados = sit_ped.filtrar_bloqueio(filtrados, request.args.get('bloqueio'))
        if _flag_query('so_atrasados_fin'):
            filtrados = sit_ped.filtrar_liberacao_atrasada(filtrados)
    except (sit_ped_hana.SAPIndisponivel, sit_ped.ValidationError) as exc:
        return _resposta_situacao_erro(exc)
    except Exception as exc:  # nunca deixar a request virar um 500 mudo
        logger.error("[SIT_PED] falha inesperada na lista: %s", exc)
        return jsonify(ok=False, error='falha ao montar a situacao dos pedidos'), 502

    itens = _resumir(filtrados) if _campos_resumo(True) else filtrados
    logger.info("[SIT_PED] lista: %d de %d (bloqueio=%s status=%s montador=%s busca=%s).",
                len(itens), recorte['kpis']['total'], request.args.get('bloqueio'),
                request.args.get('status'), request.args.get('montador'),
                bool(request.args.get('busca')))
    return jsonify(
        ok=True,
        gerado_em=recorte['gerado_em'],
        cache_idade_s=sit_ped_hana.idade_do_cache_s(),
        kpis=recorte['kpis'],
        total_no_recorte=recorte['kpis']['total'],
        total_filtrado=len(itens),
        pedidos=itens,
        montadores=recorte['montadores'],
    )


@app.get('/pedidos/<numero>/situacao')
@requer_chave('leitura')
def situacao_pedido_unico(numero: str):
    """Situacao de UM pedido -- a consulta 1 do plano. Requer X-API-Key.

    ``<numero>`` e' o **DocNum** (o numero que aparece na tela, ex.: 84260);
    ``?chave=docentry`` procura pelo DocEntry. Os dois **nao** sao o mesmo numero, e
    confundi-los devolveria outro pedido calado -- a mesma armadilha das rotas de Ordem
    de Producao.

    **Pedido cancelado no SAP responde 200 dizendo "Cancelado"** (desde 2026-09-03). A
    view exclui cancelado, entao a resposta vem da ORDR ao vivo, no MESMO formato: as
    tres etapas viram ``"Cancelado"``, com ``fonte: "ordr"``, ``pedido_cancelado: true``
    e ``aviso``. Quem consome nao precisa de outra chamada nem de outro `if`.

    **404 quer dizer "nao da' para afirmar a situacao", nao "sem bloqueio"** -- e nunca
    vem mudo: ``motivo`` diz qual dos tres casos e' (``fora_do_recorte``, o pedido de
    2024 que a view nao carrega; ``pedido_nao_encontrado``, DocNum que nao existe na
    ORDR; ``indeterminado``, o SAP nao respondeu agora) e ``status_pedido`` traz o que a
    ORDR disser. Responder "sem bloqueio" em qualquer um deles seria mentira.

    Em toda resposta, ``status_pedido`` + ``pedido_cancelado`` no topo -- o MESMO par de
    ``GET /ordens-servico/<nped>``, com o mesmo sentido (``null`` = nao se sabe).

    **409** quando o numero casa com mais de um pedido: recusa, nunca resolve por
    ``[0]``. Nao deve acontecer (a view e' por DocEntry), e e' justamente por isso que
    merece resposta propria em vez de "o primeiro que achar".

    Aqui o default e' ``campos=completo`` (e' um registro so, cabe); ``?campos=resumo``
    corta para as 10 colunas da tela.
    """
    n = _inteiro_positivo(numero, 'numero do pedido')

    campo = 'doc_entry' if _chave_docentry() else 'doc_num'
    try:
        _, pedidos = _situacao_recorte()
    except (sit_ped_hana.SAPIndisponivel, sit_ped.ValidationError) as exc:
        return _resposta_situacao_erro(exc)
    except Exception as exc:
        logger.error("[SIT_PED] falha inesperada no pedido %s: %s", n, exc)
        return jsonify(ok=False, error='falha ao consultar a situacao do pedido'), 502

    achados = [p for p in pedidos if p.get(campo) == n]
    if not achados:
        return _situacao_fora_da_view(n, campo)
    if len(achados) > 1:
        logger.warning("[SIT_PED] %s %s casa com %d pedidos.", campo, n, len(achados))
        return jsonify(
            ok=False,
            error=f'{campo} {n} casa com {len(achados)} pedidos - consulte por DocEntry',
            pedido=n, chave=campo, total=len(achados),
        ), 409

    pedido = _resumir(achados)[0] if _campos_resumo(False) else achados[0]
    return jsonify(ok=True,
                   gerado_em=sit_ped.now_br().isoformat(timespec='seconds'),
                   cache_idade_s=sit_ped_hana.idade_do_cache_s(),
                   fonte='view',
                   status_pedido=achados[0].get('status_pedido'),
                   pedido_cancelado=False,  # a view exclui cancelado: se esta aqui, nao e'
                   pedido=pedido)


#: Motivo publicado quando a situacao nao pode ser afirmada. Chave estavel (o texto do
#: ``error`` e' para gente; isto e' para o `if` de quem consome).
SIT_PED_MOTIVOS = {
    'fora_do_recorte': 'pedido {n} fora do recorte da view (ela carrega so os pedidos '
                       'correntes) - isto NAO quer dizer que ele esteja sem bloqueio',
    'pedido_nao_encontrado': 'pedido {n} nao existe no SAP (nao ha DocNum {n} na ORDR)',
    'indeterminado': 'pedido {n} nao esta no recorte da view e o SAP nao respondeu agora '
                     '- situacao INDETERMINADA, nao conclua que ele esta liberado',
}

#: Frase unica do pedido cancelado — a MESMA de ``/ordens-servico/<nped>``.
SIT_PED_AVISO_CANCELADO = ('Pedido cancelado no SAP - nao ha etapa a liberar; '
                           'nao produza nem entregue por ele.')


def _situacao_fora_da_view(n: int, campo: str):
    """Pedido que a ``VW_STATUS_PEDIDO_DDP`` nao carrega -> pergunta ao SAP (ORDR).

    **Cancelado responde 200 dizendo "Cancelado"** (decisao do dono, 2026-09-03). Ate
    aqui a rota devolvia um 404 mudo, identico ao de um pedido de 2024, e a tela de quem
    consome mostrava "sem situacao" para pedido que o usuario tinha cancelado no SAP
    (84282, 84305, 84314) — reclamacao real de quem consome. Cancelado **e'** uma
    situacao, e a ORDR sabe dize-la.

    O payload sai com o **mesmo formato** do caminho da view (passa pelo
    :func:`situacao_pedidos.normalizar`, entao nenhuma chave falta e nada muda de tipo):
    as tres etapas vem ``"Cancelado"`` em vez de Liberado/Bloqueado. Quem ja desenha o
    chip da etapa passa a escrever "Cancelado" sem mudar uma linha.

    Os demais casos continuam **404** — mas nunca mudos: ``motivo`` diz qual e'
    (``fora_do_recorte`` / ``pedido_nao_encontrado`` / ``indeterminado``) e
    ``status_pedido`` traz o que a ORDR disser. SAP fora e' ``indeterminado``, jamais
    "sem bloqueio".
    """
    # A ORDR so' sabe procurar por DocNum: com ?chave=docentry nao ha o que perguntar,
    # e inventar uma traducao devolveria a situacao de OUTRO pedido, calada.
    info = None
    if campo == 'doc_num':
        try:
            info = consultar_status_pedido(n)
        except Exception as exc:  # ORDR fora nunca vira 500 nesta rota
            logger.error("[SIT_PED] falha ao consultar a ORDR do pedido %s: %s", n, exc)

    if info and info.get('pedido_cancelado'):
        logger.info("[SIT_PED] pedido %s cancelado no SAP (fora da view).", n)
        pedido = _pedido_cancelado_ordr(n, info)
        if _campos_resumo(False):
            pedido = _resumir([pedido])[0]
        return jsonify(ok=True,
                       gerado_em=sit_ped.now_br().isoformat(timespec='seconds'),
                       cache_idade_s=sit_ped_hana.idade_do_cache_s(),
                       fonte='ordr',
                       status_pedido='Cancelado',
                       pedido_cancelado=True,
                       aviso={'tipo': 'pedido_cancelado',
                              'motivo': SIT_PED_AVISO_CANCELADO},
                       pedido=pedido)

    if info is None:
        # Por DocEntry nem se perguntou: o pedido pode existir, so nao esta na view.
        motivo = 'fora_do_recorte' if campo != 'doc_num' else 'indeterminado'
        status, cancelado = None, None
    elif info.get('pedido_existe'):
        motivo, status, cancelado = 'fora_do_recorte', info.get('pedido_status'), False
    else:
        motivo, status, cancelado = 'pedido_nao_encontrado', None, False
    logger.info("[SIT_PED] pedido %s (%s) sem situacao na view: %s.", n, campo, motivo)
    return jsonify(
        ok=False, motivo=motivo, error=SIT_PED_MOTIVOS[motivo].format(n=n),
        pedido=n, chave=campo, status_pedido=status, pedido_cancelado=cancelado,
    ), 404


def _pedido_cancelado_ordr(n: int, info: dict) -> dict:
    """Linha da ORDR -> o MESMO dict que a view produz, com as etapas ``"Cancelado"``.

    Monta uma linha crua (PascalCase) e a entrega ao ``normalizar`` do nucleo portado,
    em vez de escrever o dict a mao: assim o formato **nao consegue** divergir do
    caminho da view quando alguem acrescentar um campo la. ``com_alerta`` fecha o
    ``alerta_liberacao`` (sempre ``None`` aqui — cancelado nao alarma os 10 dias).
    """
    crua = {'DocNum': n, 'CardCode': info.get('card_code'),
            'CardName': info.get('card_name'), 'Data_Pedido': info.get('data_pedido'),
            'DocTotal': info.get('valor_total'), 'DocCur': info.get('moeda'),
            'Financeiro': 'Cancelado', 'Producao': 'Cancelado', 'Entrega': 'Cancelado',
            'StatusPedido': 'Cancelado'}
    # D5: o endereco vem da RDR12 daquele DocNum, nao de nulos. Pedido cancelado
    # tambem tem endereco, e a chave nao pode faltar so aqui — ha teste comparando as
    # chaves dos dois caminhos. Best-effort: SAP fora nao derruba a resposta de "este
    # pedido esta cancelado", que e' o que a rota veio dizer.
    try:
        crua.update(sit_ped_hana.fetch_endereco_do_pedido(n))
    except Exception as exc:
        logger.warning("[SIT_PED] endereco do pedido cancelado %s indisponivel: %s", n, exc)
    pedido = sit_ped.com_alerta(sit_ped.normalizar([crua]))[0]
    _aplicar_endereco([pedido], [{**crua, 'DocEntry': pedido.get('doc_entry')}])
    # Cancelado nao passa pela view nem pelas consultas de liberacao: as chaves entram
    # nulas (primeira_nf_emitida = false), para o formato ser o mesmo do caminho da view.
    _aplicar_liberacao_e_nf([pedido], [])
    return pedido


# ---------------------------------------------------------------------------
# RH — espelho de colaboradores do Kairos (LEITURA)
# ---------------------------------------------------------------------------
# Quem escreve a tabela é o web_orcaview_V118 (.90), 12:40 em dias úteis. Este
# repo NÃO tem credencial do Kairos e não deve ganhar uma: aqui só se lê o
# espelho, como no GET /ordens-servico/<nped>.

#: As três empresas do Kairos. Chave fora desta lista é RECUSADA (400): o
#: cliente Kairos do V117 cai calado na 'altamira' quando não reconhece a
#: chave, e repetir isso aqui devolveria o quadro da empresa errada com 200.
COLAB_EMPRESAS = ('altamira', 'tecnequip', 'proalta')

#: Projeção: tudo o que o contrato publica, nada do que é interno
#: (primeira_vista_em, atualizado_em — carimbos do banco).
_COLAB_COLS = (
    'empresa,person_id,nome,matricula,setor,cargo,status,'
    'em_ferias_ou_afastado,sem_expediente_desde,'
    'data_admissao,data_desligamento,ultima_vista_em'
)
_COLAB_PAGINA = 1000        # o PostgREST corta em 1000 com HTTP 200 — paginar é obrigatório
_COLAB_MAX_LINHAS = 20000   # teto de sanidade do laço de paginação
_COLAB_SLOT = (12, 40)      # horário da carga no .90
_COLAB_TOLERANCIA_H = 2     # atraso aceitável (o agendador retenta 4x a cada 3 min)
_COLAB_SEM_SETOR = 'SEM SETOR'


def _fetch_colaboradores(empresa: str | None = None,
                         somente_ativos: bool = False) -> list[dict]:
    """Rows of the roster mirror, paginated (the table only grows: rows are never
    deleted — who leaves becomes ``status='desligado'``/``'ausente'``)."""
    tabela = get_settings().colab_table_name
    linhas: list[dict] = []
    for inicio in range(0, _COLAB_MAX_LINHAS, _COLAB_PAGINA):
        q = _supabase().table(tabela).select(_COLAB_COLS)
        if empresa:
            q = q.eq('empresa', empresa)
        if somente_ativos:
            q = q.eq('status', 'ativo')
        pagina = q.order('empresa').order('nome').range(
            inicio, inicio + _COLAB_PAGINA - 1
        ).execute().data or []
        linhas.extend(pagina)
        if len(pagina) < _COLAB_PAGINA:
            break
    return linhas


def _colab_carimbo(linhas: list[dict]) -> str | None:
    """Newest ``ultima_vista_em`` — the stamp of the last load that saw anybody.

    ISO strings with the same offset sort as text; the collector always writes UTC.
    """
    carimbos = [l.get('ultima_vista_em') for l in linhas if l.get('ultima_vista_em')]
    return max(carimbos) if carimbos else None


def _colab_frescor(carimbo_iso: str | None) -> dict:
    """Frescor do espelho: perdeu o último slot? contra qual slot comparou?

    Não é um "mais velho que N horas": na segunda de manhã o dado É de sexta e
    isso está certo (a carga só roda em dia útil). A pergunta é se o 12:40 mais
    recente que já passou produziu carga.

    Devolve também ``carga_esperada_em`` (o slot da comparação, que torna o
    ``desatualizado`` auditável em vez de mágico) e ``atualizado_em_br`` — o mesmo
    instante em horário de Brasília, porque o carimbo cru é UTC e "19:31" já é
    lido como se fosse hora local por quem bate o olho no JSON.
    """
    # Import local: o módulo `time` (stdlib) já é importado no topo com esse nome.
    from datetime import datetime, timedelta
    from datetime import time as _time

    agora = sit_ped.now_br()
    limite = agora - timedelta(hours=_COLAB_TOLERANCIA_H)
    esperado = None
    dia = limite.date()
    for _ in range(30):  # ~1 mês de folga cobre qualquer feriadão
        if feriados_br.is_business_day(dia):
            slot = datetime.combine(dia, _time(*_COLAB_SLOT), tzinfo=agora.tzinfo)
            if slot <= limite:
                esperado = slot
                break
        dia -= timedelta(days=1)

    carimbo = None
    if carimbo_iso:
        try:
            carimbo = datetime.fromisoformat(str(carimbo_iso).replace('Z', '+00:00'))
        except ValueError:
            carimbo = None
        if carimbo is not None and carimbo.tzinfo is None:
            carimbo = carimbo.replace(tzinfo=agora.tzinfo)

    return {
        'desatualizado': carimbo is None or (esperado is not None and carimbo < esperado),
        'carga_esperada_em': esperado.isoformat(timespec='minutes') if esperado else None,
        'atualizado_em_br': (carimbo.astimezone(agora.tzinfo).isoformat(timespec='seconds')
                             if carimbo else None),
    }


def _agrupar_colaboradores(linhas: list[dict]) -> list[dict]:
    """Empresa → Setor → colaboradores, ordenado e com contagens.

    ``cargo`` é CAMPO do colaborador, não um nível: aninhar por cargo criaria um
    nível de uma pessoa só na maioria dos casos (decisão 4 do plano).
    """
    por_empresa: dict = {}
    for linha in linhas:
        setores = por_empresa.setdefault(linha.get('empresa') or '?', {})
        setor = (linha.get('setor') or '').strip() or _COLAB_SEM_SETOR
        setores.setdefault(setor, []).append({
            'person_id': linha.get('person_id'),
            'nome': linha.get('nome'),
            'matricula': linha.get('matricula'),
            'cargo': linha.get('cargo'),
            'status': linha.get('status'),
            'em_ferias_ou_afastado': bool(linha.get('em_ferias_ou_afastado')),
            'sem_expediente_desde': linha.get('sem_expediente_desde'),
            'data_admissao': linha.get('data_admissao'),
            'data_desligamento': linha.get('data_desligamento'),
        })
    return [
        {
            'empresa': empresa,
            'total': sum(len(pessoas) for pessoas in setores.values()),
            'setores': [
                {
                    'setor': setor,
                    'total': len(pessoas),
                    'colaboradores': sorted(
                        pessoas, key=lambda c: (c.get('nome') or '').upper()
                    ),
                }
                for setor, pessoas in sorted(setores.items())
            ],
        }
        for empresa, setores in sorted(por_empresa.items())
    ]


@app.get('/rh/colaboradores')
@requer_chave('rh')
def rh_colaboradores():
    """Quadro de colaboradores das 3 empresas do Kairos. Requer X-API-Key.

    Devolve ``empresas[] → setores[] → colaboradores[]``, cada pessoa com
    ``status`` (``ativo``/``desligado``/``ausente``), cargo, matrícula e o sinal
    ``em_ferias_ou_afastado``. **A linha do desligado nunca some** — é isso que
    impede o programa de quem consome de quebrar quando alguém sai; use
    ``?somente_ativos=1`` para receber só quem está na ativa.

    ``?empresa=`` restringe a uma empresa e **recusa (400)** chave desconhecida,
    de propósito: cair no default devolveria o quadro de outra empresa com 200.

    ``desatualizado=true`` diz que a carga do último 12:40 de dia útil não
    chegou — o dado ainda é servido (é o último bom conhecido), mas quem
    consome fica sabendo. Fonte: espelho ``kairos_colaboradores`` no Supabase,
    escrito pelo web_orcaview_V118; esta API só lê.
    """
    empresa = (request.args.get('empresa') or '').strip().lower()
    if empresa and empresa not in COLAB_EMPRESAS:
        return jsonify(
            ok=False,
            error=f"empresa invalida: use uma de {', '.join(COLAB_EMPRESAS)}",
            empresas_validas=list(COLAB_EMPRESAS),
        ), 400

    somente_ativos = _flag_query('somente_ativos')
    try:
        linhas = _fetch_colaboradores(empresa or None, somente_ativos)
    except Exception as exc:
        logger.error("[RH] falha ao ler o espelho de colaboradores: %s", exc)
        return jsonify(ok=False, error='falha ao ler o espelho de colaboradores'), 502

    carimbo = _colab_carimbo(linhas)
    frescor = _colab_frescor(carimbo)
    return jsonify(
        ok=True,
        atualizado_em=carimbo,
        total=len(linhas),
        **frescor,
        somente_ativos=somente_ativos,
        empresas=_agrupar_colaboradores(linhas),
    )


def main() -> None:
    """Start the server (waitress in production; Flask dev as fallback)."""
    _configure_logging()
    s = get_settings()
    if not s.os_api_key:
        logger.warning(
            "OS_API_KEY não definido — endpoint SEM autenticação "
            "(ok p/ rede interna/dev; defina OS_API_KEY em produção). "
            "A escrita de status de OP fica BLOQUEADA (503) enquanto não houver chave."
        )
    # Loud on purpose: this is the one feature of this service that changes data inside
    # SAP, and it points at the production company database.
    if s.op_sl_ready():
        logger.warning(
            "ESCRITA de status de Ordem de Producao LIGADA — base %s em %s (usuario %s). "
            "Liga pelo IP da maquina (.11), sem chave no .env.",
            s.op_sl_company_db, s.op_sl_server, s.op_sl_username,
        )
    # The update search costs 3.1s here (measured; 30s cold) and would blow the timeout of
    # whoever calls /status — hence it runs on a daemon thread, off the request path.
    # Here in the entrypoint and NOT on import: otherwise the test suite would fire
    # PowerShell.
    windows_update.iniciar_coletor(s)
    # The .90 seen from here, every 5 min (only on the .11; same reason to start it here).
    ronda_90.iniciar()
    # F4 (one owner per action): an approved action cut by a crash must not hold its target.
    aprovacoes.varrer_orfaos()
    # Rotulo do Tipo de Montagem passa a vir do SAP (UFD1) em vez do fallback
    # embutido. Aqui e NAO no import, pelo mesmo motivo da linha acima: no import, a
    # suite de testes acabaria falando com o HANA de verdade.
    sit_ped_hana.ligar_rotulos_do_sap()
    host, port = s.os_api_host, s.os_api_port
    try:
        from waitress import serve
        logger.info("Servindo via waitress em http://%s:%s", host, port)
        # 8 threads, not waitress's 4 (01/10/2026 review): a few slow HANA reads must not take
        # every thread and leave /health without an answer for the .90 watchdog.
        serve(app, host=host, port=port, threads=API_THREADS)
    except ImportError:
        logger.warning("waitress não instalado — usando o servidor de DEV do Flask.")
        app.run(host=host, port=port)


if __name__ == '__main__':
    main()
