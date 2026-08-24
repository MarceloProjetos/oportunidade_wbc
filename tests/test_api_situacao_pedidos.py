"""Testes das 2 rotas de Situacao dos Pedidos (F3) -- sem HANA.

O que se testa aqui e' a **borda HTTP**: codigo de status, corpo e a traducao de erro de
dominio. A logica de recorte ja tem dono (``test_situacao_pedidos``), e a leitura tambem
(``test_situacao_pedidos_hana``) -- repeti-la aqui so faria a suite doer duas vezes pelo
mesmo bug.

A dublagem e' no ``fetch_status_pedidos``: dali para dentro roda o codigo de verdade
(normalizar, filtrar, resumir), entao um erro no encadeamento das rotas aparece.

Plano: ``docs/PLANO_SITUACAO_PEDIDOS_MCP.md``.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

pytest.importorskip('flask')

import api as apimod  # noqa: E402
import sap_montagem_labels  # noqa: E402
import situacao_pedidos as sit_ped  # noqa: E402
import situacao_pedidos_hana as hana  # noqa: E402
from config import reset_settings  # noqa: E402


def _row(**over: Any) -> dict[str, Any]:
    """Linha crua da view, como o ``fetch_status_pedidos`` a devolve."""
    base: dict[str, Any] = {
        "DocEntry": 15118, "DocNum": 83554, "Data_Pedido": "2026-01-21",
        "CardCode": "C005324", "CardName": "JKV MADEIRAS E FERRAGENS COMERCIAL LTDA",
        "GroupNum": 967, "PymntGroup": "100% ENTREGA", "Integrar": "S",
        "Financeiro": "Liberado", "Sinal": "N", "Producao": "Liberada",
        "Entrega": "Liberada", "Data_Entrega": "2026-02-25",
        "Prazo_Entrega": "23/02 A 27/02", "Atrasado": "N", "DDO": "N", "Peso": 100.0,
        "StatusPedido": "Aberto", "Data_Lib_Fin": "2026-01-22",
        "Data_Lib_Prod": "2026-01-23", "Data_Pagto": None,
        "Total_OS": 5, "Total_OS_Fechadas": 3,
        "CotacaoWbc": "00125238", "VersaoWbc": "A", "MontagemCod": "3",
        "MontagemTexto": ".", "MontagemValor": 9700.0,
        "MontadorCnpj": "67.133.900/0001-88",
        "MontadorNome": "MONTARIM MONTAGENS INDUSTRIAIS LTDA",
        "DocTotal": 41250.0, "DocCur": "R$", "SlpCode": 12, "Vendedor": "MARCOS",
    }
    base.update(over)
    return base


def _ha(dias: int) -> str:
    """Data ISO de N dias atras.

    RELATIVA a hoje, e nao fixa: a rota nao aceita ``hoje=`` (o cliente HTTP nao tem como
    passar), entao a regra dos 10 dias e' medida contra o relogio de verdade. Com data
    fixa no fixture, o teste passaria hoje e mentiria daqui a um mes.
    """
    return (date.today() - timedelta(days=dias)).isoformat()


#: O lote das asserções: 1 limpo, 1 travado nas 3 etapas ha 48 dias, 1 travado so no
#: financeiro e ha 5 dias (abaixo do limite), 1 fechado com a producao travada.
LOTE = [
    _row(Data_Pedido=_ha(3)),
    _row(DocEntry=2, DocNum=84260, CardName="FLOW X INTERNATIONAL BRASIL LTDA",
         Data_Pedido=_ha(48), Financeiro="Bloqueado", Producao="Bloqueada",
         Entrega="Bloqueada", Sinal="S",
         PymntGroup="30% SINAL / 20% ENTREGA / 30% 45DDL / 10% 65DDL / 10% 85DDL"),
    _row(DocEntry=3, DocNum=84293, CardName="COMPANHIA DE CIMENTO CAMPEAO",
         Data_Pedido=_ha(5), Financeiro="Bloqueado",
         MontadorCnpj="", MontadorNome=""),
    _row(DocEntry=4, DocNum=83100, Data_Pedido=_ha(200), StatusPedido="Fechado",
         Producao="Bloqueada"),
]


@pytest.fixture
def client(monkeypatch):
    """API aberta (sem OS_API_KEY) + HANA dublado no ``fetch_status_pedidos``."""
    monkeypatch.delenv('OS_API_KEY', raising=False)
    reset_settings()
    sap_montagem_labels.registrar_fonte(None)   # rótulo pelo fallback, sem rede
    hana.limpar_cache()

    idas: list[bool] = []

    def _fake(*, recarregar: bool = False):
        idas.append(recarregar)
        return [dict(r) for r in LOTE]

    monkeypatch.setattr(apimod.sit_ped_hana, 'fetch_status_pedidos', _fake)
    monkeypatch.setattr(apimod.sit_ped_hana, 'idade_do_cache_s', lambda: 12.3)
    apimod.app.config.update(TESTING=True)
    c = apimod.app.test_client()
    c._idas = idas
    return c


def _quebra(monkeypatch, exc: Exception) -> None:
    def _cai(**_k):
        raise exc
    monkeypatch.setattr(apimod.sit_ped_hana, 'fetch_status_pedidos', _cai)


# --- GET /pedidos/situacao ---------------------------------------------------

def test_lista_sem_filtro_devolve_o_recorte_inteiro(client):
    r = client.get('/pedidos/situacao')
    assert r.status_code == 200
    b = r.get_json()
    assert b['ok'] is True
    assert b['total_no_recorte'] == 4
    assert b['total_filtrado'] == 4
    assert b['kpis'] == {'total': 4, 'atrasados': 0, 'financeiro_bloqueado': 2,
                         'producao_bloqueada': 2, 'entrega_bloqueada': 1}
    assert b['cache_idade_s'] == 12.3
    assert b['gerado_em']


def test_lista_traz_os_montadores_do_recorte(client):
    """A consulta 3 do plano é "todos os dados **incluindo montadores**" — 1 chamada só."""
    m = client.get('/pedidos/situacao').get_json()['montadores']
    assert len(m) == 1
    assert m[0]['nome'] == "MONTARIM MONTAGENS INDUSTRIAIS LTDA"
    assert m[0]['qtd'] == 3          # os 4 menos o que veio sem CNPJ


def test_lista_usa_resumo_por_padrao(client):
    """D4: 236 pedidos com ~40 campos não cabem no contexto de um cliente MCP."""
    p = client.get('/pedidos/situacao').get_json()['pedidos'][0]
    assert set(p) == set(sit_ped.CAMPOS_RESUMO)
    assert 'valor_total' not in p


def test_lista_com_campos_completo(client):
    p = client.get('/pedidos/situacao?campos=completo').get_json()['pedidos'][0]
    assert p['valor_total'] == 41250.0
    assert p['vendedor'] == 'MARCOS'
    assert p['montagem']['tipo'] == 'MONTAGEM POR CONTA DE TERCEIROS'


def test_bloqueio_qualquer_pega_as_tres_etapas(client):
    """A consulta 2 do plano."""
    b = client.get('/pedidos/situacao?bloqueio=qualquer').get_json()
    assert {p['doc_num'] for p in b['pedidos']} == {84260, 84293, 83100}
    assert b['total_filtrado'] == 3


def test_bloqueio_por_etapa(client):
    for etapa, esperado in (('financeiro', 2), ('producao', 2), ('entrega', 1)):
        b = client.get(f'/pedidos/situacao?bloqueio={etapa}').get_json()
        assert b['total_filtrado'] == esperado, etapa


def test_kpis_e_montadores_nao_encolhem_com_o_filtro(client):
    """O card diz quantos EXISTEM; o filtro diz quais APARECEM. Igual à tela."""
    b = client.get('/pedidos/situacao?bloqueio=financeiro').get_json()
    assert b['total_filtrado'] == 2
    assert b['total_no_recorte'] == 4
    assert b['kpis']['total'] == 4
    assert len(b['montadores']) == 1


def test_filtro_que_nao_casa_com_nada_e_200_e_nao_404(client):
    """"Não há nada bloqueado" é resposta legítima, não pedido inexistente."""
    b = client.get('/pedidos/situacao?busca=CLIENTE-QUE-NAO-EXISTE').get_json()
    assert b['ok'] is True
    assert b['pedidos'] == []
    assert b['total_filtrado'] == 0


def test_so_atrasados_fin_corta_pelos_10_dias(client):
    b = client.get('/pedidos/situacao?so_atrasados_fin=1&campos=completo').get_json()
    assert {p['doc_num'] for p in b['pedidos']} == {84260}
    assert b['pedidos'][0]['alerta_liberacao'].startswith(
        'Mais de 10 dias preso no financeiro')


def test_o_alerta_vem_junto_no_resumo(client):
    p = {x['doc_num']: x for x in client.get('/pedidos/situacao').get_json()['pedidos']}
    assert p[84260]['alerta_liberacao'].startswith('Mais de 10 dias')
    assert p[84293]['alerta_liberacao'] is None   # bloqueado, mas recente
    assert p[83554]['alerta_liberacao'] is None   # liberado


def test_status_e_montador_e_busca(client):
    assert client.get(
        '/pedidos/situacao?status=fechado').get_json()['total_filtrado'] == 1
    assert client.get(
        '/pedidos/situacao?montador=__sem__').get_json()['total_filtrado'] == 1
    assert client.get(
        '/pedidos/situacao?busca=FLOW X').get_json()['total_filtrado'] == 1


def test_parametro_fora_do_dominio_e_422(client):
    for qs in ('bloqueio=comercial', 'status=inventado'):
        r = client.get(f'/pedidos/situacao?{qs}')
        assert r.status_code == 422, qs
        assert r.get_json()['ok'] is False
        assert r.get_json()['error']                 # a mensagem vai inteira no corpo


def test_recarregar_chega_ate_a_camada_hana(client):
    client.get('/pedidos/situacao')
    client.get('/pedidos/situacao?recarregar=1')
    assert client._idas == [False, True]


def test_uma_request_faz_uma_unica_leitura(client):
    """KPIs, lista e montadores saem do MESMO retrato — nunca duas idas ao HANA."""
    client.get('/pedidos/situacao')
    assert len(client._idas) == 1


# --- GET /pedidos/<numero>/situacao ------------------------------------------

def test_pedido_por_docnum(client):
    r = client.get('/pedidos/84260/situacao')
    assert r.status_code == 200
    b = r.get_json()
    assert b['ok'] is True
    assert b['pedido']['doc_num'] == 84260
    assert b['pedido']['financeiro'] == 'Bloqueado'
    assert b['pedido']['producao'] == 'Bloqueado'      # "Bloqueada" canonizado
    assert b['pedido']['valor_total'] == 41250.0       # default aqui é completo
    assert b['cache_idade_s'] == 12.3


def test_pedido_por_docentry(client):
    """DocEntry ≠ DocNum — confundir os dois devolveria outro pedido, calado."""
    b = client.get('/pedidos/2/situacao?chave=docentry').get_json()
    assert b['pedido']['doc_num'] == 84260
    assert b['pedido']['doc_entry'] == 2
    # o mesmo número como DocNum não existe no recorte
    assert client.get('/pedidos/2/situacao').status_code == 404


def test_pedido_com_campos_resumo(client):
    p = client.get('/pedidos/84260/situacao?campos=resumo').get_json()['pedido']
    assert set(p) == set(sit_ped.CAMPOS_RESUMO)


def test_pedido_fora_do_recorte_e_404_que_nao_mente(client):
    """404 = "não está na view", NUNCA "sem bloqueio"."""
    r = client.get('/pedidos/70000/situacao')
    assert r.status_code == 404
    b = r.get_json()
    assert b['ok'] is False
    assert b['pedido'] == 70000
    assert 'fora do recorte' in b['error']
    assert 'NAO quer dizer que ele esteja sem bloqueio' in b['error']


def test_numero_invalido_e_400(client):
    for numero in ('abc', '0', '-5'):
        r = client.get(f'/pedidos/{numero}/situacao')
        assert r.status_code == 400, numero


def test_numero_ambiguo_e_409_e_nunca_o_primeiro(client, monkeypatch):
    """DocNum repetido é recusado, nunca resolvido por ``[0]``."""
    monkeypatch.setattr(apimod.sit_ped_hana, 'fetch_status_pedidos',
                        lambda **_k: [_row(DocNum=84260, DocEntry=1),
                                      _row(DocNum=84260, DocEntry=2)])
    r = client.get('/pedidos/84260/situacao')
    assert r.status_code == 409
    assert r.get_json()['total'] == 2


# --- falhas do SAP -----------------------------------------------------------

def test_hana_fora_do_ar_e_503_com_a_mensagem(client, monkeypatch):
    _quebra(monkeypatch, hana.SAPIndisponivel('SAP HANA fora do ar.'))
    for url in ('/pedidos/situacao', '/pedidos/84260/situacao'):
        r = client.get(url)
        assert r.status_code == 503, url
        assert r.get_json()['error'] == 'SAP HANA fora do ar.'


def test_guarda_de_volume_e_422(client, monkeypatch):
    """View que mudou de natureza é erro de domínio, não indisponibilidade."""
    _quebra(monkeypatch, sit_ped.ValidationError('a view mudou de natureza'))
    r = client.get('/pedidos/situacao')
    assert r.status_code == 422
    assert 'mudou de natureza' in r.get_json()['error']


def test_erro_inesperado_vira_502_e_nao_500_mudo(client, monkeypatch):
    _quebra(monkeypatch, RuntimeError('boom'))
    for url in ('/pedidos/situacao', '/pedidos/84260/situacao'):
        r = client.get(url)
        assert r.status_code == 502, url
        assert r.get_json()['ok'] is False
        assert 'boom' not in r.get_json()['error']   # detalhe interno fica no log


# --- autenticação ------------------------------------------------------------

def test_as_duas_rotas_exigem_a_chave(client, monkeypatch):
    """Rota nova sem guarda é a classe de bug que o ``requer_chave`` existe para matar."""
    monkeypatch.setenv('OS_API_KEY', 'segredo')
    reset_settings()
    try:
        for url in ('/pedidos/situacao', '/pedidos/84260/situacao'):
            assert client.get(url).status_code == 401, url
            assert client.get(url, headers={'X-API-Key': 'segredo'}).status_code == 200
    finally:
        reset_settings()
