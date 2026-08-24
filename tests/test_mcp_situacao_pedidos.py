"""Testes das 3 tools de Situacao dos Pedidos na fachada MCP (F4).

A fachada e' fina de proposito: cada tool so monta um GET. Entao o que se testa aqui e'
exatamente isso -- **caminho, parametros e defaults** -- e nada mais. Os defaults sao
decisao de produto congelada (D3 e D4 do plano); um deles mudar sem querer nao apareceria
em lugar nenhum ate alguem estranhar o numero na tela.

O modulo e' carregado **por caminho**, com nome proprio: `mcp/` nao e' pacote (nao tem
`__init__.py`) e o nome `mcp` ja pertence ao SDK instalado.

Plano: ``docs/PLANO_SITUACAO_PEDIDOS_MCP.md``.
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
from typing import Any

import pytest

pytest.importorskip('mcp')

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAMINHO = os.path.join(RAIZ, 'mcp', 'mcp_server.py')


def _carregar():
    spec = importlib.util.spec_from_file_location('_fachada_mcp', CAMINHO)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope='module')
def fachada():
    return _carregar()


@pytest.fixture
def chamadas(fachada, monkeypatch):
    """Intercepta o ``_get``: guarda ``(path, params)`` e devolve uma resposta boba."""
    registro: list[tuple[str, dict | None]] = []

    def _fake(path: str, params: dict[str, Any] | None = None):
        registro.append((path, params))
        return {'ok': True, 'eco': path}

    monkeypatch.setattr(fachada, '_get', _fake)
    return registro


# --- registro ----------------------------------------------------------------

def test_as_tres_tools_estao_registradas(fachada):
    nomes = {t.name for t in asyncio.run(fachada.mcp.list_tools())}
    assert {'situacao_pedido', 'pedidos_bloqueados', 'panorama_pedidos'} <= nomes


def test_as_tres_sao_marcadas_como_leitura(fachada):
    """O cliente MCP mostra ao usuário que é consulta, não ação."""
    tools = {t.name: t for t in asyncio.run(fachada.mcp.list_tools())}
    for nome in ('situacao_pedido', 'pedidos_bloqueados', 'panorama_pedidos'):
        assert tools[nome].annotations.readOnlyHint is True, nome


def test_a_descricao_avisa_que_404_nao_e_sem_bloqueio(fachada):
    """A docstring é o que o modelo lê — e é lá que mora a armadilha desta view.

    Sem este aviso, "404" vira "está tudo liberado" na boca do modelo.
    """
    tools = {t.name: t for t in asyncio.run(fachada.mcp.list_tools())}
    d = tools['situacao_pedido'].description
    assert 'fora do recorte' in d
    assert 'NÃO' in d and 'sem bloqueio' in d


def test_a_descricao_avisa_a_divergencia_do_status_com_a_tela(fachada):
    """D3: o default diverge da tela DE PROPÓSITO — quem lê o número precisa saber."""
    tools = {t.name: t for t in asyncio.run(fachada.mcp.list_tools())}
    d = tools['pedidos_bloqueados'].description
    assert 'DIVERGE' in d
    assert 'status="todos"' in d


# --- situacao_pedido ---------------------------------------------------------

def test_pedido_por_docnum_e_o_default(fachada, chamadas):
    fachada.situacao_pedido(84260)
    assert chamadas == [('/pedidos/84260/situacao', None)]


def test_pedido_por_docentry_quando_pedido(fachada, chamadas):
    fachada.situacao_pedido(16586, chave='docentry')
    assert chamadas == [('/pedidos/16586/situacao', {'chave': 'docentry'})]


def test_chave_e_tolerante_a_caixa_e_espaco(fachada, chamadas):
    fachada.situacao_pedido(1, chave='  DocEntry ')
    assert chamadas[0][1] == {'chave': 'docentry'}


def test_chave_desconhecida_cai_no_docnum(fachada, chamadas):
    """Um valor esquisito não pode virar consulta por DocEntry sem querer."""
    fachada.situacao_pedido(1, chave='sei la')
    assert chamadas[0][1] is None


def test_numero_em_texto_e_aceito(fachada, chamadas):
    """O modelo às vezes manda "84260" com aspas — não é motivo para estourar."""
    fachada.situacao_pedido('84260')
    assert chamadas[0][0] == '/pedidos/84260/situacao'


def test_erro_da_api_chega_inteiro_a_quem_chamou(fachada, monkeypatch):
    """O 404 com a mensagem boa é repassado — o modelo não vê "HTTP 404" genérico."""
    corpo = {'ok': False, 'error': 'pedido 70000 fora do recorte da view', 'pedido': 70000}
    monkeypatch.setattr(fachada, '_get', lambda *_a, **_k: corpo)
    assert fachada.situacao_pedido(70000) == corpo


# --- pedidos_bloqueados ------------------------------------------------------

def test_bloqueados_usa_qualquer_e_aberto_por_padrao(fachada, chamadas):
    """D3 congelada: ``bloqueio=qualquer`` + ``status=aberto``."""
    fachada.pedidos_bloqueados()
    assert chamadas == [('/pedidos/situacao',
                         {'bloqueio': 'qualquer', 'status': 'aberto'})]


def test_bloqueados_repassa_os_parametros(fachada, chamadas):
    fachada.pedidos_bloqueados(bloqueio='financeiro', status='todos')
    assert chamadas[0][1] == {'bloqueio': 'financeiro', 'status': 'todos'}


def test_bloqueados_nao_valida_o_dominio_localmente(fachada, chamadas):
    """Quem valida é a API (422 com a mensagem certa) — uma regra, um lugar."""
    fachada.pedidos_bloqueados(bloqueio='comercial')
    assert chamadas[0][1]['bloqueio'] == 'comercial'


# --- panorama_pedidos --------------------------------------------------------

def test_panorama_usa_resumo_por_padrao(fachada, chamadas):
    """D4 congelada: a carteira inteira em ``completo`` não cabe no contexto."""
    fachada.panorama_pedidos()
    assert chamadas == [('/pedidos/situacao', {'campos': 'resumo'})]


def test_panorama_completo_quando_pedido(fachada, chamadas):
    fachada.panorama_pedidos(campos='completo')
    assert chamadas[0][1] == {'campos': 'completo'}


def test_panorama_nao_manda_filtro_nenhum(fachada, chamadas):
    """É o recorte inteiro: qualquer filtro aqui seria a tool errada."""
    fachada.panorama_pedidos()
    assert set(chamadas[0][1]) == {'campos'}
