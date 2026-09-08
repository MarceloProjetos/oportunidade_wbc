"""O peso da linha do pedido (`Weight1`).

Quatro regras que erram em silêncio se forem esquecidas: somar **só o nível 1**
da árvore (a soma acontece na consulta), gravar o peso **unitário** (o SAP
multiplica pela quantidade), aplicar a **folga de embalagem** de 10% e truncar,
e **não enviar o campo** quando não se sabe o peso.

As três primeiras vêm de medição na produção, não de suposição — ver
`domain.linhas._peso_unitario` e `DECISOES.md`.
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
    def test_reproduz_o_pedido_real(self) -> None:
        """O caso que fechou a conta.

        Orçamento `00124853`, item 1: a árvore soma **760,65 kg** no nível 1, e
        o pedido 84112 da produção tem `Weight1 = 836`. Com a folga de 10% e o
        truncamento: `floor(760,65 × 1,10) = floor(836,715) = 836`. Bate na
        unidade com o que a produção gravou.
        """
        resultado = regras_pedido.linhas(
            _orcamento(_item(1)), DE_PARA, pesos={1: Decimal("760.65")}
        )
        assert resultado.linhas[0]["Weight1"] == 836.0

    def test_cada_item_recebe_o_seu(self) -> None:
        """O casamento é por `ORCITM`, não por posição na lista."""
        resultado = regras_pedido.linhas(
            _orcamento(_item(5), _item(1)),
            DE_PARA,
            pesos={1: Decimal("760.65"), 5: Decimal("45.13")},
        )
        # floor(45,13 × 1,1) = 49 ; floor(760,65 × 1,1) = 836
        assert [linha["Weight1"] for linha in resultado.linhas] == [49.0, 836.0]

    def test_peso_e_unitario(self) -> None:
        """`Weight1` é o peso de UMA unidade — o SAP multiplica pela quantidade.

        Sem a divisão, uma linha com quantidade 4 sairia com o peso do lote em
        cada unidade, e o total do documento ficaria 4× maior.
        """
        resultado = regras_pedido.linhas(
            _orcamento(_item(1, quantidade=Decimal(4))), DE_PARA, pesos={1: Decimal("760.60")}
        )
        assert resultado.linhas[0]["Quantity"] == 4.0
        # floor((760,60 / 4) × 1,1) = floor(209,165) = 209
        assert resultado.linhas[0]["Weight1"] == 209.0

    def test_quantidade_nula_usa_o_fallback_1(self) -> None:
        """`ORCPRDQTD` é nula em 100% das linhas do WBC: o divisor é o 1 da
        regra de negócio, não um zero que estouraria a divisão."""
        resultado = regras_pedido.linhas(
            _orcamento(_item(1, quantidade=None)), DE_PARA, pesos={1: Decimal("760.65")}
        )
        assert resultado.linhas[0]["Weight1"] == 836.0

    def test_o_fator_e_configuravel(self) -> None:
        """É regra de negócio (embalagem), não constante física."""
        resultado = regras_pedido.linhas(
            _orcamento(_item(1)),
            DE_PARA,
            pesos={1: Decimal(100)},
            fator_de_embarque=Decimal("1.25"),
        )
        assert resultado.linhas[0]["Weight1"] == 125.0

    def test_trunca_e_nao_arredonda(self) -> None:
        """1.056 dos 1.061 pesos da produção são inteiros redondos, e truncar
        acerta mais que arredondar (36,8% contra 25,1%)."""
        resultado = regras_pedido.linhas(_orcamento(_item(1)), DE_PARA, pesos={1: Decimal(90)})
        # 90 × 1,1 = 99,0000...  — mas 89,99 × 1,1 = 98,989 vira 98, não 99.
        assert resultado.linhas[0]["Weight1"] == 99.0
        truncado = regras_pedido.linhas(_orcamento(_item(1)), DE_PARA, pesos={1: Decimal("89.99")})
        assert truncado.linhas[0]["Weight1"] == 98.0


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

    def test_peso_que_trunca_para_zero_nao_e_enviado(self) -> None:
        """Um item de 0,5 kg viraria `Weight1 = 0` depois do truncamento —
        pior que o 1 kg do cadastro, porque some do somatório da expedição."""
        resultado = regras_pedido.linhas(_orcamento(_item(1)), DE_PARA, pesos={1: Decimal("0.5")})
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
