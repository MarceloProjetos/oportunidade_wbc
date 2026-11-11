"""Orders closed by the future-delivery NF before their OPs (84537, 84584 — 11/11/2026).

SAP closes the order at the "simples faturamento" NF (CFOP 5922/6922). The test run on 84584
showed what it accepts on a closed order: header UDFs and OPs linked to it, yes; ``U_INO_OP`` on a
closed line, no (-1029), which aborted the order after its first group. It also showed a payment
text longer than the 254 characters of ``U_ORCPGT`` refusing the whole ``OrcDetalhe``.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import faturamento_pedido as fat
from controleproducao.core import entrega_futura
from controleproducao.core.exceptions import WbcDatabaseError
from controleproducao.modules.manutencao_op import fechados
from controleproducao.modules.pedidos_wbc import service as svc
from controleproducao.modules.pedidos_wbc.schemas import NewOrcPrd

FUTURA_PENDENTE = fat.Faturamento("entrega_futura", "nenhuma", "2026-11-10")
FUTURA_REMETIDA = fat.Faturamento("entrega_futura", "completa", "2026-04-02")
VENDA = fat.Faturamento("venda", None, "2026-10-08")


def _faturamento(monkeypatch, resultado):
    """`ler_faturamento` replaced: the rule itself is covered by `tests/test_faturamento_pedido.py`."""
    chamadas: list[list[int]] = []

    def ler(_sap, _schema, pedidos):
        chamadas.append(list(pedidos))
        return resultado

    monkeypatch.setattr(fat, "ler_faturamento", ler)
    return chamadas


# ---------------------------------------------------------------------------
# core.entrega_futura
# ---------------------------------------------------------------------------
def test_pendentes_so_devolve_entrega_futura_ainda_nao_remetida(monkeypatch):
    chamadas = _faturamento(monkeypatch, {21043: FUTURA_PENDENTE, 18000: FUTURA_REMETIDA, 20900: VENDA})
    assert entrega_futura.pendentes(MagicMock(), [21043, 18000, 20900, 21043]) == {21043}
    assert chamadas == [[18000, 20900, 21043]]


def test_pendentes_sem_pedido_nao_consulta_e_falha_vira_none(monkeypatch):
    chamadas = _faturamento(monkeypatch, None)
    assert entrega_futura.pendentes(MagicMock(), []) == set()
    assert chamadas == []
    assert entrega_futura.pendentes(MagicMock(), [1]) is None


def test_adaptador_entrega_dataframe_com_os_parametros_do_sql_seguro():
    leitor = MagicMock()
    leitor.fetch_all = MagicMock(return_value=[{"Pedido": 21043, "Futura": 1.0}])
    df = entrega_futura._ComoDataFrame(leitor).execute_query("SELECT 1", [21043])
    assert leitor.fetch_all.call_args.args == ("SELECT 1", (21043,))
    assert [int(r["Pedido"]) for _, r in df.iterrows()] == [21043]


def test_regra_de_verdade_sobre_o_leitor_do_cp():
    """The real `ler_faturamento` through the adapter (the other tests replace it): 84584 with its
    future NF and no remessa is pending; a fully shipped one is not."""
    from datetime import datetime
    from decimal import Decimal

    def fetch_all(sql, params=()):
        if '"UltimaNF"' in sql:
            return [{"Pedido": 21043, "UltimaNF": datetime(2026, 11, 10), "Futura": Decimal("60635.00")},
                    {"Pedido": 18000, "UltimaNF": datetime(2026, 4, 2), "Futura": Decimal("1000.00")}]
        if '"Devolvido"' in sql:
            return []
        if '"Remessa"' in sql:
            return [{"Pedido": 18000, "Remessa": 7001, "Valor": Decimal("1000.00")}]
        raise AssertionError(sql)

    leitor = MagicMock()
    leitor.fetch_all = MagicMock(side_effect=fetch_all)
    assert entrega_futura.pendentes(leitor, [21043, 18000]) == {21043}


def test_falha_de_import_ou_leitura_vira_desconhecido(monkeypatch):
    def quebra(*_a):
        raise ImportError("pipeline_core")

    monkeypatch.setattr(fat, "ler_faturamento", quebra)
    assert entrega_futura.pendentes(MagicMock(), [21043]) is None


# ---------------------------------------------------------------------------
# "Pedidos novos" lists the closed future-delivery order
# ---------------------------------------------------------------------------
def _linha(doc_num: int, opp: int, orc: str, doc_entry: int | None = None) -> dict:
    linha = {
        "Selecionar": "N", "Num Oportunidade": opp, "Nº Pedido": doc_num, "Cod.Cliente": "C006321",
        "Nome Cliente": "POLI FILTRO", "Total Pedido": 66698.46, "Data Lancamento": "2026-11-09 00:00:00",
        "Nº Oportunidade": orc, "Nota Espelho": "N",
    }
    if doc_entry is not None:
        linha["DocEntry"] = doc_entry
    return linha


def _leitor_da_busca(abertos: list[dict], fechados_: list[dict] | Exception) -> MagicMock:
    def fetch_all(sql, *_a):
        if "\"DocStatus\" = 'C'" in sql:
            if isinstance(fechados_, Exception):
                raise fechados_
            return list(fechados_)
        return [] if "OWOR" in sql else list(abertos)

    leitor = MagicMock()
    leitor.fetch_all = MagicMock(side_effect=fetch_all)
    return leitor


def test_novos_traz_o_fechado_de_entrega_futura_pendente_com_selo(monkeypatch):
    _faturamento(monkeypatch, {21043: FUTURA_PENDENTE, 18000: FUTURA_REMETIDA})
    leitor = _leitor_da_busca(
        [_linha(84600, 9001, "00126100")],
        [_linha(84584, 15500, "00126063", 21043), _linha(83838, 14000, "00124000", 18000)],
    )
    pedidos = asyncio.run(svc.buscar_pedidos_para_integrar(leitor))
    assert [(p.doc_num, p.entrega_futura) for p in pedidos] == [(84600, False), (84584, True)]


def test_integrados_tambem_traz_o_fechado_pendente(monkeypatch):
    """Reprocessar is the screen's way back from a Processar that stopped midway (review, 11/11)."""
    _faturamento(monkeypatch, {21043: FUTURA_PENDENTE})
    leitor = _leitor_da_busca([_linha(84600, 9001, "00126100")], [_linha(84584, 15500, "00126063", 21043)])
    pedidos = asyncio.run(svc.buscar_pedidos_para_integrar(leitor, integrados=True))
    assert [(p.doc_num, p.entrega_futura) for p in pedidos] == [(84600, False), (84584, True)]
    lidos = [c.args[0] for c in leitor.fetch_all.call_args_list if "\"DocStatus\" = 'C'" in c.args[0]]
    assert lidos == [svc.q.BUSCA_PEDIDOS_FECHADOS_INTEGRADOS]


@pytest.mark.parametrize("falha", ["faturamento", "leitura"])
def test_falha_deixa_o_fechado_fora_e_os_abertos_na_lista(monkeypatch, falha):
    _faturamento(monkeypatch, None)
    fechados_ = RuntimeError("HANA caiu") if falha == "leitura" else [_linha(84584, 15500, "00126063", 21043)]
    leitor = _leitor_da_busca([_linha(84600, 9001, "00126100")], fechados_)
    assert [p.doc_num for p in asyncio.run(svc.buscar_pedidos_para_integrar(leitor))] == [84600]


@pytest.mark.parametrize(("sql", "processado"), [
    (svc.q.BUSCA_PEDIDOS_FECHADOS_PARA_INTEGRAR, "N"), (svc.q.BUSCA_PEDIDOS_FECHADOS_INTEGRADOS, "Y"),
])
def test_sql_dos_fechados_exige_nf_futura_viva(sql, processado):
    for trecho in ("\"DocStatus\" = 'C'", "\"CANCELED\" = 'N'",
                   f"IFNULL(ORDR.\"U_INO_ProcessWBC\",'N') = '{processado}'",
                   "\"CFOPCode\" IN ('5922','6922')", "I.\"CANCELED\" = 'N'", "\"DocEntry\" as \"DocEntry\""):
        assert trecho in sql


# ---------------------------------------------------------------------------
# Closed lines: no U_INO_OP (SAP -1029), and the order goes on
# ---------------------------------------------------------------------------
def test_linha_fechada_nao_recebe_u_ino_op():
    sl, hana = AsyncMock(), MagicMock()
    hana.fetch_all = MagicMock(return_value=[
        {"LineNum": 0, "U_INO_ORCITM": "1", "LineStatus": "C"},
        {"LineNum": 1, "U_INO_ORCITM": "5", "LineStatus": "O"},
    ])
    asyncio.run(svc.marca_op_nas_linhas(sl, hana, ["1", "5"], 164396, "21043"))
    assert sl.update_entity.await_args.args[2] == {"DocumentLines": [{"LineNum": 1, "U_INO_OP": 164396}]}


def test_pedido_todo_fechado_nao_manda_patch():
    sl, hana = AsyncMock(), MagicMock()
    hana.fetch_all = MagicMock(return_value=[{"LineNum": 0, "U_INO_ORCITM": "1", "LineStatus": "C"}])
    asyncio.run(svc.marca_op_nas_linhas(sl, hana, ["1"], 164396, "21043"))
    assert sl.update_entity.await_count == 0


def test_reprocessar_nao_zera_u_ino_op_da_linha_fechada():
    sl = AsyncMock()
    sl.get_by_key = AsyncMock(return_value={"DocumentLines": [
        {"LineNum": 0, "LineStatus": "bost_Close"}, {"LineNum": 1, "LineStatus": "bost_Open"},
    ]})
    asyncio.run(svc._update_tab_pedido_cong(sl, [], "00126063", "21043", 0, "N"))
    assert sl.update_entity.await_args.args[2] == {
        "U_INO_ProcessWBC": "N", "DocumentLines": [{"LineNum": 1, "U_INO_OP": 0}],
    }


def test_linha_fechada_nao_conta_como_ja_tendo_op_e_nao_e_limpa():
    # It keeps the U_INO_OP of an OP made while the order was open: SAP refuses to zero it.
    # Only on an order closed as a whole: an open order's partially invoiced line keeps the old rule.
    assert "OR P.\"DocStatus\" = 'C'" in svc.q.BUSCA_MAX_ITEM_LINHA
    assert "T0.\"LineStatus\" <> 'C'" in svc.q.LINHAS_COM_OP


# ---------------------------------------------------------------------------
# U_ORCPGT: 254 characters
# ---------------------------------------------------------------------------
def _cabecalho(**campos) -> NewOrcPrd:
    vazio = {float: 0.0, int: 0, str: ""}
    base = {nome: vazio.get(info.annotation, "") for nome, info in NewOrcPrd.model_fields.items() if nome != "linhas"}
    return NewOrcPrd(**{**base, **campos})


def test_texto_de_pagamento_longo_vai_cortado_em_254():
    longo = "12% 21 DDP|à 21 dias do pedido;" * 12
    body = svc._monta_body_orc_detalhe("00126063", _cabecalho(orc_pgt=longo), [])
    assert body["U_ORCPGT"] == longo[:254]
    curto = svc._monta_body_orc_detalhe("00126063", _cabecalho(orc_pgt="30 DDP"), [])
    assert curto["U_ORCPGT"] == "30 DDP"


def test_condicao_de_pagamento_longa_transborda_para_pgtcod2():
    """84584, 2nd run: the long condition refused the OrcDetalhe on `U_PGTCOD` (254)."""
    longa = "25% a 07 DDP / 40% a 59 DDP / 35% a 88 DDP;" * 8
    body = svc._monta_body_orc_detalhe("00126063", _cabecalho(pgt_cod=longa), [])
    assert body["U_PGTCOD"] == longa[:254] and body["U_PGTCOD2"] == longa[254:]
    curta = svc._monta_body_orc_detalhe("00126063", _cabecalho(pgt_cod="1210"), [])
    assert curta["U_PGTCOD"] == "1210" and "U_PGTCOD2" not in curta


def test_textos_livres_cortados_no_tamanho_do_campo_e_codigos_inteiros():
    nome = "POLI FILTRO INDUSTRIA E COMERCIO DE PECAS PARA AUTOS LTDA"          # 57 > 50
    linha = {"U_INO_PROD": "P" * 230, "U_INO_COR": "C" * 60, "U_INO_CODIGO": "X" * 70}
    body = svc._monta_body_orc_detalhe("00126063", _cabecalho(cli_nom=nome, rep_cod="R" * 30), [linha])
    assert body["U_CLINOM"] == nome[:50]
    assert body["U_REPCOD"] == "R" * 30                       # a code: never cut
    filha = body[svc._UDO_ORC_DETALHE_CHILD][0]
    assert (len(filha["U_INO_PROD"]), len(filha["U_INO_COR"]), len(filha["U_INO_CODIGO"])) == (200, 50, 70)
    assert linha["U_INO_PROD"] == "P" * 230                    # the caller's dict is not changed


# ---------------------------------------------------------------------------
# Manutenção de OP: "Pedidos fechados com OPs abertas" leaves them out
# ---------------------------------------------------------------------------
def _linha_fechado(pedido: int, doc_entry: int) -> dict:
    return {"Pedido": pedido, "Cliente": "X", "Entrega": "2026-12-14 00:00:00", "Atrasado": "N",
            "Total_OS": 17, "Total_OS_Fechadas": 0, "DocEntry": doc_entry, "Abertas": 17, "Planejadas": 17}


def test_quadro_de_fechados_nao_oferece_entrega_futura_pendente(monkeypatch):
    _faturamento(monkeypatch, {21043: FUTURA_PENDENTE, 20500: VENDA})
    leitor = MagicMock()
    leitor.fetch_all = MagicMock(return_value=[_linha_fechado(84584, 21043), _linha_fechado(84500, 20500)])
    assert [p["pedido"] for p in fechados.pedidos_fechados_com_op(leitor)] == [84500]
    assert 'V."DocEntry" "DocEntry"' in fechados.PEDIDOS_FECHADOS_COM_OP


def test_quadro_de_fechados_sem_doc_entry_nao_monta_a_lista(monkeypatch):
    """No DocEntry = the future-delivery check cannot run: unknown, never "offer to Encerrar"."""
    _faturamento(monkeypatch, {})
    leitor = MagicMock()
    leitor.fetch_all = MagicMock(return_value=[{**_linha_fechado(84500, 20500), "DocEntry": None}])
    with pytest.raises(WbcDatabaseError):
        fechados.pedidos_fechados_com_op(leitor)


def test_quadro_de_fechados_sem_faturamento_nao_monta_a_lista(monkeypatch):
    _faturamento(monkeypatch, None)
    leitor = MagicMock()
    leitor.fetch_all = MagicMock(return_value=[_linha_fechado(84500, 20500)])
    with pytest.raises(WbcDatabaseError):
        fechados.pedidos_fechados_com_op(leitor)
