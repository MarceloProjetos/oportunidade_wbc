"""Orçamento sem item não vira decisão de criar documento.

O SAP recusa documento sem valor (`HTTP 400 | SAP -5002 | "Document total value
must be zero or greater than zero"`), então a ação nunca teria efeito. Até
17/09/2026 a decisão dizia "cria a cotação" mesmo assim e quem barrava era o
executor, depois de montar o payload e gravar um evento no acompanhamento — **a
cada ciclo, para sempre**. Com a janela de 24 meses (`PLANO_JANELA_SOB_DEMANDA`)
todo orçamento velho e vazio entrou nessa roda: o 00125188 (SitCode 20, zero
item) somou 100 eventos em um dia, um a cada 3 minutos, e foi assim que o
Marcelo viu.

O corte vive no **domínio** pela mesma razão do corte de pedido: a prévia
(`wbcpython pendentes`) e o ciclo chamam a mesma `decidir`, e um filtro na
execução deixaria o ensaio prometendo documento que o ciclo não criaria.
"""

from __future__ import annotations

from wbcpython.domain.sitcode import Acao, EstadoIntegracao, decidir


def _sem_cotacao(*, sem_itens: bool, sitcode: int = 20) -> EstadoIntegracao:
    """A situação do 00125188: SitCode 20, nenhuma cotação no SAP."""
    return EstadoIntegracao(
        orcamento="00125188",
        sitcode_wbc=sitcode,
        sitcode_sap="20",
        tem_cotacao=False,
        tem_pedido=False,
        revisao_wbc="",
        orcamento_sem_itens=sem_itens,
    )


class TestComItens:
    def test_nada_muda(self) -> None:
        """O caso normal não pode sentir a regra."""
        decisao = decidir(_sem_cotacao(sem_itens=False))
        assert Acao.CRIAR_COTACAO in decisao.acoes
        assert decisao.regra == "sem_cotacao_cria"


class TestSemItens:
    def test_nao_decide_criar_cotacao(self) -> None:
        decisao = decidir(_sem_cotacao(sem_itens=True))
        assert Acao.CRIAR_COTACAO not in decisao.acoes
        assert decisao.tem_acao is False, decisao.acoes

    def test_o_vinculo_sai_junto(self) -> None:
        """Sem documento não há o que vincular — mesma regra do corte de pedido."""
        decisao = decidir(_sem_cotacao(sem_itens=True))
        assert Acao.VINCULAR_DOCUMENTO_A_OPORTUNIDADE not in decisao.acoes

    def test_a_regra_diz_por_que(self) -> None:
        """O nome da regra e o motivo são o que a prévia e o painel mostram."""
        decisao = decidir(_sem_cotacao(sem_itens=True))
        assert decisao.regra.endswith("+sem_itens_no_wbc")
        assert any("não tem item no WBC" in m for m in decisao.motivos)

    def test_encerramento_continua_cancelando_a_cotacao(self) -> None:
        """Cancelar e espelhar status não precisam de linha — não podem sumir.

        É o orçamento perdido no WBC que já tem cotação no SAP: se o corte
        levasse estas ações junto, a cotação de um negócio perdido ficaria
        aberta para sempre.
        """
        estado = EstadoIntegracao(
            orcamento="00125188",
            sitcode_wbc=99,
            sitcode_sap="99",
            tem_cotacao=True,
            tem_pedido=False,
            orcamento_sem_itens=True,
        )
        decisao = decidir(estado)
        assert Acao.CANCELAR_COTACAO_NO_ENCERRAMENTO in decisao.acoes

    def test_volta_a_criar_quando_o_item_entra(self) -> None:
        """A decisão só olha o estado de agora: nada fica marcado como tentado."""
        assert Acao.CRIAR_COTACAO in decidir(_sem_cotacao(sem_itens=False)).acoes
