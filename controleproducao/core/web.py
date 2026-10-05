"""Peças da camada web que dependem do FastAPI (22/09/2026).

Separado de `guardas.py` de propósito: lá mora a **regra** (para onde a escrita vai, e se
esta máquina pode gravar lá), pura e testável sem servidor; aqui mora só a tradução dela
para HTTP. A regra é a mesma para CLI e web — se ela vivesse dentro de um `Depends`, a CLI
não poderia usá-la e as duas camadas divergiriam com o tempo.
"""
from __future__ import annotations

import logging

from fastapi import HTTPException

from controleproducao import config as cfg
from controleproducao.config import get_settings
from controleproducao.core.guardas import ProductionWriteBlocked, ambiente_descrito, aviso_de_escrita

logger = logging.getLogger(__name__)


def avisa_escrita(operacao: str) -> str | None:
    """Gate and notice of every route that writes into the SAP.

    Called by **every** write route before it creates a task (there is a coverage test in
    `test_web_modulos.py`). Two refusals, both 503 — the code the SIS already uses for
    "write disabled by configuration" (the OP status route of the API):

    - no ``OS_API_KEY`` configured → writes are off (fail-closed: a screen nobody logs into
      must not create or close OPs in the SAP; reading stays open, like the painel WBC);
    - target is production and this is not the production machine →
      ``ProductionWriteBlocked`` from ``guardas`` (the IP rule of the SIS).

    The key is read from the real settings on purpose (``cfg.get_settings``): tests patch
    this module's ``get_settings`` with a stand-in that only describes the environment.
    """
    if not cfg.get_settings().os_api_key.get_secret_value():
        raise HTTPException(
            status_code=503,
            detail="Escrita desabilitada: OS_API_KEY não configurada no .env — a tela fica só leitura.",
        )
    try:
        aviso = aviso_de_escrita(get_settings(), operacao)
    except ProductionWriteBlocked as exc:
        logger.warning("%s", exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if aviso:
        logger.warning("%s", aviso)
    return aviso


def contexto_do_ambiente() -> dict:
    """Dados do ambiente para os templates — o alvo tem que ficar visível na tela.

    Nunca inclui credenciais: só o nome da company DB e se é produção.
    """
    settings = get_settings()
    return {
        "ambiente": ambiente_descrito(settings),
        "em_producao": settings.is_production,
        "company_db": settings.sl_company_db,
    }
