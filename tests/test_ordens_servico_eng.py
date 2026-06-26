"""Testes do pipeline de Ordens de Serviço (Engenharia) — sem credenciais reais."""

from unittest.mock import MagicMock

import pandas as pd
import pytest

import extract_ordens_servico_engenharia as mod
from config import get_settings


def _set_sap_env(monkeypatch):
    monkeypatch.setenv('SAP_HOST', 'host')
    monkeypatch.setenv('SAP_USER', 'user')
    monkeypatch.setenv('SAP_PASSWORD', 'pwd')
    monkeypatch.setenv('SAP_SCHEMA', 'SBOALTAMIRAPROD')


def _set_supabase_env(monkeypatch):
    monkeypatch.setenv('SUPABASE_URL', 'https://x.supabase.co')
    monkeypatch.setenv('SUPABASE_SERVICE_ROLE_KEY', 'svc')


class _FakeSAP:
    """SAPExtractor falso que grava a query e devolve um DataFrame fixo."""
    last_query = None

    def __init__(self, *a, **k):
        pass

    def connect(self):
        return True

    def execute_query(self, query):
        _FakeSAP.last_query = query
        return pd.DataFrame({'NPED': [84080], 'N_OP': [1]})

    def close(self):
        pass


def test_extract_query_uses_quoted_schema_and_int_nped(monkeypatch):
    _set_sap_env(monkeypatch)
    monkeypatch.setattr(mod, 'SAPExtractor', _FakeSAP)

    df = mod.extract_os_to_dataframe(84080)

    assert df is not None and len(df) == 1
    assert _FakeSAP.last_query == (
        'SELECT * FROM "SBOALTAMIRAPROD"."VW_EXPORT_ORDENS_SERVICO_1" '
        'WHERE "NPED" = 84080'
    )


def test_extract_accepts_numeric_string(monkeypatch):
    _set_sap_env(monkeypatch)
    monkeypatch.setattr(mod, 'SAPExtractor', _FakeSAP)

    mod.extract_os_to_dataframe('84080')
    assert _FakeSAP.last_query.endswith('WHERE "NPED" = 84080')


@pytest.mark.parametrize('bad', [
    '84080; DROP TABLE x', "1 OR 1=1", 'abc', '',
    '-5', '0', '+5', '84080.0',   # negativos/zero/sinal/decimal também são rejeitados
])
def test_extract_rejects_non_integer_nped(monkeypatch, bad):
    _set_sap_env(monkeypatch)
    monkeypatch.setattr(mod, 'SAPExtractor', _FakeSAP)
    with pytest.raises(ValueError):
        mod.extract_os_to_dataframe(bad)


def test_main_rejects_invalid_mode():
    assert mod.main(84080, execution_mode='snapshot') is False  # snapshot não é modo de OS
    assert mod.main(84080, execution_mode='nope') is False


def test_main_invalid_nped_returns_false(monkeypatch):
    _set_supabase_env(monkeypatch)
    # SupabaseLoader não deve sequer ser usado para inserir
    fake_cls = MagicMock()
    monkeypatch.setattr(mod, 'SupabaseLoader', fake_cls)
    assert mod.main('abc') is False
    fake_cls.return_value.insert_data.assert_not_called()


def test_main_empty_extraction_does_not_delete(monkeypatch):
    _set_supabase_env(monkeypatch)
    monkeypatch.setattr(mod, 'extract_os_to_dataframe', lambda nped: pd.DataFrame())
    fake_cls = MagicMock()
    inst = fake_cls.return_value
    monkeypatch.setattr(mod, 'SupabaseLoader', fake_cls)

    assert mod.main(84080) is False
    inst.insert_data.assert_not_called()
    inst.delete_other_executions.assert_not_called()


def test_replace_nped_prunes_scoped_to_nped(monkeypatch):
    _set_supabase_env(monkeypatch)
    df = pd.DataFrame({'NPED': [84080, 84080], 'N_OP': [1, 1], 'TotalOrcam': [10.5, 2.0]})
    monkeypatch.setattr(mod, 'extract_os_to_dataframe', lambda nped: df)

    fake_cls = MagicMock()
    inst = fake_cls.return_value
    inst.insert_data.return_value = True
    inst.delete_other_executions.return_value = True
    monkeypatch.setattr(mod, 'SupabaseLoader', fake_cls)

    assert mod.main(84080, execution_mode='replace_nped') is True

    inst.insert_data.assert_called_once()
    inst.delete_other_executions.assert_called_once()
    call = inst.delete_other_executions.call_args
    assert call.args[0] == get_settings().os_table_name
    assert call.kwargs['where_eq'] == {'NPED': 84080}


def _fake_sap_statuses(statuses):
    """Fábrica de um SAPExtractor falso cujo SELECT devolve os Status dados."""
    class _FS:
        def __init__(self, *a, **k):
            pass

        def connect(self):
            return True

        def execute_query(self, query):
            return pd.DataFrame({'Status': statuses})

        def close(self):
            pass
    return _FS


def test_diagnosticar_sem_os(monkeypatch):
    _set_sap_env(monkeypatch)
    monkeypatch.setattr(mod, 'SAPExtractor', _fake_sap_statuses([]))
    d = mod.diagnosticar_nped(84106)
    assert d == {'tem_os': False, 'cancelada': False, 'status': []}


def test_diagnosticar_tem_os(monkeypatch):
    _set_sap_env(monkeypatch)
    monkeypatch.setattr(mod, 'SAPExtractor', _fake_sap_statuses(['R']))
    d = mod.diagnosticar_nped(84080)
    assert d['tem_os'] is True and d['cancelada'] is False


def test_diagnosticar_cancelada(monkeypatch):
    _set_sap_env(monkeypatch)
    monkeypatch.setattr(mod, 'SAPExtractor', _fake_sap_statuses(['C']))
    d = mod.diagnosticar_nped(84080)
    assert d['tem_os'] is True and d['cancelada'] is True


def test_insert_mode_does_not_prune(monkeypatch):
    _set_supabase_env(monkeypatch)
    df = pd.DataFrame({'NPED': [84080], 'N_OP': [1]})
    monkeypatch.setattr(mod, 'extract_os_to_dataframe', lambda nped: df)

    fake_cls = MagicMock()
    inst = fake_cls.return_value
    inst.insert_data.return_value = True
    monkeypatch.setattr(mod, 'SupabaseLoader', fake_cls)

    assert mod.main(84080, execution_mode='insert') is True
    inst.insert_data.assert_called_once()
    inst.delete_other_executions.assert_not_called()
