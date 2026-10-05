"""`sql(t"...")`: valor vira `?`, identificador é conferido, nada é colado sem regra."""

import pytest

from sql_seguro import nome_simples, sql


def test_valor_vira_parametro():
    nped = 84080
    texto, params = sql(t'SELECT * FROM "S"."V" WHERE "N_PED" = {nped}')
    assert texto == 'SELECT * FROM "S"."V" WHERE "N_PED" = ?'
    assert params == [84080]


def test_texto_malicioso_vira_parametro_e_nao_sql():
    nped = "1; DROP TABLE ORDR"
    texto, params = sql(t'WHERE "DocNum" = {nped}')
    assert texto == 'WHERE "DocNum" = ?'
    assert params == ["1; DROP TABLE ORDR"]


@pytest.mark.parametrize("nome", ['"SBOALTAMIRAPROD"."ORDR"', "ORDR", "SCHEMA.VIEW", '"S"."V_1"'])
def test_identificador_valido_e_colado(nome):
    assert sql(t"FROM {nome:ident}") == (f"FROM {nome}", [])


@pytest.mark.parametrize("nome", ['"S"; DROP', 'ORDR--', '"S"."V" x', "", "1ABC", '"S".""'])
def test_identificador_invalido_e_recusado(nome):
    with pytest.raises(ValueError, match="identificador"):
        sql(t"FROM {nome:ident}")


def test_int_e_colado_so_se_for_int():
    limite = 30
    assert sql(t"LIMIT {limite:int}") == ("LIMIT 30", [])
    for ruim in ("30", 30.0, True):
        with pytest.raises(ValueError):
            sql(t"LIMIT {ruim:int}")


def test_formato_e_conversao_desconhecidos_sao_erro():
    x = 1
    with pytest.raises(ValueError, match="formato"):
        sql(t"{x:raw}")
    with pytest.raises(ValueError, match="conversão"):
        sql(t"{x!r}")


def test_f_string_ou_str_e_recusada():
    with pytest.raises(TypeError):
        sql("SELECT 1")  # type: ignore[arg-type]


def test_concatenacao_implicita_de_t_strings():
    base, nped = '"S"."ORDR"', 7
    texto, params = sql(
        t'SELECT "CANCELED" '
        t'FROM {base:ident} WHERE "DocNum" = {nped}'
    )
    assert texto == 'SELECT "CANCELED" FROM "S"."ORDR" WHERE "DocNum" = ?'
    assert params == [7]


def test_nome_simples():
    assert nome_simples("SBOALTAMIRAPROD") == "SBOALTAMIRAPROD"
    for ruim in ('SBO"; DROP', "S.V", "", "1X", '"S"'):
        with pytest.raises(ValueError):
            nome_simples(ruim)
