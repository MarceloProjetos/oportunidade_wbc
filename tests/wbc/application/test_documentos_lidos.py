"""Testes do adaptador que responde sobre documentos sem ir ao SAP."""

from __future__ import annotations

import pytest

from wbcpython.application.documentos_lidos import DocumentosJaLidos
from wbcpython.application.processar import fonte_de_documentos
from wbcpython.infrastructure.service_layer.documentos import TipoDocumento
from wbcpython.tracking import RepositorioTracking

COTACAO = TipoDocumento.COTACAO
PEDIDO = TipoDocumento.PEDIDO


@pytest.fixture
def tracking(tmp_path) -> RepositorioTracking:
    return RepositorioTracking.a_partir_da_url(f"sqlite:///{tmp_path}/t.db")


class RepositorioEspiao:
    """Repositório que acusa se alguém foi ao SAP sem precisar."""

    def __init__(self) -> None:
        self.chamadas = 0

    def existe(self, tipo, orcamento) -> bool:
        self.chamadas += 1
        return True

    def revisao_aplicada(self, tipo, orcamento) -> str:
        self.chamadas += 1
        return "Z"


class TestRespostas:
    def test_sem_documento(self) -> None:
        d = DocumentosJaLidos({"cotacao": None, "pedido": None})
        assert d.existe(COTACAO, "00125532") is False
        assert d.revisao_aplicada(COTACAO, "00125532") == ""
        assert d.doc_entry(COTACAO, "00125532") is None

    def test_com_documento(self) -> None:
        d = DocumentosJaLidos(
            {"cotacao": {"DocEntry": 101901, "U_INO_VERSAOWBC": "A"}, "pedido": None}
        )
        assert d.existe(COTACAO, "00125532") is True
        assert d.revisao_aplicada(COTACAO, "00125532") == "A"
        assert d.doc_entry(COTACAO, "00125532") == 101901
        assert d.existe(PEDIDO, "00125532") is False

    def test_revisao_nula_vira_string_vazia(self) -> None:
        """`''` é como a máquina de estados representa "sem revisão"."""
        d = DocumentosJaLidos({"cotacao": {"DocEntry": 1, "U_INO_VERSAOWBC": None}})
        assert d.revisao_aplicada(COTACAO, "00125532") == ""


class TestEscolhaDaFonte:
    """A escolha é por presença de dado — não há interruptor para errar."""

    def test_usa_o_que_veio_na_leitura_quando_ha_documentos(self) -> None:
        espiao = RepositorioEspiao()
        oportunidade = {"documentos": {"cotacao": None, "pedido": None}}

        fonte = fonte_de_documentos(oportunidade, espiao)
        fonte.existe(COTACAO, "00125532")
        fonte.revisao_aplicada(COTACAO, "00125532")

        assert isinstance(fonte, DocumentosJaLidos)
        assert espiao.chamadas == 0  # nenhuma ida ao SAP

    def test_cai_no_repositorio_quando_nao_ha_bloco(self) -> None:
        """Oportunidade vinda do Service Layer, ou de teste, segue como antes."""
        espiao = RepositorioEspiao()
        fonte = fonte_de_documentos({"U_ORCNUM_WBC": "00125532"}, espiao)

        assert fonte is espiao


class TestPreFiltroPelaSituacao:
    """Decidir sem carregar as linhas é o que torna a janela inteira viável.

    Carregar um orçamento completo custa 279 ms contra 29 ms da situação — e a
    grande maioria dos orçamentos da janela não tem ação. Sem este pré-filtro,
    um ciclo de 1.785 orçamentos levaria oito minutos.
    """

    def test_sem_acao_nao_carrega_o_orcamento(self, tracking) -> None:
        from wbcpython.application.processar import ProcessadorDeOrcamento

        class WbcEspiao:
            def __init__(self) -> None:
                self.buscas = 0

            def buscar_orcamento(self, orcnum):
                self.buscas += 1
                raise AssertionError("não deveria carregar o orçamento sem ação")

        wbc = WbcEspiao()
        oportunidade = {
            "SequentialNo": 77,
            "U_ORCNUM_WBC": "00125532",
            "U_INO_StatusWBC": "40",
            "documentos": {"cotacao": {"DocEntry": 1, "U_INO_VERSAOWBC": ""}, "pedido": None},
        }

        resultado = ProcessadorDeOrcamento(
            wbc=wbc, orcdetalhe=None, documentos=None, oportunidades=None, tracking=tracking
        ).processar(oportunidade, situacao=(40, ""))

        assert wbc.buscas == 0
        assert resultado.sucesso
        assert resultado.acoes_executadas == ()

    def test_com_acao_carrega_o_orcamento(self, tracking) -> None:
        """Quem age precisa das linhas — e refaz a decisão sobre elas."""
        from wbcpython.application.processar import ProcessadorDeOrcamento

        class WbcQueCarrega:
            def __init__(self) -> None:
                self.buscas = 0

            def buscar_orcamento(self, orcnum):
                self.buscas += 1

        wbc = WbcQueCarrega()
        oportunidade = {
            "SequentialNo": 77,
            "U_ORCNUM_WBC": "00125532",
            "U_INO_StatusWBC": "0",
            "documentos": {"cotacao": None, "pedido": None},
        }

        ProcessadorDeOrcamento(
            wbc=wbc, orcdetalhe=None, documentos=None, oportunidades=None, tracking=tracking
        ).processar(oportunidade, situacao=(40, ""))

        assert wbc.buscas == 1
