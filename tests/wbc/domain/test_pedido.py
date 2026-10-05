"""Testes do **pedido de venda** — cabeçalho, linhas e payload.

Par de `test_cotacao.py`. Aqui também vários testes afirmam a ausência de
campos: é o que impede o pedido de voltar a nascer com os campos da cotação.
"""

from __future__ import annotations

from datetime import date
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
from wbcpython.domain import pedido
from wbcpython.domain.venda_comum import LIMITE_COMENTARIO


class TestComentario:
    def test_e_a_condicao_de_pagamento_crua(self) -> None:
        """Produção grava o `PGTCOD` como veio — sem trocar `|` e `;`.

        É diferente do `U_INO_CondPag` da cotação, de propósito.
        """
        assert pedido.comentario("30 DIAS|60 DIAS;90 DIAS") == "30 DIAS|60 DIAS;90 DIAS"

    def test_corta_em_150(self) -> None:
        assert len(pedido.comentario("x" * 400)) == LIMITE_COMENTARIO


class TestCabecalho:
    def test_traz_o_conjunto_do_legado(self) -> None:
        campos = pedido.cabecalho(_impressao())
        assert campos["U_INO_TIPO_MT"] == "CIF"
        assert campos["U_INO_COM"] == "3.5"
        assert campos["U_INO_VL_COM"] == 1200.0
        assert campos["Comments"] == "30 DIAS|60 DIAS;90 DIAS"

    def test_valor_de_montagem_vem_de_orcbas3(self) -> None:
        """Divergência deliberada: `ServiceProcess.cs:281` usa `ORCBAS3` aqui."""
        assert pedido.cabecalho(_impressao())["U_INO_VL_MT"] == 7000.25

    def test_valores_saem_como_numero(self) -> None:
        """`VL_MT` e `VL_COM` são `db_Float`; mandar texto é pedir erro."""
        campos = pedido.cabecalho(_impressao())
        assert isinstance(campos["U_INO_VL_MT"], float)
        assert isinstance(campos["U_INO_VL_COM"], float)

    def test_percentual_de_comissao_sem_casas_sobrando(self) -> None:
        """Produção grava `'3'`, `'4'`, `'4.5'` — não `'4.0000'`."""
        assert pedido.cabecalho(_impressao(percentual_comissao=Decimal(4)))["U_INO_COM"] == "4"
        assert (
            pedido.cabecalho(_impressao(percentual_comissao=Decimal("4.50")))["U_INO_COM"] == "4.5"
        )
        assert pedido.cabecalho(_impressao(percentual_comissao=Decimal(100)))["U_INO_COM"] == "100"

    def test_vencimento_e_hoje_mais_o_prazo_de_entrega(self) -> None:
        """Conferido em produção: `DocDueDate - DocDate` é sempre o `PRZENT`."""
        campos = pedido.cabecalho(_impressao(), hoje=date(2026, 8, 26))
        assert campos["DocDueDate"] == "2026-10-07"

    def test_nao_leva_os_campos_da_cotacao(self) -> None:
        campos = pedido.cabecalho(_impressao())
        for campo in (
            "U_INO_PrazoEntrega",
            "U_INO_ValorTransp",
            "U_INO_ValorEmbalagem",
            "U_INO_PessoaContato",
            "U_INO_CondPag",
            "U_INO_Montagem",
            "U_INO_Montagem2",
        ):
            assert campo not in campos


class TestLinhas:
    """O pedido leva `U_INO_Composicao` e `U_INO_Id_IntWBC` — nunca `U_INO_ACAB`."""

    def test_leva_composicao_e_id(self) -> None:
        r = pedido.linhas(_orcamento(_item(orcitm=15)), DE_PARA)
        assert r.linhas[0]["U_INO_Composicao"]
        assert r.linhas[0]["U_INO_Id_IntWBC"] == "126979"
        assert r.linhas[0]["U_INO_ORCITM"] == "15"

    def test_nao_leva_acabamento(self) -> None:
        r = pedido.linhas(_orcamento(_item(), impressao=_impressao()), DE_PARA)
        assert "U_INO_ACAB" not in r.linhas[0]

    def test_composicao_e_o_texto_cortado_no_valor(self) -> None:
        r = pedido.linhas(_orcamento(_item(texto="Estrutura X Valor 10")), DE_PARA)
        assert r.linhas[0]["U_INO_Composicao"] == "Estrutura X"


class TestPayload:
    def test_junta_campos_comuns_e_cabecalho(self) -> None:
        payload = pedido.montar_payload(
            _orcamento(impressao=_impressao()),
            parceiro="C999",
            filial=1,
            snapshot_id=513900,
            hoje=date(2026, 8, 26),
        )
        assert payload["CardCode"] == "C999"
        assert payload["U_INO_COTWBC"] == "00125535"
        assert payload["U_INO_ORCAMENTO"] == 513900
        assert payload["DocDueDate"] == "2026-10-07"
        assert "U_INO_PrazoEntrega" not in payload
