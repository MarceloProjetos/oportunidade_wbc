"""The WBC SQL Server client on pymssql (F7, 29/09/2026): qmark → pyformat translation.

Callers keep writing `?`; pymssql wants `%s` and %-formats the whole text, so a stray `%`
or a `?` inside a literal would corrupt the query. No test here opens a connection.
"""

import pytest

from controleproducao.core.sqlserver_client import _para_pyformat


def test_marcador_vira_percent_s():
    assert _para_pyformat("SELECT * FROM T WHERE A = ? AND B = ?", 2) == (
        "SELECT * FROM T WHERE A = %s AND B = %s"
    )


def test_percent_literal_e_escapado():
    sql = "SELECT * FROM T WHERE NOME LIKE '%x%' AND ID = ?"
    assert _para_pyformat(sql, 1) == "SELECT * FROM T WHERE NOME LIKE '%%x%%' AND ID = %s"


def test_interrogacao_dentro_de_literal_identificador_e_comentario_fica():
    sql = "SELECT 'a?b', \"c?\", [d?] FROM T -- e?\nWHERE X = ? /* f? */"
    assert _para_pyformat(sql, 1) == "SELECT 'a?b', \"c?\", [d?] FROM T -- e?\nWHERE X = %s /* f? */"


def test_aspas_escapadas_no_literal():
    assert _para_pyformat("SELECT 'it''s ?' WHERE A = ?", 1) == "SELECT 'it''s ?' WHERE A = %s"


def test_contagem_divergente_e_recusada():
    with pytest.raises(ValueError):
        _para_pyformat("SELECT ? , ?", 1)
    with pytest.raises(ValueError):
        _para_pyformat("SELECT 1", 1)
