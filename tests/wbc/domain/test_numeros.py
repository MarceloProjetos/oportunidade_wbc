"""Testes da normalização numérica dos campos de texto do SAP."""

from __future__ import annotations

import logging
from decimal import Decimal

import pytest

from wbcpython.domain.numeros import (
    NumeroInvalido,
    formatar_para_sap,
    normalizar_decimal,
    normalizar_decimal_tolerante,
)


class TestAsDuasConvencoesReais:
    """Os dois formatos observados nos dados reais do OrcDetalhe."""

    @pytest.mark.parametrize(
        ("entrada", "esperado"),
        [
            ("0.0000", "0"),
            ("0,0000", "0"),
            ("10.0000", "10"),
            ("10,0000", "10"),
            ("1.0000", "1"),
            ("1,0000", "1"),
            ("9.0000", "9"),
        ],
    )
    def test_ambas_dao_o_mesmo_numero(self, entrada: str, esperado: str) -> None:
        assert normalizar_decimal(entrada) == Decimal(esperado)


class TestSeparadorDeMilhar:
    def test_ponto_milhar_e_virgula_decimal(self) -> None:
        assert normalizar_decimal("1.234,56") == Decimal("1234.56")

    def test_virgula_milhar_e_ponto_decimal(self) -> None:
        assert normalizar_decimal("1,234.56") == Decimal("1234.56")

    def test_o_ultimo_separador_e_o_decimal(self) -> None:
        # A regra que resolve o caso ambíguo, nas duas direções.
        assert normalizar_decimal("1.234.567,89") == Decimal("1234567.89")
        assert normalizar_decimal("1,234,567.89") == Decimal("1234567.89")

    def test_tres_casas_com_um_separador_e_lido_como_milhar(self) -> None:
        # Decisão documentada: "1.234" em dado de ERP é mil duzentos e trinta e quatro.
        assert normalizar_decimal("1.234") == Decimal(1234)

    def test_duas_casas_e_decimal(self) -> None:
        assert normalizar_decimal("1.23") == Decimal("1.23")
        assert normalizar_decimal("1,23") == Decimal("1.23")

    def test_quatro_casas_e_decimal(self) -> None:
        assert normalizar_decimal("1.2345") == Decimal("1.2345")


class TestTiposNativos:
    def test_int(self) -> None:
        assert normalizar_decimal(5) == Decimal(5)

    def test_float(self) -> None:
        assert normalizar_decimal(1.5) == Decimal("1.5")

    def test_decimal_passa_direto(self) -> None:
        d = Decimal("3.14")
        assert normalizar_decimal(d) is d

    def test_none_vira_zero(self) -> None:
        assert normalizar_decimal(None) == Decimal(0)

    def test_none_com_padrao(self) -> None:
        assert normalizar_decimal(None, padrao=Decimal(-1)) == Decimal(-1)

    def test_vazio_vira_zero(self) -> None:
        assert normalizar_decimal("") == Decimal(0)
        assert normalizar_decimal("   ") == Decimal(0)


class TestSinal:
    def test_negativo_com_virgula(self) -> None:
        assert normalizar_decimal("-1.234,56") == Decimal("-1234.56")

    def test_positivo_explicito(self) -> None:
        assert normalizar_decimal("+10,50") == Decimal("10.50")


class TestValorInvalido:
    @pytest.mark.parametrize("entrada", ["abc", "R$ 10,00", "1.2.3,4,5x", "--", "N/A"])
    def test_texto_nao_numerico_levanta(self, entrada: str) -> None:
        with pytest.raises(NumeroInvalido):
            normalizar_decimal(entrada)

    def test_versao_tolerante_devolve_zero(self) -> None:
        assert normalizar_decimal_tolerante("abc", campo="U_ORCVALEMB") == Decimal(0)

    def test_versao_tolerante_registra_aviso(self, caplog: pytest.LogCaptureFixture) -> None:
        # Devolver zero calado seria o pior comportamento possível: o aviso é o
        # que permite descobrir dado sujo em produção.
        with caplog.at_level(logging.WARNING):
            normalizar_decimal_tolerante("xyz", campo="U_ORCIMP_RETORNO")
        assert "U_ORCIMP_RETORNO" in caplog.text
        assert "xyz" in caplog.text


class TestFormatarParaSap:
    def test_usa_ponto_e_quatro_casas(self) -> None:
        assert formatar_para_sap(Decimal("1.5")) == "1.5000"
        assert formatar_para_sap(0) == "0.0000"
        assert formatar_para_sap(10) == "10.0000"

    def test_ida_e_volta(self) -> None:
        for original in ["0,0000", "10.0000", "1.234,56"]:
            valor = normalizar_decimal(original)
            assert normalizar_decimal(formatar_para_sap(valor)) == valor
