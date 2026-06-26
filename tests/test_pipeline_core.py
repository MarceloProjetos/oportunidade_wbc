"""Testes do núcleo compartilhado (pipeline_core)."""

import pytest

from pipeline_core import coerce_positive_int


@pytest.mark.parametrize('value,expected', [
    (84080, 84080),
    ('84080', 84080),
    ('  84080  ', 84080),   # espaços nas pontas
    ('0084080', 84080),     # zeros à esquerda
    (1, 1),
])
def test_coerce_positive_int_aceita_validos(value, expected):
    assert coerce_positive_int(value) == expected


@pytest.mark.parametrize('value', [
    '-5', '0', '000', '+5', '84080.0', 'abc', '', '1 OR 1=1', '84080; DROP', None,
])
def test_coerce_positive_int_rejeita_invalidos(value):
    with pytest.raises(ValueError):
        coerce_positive_int(value, what='NPED')
