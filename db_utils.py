"""DBAPI2 helpers — avoids pandas read_sql UserWarning on hdbcli/pyodbc."""

from __future__ import annotations

from typing import Any

import pandas as pd


def read_dbapi_query(query: str, connection: Any) -> pd.DataFrame:
    """Execute SQL on a PEP-249 connection and return a DataFrame."""
    cursor = connection.cursor()
    cursor.execute(query)
    if cursor.description is None:
        return pd.DataFrame()
    columns = [col[0] for col in cursor.description]
    return pd.DataFrame.from_records(cursor.fetchall(), columns=columns)
