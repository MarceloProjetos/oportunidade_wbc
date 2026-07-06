"""Testes do pipeline das views de impressão de OS (HANA) — sem credenciais reais."""

from unittest.mock import MagicMock

import pandas as pd
import pytest

import extract_os_impressao_views as mod
from config import OS_IMPRESSAO_VIEWS


def _set_env(monkeypatch):
    monkeypatch.setenv('SAP_HOST', 'host')
    monkeypatch.setenv('SAP_USER', 'user')
    monkeypatch.setenv('SAP_PASSWORD', 'pwd')
    monkeypatch.setenv('SAP_SCHEMA', 'SBOALTAMIRAPROD')
    monkeypatch.setenv('SUPABASE_URL', 'https://x.supabase.co')
    monkeypatch.setenv('SUPABASE_SERVICE_ROLE_KEY', 'svc')


class _FakeSAP:
    """SAPExtractor falso: grava todas as queries e devolve 1 linha com NPED."""
    queries: list = []

    def __init__(self, *a, **k):
        pass

    def connect(self):
        return True

    def execute_query(self, query):
        _FakeSAP.queries.append(query)
        return pd.DataFrame({'NPED': [84080], 'TotalOrcam': [10.5]})

    def close(self):
        pass


def test_extract_queries_cover_all_three_views(monkeypatch):
    _set_env(monkeypatch)
    _FakeSAP.queries = []
    monkeypatch.setattr(mod, 'SAPExtractor', _FakeSAP)

    frames = mod._extract_views_by_nped(84080)

    assert frames is not None
    # uma query por view, com schema entre aspas e NPED inteiro interpolado
    assert len(_FakeSAP.queries) == len(OS_IMPRESSAO_VIEWS)
    for (view_name, table_name), q in zip(OS_IMPRESSAO_VIEWS, _FakeSAP.queries):
        assert q == (
            f'SELECT * FROM "SBOALTAMIRAPROD"."{view_name}" WHERE "NPED" = 84080'
        )
        assert table_name in frames


def test_sync_loads_each_table_replace_nped(monkeypatch):
    _set_env(monkeypatch)
    _FakeSAP.queries = []
    monkeypatch.setattr(mod, 'SAPExtractor', _FakeSAP)

    fake_cls = MagicMock()
    inst = fake_cls.return_value
    inst.insert_data.return_value = True
    inst.delete_other_executions.return_value = True
    monkeypatch.setattr(mod, 'SupabaseLoader', fake_cls)

    results = mod.sync_impressao_views(84080)

    tables = [t for _, t in OS_IMPRESSAO_VIEWS]
    assert results == {t: True for t in tables}
    # inseriu e podou uma vez por view, escopando a poda ao NPED
    assert inst.insert_data.call_count == len(tables)
    assert inst.delete_other_executions.call_count == len(tables)
    for call in inst.delete_other_executions.call_args_list:
        assert call.kwargs['where_eq'] == {'NPED': 84080}
    inserted_tables = {c.args[0] for c in inst.insert_data.call_args_list}
    assert inserted_tables == set(tables)


def test_empty_view_does_not_delete(monkeypatch):
    _set_env(monkeypatch)

    class _EmptySAP(_FakeSAP):
        def execute_query(self, query):
            return pd.DataFrame()

    monkeypatch.setattr(mod, 'SAPExtractor', _EmptySAP)
    fake_cls = MagicMock()
    inst = fake_cls.return_value
    monkeypatch.setattr(mod, 'SupabaseLoader', fake_cls)

    results = mod.sync_impressao_views(84080)

    assert all(v is False for v in results.values())
    inst.insert_data.assert_not_called()
    inst.delete_other_executions.assert_not_called()


@pytest.mark.parametrize('bad', ['84080; DROP TABLE x', '1 OR 1=1', 'abc', '', '-5', '0', '84080.0'])
def test_invalid_nped_returns_all_false_no_insert(monkeypatch, bad):
    _set_env(monkeypatch)
    fake_cls = MagicMock()
    monkeypatch.setattr(mod, 'SupabaseLoader', fake_cls)
    monkeypatch.setattr(mod, 'SAPExtractor', _FakeSAP)

    results = mod.sync_impressao_views(bad)

    assert results == {t: False for _, t in OS_IMPRESSAO_VIEWS}
    fake_cls.return_value.insert_data.assert_not_called()


def test_main_true_when_any_view_syncs(monkeypatch):
    _set_env(monkeypatch)
    monkeypatch.setattr(mod, 'sync_impressao_views',
                        lambda nped: {'vw_os_exped_impressao_v2': True, 'vw_os_pintura_v0': False})
    assert mod.main(84080) is True
    monkeypatch.setattr(mod, 'sync_impressao_views', lambda nped: {'a': False, 'b': False})
    assert mod.main(84080) is False


def test_connection_failure_returns_all_false(monkeypatch):
    _set_env(monkeypatch)

    class _DeadSAP(_FakeSAP):
        def connect(self):
            return False

    monkeypatch.setattr(mod, 'SAPExtractor', _DeadSAP)
    fake_cls = MagicMock()
    monkeypatch.setattr(mod, 'SupabaseLoader', fake_cls)

    results = mod.sync_impressao_views(84080)
    assert results == {t: False for _, t in OS_IMPRESSAO_VIEWS}
    fake_cls.return_value.insert_data.assert_not_called()
