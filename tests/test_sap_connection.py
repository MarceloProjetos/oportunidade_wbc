"""Testes da conexão SAP HANA compartilhada."""

from sap_connection import is_sap_tenant_error


def test_is_sap_tenant_error_matches_not_connected():
    assert is_sap_tenant_error(Exception('not connected'))
    assert is_sap_tenant_error(Exception('NOT  CONNECTED to database'))


def test_is_sap_tenant_error_ignores_other_errors():
    assert not is_sap_tenant_error(Exception('connection timeout'))
    assert not is_sap_tenant_error(Exception('authentication failed'))


# --- the breaker (01/10/2026 review) -------------------------------------------------------

import pytest  # noqa: E402

import sap_connection as sc  # noqa: E402


@pytest.fixture
def hana_fora(monkeypatch):
    """`dbapi.connect` that fails and counts its calls; a clock the test moves."""
    chamadas = []
    relogio = [1000.0]

    def connect(**_kw):
        chamadas.append(1)
        raise OSError('connection refused')

    monkeypatch.setattr(sc.dbapi, 'connect', connect)
    monkeypatch.setattr(sc.time, 'monotonic', lambda: relogio[0])
    return chamadas, relogio


def _conectar():
    return sc.connect_sap_hana('h', 30015, 'u', 'p', with_retry=False)


def test_depois_de_uma_falha_as_proximas_nem_tentam(hana_fora):
    """With HANA down each connection cost ~51 s on an API thread; four of those took every
    waitress thread and /health stopped answering."""
    chamadas, _ = hana_fora
    with pytest.raises(OSError):
        _conectar()
    with pytest.raises(sc.HanaIndisponivel) as erro:
        _conectar()
    assert len(chamadas) == 1
    assert 'connection refused' in str(erro.value)


def test_passado_o_prazo_tenta_de_novo(hana_fora):
    chamadas, relogio = hana_fora
    with pytest.raises(OSError):
        _conectar()
    relogio[0] += sc.DISJUNTOR_SEGUNDOS + 1
    with pytest.raises(OSError):
        _conectar()
    assert len(chamadas) == 2


def test_conexao_que_volta_fecha_o_disjuntor(hana_fora, monkeypatch):
    chamadas, relogio = hana_fora
    with pytest.raises(OSError):
        _conectar()
    relogio[0] += sc.DISJUNTOR_SEGUNDOS + 1
    monkeypatch.setattr(sc.dbapi, 'connect', lambda **_kw: 'conexao')
    assert _conectar() == 'conexao'
    assert sc._disjuntor_aberto() is None


def test_hana_indisponivel_e_erro_de_conexao():
    """Callers that catch ConnectionError/Exception keep working unchanged."""
    assert issubclass(sc.HanaIndisponivel, ConnectionError)
