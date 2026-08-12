"""Testes do pipeline de agregados do dashboard Vendas.

O alvo é o que decide NÚMERO, não o que fala com banco: as funções puras que
transformam o retorno do HANA nas linhas das três tabelas. Os invariantes
sustentam a tela e nenhum deles falha com erro — todos falham com número errado:

1. O consolidado ``__TOTAL__`` é a soma das partes exibidas (vem do mesmo
   retorno, não de uma segunda consulta).
2. Vendedor sem venda no período ganha linha ZERADA — "nenhum pedido hoje" e "o
   dado não chegou" não podem pintar a mesma tela.
3. O placar de vendedores existe só no escopo ``__TOTAL__``; o representante
   alcança o ranking dos **próprios clientes**, e nunca o número dos colegas.
4. Cada escopo (hoje/ontem/mês/mês passado) tem ranking próprio — ranking de um
   dia não se deriva do ranking do mês.
"""

from datetime import date

import pytest

from extract_vendas_bi import (
    TOTAL,
    janelas,
    linhas_kpi,
    linhas_ranking,
    linhas_serie,
    sql_detalhe_recente,
    sql_faturamento_mensal,
    sql_pedidos_mensal,
)

QUANDO = '2026-08-12T10:00:00'
HOJE = date(2026, 8, 12)

# Dois dias de agosto e um de julho, com dois vendedores e três clientes.
DETALHE = [
    {'DIA': '2026-08-12', 'VENDEDOR': 'Clayton', 'CHAVE': 'C1', 'NOME': 'VW', 'VALOR': 100.0, 'QTD': 1},
    {'DIA': '2026-08-11', 'VENDEDOR': 'Clayton', 'CHAVE': 'C1', 'NOME': 'VW', 'VALOR': 300.0, 'QTD': 2},
    {'DIA': '2026-08-11', 'VENDEDOR': 'Robson', 'CHAVE': 'C2', 'NOME': 'MIAMI', 'VALOR': 50.0, 'QTD': 1},
    {'DIA': '2026-07-20', 'VENDEDOR': 'Robson', 'CHAVE': 'C3', 'NOME': 'CUMMINS', 'VALOR': 900.0, 'QTD': 3},
]


class TestJanelas:
    def test_os_quatro_escopos_com_competencia(self):
        j = janelas(HOJE)
        assert set(j) == {'hoje', 'ontem', 'mes_atual', 'mes_passado'}
        assert j['hoje'][:2] == (HOJE, HOJE)
        assert j['ontem'][:2] == (date(2026, 8, 11), date(2026, 8, 11))
        assert j['mes_atual'][:2] == (date(2026, 8, 1), HOJE)
        assert j['mes_passado'][:2] == (date(2026, 7, 1), date(2026, 7, 31))

    def test_virada_de_ano_sai_de_graca(self):
        j = janelas(date(2026, 1, 5))
        assert j['mes_passado'][:2] == (date(2025, 12, 1), date(2025, 12, 31))

    def test_mes_passado_de_marco_pega_fevereiro_inteiro(self):
        # Ano bissexto: 2028 tem 29/02. Aritmética de mês na mão erraria aqui.
        j = janelas(date(2028, 3, 10))
        assert j['mes_passado'][:2] == (date(2028, 2, 1), date(2028, 2, 29))


class TestLinhasSerie:
    def _serie(self):
        return linhas_serie(
            [
                {'ANO': 2026, 'MES': 8, 'VENDEDOR': 'Clayton', 'VALOR': 527389.46, 'QTD': 4},
                {'ANO': 2026, 'MES': 8, 'VENDEDOR': 'Robson', 'VALOR': 329770.84, 'QTD': 5},
            ],
            'pedidos',
            QUANDO,
        )

    def test_total_e_a_soma_das_partes(self):
        agosto = {linha['vendedor']: linha for linha in self._serie()}
        assert agosto[TOTAL]['valor'] == pytest.approx(527389.46 + 329770.84)
        assert agosto[TOTAL]['qtd_pedidos'] == 9

    def test_mes_ou_ano_invalido_e_descartado(self):
        assert linhas_serie([{'ANO': None, 'MES': 8, 'VENDEDOR': 'X'}], 'pedidos', QUANDO) == []

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
        assert next(linha for linha in linhas if linha['vendedor'] == TOTAL)['valor'] == 50


class TestLinhasKpi:
    def _kpis(self):
        return {(k['escopo'], k['vendedor']): k for k in linhas_kpi(DETALHE, HOJE, QUANDO)}

    def test_quatro_escopos_para_cada_vendedor_e_para_o_total(self):
        kpis = linhas_kpi(DETALHE, HOJE, QUANDO)
        for vendedor in (TOTAL, 'Clayton', 'Robson'):
            escopos = {k['escopo'] for k in kpis if k['vendedor'] == vendedor}
            assert escopos == {'hoje', 'ontem', 'mes_atual', 'mes_passado'}

    def test_cada_escopo_soma_a_sua_janela(self):
        k = self._kpis()
        assert k[('hoje', TOTAL)]['valor'] == 100.0
        assert k[('ontem', TOTAL)]['valor'] == 350.0
        assert k[('mes_atual', TOTAL)]['valor'] == 450.0
        assert k[('mes_passado', TOTAL)]['valor'] == 900.0

    def test_total_e_a_soma_dos_vendedores_no_mesmo_escopo(self):
        k = self._kpis()
        assert k[('ontem', 'Clayton')]['valor'] + k[('ontem', 'Robson')]['valor'] == pytest.approx(
            k[('ontem', TOTAL)]['valor']
        )

    def test_vendedor_sem_venda_no_periodo_ganha_linha_zerada(self):
        k = self._kpis()
        assert k[('hoje', 'Robson')]['valor'] == 0.0
        assert k[('hoje', 'Robson')]['qtd_pedidos'] == 0

    def test_competencia_de_cada_escopo(self):
        k = self._kpis()
        assert k[('hoje', TOTAL)]['competencia'] == '2026-08-12'
        assert k[('ontem', TOTAL)]['competencia'] == '2026-08-11'
        assert k[('mes_atual', TOTAL)]['competencia'] == '2026-08-01'
        assert k[('mes_passado', TOTAL)]['competencia'] == '2026-07-01'

    def test_sem_dado_nenhum_ainda_devolve_os_quatro_cartoes_zerados(self):
        kpis = linhas_kpi([], HOJE, QUANDO)
        assert {k['escopo'] for k in kpis} == {'hoje', 'ontem', 'mes_atual', 'mes_passado'}
        assert all(k['valor'] == 0.0 for k in kpis)


class TestLinhasRanking:
    def _ranking(self):
        return linhas_ranking(DETALHE, HOJE, QUANDO)

    def test_um_ranking_por_escopo(self):
        assert {linha['escopo'] for linha in self._ranking()} == {
            'hoje',
            'ontem',
            'mes_atual',
            'mes_passado',
        }

    def test_vendedores_ordenados_e_numerados_no_escopo_do_mes(self):
        vend = [
            linha
            for linha in self._ranking()
            if linha['tipo'] == 'vendedor' and linha['escopo'] == 'mes_atual'
        ]
        assert [linha['nome'] for linha in vend] == ['Clayton', 'Robson']
        assert [linha['posicao'] for linha in vend] == [1, 2]

    def test_escopo_de_um_dia_nao_herda_o_ranking_do_mes(self):
        hoje = [
            linha
            for linha in self._ranking()
            if linha['tipo'] == 'vendedor' and linha['escopo'] == 'hoje'
        ]
        # Só o Clayton vendeu hoje.
        assert [linha['nome'] for linha in hoje] == ['Clayton']

    def test_placar_de_vendedores_so_existe_no_escopo_total(self):
        vend = [linha for linha in self._ranking() if linha['tipo'] == 'vendedor']
        assert {linha['vendedor'] for linha in vend} == {TOTAL}

    def test_representante_alcanca_os_proprios_clientes(self):
        clientes = [
            linha
            for linha in self._ranking()
            if linha['tipo'] == 'cliente' and linha['escopo'] == 'mes_atual'
        ]
        do_clayton = [linha for linha in clientes if linha['vendedor'] == 'Clayton']
        assert [linha['chave'] for linha in do_clayton] == ['C1']
        assert do_clayton[0]['valor'] == 400.0
        assert do_clayton[0]['posicao'] == 1

    def test_e_nao_alcanca_os_clientes_dos_outros(self):
        clientes = [linha for linha in self._ranking() if linha['tipo'] == 'cliente']
        do_clayton = {linha['chave'] for linha in clientes if linha['vendedor'] == 'Clayton'}
        assert 'C2' not in do_clayton and 'C3' not in do_clayton

    def test_total_ve_todos_os_clientes_do_periodo(self):
        clientes = [
            linha
            for linha in self._ranking()
            if linha['tipo'] == 'cliente'
            and linha['escopo'] == 'mes_atual'
            and linha['vendedor'] == TOTAL
        ]
        assert {linha['chave'] for linha in clientes} == {'C1', 'C2'}

    def test_cliente_sem_codigo_e_descartado(self):
        linhas = linhas_ranking(
            [{'DIA': '2026-08-12', 'VENDEDOR': 'X', 'CHAVE': '', 'NOME': 'n', 'VALOR': 9, 'QTD': 1}],
            HOJE,
            QUANDO,
        )
        assert [linha for linha in linhas if linha['tipo'] == 'cliente'] == []

    def test_top_n_corta_a_cauda(self):
        muitos = [
            {
                'DIA': '2026-08-12',
                'VENDEDOR': 'X',
                'CHAVE': f'C{i}',
                'NOME': f'N{i}',
                'VALOR': i,
                'QTD': 1,
            }
            for i in range(1, 40)
        ]
        linhas = linhas_ranking(muitos, HOJE, QUANDO, top_clientes=5)
        do_total = [
            linha
            for linha in linhas
            if linha['tipo'] == 'cliente'
            and linha['escopo'] == 'hoje'
            and linha['vendedor'] == TOTAL
        ]
        assert len(do_total) == 5
        assert do_total[0]['chave'] == 'C39'

    def test_chave_natural_nao_colide_entre_escopos(self):
        chaves = [
            (linha['escopo'], linha['tipo'], linha['vendedor'], linha['chave'])
            for linha in self._ranking()
        ]
        assert len(chaves) == len(set(chaves))


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

    def test_detalhe_traz_dia_vendedor_e_cliente_numa_consulta_so(self):
        sql = sql_detalhe_recente('SBOALTAMIRAPROD', date(2026, 7, 1), date(2026, 8, 12))
        for coluna in ('"CodVend"', '"CardCode"', 'TO_VARCHAR("DATA"'):
            assert coluna in sql

    def test_intervalo_do_detalhe_e_meio_aberto_e_inclui_hoje_inteiro(self):
        sql = sql_detalhe_recente('SBOALTAMIRAPROD', date(2026, 7, 1), date(2026, 8, 12))
        assert "'2026-07-01 00:00:00'" in sql
        # Fim exclusivo no dia SEGUINTE: sem isso, pedido das 14h de hoje sumia.
        assert "'2026-08-13 00:00:00'" in sql

    def test_schema_invalido_e_recusado_antes_de_virar_consulta(self):
        with pytest.raises(ValueError):
            sql_pedidos_mensal('SBO"; DROP TABLE x; --', 2024)
        with pytest.raises(ValueError):
            sql_detalhe_recente('SBO OUTRO', date(2026, 8, 1), date(2026, 8, 2))
