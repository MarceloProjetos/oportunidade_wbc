"""Testes do módulo de escrita de status de OP no Service Layer.

TUDO offline: nenhum teste abre socket. O transporte é substituído por uma
``_SessaoFake`` que grava as chamadas recebidas e devolve respostas roteirizadas — assim
dá para afirmar não só o que aconteceu, mas o que NÃO aconteceu (o "zero PATCH" da
idempotência é metade do valor desta suíte).
"""

import json

import pytest

requests = pytest.importorskip('requests')

import ordens_producao_sl as opsl  # noqa: E402
from config import get_settings, reset_settings  # noqa: E402

# ── Dublês ────────────────────────────────────────────────────────────────────────

class _RespFake:
    """Resposta mínima com a superfície que o módulo usa."""

    def __init__(self, status=200, body=None, text=None):
        self.status_code = status
        self._body = body
        self.text = text if text is not None else (json.dumps(body) if body is not None else '')

    @property
    def ok(self):
        return 200 <= self.status_code < 300

    def json(self):
        if self._body is None:
            raise ValueError('sem corpo JSON')
        return self._body


class _SessaoFake:
    """Sessão que serve respostas de uma fila e registra tudo o que recebeu."""

    def __init__(self, respostas=None):
        self.respostas = list(respostas or [])
        self.chamadas = []          # (metodo, url, json_body, kwargs)
        self.fechada = False

    def _servir(self, metodo, url, **kw):
        self.chamadas.append((metodo, url, kw.get('json'), kw))
        if not self.respostas:
            return _RespFake(200, {'value': []})
        item = self.respostas.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def request(self, metodo, url, **kw):
        return self._servir(metodo, url, **kw)

    def post(self, url, **kw):
        return self._servir('POST', url, **kw)

    def close(self):
        self.fechada = True

    # helpers de leitura
    def metodos(self):
        return [c[0] for c in self.chamadas]

    def patches(self):
        return [c for c in self.chamadas if c[0] == 'PATCH']


def _op(doc_entry=126599, doc_num=125060, status='boposReleased', **extra):
    """Registro cru do Service Layer (nomes de campo do SAP)."""
    base = {
        'AbsoluteEntry': doc_entry, 'DocumentNumber': doc_num,
        'ItemNo': 'PAR000PADRA000000000', 'PlannedQuantity': 36.0,
        'ProductionOrderStatus': status, 'ProductionOrderOrigin': 'bopooSalesOrder',
        'ProductionOrderOriginNumber': 83871, 'DueDate': '2026-08-20',
    }
    base.update(extra)
    return base


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    """Feature LIGADA e credenciada; sessão zerada entre testes (é global de módulo)."""
    monkeypatch.setenv('OP_SL_ENABLED', 'true')
    monkeypatch.setenv('OP_SL_SERVER', 'sap-teste')
    monkeypatch.setenv('OP_SL_COMPANY_DB', 'SBOTESTE')
    monkeypatch.setenv('OP_SL_USERNAME', 'usuario')
    monkeypatch.setenv('OP_SL_PASSWORD', 'senha-secreta')
    monkeypatch.delenv('OP_STATUS_PERMITIDOS', raising=False)
    reset_settings()
    opsl._sessao = None
    opsl._sessao_criada_em = 0.0
    opsl._avisou_tls = False
    monkeypatch.setattr(opsl.time, 'sleep', lambda _s: None)   # backoff não atrasa a suíte
    yield
    opsl._sessao = None
    opsl._sessao_criada_em = 0.0


@pytest.fixture
def sessao(monkeypatch):
    """Instala uma ``_SessaoFake`` já autenticada (o login não conta nas chamadas)."""
    s = _SessaoFake()
    monkeypatch.setattr(opsl, '_login', lambda _cfg: s)
    return s


# ── Sessão e transporte ───────────────────────────────────────────────────────────

def test_login_monta_payload_do_service_layer(monkeypatch):
    sessao = _SessaoFake([_RespFake(200, {'SessionId': 'abc'})])
    monkeypatch.setattr(opsl, '_TimeoutSession', lambda _t: sessao)
    opsl._login(get_settings())
    metodo, url, corpo, _ = sessao.chamadas[0]
    assert (metodo, url) == ('POST', 'https://sap-teste:50000/b1s/v1/Login')
    assert corpo == {'CompanyDB': 'SBOTESTE', 'UserName': 'usuario', 'Password': 'senha-secreta'}


def test_login_nunca_registra_a_senha_no_log(monkeypatch, caplog):
    monkeypatch.setattr(opsl, '_TimeoutSession',
                        lambda _t: _SessaoFake([_RespFake(401, text='Invalid credentials')] * 3))
    with caplog.at_level('DEBUG'), pytest.raises(opsl.OPIndisponivel):
        opsl._login(get_settings())
    assert 'senha-secreta' not in caplog.text


def test_sessao_e_reusada_entre_chamadas(sessao, monkeypatch):
    logins = []
    monkeypatch.setattr(opsl, '_login', lambda _cfg: logins.append(1) or sessao)
    sessao.respostas = [_RespFake(200, {'value': [_op()]}) for _ in range(3)]
    for _ in range(3):
        opsl.consultar_op(125060)
    assert len(logins) == 1, 'um login por request esgota o pool de sessões do SL'


def test_ttl_vencido_renova_e_desloga_a_sessao_antiga(monkeypatch):
    antiga, nova = _SessaoFake(), _SessaoFake([_RespFake(200, {'value': [_op()]})])
    monkeypatch.setattr(opsl, '_login', lambda _cfg: nova)
    opsl._sessao, opsl._sessao_criada_em = antiga, opsl.time.monotonic() - 99_999
    opsl.consultar_op(125060)
    assert opsl._sessao is nova
    # a sessão trocada tem de sair pelo /Logout: vazar uma por ciclo de TTL derruba o SL
    assert any('/Logout' in c[1] for c in antiga.chamadas)
    assert antiga.fechada


def test_401_refaz_login_e_repete_a_chamada(monkeypatch):
    morta = _SessaoFake([_RespFake(401, text='Invalid session')])
    viva = _SessaoFake([_RespFake(200, {'value': [_op()]})])
    fila = [morta, viva]
    monkeypatch.setattr(opsl, '_login', lambda _cfg: fila.pop(0))
    assert opsl.consultar_op(125060)['doc_num'] == 125060
    assert viva.metodos() == ['GET'], 'a chamada original precisa ser repetida na sessão nova'


def test_corpo_invalid_session_sem_401_tambem_refaz_login(monkeypatch):
    # erro 301 do SL volta com outro status code; casar só com 401 deixaria isso como falha
    morta = _SessaoFake([_RespFake(500, text='Invalid session id')])
    viva = _SessaoFake([_RespFake(200, {'value': [_op()]})])
    fila = [morta, viva]
    monkeypatch.setattr(opsl, '_login', lambda _cfg: fila.pop(0))
    assert opsl.consultar_op(125060)['doc_num'] == 125060


def test_relogin_acontece_uma_unica_vez(monkeypatch):
    """SL travado em 401 não pode virar loop de /Login até o timeout do request."""
    sessoes = [_SessaoFake([_RespFake(401, text='Invalid session')]) for _ in range(5)]
    logins = []
    monkeypatch.setattr(opsl, '_login', lambda _cfg: logins.append(1) or sessoes[len(logins) - 1])
    with pytest.raises(opsl.OPIndisponivel):
        opsl.consultar_op(125060)
    assert len(logins) == 2, 'exatamente 1 replay: login inicial + 1 relogin'


def test_login_falha_tres_vezes_vira_erro_tipado(monkeypatch):
    monkeypatch.setattr(opsl, '_TimeoutSession',
                        lambda _t: _SessaoFake([requests.RequestException('conexao recusada')]))
    with pytest.raises(opsl.OPIndisponivel) as exc:
        opsl._login(get_settings())
    assert exc.value.http == 502


@pytest.fixture
def timeout_capturado(monkeypatch):
    """Captura o ``timeout`` que chega no ``requests.Session.request`` de verdade."""
    capturado = {}
    monkeypatch.setattr(
        requests.Session, 'request',
        lambda self, *a, **k: capturado.update(k) or _RespFake(),
    )
    return capturado


def test_timeout_default_vai_em_toda_request(timeout_capturado):
    opsl._TimeoutSession((5, 30)).request('GET', 'http://x')
    assert timeout_capturado['timeout'] == (5, 30), 'sem timeout, SAP caído congela a thread'


def test_timeout_explicito_ainda_pode_sobrescrever(timeout_capturado):
    opsl._TimeoutSession((5, 30)).request('GET', 'http://x', timeout=(1, 2))
    assert timeout_capturado['timeout'] == (1, 2)


def test_erro_aninhado_do_service_layer_e_extraido():
    resp = _RespFake(400, {'error': {'message': {'value': 'Status change not allowed'}}})
    assert opsl._erro_sl(resp) == 'Status change not allowed'


def test_erro_com_corpo_nao_json_nao_estoura():
    assert 'Bad Gateway' in opsl._erro_sl(_RespFake(502, text='<html>Bad Gateway</html>'))


def test_erro_sem_corpo_algum_devolve_o_status():
    assert opsl._erro_sl(_RespFake(500, text='')) == 'HTTP 500'


def test_verify_ssl_respeita_a_configuracao(sessao, monkeypatch):
    monkeypatch.setenv('OP_SL_VERIFY_SSL', 'true')
    reset_settings()
    sessao.respostas = [_RespFake(200, {'value': [_op()]})]
    opsl.consultar_op(125060)
    assert sessao.chamadas[0][3]['verify'] is True


# ── Resolução DocNum / DocEntry ───────────────────────────────────────────────────

def test_docnum_e_resolvido_por_filter(sessao):
    sessao.respostas = [_RespFake(200, {'value': [_op()]})]
    op = opsl.consultar_op(125060)
    assert '$filter=DocumentNumber eq 125060' in sessao.chamadas[0][1]
    assert (op['doc_entry'], op['doc_num']) == (126599, 125060)


def test_docentry_vai_direto_sem_filter(sessao):
    sessao.respostas = [_RespFake(200, _op())]
    opsl.consultar_op(126599, por_docentry=True)
    url = sessao.chamadas[0][1]
    assert url.endswith('/ProductionOrders(126599)') and '$filter' not in url


def test_docnum_inexistente_vira_nao_encontrada(sessao):
    sessao.respostas = [_RespFake(200, {'value': []})]
    with pytest.raises(opsl.OPNaoEncontrada) as exc:
        opsl.consultar_op(999999)
    assert exc.value.http == 404


def test_docentry_inexistente_vira_nao_encontrada(sessao):
    sessao.respostas = [_RespFake(404, text='not found')]
    with pytest.raises(opsl.OPNaoEncontrada):
        opsl.consultar_op(999999, por_docentry=True)


def test_docnum_com_mais_de_um_resultado_e_recusado(sessao):
    """Pegar ops[0] mexeria numa OP arbitrária em produção. Recusar custa uma retentativa."""
    sessao.respostas = [_RespFake(200, {'value': [_op(), _op(126600, 125060)]})]
    with pytest.raises(opsl.OPAmbigua) as exc:
        opsl.consultar_op(125060)
    assert exc.value.http == 409


def test_absoluteentry_ausente_cai_no_docentry(sessao):
    cru = _op()
    del cru['AbsoluteEntry']
    cru['DocEntry'] = 126599
    sessao.respostas = [_RespFake(200, {'value': [cru]})]
    assert opsl.consultar_op(125060)['doc_entry'] == 126599


def test_numero_invalido_e_recusado_sem_rede(sessao):
    for ruim in ('abc', 0, -5):
        with pytest.raises(opsl.OPStatusInvalido):
            opsl.consultar_op(ruim)
    assert sessao.chamadas == []


def test_resumo_traz_status_legivel_e_transicoes(sessao):
    sessao.respostas = [_RespFake(200, {'value': [_op(status='boposPlanned')]})]
    op = opsl.consultar_op(125060)
    assert op['status_desc'] == 'Planejada'
    assert op['transicoes_permitidas'] == ['liberada', 'encerrada']


def test_transicoes_de_op_terminal_sao_vazias(sessao):
    sessao.respostas = [_RespFake(200, {'value': [_op(status='boposClosed')]})]
    assert opsl.consultar_op(125060)['transicoes_permitidas'] == []


# ── Resolução de status ───────────────────────────────────────────────────────────

@pytest.mark.parametrize('bruto,esperado', [
    ('liberada', 'boposReleased'), ('LIBERAR', 'boposReleased'),
    ('boposReleased', 'boposReleased'), ('boposreleased', 'boposReleased'),
    ('encerrada', 'boposClosed'), ('fechar', 'boposClosed'), ('boposClosed', 'boposClosed'),
])
def test_resolver_status_aceita_apelido_e_codigo(bruto, esperado):
    assert opsl.resolver_status(bruto) == esperado


@pytest.mark.parametrize('ruim', ['congelada', '', None, 'bopos', 42])
def test_resolver_status_recusa_o_resto(ruim):
    with pytest.raises(opsl.OPStatusInvalido):
        opsl.resolver_status(ruim)


# ── Máquina de estados ────────────────────────────────────────────────────────────

@pytest.mark.parametrize('atual,alvo', [
    ('boposPlanned', 'liberada'),
    ('boposPlanned', 'encerrada'),
    ('boposReleased', 'encerrada'),
])
def test_transicoes_validas_mandam_patch(sessao, atual, alvo):
    sessao.respostas = [_RespFake(200, {'value': [_op(status=atual)]}), _RespFake(204)]
    r = opsl.atualizar_status(125060, alvo)
    assert r['ja_estava'] is False
    (metodo, url, corpo, _), = sessao.patches()
    assert metodo == 'PATCH' and url.endswith('/ProductionOrders(126599)')
    assert corpo == {'ProductionOrderStatus': opsl.resolver_status(alvo)}


@pytest.mark.parametrize('atual,alvo', [
    ('boposReleased', 'liberada'),
    ('boposClosed', 'encerrada'),
])
def test_alvo_igual_ao_atual_nao_manda_patch(sessao, atual, alvo):
    """Idempotência por construção: repetir a chamada não pode aplicar nada duas vezes."""
    sessao.respostas = [_RespFake(200, {'value': [_op(status=atual)]})]
    r = opsl.atualizar_status(125060, alvo)
    assert r['ja_estava'] is True
    assert sessao.patches() == []


@pytest.mark.parametrize('atual', ['boposClosed', 'boposCancelled'])
def test_nada_sai_de_status_terminal(sessao, atual):
    sessao.respostas = [_RespFake(200, {'value': [_op(status=atual)]})]
    with pytest.raises(opsl.OPTransicaoInvalida) as exc:
        opsl.atualizar_status(125060, 'liberada')
    assert exc.value.http == 409
    assert sessao.patches() == []


def test_cancelada_nao_pode_nem_ser_encerrada(sessao):
    sessao.respostas = [_RespFake(200, {'value': [_op(status='boposCancelled')]})]
    with pytest.raises(opsl.OPTransicaoInvalida):
        opsl.atualizar_status(125060, 'encerrada')


def test_status_fora_da_allowlist_nao_toca_na_rede(sessao):
    for fora in ('cancelada', 'planejada'):
        with pytest.raises(opsl.OPStatusInvalido) as exc:
            opsl.atualizar_status(125060, fora)
        assert exc.value.http == 400
    assert sessao.chamadas == [], 'a allowlist tem de barrar ANTES de qualquer HTTP'


def test_allowlist_do_env_e_respeitada(sessao, monkeypatch):
    monkeypatch.setenv('OP_STATUS_PERMITIDOS', 'boposClosed')
    reset_settings()
    with pytest.raises(opsl.OPStatusInvalido):
        opsl.atualizar_status(125060, 'liberada')
    sessao.respostas = [_RespFake(200, {'value': [_op(status='boposPlanned')]}), _RespFake(204)]
    assert opsl.atualizar_status(125060, 'encerrada')['ja_estava'] is False


def test_compare_and_swap_divergente_bloqueia(sessao):
    sessao.respostas = [_RespFake(200, {'value': [_op(status='boposReleased')]})]
    with pytest.raises(opsl.OPConflito) as exc:
        opsl.atualizar_status(125060, 'encerrada', status_atual='planejada')
    assert exc.value.http == 409
    assert exc.value.extra['status_atual'] == 'boposReleased'
    assert sessao.patches() == []


def test_compare_and_swap_coincidente_passa(sessao):
    sessao.respostas = [_RespFake(200, {'value': [_op(status='boposReleased')]}), _RespFake(204)]
    assert opsl.atualizar_status(125060, 'encerrada', status_atual='liberada')['ja_estava'] is False


@pytest.mark.parametrize('codigo', [200, 204])
def test_patch_200_e_204_contam_como_sucesso(sessao, codigo):
    sessao.respostas = [_RespFake(200, {'value': [_op(status='boposPlanned')]}),
                        _RespFake(codigo)]
    assert opsl.atualizar_status(125060, 'liberada')['status_novo'] == 'boposReleased'


def test_patch_recusado_pelo_sap_nao_retenta(sessao):
    """Recusa de mudança de status é determinística — retentar só enterra a mensagem."""
    sessao.respostas = [
        _RespFake(200, {'value': [_op(status='boposPlanned')]}),
        _RespFake(400, {'error': {'message': {'value': 'Status change not allowed'}}}),
    ]
    with pytest.raises(opsl.OPIndisponivel) as exc:
        opsl.atualizar_status(125060, 'liberada')
    assert 'Status change not allowed' in exc.value.motivo
    assert len(sessao.patches()) == 1


def test_resultado_traz_o_de_para(sessao):
    sessao.respostas = [_RespFake(200, {'value': [_op(status='boposPlanned')]}), _RespFake(204)]
    r = opsl.atualizar_status(125060, 'encerrada')
    assert (r['status_anterior'], r['status_novo']) == ('boposPlanned', 'boposClosed')
    assert (r['doc_num'], r['doc_entry']) == (125060, 126599)


# ── Guardas de configuração ───────────────────────────────────────────────────────

def test_kill_switch_desligado_recusa_sem_rede(monkeypatch, sessao):
    monkeypatch.setenv('OP_SL_ENABLED', 'false')
    reset_settings()
    for chamada in (lambda: opsl.consultar_op(125060),
                    lambda: opsl.atualizar_status(125060, 'encerrada')):
        with pytest.raises(opsl.OPDesativado) as exc:
            chamada()
        assert exc.value.http == 503
    assert sessao.chamadas == []


def test_kill_switch_nasce_desligado(monkeypatch):
    monkeypatch.delenv('OP_SL_ENABLED', raising=False)
    reset_settings()
    assert get_settings().op_sl_enabled is False, 'o default TEM de ser desligado'


def test_credencial_ausente_recusa_sem_rede(monkeypatch, sessao):
    monkeypatch.delenv('OP_SL_PASSWORD', raising=False)
    reset_settings()
    with pytest.raises(opsl.OPDesativado):
        opsl.consultar_op(125060)
    assert sessao.chamadas == []


def test_allowlist_vazia_bloqueia_tudo(monkeypatch, sessao):
    monkeypatch.setenv('OP_STATUS_PERMITIDOS', 'boposInventado,lixo')
    reset_settings()
    with pytest.raises(opsl.OPDesativado):
        opsl.atualizar_status(125060, 'encerrada')
    assert sessao.chamadas == []


def test_service_layer_fora_do_ar_vira_502(sessao):
    sessao.respostas = [requests.RequestException('connection refused')]
    with pytest.raises(opsl.OPIndisponivel) as exc:
        opsl.consultar_op(125060)
    assert exc.value.http == 502
