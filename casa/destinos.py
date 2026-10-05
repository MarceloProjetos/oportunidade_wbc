"""Where each screen of the "Central Integração SAP" lives, seen from another one.

One rule, used by the three processes (it was copied six times across ``api.py``,
``wbcpython/dashboard/web.py`` and ``controleproducao/core/acesso.py``): the address set in the
``.env`` verbatim, or the same host as the request on the screen's port — the .11 case, where
the three screens share one machine. Pure function: each app passes its own request's scheme
and host, because Flask and Starlette spell them differently.
"""
from __future__ import annotations

from urllib.parse import urlsplit


def endereco(configurado: str | None, esquema: str, host: str, porta: int, caminho: str = "/") -> str:
    """``configurado`` (``WBC_PAINEL_URL``, ``CP_URL``, ``SIS_PAINEL_URL``…) when set, else
    ``{esquema}://{host}:{porta}{caminho}``."""
    return (configurado or "").strip() or f"{esquema}://{host}:{porta}{caminho}"


def inicio(sis_painel_url: str | None, esquema: str, host: str, porta_api: int) -> str:
    """The Central's home page (``/inicio`` of the API 8077), from another screen. It lives
    beside the Sincronização, so a configured ``SIS_PAINEL_URL`` gives its host; otherwise
    the same host on ``OS_API_PORT``."""
    configurado = (sis_painel_url or "").strip()
    if configurado:
        partes = urlsplit(configurado)
        return f"{partes.scheme}://{partes.netloc}/inicio"
    return f"{esquema}://{host}:{porta_api}/inicio"


def na_tela(base: str, caminho: str) -> str:
    """A path inside a screen whose base address came from :func:`endereco`, with exactly one
    slash between them (a configured ``http://x:8080/`` must not become ``…8080//ops``)."""
    return f"{base.rstrip('/')}/{caminho.lstrip('/')}"
