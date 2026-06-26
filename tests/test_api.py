"""Testes da API HTTP de disparo da sync de OS (sem rede; sync_os mockado)."""

import pytest

pytest.importorskip('flask')  # pula o módulo se flask não estiver instalado

import api as apimod  # noqa: E402
from config import get_settings, reset_settings  # noqa: E402


@pytest.fixture
def client(monkeypatch):
    # Estado base: SEM OS_API_KEY (API aberta). Os testes de auth definem a chave.
    # (o .env local pode ter OS_API_KEY; aqui garantimos um estado determinístico.)
    monkeypatch.delenv('OS_API_KEY', raising=False)
    reset_settings()
    # sync_os mockado: registra os NPEDs chamados e devolve sucesso por padrão
    chamados = []
    monkeypatch.setattr(apimod, 'sync_os', lambda n: chamados.append(n) or True)
    # diagnóstico mockado: por padrão "tem OS, não cancelada" → segue p/ sincronizar
    monkeypatch.setattr(apimod, 'diagnosticar_nped', lambda n: {'tem_os': True, 'cancelada': False})
    apimod.app.config.update(TESTING=True)
    c = apimod.app.test_client()
    c._chamados = chamados
    return c


def test_health(client):
    r = client.get('/health')
    assert r.status_code == 200
    assert r.get_json()['status'] == 'ok'


def test_ui_served_at_root(client):
    r = client.get('/')
    assert r.status_code == 200
    assert b'Painel de Sincroniza' in r.data  # a pagina HTML


def test_favicon_no_content(client):
    assert client.get('/favicon.ico').status_code == 204


def test_historico_returns_items(client, monkeypatch):
    monkeypatch.setattr(apimod, '_fetch_log', lambda table, n: [
        {'nped': 84080, 'status': 'sucesso', 'qtd_registros': 383,
         'duracao_segundos': 3.7, 'data_hora_sincronizacao': '2026-06-26T11:19:00+00:00'},
    ])
    r = client.get('/historico')
    assert r.status_code == 200
    body = r.get_json()
    assert body['ok'] is True
    assert body['items'][0]['nped'] == 84080


def test_historico_respects_limit(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(apimod, '_fetch_log', lambda table, n: captured.__setitem__('n', n) or [])
    client.get('/historico?limit=5')
    assert captured['n'] == 5


def test_historico_requires_key_when_set(client, monkeypatch):
    monkeypatch.setenv('OS_API_KEY', 'segredo')
    reset_settings()
    monkeypatch.setattr(apimod, '_fetch_log', lambda table, n: [])
    assert client.get('/historico').status_code == 401
    assert client.get('/historico', headers={'X-API-Key': 'segredo'}).status_code == 200


def test_limpar_historico(client, monkeypatch):
    chamado = {}

    def _fake_clear(table):
        chamado['ok'] = True
        return 3

    monkeypatch.setattr(apimod, '_clear_log', _fake_clear)
    r = client.delete('/historico')
    assert r.status_code == 200
    assert r.get_json() == {'ok': True, 'removed': 3}
    assert chamado.get('ok') is True


def test_limpar_historico_requires_key_when_set(client, monkeypatch):
    monkeypatch.setenv('OS_API_KEY', 'segredo')
    reset_settings()
    monkeypatch.setattr(apimod, '_clear_log', lambda table: 0)
    assert client.delete('/historico').status_code == 401
    assert client.delete('/historico', headers={'X-API-Key': 'segredo'}).status_code == 200


# ----- Oportunidades (pipeline agendado) -----

def test_oport_historico_returns_items(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(apimod, '_fetch_log',
                        lambda table, n: captured.update(table=table) or [{'status': 'sucesso'}])
    r = client.get('/oportunidades/historico')
    assert r.status_code == 200 and r.get_json()['ok'] is True
    assert captured['table'] == get_settings().sync_log_table_name  # lê o log de oportunidades


def test_oport_limpar(client, monkeypatch):
    monkeypatch.setattr(apimod, '_clear_log', lambda table: 5)
    r = client.delete('/oportunidades/historico')
    assert r.status_code == 200 and r.get_json() == {'ok': True, 'removed': 5}


def test_oport_sincronizar_ok(client, monkeypatch):
    from contextlib import contextmanager

    @contextmanager
    def _fake_lock(timeout=0):
        yield

    monkeypatch.setattr(apimod, 'oportunidades_sync_lock', _fake_lock)
    monkeypatch.setattr(apimod, 'sync_oportunidades', lambda: True)
    r = client.post('/oportunidades/sincronizar')
    assert r.status_code == 200 and r.get_json()['ok'] is True


def test_oport_sincronizar_busy_409(client, monkeypatch):
    from contextlib import contextmanager

    @contextmanager
    def _busy_lock(timeout=0):
        raise apimod.FileLockTimeout('busy')
        yield  # pragma: no cover

    monkeypatch.setattr(apimod, 'oportunidades_sync_lock', _busy_lock)
    monkeypatch.setattr(apimod, 'sync_oportunidades', lambda: True)
    r = client.post('/oportunidades/sincronizar')
    assert r.status_code == 409
    assert r.get_json()['tipo'] == 'ocupado'


def test_oport_sincronizar_requires_key_when_set(client, monkeypatch):
    monkeypatch.setenv('OS_API_KEY', 'segredo')
    reset_settings()
    monkeypatch.setattr(apimod, 'sync_oportunidades', lambda: True)
    assert client.post('/oportunidades/sincronizar').status_code == 401


def test_oport_info(client, monkeypatch):
    monkeypatch.setattr(apimod, '_count_rows', lambda table: 1543)
    r = client.get('/oportunidades/info')
    assert r.status_code == 200
    d = r.get_json()
    assert d['ok'] is True and d['total'] == 1543
    assert 'intervalo_minutos' in d and 'janela_horas' in d


def test_sync_single_ok(client):
    r = client.post('/sync/ordens-servico/84080')
    assert r.status_code == 200
    body = r.get_json()
    assert body['ok'] is True
    assert body['results'][0]['nped'] == 84080 and body['results'][0]['ok'] is True
    assert client._chamados == [84080]


def test_sync_batch_ok(client):
    r = client.post('/sync/ordens-servico', json={'npeds': [84080, 84095]})
    assert r.status_code == 200
    assert r.get_json()['summary'] == {'total': 2, 'sucesso': 2, 'falha': 0}
    assert client._chamados == [84080, 84095]


def test_sync_body_single_nped(client):
    r = client.post('/sync/ordens-servico', json={'nped': 84080})
    assert r.status_code == 200
    assert client._chamados == [84080]


@pytest.mark.parametrize('bad', ['-5', '0', 'abc', '84080.0'])
def test_sync_invalid_nped_path_400(client, bad):
    r = client.post(f'/sync/ordens-servico/{bad}')
    assert r.status_code == 400
    assert client._chamados == []  # não chamou a sync


def test_sync_missing_body_400(client):
    r = client.post('/sync/ordens-servico', json={})
    assert r.status_code == 400


def test_partial_failure_207(client, monkeypatch):
    monkeypatch.setattr(apimod, 'sync_os', lambda n: n == 84080)
    r = client.post('/sync/ordens-servico', json={'npeds': [84080, 99999]})
    assert r.status_code == 207
    assert r.get_json()['summary'] == {'total': 2, 'sucesso': 1, 'falha': 1}


def test_all_failed_207(client, monkeypatch):
    monkeypatch.setattr(apimod, 'sync_os', lambda n: False)
    r = client.post('/sync/ordens-servico/84080')
    assert r.status_code == 207
    body = r.get_json()
    assert body['ok'] is False
    assert body['results'][0]['tipo'] == 'erro'


def test_sem_os_aviso(client, monkeypatch):
    """Pedido sem OS gerada (OWOR vazia) → aviso 'sem_os', sem chamar a sync."""
    monkeypatch.setattr(apimod, 'diagnosticar_nped', lambda n: {'tem_os': False, 'cancelada': False})
    r = client.post('/sync/ordens-servico/84106')
    res = r.get_json()['results'][0]
    assert res['ok'] is False and res['tipo'] == 'sem_os'
    assert 'gerada' in res['motivo'].lower()
    assert client._chamados == []  # não tentou sincronizar


def test_cancelada_aviso(client, monkeypatch):
    """OS cancelada (todas com Status='C') → aviso 'cancelada', sem chamar a sync."""
    monkeypatch.setattr(apimod, 'diagnosticar_nped', lambda n: {'tem_os': True, 'cancelada': True})
    r = client.post('/sync/ordens-servico/84080')
    res = r.get_json()['results'][0]
    assert res['ok'] is False and res['tipo'] == 'cancelada'
    assert 'cancel' in res['motivo'].lower()
    assert client._chamados == []


def test_auth_required_when_key_set(client, monkeypatch):
    monkeypatch.setenv('OS_API_KEY', 'segredo')
    reset_settings()
    # sem header → 401 (e não chama a sync)
    assert client.post('/sync/ordens-servico/84080').status_code == 401
    assert client._chamados == []
    # X-API-Key correto → 200
    assert client.post('/sync/ordens-servico/84080',
                       headers={'X-API-Key': 'segredo'}).status_code == 200
    # Authorization: Bearer correto → 200
    assert client.post('/sync/ordens-servico/84080',
                       headers={'Authorization': 'Bearer segredo'}).status_code == 200
    # chave errada → 401
    assert client.post('/sync/ordens-servico/84080',
                       headers={'X-API-Key': 'errada'}).status_code == 401
