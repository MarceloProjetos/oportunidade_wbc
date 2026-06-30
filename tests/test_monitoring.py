"""Monitoring/status tests (sem abrir conexões reais — checagens stubadas)."""

import monitoring


def _stub_all_ok(monkeypatch):
    monkeypatch.setattr(monitoring, '_check_sap', lambda: 'sap-ok')
    monkeypatch.setattr(monitoring, '_check_sql_server', lambda: 'sql-ok')
    monkeypatch.setattr(monitoring, '_check_supabase', lambda: 'sb-ok')
    monkeypatch.setattr(monitoring, '_scheduler_signal',
                        lambda: {'last_sync': 'x', 'minutes_ago': 5, 'stale': False, 'in_window': True})


def test_system_info_keys():
    info = monitoring._system_info()
    for k in ('hostname', 'ip', 'os', 'python', 'psutil', 'disk_low'):
        assert k in info


def test_collect_status_shape(monkeypatch):
    _stub_all_ok(monkeypatch)
    data = monitoring.collect_status()
    assert data['ok'] is True
    assert data['healthy'] is True
    assert set(data['checks']) == {'sap', 'sql_server', 'supabase'}
    assert 'ms' in data['checks']['sap']
    assert 'scheduler' in data and 'system' in data and 'uptime_s' in data
    assert data['alerts'] == []


def test_collect_status_marks_failure(monkeypatch):
    _stub_all_ok(monkeypatch)
    monkeypatch.setattr(monitoring, '_check_sap', lambda: (_ for _ in ()).throw(RuntimeError('fora do ar')))
    data = monitoring.collect_status()
    assert data['ok'] is False
    assert data['checks']['sap']['ok'] is False
    assert 'fora do ar' in data['checks']['sap']['error']


def test_checks_filter(monkeypatch):
    _stub_all_ok(monkeypatch)
    data = monitoring.collect_status(only={'sap'})
    assert set(data['checks']) == {'sap'}        # só a checagem pedida rodou
    assert 'scheduler' not in data               # scheduler não foi selecionado


def test_alert_scheduler_stale(monkeypatch):
    _stub_all_ok(monkeypatch)
    monkeypatch.setattr(monitoring, '_scheduler_signal',
                        lambda: {'minutes_ago': 52, 'stale': True, 'in_window': True})
    data = monitoring.collect_status()
    assert data['ok'] is True            # conexões ok
    assert data['healthy'] is False      # mas há alerta
    assert any('agendador' in a for a in data['alerts'])
