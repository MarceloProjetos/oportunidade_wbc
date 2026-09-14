"""Janela estendida acerta cotação e oportunidade, e não mexe em pedido.

Regra de negócio do Marcelo (14/09/2026), depois do ensaio de 13 meses em
produção: aquela leva de 144 escritas incluía **criar pedido** para
oportunidades de 2025, uma delas somando R$ 1,26 milhão em linhas. Alcançar
para trás serve para acertar a cotação e o espelho da oportunidade; abrir um
pedido de um negócio de mais de seis meses é outra decisão — e alguém pode já
tê-lo resolvido à mão no SAP nesse tempo.

O corte vive no **domínio** de propósito: a prévia (`wbcpython pendentes`) e o
ciclo chamam a mesma `decidir`. Um filtro na execução deixaria o ensaio
prometendo pedidos que o ciclo não criaria — que é exatamente a mentira que o
ensaio existe para não contar.
"""

from __future__ import annotations

import pytest

from wbcpython.domain.sitcode import Acao, EstadoIntegracao, decidir


def _emitido_com_pedido_a_criar(*, fora: bool) -> EstadoIntegracao:
    """SitCode 60 com cotação e sem pedido: a regra `cria_pedido`."""
    return EstadoIntegracao(
        orcamento="00122598",
        sitcode_wbc=60,
        sitcode_sap="60",
        tem_cotacao=True,
        tem_pedido=False,
        fora_da_janela_padrao=fora,
    )


class TestDentroDaJanela:
    def test_nada_muda(self) -> None:
        """O ciclo normal não pode sentir a regra: dentro da janela padrão
        nenhuma oportunidade lida é anterior ao corte."""
        decisao = decidir(_emitido_com_pedido_a_criar(fora=False))

        assert Acao.CRIAR_PEDIDO in decisao.acoes
        assert "sem_pedido" not in decisao.regra


class TestForaDaJanela:
    def test_o_pedido_nao_e_criado(self) -> None:
        decisao = decidir(_emitido_com_pedido_a_criar(fora=True))

        assert Acao.CRIAR_PEDIDO not in decisao.acoes

    def test_a_cotacao_continua(self) -> None:
        """Cotação é proposta, não compromisso — ela é o motivo de alcançar
        para trás."""
        decisao = decidir(_emitido_com_pedido_a_criar(fora=True))

        assert Acao.ATUALIZAR_COTACAO in decisao.acoes

    def test_a_regra_diz_o_que_aconteceu(self) -> None:
        """Sem isso, o painel mostraria `cria_pedido` numa linha que não cria
        pedido nenhum — e ninguém entenderia o ensaio."""
        decisao = decidir(_emitido_com_pedido_a_criar(fora=True))

        assert decisao.regra.endswith("+sem_pedido_fora_da_janela")
        assert any("não cria nem altera pedido" in m for m in decisao.motivos)

    @pytest.mark.parametrize(
        "acao",
        [Acao.CRIAR_PEDIDO, Acao.ATUALIZAR_PEDIDO, Acao.CANCELAR_E_RECRIAR_PEDIDO],
    )
    def test_nenhuma_acao_de_pedido_passa(self, acao: Acao) -> None:
        """As três, e não só a de criar: alterar e recriar também mexem num
        documento de compromisso."""
        from wbcpython.domain.sitcode import ACOES_DE_PEDIDO

        assert acao in ACOES_DE_PEDIDO

    def test_encerramento_cancela_a_cotacao_normalmente(self) -> None:
        """SitCode 99 fora da janela: o cancelamento da cotação continua.

        É o caso mais comum do ensaio de 13 meses — dezenas de orçamentos
        cancelados no WBC com a cotação ainda aberta no SAP. Bloqueá-los seria
        tirar justamente o que a janela estendida tem de útil e barato.
        """
        decisao = decidir(
            EstadoIntegracao(
                orcamento="00122842",
                sitcode_wbc=99,
                sitcode_sap="99",
                status_oportunidade="L",
                tem_cotacao=True,
                fora_da_janela_padrao=True,
            )
        )

        assert Acao.CANCELAR_COTACAO_NO_ENCERRAMENTO in decisao.acoes

    def test_decisao_sem_pedido_nenhum_nao_e_tocada(self) -> None:
        """Quem já não mexia em pedido sai igual — inclusive a `regra`, que o
        painel usa para agrupar."""
        dentro = decidir(
            EstadoIntegracao(orcamento="00124035", sitcode_wbc=6, fora_da_janela_padrao=False)
        )
        fora = decidir(
            EstadoIntegracao(orcamento="00124035", sitcode_wbc=6, fora_da_janela_padrao=True)
        )

        assert fora == dentro
