"""Login gate of the Controle de Produção screen (28/09/2026).

Same cookie, same key and same HMAC as the painel WBC (``wbcpython.dashboard.acesso``): the
two screens are separate processes on the same host, and browsers do not scope cookies by
port, so whoever entered the painel is already in here — and vice versa. Each app keeps its
own ``/entrar`` because the painel's login page only redirects to paths of its own port.

Open without the key: ``/entrar``, ``/sair``, ``/favicon.ico``, ``/health*`` (the SIS
``/status`` probe and the deploy guard), ``/painel-wbc`` (a redirect) and ``/static/*``.
Everything else — pages, the task JSON the browser polls and ``POST /tarefas/{id}/cancelar``
— needs the cookie or ``X-API-Key``. With no ``OS_API_KEY`` configured the screen is open for
READING, like the painel; writes are refused by ``core.web.avisa_escrita`` (503).

CSRF: a POST authenticated by the cookie must carry an ``Origin`` (or ``Referer``) whose host
is this server's own host. ``samesite=lax`` alone still sends the cookie on top-level
navigations, and ``Liberar`` writes on the first POST. Requests authenticated
by the key header (scripts, ``curl``) are exempt: they never carry the cookie.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlsplit

from fastapi import FastAPI, Form, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse

from controleproducao.config import get_settings
from controleproducao.core.tarefas import TAREFAS
from controleproducao.core.templates import templates
from wbcpython.dashboard import acesso as painel

ROTAS_ABERTAS = frozenset({"/entrar", "/sair", "/favicon.ico", "/health", "/health/ocupado", "/painel-wbc"})


def _chave_e_token() -> tuple[str, str]:
    """Read per request, not at app creation: tests swap the key with ``cache_clear``."""
    chave = get_settings().os_api_key.get_secret_value()
    return chave, (painel.token_da_chave(chave) if chave else "")


def _e_chamada_de_script(request: Request) -> bool:
    """A ``fetch`` from the page (``X-Requested-With``) or any client asking for JSON."""
    if request.headers.get("x-requested-with"):
        return True
    return request.headers.get("accept", "").lower().startswith("application/json")


def _mesma_origem(request: Request) -> bool:
    """``Origin`` (or ``Referer``) host must be the request's own host; neither → refused."""
    origem = request.headers.get("origin") or request.headers.get("referer") or ""
    host_origem = urlsplit(origem).hostname
    return bool(host_origem) and host_origem == request.url.hostname


def tarefas_ativas() -> int:
    """Tasks running or queued — what a deploy must not interrupt."""
    return sum(1 for tarefa in TAREFAS.listar(limite=200) if not tarefa.terminada)


def _tela_de_entrada(request: Request, *, erro: str | None, proximo: str) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "entrar.html", {"erro": erro, "proximo": painel.destino_local(proximo)}
    )


def instalar(app: FastAPI) -> None:
    """Middleware + the open routes. Called once by ``main.py`` after the routers."""

    @app.middleware("http")
    async def exigir_chave(request: Request, call_next: Any) -> Any:
        caminho = request.url.path
        if caminho in ROTAS_ABERTAS or caminho.startswith("/static/"):
            return await call_next(request)
        chave, token = _chave_e_token()
        if not painel.autenticado(request, chave, token):
            if _e_chamada_de_script(request):
                # A fetch (task polling, "Interromper") must SEE the refusal: a 303 would be
                # followed silently and the login HTML would land where JSON was expected —
                # the page would freeze while the task keeps writing. Same idea as the
                # painel's HX-Redirect for HTMX.
                return JSONResponse(
                    {"detail": "Sessão expirada — entre de novo.", "entrar": "/entrar"}, status_code=401
                )
            proximo = caminho + (f"?{request.url.query}" if request.url.query else "")
            return RedirectResponse(f"/entrar?proximo={quote(proximo, safe='')}", status_code=303)
        if request.method == "POST" and painel.por_cookie(request, token) and not _mesma_origem(request):
            return JSONResponse(
                {"detail": "Origem da requisição não confere com este servidor (proteção CSRF)."},
                status_code=403,
            )
        return await call_next(request)

    @app.get("/entrar", response_class=HTMLResponse)
    def entrar(request: Request, proximo: str = "/") -> Any:
        chave, _ = _chave_e_token()
        if not chave:
            return RedirectResponse("/", status_code=303)
        return _tela_de_entrada(request, erro=None, proximo=proximo)

    @app.post("/entrar", response_class=HTMLResponse)
    def conferir_chave(
        request: Request, chave_informada: str = Form("", alias="chave"), proximo: str = Form("/")
    ) -> Any:
        chave, token = _chave_e_token()
        if not chave:
            return RedirectResponse("/", status_code=303)
        if not painel.igual(chave_informada, chave):
            resposta = _tela_de_entrada(request, erro="Chave incorreta.", proximo=proximo)
            resposta.status_code = 401
            return resposta
        resposta = RedirectResponse(painel.destino_local(proximo), status_code=303)
        resposta.set_cookie(
            painel.COOKIE_DE_ACESSO, token, max_age=30 * 24 * 3600, httponly=True, samesite="lax"
        )
        return resposta

    @app.post("/sair")
    def sair() -> RedirectResponse:
        """Forgets the key in this browser (and in the painel WBC — same cookie)."""
        resposta = RedirectResponse("/entrar", status_code=303)
        resposta.delete_cookie(painel.COOKIE_DE_ACESSO)
        return resposta

    @app.get("/favicon.ico")
    def favicon() -> Response:
        return Response(status_code=204)

    @app.get("/health")
    def health() -> JSONResponse:
        """Liveness for the SIS ``/status`` check and for people. Open; no secrets."""
        s = get_settings()
        ativas = tarefas_ativas()
        return JSONResponse(
            {
                "ok": True,
                "servico": "controleproducao",
                "producao": s.is_production,
                "company_db": s.sl_company_db,
                "tarefas_ativas": ativas,
                "ocupado": ativas > 0,
                "chave_configurada": bool(s.os_api_key.get_secret_value()),
            }
        )

    @app.get("/health/ocupado", response_class=PlainTextResponse)
    def ocupado() -> str:
        """``1`` while a task runs or waits, else ``0`` — what ``deploy_update.bat`` reads
        before ``nssm stop`` (stopping mid-task leaves half-created OPs in the SAP)."""
        return "1" if tarefas_ativas() else "0"

    @app.get("/painel-wbc")
    def painel_wbc(request: Request) -> RedirectResponse:
        """The way back to the painel WBC: ``WBC_PAINEL_URL`` verbatim, or this host on
        ``PAINEL_PORTA`` (the .11 case)."""
        s = get_settings()
        destino = s.wbc_painel_url.strip() or f"{request.url.scheme}://{request.url.hostname}:{s.painel_porta}/"
        return RedirectResponse(destino, status_code=302)
