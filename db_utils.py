"""DBAPI2 helpers — avoids pandas read_sql UserWarning on hdbcli/pyodbc."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pandas as pd


def read_dbapi_query(
    query: str, connection: Any, params: Sequence[Any] | None = None
) -> pd.DataFrame:
    """Execute SQL on a PEP-249 connection and return a DataFrame.

    Args:
        query: SQL using ``?`` placeholders if ``params`` is given (qmark style;
            supported by both hdbcli and pyodbc).
        params: placeholder values (parameterized query — prevents SQL injection).

    Note:
        ``SAPExtractor.execute_query`` passes ``params`` since 2026-09-24 (the OS pipeline
        builds its queries with ``sql_seguro.sql(t"...")``).
    """
    cursor = connection.cursor()
    if params is None:
        cursor.execute(query)
    else:
        cursor.execute(query, params)
    if cursor.description is None:
        return pd.DataFrame()
    columns = [col[0] for col in cursor.description]
    return pd.DataFrame.from_records(cursor.fetchall(), columns=columns)
