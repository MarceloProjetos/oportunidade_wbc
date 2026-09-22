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
    eh_porta_paletes,
    quantidade_no_texto,
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
    """1 quando ORCPRDQTD for vazia, nula, inconsistente ou zero — e o texto
    não disser quantos módulos são."""

    @pytest.mark.parametrize("quantidade", [None, Decimal(0), Decimal(-5)])
    def test_quantidade_invalida_vira_um(self, quantidade) -> None:
        r = _linhas(_orcamento(_item(quantidade=quantidade)))
        assert r.linhas[0]["Quantity"] == 1

    def test_quantidade_valida_e_respeitada(self) -> None:
        r = _linhas(_orcamento(_item(quantidade=Decimal(16), valor=Decimal(1600))))
        assert r.linhas[0]["Quantity"] == 16
        assert r.linhas[0]["LineTotal"] == pytest.approx(1600.0)

    def test_o_total_da_linha_e_preservado(self) -> None:
        """ORCVAL é o total; qualquer que seja a quantidade, o total não muda."""
        for quantidade in (None, Decimal(4), Decimal(16)):
            r = _linhas(_orcamento(_item(quantidade=quantidade, valor=Decimal("41810.91"))))
            assert r.linhas[0]["LineTotal"] == pytest.approx(41810.91)


class TestQuantidadeNoTexto:
    """Porta-paletes lê "N Módulos" do ORCTXT — a coluna é nula na tabela inteira.

    Relato de 15/09/2026: *"o item PORTA-PALETES sempre é criado como 01
    unidade conjunto"*. Os textos abaixo são reais (base do WBC).
    """

    @pytest.mark.parametrize(
        ("texto", "esperado"),
        [
            ("PORTA-PALETES 16 Módulos de estruturas metálicas", 16),
            ("PORTA-PALETES ÁREA 1 10 Módulos de estruturas", 10),
            ("PORTA-PALETES - OPÇÃO 1 14 Módulos de estruturas", 14),
            ("Porta palete 16 modulos duplos", 16),
            ("PORTAPALETES 03 MODULOS", 3),
            ("PORTA-PALETES 3279 Módulos de estruturas metálicas, sendo: 3183 medindo", 3279),
            ("PORTA-PALETES - FASE I - 04 NÍVEIS 59 Módulos de estruturas", 59),
            ("PORTA-PALETES - OPÇÃO 2000 Kgf 56 Módulos", 56),
            ("PORTA-PALETES 01 conjunto composto por 06 módulos, sendo", 6),
            # Rótulo antes do nome: área, item, opção, galpão.
            ("ÁREA: SECA  PORTA-PALETES 226 Módulos de estruturas", 226),
            ("ITEM 02 - PORTA-PALETES 04 Módulos de estruturas", 4),
            ("OPÇÃO 02 - PORTA-PALETES 59 Módulos", 59),
            ("DW WORLD STG - RCK- 002A  PORTA-PALETES 84 Módulos", 84),
        ],
    )
    def test_le_o_numero_antes_de_modulos(self, texto: str, esperado: int) -> None:
        assert quantidade_no_texto(texto) == esperado

    @pytest.mark.parametrize(
        "texto",
        [
            "PORTA-PALETES junção dupla",
            "PORTA-PALETES 36 Sapatas para colunas de porta paletes",
            "PORTA-PALETES - MODELO C 17 Modelos C medindo 2300 x 800",
            "PORTA-PALETES 0 Módulos",
            "",
        ],
    )
    def test_porta_paletes_sem_numero_devolve_none(self, texto: str) -> None:
        assert eh_porta_paletes(texto) or texto == ""
        assert quantidade_no_texto(texto) is None

    @pytest.mark.parametrize(
        "texto",
        [
            "ESTANTES METÁLICAS 15 módulos de estantes metálicas",
            "MEZANINO 12 Módulos",
            # Acessório que cita porta-paletes: não é a estrutura.
            "STOPS TRASEIROS PARA PORTA-PALETES 24 Stops traseiros para 12 módulos",
            "COLUNAS DE PORTA-PALETES 12 colunas de porta-paletes",
            "PLANOS METÁLICOS 60 Planos metálicos para estruturas do tipo porta-paletes com 4 módulos",
            "ESTANTES METÁLICAS - GAVETEIROS 16 Módulos de estantes, para os porta-paletes",
        ],
    )
    def test_o_que_nao_e_porta_paletes_fica_de_fora(self, texto: str) -> None:
        assert not eh_porta_paletes(texto)
        assert quantidade_no_texto(texto) is None

    def test_a_quantidade_lida_vai_para_a_linha(self) -> None:
        r = _linhas(_orcamento(_item(texto="PORTA-PALETES 8 Módulos", valor=Decimal("1000"))))
        assert r.linhas[0]["Quantity"] == 8
        assert r.linhas[0]["LineTotal"] == pytest.approx(1000.0)

    @pytest.mark.parametrize(("modulos", "valor"), [(8, "41810.91"), (134, "319254.90"), (272, "707201.92")])
    def test_o_total_nao_muda_com_a_quantidade_lida(self, modulos: int, valor: str) -> None:
        """A regressão que não podia passar: o total da linha é o ORCVAL, ao centavo."""
        item = _item(texto=f"PORTA-PALETES {modulos} Módulos", valor=Decimal(valor))
        r = _linhas(_orcamento(item))
        assert Decimal(str(r.linhas[0]["LineTotal"])) == Decimal(valor)
        # O unitário informativo vezes a quantidade devolve o total.
        assert (item.preco_unitario * item.quantidade_para_documento).quantize(Decimal("0.01")) == Decimal(valor)

    def test_orcprdqtd_preenchida_vence_o_texto(self) -> None:
        r = _linhas(_orcamento(_item(texto="PORTA-PALETES 8 Módulos", quantidade=Decimal(3))))
        assert r.linhas[0]["Quantity"] == 3
        assert r.notas == ()

    def test_a_leitura_vira_nota_no_log(self) -> None:
        r = _linhas(_orcamento(_item(texto="PORTA-PALETES 8 Módulos", valor=Decimal("1000"))))
        assert len(r.notas) == 1
        nota = r.notas[0]
        assert not nota.atencao
        assert nota.texto.startswith("[porta-paletes] Item 1: 8 módulos lidos do texto")
        assert "LineTotal R$ 1.000,00" in nota.texto
        assert "unitário R$ 125,0000" in nota.texto
        assert r.avisos == ()

    def test_porta_paletes_sem_numero_vira_nota_com_atencao(self) -> None:
        r = _linhas(_orcamento(_item(texto="PORTA-PALETES 36 Sapatas")))
        assert r.linhas[0]["Quantity"] == 1
        assert len(r.notas) == 1
        assert r.notas[0].atencao
        assert 'sem "N Módulos"' in r.notas[0].texto
        assert r.avisos == (), "não é erro do orçamento: fica no log"

    def test_estante_com_modulos_segue_calada(self) -> None:
        r = _linhas(_orcamento(_item(grupo=1, texto="ESTANTES METÁLICAS 15 módulos")))
        assert r.linhas[0]["Quantity"] == 1
        assert r.notas == ()

    def test_o_peso_nao_divide_pela_quantidade_lida(self) -> None:
        """`Weight1` é o total da linha: com 8 módulos lidos, o peso da árvore
        (do conjunto) vai inteiro — o SAP não multiplica pela quantidade."""
        item = _item(texto="PORTA-PALETES 8 Módulos")
        r = _linhas(_orcamento(item), pesos={1: Decimal("800")})
        assert r.linhas[0]["Quantity"] == 8
        assert r.linhas[0]["Weight1"] == 880.0


class TestCamposDaLinhaNoSap:
    """O que vai e o que não vai no `DocumentLines`."""

    def test_o_valor_vai_como_line_total_e_nao_como_price(self) -> None:
        """Enviando `Price = ORCVAL ÷ qtd`, o SAP arredonda a 4 casas e o total
        diverge 1 centavo. Com `LineTotal`, o total bate por construção."""
        linha = _linhas(_orcamento(_item(valor=Decimal("707201.92")))).linhas[0]
        assert linha["LineTotal"] == pytest.approx(707201.92)
        assert "Price" not in linha
        assert "UnitPrice" not in linha

    def test_a_unidade_nao_e_enviada(self) -> None:
        """A unidade CJ foi implementada e desfeita a pedido do negócio: sem o
        campo, o SAP usa a unidade do cadastro do item."""
        linha = _linhas(_orcamento(_item())).linhas[0]
        assert "MeasureUnit" not in linha
        assert "UoMEntry" not in linha


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
