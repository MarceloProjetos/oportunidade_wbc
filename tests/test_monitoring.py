"""Monitoring/status tests (sem abrir conexões reais — checagens stubadas)."""

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

import monitoring
from config import reset_settings


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


# ===================== Nome de check inválido (regressão 2026-07-15) =====================
# Medido em produção: `?checks=sqlserver2,agendador_typo&strict=1` respondia
# 200 {"checks": {}, "healthy": true} — nenhum `if` casava, `checks` saía vazio e
# `all([])` é True. Monitor com typo na URL ficava cego reportando saúde perfeita.

def test_collect_status_rejeita_check_desconhecido():
    with pytest.raises(ValueError) as exc:
        monitoring.collect_status(only={'sqlserver2'})
    assert 'sqlserver2' in str(exc.value)
    assert 'sap' in str(exc.value)          # diz o que é válido


def test_collect_status_rejeita_mistura_valido_e_invalido(monkeypatch):
    """Um nome bom não legitima o ruim: se algo foi digitado errado, o chamador
    tem de saber — senão acha que checou SAP e o typo."""
    _stub_all_ok(monkeypatch)
    with pytest.raises(ValueError):
        monitoring.collect_status(only={'sap', 'lixo'})


def test_collect_status_aceita_subconjunto_valido(monkeypatch):
    """O caminho feliz do ?checks= não pode ter regredido."""
    _stub_all_ok(monkeypatch)
    data = monitoring.collect_status(only={'sap'})
    assert set(data['checks']) == {'sap'}


# ============ Alarme falso do agendador (regressão 2026-07-16) ============
# Dois defeitos na mesma expressão:
#  (a) limiar 35 min HARDCODED enquanto INTERVALO_MINUTOS é configurável;
#  (b) sem carência na abertura da janela: às 07:00 a última carga é a de ~18:5x de
#      ontem (~780 min) -> 'stale' -> alerta + 503 no strict, TODO dia útil.

def _sinal(monkeypatch, *, agora, ultima_carga, intervalo=30):
    """Roda _scheduler_signal com relógio e log fakes.

    `create_client` é importado DENTRO da função, então não dá para monkeypatchar o
    módulo `monitoring` — o stub tem de ir em `sys.modules['supabase']`.
    """
    import sys
    from datetime import datetime as _dt

    monkeypatch.setenv('INTERVALO_MINUTOS', str(intervalo))
    monkeypatch.setenv('SUPABASE_URL', 'https://x.supabase.co')
    monkeypatch.setenv('SUPABASE_SERVICE_ROLE_KEY', 'svc')
    monkeypatch.setenv('JANELA_HORAS', '7-18')
    reset_settings()

    class _FakeDT(_dt):
        @classmethod
        def now(cls, tz=None):
            return agora

    monkeypatch.setattr(monitoring, 'datetime', _FakeDT)

    linha = {'data_hora_sincronizacao': ultima_carga.isoformat(), 'status': 'sucesso'}
    fake_client = SimpleNamespace(table=lambda _t: SimpleNamespace(
        select=lambda *_a: SimpleNamespace(
            order=lambda *_a, **_k: SimpleNamespace(
                limit=lambda _n: SimpleNamespace(
                    execute=lambda: SimpleNamespace(data=[linha]))))))
    monkeypatch.setitem(sys.modules, 'supabase',
                        SimpleNamespace(create_client=lambda *a, **k: fake_client))
    monkeypatch.setitem(sys.modules, 'supabase.client',
                        SimpleNamespace(ClientOptions=lambda **k: None))
    return monitoring._scheduler_signal()


def test_abertura_da_janela_nao_alarma(monkeypatch):
    """07:12 numa quarta: última carga é de ontem 18:52 (~780 min). Era 'stale' e
    gritava até a 1ª execução do dia — ~30 min de alarme falso, TODO dia útil."""
    from datetime import datetime as _dt
    sinal = _sinal(monkeypatch,
                   agora=_dt(2026, 7, 15, 7, 12),          # quarta, janela recém-aberta
                   ultima_carga=_dt(2026, 7, 14, 18, 52))  # ontem à noite
    assert sinal['in_window'] is True
    assert sinal['warming_up'] is True
    assert sinal['stale'] is False, 'voltou o alarme falso das 07:00'


def test_agendador_parado_de_verdade_alarma(monkeypatch):
    """O fix não pode cegar o monitor: 11:00 sem carga desde 08:00 é sintoma real."""
    from datetime import datetime as _dt
    sinal = _sinal(monkeypatch,
                   agora=_dt(2026, 7, 15, 11, 0),
                   ultima_carga=_dt(2026, 7, 15, 8, 0))    # 180 min > 35
    assert sinal['warming_up'] is False
    assert sinal['stale'] is True


def test_limiar_deriva_do_intervalo(monkeypatch):
    """INTERVALO_MINUTOS=60: uma carga de 45 min atrás é NORMAL. Com o 35 fixo, o
    /status gritava o dia inteiro (e ?strict=1 dava 503 permanente)."""
    from datetime import datetime as _dt
    sinal = _sinal(monkeypatch,
                   agora=_dt(2026, 7, 15, 14, 0),
                   ultima_carga=_dt(2026, 7, 15, 13, 15),  # 45 min atrás
                   intervalo=60)
    assert sinal['threshold_min'] == 65          # 60 + folga, não 35
    assert sinal['stale'] is False


def test_limiar_derivado_ainda_pega_parada_real(monkeypatch):
    """Com intervalo 60, 90 min sem carga continua sendo alerta."""
    from datetime import datetime as _dt
    sinal = _sinal(monkeypatch,
                   agora=_dt(2026, 7, 15, 14, 0),
                   ultima_carga=_dt(2026, 7, 15, 12, 30),  # 90 min > 65
                   intervalo=60)
    assert sinal['stale'] is True


def test_fora_da_janela_nunca_alarma(monkeypatch):
    """22:00: não ter carga recente é o esperado (comportamento preservado)."""
    from datetime import datetime as _dt
    sinal = _sinal(monkeypatch,
                   agora=_dt(2026, 7, 15, 22, 0),
                   ultima_carga=_dt(2026, 7, 15, 18, 50))
    assert sinal['in_window'] is False and sinal['stale'] is False
