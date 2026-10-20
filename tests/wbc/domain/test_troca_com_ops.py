"""Partner swap on an order that production already holds (PLANO_TROCA_PN_COM_OP, F2).

20/10/2026: the swap cancelled 84505 and 84506 with 110 planned OPs on them while the
replacements 84507 and 84510 got their own. Owner's decision (option B): planned OPs are
cancelled by the worker first; released or closed ones stop the swap for a person;
unknown counts stop it too.
"""

from __future__ import annotations

from wbcpython.domain.sitcode import ACOES_DE_PEDIDO, Acao, EstadoIntegracao, decidir


def _troca(**campos: object) -> EstadoIntegracao:
    base: dict[str, object] = {
        "orcamento": "00124837",
        "sitcode_wbc": 60,
        "tem_cotacao": True,
        "tem_pedido": True,
        "alterado": True,
        "parceiro_atual": "C011414",
        "parceiro_pedido_sap": "C011414",
        "parceiro_novo": "C012060",
        "ops_planejadas_pedido": 0,
        "ops_em_producao_pedido": 0,
    }
    base.update(campos)
    return EstadoIntegracao(**base)  # type: ignore[arg-type]


def _regras(decisao) -> list[str]:
    return decisao.regra.split("+")


class TestTrocaComOps:
    def test_sem_op_troca_como_antes(self) -> None:
        decisao = decidir(_troca())

        assert "troca_de_pn" in _regras(decisao)
        assert Acao.CANCELAR_E_RECRIAR_PEDIDO in decisao.acoes
        assert Acao.CANCELAR_OPS_DO_PEDIDO not in decisao.acoes

    def test_so_planejadas_cancela_as_ops_antes_do_pedido(self) -> None:
        decisao = decidir(_troca(ops_planejadas_pedido=58))

        assert "troca_de_pn_cancela_ops" in _regras(decisao)
        acoes = list(decisao.acoes)
        assert acoes.index(Acao.CANCELAR_OPS_DO_PEDIDO) < acoes.index(
            Acao.CANCELAR_E_RECRIAR_PEDIDO
        )
        assert "58 OP(s) planejada(s)" in " ".join(decisao.motivos)

    def test_op_liberada_ou_encerrada_para_a_troca(self) -> None:
        decisao = decidir(_troca(ops_planejadas_pedido=10, ops_em_producao_pedido=3))

        assert "troca_de_pn_bloqueada_op_em_producao" in _regras(decisao)
        assert not set(decisao.acoes) & ACOES_DE_PEDIDO
        assert "3 OP(s) liberada(s) ou encerrada(s)" in " ".join(decisao.motivos)

    def test_contagem_desconhecida_para_a_troca(self) -> None:
        """A guard reading zero from missing data would cancel an order in production."""
        decisao = decidir(_troca(ops_planejadas_pedido=None, ops_em_producao_pedido=None))

        assert "troca_de_pn_ops_desconhecidas" in _regras(decisao)
        assert not set(decisao.acoes) & ACOES_DE_PEDIDO

    def test_mensagem_diz_onde_a_marca_mora(self) -> None:
        """The mark is on the SAP opportunity (U_INO_PN_Correc), not in the WBC."""
        motivos = " ".join(decidir(_troca()).motivos)

        assert "oportunidade do SAP" in motivos
        assert "no WBC (" not in motivos

    def test_fora_da_janela_nao_sobra_o_cancelamento_de_op(self) -> None:
        """The order actions leave together: OP cancellation never stays on its own."""
        decisao = decidir(_troca(ops_planejadas_pedido=58, fora_da_janela_padrao=True))

        assert Acao.CANCELAR_OPS_DO_PEDIDO not in decisao.acoes
        assert Acao.CANCELAR_E_RECRIAR_PEDIDO not in decisao.acoes

    def test_pedido_ja_no_parceiro_novo_nao_cancela_op(self) -> None:
        """Idempotent: once the order is on the new partner there is no swap, OPs or not."""
        decisao = decidir(
            _troca(parceiro_pedido_sap="C012060", ops_planejadas_pedido=58, alterado=False)
        )

        assert Acao.CANCELAR_OPS_DO_PEDIDO not in decisao.acoes

    def test_pedido_fechado_continua_pedindo_gente(self) -> None:
        decisao = decidir(_troca(pedido_fechado=True, ops_planejadas_pedido=58))

        assert "troca_de_pn_impossivel_pedido_fechado" in _regras(decisao)
        assert Acao.CANCELAR_OPS_DO_PEDIDO not in decisao.acoes
