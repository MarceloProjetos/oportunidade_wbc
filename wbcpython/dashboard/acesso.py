"""Access gate shared by the painel WBC and the Controle de Produção web app.

The cookie primitives (name, HMAC, constant-time compare, local redirect) live in
``casa/acesso.py`` since 01/10/2026 — the Painel de Sincronização (Flask, 8077) checks the
same cookie and must not import the painel to do it. They are re-exported here so every
existing import keeps working. What stays here reads a Starlette request.
"""

from __future__ import annotations

from typing import Any

from casa.acesso import COOKIE_DE_ACESSO, destino_local, igual, token_da_chave

__all__ = ["COOKIE_DE_ACESSO", "autenticado", "destino_local", "igual", "por_cookie", "token_da_chave"]


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
