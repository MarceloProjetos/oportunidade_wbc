"""Testes da comparação de revisões."""

from __future__ import annotations

import pytest

from wbcpython.domain.revisao import (
    SEM_REVISAO,
    RevisaoInvalida,
    ordem_revisao,
    revisao_wbc_e_mais_nova,
)


class TestOrdemRevisao:
    def test_ausencia_de_revisao(self) -> None:
        assert ordem_revisao("") == SEM_REVISAO
        assert ordem_revisao("   ") == SEM_REVISAO
        assert ordem_revisao(None) == SEM_REVISAO

    @pytest.mark.parametrize(("letra", "esperado"), [("A", 1), ("B", 2), ("C", 3), ("Z", 26)])
    def test_letras_seguem_o_alfabeto(self, letra: str, esperado: int) -> None:
        assert ordem_revisao(letra) == esperado

    def test_ignora_caixa(self) -> None:
        assert ordem_revisao("c") == ordem_revisao("C")

    def test_ignora_espacos_em_volta(self) -> None:
        assert ordem_revisao(" B ") == ordem_revisao("B")

    def test_digitos_vem_antes_das_letras(self) -> None:
        """A fórmula do legado produz negativos para dígitos — e isso é correto.

        Dados reais usam "0" como versão inicial e "A"/"B"/"C" como revisões,
        então a ordem "dígito < letra" é justamente a semântica desejada.
        """
        assert ordem_revisao("0") < ordem_revisao("A")
        assert ordem_revisao("9") < ordem_revisao("A")

    def test_digitos_ordenam_entre_si(self) -> None:
        assert ordem_revisao("0") < ordem_revisao("1") < ordem_revisao("9")

    def test_ordem_total_coerente(self) -> None:
        sequencia = ["0", "1", "9", "A", "B", "Z"]
        valores = [ordem_revisao(v) for v in sequencia]
        assert valores == sorted(valores)


class TestRevisaoInvalida:
    def test_modo_tolerante_usa_o_primeiro_caractere(self) -> None:
        # O legado quebrava aqui (char.Parse lança exceção); preferimos degradar.
        assert ordem_revisao("AB") == ordem_revisao("A")

    def test_modo_estrito_levanta(self) -> None:
        with pytest.raises(RevisaoInvalida):
            ordem_revisao("AB", estrito=True)

    def test_modo_estrito_aceita_valor_valido(self) -> None:
        assert ordem_revisao("A", estrito=True) == 1


class TestRevisaoWbcEMaisNova:
    def test_wbc_mais_nova(self) -> None:
        assert revisao_wbc_e_mais_nova("B", "A") is True

    def test_sap_mais_nova(self) -> None:
        assert revisao_wbc_e_mais_nova("A", "B") is False

    def test_empate_nao_reprocessa(self) -> None:
        # Decisão de negócio: empate significa "documento congelado".
        assert revisao_wbc_e_mais_nova("B", "B") is False

    def test_wbc_vazio_nunca_e_mais_novo(self) -> None:
        assert revisao_wbc_e_mais_nova("", "A") is False
        assert revisao_wbc_e_mais_nova("", "") is False

    def test_sap_vazio_com_letra_no_wbc(self) -> None:
        assert revisao_wbc_e_mais_nova("A", "") is True

    def test_versao_inicial_zero_contra_primeira_revisao(self) -> None:
        assert revisao_wbc_e_mais_nova("A", "0") is True
        assert revisao_wbc_e_mais_nova("0", "A") is False


class TestRevisaoAusenteNuncaDisparaAcaoDestrutiva:
    """Regressão: revisão vazia era considerada mais nova que a versão "0".

    A fórmula do legado (ASCII-64) dá -16 para "0", e a ausência de revisão era
    representada por 0 — que caía *entre* os dígitos e as letras. Com isso, um
    orçamento sem revisão no WBC contra uma cotação com revisão "0" no SAP
    entrava no ramo de cancelar-e-recriar: dado faltando provocava ação
    destrutiva. Agora a ausência ordena abaixo de tudo.
    """

    @pytest.mark.parametrize("revisao_sap", ["0", "1", "9", "A", "Z"])
    def test_ausencia_nunca_e_mais_nova(self, revisao_sap: str) -> None:
        assert revisao_wbc_e_mais_nova("", revisao_sap) is False

    def test_ausencia_ordena_abaixo_de_qualquer_revisao(self) -> None:
        assert ordem_revisao("") < ordem_revisao("0") < ordem_revisao("A")

    def test_none_tambem_ordena_abaixo(self) -> None:
        assert ordem_revisao(None) < ordem_revisao("0")

    def test_ambos_ausentes_continua_congelado(self) -> None:
        assert revisao_wbc_e_mais_nova("", "") is False
