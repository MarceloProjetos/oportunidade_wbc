"""DBAPI2 helpers — avoids pandas read_sql UserWarning on hdbcli/pyodbc."""

from __future__ import annotations

from typing import Any, Optional, Sequence

import pandas as pd


def read_dbapi_query(
    query: str, connection: Any, params: Optional[Sequence[Any]] = None
) -> pd.DataFrame:
    """Execute SQL on a PEP-249 connection and return a DataFrame.

    Args:
        query: SQL using ``?`` placeholders if ``params`` is given (qmark style;
            supported by both hdbcli and pyodbc).
        params: placeholder values (parameterized query — prevents SQL injection).

    Note:
        No caller passes ``params`` today (the last one, ``extract_wbc_arvore``, was
        removed in the 2026-07-14 consolidation), so the parameterized branch is
        unreachable. This is **deliberate**: it is the only door to a parameterized
        query when the next ``WHERE x = ?`` shows up. Dropping it would save 3 lines
        and remove the injection defense — not worth it.
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
