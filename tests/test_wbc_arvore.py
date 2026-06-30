"""Testes do pipeline da árvore WBC (INTEGRACAO_ORCPRDARV) — sem credenciais reais."""

from unittest.mock import MagicMock

import pandas as pd
import pytest

import extract_wbc_arvore as mod
from config import get_settings


def _set_sap_env(monkeypatch):
    monkeypatch.setenv('SAP_HOST', 'host')
    monkeypatch.setenv('SAP_USER', 'user')
    monkeypatch.setenv('SAP_PASSWORD', 'pwd')
    monkeypatch.setenv('SAP_SCHEMA', 'SBOALTAMIRAPROD')


def _set_supabase_env(monkeypatch):
    monkeypatch.setenv('SUPABASE_URL', 'https://x.supabase.co')
    monkeypatch.setenv('SUPABASE_SERVICE_ROLE_KEY', 'svc')


@pytest.mark.parametrize('raw,esperado', [
    ('123822', '00123822'),
    ('00123822', '00123822'),
    (123822, '00123822'),
    ('  123822 ', '00123822'),
    ('', None),
    (None, None),
    ('ABC1', 'ABC1'),          # não-numérico: mantém aparado
])
def test_normaliza_orcnum(raw, esperado):
    assert mod._normaliza_orcnum(raw) == esperado


class _FakeSAP:
    """SAPExtractor falso: grava a query e devolve o NºOrçament dado."""
    last_query = None

    def __init__(self, *a, **k):
        pass

    def connect(self):
        return True

    def execute_query(self, query):
        _FakeSAP.last_query = query
        return pd.DataFrame({'NºOrçament': ['123822']})

    def close(self):
        pass


def test_resolver_orcnum_lê_numorcament_e_normaliza(monkeypatch):
    _set_sap_env(monkeypatch)
    monkeypatch.setattr(mod, 'SAPExtractor', _FakeSAP)

    orcnum = mod.resolver_orcnum(83913)

    assert orcnum == '00123822'
    assert '"NºOrçament"' in _FakeSAP.last_query
    assert 'WHERE "NPED" = 83913' in _FakeSAP.last_query


def test_main_sem_orcnum_não_carrega(monkeypatch):
    _set_supabase_env(monkeypatch)
    monkeypatch.setattr(mod, 'resolver_orcnum', lambda nped: None)
    fake_cls = MagicMock()
    monkeypatch.setattr(mod, 'SupabaseLoader', fake_cls)

    assert mod.main(83913) is False
    fake_cls.return_value.insert_data.assert_not_called()


def test_main_arvore_vazia_não_poda(monkeypatch):
    _set_supabase_env(monkeypatch)
    monkeypatch.setattr(mod, 'resolver_orcnum', lambda nped: '00123822')
    monkeypatch.setattr(mod, 'extract_arvore_to_dataframe', lambda orcnum: pd.DataFrame())
    fake_cls = MagicMock()
    inst = fake_cls.return_value
    monkeypatch.setattr(mod, 'SupabaseLoader', fake_cls)

    assert mod.main(83913) is False
    inst.insert_data.assert_not_called()
    inst.delete_other_executions.assert_not_called()


def test_main_replace_escopa_por_orcnum(monkeypatch):
    _set_supabase_env(monkeypatch)
    df = pd.DataFrame({'ORCNUM': ['00123822', '00123822'], 'PRDCOD': ['A', 'B'], 'ORCQTD': [1.0, 2.5]})
    monkeypatch.setattr(mod, 'resolver_orcnum', lambda nped: '00123822')
    monkeypatch.setattr(mod, 'extract_arvore_to_dataframe', lambda orcnum: df)

    fake_cls = MagicMock()
    inst = fake_cls.return_value
    inst.insert_data.return_value = True
    inst.delete_other_executions.return_value = True
    monkeypatch.setattr(mod, 'SupabaseLoader', fake_cls)

    assert mod.main(83913) is True
    inst.insert_data.assert_called_once()
    call = inst.delete_other_executions.call_args
    assert call.args[0] == get_settings().wbc_arvore_table
    assert call.kwargs['where_eq'] == {'ORCNUM': '00123822'}


def test_main_invalid_nped_returns_false(monkeypatch):
    _set_supabase_env(monkeypatch)
    fake_cls = MagicMock()
    monkeypatch.setattr(mod, 'SupabaseLoader', fake_cls)
    assert mod.main('abc') is False
    fake_cls.return_value.insert_data.assert_not_called()
