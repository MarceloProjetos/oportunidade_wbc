"""O peso da linha do pedido (`Weight1`).

Regras que erram em silêncio se forem esquecidas: somar **só o nível 1** da árvore
(a soma acontece na consulta), gravar o peso **da linha inteira** (o SAP não
multiplica pela quantidade), **árvore + 10%** com 2 casas, sem truncar (regra
confirmada pelo Marcelo em 29/09/2026), e **não enviar o campo** quando não se sabe
o peso. Ver `domain.linhas.peso_da_linha`.
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
        """Árvore 226,43 kg no nível 1 → a linha leva 249,07 (+10%, 2 casas).

        A soma vem do SQL Server como float (226.42999999999998); o arredondamento é
        feito uma vez só, no fim."""
        resultado = regras_pedido.linhas(
            _orcamento(_item(1)), DE_PARA, pesos={1: Decimal("226.42999999999998")}
        )
        assert resultado.linhas[0]["Weight1"] == 249.07

    def test_cada_item_recebe_o_seu(self) -> None:
        """O casamento é por `ORCITM`, não por posição na lista."""
        resultado = regras_pedido.linhas(
            _orcamento(_item(5), _item(1)),
            DE_PARA,
            pesos={1: Decimal("760.65"), 5: Decimal("45.13")},
        )
        # 45,13 × 1,1 = 49,643 ; 760,65 × 1,1 = 836,715
        assert [linha["Weight1"] for linha in resultado.linhas] == [49.64, 836.72]

    def test_peso_e_da_linha_inteira(self) -> None:
        """`Weight1` é o total da linha — o SAP grava como vem, não multiplica.

        Regressão do pedido 84407 (22/09/2026): 167 módulos e 20.830,79 kg na
        árvore saíram como 137 kg, porque a linha levava o peso dividido pela
        quantidade."""
        resultado = regras_pedido.linhas(
            _orcamento(_item(1, quantidade=Decimal(167))), DE_PARA, pesos={1: Decimal("20830.79")}
        )
        assert resultado.linhas[0]["Quantity"] == 167.0
        assert resultado.linhas[0]["Weight1"] == 22913.87

    def test_quantidade_nula_nao_muda_o_peso(self) -> None:
        """`ORCPRDQTD` é nula em 100% das linhas do WBC: o peso não depende
        da quantidade."""
        resultado = regras_pedido.linhas(
            _orcamento(_item(1, quantidade=None)), DE_PARA, pesos={1: Decimal("760.65")}
        )
        assert resultado.linhas[0]["Weight1"] == 836.72

    def test_mais_dez_por_cento_sem_truncar(self) -> None:
        """Até 29/09/2026 a conta truncava: 89,99 × 1,1 = 98,989 virava 98."""
        resultado = regras_pedido.linhas(_orcamento(_item(1)), DE_PARA, pesos={1: Decimal("89.99")})
        assert resultado.linhas[0]["Weight1"] == 98.99

    def test_arredonda_para_duas_casas(self) -> None:
        resultado = regras_pedido.linhas(_orcamento(_item(1)), DE_PARA, pesos={1: Decimal("110.8765")})
        assert resultado.linhas[0]["Weight1"] == 121.96          # 121,96415


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
        """Sem o truncamento, meio quilo vai (0,55 com os 10%); antes virava zero e não ia."""
        resultado = regras_pedido.linhas(_orcamento(_item(1)), DE_PARA, pesos={1: Decimal("0.5")})
        assert resultado.linhas[0]["Weight1"] == 0.55

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
