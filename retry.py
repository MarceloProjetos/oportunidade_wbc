"""Retry com backoff exponencial — genérico, SEM dependências pesadas.

Vive num módulo próprio (e não em ``pipeline_core``) de propósito: ``sap_connection``
precisa de retry mas não do Supabase, e ``pipeline_core`` arrasta ``supabase``/``numpy``
(~1,2 s de import). Um módulo enxuto deixa os dois usarem a MESMA política sem que o
agendador pague por um import que não usa.

Antes havia duas cópias byte-a-byte desta função (``pipeline_core.with_retries`` e
``sap_connection._with_retries``), o que significava lembrar dos dois lugares ao mexer na
política de retry — e o do SAP era o que ninguém lembrava.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


def with_retries(
    operation: Callable[[], Any],
    *,
    attempts: int,
    base_delay: float,
    what: str = 'operation',
    retry_on: Optional[Callable[[Exception], bool]] = None,
) -> Any:
    """Executa ``operation`` com backoff exponencial (``base_delay * 2**n``).

    Args:
        operation: chamável sem argumentos; seu retorno é devolvido.
        attempts: número máximo de tentativas.
        base_delay: espera após a 1ª falha (dobra a cada tentativa).
        what: rótulo para o log.
        retry_on: recebe a exceção e devolve ``True`` p/ retentar. ``None`` = retenta
            tudo. Use para NÃO insistir em erro determinístico (ex.: PGRST204 de schema,
            erro de tenant do SAP) — retentar só atrasa e afoga a mensagem real.

    Returns:
        O retorno de ``operation``.

    Raises:
        A última exceção, se todas as tentativas falharem (ou na 1ª, se ``retry_on``
        disser que não é para retentar).
    """
    last_exc: Optional[Exception] = None
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except Exception as exc:
            if retry_on is not None and not retry_on(exc):
                raise
            last_exc = exc
            if attempt < attempts:
                delay = base_delay * (2 ** (attempt - 1))
                logger.warning(
                    "%s: tentativa %s/%s falhou (%s). Retentando em %.0fs...",
                    what, attempt, attempts, exc, delay,
                )
                time.sleep(delay)
            else:
                logger.error("%s: todas as %s tentativas falharam.", what, attempts)
    raise last_exc  # type: ignore[misc]
