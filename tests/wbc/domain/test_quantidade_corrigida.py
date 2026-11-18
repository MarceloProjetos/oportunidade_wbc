"""Which lines of an existing order `wbcpython quantidades` changes, and how the write is proven.

Lines as the Service Layer returns them; the texts are the real ones of order 84603.
"""

from __future__ import annotations

from wbcpython.domain.quantidade_corrigida import QuantidadeCorrigida, divergencias, quantidades_a_corrigir, retrato


def _linha(line_num, texto, *, qtd=1.0, aberta=None, status="bost_Open", orcitm="5", total=100.0, peso=10.0):
    return {
        "LineNum": line_num, "Quantity": qtd, "RemainingOpenQuantity": qtd if aberta is None else aberta,
        "LineStatus": status, "U_INO_ORCITM": orcitm, "U_INO_D_Adicionais": texto,
        "LineTotal": total, "Weight1": peso,
    }


PEDIDO_84603 = [
    _linha(0, "PORTA-PALETES 92 Módulos de estruturas metálicas", qtd=92.0, orcitm="1"),
    _linha(1, "PROTETORES DE COLUNAS 88 Protetores de colunas medindo 300 mm", orcitm="5", total=10279.28, peso=436.9),
    _linha(2, "PROTETOR DE MONTANTE 02 Protetores de montante  medindo 1000 x 400 mm", orcitm="10", total=786.34),
    _linha(3, "PROTETOR DE MONTANTE 04 Protetores de montante  medindo 2300 x 400 mm", orcitm="15", total=2000.56),
]


def test_84603_corrige_as_tres_linhas_de_protetor_e_mantem_total_e_peso() -> None:
    mudancas, fora = quantidades_a_corrigir(PEDIDO_84603)
    assert [(m.line_num, m.nova) for m in mudancas] == [(1, 88), (2, 2), (3, 4)]
    assert (mudancas[0].total, mudancas[0].peso) == (10279.28, 436.9)
    assert fora == []


def test_so_mexe_em_linha_aberta_em_um_da_integracao_e_com_outra_quantidade_no_texto() -> None:
    linhas = [
        _linha(0, "PROTETORES 88 Protetores", status="bost_Close"),
        _linha(1, "PROTETORES 88 Protetores", qtd=3.0, aberta=3.0),
        _linha(2, "PROTETORES 88 Protetores", orcitm=""),
        _linha(3, "PROTETORES sem número"),
        _linha(4, "MEZANINO 01 Conjunto de estruturas"),
    ]
    assert quantidades_a_corrigir(linhas) == ([], [])


def test_linha_entregue_em_parte_fica_com_um_e_e_relatada() -> None:
    mudancas, fora = quantidades_a_corrigir([_linha(1, "PROTETORES 88 Protetores", aberta=0.0)])
    assert mudancas == []
    assert fora == ["linha 1 (ORCITM 5) já entregue em parte: fica com 1, o texto diz 88"]


def test_total_que_nao_fecha_com_preco_de_4_casas_fica_e_e_relatado() -> None:
    """100.00 ÷ 3 = 33.3333 -> 99.9999 -> 100.00 fecha; 0.10 ÷ 3 = 0.0333 -> 0.0999 -> 0.10 fecha; 1.00 ÷ 7 não."""
    assert quantidades_a_corrigir([_linha(1, "PROTETORES 03 Protetores", total=100.0)])[0][0].nova == 3
    mudancas, fora = quantidades_a_corrigir([_linha(1, "PROTETORES 1678 Protetores", total=35021.29)])
    assert mudancas == []
    assert fora == ["linha 1 (ORCITM 5): 35021.29 ÷ 1678 não fecha ao centavo com preço de 4 casas: fica"]


def test_gravacao_conferida_quando_o_sap_guardou_o_que_foi_mandado() -> None:
    m = QuantidadeCorrigida(line_num=1, orcitm="5", nova=88, total=10279.28, peso=436.9, texto="")
    assert divergencias([m], [{"LineNum": 1, "Quantity": 88.0, "LineTotal": 10279.28, "Weight1": 436.9}]) == []


def test_qualquer_diferenca_no_que_o_sap_guardou_e_relatada() -> None:
    m = QuantidadeCorrigida(line_num=1, orcitm="5", nova=88, total=10279.28, peso=436.9, texto="")
    gravada = {"LineNum": 1, "Quantity": 1.0, "LineTotal": 10279.27, "Weight1": 38447.2}
    assert divergencias([m], [gravada]) == [
        "linha 1: quantidade 1, esperado 88",
        "linha 1: total 10279.27, esperado 10279.28",
        "linha 1: peso 38447.2, esperado 436.9",
    ]
    assert divergencias([m], []) == ["linha 1 sumiu do pedido"]


def test_com_o_retrato_as_outras_linhas_tem_de_ficar_iguais() -> None:
    m = QuantidadeCorrigida(line_num=1, orcitm="5", nova=88, total=10279.28, peso=436.9, texto="")
    antes = retrato(PEDIDO_84603)
    gravado = [dict(ln) for ln in PEDIDO_84603]
    gravado[1]["Quantity"] = 88.0
    assert divergencias([m], gravado, antes) == []
    gravado[2]["Quantity"] = 88.0
    assert divergencias([m], gravado, antes) == [
        "linha 2 (não pedida) mudou: (1.0, 786.34, 10.0) -> (88.0, 786.34, 10.0)"
    ]
    assert divergencias([m], gravado[:2], antes)[0] == "linhas do pedido mudaram: antes [0, 1, 2, 3], agora [0, 1]"
