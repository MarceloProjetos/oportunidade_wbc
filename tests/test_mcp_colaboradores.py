"""Testes das 2 tools de Colaboradores na fachada MCP (F5).

A fachada e' fina: a chamada HTTP e' uma so. O que tem logica AQUI -- e por isso e' o
que se testa -- e' o cuidado de conversa em volta dela: filtro de setor sem acento,
teto de nomes que nao esconde as contagens, resumo sem nomes, e a traducao do 404 de
rota inexistente (a .11 sem o deploy da F4 responde isso, e "HTTP 404" cru levaria o
modelo a concluir que nao ha colaboradores).

O modulo e' carregado por caminho, com nome proprio: `mcp/` nao e' pacote e o nome
`mcp` ja pertence ao SDK instalado.
"""
from __future__ import annotations

import importlib.util
import os
from typing import Any

import pytest

pytest.importorskip('mcp')

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAMINHO = os.path.join(RAIZ, 'mcp', 'mcp_server.py')


@pytest.fixture(scope='module')
def fachada():
    spec = importlib.util.spec_from_file_location('_fachada_mcp_colab', CAMINHO)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _payload() -> dict:
    """Resposta do endpoint: 2 setores na tecnequip, 3 pessoas."""
    return {
        'ok': True, 'atualizado_em': '2026-08-31T15:40:00+00:00',
        'desatualizado': False, 'total': 3, 'somente_ativos': True,
        'empresas': [{
            'empresa': 'tecnequip', 'total': 3,
            'setores': [
                {'setor': 'EXPEDIÇÃO', 'total': 1, 'colaboradores': [
                    {'person_id': 3, 'nome': 'Carlos', 'status': 'ativo'}]},
                {'setor': 'PRODUÇÃO', 'total': 2, 'colaboradores': [
                    {'person_id': 1, 'nome': 'Ana', 'status': 'ativo'},
                    {'person_id': 2, 'nome': 'Bruno', 'status': 'ativo'}]},
            ],
        }],
    }


@pytest.fixture
def chamadas(fachada, monkeypatch):
    """Intercepta o ``_get``: guarda ``(path, params)`` e devolve o payload."""
    registro: list[tuple[str, dict | None]] = []

    def _fake(path: str, params: dict[str, Any] | None = None):
        registro.append((path, params))
        return _payload()

    monkeypatch.setattr(fachada, '_get', _fake)
    return registro


def test_listar_usa_o_endpoint_e_o_default_e_so_ativos(fachada, chamadas):
    fachada.listar_colaboradores()
    assert chamadas == [('/rh/colaboradores', {'somente_ativos': 1})]


def test_listar_sem_somente_ativos_nao_manda_o_filtro(fachada, chamadas):
    """Sem o param, o endpoint devolve TODOS (inclusive desligados) — o contrato
    existe justamente para a linha do desligado não sumir."""
    fachada.listar_colaboradores(somente_ativos=False)
    assert chamadas == [('/rh/colaboradores', None)]


def test_listar_normaliza_a_empresa(fachada, chamadas):
    fachada.listar_colaboradores(empresa=' Tecnequip ')
    assert chamadas[0][1]['empresa'] == 'tecnequip'


def test_filtro_de_setor_ignora_acento_e_caixa(fachada, chamadas):
    r = fachada.listar_colaboradores(setor='producao')
    (empresa,) = r['empresas']
    assert [s['setor'] for s in empresa['setores']] == ['PRODUÇÃO']
    assert empresa['total'] == 2 and r['total'] == 2   # totais recalculados


def test_setor_inexistente_devolve_os_disponiveis(fachada, chamadas):
    """Lista vazia calada faria o modelo dizer "não tem ninguém"."""
    r = fachada.listar_colaboradores(setor='almoxarifado')
    assert r['empresas'] == [] and 'aviso' in r
    assert r['setores_disponiveis'] == ['EXPEDIÇÃO', 'PRODUÇÃO']


def test_teto_corta_nomes_mas_preserva_as_contagens(fachada, chamadas):
    r = fachada.listar_colaboradores(limite=1)
    assert r['truncado'] is True and r['mostrando'] == 1 and r['total'] == 3
    setores = {s['setor']: s for s in r['empresas'][0]['setores']}
    assert len(setores['EXPEDIÇÃO']['colaboradores']) == 1   # coube
    assert setores['PRODUÇÃO']['colaboradores'] == [] and setores['PRODUÇÃO']['omitidos'] == 2
    assert setores['PRODUÇÃO']['total'] == 2                # a contagem continua certa


def test_resumo_troca_pessoas_por_contagens(fachada, chamadas):
    r = fachada.resumo_colaboradores()
    assert r['empresas'] == [
        {'empresa': 'tecnequip', 'total': 3,
         'setores': {'EXPEDIÇÃO': 1, 'PRODUÇÃO': 2}},
    ]
    assert 'Ana' not in str(r)   # nenhum nome atravessa


def test_404_de_rota_vira_dica_de_deploy(fachada, monkeypatch):
    monkeypatch.setattr(fachada, '_get', lambda *a, **kw: {
        'ok': False, 'erro': 'HTTP 404 em /rh/colaboradores'})
    r = fachada.listar_colaboradores()
    assert r['ok'] is False and 'OrcaView-OS-API' in r['dica']


def test_erro_da_api_passa_inteiro(fachada, monkeypatch):
    """400 de empresa inválida chega ao modelo com a lista de válidas."""
    monkeypatch.setattr(fachada, '_get', lambda *a, **kw: {
        'ok': False, 'error': 'empresa invalida', 'empresas_validas': ['altamira']})
    r = fachada.resumo_colaboradores(empresa='tecnequipe')
    assert r['empresas_validas'] == ['altamira']
