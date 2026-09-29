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


# ---------------------------------------------------------------------------
# One connection per execution (30/09/2026)
# ---------------------------------------------------------------------------
class _Interna:
    def __init__(self, conexao):
        self._conexao = conexao

    @property
    def connected(self):
        return not self._conexao.morta


class _CursorWbc:
    def __init__(self, conexao):
        self.conexao = conexao
        self.description = [("A",)]

    def execute(self, sql, params=None):
        import pymssql

        if self.conexao.morta:
            raise pymssql.OperationalError("DBPROCESS is dead or not enabled")
        if "ERRO_DE_SQL" in sql:
            raise pymssql.OperationalError("Invalid column name 'ERRO_DE_SQL'")
        self.conexao.consultas.append(sql)

    def fetchall(self):
        return [(1,)]

    def close(self):
        pass


class _ConexaoWbc:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.morta = False
        self.fechada = False
        self.consultas: list[str] = []
        self.rollbacks = 0
        self._conn = _Interna(self)

    def cursor(self):
        return _CursorWbc(self)

    def rollback(self):
        self.rollbacks += 1

    def commit(self):  # pragma: no cover - must never be called
        raise AssertionError("o cliente do WBC nunca faz commit")

    def close(self):
        self.fechada = True


@pytest.fixture
def wbc_falso(monkeypatch):
    """A fake `pymssql.connect` (the real one is locked by the root conftest)."""
    import pymssql

    abertas: list[_ConexaoWbc] = []

    def conectar(**kwargs):
        conexao = _ConexaoWbc(**kwargs)
        abertas.append(conexao)
        return conexao

    monkeypatch.setattr(pymssql, "connect", conectar)
    cliente = WbcSqlServerClient(MagicMock(wbc_sql_host="h", wbc_sql_port=1433))
    return cliente, abertas


def test_uma_conexao_para_muitas_consultas(wbc_falso):
    """The point: an order made one SQL Server login per structure item and per code."""
    cliente, abertas = wbc_falso
    for i in range(10):
        cliente.fetch_all("SELECT ? AS n", (i,))
        cliente.fetch_all_values("SELECT ? AS n", (i,))
    assert len(abertas) == 1 and len(abertas[0].consultas) == 20


def test_cada_consulta_termina_a_transacao(wbc_falso):
    """autocommit=False + a reused connection would keep one transaction open for minutes."""
    cliente, abertas = wbc_falso
    for _ in range(3):
        cliente.fetch_all("SELECT 1 AS ok")
    assert abertas[0].rollbacks == 3


def test_nao_conecta_ate_a_primeira_consulta_e_nem_para_sql_recusado(wbc_falso):
    cliente, abertas = wbc_falso
    assert abertas == []
    with pytest.raises(ValueError, match="só lê"):
        cliente.fetch_all("UPDATE T SET A = ?", (1,))
    with pytest.raises(ValueError, match="marcador"):
        cliente.fetch_all("SELECT ? , ?", (1,))
    assert abertas == []


def test_reconecta_uma_vez_se_a_conexao_cair(wbc_falso):
    cliente, abertas = wbc_falso
    cliente.fetch_all("SELECT 1 AS ok")
    abertas[0].morta = True
    assert cliente.fetch_all("SELECT 2 AS ok") == [{"A": 1}]   # no exception
    assert len(abertas) == 2 and abertas[0].fechada


def test_erro_de_sql_com_conexao_viva_nao_reconecta(wbc_falso):
    from controleproducao.core.exceptions import WbcDatabaseError

    cliente, abertas = wbc_falso
    cliente.fetch_all("SELECT 1 AS ok")
    with pytest.raises(WbcDatabaseError, match="Invalid column"):
        cliente.fetch_all("SELECT ERRO_DE_SQL FROM T")
    assert len(abertas) == 1 and not abertas[0].fechada
    cliente.fetch_all("SELECT 3 AS ok")                          # still usable
    assert len(abertas) == 1


def test_with_fecha_a_conexao(wbc_falso):
    _, abertas = wbc_falso
    import pymssql  # noqa: F401 - the fixture already patched connect

    with WbcSqlServerClient(MagicMock(wbc_sql_host="h", wbc_sql_port=1433)) as cliente:
        cliente.fetch_all("SELECT 1 AS ok")
    assert abertas[-1].fechada


def test_conexao_sem_autocommit_e_com_teto_por_consulta(wbc_falso):
    cliente, abertas = wbc_falso
    cliente.fetch_all("SELECT 1 AS ok")
    kwargs = abertas[0].kwargs
    assert kwargs["autocommit"] is False
    assert kwargs["timeout"] == mod._QUERY_TIMEOUT_S > 0
    assert kwargs["login_timeout"] == mod._LOGIN_TIMEOUT_S
