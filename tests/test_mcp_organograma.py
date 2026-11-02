"""The two org chart tools of the MCP facade (F2 of docs/PLANO_ORGANOGRAMA.md).

The facade is thin: one GET each. What is tested here is what the facade itself decides —
which route, which parameters, and that a topic typed by the model cannot reach another route
(it is quoted into ONE path segment). Loaded by path, under its own name: ``mcp/`` is not a
package and the name ``mcp`` belongs to the installed SDK.
"""
from __future__ import annotations

import importlib.util
import os
from typing import Any

import pytest

pytest.importorskip("mcp.server.fastmcp")

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAMINHO = os.path.join(RAIZ, "mcp", "mcp_server.py")


@pytest.fixture(scope="module")
def fachada():
    spec = importlib.util.spec_from_file_location("_fachada_mcp_organograma", CAMINHO)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def chamadas(fachada, monkeypatch):
    registro: list[tuple[str, dict | None]] = []

    def _fake(path: str, params: dict[str, Any] | None = None):
        registro.append((path, params))
        return {"ok": True}

    monkeypatch.setattr(fachada, "_get", _fake)
    return registro


def test_organograma_inteiro_e_por_membro(fachada, chamadas):
    fachada.organograma()
    fachada.organograma(para="mira")
    assert chamadas == [("/operacao/organograma", None), ("/operacao/organograma", {"para": "mira"})]


def test_quem_cuida_de_usa_um_segmento_so(fachada, chamadas):
    fachada.quem_cuida_de(" ti_servidor ")
    fachada.quem_cuida_de("../../aprovacoes/1234/aprovar")
    assert chamadas[0] == ("/operacao/organograma/temas/ti_servidor", None)
    assert chamadas[1] == ("/operacao/organograma/temas/..%2F..%2Faprovacoes%2F1234%2Faprovar", None)


def test_as_duas_ferramentas_sao_leitura():
    spec = importlib.util.spec_from_file_location("_acesso_mcp_organograma", os.path.join(RAIZ, "mcp", "acesso_mcp.py"))
    acesso_mcp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(acesso_mcp)
    assert acesso_mcp.ESCOPO_DA_FERRAMENTA["organograma"] == "leitura"
    assert acesso_mcp.ESCOPO_DA_FERRAMENTA["quem_cuida_de"] == "leitura"
