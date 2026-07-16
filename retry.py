"""Exponential-backoff retry — generic, with NO heavy dependencies.

Deliberately its own module instead of living in ``pipeline_core``: ``sap_connection``
needs retry but not Supabase, and importing ``pipeline_core`` drags in
``supabase``/``numpy`` (~1.2s). A lean module lets both share the SAME policy without
making the scheduler pay for an import it never uses.

This function used to exist as two byte-identical copies (``pipeline_core.with_retries``
and ``sap_connection._with_retries``), so changing the retry policy meant remembering
both places — and the SAP one was the copy everyone forgot.
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
    """Run ``operation`` with exponential backoff (``base_delay * 2**n``).

    Args:
        operation: zero-argument callable; its return value is passed through.
        attempts: maximum number of tries.
        base_delay: wait after the 1st failure (doubles on each retry).
        what: label for the log.
        retry_on: receives the exception, returns ``True`` to retry. ``None`` = retry
            everything. Use it to NOT hammer a deterministic error (e.g. PGRST204 schema
            mismatch, SAP tenant error) — retrying only stalls and buries the real message.

    Returns:
        Whatever ``operation`` returns.

    Raises:
        The last exception, if every attempt fails (or the 1st one, if ``retry_on``
        says not to retry).
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
