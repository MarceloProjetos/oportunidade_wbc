"""As regras puras da janela sob demanda: bandas, teto e prazo.

A tabela de bandas é a parte do plano que o Marcelo decidiu à mão (11/09/2026) e
a que mais custa caro se alguém "simplificar" depois: ela é o que separa "o
ciclo estendido escreve o que precisa" de "o ciclo estendido escreve 200 e
abandona o resto". Por isso cada borda tem teste próprio.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from wbcpython.domain import janela as jn


class TestMultiplicadorDoTeto:
    @pytest.mark.parametrize(
        ("meses", "esperado"),
        [
            (1, 1),
            (6, 1),  # o padrão não ganha folga nenhuma
            (7, 3),  # primeira banda começa no mês seguinte ao padrão
            (12, 3),  # ...e vai até o fim dela
            (13, 6),
            (18, 6),
            (19, 9),
            (24, 9),  # a quarta banda é 9×, não 12× — decisão 2 do plano
        ],
    )
    def test_a_tabela_do_plano(self, meses: int, esperado: int) -> None:
        assert jn.multiplicador_do_teto(meses, padrao=6) == esperado

    def test_a_banda_conta_a_partir_do_padrao_configurado(self) -> None:
        """`MESES_DE_JANELA` continua ajustável, e a banda acompanha.

        É o excedente que justifica o teto maior. Com o padrão em 12, pedir 12
        não é pedir nada — e não pode ganhar 3× de teto por causa de um número
        fixo no código.
        """
        assert jn.multiplicador_do_teto(12, padrao=12) == 1
        assert jn.multiplicador_do_teto(13, padrao=12) == 3


class TestTetoDeEscrita:
    def test_cresce_com_a_banda(self) -> None:
        assert jn.teto_de_escrita(6, padrao=6, base=200) == 200
        assert jn.teto_de_escrita(12, padrao=6, base=200) == 600
        assert jn.teto_de_escrita(18, padrao=6, base=200) == 1200
        assert jn.teto_de_escrita(24, padrao=6, base=200) == 1800

    def test_o_teto_absoluto_vence_a_banda(self) -> None:
        """A rede embaixo da regra: o escalonamento nunca passa do limite duro.

        Existe para o dia em que alguém aumentar a janela máxima sem refazer a
        conta do pior caso — sem ele, um número digitado errado vira um ciclo de
        milhares de escritas irreversíveis em produção.
        """
        assert jn.teto_de_escrita(24, padrao=6, base=200, absoluto=1000) == 1000


class TestValidarMeses:
    def test_aceita_o_que_esta_na_faixa(self) -> None:
        assert jn.validar_meses(24, padrao=6, maximo=24) == 24

    def test_recusa_acima_do_maximo(self) -> None:
        with pytest.raises(ValueError, match="máximo de 24"):
            jn.validar_meses(36, padrao=6, maximo=24)

    def test_recusa_abaixo_do_padrao(self) -> None:
        """Encolher a janela por aqui seria outra decisão, e não esta.

        A janela sob demanda existe para **alcançar** oportunidade antiga. Quem
        quer menos do que o padrão está pedindo para o ciclo deixar de olhar
        para coisas que ele olha todo dia — isso se muda no `.env`, com quem
        responde pela integração na frente.
        """
        with pytest.raises(ValueError, match="menor que o padrão"):
            jn.validar_meses(3, padrao=6, maximo=24)


class TestExpiracao:
    def test_aguardando_expira_no_prazo(self) -> None:
        prazo = datetime(2026, 9, 11, 14, 0)
        assert jn.expirou(jn.EstadoDaJanela.AGUARDANDO, prazo, agora=prazo)
        assert jn.expirou(
            jn.EstadoDaJanela.AGUARDANDO, prazo, agora=prazo + timedelta(seconds=1)
        )
        assert not jn.expirou(
            jn.EstadoDaJanela.AGUARDANDO, prazo, agora=prazo - timedelta(seconds=1)
        )

    def test_armado_nunca_expira(self) -> None:
        """Um pedido armado na sexta às 19h só é atendido na segunda.

        O worker não roda fora do expediente. Expirar `ARMADO` por tempo faria o
        sistema engolir calado justamente o pedido de quem esperou o fim de
        semana inteiro para ver o resultado.
        """
        passado = datetime(2020, 1, 1)
        assert not jn.expirou(jn.EstadoDaJanela.ARMADO, passado, agora=datetime.now())

    def test_ocioso_nao_expira(self) -> None:
        assert not jn.expirou(jn.EstadoDaJanela.OCIOSO, None, agora=datetime.now())
