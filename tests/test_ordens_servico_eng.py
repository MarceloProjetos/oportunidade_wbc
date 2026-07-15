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
        return pd.DataFrame({'N_PED': [84080], 'N_OP': [1]})

    def close(self):
        pass


def test_extract_query_uses_quoted_schema_and_int_nped(monkeypatch):
    _set_sap_env(monkeypatch)
    monkeypatch.setattr(mod, 'SAPExtractor', _FakeSAP)

    df = mod.extract_os_to_dataframe(84080)

    assert df is not None and len(df) == 1
    assert _FakeSAP.last_query == (
        'SELECT * FROM "SBOALTAMIRAPROD"."VW_OS_INTEGRACAO" '
        'WHERE "N_PED" = 84080'
    )


def test_extract_accepts_numeric_string(monkeypatch):
    _set_sap_env(monkeypatch)
    monkeypatch.setattr(mod, 'SAPExtractor', _FakeSAP)

    mod.extract_os_to_dataframe('84080')
    assert _FakeSAP.last_query.endswith('WHERE "N_PED" = 84080')


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
    df = pd.DataFrame({'N_PED': [84080, 84080], 'N_OP': [1, 1], 'TotalOrcam': [10.5, 2.0]})
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
    assert call.kwargs['where_eq'] == {'N_PED': 84080}


def _fake_sap_diag(os_statuses, pedido_rows):
    """Fábrica de um SAPExtractor falso p/ o diagnóstico: responde a query da OWOR
    (lista de Status) e a da ORDR (linhas do pedido) — ``pedido_rows=None`` simula
    falha na consulta ao pedido."""
    class _FS:
        def __init__(self, *a, **k):
            pass

        def connect(self):
            return True

        def execute_query(self, query):
            if 'OWOR' in query:
                return pd.DataFrame({'Status': os_statuses})
            return None if pedido_rows is None else pd.DataFrame(pedido_rows)

        def close(self):
            pass
    return _FS


_PEDIDO_ABERTO = [{'CANCELED': 'N', 'DocStatus': 'O'}]


def test_diagnosticar_sem_os_pedido_aberto(monkeypatch):
    _set_sap_env(monkeypatch)
    monkeypatch.setattr(mod, 'SAPExtractor', _fake_sap_diag([], _PEDIDO_ABERTO))
    d = mod.diagnosticar_nped(84106)
    assert d == {'tem_os': False, 'cancelada': False, 'status': [],
                 'pedido_existe': True, 'pedido_cancelado': False,
                 'pedido_status': 'Aberto'}


def test_diagnosticar_tem_os(monkeypatch):
    _set_sap_env(monkeypatch)
    monkeypatch.setattr(mod, 'SAPExtractor', _fake_sap_diag(['R'], _PEDIDO_ABERTO))
    d = mod.diagnosticar_nped(84080)
    assert d['tem_os'] is True and d['cancelada'] is False
    assert d['pedido_status'] == 'Aberto'


def test_diagnosticar_cancelada(monkeypatch):
    _set_sap_env(monkeypatch)
    monkeypatch.setattr(mod, 'SAPExtractor', _fake_sap_diag(['C'], _PEDIDO_ABERTO))
    d = mod.diagnosticar_nped(84080)
    assert d['tem_os'] is True and d['cancelada'] is True


def test_diagnosticar_pedido_cancelado(monkeypatch):
    _set_sap_env(monkeypatch)
    monkeypatch.setattr(mod, 'SAPExtractor',
                        _fake_sap_diag([], [{'CANCELED': 'Y', 'DocStatus': 'C'}]))
    d = mod.diagnosticar_nped(84109)
    assert d['tem_os'] is False
    assert d['pedido_cancelado'] is True and d['pedido_status'] == 'Cancelado'


def test_diagnosticar_pedido_fechado(monkeypatch):
    _set_sap_env(monkeypatch)
    monkeypatch.setattr(mod, 'SAPExtractor',
                        _fake_sap_diag(['L'], [{'CANCELED': 'N', 'DocStatus': 'C'}]))
    d = mod.diagnosticar_nped(84080)
    assert d['pedido_cancelado'] is False and d['pedido_status'] == 'Fechado'


def test_diagnosticar_pedido_nao_encontrado(monkeypatch):
    _set_sap_env(monkeypatch)
    monkeypatch.setattr(mod, 'SAPExtractor', _fake_sap_diag([], []))
    d = mod.diagnosticar_nped(99999)
    assert d['tem_os'] is False
    assert d['pedido_existe'] is False and d['pedido_status'] is None


def test_diagnosticar_ordr_falha_nao_invalida_os(monkeypatch):
    """Consulta à ORDR falhou (None) → chaves pedido_* ficam None; diag da OS intacto."""
    _set_sap_env(monkeypatch)
    monkeypatch.setattr(mod, 'SAPExtractor', _fake_sap_diag(['R'], None))
    d = mod.diagnosticar_nped(84080)
    assert d['tem_os'] is True and d['cancelada'] is False
    assert d['pedido_existe'] is None and d['pedido_cancelado'] is None
    assert d['pedido_status'] is None


def test_insert_mode_does_not_prune(monkeypatch):
    _set_supabase_env(monkeypatch)
    df = pd.DataFrame({'N_PED': [84080], 'N_OP': [1]})
    monkeypatch.setattr(mod, 'extract_os_to_dataframe', lambda nped: df)

    fake_cls = MagicMock()
    inst = fake_cls.return_value
    inst.insert_data.return_value = True
    monkeypatch.setattr(mod, 'SupabaseLoader', fake_cls)

    assert mod.main(84080, execution_mode='insert') is True
    inst.insert_data.assert_called_once()
    inst.delete_other_executions.assert_not_called()


# ============ Poda falha => NÃO é sucesso (regressão 2026-07-15) ============

def test_poda_falha_nao_reporta_sucesso(monkeypatch, caplog):
    """Insert OK + poda FALHA = tabela com DUAS execuções do pedido → a leitura soma em
    dobro. Antes isto era um WARNING e `main` devolvia True: o log de sync gravava
    'sucesso' com a tabela corrompida, e ninguém ficava sabendo."""
    import logging
    _set_supabase_env(monkeypatch)
    df = pd.DataFrame({'N_PED': [84080], 'N_OP': [1], 'TotalOrcam': [10.0]})
    monkeypatch.setattr(mod, 'extract_os_to_dataframe', lambda nped: df)

    fake_cls = MagicMock()
    inst = fake_cls.return_value
    inst.insert_data.return_value = True
    inst.delete_other_executions.return_value = False      # a poda falha
    monkeypatch.setattr(mod, 'SupabaseLoader', fake_cls)

    with caplog.at_level(logging.ERROR):
        assert mod.main(84080, execution_mode='replace_nped') is False   # era True

    assert 'somar em dobro' in caplog.text                 # e diz o que fazer
    # o log de sync tem de registrar 'falha', não 'sucesso'
    status = inst.registrar_sincronizacao.call_args.args[3]
    assert status == 'falha'


def test_sync_pega_lock_do_pedido(monkeypatch):
    """A escrita tem de acontecer DENTRO do lock cross-process do N_PED."""
    _set_supabase_env(monkeypatch)
    df = pd.DataFrame({'N_PED': [84080], 'N_OP': [1]})
    monkeypatch.setattr(mod, 'extract_os_to_dataframe', lambda nped: df)

    eventos = []

    from contextlib import contextmanager

    @contextmanager
    def _lock_espiao(nped, timeout=0):
        eventos.append(('lock', nped))
        yield
        eventos.append(('unlock', nped))

    monkeypatch.setattr(mod, 'os_sync_lock', _lock_espiao)
    fake_cls = MagicMock()
    inst = fake_cls.return_value
    inst.insert_data.side_effect = lambda *a, **k: eventos.append(('insert', None)) or True
    inst.delete_other_executions.side_effect = lambda *a, **k: eventos.append(('poda', None)) or True
    monkeypatch.setattr(mod, 'SupabaseLoader', fake_cls)

    assert mod.main(84080) is True
    nomes = [e[0] for e in eventos]
    assert nomes == ['lock', 'insert', 'poda', 'unlock']   # insert+poda DENTRO do lock
    assert eventos[0][1] == 84080                          # travou o pedido certo
