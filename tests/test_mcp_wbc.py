"""Tool `estado_integracao_wbc` da fachada MCP (Integração WBC → SAP).

A fachada e' fina: uma chamada HTTP ao /status com `checks=wbc_worker`. O que se testa
e' o recorte da resposta (so' o bloco do worker + alertas), a passagem inteira do erro
da API, e que o servidor se apresenta dizendo que a integracao WBC roda nesta maquina.

O modulo e' carregado por caminho, com nome proprio: `mcp/` nao e' pacote e o nome
`mcp` ja pertence ao SDK instalado.
"""
from __future__ import annotations

import importlib.util
import os

import pytest

pytest.importorskip('mcp')

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAMINHO = os.path.join(RAIZ, 'mcp', 'mcp_server.py')


@pytest.fixture(scope='module')
def fachada():
    spec = importlib.util.spec_from_file_location('_fachada_mcp_wbc', CAMINHO)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _status_com_worker() -> dict:
    return {
        'ok': True, 'healthy': False,
        'checks': {},
        'system': {'hostname': 'SRV11'},
        'wbc_worker': {'installed': True, 'available': True, 'healthy': False, 'stale': True,
                       'in_window': True, 'minutes_ago': 40, 'threshold_min': 10,
                       'last': {'id': 725, 'status': 'concluida'}},
        'alerts': ['worker WBC sem ciclo há 40 min dentro do expediente (limite 10 min, '
                   'intervalo 180 s) — serviço OrcaView-WBC-Worker parado?'],
    }


def test_recorta_so_o_bloco_do_worker_e_os_alertas(fachada, monkeypatch):
    chamadas = []
    monkeypatch.setattr(fachada, '_get',
                        lambda path, params=None: chamadas.append((path, params)) or _status_com_worker())
    r = fachada.estado_integracao_wbc()
    assert chamadas == [('/status', {'checks': 'wbc_worker'})]
    assert set(r) == {'ok', 'wbc_worker', 'alerts'}
    assert r['wbc_worker']['stale'] is True
    assert 'OrcaView-WBC-Worker' in r['alerts'][0]
    assert 'system' not in r  # o resto do /status nao vem junto


def test_erro_da_api_passa_inteiro(fachada, monkeypatch):
    """Sem o bloco (API antiga, sem o deploy): devolve o que a API disse, sem inventar."""
    monkeypatch.setattr(fachada, '_get', lambda path, params=None: {'ok': False, 'error': 'HTTP 400'})
    assert fachada.estado_integracao_wbc() == {'ok': False, 'error': 'HTTP 400'}


def test_docstring_ensina_a_ler_installed_e_healthy_null(fachada):
    d = fachada.estado_integracao_wbc.__doc__
    assert 'installed=false' in d and 'healthy=null' in d and 'in_window' in d


def test_servidor_diz_que_a_integracao_wbc_roda_aqui(fachada):
    assert 'estado_integracao_wbc' in fachada._INSTRUCOES
    assert '8079' in fachada._INSTRUCOES
