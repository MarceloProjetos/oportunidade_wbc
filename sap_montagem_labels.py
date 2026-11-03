"""sap_montagem_labels -- single source of the "Tipo de Montagem" label.

**PORT of** ``web_orcaview_V118/backend/services/sap_montagem_labels.py`` (D1 of plan
``PLANO_SITUACAO_PEDIDOS_MCP.md (removed 2026-09-29; history in git)``). The order stores
only the code in ``ORDR.U_INO_TPO_MONTAGEM`` (``1``, ``2``, ``3``, ``5``, ``6``, ``EXP``);
the readable label is the one in the UDF's valid-values list, which lives in ``UFD1``.

**One deliberate difference from the web repo:** there :func:`get_labels` imports the HANA
client directly. Here the SAP lookup is a **hook** (:func:`registrar_fonte`) that
``situacao_pedidos_hana`` plugs in at API startup; while no source is registered (tests,
other processes) :data:`FALLBACK_LABELS` applies. That is why :func:`get_labels` stays
**outside** the diffability test -- it is I/O, like ``fetch_pedidos``. :func:`rotulo` and
:data:`FALLBACK_LABELS` are compared.

**Do not create a local label map.** Until 2026-07-27 the map was hardcoded in two places
in the web repo, knew only two codes and diverged from SAP -- 26 orders with code ``2``
fell into inconsistent free text (sometimes literally ``"."``). A third copy here is a
regression.
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
    """Plug in the real SAP lookup. ``fn(table, alias) -> [{'value','descr'}, ...]``.

    Passing ``None`` unplugs it and goes back to the fallback -- what the tests use.
    Either way the label cache is cleared.
    """
    global _fonte
    _fonte = fn
    limpar_cache()


def limpar_cache() -> None:
    """Empty the label cache (tests, and after a change in SAP)."""
    global _cache
    with _cache_lock:
        _cache = None


def get_labels() -> dict[str, str]:
    """Map ``code -> label`` of the assembly type, from SAP (or the fallback).

    Never raises: a failing lookup becomes a log line + :data:`FALLBACK_LABELS`. The
    caller is building a response; having no label is worse than yesterday's label.
    Only a real SAP answer is cached (6 h); the fallback never is.

    Returns:
        A copy of the map (the caller cannot poison the cache).
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
