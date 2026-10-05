"""Formatação numérica pt-BR (22/09/2026).

`457578.12` na tela não é um número que alguém confere — é uma sequência de dígitos.
"""
from pathlib import Path as _Path

import pytest

from controleproducao.core.formato import numero_br, quantidade_br

# SIS repo root (tests/controleproducao/<file> -> parents[2]): the tests open package files
# from disk without depending on the directory pytest was launched from.
_RAIZ = _Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("valor, esperado", [
    (457578.12, "457.578,12"),
    (3147638.59, "3.147.638,59"),
    (4035.33, "4.035,33"),
    (1234.5, "1.234,50"),
    (0, "0,00"),
    (-1234.5, "-1.234,50"),
])
def test_valor_em_pt_br(valor, esperado):
    assert numero_br(valor) == esperado


def test_nao_quebra_com_o_que_nao_e_numero():
    """Uma coluna some do SELECT e vira None; a tela não pode cair por causa disso."""
    assert numero_br(None) == ""
    assert numero_br("") == ""
    assert numero_br("à vista") == "à vista"


@pytest.mark.parametrize("valor, esperado", [
    (640, "640"),          # inteiro não ganha ",00": é ruído numa coluna que se percorre
    (1.0, "1"),
    (10.09, "10,09"),      # mas a quantidade real de insumo precisa das duas casas —
    (0.55, "0,55"),        # arredondar aqui esconderia a divergência de um centavo
    (9106.08, "9.106,08"),
    (127740.25, "127.740,25"),
])
def test_quantidade_sem_casas_inuteis(valor, esperado):
    assert quantidade_br(valor) == esperado


def test_decimal_do_banco_passa_direto():
    """`hdbcli` devolve `Decimal`; converter para `float` antes perderia precisão à toa."""
    from decimal import Decimal
    assert quantidade_br(Decimal("10.09")) == "10,09"
    assert numero_br(Decimal("457578.12")) == "457.578,12"


def test_numero_de_op_nao_e_formatado_na_tela():
    """Nº de OP é identificador, não grandeza: "156.378" mandaria o operador procurar um
    número que não existe no SAP."""
    html = open(_RAIZ / "controleproducao/templates/manutencao_op.html", encoding="utf-8").read()
    # Quantidade é formatada...
    assert "{{ valor | qtd }}" in html
    # ...e o ramo do Número OP imprime o valor cru. (A primeira ocorrência da coluna
    # está no <thead>; o que interessa é o ramo do corpo, logo após o comentário.)
    corpo = html.split("Nº de OP é identificador")[1]
    assert "{{ valor }}" in corpo[:300]
    assert "| qtd" not in corpo[:300]
