"""OP read/cancel over the Service Layer and the Controle de Produção busy probe (F2)."""

from __future__ import annotations

import io
import urllib.error

import pytest

from wbcpython.infrastructure import controle_producao
from wbcpython.infrastructure.controle_producao import controle_producao_ocupado
from wbcpython.infrastructure.service_layer.ordens_producao import (
    RepositorioOrdensProducaoServiceLayer,
)


class ClienteFalso:
    def __init__(self, registros) -> None:
        self._registros = registros
        self.listagens: list[dict] = []
        self.patches: list[tuple[str, dict]] = []

    def listar(self, entidade, **kw):
        self.listagens.append({"entidade": entidade, **kw})
        return self._registros

    def patch(self, caminho, *, json, headers=None):
        self.patches.append((caminho, json))


class TestRepositorio:
    def test_le_as_vivas_do_pedido_pelo_vinculo_da_origem(self) -> None:
        cliente = ClienteFalso(
            [{"AbsoluteEntry": 160867, "DocumentNumber": 159019, "ProductionOrderStatus": "boposPlanned"}]
        )
        ops = RepositorioOrdensProducaoServiceLayer(cliente).vivas(20666)

        consulta = cliente.listagens[0]
        assert consulta["entidade"] == "ProductionOrders"
        assert "ProductionOrderOriginEntry eq 20666" in consulta["filtro"]
        assert "ProductionOrderStatus ne 'boposCancelled'" in consulta["filtro"]
        assert [(o.doc_entry, o.doc_num, o.planejada) for o in ops] == [(160867, 159019, True)]

    def test_cancela_com_o_mesmo_patch_do_controle_de_producao(self) -> None:
        cliente = ClienteFalso([])
        RepositorioOrdensProducaoServiceLayer(cliente).cancelar(160867)

        assert cliente.patches == [
            ("ProductionOrders(160867)", {"ProductionOrderStatus": "boposCancelled"})
        ]


class _Resposta(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestControleDeProducaoOcupado:
    @pytest.mark.parametrize(("corpo", "esperado"), [(b"1", True), (b"0\n", False), (b"ok", None)])
    def test_resposta(self, monkeypatch, corpo, esperado) -> None:
        monkeypatch.setattr(controle_producao._SEM_PROXY, "open", lambda url, timeout: _Resposta(corpo))
        assert controle_producao_ocupado(8080) is esperado

    def test_servico_parado_nao_esta_processando(self, monkeypatch) -> None:
        def recusa(url, timeout):
            raise urllib.error.URLError(ConnectionRefusedError(10061, "recusada"))

        monkeypatch.setattr(controle_producao._SEM_PROXY, "open", recusa)
        assert controle_producao_ocupado(8080) is False

    def test_timeout_e_desconhecido(self, monkeypatch) -> None:
        def demora(url, timeout):
            raise TimeoutError("timed out")

        monkeypatch.setattr(controle_producao._SEM_PROXY, "open", demora)
        assert controle_producao_ocupado(8080) is None

    def test_resposta_http_quebrada_e_desconhecida(self, monkeypatch) -> None:
        """`http.client.HTTPException` is not an OSError (review of the F2)."""
        import http.client

        def quebrada(url, timeout):
            raise http.client.BadStatusLine("lixo")

        monkeypatch.setattr(controle_producao._SEM_PROXY, "open", quebrada)
        assert controle_producao_ocupado(8080) is None

    def test_pergunta_na_propria_maquina(self, monkeypatch) -> None:
        urls: list[str] = []

        def anota(url, timeout):
            urls.append(url)
            return _Resposta(b"0")

        monkeypatch.setattr(controle_producao._SEM_PROXY, "open", anota)
        controle_producao_ocupado(8080)
        assert urls == ["http://127.0.0.1:8080/health/ocupado"]
