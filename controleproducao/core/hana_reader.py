"""Direct read-only access to HANA via `hdbcli`.

**Preferred READ path** of the project (decision revised 2026-09-16, migration_guide.md
section 6.4): reads go through here by default, both the heavy queries inherited from
`Querys.resx` and the simple ones. The Service Layer is used for reads only when this path
cannot do it, typically when the B1-assembled business object is needed rather than table
rows (e.g. an `Order` with its `DocumentLines`).

The ORIGINAL decision (2026-09-14) was the opposite: Service Layer first, `hdbcli` only as a
temporary bridge until Calculation Views replaced it. A leftover comment saying "replace with
a View when possible" comes from that rule and is obsolete.

Never use this module to WRITE: every SAP write goes through the Service Layer (the B1's own
audit, calculated-field triggers and business validations depend on it). Unchanged by the
revision. Read-only is enforced by `exige_leitura`, not by the driver (hdbcli autocommits).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from controleproducao.config import Settings
from controleproducao.core.exceptions import WbcDatabaseError
from controleproducao.core.perf import PERFIL, forma_sql
from controleproducao.core.sql_ligado import exige_leitura, nome_de_schema

if TYPE_CHECKING:
    from hdbcli import dbapi


logger = logging.getLogger(__name__)


class HanaDirectReader:
    """HANA reader that **reuses the connection** across queries.

    2026-09-16 optimization: each query used to open a new connection (full auth +
    `SET SCHEMA`). Profiling quote 00125192 showed 62 connections adding up to 3.7 s (20% of
    the total, ~60 ms each) for 0.9 s of actual queries; with reuse those 62 opens become 1.

    The connection is opened lazily on the first query (instantiating does not connect) and
    kept until `close()` or process end. If it died mid-way (server timeout, network drop),
    the next query reconnects and retries once, transparently; a SQL error on a live
    connection is raised without retry.

    Read-only: no `commit`/`rollback` and no write method; every statement passes
    `exige_leitura`.
    """

    def __init__(self, settings: Settings):
        self._settings = settings
        self._conexao: dbapi.Connection | None = None

    # ------------------------------------------------------------------
    # Conexão
    # ------------------------------------------------------------------
    def _abre_conexao(self) -> dbapi.Connection:
        # Import tardio — ver comentário equivalente em core/sqlserver_client.py.
        from hdbcli import dbapi

        s = self._settings
        try:
            with PERFIL.medir("HANA · abrir conexão", f"{s.hana_host}:{s.hana_port}"):
                conn = dbapi.connect(
                    address=s.hana_host, port=s.hana_port, user=s.hana_username, password=s.hana_password
                )
                if s.hana_schema:
                    cursor = conn.cursor()
                    # The one name pasted into SQL text: validated, since it comes from .env.
                    cursor.execute(f'SET SCHEMA "{nome_de_schema(s.hana_schema)}"')
                    cursor.close()
            return conn
        except dbapi.Error as exc:  # pragma: no cover - depende de infra real
            raise WbcDatabaseError(f"Falha ao conectar diretamente no HANA: {exc}") from exc

    def _conexao_ativa(self) -> dbapi.Connection:
        if self._conexao is None:
            self._conexao = self._abre_conexao()
        return self._conexao

    def _descarta_conexao(self) -> None:
        """Closes and forgets the current connection so the next query reopens it."""
        if self._conexao is not None:
            try:
                self._conexao.close()
            except Exception:  # noqa: BLE001 - já estamos descartando; erro aqui é irrelevante
                pass
            self._conexao = None

    def _ainda_conectado(self) -> bool:
        """hdbcli's `isconnected()`; unknown (no connection / no method / it raises) = False,
        which keeps the old reconnect-and-retry-once path."""
        try:
            return bool(self._conexao is not None and self._conexao.isconnected())
        except Exception:  # noqa: BLE001 - a broken connection may raise here too
            return False

    def close(self) -> None:
        """Closes the reused connection. The CLI calls it at the end of each command; a web
        server must call it when dropping the reader, or the connection stays open."""
        self._descarta_conexao()

    def __enter__(self) -> HanaDirectReader:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Consultas
    # ------------------------------------------------------------------
    def _consulta(self, sql: str, params: tuple[Any, ...], como_dict: bool) -> list[Any]:
        from hdbcli import dbapi

        # hdbcli autocommits by default: the guard, not the driver, keeps this read-only.
        exige_leitura(sql)

        def _executa() -> list[Any]:
            cursor = self._conexao_ativa().cursor()
            try:
                with PERFIL.medir("HANA · executar query", forma_sql(sql)):
                    cursor.execute(sql, params)
                    linhas = cursor.fetchall()
                if not como_dict:
                    return [list(linha) for linha in linhas]
                colunas = [col[0] for col in cursor.description] if cursor.description else []
                return [dict(zip(colunas, linha)) for linha in linhas]
            finally:
                cursor.close()

        try:
            return _executa()
        except dbapi.Error as exc:
            # Reconnect only when the reused connection is actually gone. A SQL/conversion
            # error on a live connection fails the same way twice: retrying it threw away a
            # good connection, paid a new login and logged a misleading "Reconectando".
            if self._ainda_conectado():
                raise WbcDatabaseError(f"Falha ao consultar o HANA: {exc}") from exc
            # A conexão reaproveitada pode ter expirado ou caído. Descarta, reconecta e
            # tenta UMA vez; se falhar de novo, o erro é de verdade e sobe.
            logger.warning("Consulta ao HANA falhou (%s). Reconectando e tentando de novo...", exc)
            self._descarta_conexao()
            try:
                return _executa()
            except dbapi.Error as exc2:  # pragma: no cover - depende de infra real
                raise WbcDatabaseError(f"Falha ao consultar o HANA: {exc2}") from exc2

    def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        return self._consulta(sql, params, como_dict=True)

    def fetch_all_values(self, sql: str, params: tuple[Any, ...] = ()) -> list[list[Any]]:
        """Like `fetch_all`, but returns each row as a positional list instead of a `dict`.

        See the `sqlserver_client.py` equivalent: queries with repeated/unaliased columns
        (e.g. the three `max(...)` in `BUSCA_MAX_ITEM_LINHA`) lose values in
        `dict(zip(columns, values))`."""
        return self._consulta(sql, params, como_dict=False)
