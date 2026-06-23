"""Conexão compartilhada com SAP HANA (hdbcli)."""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Callable, Optional

import pandas as pd
from hdbcli import dbapi

from config import (
    RETRY_ATTEMPTS,
    RETRY_BASE_DELAY_S,
    SAP_COMM_TIMEOUT_MS,
    SAP_CONNECT_TIMEOUT_MS,
)

logger = logging.getLogger(__name__)

_SAP_NOT_CONNECTED_RE = re.compile(r'not\s+connected', re.IGNORECASE)


def is_sap_tenant_error(exc: BaseException) -> bool:
    """Indica que o tenant SAP HANA (``databaseName``) não está conectado."""
    return bool(_SAP_NOT_CONNECTED_RE.search(str(exc)))


def _with_retries(
    operation: Callable[[], Any],
    *,
    attempts: int = RETRY_ATTEMPTS,
    base_delay: float = RETRY_BASE_DELAY_S,
    what: str = 'operação',
    retry_on: Optional[Callable[[Exception], bool]] = None,
) -> Any:
    """Executa ``operation`` com backoff exponencial (uso interno da conexão SAP)."""
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
                    f'{what}: tentativa {attempt}/{attempts} falhou ({exc}). '
                    f'Retentando em {delay:.0f}s...'
                )
                time.sleep(delay)
            else:
                logger.error(f'{what}: todas as {attempts} tentativas falharam.')
    raise last_exc  # type: ignore[misc]


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
    """Conecta ao SAP HANA com fallback sem ``databaseName`` em erro de tenant.

    Args:
        host: Host do servidor SAP HANA.
        port: Porta do servidor.
        user: Usuário.
        password: Senha.
        database: Tenant opcional (``databaseName``).
        with_timeouts: Aplica timeouts de conexão/comunicação do módulo ``config``.
        with_retry: Retenta erros transitórios com backoff.

    Returns:
        Conexão ``hdbcli`` ativa.

    Raises:
        Exception: Se todas as tentativas falharem.
    """
    connect_args = _build_connect_args(
        host, port, user, password, database, with_timeouts=with_timeouts
    )

    def _connect(args: dict[str, Any]) -> Any:
        if with_retry:
            return _with_retries(
                lambda: dbapi.connect(**args),
                what=f'conexão SAP HANA ({host}:{port})',
                retry_on=lambda exc: not is_sap_tenant_error(exc),
            )
        return dbapi.connect(**args)

    try:
        conn = _connect(connect_args)
        logger.info(f'Conectado ao SAP HANA ({host}:{port})')
        return conn
    except Exception as exc:
        if database and is_sap_tenant_error(exc):
            logger.warning(
                f"Database '{database}' não conectado ({exc}). Tentando sem databaseName..."
            )
            connect_args.pop('databaseName', None)
            conn = _connect(connect_args)
            logger.info(f'Conectado ao SAP HANA ({host}:{port}) sem databaseName')
            return conn
        raise


class SAPExtractor:
    """Extrai dados do SAP B1 (HANA) via queries SQL."""

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
        """Conecta ao SAP HANA (timeouts + retry + fallback de tenant)."""
        try:
            self.connection = connect_sap_hana(
                self.host,
                self.port,
                self.user,
                self.password,
                self.database,
            )
            return True
        except Exception as exc:
            logger.error(f'Erro ao conectar ao SAP HANA: {exc}')
            return False

    def execute_query(self, query: str) -> Optional[pd.DataFrame]:
        """Executa uma query e retorna um DataFrame."""
        try:
            if not self.connection:
                raise RuntimeError('Não conectado ao SAP HANA')
            df = pd.read_sql(query, self.connection)
            logger.info(f'Query executada com sucesso. {len(df)} linhas retornadas.')
            return df
        except Exception as exc:
            logger.error(f'Erro ao executar query: {exc}')
            return None

    def close(self) -> None:
        """Fecha a conexão com SAP HANA, se aberta."""
        if self.connection:
            self.connection.close()
            self.connection = None
            logger.info('Conexão com SAP HANA fechada')
