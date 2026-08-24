"""sap_montagem_labels -- fonte unica do rotulo "Tipo de Montagem".

**PORTE de** ``web_orcaview_V117/backend/services/sap_montagem_labels.py`` (D1 do plano
``docs/PLANO_SITUACAO_PEDIDOS_MCP.md``). O pedido guarda so o codigo em
``ORDR.U_INO_TPO_MONTAGEM`` (``1``, ``2``, ``3``, ``5``, ``6``, ``EXP``); o rotulo legivel
e' o da lista de valores validos da UDF, que mora em ``UFD1``.

**Uma diferenca deliberada em relacao ao V117:** la o :func:`get_labels` importa o cliente
HANA direto. Aqui a busca no SAP e' um **gancho** (:func:`registrar_fonte`) que a F2 liga
quando a leitura HANA existir; ate la vale o :data:`FALLBACK_LABELS`. Por isso
:func:`get_labels` fica **fora** do teste de diffabilidade -- e' I/O, como o
``fetch_pedidos``. :func:`rotulo` e :data:`FALLBACK_LABELS`, esses sim, sao comparados.

**Nao criar um mapa local de rotulos.** Ate 2026-07-27 o mapa estava chumbado em dois
lugares no V117, so conhecia dois codigos e divergia do SAP -- 26 pedidos com o codigo
``2`` cairam em texto livre inconsistente (as vezes literalmente ``"."``). Uma terceira
copia aqui e' regressao.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

logger = logging.getLogger(__name__)

__all__ = [
    "ALIAS_UDF",
    "FALLBACK_LABELS",
    "SEM_MONTAGEM",
    "TABELA_UDF",
    "get_labels",
    "limpar_cache",
    "registrar_fonte",
    "rotulo",
]

#: Tabela e alias da UDF no B1 (``ORDR.U_INO_TPO_MONTAGEM``). O alias NAO leva o prefixo
#: ``U_``; a lista de valores e' por tabela (o mesmo alias existe em ~30).
TABELA_UDF = "ORDR"
ALIAS_UDF = "INO_TPO_MONTAGEM"

#: Rede de seguranca: os valores validos medidos em producao (2026-07-27).
#: So entra em acao quando o HANA nao responde -- tela sem rotulo seria pior.
FALLBACK_LABELS: dict[str, str] = {
    "1": "MONTAGEM ESPECIAL",
    "2": "MONTAGEM POR CONTA DA ALTAMIRA",
    "3": "MONTAGEM POR CONTA DE TERCEIROS",
    "5": "MONTAGEM POR CONTA DO CLIENTE",
    "6": "LATERAL MONTADA",
    "EXP": "SEM MONTAGEM",
}

#: Exibido quando nao ha codigo nem texto livre aproveitavel.
SEM_MONTAGEM = "SEM MONTAGEM"

#: Texto livre que o SAP guarda como "vazio" em ``U_INO_TIPO_MT``.
_TEXTO_VAZIO = "."

#: 6h: a lista e' configuracao do B1. TTL curto so somaria ida e volta inutil; TTL
#: infinito exigiria restart para refletir uma mudanca no SAP.
_CACHE_TTL_SECONDS = 6 * 3600

_cache_lock = threading.Lock()
#: ``(timestamp, labels)`` -- so gravado quando o SAP realmente responde; fallback nunca
#: e' cacheado (senao 1s de HANA fora congelaria o fallback por 6 horas).
_cache: tuple[float, dict[str, str]] | None = None

#: Quem sabe perguntar ao SAP. ``None`` = ninguem ligou ainda (estado da F1) -- ver o
#: docstring do modulo. A F2 chama :func:`registrar_fonte`.
_fonte: Callable[[str, str], list[dict[str, str]]] | None = None


def registrar_fonte(fn: Callable[[str, str], list[dict[str, str]]] | None) -> None:
    """Liga a busca real no SAP (F2). ``fn(tabela, alias) -> [{'value','descr'}, ...]``.

    Passar ``None`` desliga e volta ao fallback -- e' o que os testes usam.
    """
    global _fonte
    _fonte = fn
    limpar_cache()


def limpar_cache() -> None:
    """Esvazia o cache de rotulos (testes e apos mudanca no SAP)."""
    global _cache
    with _cache_lock:
        _cache = None


def get_labels() -> dict[str, str]:
    """Mapa ``codigo -> rotulo`` do tipo de montagem, do SAP (ou do fallback).

    Nunca levanta: qualquer falha vira log + :data:`FALLBACK_LABELS`. Quem chama esta
    montando uma resposta; ficar sem rotulo e' pior que o rotulo de ontem.

    Returns:
        Uma copia do mapa (quem chama nao envenena o cache).
    """
    global _cache

    agora = time.monotonic()
    with _cache_lock:
        if _cache and (agora - _cache[0]) < _CACHE_TTL_SECONDS:
            return dict(_cache[1])

    if _fonte is None:
        return dict(FALLBACK_LABELS)

    try:
        valores = _fonte(TABELA_UDF, ALIAS_UDF)
    except Exception as e:  # SAP fora, driver ausente, disjuntor aberto...
        logger.warning("[MONTAGEM] rótulos do SAP indisponíveis (%s) — usando fallback.", e)
        return dict(FALLBACK_LABELS)

    labels = {v["value"]: v["descr"] for v in valores if v.get("value") and v.get("descr")}
    if not labels:
        # UDF sem lista de valores validos: pode ser mudanca de cadastro no B1.
        # Nao cacheia -- a proxima chamada tenta de novo.
        logger.warning(
            "[MONTAGEM] %s.U_%s não devolveu valores válidos — usando fallback.",
            TABELA_UDF, ALIAS_UDF,
        )
        return dict(FALLBACK_LABELS)

    with _cache_lock:
        _cache = (time.monotonic(), labels)
    return dict(labels)


def rotulo(tipo_cod: str | None, tipo_texto: str | None = None) -> str:
    """Display label of the assembly type.

    Order: the code's label (SAP) → free text ``U_INO_TIPO_MT`` (ignoring the
    ``"."`` SAP stores as empty) → :data:`SEM_MONTAGEM`. The free text survives as
    the 2nd step because old orders carry a real note there ("Seguirá 01 lateral
    pré-montado"), which beats "SEM MONTAGEM".

    Args:
        tipo_cod: ``ORDR.U_INO_TPO_MONTAGEM``.
        tipo_texto: ``ORDR.U_INO_TIPO_MT`` (optional).
    """
    cod = str(tipo_cod or "").strip()
    if cod:
        label = get_labels().get(cod)
        if label:
            return label

    texto = str(tipo_texto or "").strip()
    if texto and texto != _TEXTO_VAZIO:
        return texto
    return SEM_MONTAGEM
