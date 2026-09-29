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

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from controleproducao.core import acesso, historico
from controleproducao.core.tarefas import TAREFAS
from controleproducao.core.tarefas_router import router as tarefas_router
from controleproducao.core.templates import templates
from controleproducao.modules.manutencao_op.router import router as manutencao_op_router
from controleproducao.modules.pedidos_wbc.router import router as pedidos_wbc_router
from controleproducao.modules.romaneio.router import router as romaneio_router


@asynccontextmanager
async def _ciclo_de_vida(_app: FastAPI) -> AsyncIterator[None]:
    """Attach the Supabase history at startup (.11 only); let pending writes land on stop.

    At startup and not at import: importing the app (tests, the CLI) must not decide which
    machine this is nor read Supabase settings.
    """
    TAREFAS.historico = historico.da_maquina()
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

app.include_router(pedidos_wbc_router)
app.include_router(manutencao_op_router)
app.include_router(romaneio_router)
app.include_router(tarefas_router)

# Login gate (same cookie as the painel WBC), /health, /entrar, /painel-wbc — see core/acesso.py.
acesso.instalar(app)


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request, "home.html", {})
