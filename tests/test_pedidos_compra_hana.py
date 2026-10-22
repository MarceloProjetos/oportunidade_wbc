"""Purchase-order lines (``pedidos_compra_hana`` + ``GET /compras/pedidos-itens``) — no HANA.

The cache-filling read is stubbed at ``_buscar_no_hana``'s inputs (``_linhas``/``_conectar``)
for the module tests and at ``fetch_itens`` for the route; the MCP truncation is tested against
the facade with ``_get`` stubbed. Contract: ``API_PEDIDOS_COMPRA.md``.
"""
from __future__ import annotations

import importlib.util
import os
from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest

pytest.importorskip("flask")

import api as apimod  # noqa: E402
import pedidos_compra_hana as pc  # noqa: E402
import situacao_pedidos_hana as hana  # noqa: E402
from config import reset_settings  # noqa: E402
from seguranca import credenciais  # noqa: E402

MESTRA = "chave-mestra-de-teste"


def _crua(**over: Any) -> dict[str, Any]:
    """Raw view row as HANA returns it (TIMESTAMP + DECIMAL)."""
    base: dict[str, Any] = {
        "DocEntry": 15025, "DocNum": 9863, "Filial": 1,
        "DataPedido": datetime(2026, 6, 30), "DataEntrega": datetime(2026, 11, 9),
        "CodFornecedor": "F000122", "Fornecedor": "BELENUS LTDA",
        "CodItem": "PAG516SEXTA5/8000916", "Descricao": "P. ALTAMIRA 5/16-18 X 5/8 ZB GRAU 8",
        "Grupo": "Fixação", "StatusPedido": "Aberto", "StatusItem": "Aberto",
        "QtdPedida": Decimal("1200"), "QtdEmAberto": Decimal("1200"),
    }
    base.update(over)
    return base


ITENS = [pc.normalizar(r) for r in (
    _crua(),
    _crua(CodItem="POG516", Descricao="POR. SEXT. PESADA", Grupo="Material Produtivo",
          StatusItem="Fechado", QtdEmAberto=Decimal("0")),
    _crua(DocEntry=16356, DocNum=10713, DataEntrega=datetime(2026, 10, 13), CodFornecedor="F003464",
          Fornecedor="DOX BRASIL INDUSTRIA E COMERCIO DE ACOS S.A.", CodItem="I000139",
          Descricao="RFQ CIVIL 300 1,80X1200", Grupo="Material Produtivo", StatusPedido="Fechado",
          StatusItem="Fechado", QtdPedida=Decimal("22.53"), QtdEmAberto=Decimal("0")),
    _crua(DocEntry=17000, DocNum=11000, DataEntrega=None),
)]


@pytest.fixture(autouse=True)
def _sem_cache():
    pc.limpar_cache()
    yield
    pc.limpar_cache()


def test_normalizar_publica_datas_iso_e_numeros():
    i = ITENS[2]
    assert i == {"doc_entry": 16356, "doc_num": 10713, "filial": 1, "data_pedido": "2026-06-30",
                 "data_entrega": "2026-10-13", "cod_fornecedor": "F003464",
                 "fornecedor": "DOX BRASIL INDUSTRIA E COMERCIO DE ACOS S.A.", "cod_item": "I000139",
                 "descricao": "RFQ CIVIL 300 1,80X1200", "grupo": "Material Produtivo",
                 "status_pedido": "Fechado", "status_item": "Fechado",
                 "qtd_pedida": 22.53, "qtd_em_aberto": 0.0}
    assert ITENS[3]["data_entrega"] is None


@pytest.mark.parametrize("filtros,docs_itens", [
    ({}, [(9863, "PAG516SEXTA5/8000916"), (9863, "POG516"), (10713, "I000139"), (11000, "PAG516SEXTA5/8000916")]),
    ({"status": "aberto"}, [(9863, "PAG516SEXTA5/8000916"), (11000, "PAG516SEXTA5/8000916")]),
    ({"status": "FECHADO"}, [(9863, "POG516"), (10713, "I000139")]),
    ({"pedido": "9863"}, [(9863, "PAG516SEXTA5/8000916"), (9863, "POG516")]),
    ({"fornecedor": "f003464"}, [(10713, "I000139")]),
    ({"item": "pog516"}, [(9863, "POG516")]),
    ({"busca": "dox brasil"}, [(10713, "I000139")]),
    ({"busca": "fixação"}, [(9863, "PAG516SEXTA5/8000916"), (11000, "PAG516SEXTA5/8000916")]),
    # A line without a delivery date never matches a date window.
    ({"entrega_de": "2026-11-01"}, [(9863, "PAG516SEXTA5/8000916"), (9863, "POG516")]),
    ({"entrega_ate": "2026-10-31"}, [(10713, "I000139")]),
    ({"pedido": "1", "status": "todos"}, []),
])
def test_filtrar(filtros, docs_itens):
    assert [(i["doc_num"], i["cod_item"]) for i in pc.filtrar(ITENS, **filtros)] == docs_itens


@pytest.mark.parametrize("filtros", [{"status": "pendente"}, {"pedido": "abc"},
                                     {"entrega_de": "09/11/2026"}, {"entrega_ate": "2026-13-01"}])
def test_filtrar_recusa_parametro_fora_do_dominio(filtros):
    with pytest.raises(pc.ValidationError):
        pc.filtrar(ITENS, **filtros)


def test_fetch_usa_o_cache_e_recarregar_vai_ao_hana(monkeypatch):
    idas = []

    class _Conn:
        def close(self):
            pass

    def _linhas(conn, sql, params=()):
        idas.append(sql)
        return [{"N": 1}] if "COUNT" in sql else [_crua()]

    monkeypatch.setattr(pc, "_schema", lambda: "SBOALTAMIRAPROD")
    monkeypatch.setattr(pc, "_conectar", lambda: _Conn())
    monkeypatch.setattr(pc, "_linhas", _linhas)
    assert pc.fetch_itens()[0]["doc_num"] == 9863
    pc.fetch_itens()
    assert len(idas) == 2                         # count + select, once
    assert '"SBOALTAMIRAPROD"."VW_ALTA_PEDIDOS_COMPRA_ITENS"' in idas[1]
    pc.fetch_itens(recarregar=True)
    assert len(idas) == 4
    assert pc.idade_do_cache_s() is not None


def test_fetch_recusa_volume_acima_do_teto(monkeypatch):
    class _Conn:
        def close(self):
            pass

    monkeypatch.setattr(pc, "_schema", lambda: "S")
    monkeypatch.setattr(pc, "_conectar", lambda: _Conn())
    monkeypatch.setattr(pc, "_linhas", lambda conn, sql, params=(): [{"N": pc._MAX_LINHAS + 1}])
    with pytest.raises(pc.ValidationError):
        pc.fetch_itens()


# ── Route ───────────────────────────────────────────────────────────────────────

@pytest.fixture
def c(monkeypatch):
    monkeypatch.setenv("OS_API_KEY", MESTRA)
    reset_settings()
    apimod._rate_limiter.reset()
    monkeypatch.setattr(pc, "fetch_itens", lambda recarregar=False: list(ITENS))
    monkeypatch.setattr(pc, "idade_do_cache_s", lambda: 7.0)
    apimod.app.config.update(TESTING=True)
    return apimod.app.test_client()


def _chave(*escopos: str) -> dict[str, str]:
    return {"X-API-Key": credenciais.criar("equipe", list(escopos))}


def test_rota_devolve_itens_filtrados(c):
    r = c.get("/compras/pedidos-itens?status=aberto&fornecedor=F000122", headers=_chave("leitura"))
    b = r.get_json()
    assert r.status_code == 200 and b["ok"] is True
    assert (b["total_na_view"], b["total_filtrado"], b["cache_idade_s"]) == (4, 2, 7.0)
    assert {i["doc_num"] for i in b["itens"]} == {9863, 11000}


def test_rota_exige_leitura(c):
    assert c.get("/compras/pedidos-itens").status_code == 401
    assert c.get("/compras/pedidos-itens", headers=_chave("rh")).status_code == 403
    assert c.get("/compras/pedidos-itens", headers={"X-API-Key": MESTRA}).status_code == 200


def test_rota_parametro_invalido_e_422(c):
    r = c.get("/compras/pedidos-itens?status=pendente", headers=_chave("leitura"))
    assert r.status_code == 422 and r.get_json()["motivo"] == "parametro_invalido"


def test_rota_sap_fora_e_503(c, monkeypatch):
    def _fora(recarregar=False):
        raise hana.SAPIndisponivel("HANA fora")
    monkeypatch.setattr(pc, "fetch_itens", _fora)
    r = c.get("/compras/pedidos-itens", headers=_chave("leitura"))
    assert r.status_code == 503 and r.get_json()["motivo"] == "sap_indisponivel"


# ── MCP facade ──────────────────────────────────────────────────────────────────

def _fachada():
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location("_fachada_mcp_compras", os.path.join(raiz, "mcp", "mcp_server.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_mcp_manda_so_os_filtros_preenchidos_e_corta_a_lista(monkeypatch):
    pytest.importorskip("mcp.server.fastmcp")
    f = _fachada()
    chamadas = []
    muitos = [dict(ITENS[0], doc_num=n) for n in range(f._LIMITE_ITENS_COMPRA + 5)]
    monkeypatch.setattr(f, "_get", lambda path, params=None: chamadas.append((path, params)) or {
        "ok": True, "total_filtrado": len(muitos), "itens": muitos})
    r = f.pedidos_compra_itens(fornecedor="F000122")
    assert chamadas == [("/compras/pedidos-itens", {"fornecedor": "F000122", "status": "aberto"})]
    assert r["cortado"] is True and len(r["itens"]) == f._LIMITE_ITENS_COMPRA
    assert r["total_filtrado"] == f._LIMITE_ITENS_COMPRA + 5
