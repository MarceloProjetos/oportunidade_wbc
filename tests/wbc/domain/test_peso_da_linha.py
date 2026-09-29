"""O peso da linha do pedido (`Weight1`).

Regras que erram em silêncio se forem esquecidas: somar **só o nível 1** da árvore
(a soma acontece na consulta), gravar o peso **da linha inteira** (o SAP não
multiplica pela quantidade), gravar o **peso líquido** com 2 casas — sem folga e
sem truncar (decisão do Marcelo em 29/09/2026) — e **não enviar o campo** quando
não se sabe o peso. Ver `domain.linhas.peso_da_linha`.
"""

from __future__ import annotations

from decimal import Decimal

from wbcpython.domain import cotacao as regras_cotacao
from wbcpython.domain import pedido as regras_pedido
from wbcpython.infrastructure.service_layer.grupo_produtos import GrupoDeProduto
from wbcpython.infrastructure.wbc_sql.models import ItemOrcamentoWbc, OrcamentoWbc

DE_PARA = {"2": GrupoDeProduto("2", "Porta-Paletes", "I000003")}


def _orcamento(*itens: ItemOrcamentoWbc) -> OrcamentoWbc:
    return OrcamentoWbc(
        orcnum="00124853",
        revisao="A",
        sitcode=60,
        cliente_nome="BALTEAU",
        representante="043",
        municipio="ITAJUBA",
        uf="MG",
        itens=itens,
    )


def _item(orcitm: int, *, quantidade: Decimal | None = None) -> ItemOrcamentoWbc:
    return ItemOrcamentoWbc(orcitm=orcitm, grupo=2, quantidade=quantidade, valor=Decimal("100.00"))


class TestPesoNoPedido:
    def test_reproduz_o_orcamento_00125817(self) -> None:
        """O caso que mudou a regra (29/09/2026): a árvore soma 226,43 kg no nível 1 e o
        pedido 84444 saiu com 249 (226,43 × 1,10, truncado). O certo é o líquido.

        A soma vem do SQL Server como float (226.42999999999998): arredondar para 2 casas
        é o que devolve o número que a pessoa vê no WBC."""
        resultado = regras_pedido.linhas(
            _orcamento(_item(1)), DE_PARA, pesos={1: Decimal("226.42999999999998")}
        )
        assert resultado.linhas[0]["Weight1"] == 226.43

    def test_cada_item_recebe_o_seu(self) -> None:
        """O casamento é por `ORCITM`, não por posição na lista."""
        resultado = regras_pedido.linhas(
            _orcamento(_item(5), _item(1)),
            DE_PARA,
            pesos={1: Decimal("760.65"), 5: Decimal("45.13")},
        )
        assert [linha["Weight1"] for linha in resultado.linhas] == [45.13, 760.65]

    def test_peso_e_da_linha_inteira(self) -> None:
        """`Weight1` é o total da linha — o SAP grava como vem, não multiplica.

        Regressão do pedido 84407 (22/09/2026): 167 módulos e 20.830,79 kg na
        árvore saíram como 137 kg, porque a linha levava o peso dividido pela
        quantidade."""
        resultado = regras_pedido.linhas(
            _orcamento(_item(1, quantidade=Decimal(167))), DE_PARA, pesos={1: Decimal("20830.79")}
        )
        assert resultado.linhas[0]["Quantity"] == 167.0
        assert resultado.linhas[0]["Weight1"] == 20830.79

    def test_quantidade_nula_nao_muda_o_peso(self) -> None:
        """`ORCPRDQTD` é nula em 100% das linhas do WBC: o peso não depende
        da quantidade."""
        resultado = regras_pedido.linhas(
            _orcamento(_item(1, quantidade=None)), DE_PARA, pesos={1: Decimal("760.65")}
        )
        assert resultado.linhas[0]["Weight1"] == 760.65

    def test_sem_folga_e_sem_truncar(self) -> None:
        """Até 29/09/2026 a linha levava líquido × 1,10 truncado: 89,99 virava 98."""
        resultado = regras_pedido.linhas(_orcamento(_item(1)), DE_PARA, pesos={1: Decimal("89.99")})
        assert resultado.linhas[0]["Weight1"] == 89.99

    def test_arredonda_para_duas_casas(self) -> None:
        resultado = regras_pedido.linhas(_orcamento(_item(1)), DE_PARA, pesos={1: Decimal("110.8765")})
        assert resultado.linhas[0]["Weight1"] == 110.88


class TestQuandoNaoHaPeso:
    """ "Não sei o peso" e "o peso é zero" são coisas diferentes."""

    def test_item_fora_da_arvore_nao_recebe_o_campo(self) -> None:
        """Sem o campo, o SAP mantém o peso do cadastro — o comportamento de
        hoje. Mandar zero trocaria um número errado por outro."""
        resultado = regras_pedido.linhas(_orcamento(_item(9)), DE_PARA, pesos={1: Decimal(10)})
        assert "Weight1" not in resultado.linhas[0]

    def test_peso_zero_na_arvore_tambem_nao_e_enviado(self) -> None:
        resultado = regras_pedido.linhas(_orcamento(_item(1)), DE_PARA, pesos={1: Decimal(0)})
        assert "Weight1" not in resultado.linhas[0]

    def test_peso_pequeno_e_enviado(self) -> None:
        """Sem o truncamento, meio quilo é meio quilo (antes virava zero e não ia)."""
        resultado = regras_pedido.linhas(_orcamento(_item(1)), DE_PARA, pesos={1: Decimal("0.5")})
        assert resultado.linhas[0]["Weight1"] == 0.5

    def test_peso_que_arredonda_para_zero_nao_e_enviado(self) -> None:
        resultado = regras_pedido.linhas(_orcamento(_item(1)), DE_PARA, pesos={1: Decimal("0.004")})
        assert "Weight1" not in resultado.linhas[0]

    def test_sem_pesos_o_pedido_sai_como_antes(self) -> None:
        """Compatibilidade: quem não passa pesos não ganha o campo."""
        resultado = regras_pedido.linhas(_orcamento(_item(1)), DE_PARA)
        assert "Weight1" not in resultado.linhas[0]


class TestCotacao:
    def test_a_cotacao_nao_leva_peso(self) -> None:
        """Decisão do legado, não esquecimento: `ServiceProcess.cs:640` grava o
        peso no pedido e a linha equivalente da cotação está comentada (`:377`).

        A ausência do parâmetro em `cotacao.linhas` é o que torna a decisão
        visível — não há como passar peso para a cotação por engano.
        """
        import inspect

        resultado = regras_cotacao.linhas(_orcamento(_item(1)), DE_PARA)
        assert "Weight1" not in resultado.linhas[0]
        assert "pesos" not in inspect.signature(regras_cotacao.linhas).parameters
        assert "pesos" in inspect.signature(regras_pedido.linhas).parameters
