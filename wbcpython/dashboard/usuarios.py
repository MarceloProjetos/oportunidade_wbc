"""Os nomes que o cartão da janela oferece no campo "Seu nome".

Duas fontes, nesta ordem:

1. os **perfis ativos do OrçaView**, pela API 8077 da própria máquina
   (`GET /usuarios-ativos`), que é quem conhece o Supabase;
2. o **histórico local** de quem já armou a janela, do banco de acompanhamento.

O painel não fala com o Supabase, e não deve passar a falar — a regra está em
`dashboard/__init__`. Por isso a lista vem por `localhost`, de um serviço que já
tem a credencial: a dependência nova é a API ao lado, não um terceiro sistema.

E por isso a fonte 2 existe: se a API estiver fora, o campo continua oferecendo
nomes em vez de virar texto livre outra vez.
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
    """Os perfis ativos, ou `[]` se a API não responder."""
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
    """Os nomes a oferecer, sem repetir, com os perfis do OrçaView na frente.

    O histórico entra **depois** e sem duplicar: quem está no OrçaView aparece
    uma vez só, e quem armou a janela com um nome que não é perfil (um "TI", um
    terceiro) continua aparecendo em vez de sumir da lista.
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
