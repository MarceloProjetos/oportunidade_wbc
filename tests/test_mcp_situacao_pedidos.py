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
    # Semântica, não caixa alta: o aviso precisa dizer que 404 ≠ "sem bloqueio" e que
    # não se inventa "liberado" — o tom da frase é livre.
    assert 'sem bloqueio' in d and 'invente "está liberado"' in d


def test_a_descricao_manda_dizer_cancelado(fachada):
    """Cancelado chega como 200 — sem isto o modelo lê "Cancelado" e responde "liberado"."""
    tools = {t.name: t for t in asyncio.run(fachada.mcp.list_tools())}
    d = tools['situacao_pedido'].description
    assert 'Cancelado' in d and 'pedido_cancelado' in d


def test_a_descricao_avisa_a_divergencia_do_status_com_a_tela(fachada):
    """D3: o default diverge da tela DE PROPÓSITO — quem lê o número precisa saber."""
    tools = {t.name: t for t in asyncio.run(fachada.mcp.list_tools())}
    d = tools['pedidos_bloqueados'].description
    assert 'diverge' in d.lower()          # semântica, não caixa alta
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


# --- panorama_pedidos: teto, ordenação e filtros (F1 do PLANO_UX_FACHADA_MCP) ------------

def _carteira(n: int = 300) -> dict:
    """Carteira sintética: i % 10 == 0 atrasado; i % 3 == 0 com 2 bloqueios; 2 montadores."""
    pedidos = []
    for i in range(n):
        pedidos.append({
            'doc_num': 80000 + i, 'card_name': f'CLIENTE {i}', 'data_pedido': f'2026-{1 + i % 9:02d}-01',
            'atrasado': i % 10 == 0,
            'financeiro': 'Bloqueado' if i % 3 == 0 else 'Liberado',
            'producao': 'Bloqueado' if i % 3 == 0 else 'Liberado',
            'entrega': 'Liberado', 'sinal': True, 'prazo_entrega': '', 'pymnt_group': '',
            'alerta_liberacao': None,
            'montagem': 'BARROS MONTAGENS' if i % 2 == 0 else 'DAPPER CROSS',
            'vendedor': 'JOÃO' if i % 4 == 0 else 'MARIA',
        })
    return {'ok': True, 'pedidos': pedidos, 'total_filtrado': n, 'total_no_recorte': n,
            'kpis': {'total': n, 'atrasados': n // 10}, 'montadores': [{'nome': 'X', 'qtd': n}],
            'cache_idade_s': 1.0}


@pytest.fixture
def carteira(fachada, monkeypatch):
    registro: list[tuple[str, dict | None]] = []

    def _fake(path, params=None):
        registro.append((path, params))
        return _carteira()

    monkeypatch.setattr(fachada, '_get', _fake)
    return registro


def test_panorama_corta_em_40_e_avisa(fachada, carteira):
    """300 pedidos → 40 na lista; kpis e montadores intactos; truncado dito com todas as letras."""
    r = fachada.panorama_pedidos()
    assert len(r['pedidos']) == 40
    assert r['truncado'] is True and r['mostrando'] == 40 and r['total_filtrado'] == 300
    assert r['kpis']['total'] == 300 and r['montadores'][0]['qtd'] == 300
    assert 'limite' in r['aviso']


def test_panorama_atrasados_e_bloqueados_vem_primeiro(fachada, carteira):
    r = fachada.panorama_pedidos()
    primeiros = r['pedidos']
    assert all(p['atrasado'] for p in primeiros[:30])          # os 30 atrasados da carteira
    # entre os não atrasados, quem tem 2 bloqueios vem antes de quem tem 0
    resto = primeiros[30:]
    assert all(p['financeiro'] == 'Bloqueado' for p in resto)


def test_panorama_limite_0_sem_filtro_volta_ao_default(fachada, carteira):
    """Sem teto só com filtro: a carteira inteira não cabe na conversa."""
    r = fachada.panorama_pedidos(limite=0)
    assert len(r['pedidos']) == 40 and r['truncado'] is True


def test_panorama_filtro_montador_busca_completo_e_projeta_resumo(fachada, carteira):
    r = fachada.panorama_pedidos(montador='barros')
    assert carteira == [('/pedidos/situacao', {'campos': 'completo'})]
    assert r['total_filtrado'] == 150 and len(r['pedidos']) == 40
    assert all('BARROS' in p['montagem'] for p in r['pedidos'])
    assert 'valor_total' not in r['pedidos'][0]        # projetado de volta ao resumo
    assert r['filtro']['montador'] == 'barros'


def test_panorama_filtro_vendedor_sem_acento(fachada, carteira):
    r = fachada.panorama_pedidos(vendedor='joao', limite=0)
    assert r['total_filtrado'] == 75 and len(r['pedidos']) == 75 and 'truncado' not in r


def test_panorama_so_atrasados(fachada, carteira):
    r = fachada.panorama_pedidos(so_atrasados=True)
    assert carteira == [('/pedidos/situacao', {'campos': 'resumo'})]   # atrasado existe no resumo
    assert r['total_filtrado'] == 30 and len(r['pedidos']) == 30 and 'truncado' not in r


def test_panorama_completo_respeita_o_teto(fachada, carteira):
    r = fachada.panorama_pedidos(campos='completo', limite=5)
    assert len(r['pedidos']) == 5 and 'montagem' in r['pedidos'][0]


def test_panorama_erro_do_get_passa_inteiro(fachada, monkeypatch):
    monkeypatch.setattr(fachada, '_get', lambda *a, **k: {'ok': False, 'erro': 'HTTP 503'})
    assert fachada.panorama_pedidos() == {'ok': False, 'erro': 'HTTP 503'}


# --- F2: o servidor se apresenta; 404 de rota inexistente vira dica em qualquer tool ------

def test_servidor_tem_instructions_com_a_maquina_certa(fachada):
    ins = fachada.mcp.instructions
    assert '192.168.7.11' in ins and 'sap-rdp' in ins and 'confirmar=True' in ins


class _Resp:
    def __init__(self, status, text='<html>Not Found</html>'):
        self.status_code, self.text = status, text

    def json(self):
        raise ValueError('não é JSON')


def test_404_html_em_qualquer_rota_traz_dica_de_versao(fachada, monkeypatch):
    monkeypatch.setattr(fachada.httpx, 'get', lambda *a, **k: _Resp(404))
    r = fachada._get('/historico')
    assert r['ok'] is False and 'OrcaView-OS-API' in r['dica'] and 'HTTP 404' in r['erro']


def test_500_html_nao_ganha_dica_de_versao(fachada, monkeypatch):
    monkeypatch.setattr(fachada.httpx, 'get', lambda *a, **k: _Resp(500, 'boom'))
    r = fachada._get('/historico')
    assert r['ok'] is False and 'dica' not in r and r['corpo'] == 'boom'


def test_post_usa_o_mesmo_tratamento(fachada, monkeypatch):
    monkeypatch.setattr(fachada.httpx, 'post', lambda *a, **k: _Resp(404))
    r = fachada._post('/ordens-servico/1/sincronizar')
    assert 'dica' in r
