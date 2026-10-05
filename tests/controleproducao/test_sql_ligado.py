"""`core/sql_ligado`: the injection boundary of the module-2 templates and the read-only guard.

`ligar` turns the C# `{name}` templates into `?` + params (F7, 29/09/2026); `exige_leitura`
is called by both readers before executing (30/09/2026). Every real query of both modules
must pass through them — the last two tests walk the query modules to prove it.
"""
import importlib

import pytest

from controleproducao.core.sql_ligado import exige_leitura, ligar, nome_de_schema

_MODULOS_DE_CONSULTA = (
    "controleproducao.modules.pedidos_wbc.queries",
    "controleproducao.modules.manutencao_op.queries",
)


def test_texto_entre_aspas_vira_parametro_texto():
    assert ligar("SELECT 1 FROM T WHERE A = '{a}'", a=84433) == ("SELECT 1 FROM T WHERE A = ?", ("84433",))


def test_sem_aspas_so_aceita_inteiro():
    assert ligar("SELECT 1 FROM T WHERE A = {a}", a="  42 ") == ("SELECT 1 FROM T WHERE A = ?", (42,))
    assert ligar("SELECT 1 FROM T WHERE A = {a}", a=7.0)[1] == (7,)
    for ruim in ("1 OR 1=1", "3.5", "", None, True, 2.5):
        with pytest.raises(ValueError):
            ligar("SELECT 1 FROM T WHERE A = {a}", a=ruim)


def test_lista_vira_marcadores_na_ordem():
    sql, params = ligar("SELECT 1 FROM T WHERE A IN ({xs}) AND B IN ({xs})", xs=["a", "b"])
    assert sql == "SELECT 1 FROM T WHERE A IN (?, ?) AND B IN (?, ?)"
    assert params == ("a", "b", "a", "b")          # repeated name: params repeat, in order
    with pytest.raises(ValueError, match="lista vazia"):
        ligar("SELECT 1 FROM T WHERE A IN ({xs})", xs=[])


def test_schema_so_identificador_e_filtro_so_vazio():
    assert ligar('SELECT 1 FROM {schema}"T"', schema='"SBOALTAMIRAPROD".')[0] == 'SELECT 1 FROM "SBOALTAMIRAPROD"."T"'
    assert ligar('SELECT 1 FROM {schema}"T"', schema="")[0] == 'SELECT 1 FROM "T"'
    for ruim in ('X"; DROP TABLE T; --', '"A B".', "SBO.", 1):
        with pytest.raises(ValueError, match="schema"):
            ligar('SELECT 1 FROM {schema}"T"', schema=ruim)
    with pytest.raises(ValueError, match="filtro"):
        ligar("SELECT 1 FROM T WHERE 1=1 {filtro}", filtro="OR 1=1")


def test_nome_faltando_ou_sobrando_e_erro_de_programacao():
    with pytest.raises(KeyError):
        ligar("SELECT 1 FROM T WHERE A = {a}")
    with pytest.raises(TypeError, match="sem lugar"):
        ligar("SELECT 1 FROM T", a=1)


def test_nome_de_schema():
    assert nome_de_schema("SBOALTAMIRAPROD") == "SBOALTAMIRAPROD"
    for ruim in ('SBO"; DROP', "SBO PROD", "", None):
        with pytest.raises(ValueError):
            nome_de_schema(ruim)


@pytest.mark.parametrize("sql", [
    "UPDATE T SET A = 1",
    "DELETE FROM T",
    "SELECT 1; DELETE FROM T",
    "SELECT * INTO X FROM T",
    "WITH C AS (SELECT 1 AS A) INSERT INTO T SELECT A FROM C",
    "EXEC sp_x",
    "CALL P()",
    "MERGE INTO T USING S ON 1=1",
])
def test_leitura_recusa_escrita(sql):
    with pytest.raises(ValueError, match="só lê"):
        exige_leitura(sql)


def test_leitura_ignora_palavras_dentro_de_texto_identificador_e_comentario():
    exige_leitura("SELECT 'a; DELETE', \"UPDATE\", [INSERT] FROM T -- DROP\nWHERE X = 'INTO'")
    exige_leitura("SELECT REPLACE(A, 'x', 'y') FROM T;")          # REPLACE is the function
    exige_leitura("  (SELECT 1 FROM T)")
    exige_leitura('SELECT "U_INO_Update", "UpdateDate" FROM "ORDR"')


def _consultas():
    for nome in _MODULOS_DE_CONSULTA:
        modulo = importlib.import_module(nome)
        for chave, valor in vars(modulo).items():
            if chave.isupper() and isinstance(valor, str) and "SELECT" in valor.upper():
                yield f"{nome.rsplit('.', 2)[-2]}.{chave}", valor


@pytest.mark.parametrize("nome,sql", list(_consultas()), ids=lambda v: v if "." in str(v) else "")
def test_toda_consulta_real_passa_na_trava_de_leitura(nome, sql):
    exige_leitura(sql)


def test_todo_modelo_do_modulo_2_liga_sem_sobrar_marca():
    """Dummy values for every name; the result must have no `{` left and match the `?` count."""
    import re

    from controleproducao.modules.pedidos_wbc import queries as q

    for chave, modelo in vars(q).items():
        if not (chave.isupper() and isinstance(modelo, str)):
            continue
        nomes = {a or b for a, b in re.findall(r"'\{(\w+)\}'|\{(\w+)\}", modelo)}
        valores = {n: ("" if n in ("schema", "filtro") else [1, 2] if n.endswith("s") else 1) for n in nomes}
        sql, params = ligar(modelo, **valores)
        assert "{" not in sql, chave
        # Some queries already carry native `?` (e.g. GET_ORCS_WBC): count only the new ones.
        assert sql.count("?") - modelo.count("?") == len(params), chave
