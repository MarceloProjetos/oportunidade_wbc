"""Testes do nucleo portado + do que e' exclusivo da .11 (F1).

Tudo puro, sem HANA: os construtores reproduzem a linha crua exatamente como o
``fetch_status_pedidos`` devolve (PascalCase, datas ISO, ``Prazo_Entrega`` como texto,
genero do status variando por coluna).

Divisao de trabalho com ``test_situacao_pedidos_diffavel.py``: la se compara o FONTE com
o V117; aqui se verifica o COMPORTAMENTO na .11 -- inclusive o dos quatro acrescimos que
nao existem no V117 (``alerta_liberacao``, ``com_alerta``, ``filtrar_bloqueio``,
``resumir``).

Plano: ``docs/PLANO_SITUACAO_PEDIDOS_MCP.md``.
"""
from __future__ import annotations

from datetime import date
from typing import Any

import pytest

import sap_montagem_labels
import situacao_pedidos as sp

#: Data de referencia fixa. Sem ela o aging dependeria do dia em que a suite roda.
HOJE = date(2026, 3, 10)


def _row(**over: Any) -> dict[str, Any]:
    """Uma linha crua da view, com o texto e os generos que a F0 mediu em producao."""
    base: dict[str, Any] = {
        "DocEntry": 15118,
        "DocNum": 83554,
        "Data_Pedido": "2026-01-21",
        "CardCode": "C005324",
        "CardName": "JKV MADEIRAS E FERRAGENS COMERCIAL LTDA",
        "GroupNum": 967,
        "PymntGroup": "100% ENTREGA",
        "Integrar": "S",
        "Financeiro": "Liberado",
        "Sinal": "N",
        "Producao": "Liberada",
        "Entrega": "Liberada",
        "Data_Entrega": "2026-02-25",
        "Prazo_Entrega": "23/02 A 27/02",
        "Atrasado": "N",
        "DDO": "N",
        "Peso": 100.0,
        "StatusPedido": "Aberto",
        "Data_Lib_Fin": "2026-01-22",
        "Data_Lib_Prod": "2026-01-23",
        "Data_Pagto": None,
        "Total_OS": 5,
        "Total_OS_Fechadas": 3,
        "CotacaoWbc": "00125238",
        "VersaoWbc": "A",
        "MontagemCod": "3",
        "MontagemTexto": ".",
        "MontagemValor": 9700.0,
        "MontadorCnpj": "67.133.900/0001-88",
        "MontadorNome": "MONTARIM MONTAGENS INDUSTRIAIS LTDA",
        "DocTotal": 41250.0,
        "DocCur": "R$",
        "SlpCode": 12,
        "Vendedor": "MARCOS",
    }
    base.update(over)
    return base


def _lote() -> list[dict[str, Any]]:
    """Cinco pedidos com contagens conhecidas -- e' o lote das asserções de KPI.

    2 travados no financeiro (um deles ha 48 dias, em aberto), 2 na producao,
    1 na entrega, 1 atrasado em aberto, 1 fechado que foi entregue atrasado.
    """
    return [
        _row(),  # tudo liberado, aberto
        _row(DocEntry=2, DocNum=84260, CardName="FLOW X INTERNATIONAL BRASIL LTDA",
             Data_Pedido="2026-01-21", Financeiro="Bloqueado", Producao="Bloqueada",
             Entrega="Bloqueada", Sinal="S", Prazo_Entrega="21/09 A 25/09",
             Data_Entrega="2026-09-23",
             PymntGroup="30% SINAL / 20% ENTREGA / 30% 45DDL / 10% 65DDL / 10% 85DDL"),
        _row(DocEntry=3, DocNum=84293, CardName="COMPANHIA DE CIMENTO CAMPEAO",
             Data_Pedido="2026-03-05", Financeiro="Bloqueado", MontadorCnpj="",
             MontadorNome=""),
        _row(DocEntry=4, DocNum=83832, CardName="FUNDACAO BRADESCO",
             Producao="Bloqueada", Atrasado="S"),
        _row(DocEntry=5, DocNum=83100, StatusPedido="Fechado", Atrasado="S",
             Financeiro="Bloqueado"),
    ]


@pytest.fixture(autouse=True)
def _sem_sap():
    """Nenhum teste desta suite fala com o SAP -- o rotulo sai do fallback.

    Sem isto, um dia em que a F2 ligar a fonte real, a suite passaria a depender de rede.
    """
    sap_montagem_labels.registrar_fonte(None)
    yield
    sap_montagem_labels.registrar_fonte(None)


# --- nucleo portado ---------------------------------------------------------

def test_status_canoniza_os_dois_generos():
    """"Liberada" da Producao e "Liberado" do Financeiro viram o MESMO valor."""
    p = sp.normalizar([_row()], hoje=HOJE)[0]
    assert p["financeiro"] == "Liberado"
    assert p["producao"] == "Liberado"
    assert p["entrega"] == "Liberado"


def test_status_desconhecido_passa_visivel():
    """Valor fora do dominio nao vira "Liberado" por descuido -- fica VISIVEL."""
    p = sp.normalizar([_row(Producao="Em análise")], hoje=HOJE)[0]
    assert p["producao"] == "Em análise"


def test_prazo_fim_pega_o_ano_da_data_de_entrega():
    """``Prazo_Entrega`` e' texto sem ano; quem da o ano e' a ``Data_Entrega``."""
    assert sp.prazo_fim("23/02 A 27/02", "2026-02-25") == "2026-02-27"


def test_prazo_fim_guarda_a_virada_de_ano():
    """Janela 28/12 A 01/01 entregue em dezembro: o fim e' 01/01 do ANO SEGUINTE."""
    assert sp.prazo_fim("28/12 A 01/01", "2026-12-30") == "2027-01-01"


def test_prazo_fim_devolve_none_em_data_invalida():
    """31/02 nao existe: ``None`` ("não sei") em vez de um numero inventado."""
    assert sp.prazo_fim("28/02 A 31/02", "2026-02-25") is None
    assert sp.prazo_fim("sem prazo", "2026-02-25") is None
    assert sp.prazo_fim("23/02 A 27/02", None) is None


def test_pedido_fechado_nao_conta_como_atrasado():
    """A regra do dono (31/07/2026): atraso e' coisa de pedido em ABERTO.

    O ``S`` cru da view sobrevive em ``atrasado_sap`` -- e' o que permite dizer
    "foi entregue com atraso".
    """
    p = sp.normalizar([_row(StatusPedido="Fechado", Atrasado="S")], hoje=HOJE)[0]
    assert p["atrasado"] is False
    assert p["atrasado_sap"] is True


def test_kpis_e_tabela_derivam_da_mesma_lista():
    """A invariante da conferencia: divergencia entre card e tabela e' bug."""
    d = sp.montar_dashboard(_lote())
    assert d["kpis"]["total"] == len(d["pedidos"]) == 5
    assert d["conferencia"]["total_kpi"] == d["conferencia"]["total_tabela"]


def test_kpis_contam_cada_etapa():
    k = sp.montar_dashboard(_lote())["kpis"]
    assert k["financeiro_bloqueado"] == 3
    assert k["producao_bloqueada"] == 2
    assert k["entrega_bloqueada"] == 1
    assert k["atrasados"] == 1  # o fechado com Atrasado='S' NAO entra


def test_montadores_do_recorte_ignora_pedido_sem_montador():
    m = sp.montar_dashboard(_lote())["montadores"]
    assert len(m) == 1
    assert m[0]["nome"] == "MONTARIM MONTAGENS INDUSTRIAIS LTDA"
    assert m[0]["qtd"] == 4  # os 5 menos o que veio sem CNPJ


def test_filtrar_recusa_kpi_fora_do_dominio():
    with pytest.raises(sp.ValidationError):
        sp.filtrar(sp.normalizar(_lote(), hoje=HOJE), kpi="inventado")


def test_filtrar_por_busca_acha_pela_cotacao_wbc():
    """Quem pergunta muitas vezes so tem o numero do WBC na mao."""
    pedidos = sp.normalizar(_lote(), hoje=HOJE)
    assert len(sp.filtrar(pedidos, busca="00125238")) == 5


def test_montagem_usa_o_rotulo_oficial_do_sap():
    """Codigo 3 -> o rotulo da lista de valores validos, nunca um mapa local."""
    p = sp.normalizar([_row()], hoje=HOJE)[0]
    assert p["montagem"]["tipo"] == "MONTAGEM POR CONTA DE TERCEIROS"


def test_montagem_cai_no_texto_livre_quando_nao_ha_codigo():
    """Pedido antigo traz nota real no texto livre -- vale mais que "SEM MONTAGEM"."""
    p = sp.normalizar(
        [_row(MontagemCod="", MontagemTexto="Seguirá 01 lateral pré-montado")],
        hoje=HOJE)[0]
    assert p["montagem"]["tipo"] == "Seguirá 01 lateral pré-montado"


# --- exclusivo da .11 -------------------------------------------------------

def test_alerta_liberacao_so_aparece_acima_do_limite():
    """A regra e' do nucleo (``fin_liberacao_atrasada``); aqui so se escreve a frase."""
    pedidos = sp.com_alerta(sp.normalizar(_lote(), hoje=HOJE))
    por_num = {p["doc_num"]: p for p in pedidos}

    travado = por_num[84260]  # bloqueado no financeiro desde 21/01, em aberto
    assert travado["dias_desde_pedido"] == 48
    assert travado["alerta_liberacao"] == "Mais de 10 dias preso no financeiro (48 dias)"

    recente = por_num[84293]  # bloqueado, mas so ha 5 dias
    assert recente["alerta_liberacao"] is None

    liberado = por_num[83554]
    assert liberado["alerta_liberacao"] is None


def test_alerta_liberacao_ignora_pedido_fechado():
    """Pedido fechado nunca alarma -- mesma logica do ``atrasado``."""
    pedidos = sp.com_alerta(sp.normalizar(_lote(), hoje=HOJE))
    fechado = next(p for p in pedidos if p["doc_num"] == 83100)
    assert fechado["financeiro"] == "Bloqueado"
    assert fechado["alerta_liberacao"] is None


def test_alerta_liberacao_no_limite_exato_nao_alarma():
    """"Mais de 10 dias" e' ESTRITAMENTE mais: no 10o dia ainda nao alarma."""
    dez = sp.com_alerta(sp.normalizar(
        [_row(Financeiro="Bloqueado", Data_Pedido="2026-02-28")], hoje=HOJE))[0]
    onze = sp.com_alerta(sp.normalizar(
        [_row(Financeiro="Bloqueado", Data_Pedido="2026-02-27")], hoje=HOJE))[0]
    assert dez["dias_desde_pedido"] == 10
    assert dez["alerta_liberacao"] is None
    assert onze["alerta_liberacao"] == "Mais de 10 dias preso no financeiro (11 dias)"


def test_filtrar_bloqueio_qualquer_pega_as_tres_etapas():
    """A consulta 2 do plano: travado em pelo menos UMA etapa."""
    pedidos = sp.normalizar(_lote(), hoje=HOJE)
    nums = {p["doc_num"] for p in sp.filtrar_bloqueio(pedidos, "qualquer")}
    assert nums == {84260, 84293, 83832, 83100}


def test_filtrar_bloqueio_por_etapa():
    pedidos = sp.normalizar(_lote(), hoje=HOJE)
    assert len(sp.filtrar_bloqueio(pedidos, "financeiro")) == 3
    assert len(sp.filtrar_bloqueio(pedidos, "producao")) == 2
    assert len(sp.filtrar_bloqueio(pedidos, "entrega")) == 1


def test_filtrar_bloqueio_nenhum_e_o_complemento_de_qualquer():
    pedidos = sp.normalizar(_lote(), hoje=HOJE)
    assert (len(sp.filtrar_bloqueio(pedidos, "qualquer"))
            + len(sp.filtrar_bloqueio(pedidos, "nenhum")) == len(pedidos))


def test_filtrar_bloqueio_sem_valor_nao_filtra():
    pedidos = sp.normalizar(_lote(), hoje=HOJE)
    assert len(sp.filtrar_bloqueio(pedidos, None)) == len(pedidos)
    assert len(sp.filtrar_bloqueio(pedidos, "")) == len(pedidos)


def test_filtrar_bloqueio_recusa_valor_fora_do_dominio():
    with pytest.raises(sp.ValidationError):
        sp.filtrar_bloqueio(sp.normalizar(_lote(), hoje=HOJE), "comercial")


def test_resumir_entrega_as_dez_colunas_da_tela_mais_o_alerta():
    pedidos = sp.com_alerta(sp.normalizar(_lote(), hoje=HOJE))
    r = sp.resumir(pedidos)[0]
    assert set(r) == set(sp.CAMPOS_RESUMO)
    assert "valor_total" not in r  # o resumo NAO carrega o payload completo


def test_resumir_mantem_a_chave_mesmo_sem_o_alerta():
    """Campo ausente vira ``None``, nao desaparece: quem le nao distingue os dois casos."""
    r = sp.resumir(sp.normalizar([_row()], hoje=HOJE))[0]
    assert r["alerta_liberacao"] is None
