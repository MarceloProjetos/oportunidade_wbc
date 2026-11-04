"""Every order the API sends has a status: SAP's, else the last known (2026-11-04)."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

import pandas as pd
import pytest

import api as apimod
import extract_ordens_servico_engenharia as extract
import situacao_pedidos_hana as hana
import ultimo_status_pedido as ultimo
from config import reset_settings
from tests.test_api_situacao_pedidos import _ordr, _row


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv('OS_API_KEY', raising=False)
    reset_settings()
    hana.limpar_cache()

    def _sem_base() -> None:
        raise RuntimeError('base dublada fora')

    monkeypatch.setattr(apimod, '_supabase', _sem_base)
    monkeypatch.setattr(apimod.sit_ped_hana, 'idade_do_cache_s', lambda: 1.0)
    monkeypatch.setattr(apimod.sit_ped_hana, 'fetch_endereco_do_pedido', lambda n: {})
    monkeypatch.setattr(apimod.sit_ped_hana, 'fetch_status_pedidos',
                        lambda *, recarregar=False: [_row(DocNum=84260, DocEntry=1, StatusPedido='Aberto'),
                                                     _row(DocNum=84261, DocEntry=2, StatusPedido='Fechado')])
    monkeypatch.setattr(apimod, 'consultar_status_pedido', lambda n: _ordr())
    apimod.app.config.update(TESTING=True)
    return apimod.app.test_client()


def _sap_mudo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(apimod, 'consultar_status_pedido', lambda n: None)


# ------------------------------------------------------------- the list


@pytest.mark.parametrize('campos', ['resumo', 'completo'])
def test_lista_manda_status_de_todos_os_pedidos(client: Any, campos: str) -> None:
    """The default profile sent no status at all until 2026-11-04: 97 of 97 orders."""
    pedidos = client.get(f'/pedidos/situacao?campos={campos}').get_json()['pedidos']
    assert {p['doc_num']: p['status_pedido'] for p in pedidos} == {84260: 'Aberto', 84261: 'Fechado'}


def test_linha_sem_status_na_view_sai_com_o_ultimo_conhecido(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    ultimo.registrar(84260, 'Fechado')
    monkeypatch.setattr(apimod.sit_ped_hana, 'fetch_status_pedidos',
                        lambda *, recarregar=False: [_row(DocNum=84260, DocEntry=1, StatusPedido=None)])
    p = client.get('/pedidos/situacao').get_json()['pedidos'][0]
    assert p['status_pedido'] == 'Fechado'
    assert p['status_origem'] == 'ultimo_conhecido' and p['status_em']


def test_lista_guarda_o_status_para_a_proxima_falha(client: Any) -> None:
    client.get('/pedidos/situacao')
    assert ultimo.ler(84261)['status'] == 'Fechado'
    assert ultimo.ler(2, 'doc_entry')['status'] == 'Fechado'


# ------------------------------------------------------------- one order


def test_sap_fora_manda_o_ultimo_conhecido_e_mantem_o_404(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """The HTTP code, the motivo and ``pedido_cancelado`` keep saying that SAP did not answer now."""
    ultimo.registrar(70000, 'Cancelado')
    _sap_mudo(monkeypatch)
    r = client.get('/pedidos/70000/situacao')
    b = r.get_json()
    assert r.status_code == 404 and b['motivo'] == 'indeterminado' and b['pedido_cancelado'] is None
    assert b['status_pedido'] == 'Cancelado'
    assert b['status_origem'] == 'ultimo_conhecido' and b['status_em']


def test_hana_fora_manda_o_ultimo_conhecido_no_503(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    ultimo.registrar(84260, 'Aberto')

    def _cai(*, recarregar: bool = False) -> list:
        raise hana.SAPIndisponivel('SAP HANA fora do ar.')

    monkeypatch.setattr(apimod.sit_ped_hana, 'fetch_status_pedidos', _cai)
    r = client.get('/pedidos/84260/situacao')
    b = r.get_json()
    assert r.status_code == 503 and b['status_pedido'] == 'Aberto' and b['status_origem'] == 'ultimo_conhecido'


def test_nunca_visto_com_sap_fora_sai_desconhecido(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    _sap_mudo(monkeypatch)
    b = client.get('/pedidos/70000/situacao').get_json()
    assert b['status_pedido'] == 'Desconhecido'
    assert b['status_origem'] == 'sem_historico' and b['status_em'] is None


def test_numero_que_o_sap_nao_tem_nao_e_pedido(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(apimod, 'consultar_status_pedido', lambda n: _ordr(pedido_existe=False, pedido_status=None))
    b = client.get('/pedidos/99999/situacao').get_json()
    assert b['motivo'] == 'pedido_nao_encontrado' and b['status_pedido'] is None and 'status_origem' not in b


def test_resposta_do_sap_nao_leva_status_origem(client: Any) -> None:
    b = client.get('/pedidos/84260/situacao').get_json()
    assert b['status_pedido'] == 'Aberto' and 'status_origem' not in b and 'status_origem' not in b['pedido']


@pytest.mark.parametrize('status', ['Cancelado', 'Fechado', 'Aberto'])
def test_qualquer_status_fica_guardado_pela_rota_do_pedido(client: Any, monkeypatch: pytest.MonkeyPatch,
                                                           status: str) -> None:
    monkeypatch.setattr(apimod, 'consultar_status_pedido',
                        lambda n: _ordr(pedido_status=status, pedido_cancelado=status == 'Cancelado'))
    assert client.get('/pedidos/70000/situacao').get_json()['status_pedido'] == status
    assert ultimo.ler(70000)['status'] == status


def test_por_docentry_usa_o_ultimo_conhecido_daquele_docentry(client: Any) -> None:
    ultimo.registrar(70000, 'Fechado', 5555)
    b = client.get('/pedidos/5555/situacao?chave=docentry').get_json()
    assert b['status_pedido'] == 'Fechado' and b['status_origem'] == 'ultimo_conhecido'


# ------------------------------------------------------------- the other routes


def test_lista_de_os_preenche_pedido_sem_status(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    ultimo.registrar(70000, 'Fechado')
    monkeypatch.setattr(apimod, 'listar_pedidos_com_os', lambda limit: [
        {'nped': 70000, 'status_pedido': None, 'pedido_cancelado': None},
        {'nped': 84313, 'status_pedido': 'Aberto', 'pedido_cancelado': False},
    ])
    itens = {i['nped']: i for i in client.get('/ordens-servico/disponiveis').get_json()['items']}
    assert itens[70000]['status_pedido'] == 'Fechado' and itens[70000]['status_origem'] == 'ultimo_conhecido'
    assert itens[84313]['status_pedido'] == 'Aberto' and 'status_origem' not in itens[84313]


def test_resultado_da_sincronizacao_preenche_o_status(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    ultimo.registrar(70000, 'Aberto')
    monkeypatch.setattr(apimod, 'diagnosticar_nped', lambda n: {'erro': 'hana caiu'})
    monkeypatch.setattr(apimod, 'sync_os', lambda n: False)
    assert apimod._com_status_garantido(apimod._sync_one(70000))['status_pedido'] == 'Aberto'


def test_numero_inexistente_na_sincronizacao_fica_sem_status() -> None:
    r = {'nped': 1, 'ok': False, 'tipo': 'pedido_nao_encontrado', 'status_pedido': None}
    assert apimod._com_status_garantido(r) == r


def test_status_guardado_nao_decide_nada(monkeypatch: pytest.MonkeyPatch) -> None:
    """A remembered "Fechado" must not read as "not late": without SAP the delay stays unknown."""
    ultimo.registrar(70000, 'Fechado')
    monkeypatch.setattr(apimod.sit_ped_hana, 'idade_do_cache_s', lambda: None)
    with apimod.app.test_request_context('/'):
        bloco = apimod._bloco_entrega(70000, [], None, sap_disponivel=False, status=None)
    assert bloco['status_pedido'] == 'Fechado' and bloco['status_origem'] == 'ultimo_conhecido'
    assert bloco['atrasado'] is None


# ------------------------------------------------------------- the store


def _grava_contando(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    gravacoes: list[int] = []
    original: Callable[[Any], sqlite3.Connection] = ultimo._conectar

    def _conta(arquivo: Any) -> sqlite3.Connection:
        gravacoes.append(1)
        return original(arquivo)

    monkeypatch.setattr(ultimo, '_conectar', _conta)
    return gravacoes


def test_status_igual_nao_grava_de_novo_mas_status_novo_grava(monkeypatch: pytest.MonkeyPatch) -> None:
    gravacoes = _grava_contando(monkeypatch)
    ultimo.registrar(1, 'Aberto')
    ultimo.registrar(1, 'Aberto')
    assert len(gravacoes) == 1
    ultimo.registrar(1, 'Fechado')
    assert len(gravacoes) == 2 and ultimo.ler(1)['status'] == 'Fechado'


def test_status_vazio_nao_e_conhecimento() -> None:
    ultimo.registrar(1, 'Aberto')
    ultimo.registrar(1, '  ')
    ultimo.registrar(1, None)
    assert ultimo.ler(1)['status'] == 'Aberto'


def test_carga_inicial_nao_apaga_o_que_ja_se_sabe() -> None:
    ultimo.registrar(1, 'Aberto')
    assert ultimo.semear([(1, 10, 'Fechado'), (2, 20, 'Fechado')]) == 2
    assert ultimo.ler(1)['status'] == 'Aberto' and ultimo.ler(2)['status'] == 'Fechado'


def test_arquivo_quebrado_custa_o_fallback_nunca_a_resposta(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.setenv('SIS_ULTIMO_STATUS_ARQUIVO', str(tmp_path))  # a folder: sqlite cannot open it
    ultimo.registrar(1, 'Aberto')
    assert ultimo.semear([(1, 10, 'Aberto')]) == 0
    assert ultimo.ler(1) is None


def test_lista_todos_os_pedidos_da_ordr(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Sap:
        def __init__(self, *a: Any) -> None:
            pass

        def connect(self) -> bool:
            return True

        def execute_query(self, *a: Any) -> pd.DataFrame:
            return pd.DataFrame({'DocNum': [1, 2, 3], 'DocEntry': [10, 20, 30],
                                 'CANCELED': ['N', 'Y', 'N'], 'DocStatus': ['O', 'C', 'C']})

        def close(self) -> None:
            pass

    for k, v in {'SAP_HOST': 'h', 'SAP_PORT': '1', 'SAP_USER': 'u', 'SAP_PASSWORD': 'p',
                 'SAP_DATABASE': 'd'}.items():
        monkeypatch.setenv(k, v)
    reset_settings()
    monkeypatch.setattr(extract, 'SAPExtractor', _Sap)
    assert extract.listar_status_dos_pedidos() == [(1, 10, 'Aberto'), (2, 20, 'Cancelado'), (3, 30, 'Fechado')]


def test_carga_inicial_sem_sap_nao_derruba_a_api(monkeypatch: pytest.MonkeyPatch) -> None:
    def _cai() -> list:
        raise RuntimeError('hana caiu')

    monkeypatch.setattr(apimod, 'listar_status_dos_pedidos', _cai)
    apimod._semear_ultimo_status()
