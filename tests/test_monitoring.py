"""Monitoring/status tests (sem abrir conexões reais — checagens stubadas)."""

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

import monitoring
from config import reset_settings


def _wu_ok() -> dict:
    """Bloco windows_update de uma máquina saudável (varreu hoje, sem reboot pendente)."""
    return {'reboot_pendente': {'pendente': False, 'motivos': [], 'erro': None},
            'estado': 'ok', 'patching_automatico': True, 'pendentes': 3,
            'pendentes_motivo': None, 'ultima_varredura': '2026-07-16T06:20:00',
            'ultimo_patch': '2026-06-30', 'dias_sem_patch': 16}


def _wbc_ok() -> dict:
    """Bloco wbc_worker de um worker vivo (ciclo concluído há 2 min, dentro do expediente)."""
    return {'available': True, 'installed': True, 'healthy': True, 'in_window': True,
            'threshold_min': 10, 'interval_s': 180, 'db': 'state/wbc_tracking.db',
            'last': {'id': 725, 'status': 'concluida', 'detalhe': ''}, 'running': False,
            'minutes_ago': 2, 'stale': False, 'stuck': False, 'tabelas_faltando': [],
            'hoje': {'execucoes': 120, 'falhas': 0}}


def _stub_all_ok(monkeypatch, *, stub_wu=True, stub_wbc=True):
    """Deixa todas as checagens verdes. `stub_wu=False` só para quem testa o próprio
    `_windows_update_signal` (aí ele precisa rodar de verdade); idem `stub_wbc=False`
    para quem testa `_wbc_worker_signal` contra um SQLite de verdade."""
    if stub_wbc:
        monkeypatch.setattr(monitoring, '_wbc_worker_signal', _wbc_ok)
    monkeypatch.setattr(monitoring, '_check_sap', lambda: 'sap-ok')
    monkeypatch.setattr(monitoring, '_check_sql_server', lambda: 'sql-ok')
    monkeypatch.setattr(monitoring, '_check_supabase', lambda: 'sb-ok')
    monkeypatch.setattr(monitoring, '_scheduler_signal',
                        lambda: {'last_sync': 'x', 'minutes_ago': 5, 'stale': False, 'in_window': True})
    monkeypatch.setattr(monitoring, '_scheduled_task_signal',
                        lambda: {'available': True, 'healthy': True, 'stale': False,
                                 'task_name': 'Integração WBC', 'state': 'Ready', 'problems': []})
    # OBRIGATÓRIO stubar: sem isto `_windows_update_signal` lê o winreg REAL da máquina que
    # roda a suíte. ESTA MÁQUINA (.11) TEM REBOOT PENDENTE — os testes que exigem
    # `alerts == []` quebrariam por causa de um fato do ambiente, não do código. Pior: a
    # "correção" óbvia (apagar o alerta de reboot) deixaria a suíte verde violando a D1 —
    # ou seja, a suíte premiaria quebrar a regra que ela existe para proteger. Foi
    # exatamente o que a revisão adversarial da F1 pegou na .12.
    if stub_wu:
        monkeypatch.setattr(monitoring, '_windows_update_signal', _wu_ok)


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
    assert 'windows_update' not in data          # nem o windows_update


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


def _monitor_ligado(monkeypatch):
    """Os testes do monitor de verdade precisam religa-lo: desde 08/09/2026 ele nasce desligado."""
    monkeypatch.setenv('WBC_TASK_MONITOR', 'true')
    reset_settings()


def test_scheduled_task_signal_reads_fresh_file(monkeypatch, tmp_path):
    _monitor_ligado(monkeypatch)
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
    _monitor_ligado(monkeypatch)
    p = tmp_path / 'wbc_task_state.json'
    old = (datetime.now() - timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M:%S')
    p.write_text(json.dumps({'task_name': 'X', 'checked_at': old}), encoding='utf-8')
    monkeypatch.setattr(monitoring, '_wbc_task_state_path', lambda: str(p))
    sig = monitoring._scheduled_task_signal()
    assert sig['available'] is True
    assert sig['stale'] is True


def test_scheduled_task_signal_missing_file(monkeypatch, tmp_path):
    _monitor_ligado(monkeypatch)
    monkeypatch.setattr(monitoring, '_wbc_task_state_path', lambda: str(tmp_path / 'nope.json'))
    sig = monitoring._scheduled_task_signal()
    assert sig['available'] is False
    assert sig['healthy'] is False


# ───────────────── Windows Update / reboot pendente (check windows_update) ─────────────────
# Plano: ../SAP_RDP/docs/PLANO_WINDOWS_UPDATE.md. O que estes testes protegem:
#  - D1: SÓ reboot vira alerta (update pendente é rotina; alerta crônico não é lido);
#  - invariante 1: `pendentes: null` NUNCA pode virar 0;
#  - invariante 2: erro de leitura NUNCA pode virar "sem reboot pendente";
#  - o /status não paga os 3,1s da busca (ela roda na thread do windows_update.py).

def test_collect_status_inclui_windows_update(monkeypatch):
    _stub_all_ok(monkeypatch)
    data = monitoring.collect_status()
    assert data['windows_update']['pendentes'] == 3
    assert data['windows_update']['reboot_pendente']['pendente'] is False
    assert data['healthy'] is True       # update pendente NÃO é alerta (D1)
    assert data['alerts'] == []


def test_windows_update_nunca_gera_alerta(monkeypatch):
    """Windows Update é INFORMAÇÃO, não saúde do sistema (Marcelo, 2026-07-16).

    Reverte a D1 original (que fazia reboot pendente virar alerta). Motivo: "se um dia o
    servidor não reiniciar não importa" — o monitor não pode dizer que a integração do SAP
    está ruim por causa de um reboot pendente. Nem o pior caso acende alarme: reboot
    pendente + 47 updates + 90 dias sem patch, e `healthy` continua True.

    Este é o único ponto em que o Windows Update tocaria o comportamento de quem monitora
    (alerta derruba `healthy` e faz o ?strict=1 responder 503). Ele fica fechado.
    """
    _stub_all_ok(monkeypatch)
    monkeypatch.setattr(monitoring, '_windows_update_signal',
                        lambda: {**_wu_ok(), 'pendentes': 47, 'dias_sem_patch': 90,
                                 'reboot_pendente': {'pendente': True,
                                                     'motivos': ['PendingFileRenameOperations(32)'],
                                                     'erro': None}})
    data = monitoring.collect_status()
    assert data['alerts'] == [], 'Windows Update não pode gerar alerta'
    assert data['healthy'] is True, 'reboot/update pendente não pode degradar a integração'
    # mas o dado continua publicado, para quem perguntar
    assert data['windows_update']['reboot_pendente']['pendente'] is True
    assert data['windows_update']['pendentes'] == 47


def test_api_auth_informativo_nunca_gera_alerta(monkeypatch):
    """`api_auth.api_key_configurada` é INFORMAÇÃO, não saúde (Marcelo, 2026-08-31).

    Sem OS_API_KEY a API cai aberta (fail-open) e só o log do boot avisa; este campo
    torna isso visível no /status. Mas NUNCA vira alerta: alerta derruba `healthy` e faz
    o ?strict=1 responder 503 — acordaria o vigia por um problema de config que não
    afeta a integração. Mesmo desenho do bloco windows_update.
    """
    _stub_all_ok(monkeypatch)
    monkeypatch.delenv('OS_API_KEY', raising=False)
    reset_settings()
    data = monitoring.collect_status()
    assert data['api_auth'] == {'api_key_configurada': False}
    assert data['alerts'] == [], 'API sem chave não pode gerar alerta'
    assert data['healthy'] is True, 'API sem chave não pode degradar a integração'

    monkeypatch.setenv('OS_API_KEY', 'k')
    reset_settings()
    data = monitoring.collect_status()
    assert data['api_auth'] == {'api_key_configurada': True}


def test_windows_update_signal_junta_reboot_e_updates(monkeypatch):
    """O bloco publicado = reboot (winreg, fresco) + estado do cache, num objeto só."""
    monkeypatch.setattr(monitoring.windows_update, 'reboot_pendente',
                        lambda: {'pendente': False, 'motivos': [], 'erro': None})
    monkeypatch.setattr(monitoring.windows_update, 'estado_updates',
                        lambda: {'estado': 'ok', 'pendentes': 3, 'patching_automatico': True})
    bloco = monitoring._windows_update_signal()
    assert bloco['reboot_pendente']['pendente'] is False
    assert bloco['pendentes'] == 3 and bloco['estado'] == 'ok'


def test_windows_update_signal_nao_dispara_powershell(monkeypatch):
    """O /status não pode pagar os 3,1s da busca — a thread de background já pagou."""
    def _boom(*_a, **_k):
        raise AssertionError('o /status disparou PowerShell — isso trava o endpoint de saúde')

    monkeypatch.setattr(monitoring.windows_update.subprocess, 'run', _boom)
    monitoring._windows_update_signal()   # só winreg + cache; não pode estourar


def test_falha_no_windows_update_nao_derruba_o_status(monkeypatch):
    """Este bloco não pode virar 500 e levar SAP/SQL/Supabase junto — e "não sei"
    NUNCA vira "sem reboot pendente"/"0 pendentes"."""
    _stub_all_ok(monkeypatch, stub_wu=False)    # o _windows_update_signal REAL tem de rodar
    monkeypatch.setattr(monitoring.windows_update, 'reboot_pendente',
                        lambda: (_ for _ in ()).throw(RuntimeError('winreg explodiu')))
    data = monitoring.collect_status()          # não levanta
    wu = data['windows_update']
    assert wu['reboot_pendente']['pendente'] is None    # não sei != sem reboot
    assert wu['pendentes'] is None                      # não sei != 0
    assert 'winreg explodiu' in wu['reboot_pendente']['erro']
    assert data['alerts'] == []                 # erro de leitura não inventa alerta
    assert data['checks']['sap']['ok'] is True  # o resto do /status sobreviveu


def test_checks_filter_isola_windows_update(monkeypatch):
    """?checks=windows_update responde sem abrir as 3 conexões de teste — é o que a tool
    MCP `estado_windows_update` usa."""
    _stub_all_ok(monkeypatch)
    monkeypatch.setattr(monitoring, '_check_sap',
                        lambda: (_ for _ in ()).throw(AssertionError('não devia conectar no SAP')))
    data = monitoring.collect_status(only={'windows_update'})
    assert data['checks'] == {}
    assert data['windows_update']['pendentes'] == 3
    assert 'scheduler' not in data and 'scheduled_task' not in data


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


# ============================== wbc_worker (Integração WBC → SAP) ===========================

import os  # noqa: E402
import sqlite3  # noqa: E402


def _banco_wbc(caminho, execucoes=()):
    """Cria o SQLite do acompanhamento com as 4 tabelas (o esquema mínimo que o check lê)."""
    con = sqlite3.connect(caminho)
    con.executescript("""
        CREATE TABLE acompanhamento (orcnum TEXT PRIMARY KEY);
        CREATE TABLE eventos (id INTEGER PRIMARY KEY, orcnum TEXT);
        CREATE TABLE execucoes (id INTEGER PRIMARY KEY, inicio TEXT, fim TEXT, status TEXT,
                                processados INT, sucessos INT, erros INT, detalhe TEXT);
        CREATE TABLE travas (nome TEXT PRIMARY KEY);
    """)
    for e in execucoes:
        con.execute('INSERT INTO execucoes VALUES (?,?,?,?,?,?,?,?)', e)
    con.commit()
    con.close()


def _apontar(monkeypatch, caminho):
    monkeypatch.setenv('TRACKING_DB_URL', f'sqlite:///{caminho}')
    reset_settings()


def _carimbo(minutos_atras: int) -> str:
    return (datetime.now() - timedelta(minutes=minutos_atras)).strftime('%Y-%m-%d %H:%M:%S.%f')


def test_wbc_worker_sem_banco_e_informacao_nao_alerta(monkeypatch, tmp_path):
    """A .11 antes da virada: a integração nunca rodou ali. Alertar aqui viraria 503 no
    ?strict=1 por um serviço que ninguém ligou."""
    _apontar(monkeypatch, tmp_path / 'nao_existe.db')
    w = monitoring._wbc_worker_signal()
    assert w['installed'] is False and w['available'] is False and w['healthy'] is None
    assert 'nunca rodou' in w['note']
    assert monitoring._wbc_worker_alerts(w) == []


def test_wbc_worker_sem_banco_nao_derruba_o_healthy_do_status(monkeypatch, tmp_path):
    _stub_all_ok(monkeypatch, stub_wbc=False)
    _apontar(monkeypatch, tmp_path / 'nao_existe.db')
    data = monitoring.collect_status()
    assert data['healthy'] is True and data['alerts'] == []
    assert data['wbc_worker']['installed'] is False


def test_wbc_worker_banco_criado_mas_nunca_rodou(monkeypatch, tmp_path):
    """O painel cria as tabelas antes do primeiro ciclo: informação, sem alerta."""
    _banco_wbc(tmp_path / 't.db')
    _apontar(monkeypatch, tmp_path / 't.db')
    w = monitoring._wbc_worker_signal()
    assert w['installed'] and w['available'] and w['last'] is None and w['healthy'] is None
    assert monitoring._wbc_worker_alerts(w) == []


def test_wbc_worker_ciclo_recente_e_saudavel(monkeypatch, tmp_path):
    _banco_wbc(tmp_path / 't.db', [(725, _carimbo(3), _carimbo(2), 'concluida', 1681, 1681, 0,
                                    '1681 orçamento(s) avaliado(s); nenhum erro.')])
    _apontar(monkeypatch, tmp_path / 't.db')
    monkeypatch.setattr(monitoring, '_wbc_worker_in_window', lambda now: True)
    w = monitoring._wbc_worker_signal()
    assert w['healthy'] is True and w['stale'] is False and w['minutes_ago'] == 2
    assert w['last']['id'] == 725 and w['hoje']['execucoes'] == 1
    assert monitoring._wbc_worker_alerts(w) == []


def test_wbc_worker_silencio_no_expediente_alerta_e_nomeia_o_servico(monkeypatch, tmp_path):
    _banco_wbc(tmp_path / 't.db', [(1, _carimbo(41), _carimbo(40), 'concluida', 1, 1, 0, '')])
    _apontar(monkeypatch, tmp_path / 't.db')
    monkeypatch.setattr(monitoring, '_wbc_worker_in_window', lambda now: True)
    w = monitoring._wbc_worker_signal()
    assert w['stale'] is True and w['healthy'] is False
    alertas = monitoring._wbc_worker_alerts(w)
    assert len(alertas) == 1 and 'OrcaView-WBC-Worker' in alertas[0] and '40 min' in alertas[0]


def test_wbc_worker_silencio_fora_do_expediente_nao_alerta(monkeypatch, tmp_path):
    _banco_wbc(tmp_path / 't.db', [(1, _carimbo(600), _carimbo(599), 'concluida', 1, 1, 0, '')])
    _apontar(monkeypatch, tmp_path / 't.db')
    monkeypatch.setattr(monitoring, '_wbc_worker_in_window', lambda now: False)
    w = monitoring._wbc_worker_signal()
    assert w['stale'] is False and w['healthy'] is True
    assert monitoring._wbc_worker_alerts(w) == []


def test_wbc_worker_ultima_falhou_alerta(monkeypatch, tmp_path):
    _banco_wbc(tmp_path / 't.db', [(9, _carimbo(3), _carimbo(2), 'falhou', 0, 0, 1, 'HANA fora do ar')])
    _apontar(monkeypatch, tmp_path / 't.db')
    monkeypatch.setattr(monitoring, '_wbc_worker_in_window', lambda now: True)
    w = monitoring._wbc_worker_signal()
    assert w['healthy'] is False
    assert any('#9' in a and 'HANA fora do ar' in a for a in monitoring._wbc_worker_alerts(w))


def test_wbc_worker_ciclo_preso_alerta_mesmo_fora_do_expediente(monkeypatch, tmp_path):
    """`em_andamento` sem `fim` além do limite: o processo morreu no meio (ou está preso)."""
    _banco_wbc(tmp_path / 't.db', [(3, _carimbo(30), None, 'em_andamento', 0, 0, 0, '')])
    _apontar(monkeypatch, tmp_path / 't.db')
    monkeypatch.setattr(monitoring, '_wbc_worker_in_window', lambda now: False)
    w = monitoring._wbc_worker_signal()
    assert w['running'] is True and w['stuck'] is True and w['healthy'] is False
    assert any('#3' in a for a in monitoring._wbc_worker_alerts(w))


def test_wbc_worker_banco_sem_tabelas_alerta(monkeypatch, tmp_path):
    sqlite3.connect(tmp_path / 'vazio.db').close()
    _apontar(monkeypatch, tmp_path / 'vazio.db')
    w = monitoring._wbc_worker_signal()
    assert w['healthy'] is False and 'execucoes' in w['error']
    assert monitoring._wbc_worker_alerts(w)


def test_wbc_worker_entra_no_status_e_respeita_o_filtro(monkeypatch):
    _stub_all_ok(monkeypatch)
    assert 'wbc_worker' in monitoring.collect_status()
    assert 'wbc_worker' not in monitoring.collect_status(only={'sap'})
    so = monitoring.collect_status(only={'wbc_worker'})
    assert set(so['checks']) == set() and so['wbc_worker']['healthy'] is True


@pytest.mark.parametrize('url, esperado', [
    ('sqlite:///./state/wbc_tracking.db', os.path.join(monitoring._PROJECT_DIR, 'state', 'wbc_tracking.db')),
    ('sqlite:///state/wbc_tracking.db', os.path.join(monitoring._PROJECT_DIR, 'state', 'wbc_tracking.db')),
    ('sqlite:///D:/x/wbc.db', 'D:/x/wbc.db'),
    ('postgresql://u:p@h/db', None),
    ('sqlite:///:memory:', None),
])
def test_wbc_tracking_db_path(monkeypatch, url, esperado):
    monkeypatch.setenv('TRACKING_DB_URL', url)
    reset_settings()
    obtido = monitoring._wbc_tracking_db_path()
    assert (obtido is None) == (esperado is None)
    if esperado is not None:
        assert os.path.normpath(obtido) == os.path.normpath(esperado)


@pytest.mark.parametrize('inicio, fim, dias, quando, esperado', [
    ('07:00', '20:00', '1,2,3,4,5', datetime(2026, 9, 7, 10, 0), True),    # segunda 10h
    ('07:00', '20:00', '1,2,3,4,5', datetime(2026, 9, 7, 20, 30), False),  # segunda 20h30
    ('07:00', '20:00', '1,2,3,4,5', datetime(2026, 9, 6, 10, 0), False),   # domingo
    ('07:00', '20:00', '1,2,3,4,5,6,7', datetime(2026, 9, 6, 10, 0), True),
    ('22:00', '02:00', '1,2,3,4,5', datetime(2026, 9, 7, 23, 0), True),    # turno cruza a meia-noite
    ('22:00', '02:00', '1,2,3,4,5', datetime(2026, 9, 7, 12, 0), False),
])
def test_wbc_worker_in_window_segue_a_agenda_do_proprio_worker(monkeypatch, inicio, fim, dias, quando, esperado):
    monkeypatch.setenv('WORKER_HORARIO_INICIO', inicio)
    monkeypatch.setenv('WORKER_HORARIO_FIM', fim)
    monkeypatch.setenv('WORKER_DIAS_DE_TRABALHO', dias)
    reset_settings()
    assert monitoring._wbc_worker_in_window(quando) is esperado


@pytest.mark.parametrize('intervalo, limite', [('180', 10), ('300', 10), ('900', 30), ('lixo', 10)])
def test_wbc_worker_threshold_dois_intervalos_nunca_abaixo_de_10(monkeypatch, intervalo, limite):
    monkeypatch.setenv('WORKER_INTERVAL_SECONDS', intervalo)
    reset_settings()
    assert monitoring._wbc_worker_threshold_min() == limite


# ================= tarefa legada aposentada (2026-09-08): bloco fica, alerta nao ==================

def test_tarefa_legada_nasce_aposentada_e_nao_alerta(monkeypatch, tmp_path):
    """Default novo: sem WBC_TASK_MONITOR o bloco diz `retired` e nunca vira alerta —
    mesmo com um JSON do monitor dizendo que a tarefa esta desabilitada/parada (e' o desenho)."""
    monkeypatch.delenv('WBC_TASK_MONITOR', raising=False)
    reset_settings()
    p = tmp_path / 'wbc_task_state.json'
    p.write_text(json.dumps({'checked_at': '2020-01-01T00:00:00', 'enabled': False,
                             'problems': ['tarefa desabilitada']}), encoding='utf-8')
    monkeypatch.setattr(monitoring, '_wbc_task_state_path', lambda: str(p))
    sig = monitoring._scheduled_task_signal()
    assert sig['retired'] is True and sig['available'] is False and sig['healthy'] is None
    assert 'wbc_worker' in sig['error']          # aponta o substituto
    assert sig['task_name'] == 'Integração WBC'  # a forma que o card do .90 e a tool MCP leem
    assert monitoring._scheduled_task_alerts(sig) == []


def test_tarefa_aposentada_nao_derruba_o_healthy_do_status(monkeypatch):
    _stub_all_ok(monkeypatch)
    monkeypatch.setattr(monitoring, '_scheduled_task_signal',
                        lambda: {'available': False, 'retired': True, 'healthy': None,
                                 'task_name': 'Integração WBC', 'error': 'aposentada'})
    data = monitoring.collect_status()
    assert data['healthy'] is True and data['alerts'] == []
    assert data['scheduled_task']['retired'] is True


def test_wbc_task_monitor_true_religa_o_monitor(monkeypatch, tmp_path):
    _monitor_ligado(monkeypatch)
    monkeypatch.setattr(monitoring, '_wbc_task_state_path', lambda: str(tmp_path / 'nope.json'))
    sig = monitoring._scheduled_task_signal()
    assert 'retired' not in sig and sig['available'] is False and 'nunca rodou' in sig['error']
