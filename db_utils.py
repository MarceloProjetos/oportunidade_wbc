"""DBAPI2 helpers — avoids pandas read_sql UserWarning on hdbcli/pyodbc."""

from __future__ import annotations

from typing import Any, Optional, Sequence

import pandas as pd


def read_dbapi_query(
    query: str, connection: Any, params: Optional[Sequence[Any]] = None
) -> pd.DataFrame:
    """Execute SQL on a PEP-249 connection and return a DataFrame.

    Args:
        query: SQL com placeholders ``?`` se usar ``params`` (qmark; suportado por
            hdbcli e pyodbc).
        params: valores p/ os placeholders (consulta parametrizada — evita injeção).
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
