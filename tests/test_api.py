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
    apimod._rate_limiter.reset()   # rate-limit é singleton de processo — zera entre testes
    # sync_os mockado: registra os NPEDs chamados e devolve sucesso por padrão
    chamados = []
    monkeypatch.setattr(apimod, 'sync_os', lambda n: chamados.append(n) or True)
    # sync da árvore WBC (disparada após a OS) mockada: não abre SAP/SQL nos testes
    monkeypatch.setattr(apimod, 'sync_wbc_arvore', lambda n: True)
    # sub-sync das 3 views de impressão de OS (HANA) mockada: não abre SAP nos testes
    monkeypatch.setattr(apimod, 'sync_impressao_views', lambda n: {
        'vw_os_exped_impressao_v2': True, 'vw_os_pintura_v0': True,
        'vw_os_almox_impressao': True})
    # diagnóstico mockado: por padrão "tem OS, não cancelada, pedido aberto" → sincroniza
    monkeypatch.setattr(apimod, 'diagnosticar_nped', lambda n: {
        'tem_os': True, 'cancelada': False,
        'pedido_existe': True, 'pedido_cancelado': False, 'pedido_status': 'Aberto'})
    # campos de expedição (espelho exped) mockados: sem rede; testes específicos sobrescrevem
    monkeypatch.setattr(apimod, '_fetch_exped_campos', lambda n: None)
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


# ----- /ordens-servico/<nped> (detalhe da OS) -----

_FAKE_OS_ROWS = [
    {'id': 1, 'NPED': 84080, 'N_OP': 138757, 'DescItemPED': 'Estantes',
     'DescItemEstrut': 'Coluna', 'DtPedido': '2026-06-24T00:00:00', 'CodClien': 'C011627',
     'NomeClien': 'ARAUCO CELULOSE', 'Status': 'R', 'TotalOrcam': 20640.0,
     'id_execucao': 'exec-1', 'data_hora_extracao': '2026-06-25T16:38:20'},
    {'id': 2, 'NPED': 84080, 'N_OP': 138758, 'DescItemPED': 'Estantes',
     'DescItemEstrut': 'Longarina', 'DtPedido': '2026-06-24T00:00:00', 'CodClien': 'C011627',
     'NomeClien': 'ARAUCO CELULOSE', 'Status': 'R', 'TotalOrcam': 20640.0,
     'id_execucao': 'exec-1', 'data_hora_extracao': '2026-06-25T16:38:20'},
]


def test_os_detalhe_resumo(client, monkeypatch):
    monkeypatch.setattr(apimod, '_fetch_os_detalhe', lambda n: list(_FAKE_OS_ROWS))
    r = client.get('/ordens-servico/84080')
    assert r.status_code == 200
    body = r.get_json()
    assert body['ok'] is True and body['nped'] == 84080
    resumo = body['resumo']
    assert resumo['cliente'] == 'ARAUCO CELULOSE'
    assert resumo['status'] == 'R' and resumo['status_desc'] == 'Liberado'
    assert resumo['num_linhas'] == 2
    assert resumo['num_ops'] == 2 and resumo['ops'] == [138757, 138758]
    # TotalOrcam é POR LINHA (não cabeçalho): o resumo SOMA as linhas.
    assert resumo['total_orcamento'] == 41280.0
    assert 'linhas' not in body  # sem ?linhas=1, só o resumo


def test_resumo_total_orcamento_soma_e_tolera_lixo():
    rows = [
        {'TotalOrcam': 96.78}, {'TotalOrcam': None},
        {'TotalOrcam': '100.22'}, {'TotalOrcam': 'abc'},
    ]
    assert apimod._soma_total_orcamento(rows) == 197.0
    assert apimod._soma_total_orcamento([{'TotalOrcam': None}]) is None


def test_os_detalhe_incluir_linhas(client, monkeypatch):
    monkeypatch.setattr(apimod, '_fetch_os_detalhe', lambda n: list(_FAKE_OS_ROWS))
    body = client.get('/ordens-servico/84080?linhas=1').get_json()
    assert 'linhas' in body and len(body['linhas']) == 2


def test_os_detalhe_404_sem_os(client, monkeypatch):
    monkeypatch.setattr(apimod, '_fetch_os_detalhe', lambda n: [])
    r = client.get('/ordens-servico/99999')
    assert r.status_code == 404
    assert r.get_json()['error'] == 'pedido sem OS sincronizada'


@pytest.mark.parametrize('bad', ['-5', '0', 'abc', '84080.0'])
def test_os_detalhe_nped_invalido_400(client, monkeypatch, bad):
    monkeypatch.setattr(apimod, '_fetch_os_detalhe', lambda n: list(_FAKE_OS_ROWS))
    assert client.get(f'/ordens-servico/{bad}').status_code == 400


def test_os_detalhe_disponiveis_nao_e_capturado(client, monkeypatch):
    """A rota estática /disponiveis tem prioridade sobre o <nped> dinâmico."""
    monkeypatch.setattr(apimod, 'listar_pedidos_com_os', lambda limit: [])
    # se '<nped>' capturasse 'disponiveis', viria 400 (NPED inválido); deve vir 200.
    assert client.get('/ordens-servico/disponiveis').status_code == 200


def test_os_detalhe_requires_key_when_set(client, monkeypatch):
    monkeypatch.setenv('OS_API_KEY', 'segredo')
    reset_settings()
    monkeypatch.setattr(apimod, '_fetch_os_detalhe', lambda n: list(_FAKE_OS_ROWS))
    assert client.get('/ordens-servico/84080').status_code == 401
    assert client.get('/ordens-servico/84080',
                      headers={'X-API-Key': 'segredo'}).status_code == 200


# ----- campos de expedição no resumo (PLANO_MIRA_HARNESS F3a do web) -----

_FAKE_EXPED_ROW = {
    # ObsPedido = observação do PEDIDO (cabeçalho); "Obs" da view é da OP e não é lida.
    'DtPedido': '2026-06-15T00:00:00', 'ObsPedido': 'Entregar no galpao 2.',
    'DtLiber': '2026-06-24T00:00:00', 'DtEntregaPED': '2026-07-20T00:00:00',
}


def test_os_detalhe_com_campos_exped(client, monkeypatch):
    """Espelho de expedição tem o pedido → resumo ganha datas/obs oficiais."""
    monkeypatch.setattr(apimod, '_fetch_os_detalhe', lambda n: list(_FAKE_OS_ROWS))
    monkeypatch.setattr(apimod, '_fetch_exped_campos', lambda n: dict(_FAKE_EXPED_ROW))
    resumo = client.get('/ordens-servico/84080').get_json()['resumo']
    assert resumo['exped_disponivel'] is True
    assert resumo['data_entrega'] == '2026-07-20T00:00:00'
    assert resumo['data_liberacao'] == '2026-06-24T00:00:00'
    assert resumo['obs'] == 'Entregar no galpao 2.'
    # DtPedido da EXPED é a data de colocação oficial (DIVERGE da engenharia:
    # constatado 2026-07-10 no 84080 = 15/06 vs 24/06).
    assert resumo['data_pedido'] == '2026-06-15T00:00:00'
    assert resumo['data_pedido_engenharia'] == '2026-06-24T00:00:00'


def test_os_detalhe_sem_exped_sinaliza(client, monkeypatch):
    """Pedido ainda sem sync das views de impressão → flag False, sem campos."""
    monkeypatch.setattr(apimod, '_fetch_os_detalhe', lambda n: list(_FAKE_OS_ROWS))
    resumo = client.get('/ordens-servico/84080').get_json()['resumo']
    assert resumo['exped_disponivel'] is False
    assert 'data_entrega' not in resumo
    assert resumo['data_pedido'] == '2026-06-24T00:00:00'  # mantém a da engenharia


def test_os_detalhe_exped_falha_nao_derruba(client, monkeypatch):
    """Falha na leitura da exped é best-effort: o detalhe continua 200."""
    monkeypatch.setattr(apimod, '_fetch_os_detalhe', lambda n: list(_FAKE_OS_ROWS))

    def _boom(n):
        raise RuntimeError('supabase fora')

    monkeypatch.setattr(apimod, '_fetch_exped_campos', _boom)
    r = client.get('/ordens-servico/84080')
    assert r.status_code == 200
    assert r.get_json()['resumo']['exped_disponivel'] is False


def test_os_sincronizar_resumo_ganha_exped(client, monkeypatch):
    """O resumo fresco pós-sync também sai com os campos de expedição."""
    monkeypatch.setattr(apimod, '_fetch_os_detalhe', lambda n: list(_FAKE_OS_ROWS))
    monkeypatch.setattr(apimod, '_fetch_exped_campos', lambda n: dict(_FAKE_EXPED_ROW))
    body = client.post('/ordens-servico/84080/sincronizar').get_json()
    assert body['resumo']['data_entrega'] == '2026-07-20T00:00:00'
    assert body['resumo']['exped_disponivel'] is True


# ----- POST /ordens-servico/<nped>/sincronizar (escrita: sync + resumo) -----

def test_os_sincronizar_ok_com_resumo(client, monkeypatch):
    """Sync OK → 200, resultado.ok e o resumo fresco relido da tabela."""
    monkeypatch.setattr(apimod, '_fetch_os_detalhe', lambda n: list(_FAKE_OS_ROWS))
    r = client.post('/ordens-servico/84080/sincronizar')
    assert r.status_code == 200
    body = r.get_json()
    assert body['ok'] is True and body['nped'] == 84080
    assert body['resultado']['ok'] is True
    assert body['resumo']['num_linhas'] == 2 and body['resumo']['status_desc'] == 'Liberado'
    assert client._chamados == [84080]            # sincronizou


def test_os_sincronizar_sem_os_200_sem_resumo(client, monkeypatch):
    """Pedido ABERTO sem OS gerada → aviso 'sem_os' (com status do pedido), 200,
    sem resumo e SEM sincronizar. Motivo sem acento (legível em qualquer console)."""
    monkeypatch.setattr(apimod, 'diagnosticar_nped', lambda n: {
        'tem_os': False, 'cancelada': False,
        'pedido_existe': True, 'pedido_cancelado': False, 'pedido_status': 'Aberto'})
    r = client.post('/ordens-servico/84106/sincronizar')
    assert r.status_code == 200
    body = r.get_json()
    assert body['ok'] is False and body['resultado']['tipo'] == 'sem_os'
    assert body['resultado']['status_pedido'] == 'Aberto'
    assert body['resultado']['motivo'] == 'OS ainda nao gerada para este pedido (pedido aberto).'
    assert 'resumo' not in body
    assert client._chamados == []                 # não tentou sincronizar


def test_os_sincronizar_pedido_cancelado(client, monkeypatch):
    """Pedido CANCELADO na ORDR (sem OS) → tipo 'pedido_cancelado', não 'sem_os'."""
    monkeypatch.setattr(apimod, 'diagnosticar_nped', lambda n: {
        'tem_os': False, 'cancelada': False,
        'pedido_existe': True, 'pedido_cancelado': True, 'pedido_status': 'Cancelado'})
    r = client.post('/ordens-servico/84109/sincronizar')
    assert r.status_code == 200
    res = r.get_json()['resultado']
    assert res['tipo'] == 'pedido_cancelado' and res['status_pedido'] == 'Cancelado'
    assert res['motivo'] == 'Pedido cancelado no SAP - nao ha OS a sincronizar.'
    assert client._chamados == []


def test_os_sincronizar_pedido_nao_encontrado(client, monkeypatch):
    """NPED sem linha na ORDR → tipo 'pedido_nao_encontrado'."""
    monkeypatch.setattr(apimod, 'diagnosticar_nped', lambda n: {
        'tem_os': False, 'cancelada': False,
        'pedido_existe': False, 'pedido_cancelado': False, 'pedido_status': None})
    r = client.post('/ordens-servico/99999/sincronizar')
    assert r.status_code == 200
    res = r.get_json()['resultado']
    assert res['tipo'] == 'pedido_nao_encontrado'
    assert client._chamados == []


def test_os_sincronizar_diag_legado_sem_pedido(client, monkeypatch):
    """Diag SEM as chaves pedido_* (ORDR falhou / shape antigo) → cai no 'sem_os'
    genérico, sem sufixo de status (retrocompatível)."""
    monkeypatch.setattr(apimod, 'diagnosticar_nped', lambda n: {'tem_os': False, 'cancelada': False})
    r = client.post('/ordens-servico/84106/sincronizar')
    res = r.get_json()['resultado']
    assert res['tipo'] == 'sem_os'
    assert res['motivo'] == 'OS ainda nao gerada para este pedido.'
    assert client._chamados == []


def test_os_sincronizar_cancelada(client, monkeypatch):
    monkeypatch.setattr(apimod, 'diagnosticar_nped', lambda n: {'tem_os': True, 'cancelada': True})
    r = client.post('/ordens-servico/84080/sincronizar')
    assert r.status_code == 200
    assert r.get_json()['resultado']['tipo'] == 'cancelada'
    assert client._chamados == []


def test_os_sincronizar_falha_502(client, monkeypatch):
    """Falha real de sync (tipo 'erro') → 502."""
    monkeypatch.setattr(apimod, 'sync_os', lambda n: False)
    r = client.post('/ordens-servico/84080/sincronizar')
    assert r.status_code == 502
    assert r.get_json()['resultado']['tipo'] == 'erro'


@pytest.mark.parametrize('bad', ['-5', '0', 'abc', '84080.0'])
def test_os_sincronizar_nped_invalido_400(client, bad):
    assert client.post(f'/ordens-servico/{bad}/sincronizar').status_code == 400
    assert client._chamados == []


def test_os_sincronizar_requires_key_when_set(client, monkeypatch):
    monkeypatch.setenv('OS_API_KEY', 'segredo')
    reset_settings()
    monkeypatch.setattr(apimod, '_fetch_os_detalhe', lambda n: list(_FAKE_OS_ROWS))
    assert client.post('/ordens-servico/84080/sincronizar').status_code == 401
    assert client.post('/ordens-servico/84080/sincronizar',
                       headers={'X-API-Key': 'segredo'}).status_code == 200


def test_os_sincronizar_rate_limit_429(client, monkeypatch):
    """Trava anti-loop: passou do limite → 429 com Retry-After."""
    monkeypatch.setattr(apimod, '_fetch_os_detalhe', lambda n: list(_FAKE_OS_ROWS))
    monkeypatch.setattr(apimod, '_RATE_SYNC_OS_MAX', 2)   # limite baixo p/ o teste
    assert client.post('/ordens-servico/84080/sincronizar').status_code == 200
    assert client.post('/ordens-servico/84081/sincronizar').status_code == 200
    r = client.post('/ordens-servico/84082/sincronizar')   # 3ª estoura
    assert r.status_code == 429
    body = r.get_json()
    assert body['error'] == 'rate_limited' and body['retry_after_s'] >= 1
    assert r.headers.get('Retry-After')


# ----- Rate limiter (unidade) -----

def test_rate_limiter_janela():
    rl = apimod._RateLimiter()
    assert rl.check('b', 2, 60.0)[0] is True
    assert rl.check('b', 2, 60.0)[0] is True
    permitido, retry = rl.check('b', 2, 60.0)          # 3ª estoura
    assert permitido is False and retry > 0
    assert rl.check('outro', 2, 60.0)[0] is True        # bucket diferente = independente
    rl.reset()
    assert rl.check('b', 2, 60.0)[0] is True            # reset libera


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


def test_oport_sincronizar_rate_limit_429(client, monkeypatch):
    from contextlib import contextmanager

    @contextmanager
    def _fake_lock(timeout=0):
        yield

    monkeypatch.setattr(apimod, 'oportunidades_sync_lock', _fake_lock)
    monkeypatch.setattr(apimod, 'sync_oportunidades', lambda: True)
    monkeypatch.setattr(apimod, '_RATE_FORCE_OPORT_MAX', 1)
    assert client.post('/oportunidades/sincronizar').status_code == 200
    assert client.post('/oportunidades/sincronizar').status_code == 429   # 2ª estoura


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


# ----- Hook da árvore WBC (dispara após a OS OK) -----

def test_wbc_dispara_apos_os_ok(client, monkeypatch):
    chamados_wbc = []
    monkeypatch.setattr(apimod, 'sync_wbc_arvore', lambda n: chamados_wbc.append(n) or True)
    r = client.post('/sync/ordens-servico/84080')
    assert r.status_code == 200
    assert r.get_json()['results'][0]['wbc'] is True
    assert chamados_wbc == [84080]            # OS OK → WBC disparou


def test_wbc_nao_dispara_sem_os(client, monkeypatch):
    chamados_wbc = []
    monkeypatch.setattr(apimod, 'diagnosticar_nped', lambda n: {'tem_os': False, 'cancelada': False})
    monkeypatch.setattr(apimod, 'sync_wbc_arvore', lambda n: chamados_wbc.append(n) or True)
    client.post('/sync/ordens-servico/84106')
    assert chamados_wbc == []                 # sem OS → não dispara WBC


def test_wbc_falha_nao_quebra_os(client, monkeypatch):
    """Se a sync WBC falhar, a OS ainda responde OK (best-effort)."""
    monkeypatch.setattr(apimod, 'sync_wbc_arvore',
                        lambda n: (_ for _ in ()).throw(RuntimeError('SQL fora')))
    r = client.post('/sync/ordens-servico/84080')
    assert r.status_code == 200
    res = r.get_json()['results'][0]
    assert res['ok'] is True and res['wbc'] is False


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


def test_key_via_query_param(client, monkeypatch):
    """A chave pode vir por ?key= / ?api_key= (p/ usar no navegador, sem header)."""
    monkeypatch.setenv('OS_API_KEY', 'segredo')
    reset_settings()
    monkeypatch.setattr(apimod, '_fetch_log', lambda table, n: [])
    assert client.get('/historico').status_code == 401                 # sem chave
    assert client.get('/historico?key=segredo').status_code == 200     # ?key=
    assert client.get('/historico?api_key=segredo').status_code == 200  # ?api_key=
    assert client.get('/historico?key=errada').status_code == 401      # chave errada


# ----- /status (aberto, sem chave) -----

def test_status_open_even_with_key_set(client, monkeypatch):
    """/status responde sem chave, mesmo com OS_API_KEY definido."""
    monkeypatch.setenv('OS_API_KEY', 'segredo')
    reset_settings()
    monkeypatch.setattr(apimod, 'collect_status',
                        lambda only=None: {'ok': True, 'alerts': [], 'checks': {}})
    r = client.get('/status')
    assert r.status_code == 200 and r.get_json()['ok'] is True


def test_status_strict_503_when_degraded(client, monkeypatch):
    monkeypatch.setattr(apimod, 'collect_status',
                        lambda only=None: {'ok': False, 'alerts': [], 'checks': {}})
    assert client.get('/status?strict=1').status_code == 503  # degradado + strict → 503
    assert client.get('/status').status_code == 200           # sem strict → 200 sempre
