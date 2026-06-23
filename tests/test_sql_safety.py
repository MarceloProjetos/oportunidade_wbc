"""Testes de validação SQL (allow-list)."""

import pytest

from extract_sap_to_supabase import build_view_query, validate_sql_identifier


def test_validate_sql_identifier_simple():
    assert validate_sql_identifier('VIEW_NAME') == 'VIEW_NAME'


def test_validate_sql_identifier_qualified():
    assert validate_sql_identifier('WBCCAD.dbo.INTEGRACAO_ORCSIT') == 'WBCCAD.dbo.INTEGRACAO_ORCSIT'


@pytest.mark.parametrize('invalid', [
    '',
    'view;drop',
    'schema-view',
    '1col',
    "view' OR 1=1",
])
def test_validate_sql_identifier_rejects_invalid(invalid):
    with pytest.raises(ValueError):
        validate_sql_identifier(invalid)


def test_build_view_query_with_schema():
    assert build_view_query('MINHA_VIEW', 'MEU_SCHEMA') == '"MEU_SCHEMA"."MINHA_VIEW"'


def test_build_view_query_preserves_qualified_name():
    q = build_view_query('SCHEMA.VIEW')
    assert q == 'SCHEMA.VIEW'
