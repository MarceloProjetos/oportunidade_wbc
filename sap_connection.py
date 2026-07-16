"""Shared SAP HANA (hdbcli) connection helpers."""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, Optional

import pandas as pd
from hdbcli import dbapi

from config import (
    RETRY_ATTEMPTS,
    RETRY_BASE_DELAY_S,
    SAP_COMM_TIMEOUT_MS,
    SAP_CONNECT_TIMEOUT_MS,
)
from db_utils import read_dbapi_query
from retry import with_retries

logger = logging.getLogger(__name__)

_SAP_NOT_CONNECTED_RE = re.compile(r'not\s+connected', re.IGNORECASE)


def is_sap_tenant_error(exc: BaseException) -> bool:
    """True when hdbcli reports databaseName tenant is not connected."""
    return bool(_SAP_NOT_CONNECTED_RE.search(str(exc)))


def _with_retries(
    operation: Callable[[], Any],
    *,
    attempts: int = RETRY_ATTEMPTS,
    base_delay: float = RETRY_BASE_DELAY_S,
    what: str = 'operation',
    retry_on: Optional[Callable[[Exception], bool]] = None,
) -> Any:
    """Retry with backoff — see ``retry.with_retries``.

    Thin wrapper carrying the project defaults. This used to be a byte-identical copy of
    the ``pipeline_core`` function: changing the retry policy meant remembering both
    places, and this was the forgotten one. The implementation moved to ``retry.py``
    (dependency-free) rather than importing ``pipeline_core``, which would drag
    ``supabase``/``numpy`` (~1.2s) into the scheduler, which uses none of it.
    """
    return with_retries(
        operation, attempts=attempts, base_delay=base_delay, what=what, retry_on=retry_on,
    )


def _build_connect_args(
    host: str,
    port: int,
    user: str,
    password: str,
    database: Optional[str],
    *,
    with_timeouts: bool,
) -> dict[str, Any]:
    args: dict[str, Any] = {
        'address': host,
        'port': port,
        'user': user,
        'password': password,
        'CHARSET': 'UTF8',
    }
    if database:
        args['databaseName'] = database
    if with_timeouts:
        args['connectTimeout'] = SAP_CONNECT_TIMEOUT_MS
        args['communicationTimeout'] = SAP_COMM_TIMEOUT_MS
    return args


def connect_sap_hana(
    host: str,
    port: int,
    user: str,
    password: str,
    database: Optional[str] = None,
    *,
    with_timeouts: bool = True,
    with_retry: bool = True,
) -> Any:
    """Connect to SAP HANA; retries transient errors; falls back without databaseName on tenant error."""
    connect_args = _build_connect_args(host, port, user, password, database, with_timeouts=with_timeouts)

    def _connect(args: dict[str, Any]) -> Any:
        if with_retry:
            return _with_retries(
                lambda: dbapi.connect(**args),
                what=f'SAP HANA connection ({host}:{port})',
                retry_on=lambda exc: not is_sap_tenant_error(exc),
            )
        return dbapi.connect(**args)

    try:
        conn = _connect(connect_args)
        logger.info('Connected to SAP HANA (%s:%s)', host, port)
        return conn
    except Exception as exc:
        if database and is_sap_tenant_error(exc):
            logger.warning("Tenant '%s' not connected (%s). Retrying without databaseName...", database, exc)
            connect_args.pop('databaseName', None)
            conn = _connect(connect_args)
            logger.info('Connected to SAP HANA (%s:%s) without databaseName', host, port)
            return conn
        raise


class SAPExtractor:
    """SAP B1 (HANA) query helper."""

    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        database: Optional[str] = None,
    ) -> None:
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database
        self.connection: Optional[Any] = None

    def connect(self) -> bool:
        try:
            self.connection = connect_sap_hana(
                self.host, self.port, self.user, self.password, self.database
            )
            return True
        except Exception as exc:
            logger.error('SAP HANA connection failed: %s', exc)
            return False

    def execute_query(self, query: str) -> Optional[pd.DataFrame]:
        try:
            if not self.connection:
                raise RuntimeError('Not connected to SAP HANA')
            df = read_dbapi_query(query, self.connection)
            logger.info('Query OK: %s rows', len(df))
            return df
        except Exception as exc:
            logger.error('Query failed: %s', exc)
            return None

    def close(self) -> None:
        if self.connection:
            self.connection.close()
            self.connection = None
            logger.info('SAP HANA connection closed')
