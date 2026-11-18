"""`wbcpython quantidades` — the open orders born with 1 get the quantity of their text.

What must hold: `--simular` writes nothing; a line changed between the read and the write is skipped, never
overwritten; the first divergence in what the SAP stored stops the run before the next order.
"""

from __future__ import annotations

import pytest

from wbcpython.cli import main


def _linha(line_num, texto, *, qtd=1.0, total=100.0, peso=10.0):
    return {"LineNum": line_num, "Quantity": qtd, "RemainingOpenQuantity": qtd, "LineStatus": "bost_Open",
            "U_INO_ORCITM": str(line_num + 1), "U_INO_D_Adicionais": texto, "LineTotal": total, "Weight1": peso}


def _pedido(docnum, *linhas):
    return {"DocEntry": docnum - 60000, "DocNum": docnum, "Cancelled": "tNO", "DocumentStatus": "bost_Open",
            "ApplyTaxOnFirstInstallment": "tYES", "DocTotal": 1000.0, "DocumentLines": list(linhas)}


def _ambiente(monkeypatch, *, pedidos, depois=None, estado=None):
    """`pedidos`: DocNum -> document as first read; `depois`: DocNum -> document re-read after the PATCH."""
    from wbcpython.infrastructure.hana import quantidades as mod_hana
    from wbcpython.infrastructure.service_layer import client as mod_client
    from wbcpython.infrastructure.service_layer import documentos as mod_doc

    registro: dict[str, list] = {"gravados": []}
    lidos: dict[int, int] = {}

    class ClienteFalso:
        def __init__(self, *a, **kw) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a) -> None:
            return None

    class DocumentosFalso:
        def __init__(self, cliente) -> None:
            pass

        def buscar_por_docnum(self, tipo, docnum):
            lidos[docnum] = lidos.get(docnum, 0) + 1
            if lidos[docnum] > 1 and depois is not None and docnum in depois:
                seguintes = depois[docnum] if isinstance(depois[docnum], list) else [depois[docnum]]
                return seguintes[min(lidos[docnum] - 2, len(seguintes) - 1)]
            return pedidos.get(docnum)

        def estado_das_linhas(self, tipo, doc_entry):
            doc = next(d for d in pedidos.values() if d["DocEntry"] == doc_entry)
            linhas = {ln["LineNum"]: (ln["Quantity"], ln["Weight1"]) for ln in doc["DocumentLines"]}
            return 'W/"e"', (estado or {}).get(doc_entry, linhas)

        def atualizar_quantidades(self, tipo, doc_entry, mudancas, *, etag=None):
            registro["gravados"].append((doc_entry, [(m.line_num, m.nova) for m in mudancas], etag))

    monkeypatch.setattr(mod_client, "ServiceLayerClient", ClienteFalso)
    monkeypatch.setattr(mod_doc, "RepositorioDocumentosVendaServiceLayer", DocumentosFalso)
    monkeypatch.setattr(mod_hana, "pedidos_com_linha_em_um", lambda settings: sorted(pedidos))
    return registro


PROTETORES = "PROTETORES DE COLUNAS 88 Protetores de colunas"


def _gravado(doc, **por_linha):
    linhas = [dict(ln, Quantity=por_linha.get(f"l{ln['LineNum']}", ln["Quantity"])) for ln in doc["DocumentLines"]]
    return {**doc, "DocumentLines": linhas}


def test_simular_mostra_e_nao_grava(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    registro = _ambiente(monkeypatch, pedidos={84603: _pedido(84603, _linha(1, PROTETORES))})
    assert main(["quantidades", "--simular"]) == 0
    assert registro["gravados"] == []
    saida = capsys.readouterr().out
    assert "linha 1 (ORCITM 2): 1 -> 88" in saida and "VATFirst Y" in saida
    assert "1 linha(s) mudariam em 1 pedido(s)" in saida


def test_grava_confere_e_segue(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    a = _pedido(84603, _linha(1, PROTETORES))
    b = _pedido(84604, _linha(0, "LONGARINAS 04 Pares de longarinas"))
    registro = _ambiente(monkeypatch, pedidos={84603: a, 84604: b},
                         depois={84603: _gravado(a, l1=88.0), 84604: _gravado(b, l0=4.0)})
    assert main(["quantidades"]) == 0
    assert registro["gravados"] == [(24603, [(1, 88)], 'W/"e"'), (24604, [(0, 4)], 'W/"e"')]
    assert "2 linha(s) gravada(s) em 2 pedido(s)" in capsys.readouterr().out


def test_divergencia_desfaz_e_para_antes_do_proximo_pedido(monkeypatch: pytest.MonkeyPatch,
                                                            capsys: pytest.CaptureFixture) -> None:
    a = _pedido(84603, _linha(1, PROTETORES, total=10279.28))
    b = _pedido(84604, _linha(0, "LONGARINAS 04 Pares de longarinas"))
    # The SAP took the quantity but recomputed the total (price × 88); undoing puts the read state back.
    errado = {**_gravado(a, l1=88.0)}
    errado["DocumentLines"] = [dict(errado["DocumentLines"][0], LineTotal=904576.64)]
    registro = _ambiente(monkeypatch, pedidos={84603: a, 84604: b}, depois={84603: [errado, a]})
    assert main(["quantidades"]) == 1
    assert registro["gravados"] == [(24603, [(1, 88)], 'W/"e"'), (24603, [(1, 1)], None)], "o 84604 não é tocado"
    saida = capsys.readouterr().out
    assert "total 904576.64, esperado 10279.28" in saida
    assert "Parado no pedido 84603 (desfeito e conferido)" in saida


def test_outra_linha_danificada_ou_doctotal_mudado_tambem_desfaz(monkeypatch: pytest.MonkeyPatch,
                                                                 capsys: pytest.CaptureFixture) -> None:
    """A PATCH that matched the array by position would hit a line nobody asked for."""
    a = _pedido(84603, _linha(0, "PORTA-PALETES 92 Módulos", qtd=92.0), _linha(1, PROTETORES))
    certo = _gravado(a, l1=88.0)
    torto = {**certo, "DocTotal": 1200.0,
             "DocumentLines": [dict(certo["DocumentLines"][0], Quantity=88.0), certo["DocumentLines"][1]]}
    registro = _ambiente(monkeypatch, pedidos={84603: a}, depois={84603: [torto, torto]})
    assert main(["quantidades"]) == 1
    assert len(registro["gravados"]) == 2
    saida = capsys.readouterr().out
    assert "linha 0 (não pedida) mudou" in saida and "DocTotal mudou: 1000.00 -> 1200.00" in saida
    assert "NÃO voltou ao original" in saida


def test_linha_mexida_entre_a_leitura_e_a_gravacao_e_pulada(monkeypatch: pytest.MonkeyPatch,
                                                            capsys: pytest.CaptureFixture) -> None:
    a = _pedido(84603, _linha(1, PROTETORES))
    registro = _ambiente(monkeypatch, pedidos={84603: a}, estado={24603: {1: (5.0, 10.0)}})
    assert main(["quantidades"]) == 0
    assert registro["gravados"] == []
    saida = capsys.readouterr().out
    assert "mudaram desde a leitura — pulado" in saida
    assert "1 pedido(s) com linha deixada de propósito: [84603]" in saida


def test_pedido_que_nao_esta_aberto(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    _ambiente(monkeypatch, pedidos={84603: {**_pedido(84603), "DocumentStatus": "bost_Close"}})
    assert main(["quantidades", "--pedido", "84603"]) == 1
    assert "não está aberto" in capsys.readouterr().out
