"""Access gate shared by the painel WBC and the Controle de Produção web app.

The two screens are separate processes on the same host (PAINEL_PORTA and CP_PORTA).
Browsers do not scope cookies by port, so ONE cookie opens both: it carries an HMAC of
``OS_API_KEY``, never the key itself — rotating the key in ``.env`` invalidates every
cookie at once, with no session state on the server. Kept out of ``web.py`` because
importing that module builds the painel (tracking DB, executor); this one has no I/O.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

#: Cookie that proves the key was already checked in this browser (an HMAC token).
COOKIE_DE_ACESSO = "wbc_painel"


def token_da_chave(chave: str) -> str:
    """What goes into the cookie: an HMAC of the key, not the key."""
    return hmac.new(chave.encode("utf-8"), b"painel-wbc", hashlib.sha256).hexdigest()


def igual(a: str, b: str) -> bool:
    """``compare_digest`` over bytes: comparing ``str`` leaks timing, and a key with an
    accent would make ``compare_digest`` raise ``TypeError`` (a 500 instead of a 401)."""
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def destino_local(proximo: str) -> str:
    """Only go back to a path of THIS page. ``//other-host`` and ``http://…`` become ``/``:
    ``proximo`` comes from the URL, and the login page must not turn into an open redirect."""
    proximo = (proximo or "").strip()
    if not proximo.startswith("/") or proximo.startswith("//") or "\\" in proximo:
        return "/"
    return proximo


def autenticado(request: Any, chave: str, token_esperado: str) -> bool:
    """Cookie with the expected token, or the raw key in ``X-API-Key`` / ``?key=`` (scripts
    and ``curl``, like the API). No key configured → open, as everywhere else in the SIS."""
    if not chave:
        return True
    cookie = request.cookies.get(COOKIE_DE_ACESSO, "")
    if cookie and igual(cookie, token_esperado):
        return True
    enviada = request.headers.get("x-api-key") or request.query_params.get("key") or ""
    return bool(enviada) and igual(enviada, chave)


def por_cookie(request: Any, token_esperado: str) -> bool:
    """True when THIS request was authenticated by the cookie (a browser), not by the key
    header. Only browser requests need the same-origin check on writes (CSRF)."""
    cookie = request.cookies.get(COOKIE_DE_ACESSO, "")
    return bool(cookie) and bool(token_esperado) and igual(cookie, token_esperado)
