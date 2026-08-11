"""Testes do pipeline de agregados do dashboard Vendas.

O alvo é o que decide NÚMERO, não o que fala com banco: as funções puras que
transformam o retorno do HANA nas linhas das três tabelas. Três invariantes
sustentam a tela e nenhum deles falha com erro — todos falham com número errado:

1. O consolidado ``__TOTAL__`` é a soma das partes exibidas (vem do mesmo
   retorno, não de uma segunda consulta).
2. Vendedor sem venda no dia ganha linha ZERADA — "nenhum pedido hoje" e "o dado
   não chegou" não podem pintar a mesma tela.
3. Ranking de cliente existe só no escopo ``__TOTAL__`` — representante não vê a
   carteira dos outros.
"""

from datetime import date

import pytest

from extract_vendas_bi import (
    TOTAL,
    linhas_kpi,
    linhas_ranking,
    linhas_serie,
    sql_faturamento_mensal,
    sql_pedidos_dia,
    sql_pedidos_mensal,
    sql_ranking_clientes,
)

QUANDO = '2026-08-11T17:00:00'
HOJE = date(2026, 8, 11)


def _serie_pedidos():
    return linhas_serie(
        [
            {'ANO': 2026, 'MES': 8, 'VENDEDOR': 'Clayton Capelatto', 'VALOR': 527389.46, 'QTD': 4},
            {'ANO': 2026, 'MES': 8, 'VENDEDOR': 'Robson', 'VALOR': 329770.84, 'QTD': 5},
            {'ANO': 2026, 'MES': 7, 'VENDEDOR': 'Clayton Capelatto', 'VALOR': 1000.0, 'QTD': 1},
        ],
        'pedidos',
        QUANDO,
    )


class TestLinhasSerie:
    def test_total_e_a_soma_das_partes(self):
        linhas = _serie_pedidos()
        agosto = {linha['vendedor']: linha for linha in linhas if linha['mes'] == 8}
        assert agosto[TOTAL]['valor'] == pytest.approx(527389.46 + 329770.84)
        assert agosto[TOTAL]['qtd_pedidos'] == 9

    def test_uma_linha_por_vendedor_e_mes(self):
        linhas = _serie_pedidos()
        chaves = {(linha['vendedor'], linha['ano'], linha['mes']) for linha in linhas}
        assert len(chaves) == len(linhas)

    def test_mes_ou_ano_invalido_e_descartado(self):
        linhas = linhas_serie(
            [{'ANO': None, 'MES': 8, 'VENDEDOR': 'X', 'VALOR': 1, 'QTD': 1}], 'pedidos', QUANDO
        )
        assert linhas == []

    def test_valor_nulo_do_hana_vira_zero_e_nao_explode(self):
        linhas = linhas_serie(
            [{'ANO': 2026, 'MES': 1, 'VENDEDOR': 'X', 'VALOR': None, 'QTD': None}],
            'faturamento',
            QUANDO,
        )
        assert [linha['valor'] for linha in linhas] == [0.0, 0.0]

    def test_vendedor_ausente_nao_some_do_total(self):
        linhas = linhas_serie(
            [{'ANO': 2026, 'MES': 1, 'VENDEDOR': None, 'VALOR': 50, 'QTD': 1}], 'pedidos', QUANDO
        )
        total = next(linha for linha in linhas if linha['vendedor'] == TOTAL)
        assert total['valor'] == 50


class TestLinhasKpi:
    def _kpis(self):
        diarios = [
            {'DIA': '2026-08-11', 'VENDEDOR': 'Clayton Capelatto', 'VALOR': 18909.66, 'QTD': 3},
            {'DIA': '2026-08-10', 'VENDEDOR': 'Robson', 'VALOR': 101300.82, 'QTD': 9},
        ]
        return linhas_kpi(diarios, _serie_pedidos(), HOJE, QUANDO)

    def test_quatro_escopos_por_vendedor(self):
        kpis = self._kpis()
        vendedores = {linha['vendedor'] for linha in kpis}
        assert TOTAL in vendedores
        for v in vendedores:
            escopos = {linha['escopo'] for linha in kpis if linha['vendedor'] == v}
            assert escopos == {'hoje', 'ontem', 'mes_atual', 'mes_passado'}

    def test_hoje_e_ontem_saem_do_retorno_diario(self):
        kpis = {(k['escopo'], k['vendedor']): k for k in self._kpis()}
        assert kpis[('hoje', TOTAL)]['valor'] == pytest.approx(18909.66)
        assert kpis[('ontem', TOTAL)]['valor'] == pytest.approx(101300.82)

    def test_mes_atual_vem_da_serie_e_bate_com_o_power_bi(self):
        kpis = {(k['escopo'], k['vendedor']): k for k in self._kpis()}
        assert kpis[('mes_atual', TOTAL)]['valor'] == pytest.approx(857160.30)

    def test_vendedor_sem_venda_no_dia_ganha_linha_zerada(self):
        kpis = {(k['escopo'], k['vendedor']): k for k in self._kpis()}
        # Robson vendeu ontem, não hoje: a linha de hoje precisa existir zerada.
        assert kpis[('hoje', 'Robson')]['valor'] == 0.0
        assert kpis[('hoje', 'Robson')]['qtd_pedidos'] == 0

    def test_competencia_de_mes_passado_vira_o_mes_anterior(self):
        kpis = {(k['escopo'], k['vendedor']): k for k in self._kpis()}
        assert kpis[('mes_passado', TOTAL)]['competencia'] == '2026-07-01'

    def test_virada_de_ano_no_mes_passado(self):
        kpis = linhas_kpi([], [], date(2026, 1, 5), QUANDO)
        passado = [k for k in kpis if k['escopo'] == 'mes_passado']
        assert passado and passado[0]['competencia'] == '2025-12-01'


class TestLinhasRanking:
    def _ranking(self):
        clientes = [
            {'CHAVE': 'C001', 'NOME': 'VOLKSWAGEN', 'VALOR': 478873.42},
            {'CHAVE': 'C002', 'NOME': 'MIAMI', 'VALOR': 250009.99},
            {'CHAVE': '', 'NOME': 'sem código', 'VALOR': 999999.0},
        ]
        return linhas_ranking(_serie_pedidos(), clientes, HOJE, QUANDO)

    def test_vendedores_ordenados_e_numerados(self):
        vend = [linha for linha in self._ranking() if linha['tipo'] == 'vendedor']
        assert [linha['nome'] for linha in vend] == ['Clayton Capelatto', 'Robson']
        assert [linha['posicao'] for linha in vend] == [1, 2]

    def test_total_nao_entra_como_concorrente_do_ranking(self):
        vend = [linha for linha in self._ranking() if linha['tipo'] == 'vendedor']
        assert TOTAL not in {linha['chave'] for linha in vend}

    def test_ranking_so_do_mes_pedido(self):
        vend = [linha for linha in self._ranking() if linha['tipo'] == 'vendedor']
        # Julho existe na série e não pode contaminar o ranking de agosto.
        assert all(linha['competencia'] == '2026-08-01' for linha in vend)
        assert next(linha for linha in vend if linha['nome'] == 'Clayton Capelatto')[
            'valor'
        ] == pytest.approx(527389.46)

    def test_cliente_existe_so_no_escopo_total(self):
        clientes = [linha for linha in self._ranking() if linha['tipo'] == 'cliente']
        assert clientes and all(linha['vendedor'] == TOTAL for linha in clientes)

    def test_cliente_sem_codigo_e_descartado(self):
        clientes = [linha for linha in self._ranking() if linha['tipo'] == 'cliente']
        assert [linha['chave'] for linha in clientes] == ['C001', 'C002']

    def test_top_n_corta_a_cauda(self):
        muitos = [{'CHAVE': f'C{i}', 'NOME': f'N{i}', 'VALOR': i} for i in range(1, 40)]
        linhas = linhas_ranking(_serie_pedidos(), muitos, HOJE, QUANDO, top_clientes=5)
        clientes = [linha for linha in linhas if linha['tipo'] == 'cliente']
        assert len(clientes) == 5
        assert clientes[0]['chave'] == 'C39'


class TestSql:
    """O SQL não é testado contra o HANA — só contra o que ele PROMETE."""

    def test_medida_de_pedidos_e_o_bruto_sem_indice(self):
        sql = sql_pedidos_mensal('SBOALTAMIRAPROD', 2024)
        assert 'SUM("VlrPedido")' in sql
        assert 'Indice_Pedido' not in sql

    def test_medida_de_faturamento_e_valor_puro(self):
        sql = sql_faturamento_mensal('SBOALTAMIRAPROD', 2024)
        assert 'SUM(f."Valor")' in sql
        assert 'ValorAdiant' not in sql

    def test_faturamento_casa_vendedor_por_codigo_da_OSLP_e_devolve_nome(self):
        sql = sql_faturamento_mensal('SBOALTAMIRAPROD', 2024)
        assert 'LEFT JOIN' in sql and '"OSLP"' in sql
        assert 'v."SlpName"' in sql

    def test_intervalo_do_dia_e_meio_aberto_e_inclui_hoje_inteiro(self):
        sql = sql_pedidos_dia('SBOALTAMIRAPROD', date(2026, 8, 10), date(2026, 8, 11))
        assert "'2026-08-10 00:00:00'" in sql
        # Fim exclusivo no dia SEGUINTE: sem isso, pedido das 14h de hoje sumia.
        assert "'2026-08-12 00:00:00'" in sql

    def test_schema_invalido_e_recusado_antes_de_virar_consulta(self):
        with pytest.raises(ValueError):
            sql_pedidos_mensal('SBO"; DROP TABLE x; --', 2024)
        with pytest.raises(ValueError):
            sql_ranking_clientes('SBO OUTRO', date(2026, 8, 1))
