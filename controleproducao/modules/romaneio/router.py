"""Rotas web do módulo Romaneio — placeholder (ver service.py)."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from controleproducao.core.templates import templates

router = APIRouter(prefix="/romaneio", tags=["romaneio"])


@router.get("", response_class=HTMLResponse)
async def pagina_inicial(request: Request):
    return templates.TemplateResponse(
        request,
        "em_construcao.html",
        {
            "titulo": "Romaneio",
            "prioridade": 4,
            "referencia": "Views/Romaneio.b1f.cs",
        },
    )
