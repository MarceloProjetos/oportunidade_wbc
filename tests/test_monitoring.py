"""Monitoring/status tests (sem abrir conexões reais — checagens stubadas)."""

import json
from datetime import datetime, timedelta

import monitoring


def _stub_all_ok(monkeypatch):
    monkeypatch.setattr(monitoring, '_check_sap', lambda: 'sap-ok')
    monkeypatch.setattr(monitoring, '_check_sql_server', lambda: 'sql-ok')
    monkeypatch.setattr(monitoring, '_check_supabase', lambda: 'sb-ok')
    monkeypatch.setattr(monitoring, '_scheduler_signal',
                        lambda: {'last_sync': 'x', 'minutes_ago': 5, 'stale': False, 'in_window': True})
    monkeypatch.setattr(monitoring, '_scheduled_task_signal',
                        lambda: {'available': True, 'healthy': True, 'stale': False,
                                 'task_name': 'Integração WBC', 'state': 'Ready', 'problems': []})


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


# ───────────────── Tarefa agendada "Integração WBC" (scheduled_task) ─────────────────

def test_collect_status_includes_scheduled_task(monkeypatch):
    _stub_all_ok(monkeypatch)
    data = monitoring.collect_status()
    assert data['healthy'] is True
    assert data['scheduled_task']['healthy'] is True
    assert data['alerts'] == []


def test_alert_scheduled_task_problem(monkeypatch):
    _stub_all_ok(monkeypatch)
    monkeypatch.setattr(monitoring, '_scheduled_task_signal',
                        lambda: {'available': True, 'healthy': False, 'stale': False,
                                 'task_name': 'Integração WBC',
                                 'problems': ['travada: em execucao ha 47 min (limite 10)']})
    data = monitoring.collect_status()
    assert data['ok'] is True            # conexões ok
    assert data['healthy'] is False      # mas a tarefa está ruim
    assert any('travada' in a for a in data['alerts'])
    assert data['scheduled_task']['healthy'] is False


def test_alert_scheduled_task_stale(monkeypatch):
    _stub_all_ok(monkeypatch)
    monkeypatch.setattr(monitoring, '_scheduled_task_signal',
                        lambda: {'available': True, 'healthy': True, 'stale': True,
                                 'age_min': 40, 'task_name': 'Integração WBC', 'problems': []})
    data = monitoring.collect_status()
    assert data['healthy'] is False
    assert any('desatualizado' in a for a in data['alerts'])


def test_alert_scheduled_task_missing_file(monkeypatch):
    _stub_all_ok(monkeypatch)
    monkeypatch.setattr(monitoring, '_scheduled_task_signal',
                        lambda: {'available': False, 'healthy': False,
                                 'task_name': 'Integração WBC', 'error': 'estado ausente'})
    data = monitoring.collect_status()
    assert data['healthy'] is False
    assert any('monitor da tarefa' in a for a in data['alerts'])


def test_scheduled_task_alerts_normalizes_scalar_problems():
    # ConvertTo-Json (PS 5.1) pode devolver 'problems' como string quando há 1 item.
    alerts = monitoring._scheduled_task_alerts(
        {'available': True, 'stale': False, 'task_name': 'X', 'problems': 'só um problema'}
    )
    assert alerts == ["tarefa 'X': só um problema"]


def test_scheduled_task_signal_reads_fresh_file(monkeypatch, tmp_path):
    p = tmp_path / 'wbc_task_state.json'
    p.write_text(json.dumps({
        'task_name': 'Integração WBC', 'found': True, 'healthy': True, 'problems': [],
        'checked_at': datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
    }), encoding='utf-8')
    monkeypatch.setattr(monitoring, '_wbc_task_state_path', lambda: str(p))
    sig = monitoring._scheduled_task_signal()
    assert sig['available'] is True
    assert sig['stale'] is False
    assert sig['age_min'] is not None


def test_scheduled_task_signal_marks_old_file_stale(monkeypatch, tmp_path):
    p = tmp_path / 'wbc_task_state.json'
    old = (datetime.now() - timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M:%S')
    p.write_text(json.dumps({'task_name': 'X', 'checked_at': old}), encoding='utf-8')
    monkeypatch.setattr(monitoring, '_wbc_task_state_path', lambda: str(p))
    sig = monitoring._scheduled_task_signal()
    assert sig['available'] is True
    assert sig['stale'] is True


def test_scheduled_task_signal_missing_file(monkeypatch, tmp_path):
    monkeypatch.setattr(monitoring, '_wbc_task_state_path', lambda: str(tmp_path / 'nope.json'))
    sig = monitoring._scheduled_task_signal()
    assert sig['available'] is False
    assert sig['healthy'] is False
