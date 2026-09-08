"""Testes da camada de dados do dashboard (sem subir o Streamlit)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from wbcpython.dashboard import (
    calcular_kpis,
    linha_para_tabela,
    registrar_reprocessamento,
    resumo_de_execucao,
)
from wbcpython.tracking import RepositorioTracking, StatusIntegracao, TipoEvento


@pytest.fixture
def repo(tmp_path) -> RepositorioTracking:
    return RepositorioTracking.a_partir_da_url(f"sqlite:///{tmp_path}/d.db")


class TestKpis:
    def test_conta_por_situacao(self, repo: RepositorioTracking) -> None:
        repo.registrar_verificacao("A", status=StatusIntegracao.COTACAO_CRIADA)
        repo.registrar_verificacao("B", status=StatusIntegracao.ERRO)
        repo.registrar_verificacao("C", status=StatusIntegracao.ENCERRADA)

        kpis = calcular_kpis(repo.listar())
        assert kpis.total == 3
        assert kpis.com_erro == 1
        assert kpis.encerradas == 1

    def test_soma_o_valor_dos_pedidos(self, repo: RepositorioTracking) -> None:
        repo.registrar_documento("A", tipo="pedido", doc_entry=1, valor=Decimal("100.50"))
        repo.registrar_documento("B", tipo="pedido", doc_entry=2, valor=Decimal("200.00"))
        repo.registrar_documento("C", tipo="cotacao", doc_entry=3, valor=Decimal(999))

        kpis = calcular_kpis(repo.listar())
        assert kpis.com_pedido == 2
        assert kpis.com_cotacao == 1
        assert kpis.valor_em_pedidos == Decimal("300.50")

    def test_taxa_de_erro(self, repo: RepositorioTracking) -> None:
        for i in range(9):
            repo.registrar_verificacao(f"OK{i}", status=StatusIntegracao.COTACAO_CRIADA)
        repo.registrar_verificacao("ERRO", status=StatusIntegracao.ERRO)
        assert calcular_kpis(repo.listar()).taxa_de_erro == pytest.approx(10.0)

    def test_sem_dados_nao_divide_por_zero(self, repo: RepositorioTracking) -> None:
        kpis = calcular_kpis([])
        assert kpis.taxa_de_erro == 0.0
        assert kpis.saude == "sem_dados"


class TestSemaforo:
    def test_verde_sem_erros(self, repo: RepositorioTracking) -> None:
        repo.registrar_verificacao("A", status=StatusIntegracao.COTACAO_CRIADA)
        assert calcular_kpis(repo.listar()).saude == "ok"

    def test_um_erro_ja_tira_do_verde(self, repo: RepositorioTracking) -> None:
        # Decisão deliberada: em fluxo que cria documento financeiro, um erro
        # isolado merece atenção, não é ruído estatístico.
        for i in range(50):
            repo.registrar_verificacao(f"OK{i}", status=StatusIntegracao.COTACAO_CRIADA)
        repo.registrar_verificacao("E", status=StatusIntegracao.ERRO)
        assert calcular_kpis(repo.listar()).saude == "atencao"

    def test_muitos_erros_vira_critico(self, repo: RepositorioTracking) -> None:
        for i in range(5):
            repo.registrar_verificacao(f"E{i}", status=StatusIntegracao.ERRO)
        repo.registrar_verificacao("OK", status=StatusIntegracao.COTACAO_CRIADA)
        assert calcular_kpis(repo.listar()).saude == "critico"


class TestFormatacao:
    def test_linha_da_tabela(self, repo: RepositorioTracking) -> None:
        repo.registrar_verificacao(
            "00123316",
            cliente="BALTEAU",
            vendedor="043",
            uf="MG",
            sitcode_wbc=40,
            revisao_wbc="C",
            status=StatusIntegracao.PEDIDO_CRIADO,
        )
        repo.registrar_documento(
            "00123316", tipo="pedido", doc_entry=1, doc_num=900, valor=Decimal("72628.83")
        )
        linha = linha_para_tabela(repo.obter("00123316"))

        assert linha["Orçamento"] == "00123316"
        assert linha["Cliente"] == "BALTEAU"
        assert linha["Situação"] == "Pedido criado"
        assert linha["Pedido"] == 900

    def test_valor_em_formato_brasileiro(self, repo: RepositorioTracking) -> None:
        repo.registrar_documento("X", tipo="pedido", doc_entry=1, valor=Decimal("72628.83"))
        assert linha_para_tabela(repo.obter("X"))["Valor do pedido"] == "R$ 72.628,83"

    def test_sem_valor_fica_vazio(self, repo: RepositorioTracking) -> None:
        repo.registrar_verificacao("X")
        assert linha_para_tabela(repo.obter("X"))["Valor do pedido"] == ""

    def test_sem_verificacao_mostra_travessao(self, repo: RepositorioTracking) -> None:
        repo.registrar_evento("X", mensagem="criado sem verificação")
        assert linha_para_tabela(repo.obter("X"))["Última verificação"] == "—"


class TestExecucoes:
    def test_resumo(self, repo: RepositorioTracking) -> None:
        eid = repo.iniciar_execucao()
        repo.finalizar_execucao(eid, processados=5, sucessos=4, erros=1)
        resumo = resumo_de_execucao(repo.ultimas_execucoes()[0])
        assert resumo["Situação"] == "Concluída"
        assert resumo["Erros"] == 1
        assert resumo["Duração"].endswith("s")

    def test_em_andamento(self, repo: RepositorioTracking) -> None:
        repo.iniciar_execucao()
        assert resumo_de_execucao(repo.ultimas_execucoes()[0])["Duração"] == "em andamento"


class TestReprocessamento:
    def test_registra_quem_pediu(self, repo: RepositorioTracking) -> None:
        """Ação manual que gera documento no SAP precisa deixar rastro."""
        registrar_reprocessamento(repo, "00123316", solicitante="anderson")
        eventos = repo.eventos("00123316")
        assert eventos[0].tipo is TipoEvento.REPROCESSAMENTO
        assert "anderson" in eventos[0].mensagem
        assert "anderson" in eventos[0].detalhes


class TestAvaliadoVersusComAcao:
    """A confusão que fazia o painel anunciar 100% de sucesso.

    O ciclo avalia a janela inteira de propósito. Contar tudo como "processado
    com sucesso" anunciava centenas de orçamentos integrados quando nenhum
    havia sido tocado — e essa é a linha que alguém lê para decidir se precisa
    agir.
    """

    def test_sem_acao_nao_conta_como_acao(self, repo: RepositorioTracking) -> None:
        repo.registrar_verificacao("A", status=StatusIntegracao.SEM_ACAO)
        repo.registrar_verificacao("B", status=StatusIntegracao.SEM_ACAO)
        repo.registrar_verificacao("C", status=StatusIntegracao.PEDIDO_CRIADO)

        kpis = calcular_kpis(repo.listar())
        assert kpis.total == 3
        assert kpis.sem_acao == 2
        assert kpis.com_acao == 1

    def test_pendente_nao_e_acao(self, repo: RepositorioTracking) -> None:
        """Pendente é o que ainda não foi decidido — nada foi feito com ele."""
        repo.registrar_verificacao("A", status=StatusIntegracao.PENDENTE)
        assert calcular_kpis(repo.listar()).com_acao == 0

    def test_encerrada_conta_como_acao(self, repo: RepositorioTracking) -> None:
        repo.registrar_verificacao("A", status=StatusIntegracao.ENCERRADA)
        assert calcular_kpis(repo.listar()).com_acao == 1
