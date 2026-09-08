"""Testes da **cotação** — cabeçalho, linhas e payload.

O par deste arquivo é `test_pedido.py`. Vários testes aqui afirmam a *ausência*
de um campo: é assim que se protege a separação. Um campo que vaza de um
documento para o outro não quebra nada visivelmente — só muda o que o cliente
recebe impresso.
"""

from __future__ import annotations

from decimal import Decimal

from tests.wbc.domain.conftest import (
    DE_PARA,
)
from tests.wbc.domain.conftest import (
    impressao as _impressao,
)
from tests.wbc.domain.conftest import (
    item as _item,
)
from tests.wbc.domain.conftest import (
    orcamento as _orcamento,
)
from wbcpython.domain import cotacao
from wbcpython.domain.venda_comum import LIMITE_CONDPAG


class TestCondicaoDePagamento:
    def test_troca_separadores_por_quebra_de_linha(self) -> None:
        assert cotacao.condicao_de_pagamento("30|60;90") == "30\n60\n90"

    def test_corta_em_254(self) -> None:
        assert len(cotacao.condicao_de_pagamento("x" * 400)) == LIMITE_CONDPAG


class TestMontagem:
    """O texto vai partido em dois campos, mas só quando precisa."""

    def test_texto_curto_nao_cria_o_segundo_campo(self) -> None:
        """Regressão: gravar `Montagem2` vazio apagaria o que já estava lá."""
        assert cotacao.partir_montagem("curto") == {"U_INO_Montagem": "curto"}

    def test_texto_longo_e_partido_em_200(self) -> None:
        partes = cotacao.partir_montagem("a" * 200 + "b" * 50)
        assert partes["U_INO_Montagem"] == "a" * 200
        assert partes["U_INO_Montagem2"] == "b" * 50

    def test_o_limite_exato_ainda_cabe_em_um_campo(self) -> None:
        assert cotacao.partir_montagem("a" * 200) == {"U_INO_Montagem": "a" * 200}


class TestCabecalho:
    def test_traz_o_conjunto_do_legado(self) -> None:
        udfs = cotacao.cabecalho(_impressao())
        assert udfs["U_INO_PessoaContato"] == "MARIA"
        assert udfs["U_INO_CondPag"] == "30 DIAS\n60 DIAS\n90 DIAS"
        assert udfs["U_INO_PrazoEntrega"] == "42"
        assert udfs["U_INO_ValorTransp"] == "113026.8800"
        assert udfs["U_INO_ValorEmbalagem"] == "0.0000"
        assert udfs["U_INO_TIPO_MT"] == "CIF"
        assert udfs["U_INO_Montagem"] == "Montagem inclusa."

    def test_valor_de_montagem_vem_de_orcvalmon(self) -> None:
        """Na cotação o legado usa `ORCVALMON` — no pedido usa `ORCBAS3`."""
        assert cotacao.cabecalho(_impressao())["U_INO_VL_MT"] == 2500.50

    def test_valor_de_montagem_e_numero(self) -> None:
        """`U_INO_VL_MT` é `db_Float` no SAP; mandar texto é pedir erro."""
        assert isinstance(cotacao.cabecalho(_impressao())["U_INO_VL_MT"], float)

    def test_contato_cabe_nos_20_caracteres_do_campo(self) -> None:
        udfs = cotacao.cabecalho(_impressao(contato="FABIO VIEIRA DA SILVA JUNIOR"))
        assert udfs["U_INO_PessoaContato"] == "FABIO VIEIRA DA SILV"

    def test_embalagem_grande_cabe_nos_10_caracteres_do_campo(self) -> None:
        """Sem isto, uma embalagem de seis dígitos derrubaria a cotação inteira."""
        udfs = cotacao.cabecalho(_impressao(valor_embalagem=Decimal("123456.78")))
        assert len(udfs["U_INO_ValorEmbalagem"]) <= 10
        assert udfs["U_INO_ValorEmbalagem"].startswith("123456.7")

    def test_valor_pequeno_mantem_as_quatro_casas(self) -> None:
        udfs = cotacao.cabecalho(_impressao(valor_embalagem=Decimal("12.5")))
        assert udfs["U_INO_ValorEmbalagem"] == "12.5000"

    def test_nao_leva_os_campos_do_pedido(self) -> None:
        udfs = cotacao.cabecalho(_impressao())
        for campo in ("DocDueDate", "Comments", "U_INO_COM", "U_INO_VL_COM"):
            assert campo not in udfs

    def test_nao_grava_os_udfs_comentados_no_legado(self) -> None:
        """`U_INO_Retorno` e companhia só existem dentro de comentários no C#."""
        udfs = cotacao.cabecalho(_impressao())
        for campo in (
            "U_INO_UpdateDate",
            "U_INO_PctCom",
            "U_INO_VlrCom",
            "U_INO_Retorno",
            "U_INO_Negociacao",
            "U_INO_IndVend",
            "U_INO_Embalagem",
        ):
            assert campo not in udfs


class TestLinhas:
    """A cotação leva `U_INO_ACAB` — e não leva os UDFs do pedido.

    Conferido em produção: das 579.088 linhas de cotação do WBC, nenhuma tem
    `U_INO_Composicao`. O campo aparece na view de impressão, então preenchê-lo
    mudaria o que o cliente recebe.
    """

    def test_repete_o_acabamento_em_todas_as_linhas(self) -> None:
        orcamento = _orcamento(_item(orcitm=1), _item(orcitm=2), impressao=_impressao())
        r = cotacao.linhas(orcamento, DE_PARA)
        assert [linha["U_INO_ACAB"] for linha in r.linhas] == [
            "Cinza Padrão Altamira",
            "Cinza Padrão Altamira",
        ]

    def test_sem_impressao_o_acabamento_vai_vazio(self) -> None:
        r = cotacao.linhas(_orcamento(_item()), DE_PARA)
        assert r.linhas[0]["U_INO_ACAB"] == ""

    def test_nao_leva_os_udfs_de_linha_do_pedido(self) -> None:
        r = cotacao.linhas(_orcamento(_item()), DE_PARA)
        assert "U_INO_Composicao" not in r.linhas[0]
        assert "U_INO_Id_IntWBC" not in r.linhas[0]

    def test_leva_os_campos_comuns(self) -> None:
        r = cotacao.linhas(_orcamento(_item()), DE_PARA)
        assert r.linhas[0]["U_INO_ORCITM"] == "1"
        assert r.linhas[0]["U_INO_D_Adicionais"]


class TestPayload:
    def test_junta_campos_comuns_e_cabecalho(self) -> None:
        payload = cotacao.montar_payload(
            _orcamento(impressao=_impressao()),
            parceiro="C001",
            filial=1,
            snapshot_id=513900,
            oportunidade={"SalesPerson": 11, "ContactPerson": 9308},
        )
        assert payload["CardCode"] == "C001"
        assert payload["U_INO_COTWBC"] == "00125535"
        assert payload["BPL_IDAssignedToInvoice"] == 1
        assert payload["U_INO_ORCAMENTO"] == 513900
        assert payload["SalesPersonCode"] == 11
        assert payload["ContactPersonCode"] == 9308
        assert payload["U_INO_PrazoEntrega"] == "42"

    def test_sem_snapshot_o_vinculo_e_omitido(self) -> None:
        """Campo numérico no SAP: mandar vazio é `SAP 205`."""
        payload = cotacao.montar_payload(
            _orcamento(impressao=_impressao()), parceiro="C001", filial=1
        )
        assert "U_INO_ORCAMENTO" not in payload
