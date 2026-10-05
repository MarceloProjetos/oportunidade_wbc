"""The one login of the "Central Integração SAP" (owner's decision 6, 01/10/2026).

The three screens are separate processes on the same host (8077, PAINEL_PORTA, CP_PORTA).
Browsers do not scope cookies by port, so ONE cookie opens all three: it carries an HMAC of
``OS_API_KEY``, never the key itself — rotating the key in ``.env`` invalidates every cookie
at once, with no session state on any server. Stdlib only, no I/O: the Flask API imports it
without pulling the painel in. Moved here from ``wbcpython/dashboard/acesso.py`` (which still
re-exports it) when the Sincronização joined (PLANO_CASA_COMUM_11, F3).
"""
from __future__ import annotations

import hashlib
import hmac
from urllib.parse import urlsplit

#: Cookie that proves the key was already checked in this browser (an HMAC token). The name
#: is historical (the painel issued it first) and stays: renaming it logs everybody out.
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
    # Browsers drop tab/CR/LF from a URL, so "/%09/evil.com" lands on "//evil.com" (seen on the
    # 8077 in the 01/10/2026 review). Any control character, or anything urlsplit reads as a
    # host, is refused.
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in proximo) or urlsplit(proximo).netloc:
        return "/"
    return proximo


def mesma_origem(origem: str, netloc: str) -> bool:
    """``origem`` (the ``Origin`` header, else ``Referer``) points at ``netloc`` (host:port).

    The cookie rides along on any request the browser makes to this host — also one fired by
    a page on another port of the same IP, since ``SameSite`` ignores ports. A write
    authenticated only by the cookie must prove it came from the screen itself (CSRF)."""
    alvo = urlsplit(origem or "").netloc
    return bool(alvo) and alvo == netloc
