"""Login gate of the Controle de Produção screen (28/09/2026).

Same cookie, same key and same HMAC as the painel WBC (``wbcpython.dashboard.acesso``): the
two screens are separate processes on the same host, and browsers do not scope cookies by
port, so whoever entered the painel is already in here — and vice versa. Each app keeps its
own ``/entrar`` because the painel's login page only redirects to paths of its own port.

Open without the key: ``/entrar``, ``/sair``, ``/favicon.ico``, ``/health*`` (the SIS
``/status`` probe and the deploy guard), ``/painel-wbc``, ``/sincronizacao`` and ``/orcaview``
(redirects), ``/static/*`` and ``/casa/*`` (the shared shell's CSS/JS — the key prompt wears
it too).
Everything else — pages, the task JSON the browser polls and ``POST /tarefas/{id}/cancelar``
— needs the cookie or ``X-API-Key``. With no ``OS_API_KEY`` configured the screen is open for
READING, like the painel; writes are refused by ``core.web.avisa_escrita`` (503).

CSRF: a POST authenticated by the cookie must carry an ``Origin`` (or ``Referer``) whose host
is this server's own host. ``samesite=lax`` alone still sends the cookie on top-level
navigations, and ``Liberar`` writes on the first POST. Requests authenticated
by the key header (scripts, ``curl``) are exempt: they never carry the cookie.

The JSON API under ``/api/`` (29/09/2026, docs/PLANO_API_MANUTENCAO_OP.md) takes the key in
``X-API-Key`` ONLY: no cookie (so no CSRF surface on its writes) and no ``?key=`` (a key in a
URL ends up in logs). Missing or wrong → 401 in the API's error shape. With no key configured
it is open for reading like the screen, and its writes answer 503 (``avisa_escrita``).

Anyone holding the key may consume it (D1, 29/09/2026) — a browser page on another origin
included: ``/api/`` answers CORS for any origin (``_BordaDaApi``), without credentials. That
opens nothing to a caller without the key, since the API never reads the cookie. The screens
get no CORS headers and keep the same-origin rule above.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlsplit

from fastapi import FastAPI, Form, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from casa import destinos
from controleproducao.config import get_settings
from controleproducao.core.tarefas import TAREFAS
from controleproducao.core.templates import templates
from wbcpython.dashboard import acesso as painel

ROTAS_ABERTAS = frozenset(
    {"/entrar", "/sair", "/favicon.ico", "/health", "/health/ocupado", "/painel-wbc",
     "/sincronizacao", "/inicio", "/orcaview"}
)

PREFIXO_API = "/api/"


class _BordaDaApi:
    """What any client of ``/api/`` needs from the transport (D1, 29/09/2026).

    - CORS for any origin, no credentials. Installed outermost: the browser's preflight never
      carries ``X-API-Key`` and must be answered before the key gate, and a 401 must carry
      the headers too, or the page cannot read why it was refused.
    - ``charset=utf-8`` on the JSON: without it Windows PowerShell 5.1 ``Invoke-RestMethod``
      decodes the body as Latin-1 ("concluída" → "concluÃ­da", seen against the .11).
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.cors = CORSMiddleware(
            app, allow_origins=["*"], allow_methods=["GET", "POST"], allow_headers=["*"], max_age=600
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(PREFIXO_API):
            await self.app(scope, receive, send)
            return

        async def envia(mensagem: Message) -> None:
            if mensagem["type"] == "http.response.start":
                mensagem["headers"] = [
                    (nome, b"application/json; charset=utf-8")
                    if nome.lower() == b"content-type" and valor == b"application/json" else (nome, valor)
                    for nome, valor in mensagem.get("headers", [])
                ]
            await send(mensagem)

        await self.cors(scope, receive, envia)


def _chave_e_token() -> tuple[str, str]:
    """Read per request, not at app creation: tests swap the key with ``cache_clear``."""
    chave = get_settings().os_api_key.get_secret_value()
    return chave, (painel.token_da_chave(chave) if chave else "")


def _e_chamada_de_script(request: Request) -> bool:
    """A ``fetch`` from the page (``X-Requested-With``) or any client asking for JSON."""
    if request.headers.get("x-requested-with"):
        return True
    return request.headers.get("accept", "").lower().startswith("application/json")


def _chave_no_cabecalho(request: Request, chave: str) -> bool:
    enviada = request.headers.get("x-api-key") or ""
    return bool(enviada) and painel.igual(enviada, chave)


def _mesma_origem(request: Request) -> bool:
    """``Origin`` (or ``Referer``) host AND port must be this server's; neither → refused.

    Port included since 30/09/2026: the other screens of this machine (8077, 8078, 8079) are
    other origins, and none of them posts here.
    """
    origem = request.headers.get("origin") or request.headers.get("referer") or ""
    netloc_origem = urlsplit(origem).netloc
    return bool(netloc_origem) and netloc_origem == request.url.netloc


def tarefas_ativas() -> int:
    """Tasks running or queued — what a deploy must not interrupt."""
    return sum(1 for tarefa in TAREFAS.listar(limite=200) if not tarefa.terminada)


def ocupado() -> bool:
    """A task, or the Supabase write of one that just ended (seconds; ~30 s if Supabase is
    down): stopping the service now loses SAP work or the history row."""
    return bool(tarefas_ativas() or TAREFAS.gravacoes_pendentes())


def _tela_de_entrada(request: Request, *, erro: str | None, proximo: str) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "entrar.html", {"erro": erro, "proximo": painel.destino_local(proximo)}
    )


def instalar(app: FastAPI) -> None:
    """Middleware + the open routes. Called once by ``main.py`` after the routers."""

    @app.middleware("http")
    async def exigir_chave(request: Request, call_next: Any) -> Any:
        caminho = request.url.path
        if caminho in ROTAS_ABERTAS or caminho.startswith(("/static/", "/casa/")):
            return await call_next(request)
        chave, token = _chave_e_token()
        if caminho.startswith(PREFIXO_API):
            if chave and not _chave_no_cabecalho(request, chave):
                return JSONResponse(
                    {"ok": False, "tipo": "sem_chave", "motivo": "X-API-Key ausente ou incorreta."},
                    status_code=401,
                )
            return await call_next(request)
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

    # Added after the gate, so it wraps it (Starlette: the last middleware added runs first).
    app.add_middleware(_BordaDaApi)

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
        pendentes = TAREFAS.gravacoes_pendentes()
        return JSONResponse(
            {
                "ok": True,
                "servico": "controleproducao",
                "producao": s.is_production,
                "company_db": s.sl_company_db,
                "tarefas_ativas": ativas,
                "gravacoes_pendentes": pendentes,
                "ocupado": bool(ativas or pendentes),
                "chave_configurada": bool(s.os_api_key.get_secret_value()),
                # Where the Execuções screen keeps finished runs: "supabase" (the .11) or
                # "memoria" (lost on restart) — the post-deploy check reads it here.
                "historico": "supabase" if TAREFAS.historico is not None else "memoria",
            }
        )

    @app.get("/health/ocupado", response_class=PlainTextResponse)
    def health_ocupado() -> str:
        """``1`` while a task runs or waits (or its history write is in flight), else ``0`` —
        what ``deploy_update.bat`` reads before ``nssm stop`` (stopping mid-task leaves
        half-created OPs in the SAP)."""
        return "1" if ocupado() else "0"

    @app.get("/painel-wbc")
    def painel_wbc(request: Request) -> RedirectResponse:
        """The way back to the painel WBC: ``WBC_PAINEL_URL`` verbatim, or this host on
        ``PAINEL_PORTA`` (the .11 case — ``casa/destinos.py``)."""
        s = get_settings()
        destino = destinos.endereco(s.wbc_painel_url, request.url.scheme, request.url.hostname, s.painel_porta)
        return RedirectResponse(destino, status_code=302)

    @app.get("/sincronizacao")
    def sincronizacao(request: Request) -> RedirectResponse:
        """The Painel de Sincronização of the API 8077: ``SIS_PAINEL_URL`` verbatim, or this
        host on ``OS_API_PORT`` at ``/sincronizar`` (the API root bounces to the painel WBC)."""
        s = get_settings()
        destino = destinos.endereco(s.sis_painel_url, request.url.scheme, request.url.hostname,
                                    s.os_api_port, "/sincronizar")
        return RedirectResponse(destino, status_code=302)

    @app.get("/inicio")
    def inicio(request: Request) -> RedirectResponse:
        """The brand of the shared bar → the Central's home page (``/inicio`` of the API 8077,
        beside the Sincronização). Open: it only redirects; that page asks for the key."""
        s = get_settings()
        return RedirectResponse(destinos.inicio(s.sis_painel_url, request.url.scheme,
                                                request.url.hostname, s.os_api_port), status_code=302)

    @app.get("/orcaview")
    def orcaview() -> RedirectResponse:
        """The way back to the OrçaView home (``ORCAVIEW_URL``). Open: it only redirects, and
        someone who gave up on the key must still be able to leave."""
        return RedirectResponse(get_settings().orcaview_url, status_code=302)
