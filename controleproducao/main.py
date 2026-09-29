"""Ponto de entrada da aplicação FastAPI.

Equivalente a `Program.cs` + `Menu.cs` no addon original: aqui é onde os 4 módulos são
registrados (em vez de itens de menu do SAP B1, viram rotas/páginas web).

O módulo 1 (Integração de Oportunidades) foi **removido do porte em 22/09/2026**, por
decisão do Anderson: a tela existia no addon original mas não faz parte do fluxo que a
aplicação nova precisa cobrir. Os módulos passam a ser três, e a numeração 2/3/4 foi
mantida para continuar casando com a do addon legado e com o migration_guide.md.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from controleproducao.core import acesso, historico
from controleproducao.core.tarefas import TAREFAS
from controleproducao.core.tarefas_router import router as tarefas_router
from controleproducao.core.templates import templates
from controleproducao.modules.manutencao_op.api_router import router as manutencao_op_api_router
from controleproducao.modules.manutencao_op.router import router as manutencao_op_router
from controleproducao.modules.pedidos_wbc.router import router as pedidos_wbc_router
from controleproducao.modules.romaneio.router import router as romaneio_router
from wbcpython.dashboard import acesso as painel


@asynccontextmanager
async def _ciclo_de_vida(_app: FastAPI) -> AsyncIterator[None]:
    """Attach the Supabase history at startup (.11 only); let pending writes land on stop.

    At startup and not at import: importing the app (tests, the CLI) must not decide which
    machine this is nor read Supabase settings.
    """
    TAREFAS.usar_historico(historico.da_maquina)
    yield
    await TAREFAS.aguardar_gravacoes()


# No /docs, /redoc or /openapi.json: a screen that writes into the production SAP does not
# publish its own route catalog (SIS painel does the same).
app = FastAPI(
    title="Controle de Produção — WBC (Web)", docs_url=None, redoc_url=None, openapi_url=None,
    lifespan=_ciclo_de_vida,
)

# Caminho absoluto: ver a nota em `core/templates.py`. A app precisa subir de
# qualquer diretório de trabalho.
app.mount("/static", StaticFiles(directory=Path(__file__).resolve().parent / "static"), name="static")

# `tipo` of an HTTP error under /api/ — the contract's names (API_MANUTENCAO_OP.md). The 503
# comes from `core.web.avisa_escrita`: no OS_API_KEY, or a production write off the .11.
_TIPO_DO_ERRO_NA_API = {401: "sem_chave", 404: "nao_encontrada", 405: "metodo_invalido",
                        503: "escrita_desabilitada"}


def _erro_da_api(exc: StarletteHTTPException) -> JSONResponse:
    """``{"ok": false, "tipo", "motivo"}`` — the one error shape of the JSON API (29/09/2026)."""
    if exc.status_code == 404 and exc.detail == "Not Found":
        motivo = "Rota não encontrada nesta API."
    elif exc.status_code == 405:
        motivo = "Método não aceito nesta rota."
    else:
        motivo = exc.detail if isinstance(exc.detail, str) else "Não foi possível continuar."
    return JSONResponse(
        {"ok": False, "tipo": _TIPO_DO_ERRO_NA_API.get(exc.status_code, "erro"), "motivo": motivo},
        status_code=exc.status_code, headers=getattr(exc, "headers", None),
    )


@app.exception_handler(RequestValidationError)
async def _corpo_invalido(request: Request, exc: RequestValidationError):
    """Under /api/: a body that is not JSON is a 400 in Portuguese, not FastAPI's English 422."""
    if not request.url.path.startswith(acesso.PREFIXO_API):
        return await request_validation_exception_handler(request, exc)
    return JSONResponse(
        {"ok": False, "tipo": "invalido",
         "motivo": "Corpo inválido: envie um objeto JSON (Content-Type: application/json)."},
        status_code=400,
    )


@app.exception_handler(StarletteHTTPException)
async def _erro_http(request: Request, exc: StarletteHTTPException):
    """A browser navigation (form POST, typed URL) gets the screen's error page; everything
    else — the page's own fetches, scripts, tests — keeps FastAPI's JSON.

    Before 30/09/2026 a refused write (off the .11, or no OS_API_KEY → 503) showed the raw
    `{"detail": ...}` in the browser, right after the operator pressed "Confirmar".
    """
    if request.url.path.startswith(acesso.PREFIXO_API):
        return _erro_da_api(exc)
    navegacao = "text/html" in request.headers.get("accept", "") and not request.headers.get("x-requested-with")
    if not navegacao:
        return await http_exception_handler(request, exc)
    return templates.TemplateResponse(
        request, "erro.html",
        {"mensagem": ("Página não encontrada." if exc.status_code == 404 and exc.detail == "Not Found"
                      else exc.detail if isinstance(exc.detail, str) else "Não foi possível continuar."),
         "titulo": "Escrita recusada" if exc.status_code == 503 else None,
         # Back to the page that posted, as a LOCAL path only (the Referer is a header).
         "voltar": painel.destino_local(urlsplit(request.headers.get("referer", "")).path)},
        status_code=exc.status_code,
        headers=getattr(exc, "headers", None),
    )


app.include_router(pedidos_wbc_router)
app.include_router(manutencao_op_router)
app.include_router(manutencao_op_api_router)
app.include_router(romaneio_router)
app.include_router(tarefas_router)

# Login gate (same cookie as the painel WBC), /health, /entrar, /painel-wbc — see core/acesso.py.
acesso.instalar(app)


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request, "home.html", {})
