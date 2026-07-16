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

    Note:
        Hoje **nenhum** chamador passa ``params`` (o único que passava era o
        ``extract_wbc_arvore``, removido na consolidação de 14/07), então o ramo
        parametrizado está inalcançável. É **de propósito**: é a única porta para
        consulta parametrizada quando aparecer o próximo ``WHERE x = ?``. Remover
        economizaria 3 linhas e apagaria a defesa contra injeção — não vale.
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
