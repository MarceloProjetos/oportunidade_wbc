"""The WBC SQL Server client on pymssql (F7, 29/09/2026): qmark → pyformat translation.

Callers keep writing `?`; pymssql wants `%s`. pymssql 2.4 substitutes `%s`/`%d` anywhere in
the text and leaves every other `%` alone (30/09/2026: the first version doubled `%`, which
reached the server as `%%`). No test here opens a connection — `substitute_params` is the
driver's own client-side substitution, callable without one.
"""

from unittest.mock import MagicMock

import pytest

from controleproducao.core import sqlserver_client as mod
from controleproducao.core.sqlserver_client import WbcSqlServerClient, _para_pyformat


def _no_servidor(sql: str, params: tuple) -> str:
    """What pymssql would send to the server for this `?` SQL."""
    from pymssql import _mssql

    return _mssql.substitute_params(_para_pyformat(sql, len(params)), params).decode()


def test_marcador_vira_percent_s():
    assert _para_pyformat("SELECT * FROM T WHERE A = ? AND B = ?", 2) == (
        "SELECT * FROM T WHERE A = %s AND B = %s"
    )


def test_percent_literal_chega_intacto_ao_servidor():
    assert _no_servidor("SELECT * FROM T WHERE NOME LIKE '%x%' AND ID = ?", ("a",)) == (
        "SELECT * FROM T WHERE NOME LIKE '%x%' AND ID = N'a'"
    )
    assert _no_servidor("SELECT ID % 2 FROM T WHERE ID = ?", (5,)) == "SELECT ID % 2 FROM T WHERE ID = 5"


def test_percent_s_ou_d_no_texto_e_recusado():
    """pymssql would take them as placeholders even inside a literal ('%d%' → wrong count)."""
    for sql in ("SELECT * FROM T WHERE A LIKE '%d%' AND B = ?", "SELECT '%s' FROM T WHERE B = ?"):
        with pytest.raises(ValueError, match="pymssql"):
            _para_pyformat(sql, 1)


def test_interrogacao_dentro_de_literal_identificador_e_comentario_fica():
    sql = "SELECT 'a?b', \"c?\", [d?] FROM T -- e?\nWHERE X = ? /* f? */"
    assert _para_pyformat(sql, 1) == "SELECT 'a?b', \"c?\", [d?] FROM T -- e?\nWHERE X = %s /* f? */"


def test_aspas_escapadas_no_literal():
    assert _para_pyformat("SELECT 'it''s ?' WHERE A = ?", 1) == "SELECT 'it''s ?' WHERE A = %s"


def test_texto_do_parametro_vai_citado():
    """Quoting is the driver's: a quote in the value cannot close the literal."""
    assert _no_servidor("SELECT * FROM T WHERE A = ?", ("x' OR '1'='1",)) == (
        "SELECT * FROM T WHERE A = N'x'' OR ''1''=''1'"
    )


def test_contagem_divergente_e_recusada():
    with pytest.raises(ValueError):
        _para_pyformat("SELECT ? , ?", 1)
    with pytest.raises(ValueError):
        _para_pyformat("SELECT 1", 1)


# ---------------------------------------------------------------------------
# Read-only by construction (30/09/2026)
# ---------------------------------------------------------------------------
def test_nao_existe_caminho_de_escrita():
    assert not hasattr(WbcSqlServerClient, "execute_non_query")


def test_escrita_e_recusada_antes_do_driver():
    cursor = MagicMock()
    for sql in ("UPDATE T SET A = ?", "SELECT 1; DELETE FROM T", "SELECT * INTO X FROM T WHERE A = ?"):
        with pytest.raises(ValueError, match="só lê"):
            WbcSqlServerClient._executa(cursor, sql, (1,) if "?" in sql else ())
    cursor.execute.assert_not_called()


def test_conexao_sem_autocommit_e_com_teto_por_consulta(monkeypatch):
    import pymssql

    chamada = {}

    def conectar(**kw):
        chamada.update(kw)
        return MagicMock()

    monkeypatch.setattr(pymssql, "connect", conectar)
    cliente = WbcSqlServerClient(MagicMock(wbc_sql_host="h", wbc_sql_port=1433))
    with cliente._connect():
        pass
    assert chamada["autocommit"] is False
    assert chamada["timeout"] == mod._QUERY_TIMEOUT_S > 0
    assert chamada["login_timeout"] == mod._LOGIN_TIMEOUT_S
