"""Tests for DBAPI2 query helper."""

from unittest.mock import MagicMock

from db_utils import read_dbapi_query


def test_read_dbapi_query_builds_dataframe():
    cursor = MagicMock()
    cursor.description = [('id',), ('name',)]
    cursor.fetchall.return_value = [(1, 'a'), (2, 'b')]
    conn = MagicMock()
    conn.cursor.return_value = cursor

    df = read_dbapi_query('SELECT id, name FROM t', conn)

    assert list(df.columns) == ['id', 'name']
    assert len(df) == 2
    cursor.execute.assert_called_once_with('SELECT id, name FROM t')


def test_read_dbapi_query_empty_result():
    cursor = MagicMock()
    cursor.description = None
    conn = MagicMock()
    conn.cursor.return_value = cursor

    df = read_dbapi_query('DELETE FROM t', conn)
    assert df.empty
