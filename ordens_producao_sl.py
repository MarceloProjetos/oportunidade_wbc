"""Production Order (OP) status writes through the SAP B1 Service Layer.

**The only write path into SAP in this repository.** Everything else here reads (HANA via
``hdbcli``) and writes to Supabase; this module PATCHes ``ProductionOrders`` in the
production company database (``SBOALTAMIRAPROD``). Every guard below exists because of
that one fact.

What it does
------------
- ``consultar_op(numero)`` — reads one OP by **DocNum** (the number on the SAP screen) or
  by **DocEntry** (the internal key). They are NOT the same number.
- ``atualizar_status(numero, status)`` — moves the OP to *Liberada* (``boposReleased``) or
  *Encerrada* (``boposClosed``). Nothing else: cancelling and moving back to Planned are
  out of scope, and are refused before any call reaches the SAP.

Guards
------
- **Kill switch** ``OP_SL_ENABLED`` defaults to **false**: with it off, nothing here opens
  a socket. Rollback in production is one line in the ``.env``.
- **State machine checked on our side** (see :func:`atualizar_status`), not by reading the
  SAP's error text: nothing leaves a terminal status, and target == current returns
  ``ja_estava`` WITHOUT sending a PATCH — idempotency by construction.
- **Allowlist** (``OP_STATUS_PERMITIDOS``) is checked before the network.
- **Shared session with TTL** + automatic re-login on 401 / ``"Invalid session"``. A login
  per request is what exhausts the Service Layer's finite session pool and takes it down
  for the B1 client too, so the replaced session is logged out explicitly on renewal.
- **Timeout on every request** (``_TimeoutSession``). ``requests`` ignores
  ``session.timeout``; without an explicit ``timeout=`` a dead SAP host hangs the calling
  thread forever, and waitress runs out of threads — ``/health`` dies with it.
- **No retry on the PATCH.** A refused status change is deterministic (wrong state, closed
  order, missing permission); retrying only buries the real message. Retry exists on the
  login alone, which is the genuinely transient step.

Kept deliberately diffable with ``web_orcaview_V117/backend/services/compras_sap_service.py``
— same session/re-login/error-extraction shape. A fix in one belongs in the other.

TLS: the internal Service Layer serves a **self-signed** certificate, so
``OP_SL_VERIFY_SSL`` defaults to false. Suppressing urllib3's warning is
**process-global** (urllib3 offers no per-session switch), so it happens once, lazily, at
the first login, and only after a WARNING naming the host is logged.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from typing import Any, Dict, Optional, Tuple

import requests
import urllib3

from config import Settings, get_settings

logger = logging.getLogger(__name__)

__all__ = [
    'OPError', 'OPDesativado', 'OPNaoEncontrada', 'OPAmbigua', 'OPStatusInvalido',
    'OPTransicaoInvalida', 'OPConflito', 'OPIndisponivel',
    'STATUS_LABELS', 'resolver_status', 'consultar_op', 'atualizar_status',
    'transicoes_permitidas', 'force_logout',
]

# Login retries. Short on purpose: the request is synchronous and someone is waiting. The
# transient failure this covers is the SL still loading OBServerDLL right after a restart.
_MAX_LOGIN_RETRIES = 3
_LOGIN_BACKOFF_BASE_S = 1.5
_LOGIN_BACKOFF_MAX_S = 8.0


# ── Errors ────────────────────────────────────────────────────────────────────────
# Each one carries the HTTP status the API must answer, so ``api.py`` maps them with a
# single ``except`` instead of a ladder of isinstance checks — a new error type here shows
# up correctly on the route with no change over there.

class OPError(Exception):
    """Base error. ``motivo`` is accent-free PT-BR, like the rest of this API's messages
    (readable on any console without depending on JSON's ``\\uXXXX`` escaping)."""

    tipo = 'erro'
    http = 502

    def __init__(self, motivo: str, **extra: Any) -> None:
        super().__init__(motivo)
        self.motivo = motivo
        self.extra = extra


class OPDesativado(OPError):
    """Kill switch off, or Service Layer credentials missing."""

    tipo = 'desativado'
    http = 503


class OPNaoEncontrada(OPError):
    tipo = 'nao_encontrada'
    http = 404


class OPAmbigua(OPError):
    """A DocNum matched more than one order — refuse instead of picking ``[0]``."""

    tipo = 'ambigua'
    http = 409


class OPStatusInvalido(OPError):
    """Unknown status, or a status outside ``OP_STATUS_PERMITIDOS``."""

    tipo = 'status_invalido'
    http = 400


class OPTransicaoInvalida(OPError):
    """The order is in a terminal status (Encerrada/Cancelada)."""

    tipo = 'transicao_invalida'
    http = 409


class OPConflito(OPError):
    """``status_atual`` sent by the caller does not match what is in the SAP."""

    tipo = 'conflito'
    http = 409


class OPIndisponivel(OPError):
    """Service Layer unreachable, login refused, or the SAP rejected the change."""

    tipo = 'indisponivel'
    http = 502


# ── Statuses ──────────────────────────────────────────────────────────────────────

STATUS_LABELS = {
    'boposPlanned': 'Planejada',
    'boposReleased': 'Liberada',
    'boposClosed': 'Encerrada',
    'boposCancelled': 'Cancelada',
}

# PT-BR wording accepted in the request body, so the front does not have to know the SAP
# enum. The raw code is accepted too (case-insensitively).
_STATUS_ALIASES = {
    'planejada': 'boposPlanned', 'planejar': 'boposPlanned',
    'liberada': 'boposReleased', 'liberar': 'boposReleased', 'liberado': 'boposReleased',
    'encerrada': 'boposClosed', 'encerrar': 'boposClosed', 'encerrado': 'boposClosed',
    'fechada': 'boposClosed', 'fechar': 'boposClosed', 'concluida': 'boposClosed',
    'cancelada': 'boposCancelled', 'cancelar': 'boposCancelled',
}

# The verb we hand back in ``transicoes_permitidas`` — the same word the POST accepts, so
# the front can feed the answer straight back without a translation table of its own.
_STATUS_PARA_VERBO = {
    'boposPlanned': 'planejada',
    'boposReleased': 'liberada',
    'boposClosed': 'encerrada',
    'boposCancelled': 'cancelada',
}

# Nothing leaves these. B1 has no transition out of a closed or cancelled order.
_TERMINAIS = ('boposClosed', 'boposCancelled')

_CODIGO_POR_LOWER = {c.lower(): c for c in STATUS_LABELS}


def resolver_status(bruto: Any) -> str:
    """Normalize what the caller sent into a canonical SAP status code.

    Accepts the PT-BR word (``"encerrar"``) or the raw code (``"boposClosed"``), in any
    case. Raises :class:`OPStatusInvalido` for anything else — never guesses.
    """
    texto = str(bruto or '').strip().lower()
    if not texto:
        raise OPStatusInvalido("Informe o status desejado ('liberada' ou 'encerrada').")
    codigo = _CODIGO_POR_LOWER.get(texto) or _STATUS_ALIASES.get(texto)
    if codigo is None:
        raise OPStatusInvalido(
            f'Status desconhecido: {bruto!r}. Use liberada ou encerrada '
            '(ou o codigo boposReleased / boposClosed).'
        )
    return codigo


def transicoes_permitidas(status_atual: Optional[str]) -> list:
    """Which changes the caller may actually ask for, from ``status_atual``.

    This is what lets a screen grey out the wrong button BEFORE the user clicks it. The
    current status itself is left out (asking for it is a no-op, not a transition), and a
    terminal status yields an empty list.
    """
    if status_atual in _TERMINAIS:
        return []
    return [
        _STATUS_PARA_VERBO[c]
        for c in get_settings().op_status_permitidos
        if c != status_atual and c in _STATUS_PARA_VERBO
    ]


# ── Session ───────────────────────────────────────────────────────────────────────

class _TimeoutSession(requests.Session):
    """``requests.Session`` that injects a default ``timeout`` into EVERY request.

    ``requests`` ignores ``session.timeout``. Overriding ``request()`` covers every call
    site at once — present and future — because ``.get/.post/.patch`` all funnel through
    it. A single call can still override with an explicit ``timeout=``.
    """

    def __init__(self, default_timeout: Tuple[float, float]) -> None:
        super().__init__()
        self._default_timeout = default_timeout

    def request(self, *args: Any, **kwargs: Any) -> requests.Response:
        kwargs.setdefault('timeout', self._default_timeout)
        return super().request(*args, **kwargs)


_sessao: Optional[requests.Session] = None
_sessao_criada_em: float = 0.0
_sessao_lock = threading.Lock()
_avisou_tls = False


def _exigir_habilitado(s: Settings) -> None:
    """Refuse before touching the network when the feature is off or half-configured."""
    if not s.op_sl_enabled:
        raise OPDesativado(
            'Integracao de Ordem de Producao desligada (OP_SL_ENABLED=false).'
        )
    if not (s.op_sl_username and s.op_sl_password):
        raise OPDesativado(
            'Credenciais do Service Layer ausentes (OP_SL_USERNAME / OP_SL_PASSWORD).'
        )
    if not s.op_status_permitidos:
        raise OPDesativado(
            'Nenhum status permitido configurado (OP_STATUS_PERMITIDOS) - '
            'toda escrita esta bloqueada.'
        )


def _avisar_tls_uma_vez(s: Settings) -> None:
    """Log the fact that TLS verification is off, then silence urllib3's warning.

    ``disable_warnings`` is process-global (urllib3 has no per-session switch), so it runs
    once and only after the WARNING naming the host is on the record — a security fact
    that belongs in the log, not swallowed silently at import time.
    """
    global _avisou_tls
    if _avisou_tls or s.op_sl_verify_ssl:
        return
    _avisou_tls = True
    logger.warning(
        'Service Layer %s: verificacao de certificado TLS DESLIGADA '
        '(OP_SL_VERIFY_SSL=false; certificado autoassinado interno).', s.op_sl_server,
    )
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def _login(s: Settings) -> requests.Session:
    """Authenticate and return a fresh session. Retries with backoff + jitter.

    The credentials NEVER reach the log — a failure logs the SAP's message, never the
    payload.
    """
    _avisar_tls_uma_vez(s)
    payload = {
        'CompanyDB': s.op_sl_company_db,
        'UserName': s.op_sl_username,
        'Password': s.op_sl_password,
    }
    ultimo = ''
    for tentativa in range(1, _MAX_LOGIN_RETRIES + 1):
        sessao = _TimeoutSession(s.op_sl_timeout)
        try:
            resp = sessao.post(
                f'{s.op_sl_base_url}/Login', json=payload, verify=s.op_sl_verify_ssl,
            )
            if resp.ok:
                logger.info(
                    'Service Layer: login OK em %s (base %s, usuario %s).',
                    s.op_sl_server, s.op_sl_company_db, s.op_sl_username,
                )
                return sessao
            ultimo = _erro_sl(resp)
        except requests.RequestException as exc:
            ultimo = str(exc)
        sessao.close()
        logger.warning(
            'Service Layer: login falhou (tentativa %s/%s): %s',
            tentativa, _MAX_LOGIN_RETRIES, ultimo,
        )
        if tentativa < _MAX_LOGIN_RETRIES:
            espera = min(_LOGIN_BACKOFF_BASE_S * (2 ** (tentativa - 1)), _LOGIN_BACKOFF_MAX_S)
            time.sleep(round(espera + random.uniform(0, 1.0), 2))
    raise OPIndisponivel(f'Nao foi possivel autenticar no Service Layer do SAP: {ultimo}')


def _obter_sessao(s: Settings) -> requests.Session:
    """The shared session, logging in (or renewing past the TTL) when needed."""
    global _sessao, _sessao_criada_em
    with _sessao_lock:
        vencida = (time.monotonic() - _sessao_criada_em) >= s.op_sl_session_ttl_s
        if _sessao is not None and not vencida:
            return _sessao
        antiga = _sessao if vencida else None
        _sessao = None
    # Logging out the REPLACED session matters: the Service Layer caps concurrent
    # sessions, and abandoning one per TTL cycle leaks them until the SL refuses every
    # login — the B1 client included.
    if antiga is not None:
        _encerrar_sessao(antiga, s)
    nova = _login(s)
    with _sessao_lock:
        _sessao = nova
        _sessao_criada_em = time.monotonic()
        return _sessao


def _encerrar_sessao(sessao: requests.Session, s: Settings) -> None:
    """Best-effort ``/Logout`` + close. Never raises: this is hygiene, not the task."""
    try:
        sessao.post(f'{s.op_sl_base_url}/Logout', verify=s.op_sl_verify_ssl)
    except requests.RequestException as exc:
        logger.debug('Service Layer: logout da sessao antiga falhou (ignorado): %s', exc)
    finally:
        sessao.close()


def _invalidar_sessao(morta: requests.Session) -> None:
    """Drop ``morta`` from the shared slot — but only if it is still the current one.

    The identity check avoids the race where another thread already re-logged in: without
    it, this thread would throw away a perfectly good brand-new session and the two would
    take turns invalidating each other.
    """
    global _sessao
    with _sessao_lock:
        if _sessao is morta:
            _sessao = None
    morta.close()


def force_logout() -> None:
    """Close the shared session (tests, and a clean shutdown)."""
    global _sessao, _sessao_criada_em
    with _sessao_lock:
        sessao, _sessao, _sessao_criada_em = _sessao, None, 0.0
    if sessao is not None:
        _encerrar_sessao(sessao, get_settings())


# ── Transport ─────────────────────────────────────────────────────────────────────

def _erro_sl(resp: requests.Response) -> str:
    """Pull the message out of the SL's nested error ``{error:{message:{value}}}``."""
    try:
        corpo = resp.json()
    except ValueError:
        return (resp.text or '')[:300] or f'HTTP {resp.status_code}'
    if isinstance(corpo, dict):
        msg = corpo.get('error', {})
        if isinstance(msg, dict):
            valor = msg.get('message')
            if isinstance(valor, dict) and valor.get('value'):
                return str(valor['value'])[:300]
            if isinstance(valor, str) and valor:
                return valor[:300]
    return (resp.text or '')[:300] or f'HTTP {resp.status_code}'


def _sessao_expirou(resp: requests.Response) -> bool:
    """True when the SAP invalidated our session.

    The 401 is the usual signal, but SL error 301 ("Invalid session") also comes back
    under other status codes — matching only on 401 leaves those as a hard failure that a
    simple re-login would have fixed.
    """
    if resp.status_code == 401:
        return True
    return not resp.ok and 'Invalid session' in (resp.text or '')


def _request(
    metodo: str,
    caminho: str,
    *,
    json_body: Optional[dict] = None,
    _relogin: bool = True,
) -> requests.Response:
    """One Service Layer call, re-logging in **once** if the session went stale."""
    s = get_settings()
    sessao = _obter_sessao(s)
    url = f'{s.op_sl_base_url}{caminho}'
    try:
        resp = sessao.request(metodo, url, json=json_body, verify=s.op_sl_verify_ssl)
    except requests.RequestException as exc:
        raise OPIndisponivel(
            f'Nao foi possivel falar com o Service Layer do SAP: {exc}'
        ) from exc
    if _relogin and _sessao_expirou(resp):
        logger.info('Service Layer: sessao expirada — refazendo login e repetindo a chamada.')
        _invalidar_sessao(sessao)
        # ``_relogin=False``: exactly ONE replay. Without the flag a Service Layer stuck on
        # 401 would loop until the request timed out, hammering /Login the whole way.
        return _request(metodo, caminho, json_body=json_body, _relogin=False)
    return resp


# ── Domain ────────────────────────────────────────────────────────────────────────

def _numero_valido(valor: Any) -> int:
    """Positive int, or refuse. Guarantees the ``$filter`` below is safe to interpolate."""
    try:
        n = int(str(valor).strip())
    except (TypeError, ValueError):
        raise OPStatusInvalido(f'Numero de OP invalido: {valor!r}') from None
    if n <= 0:
        raise OPStatusInvalido(f'Numero de OP invalido (deve ser > 0): {valor!r}')
    return n


def _normalizar(op: dict) -> Dict[str, Any]:
    """Shape one Service Layer record into the API's answer.

    ``AbsoluteEntry`` with a ``DocEntry`` fallback: the Service Layer names the internal
    key differently depending on the endpoint, and both show up in real payloads.
    """
    status = op.get('ProductionOrderStatus')
    return {
        'doc_entry': op.get('AbsoluteEntry') if op.get('AbsoluteEntry') is not None
        else op.get('DocEntry'),
        'doc_num': op.get('DocumentNumber'),
        'item': op.get('ItemNo'),
        'quantidade_planejada': op.get('PlannedQuantity'),
        'status': status,
        'status_desc': STATUS_LABELS.get(status, status),
        'origem': op.get('ProductionOrderOrigin'),
        'origem_numero': op.get('ProductionOrderOriginNumber'),
        'data_entrega': op.get('DueDate'),
        'transicoes_permitidas': transicoes_permitidas(status),
    }


def consultar_op(numero: Any, *, por_docentry: bool = False) -> Dict[str, Any]:
    """Read one Production Order.

    Args:
        numero: the **DocNum** (the number on the SAP screen, e.g. 129850) by default, or
            the **DocEntry** when ``por_docentry`` is set. They are different numbers for
            the same order — OP 125060 is DocEntry 126599.
        por_docentry: read ``ProductionOrders(numero)`` directly instead of resolving.

    Raises:
        OPDesativado: feature off or credentials missing.
        OPNaoEncontrada: no such order.
        OPAmbigua: the DocNum matched more than one order.
        OPIndisponivel: Service Layer unreachable or refusing.
    """
    s = get_settings()
    _exigir_habilitado(s)
    n = _numero_valido(numero)

    if por_docentry:
        resp = _request('GET', f'/ProductionOrders({n})')
        if resp.status_code == 404:
            raise OPNaoEncontrada(f'Ordem de producao com DocEntry {n} nao encontrada no SAP.')
        if not resp.ok:
            raise OPIndisponivel(f'O SAP recusou a consulta da OP {n}: {_erro_sl(resp)}')
        try:
            return _normalizar(resp.json())
        except ValueError as exc:
            raise OPIndisponivel(f'Resposta ilegivel do Service Layer para a OP {n}.') from exc

    resp = _request('GET', f'/ProductionOrders?$filter=DocumentNumber eq {n}')
    if not resp.ok:
        raise OPIndisponivel(f'O SAP recusou a consulta da OP {n}: {_erro_sl(resp)}')
    try:
        ops = resp.json().get('value') or []
    except ValueError as exc:
        raise OPIndisponivel(f'Resposta ilegivel do Service Layer para a OP {n}.') from exc
    if not ops:
        raise OPNaoEncontrada(f'Ordem de producao {n} nao encontrada no SAP.')
    if len(ops) > 1:
        # Taking ops[0] here would silently act on an arbitrary order. Refusing costs the
        # caller one retry with the DocEntry; guessing costs a wrong OP changed in
        # production.
        raise OPAmbigua(
            f'O numero {n} corresponde a {len(ops)} ordens de producao. '
            'Repita usando o DocEntry (?chave=docentry).'
        )
    return _normalizar(ops[0])


def atualizar_status(
    numero: Any,
    status: Any,
    *,
    por_docentry: bool = False,
    status_atual: Any = None,
) -> Dict[str, Any]:
    """Move an OP to *Liberada* or *Encerrada*.

    The order of the checks below IS the state machine, and it is deliberate:

    1. kill switch / credentials — nothing happens with the feature off;
    2. resolve the target and check it against ``OP_STATUS_PERMITIDOS`` — a forbidden
       target is refused **without a single HTTP call**;
    3. read the order (this is also what resolves DocNum → DocEntry, so the pre-check
       costs no extra round-trip);
    4. compare-and-swap, when the caller sent ``status_atual``;
    5. target == current → ``ja_estava``, **no PATCH** (idempotent by construction, not by
       luck: repeating the call cannot double-apply anything);
    6. current is terminal → refuse. Step 5 comes first on purpose, so asking a closed
       order to close answers 200, while asking it to reopen answers 409.

    Args:
        numero: DocNum (default) or DocEntry (with ``por_docentry``).
        status: ``"liberada"`` / ``"encerrada"``, or the raw ``bopos*`` code.
        status_atual: optional compare-and-swap. When given and it does not match what is
            in the SAP, nothing is written — this is what stops two screens from
            overwriting each other's decision.

    Returns:
        ``{doc_entry, doc_num, item, status_anterior, status_novo, ja_estava}``.
    """
    s = get_settings()
    _exigir_habilitado(s)

    alvo = resolver_status(status)
    if alvo not in s.op_status_permitidos:
        raise OPStatusInvalido(
            f"Esta API nao altera o status para '{STATUS_LABELS.get(alvo, alvo)}'. "
            f'Permitidos: {", ".join(STATUS_LABELS[c] for c in s.op_status_permitidos)}.'
        )

    op = consultar_op(numero, por_docentry=por_docentry)
    atual = op['status']
    doc_entry, doc_num = op['doc_entry'], op['doc_num']

    if status_atual is not None:
        esperado = resolver_status(status_atual)
        if esperado != atual:
            raise OPConflito(
                f'A OP {doc_num} esta como {STATUS_LABELS.get(atual, atual)} no SAP, '
                f'nao {STATUS_LABELS.get(esperado, esperado)}. '
                'Alguem mudou enquanto isso - recarregue e tente de novo.',
                status_atual=atual,
            )

    base = {
        'doc_entry': doc_entry, 'doc_num': doc_num, 'item': op['item'],
        'status_anterior': atual, 'status_novo': alvo,
    }

    if atual == alvo:
        logger.info('OP %s (DocEntry %s) ja esta como %s — nada a fazer.',
                    doc_num, doc_entry, STATUS_LABELS.get(alvo, alvo))
        return {**base, 'ja_estava': True}

    if atual in _TERMINAIS:
        raise OPTransicaoInvalida(
            f'A OP {doc_num} esta {STATUS_LABELS.get(atual, atual)} - '
            'nao ha mudanca de status possivel a partir dai.',
            status_atual=atual,
        )

    # The write itself. This line is the whole reason the module exists; everything above
    # is there to make sure it is the right OP and the right transition.
    logger.info('OP %s (DocEntry %s): %s -> %s | base %s',
                doc_num, doc_entry, atual, alvo, s.op_sl_company_db)
    resp = _request(
        'PATCH', f'/ProductionOrders({doc_entry})',
        json_body={'ProductionOrderStatus': alvo},
    )
    if resp.status_code not in (200, 204):
        detalhe = _erro_sl(resp)
        logger.error('OP %s: o SAP recusou %s -> %s: %s', doc_num, atual, alvo, detalhe)
        # No retry: a refused status change is deterministic (wrong state, missing
        # permission, order in use). Retrying only delays the real message.
        raise OPIndisponivel(f'O SAP recusou a mudanca de status da OP {doc_num}: {detalhe}')

    logger.info('OP %s (DocEntry %s) atualizada para %s.',
                doc_num, doc_entry, STATUS_LABELS.get(alvo, alvo))
    return {**base, 'ja_estava': False}
