"""SITCOD FK validation tests."""

from unittest.mock import MagicMock

import pandas as pd

from extract_sap_to_supabase import SupabaseLoader, validate_sitcod_fk


class _FakeResponse:
    def __init__(self, data):
        self.data = data


def test_validate_sitcod_fk_nulls_unknown_codes(monkeypatch):
    loader = MagicMock(spec=SupabaseLoader)
    loader.fetch_sitcod_domain.return_value = {1, 2, 3}

    df = pd.DataFrame({'SITCOD': [1, 2, 99, None], 'x': ['a', 'b', 'c', 'd']})
    out = validate_sitcod_fk(df, loader, domain_table='situacoes_orcamento')

    assert out.loc[0, 'SITCOD'] == 1
    assert out.loc[1, 'SITCOD'] == 2
    assert pd.isna(out.loc[2, 'SITCOD'])
    assert pd.isna(out.loc[3, 'SITCOD'])


def test_validate_sitcod_fk_skips_when_domain_unavailable(monkeypatch):
    loader = MagicMock(spec=SupabaseLoader)
    loader.fetch_sitcod_domain.return_value = None

    df = pd.DataFrame({'SITCOD': [99]})
    out = validate_sitcod_fk(df, loader, domain_table='situacoes_orcamento')
    assert out.loc[0, 'SITCOD'] == 99
