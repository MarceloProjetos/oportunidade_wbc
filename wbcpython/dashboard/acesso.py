"""Access gate shared by the painel WBC and the Controle de Produção web app.

The cookie primitives (name, HMAC, constant-time compare, local redirect) live in
``casa/acesso.py`` since 01/10/2026 — the Painel de Sincronização (Flask, 8077) checks the
same cookie and must not import the painel to do it. They are re-exported here so every
existing import keeps working. What stays here reads a Starlette request.
"""

from __future__ import annotations

from typing import Any

from casa.acesso import COOKIE_DE_ACESSO, destino_local, igual, mesma_origem, token_da_chave

__all__ = [
    "COOKIE_DE_ACESSO",
    "autenticado",
    "destino_local",
    "escrita_permitida",
    "igual",
    "mesma_origem",
    "por_chave",
    "por_cookie",
    "token_da_chave",
]


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


def por_chave(request: Any, chave: str) -> bool:
    """The raw key came with THIS request (``X-API-Key`` / ``?key=``): a script, not a page.
    A foreign page cannot forge it, so it needs no origin check."""
    enviada = request.headers.get("x-api-key") or request.query_params.get("key") or ""
    return bool(chave) and bool(enviada) and igual(enviada, chave)


def escrita_permitida(request: Any, chave: str) -> bool:
    """A non-GET request may go on: sent with the key, or from a page of this same host:port.

    01/10/2026 review: the 8077 and the Controle de Produção already checked the origin and the
    painel did not — and the painel's per-quote cycle writes to SAP with no password, so a page
    on another port of the .11 (SameSite ignores ports) could fire it with the operator's
    cookie. Without a key configured the painel is open, and the origin still has to match."""
    if request.method in ("GET", "HEAD", "OPTIONS") or por_chave(request, chave):
        return True
    origem = request.headers.get("origin") or request.headers.get("referer") or ""
    return mesma_origem(origem, request.url.netloc)


def por_cookie(request: Any, token_esperado: str) -> bool:
    """True when THIS request was authenticated by the cookie (a browser), not by the key
    header. Only browser requests need the same-origin check on writes (CSRF)."""
    cookie = request.cookies.get(COOKIE_DE_ACESSO, "")
    return bool(cookie) and bool(token_esperado) and igual(cookie, token_esperado)
