"""The names the window card offers in the "Seu nome" field.

Two sources, in this order:

1. the **active OrçaView profiles**, through the API 8077 of the same machine
   (`GET /usuarios-ativos`), which is the one that knows the Supabase;
2. the **local history** of who already armed the window, from the tracking database.

The panel does not talk to the Supabase and must not start to (rule in `dashboard/__init__`: pages
read only the tracking database). So the list comes over `localhost` from a service that already holds
the credential: the new dependency is the neighbouring API, not a third system.

That is also why source 2 exists: if the API is down the field still offers names instead of turning
back into free text.
"""

from __future__ import annotations

import logging
import time

from wbcpython.config import Settings
from wbcpython.tracking import RepositorioTracking

logger = logging.getLogger(__name__)

#: Quanto tempo a lista fica boa sem perguntar de novo. A API já cacheia por 10
#: min; este cache evita até a ida ao localhost a cada repintura do fragmento.
VALIDADE_S = 300

#: Curto de propósito: a lista é um conforto, não um requisito. Se a API demora,
#: o cartão sai com o histórico local em vez de fazer alguém esperar por um
#: `<datalist>`.
TIMEOUT_S = 2.0

_cache: tuple[float, list[str]] | None = None


def _da_api(config: Settings) -> list[str]:
    """The active profiles, or `[]` if the API does not answer (the list is a convenience, never a requirement)."""
    import httpx

    chave = config.api_key.get_secret_value()
    try:
        resposta = httpx.get(
            f"http://127.0.0.1:{config.os_api_port}/usuarios-ativos",
            headers={"X-API-Key": chave} if chave else {},
            timeout=TIMEOUT_S,
        )
        if resposta.status_code != 200:
            logger.info(
                "Lista de usuários indisponível (HTTP %d) — o cartão usa o histórico local.",
                resposta.status_code,
            )
            return []
        return [str(n) for n in (resposta.json().get("items") or [])]
    except Exception as exc:  # noqa: BLE001 — a lista é conforto, não requisito
        logger.info(
            "Lista de usuários indisponível (%s) — o cartão usa o histórico local.", exc
        )
        return []


def sugestoes(config: Settings, repo: RepositorioTracking) -> list[str]:
    """The names to offer, without repeats, OrçaView profiles first.

    The history comes **after** and without duplicates: whoever is in the OrçaView shows up once, and whoever
    armed the window under a name that is not a profile (a "TI", a third party) still shows up instead of
    vanishing from the list. The API answer is cached for `VALIDADE_S`.
    """
    global _cache
    agora = time.time()
    if _cache is None or agora - _cache[0] >= VALIDADE_S:
        _cache = (agora, _da_api(config))
    nomes = list(_cache[1])
    conhecidos = {n.casefold() for n in nomes}
    for nome in repo.nomes_que_ja_pediram():
        if nome.casefold() not in conhecidos:
            nomes.append(nome)
            conhecidos.add(nome.casefold())
    return nomes
