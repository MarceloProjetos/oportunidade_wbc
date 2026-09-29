"""Acesso ao SQL Server externo do sistema WBC (banco `WBCCAD`).

Equivalente direto de `Controllers/SQLConnection.cs` no addon original: mesma base de
dados, mesmas tabelas `INTEGRACAO_*`, mesmo padrão de "monta string SQL e executa".

Diferença deliberada em relação ao legado (ver "Débitos técnicos", item 2, no
migration_guide.md): aqui usamos **parâmetros** (`?`) em vez de concatenar valores vindos de
tela diretamente na string SQL, para eliminar o risco de SQL injection que existia no C#
original — sem mudar o texto/lógica das queries em si.

Driver since 29/09/2026 (F7 of docs/PLANO_CONTROLE_PRODUCAO_11.md): `pymssql`, the one the
WBC worker already uses on the .11 against the same server — it replaced `pyodbc`, which
depended on the exact name of an installed ODBC driver (the .11 only has Driver 17 and the
default was 18: it bit on 28/09). Callers still write `?` and pass a tuple;
`_para_pyformat` translates to the `%s` that `pymssql` expects.

Read-only, and it has no switch (30/09/2026): every statement passes
`sql_ligado.exige_leitura`, there is no write method, and the connection is opened with
`autocommit=False` and never committed.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from controleproducao.config import Settings
from controleproducao.core.exceptions import WbcDatabaseError
from controleproducao.core.perf import PERFIL, forma_sql
from controleproducao.core.sql_ligado import exige_leitura

logger = logging.getLogger(__name__)

# A dead server must fail in seconds, not hang the task for a minute (pymssql default: 60).
_LOGIN_TIMEOUT_S = 15
# Per-query ceiling (pymssql default: 0 = forever). A query stuck on a lock would keep the
# task — and /health/ocupado — alive indefinitely; the C# addon used 300–500 s.
_QUERY_TIMEOUT_S = 600

# pymssql 2.4 substitutes `%s`/`%d` anywhere in the text (literals included) and leaves every
# other `%` untouched — so a literal `%` needs no escaping, but these two must not appear.
_MARCADOR_DO_DRIVER = re.compile(r"%[sd]")


def _para_pyformat(sql: str, quantidade_params: int) -> str:
    """Translate qmark (`?`) placeholders to pymssql's `%s`.

    Only the `?` OUTSIDE string literals, quoted/bracketed identifiers and comments are
    placeholders. A literal `%` passes as is (checked against pymssql 2.4.2's substitution
    on 30/09/2026 — doubling it reached the server as `%%`). The count must match the
    parameters: a mismatch is a programming error and is refused here, before the server
    sees anything.
    """
    if _MARCADOR_DO_DRIVER.search(sql):
        raise ValueError(f"SQL com '%s'/'%d' no texto, que o pymssql trocaria por parâmetro: {forma_sql(sql)}")
    saida: list[str] = []
    marcadores = 0
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        if c in ("'", '"', "["):
            fecha = "]" if c == "[" else c
            j = i + 1
            while j < n:
                if sql[j] == fecha:
                    # '' inside a string literal (and "" / ]] in identifiers) is an escaped quote.
                    if j + 1 < n and sql[j + 1] == fecha:
                        j += 2
                        continue
                    break
                j += 1
            saida.append(sql[i:j + 1])
            i = j + 1
            continue
        if c == "-" and sql.startswith("--", i):
            fim = sql.find("\n", i)
            fim = n if fim == -1 else fim
            saida.append(sql[i:fim])
            i = fim
            continue
        if c == "/" and sql.startswith("/*", i):
            fim = sql.find("*/", i + 2)
            fim = n if fim == -1 else fim + 2
            saida.append(sql[i:fim])
            i = fim
            continue
        if c == "?":
            saida.append("%s")
            marcadores += 1
            i += 1
            continue
        saida.append(c)
        i += 1
    if marcadores != quantidade_params:
        raise ValueError(
            f"SQL com {marcadores} marcador(es) '?' e {quantidade_params} parâmetro(s): {forma_sql(sql)}"
        )
    return "".join(saida)


class WbcSqlServerClient:
    """Espelha os métodos de `SQLConnection.cs`:

    - `ExecuteQuery`      -> `fetch_all` (the write path was removed on 30/09/2026: the
      WBC database is read-only for this package)
    - `ExecuteSelect`     -> `fetch_scalar_list`
    - `ExecuteSelectMultip` / `ExecuteSelectMultipString` -> `fetch_row`
    - `ExecuteSelectNew`, `ExecuteSelect2`, `selectORCCAB`, `PegaEstruturaPrd`,
      `PegaOrcamentosWBC` -> ficam nos módulos de negócio (`pedidos_wbc/queries.py`
      e `service.py`), pois cada um monta um objeto de domínio específico
      (`NewOrcPrd`, `ORCPRD`, `ORCCAB`, `EstruturaPrd`, `OportunidadeDoc`)
      — este cliente só executa e devolve linhas cruas (`list[dict]`).
    """

    def __init__(self, settings: Settings):
        self._settings = settings

    @contextmanager
    def _connect(self) -> Iterator[Any]:
        # Late import: the test suite locks `pymssql.connect`, and the rest of the package
        # must import without the driver installed.
        import pymssql

        s = self._settings
        try:
            with PERFIL.medir("WBC (SQL Server) · abrir conexão", s.wbc_sql_host):
                conn = pymssql.connect(
                    server=s.wbc_sql_host,
                    port=str(s.wbc_sql_port),
                    user=s.wbc_sql_username,
                    password=s.wbc_sql_password,
                    database=s.wbc_sql_database,
                    login_timeout=_LOGIN_TIMEOUT_S,
                    timeout=_QUERY_TIMEOUT_S,
                    # Explicit on purpose: nothing here commits, so even a statement that
                    # slipped past `exige_leitura` would be rolled back on close.
                    autocommit=False,
                )
        except pymssql.Error as exc:  # pragma: no cover - depende de infra real
            raise WbcDatabaseError(f"Falha ao conectar no SQL Server WBC: {exc}") from exc
        try:
            yield conn
        finally:
            conn.close()

    @staticmethod
    def _executa(cursor: Any, sql: str, params: tuple[Any, ...]) -> None:
        import pymssql

        exige_leitura(sql)
        try:
            if params:
                cursor.execute(_para_pyformat(sql, len(params)), tuple(params))
            else:
                # No parameters: pymssql does no substitution, so the text goes as is.
                _para_pyformat(sql, 0)
                cursor.execute(sql)
        except pymssql.Error as exc:
            raise WbcDatabaseError(f"Falha ao consultar o SQL Server WBC: {exc}") from exc

    def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        """Equivalente a rodar `ExecuteQuery`/`ExecuteReader` e iterar todas as linhas."""
        with self._connect() as conn:
            cursor = conn.cursor()
            with PERFIL.medir("WBC (SQL Server) · executar query", forma_sql(sql)):
                self._executa(cursor, sql, params)
                linhas = cursor.fetchall()
            columns = [col[0] for col in cursor.description] if cursor.description else []
            return [dict(zip(columns, row)) for row in linhas]

    def fetch_all_values(self, sql: str, params: tuple[Any, ...] = ()) -> list[list[Any]]:
        """Como `fetch_all`, mas devolve cada linha como lista posicional em vez de `dict`.

        ⚠️ Adicionado em 15/09/2026 ao testar `pedidos-wbc processar-novos` contra a
        homologação real: várias queries deste módulo (herdadas do C# original, que lia
        por índice via `SqlDataReader.GetValue(i)`) têm **colunas repetidas sem alias**
        (ex.: `NOVA_TABELA_QUOT`, com 35 `ISNULL(...)` seguidos sem `AS`). O driver
        devolve nomes de coluna vazios/duplicados para essas expressões, e `fetch_all`
        monta um `dict` via `zip(colunas, valores)` — colunas com nome repetido colidem
        e o `dict` resultante perde valores. Use este método em qualquer query que já
        dependia de acesso posicional."""
        with self._connect() as conn:
            cursor = conn.cursor()
            with PERFIL.medir("WBC (SQL Server) · executar query", forma_sql(sql)):
                self._executa(cursor, sql, params)
                return [list(row) for row in cursor.fetchall()]
