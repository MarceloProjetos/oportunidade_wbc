"""Testes do motor comum de linhas — o que vale para os dois documentos.

O que está aqui decide se o SAP aceita ou recusa o documento e com qual item o
orçamento chega ao comercial. Os UDFs que variam entre cotação e pedido são
testados em `test_cotacao.py` e `test_pedido.py`, não aqui.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from tests.wbc.domain.conftest import DE_PARA
from tests.wbc.domain.conftest import item as _item
from tests.wbc.domain.conftest import orcamento as _orcamento
from wbcpython.domain.linhas import (
    DEPOSITO_PADRAO,
    GRUPO_FALLBACK,
    composicao,
    resolver_linhas,
)


def _sem_udfs(item: Any, orcamento: Any) -> dict[str, Any]:
    """Documento fictício sem UDF próprio — isola o motor do que é específico."""
    del item, orcamento
    return {}


def _linhas(orcamento, de_para=DE_PARA, **kw):
    return resolver_linhas(orcamento, de_para, udfs_da_linha=_sem_udfs, **kw)


class TestEscolhaDoItem:
    """O item vem do GRPCOD via de-para — nunca do texto da linha."""

    def test_grupo_conhecido_vira_o_item_do_de_para(self) -> None:
        r = _linhas(_orcamento(_item(grupo=8)))
        assert r.linhas[0]["ItemCode"] == "I000007"
        assert r.avisos == ()

    def test_o_texto_da_linha_nao_influencia_a_escolha(self) -> None:
        """Regressão: a leitura ingênua dos dados sugere adivinhar pelo texto."""
        texto_de_mezanino = "MEZANINO 01 Conjunto de estruturas metálicas"
        r = _linhas(_orcamento(_item(grupo=1, texto=texto_de_mezanino)))
        # Grupo 1 é Estantes. O texto diz "MEZANINO" e não muda nada.
        assert r.linhas[0]["ItemCode"] == "I000002"

    def test_cada_item_vira_uma_linha(self) -> None:
        r = _linhas(_orcamento(_item(orcitm=1, grupo=2), _item(orcitm=5, grupo=1)))
        assert [linha["ItemCode"] for linha in r.linhas] == ["I000003", "I000002"]


class TestFallbackDeGrupo:
    """Grupo fora do de-para → Porta-Paletes, mas nunca em silêncio."""

    def test_grupo_desconhecido_usa_porta_paletes(self) -> None:
        r = _linhas(_orcamento(_item(grupo=99)))
        assert r.linhas[0]["ItemCode"] == DE_PARA[GRUPO_FALLBACK].item_sap

    def test_grupo_desconhecido_gera_aviso(self) -> None:
        r = _linhas(_orcamento(_item(grupo=99)))
        assert len(r.avisos) == 1
        assert "99" in r.avisos[0]
        assert r.grupos_desconhecidos == frozenset({99})

    def test_grupo_conhecido_nao_gera_ruido(self) -> None:
        r = _linhas(_orcamento(_item(grupo=2)))
        assert r.avisos == ()
        assert r.grupos_desconhecidos == frozenset()

    def test_sem_fallback_no_de_para_a_linha_nao_e_inventada(self) -> None:
        """Não havendo nem grupo nem fallback, é melhor faltar linha que mentir."""
        r = _linhas(_orcamento(_item(grupo=99)), {"1": DE_PARA["1"]})
        assert r.linhas == ()
        assert "não enviada" in r.avisos[0]


class TestQuantidade:
    """1 quando ORCPRDQTD for vazia, nula, inconsistente ou zero — e só aí."""

    @pytest.mark.parametrize("quantidade", [None, Decimal(0), Decimal(-5)])
    def test_quantidade_invalida_vira_um(self, quantidade) -> None:
        r = _linhas(_orcamento(_item(quantidade=quantidade)))
        assert r.linhas[0]["Quantity"] == 1

    def test_quantidade_valida_e_respeitada(self) -> None:
        r = _linhas(_orcamento(_item(quantidade=Decimal(16), valor=Decimal(1600))))
        assert r.linhas[0]["Quantity"] == 16
        assert r.linhas[0]["Price"] == pytest.approx(100.0)

    def test_o_total_da_linha_e_preservado(self) -> None:
        """ORCVAL é o total; qualquer que seja a quantidade, o total não muda."""
        for quantidade in (None, Decimal(4), Decimal(16)):
            r = _linhas(_orcamento(_item(quantidade=quantidade, valor=Decimal("41810.91"))))
            linha = r.linhas[0]
            assert linha["Quantity"] * linha["Price"] == pytest.approx(41810.91)


class TestCamposComunsDaLinha:
    """O que toda linha leva, seja de cotação ou de pedido."""

    def test_deposito_e_sempre_08(self) -> None:
        r = _linhas(_orcamento(_item()))
        assert r.linhas[0]["WarehouseCode"] == "08"
        assert DEPOSITO_PADRAO == "08"

    def test_texto_livre_vai_para_o_udf_de_descricao(self) -> None:
        r = _linhas(_orcamento(_item(texto="PROTETORES")))
        assert r.linhas[0]["U_INO_D_Adicionais"] == "PROTETORES"

    def test_sequencia_sai_como_texto(self) -> None:
        r = _linhas(_orcamento(_item(orcitm=15)))
        assert r.linhas[0]["U_INO_ORCITM"] == "15"


class TestUdfsEspecificos:
    """O motor não decide UDF de documento — quem passa é o chamador."""

    def test_o_motor_nao_inventa_udf_de_documento(self) -> None:
        r = _linhas(_orcamento(_item()))
        for campo in ("U_INO_ACAB", "U_INO_Composicao", "U_INO_Id_IntWBC"):
            assert campo not in r.linhas[0]

    def test_os_udfs_recebidos_entram_na_linha(self) -> None:
        r = resolver_linhas(
            _orcamento(_item()),
            DE_PARA,
            udfs_da_linha=lambda item, orc: {"U_QUALQUER": str(item.orcitm)},
        )
        assert r.linhas[0]["U_QUALQUER"] == "1"


class TestComposicao:
    """Corte no primeiro "Valor" — usado pelo pedido, mora no motor comum."""

    def test_corta_no_marcador(self) -> None:
        # Sem o espaço antes de "Valor": o corte deixa um, e a gravação apara.
        assert composicao("Estrutura X Valor 100,00") == "Estrutura X"

    def test_sem_marcador_mantem_o_texto(self) -> None:
        assert composicao("Estrutura X") == "Estrutura X"

    def test_marcador_no_inicio_nao_corta(self) -> None:
        """Cortar deixaria a composição vazia — o legado também não corta."""
        assert composicao("Valor total do conjunto") == "Valor total do conjunto"

    def test_texto_vazio(self) -> None:
        assert composicao("") == ""


class TestOrcamentoSemItens:
    def test_nao_inventa_linha(self) -> None:
        r = _linhas(_orcamento())
        assert r.linhas == ()
        assert r.vazio


class TestEspacosAoFinal:
    """O texto do WBC vem com espaço à direita; a produção grava sem.

    Não é suposição: o pedido 84316 (produção) e o 84315 (homologação) são o
    mesmo orçamento, criados no mesmo dia com 24 minutos de diferença. O de
    produção tem 0 espaços no fim dos dois UDFs de texto; o nosso tinha 1 e 4.
    Quem apara é a gravação do DI-API — pelo Service Layer o espaço passa
    direto, e o campo aparece na impressão do cliente.
    """

    def test_d_adicionais_nao_leva_espaco_a_direita(self) -> None:
        r = resolver_linhas(
            _orcamento(_item(texto="Estrutura X   ")),
            DE_PARA,
            udfs_da_linha=lambda i, o: {},
        )
        assert r.linhas[0]["U_INO_D_Adicionais"] == "Estrutura X"

    def test_composicao_nao_leva_espaco_a_direita(self) -> None:
        assert composicao("Estrutura X    Valor 10") == "Estrutura X"

    def test_espaco_interno_e_preservado(self) -> None:
        """Aparar é só no fim: o texto do cliente tem espaçamento proposital."""
        r = resolver_linhas(
            _orcamento(_item(texto="Estrutura   X  ")),
            DE_PARA,
            udfs_da_linha=lambda i, o: {},
        )
        assert r.linhas[0]["U_INO_D_Adicionais"] == "Estrutura   X"
