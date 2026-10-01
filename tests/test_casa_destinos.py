"""The one address rule of the .11 screens (casa/destinos.py) — it replaced six copies."""
from __future__ import annotations

import pytest

from casa import destinos


@pytest.mark.parametrize(("configurado", "esperado"), [
    (None, "http://192.168.7.11:8080/"),
    ("", "http://192.168.7.11:8080/"),
    ("   ", "http://192.168.7.11:8080/"),
    ("http://outra:9080/", "http://outra:9080/"),
    ("  http://outra:9080/  ", "http://outra:9080/"),
])
def test_configurado_ganha_senao_o_mesmo_host_na_porta(configurado, esperado):
    assert destinos.endereco(configurado, "http", "192.168.7.11", 8080) == esperado


def test_caminho_proprio_da_tela():
    assert destinos.endereco("", "http", "h", 8077, "/sincronizar") == "http://h:8077/sincronizar"


@pytest.mark.parametrize(("base", "caminho", "esperado"), [
    ("http://h:8080/", "manutencao-op", "http://h:8080/manutencao-op"),
    ("http://h:8080", "manutencao-op", "http://h:8080/manutencao-op"),
    ("http://h:8080/", "", "http://h:8080/"),
    ("http://h:8080/", "/tarefas", "http://h:8080/tarefas"),
])
def test_na_tela_poe_uma_barra_so(base, caminho, esperado):
    assert destinos.na_tela(base, caminho) == esperado
