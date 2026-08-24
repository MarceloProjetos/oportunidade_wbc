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
    sql_orcamentos_mensal,
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

    def test_uf_agrega_todos_os_clientes_e_so_no_total(self):
        # 3 clientes de SP + 1 sem UF; top_clientes=2 CORTA a lista de clientes,
        # mas o agregado por UF cobre TODOS — é a razão de ele existir.
        detalhe = [
            {'DIA': '2026-08-12', 'VENDEDOR': 'Clayton', 'CHAVE': 'C1', 'NOME': 'A',
             'UF': 'SP', 'VALOR': 500.0, 'QTD': 1},
            {'DIA': '2026-08-12', 'VENDEDOR': 'Clayton', 'CHAVE': 'C2', 'NOME': 'B',
             'UF': 'sp', 'VALOR': 300.0, 'QTD': 1},
            {'DIA': '2026-08-12', 'VENDEDOR': 'Robson', 'CHAVE': 'C3', 'NOME': 'C',
             'UF': 'SP', 'VALOR': 100.0, 'QTD': 1},
            {'DIA': '2026-08-12', 'VENDEDOR': 'Robson', 'CHAVE': 'C4', 'NOME': 'D',
             'UF': None, 'VALOR': 50.0, 'QTD': 1},
            {'DIA': '2026-08-12', 'VENDEDOR': 'Robson', 'CHAVE': 'C5', 'NOME': 'E',
             'UF': 'PR', 'VALOR': 700.0, 'QTD': 1},
        ]
        # Estrangeiros ('001' da Guiana e 'GY') somam num EX único — State1
        # fora das 27 UFs não pode virar "estado" na tela (visto em produção).
        detalhe.append({'DIA': '2026-08-12', 'VENDEDOR': 'Robson', 'CHAVE': 'C6',
                        'NOME': 'GMIN', 'UF': '001', 'VALOR': 40.0, 'QTD': 1})
        detalhe.append({'DIA': '2026-08-12', 'VENDEDOR': 'Robson', 'CHAVE': 'C7',
                        'NOME': 'GY CO', 'UF': 'GY', 'VALOR': 20.0, 'QTD': 1})
        linhas = linhas_ranking(detalhe, HOJE, QUANDO, top_clientes=2)
        ufs = [x for x in linhas if x['tipo'] == 'uf' and x['escopo'] == 'hoje']
        # Ordenadas por valor, caixa normalizada, EX p/ estrangeiro, ND sem UF.
        assert [(x['chave'], x['valor'], x['posicao']) for x in ufs] == [
            ('SP', 900.0, 1), ('PR', 700.0, 2), ('EX', 60.0, 3), ('ND', 50.0, 4),
        ]
        # Visibilidade: só __TOTAL__ (o mobile ignora o tipo; o representante
        # não alcança pela RLS).
        assert {x['vendedor'] for x in ufs} == {TOTAL}
        # E a soma das UFs = o total do período (nenhum cliente fica de fora).
        assert sum(x['valor'] for x in ufs) == 1710.0

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


class TestPoda:
    """A poda do que a execução não reescreveu.

    A chave natural de `bi_vendas_kpi` e `bi_vendas_ranking` **não tem data**,
    então sem poda a linha de um período antigo fica para sempre: o ranking de
    "hoje" amanhece com os clientes de ontem enquanto o cartão em cima mostra
    R$ 0,00. Foi um achado real da auditoria de 12/08/2026.

    O critério é o carimbo da execução — exato e de uma chamada por tabela.

    A série mensal tem o furo INVERSO (achado da revisão de 19/08/2026): a chave
    tem ano e mês, então dentro da janela não há órfã — mas o upsert só alcança
    ``ano >= ano_inicial``, e o ano que sai da janela na virada ninguém reescreve
    nem apaga. A poda dela é por ano, e nunca por carimbo: consulta HANA que
    falha vira lista vazia, e o carimbo apagaria a métrica inteira que a
    execução não conseguiu ler.
    """

    ANO_INICIAL = 2024

    class LoaderFake:
        def __init__(self, falhar=False, falhar_serie=False):
            self.podas = []
            self.podas_por_ano = []
            self.falhar = falhar
            self.falhar_serie = falhar_serie

        def delete_nao_carimbadas(self, tabela, coluna, carimbo):
            self.podas.append((tabela, coluna, carimbo))
            return not self.falhar

        def delete_menor_que(self, tabela, coluna, limite):
            self.podas_por_ano.append((tabela, coluna, limite))
            return not (self.falhar or self.falhar_serie)

    def test_poda_por_carimbo_so_nas_tabelas_de_chave_sem_data(self):
        from extract_vendas_bi import TABELA_KPI, TABELA_RANKING, TABELA_SERIE, _podar

        loader = self.LoaderFake()
        assert _podar(loader, QUANDO, self.ANO_INICIAL) is True
        tabelas = [p[0] for p in loader.podas]
        assert tabelas == [TABELA_KPI, TABELA_RANKING]
        # A série NUNCA entra na poda por carimbo: consulta HANA que falha vira
        # lista vazia, e o carimbo apagaria a métrica inteira que não foi lida.
        assert TABELA_SERIE not in tabelas

    def test_o_criterio_e_o_carimbo_desta_execucao(self):
        from extract_vendas_bi import _podar

        loader = self.LoaderFake()
        _podar(loader, QUANDO, self.ANO_INICIAL)
        assert all(p[1] == 'atualizado_em' and p[2] == QUANDO for p in loader.podas)

    def test_serie_poda_os_anos_atras_da_janela_de_historico(self):
        # Sem isto, em 01/01/2027 as linhas de 2024 congelam na tabela para
        # sempre — o upsert só reescreve `ano >= ano_inicial` e nenhum DELETE
        # as alcançava. Cresce um ano a cada virada.
        from extract_vendas_bi import TABELA_SERIE, _podar

        loader = self.LoaderFake()
        assert _podar(loader, QUANDO, self.ANO_INICIAL) is True
        assert loader.podas_por_ano == [(TABELA_SERIE, 'ano', self.ANO_INICIAL)]

    def test_falha_na_poda_derruba_o_resultado(self):
        from extract_vendas_bi import _podar

        assert _podar(self.LoaderFake(falhar=True), QUANDO, self.ANO_INICIAL) is False

    def test_falha_na_poda_da_serie_tambem_derruba_o_resultado(self):
        from extract_vendas_bi import _podar

        loader = self.LoaderFake(falhar_serie=True)
        assert _podar(loader, QUANDO, self.ANO_INICIAL) is False


class TestAnoInicial:
    """A mesma conta serve à consulta e à poda — se divergirem, ou a poda come
    dado vivo, ou o lixo volta a acumular."""

    def test_janela_de_tres_anos_inclui_o_corrente(self):
        from extract_vendas_bi import ano_inicial

        assert ano_inicial(date(2026, 8, 19)) == 2024

    def test_na_virada_do_ano_a_janela_roda_e_2024_fica_atras_dela(self):
        # O cenário exato do achado: em 01/01/2027 a janela vira 2025-2027 e a
        # poda tem de alcançar tudo com ano < 2025.
        from extract_vendas_bi import ano_inicial

        assert ano_inicial(date(2027, 1, 1)) == 2025

    def test_todas_as_linhas_de_uma_execucao_levam_o_mesmo_carimbo(self):
        # É isso que torna "diferente do meu carimbo" equivalente a "resto".
        carimbos = {linha['atualizado_em'] for linha in linhas_kpi(DETALHE, HOJE, QUANDO)}
        carimbos |= {linha['atualizado_em'] for linha in linhas_ranking(DETALHE, HOJE, QUANDO)}
        assert carimbos == {QUANDO}


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

    def test_faturamento_conta_NOTA_e_nao_item(self):
        # A view tem grão de item (julho: 172 linhas para 87 notas); COUNT(*)
        # gravaria o dobro em `qtd_pedidos`.
        assert 'COUNT(DISTINCT f."DOC")' in sql_faturamento_mensal('SBOALTAMIRAPROD', 2024)

    def test_as_series_tem_teto_de_data(self):
        # Pedido digitado com ano futuro escorregaria a régua do gráfico
        # inteiro, porque o app deriva os anos do próprio dado.
        assert 'ADD_DAYS(CURRENT_DATE, 1)' in sql_pedidos_mensal('SBOALTAMIRAPROD', 2024)
        assert 'ADD_DAYS(CURRENT_DATE, 1)' in sql_faturamento_mensal('SBOALTAMIRAPROD', 2024)

    def test_faturamento_casa_vendedor_por_codigo_da_OSLP_e_devolve_nome(self):
        sql = sql_faturamento_mensal('SBOALTAMIRAPROD', 2024)
        assert 'LEFT JOIN' in sql and '"OSLP"' in sql
        assert 'v."SlpName"' in sql

    def test_detalhe_traz_dia_vendedor_e_cliente_numa_consulta_so(self):
        sql = sql_detalhe_recente('SBOALTAMIRAPROD', date(2026, 7, 1), date(2026, 8, 12))
        for coluna in ('p."CodVend"', 'p."CardCode"', 'TO_VARCHAR(p."DATA"'):
            assert coluna in sql
        # A UF vem do cadastro do parceiro (OCRD.State1), por LEFT JOIN — o
        # mesmo campo que alimenta o espelho sap_clientes que o web usa.
        assert 'LEFT JOIN' in sql and '"OCRD"' in sql and '"State1"' in sql

    def test_intervalo_do_detalhe_e_meio_aberto_e_inclui_hoje_inteiro(self):
        sql = sql_detalhe_recente('SBOALTAMIRAPROD', date(2026, 7, 1), date(2026, 8, 12))
        assert "'2026-07-01 00:00:00'" in sql
        # Fim exclusivo no dia SEGUINTE: sem isso, pedido das 14h de hoje sumia.
        assert "'2026-08-13 00:00:00'" in sql

    def test_orcamentos_vem_da_view_de_cotacoes_por_data_da_cotacao(self):
        sql = sql_orcamentos_mensal('SBOALTAMIRAPROD', 2024)
        assert '"DataCotacao"' in sql and '"Representante"' in sql
        assert 'YEAR("DataCotacao") >= 2024' in sql
        # Mesmo teto do resto da serie: cotacao digitada com ano errado nao
        # pode escorregar o eixo do grafico. Nas DUAS pernas da uniao.
        assert sql.count('ADD_DAYS(CURRENT_DATE, 1)') == 2

    def test_orcamentos_une_as_duas_views_porque_cada_uma_tem_o_que_falta(self):
        # Medido em 24/08/2026, janela de 3 anos: a VW_ORCAMENTO_ALT nao tem
        # NENHUMA cotacao cancelada (+607, +7,4% faltando no grafico), e a
        # VW_EVOL_ORCAMENTO_ALT nao tem 25 cotacoes antigas do "Administracao".
        # Trocar uma pela outra apagaria historico dos dois jeitos; a uniao
        # preserva as duas pontas (medido: zero meses perdem cotacao).
        sql = sql_orcamentos_mensal('SBOALTAMIRAPROD', 2024)
        assert '"VW_EVOL_ORCAMENTO_ALT"' in sql
        assert '"VW_ORCAMENTO_ALT"' in sql
        assert 'UNION ALL' in sql

    def test_orcamentos_deduplica_por_cotacao(self):
        # As DUAS views repetem a mesma cotacao quando ela muda de status (64097
        # aparece com 40 e com 60; 5151 com 0 e com 60). Sem o grupo interno por
        # COTACAO, o COUNT(*) conta a cotacao duas vezes e o SUM soma o valor em
        # dobro -- erro que ja existia antes da uniao e que so ficou visivel
        # quando o eixo do grafico virou QUANTIDADE (web V117.834).
        sql = sql_orcamentos_mensal('SBOALTAMIRAPROD', 2024)
        assert 'GROUP BY ANO, MES, VENDEDOR, COTACAO' in sql
        assert 'MAX(VALOR)' in sql
        # E o de fora conta LINHAS do grupo deduplicado, nao linhas da view.
        assert 'COUNT(*) AS QTD' in sql and 'GROUP BY ANO, MES, VENDEDOR' in sql

    def test_schema_invalido_e_recusado_antes_de_virar_consulta(self):
        with pytest.raises(ValueError):
            sql_pedidos_mensal('SBO"; DROP TABLE x; --', 2024)
        with pytest.raises(ValueError):
            sql_detalhe_recente('SBO OUTRO', date(2026, 8, 1), date(2026, 8, 2))


class TestRegistroDaExecucao:
    """O desfecho da rotina vai para `rotinas_execucao` — com o NOME do que falhou.

    Nasceu do incidente de 21/08/2026: o CHECK de `metrica` em produção não
    aceitava `'orcamentos'`, o upsert do lote com essas linhas era recusado, `ok`
    virava False e a poda nunca rodava — o ranking de "hoje" amanheceu com os
    clientes de ontem. A rotina devolvia `False` e **ninguém lia esse False**:
    passou 20 horas assim, e quem percebeu foi um selo amarelo na tela do celular.

    As 9 rotinas do `.90` já gravavam nesta tabela; nenhuma da `.11` gravava.
    """

    class LoaderFake:
        def __init__(self, falhar_upsert_em=None):
            self.falhar_upsert_em = falhar_upsert_em
            self.registros = []
            self.podas = []

        def upsert_data(self, tabela, linhas, on_conflict=None):
            return tabela != self.falhar_upsert_em

        def delete_nao_carimbadas(self, tabela, coluna, carimbo):
            self.podas.append(tabela)
            return True

        def delete_menor_que(self, tabela, coluna, limite):
            self.podas.append(tabela)
            return True

        def registrar_rotina(self, nome, rotulo, *, inicio, fim, sucesso, erro=None):
            self.registros.append(
                {'nome': nome, 'rotulo': rotulo, 'sucesso': sucesso, 'erro': erro}
            )
            return True

    def _rodar(self, monkeypatch, loader, ok_carga=True):
        """Roda `main` com a carga trocada por um retorno controlado."""
        import extract_vendas_bi as mod

        monkeypatch.setattr(mod, '_preparar', lambda falhas: loader)
        monkeypatch.setattr(mod, '_carga', lambda ld, hoje, falhas: ok_carga)
        return mod.main()

    def test_sucesso_registra_sucesso(self, monkeypatch):
        loader = self.LoaderFake()
        assert self._rodar(monkeypatch, loader) is True
        assert loader.registros == [
            {
                'nome': 'VENDAS_BI',
                'rotulo': 'Agregados do dashboard Vendas',
                'sucesso': True,
                'erro': None,
            }
        ]

    def test_falha_da_carga_registra_erro(self, monkeypatch):
        loader = self.LoaderFake()
        assert self._rodar(monkeypatch, loader, ok_carga=False) is False
        assert loader.registros[0]['sucesso'] is False

    def test_excecao_no_meio_da_carga_e_registrada_E_relancada(self, monkeypatch):
        """O caso mais provável de todos: a consulta ao HANA estoura no meio.

        Guarda contra a versão anterior deste código, em que o loader nascia
        DENTRO de `_carga` — a exceção o levava junto e a rotina morria sem
        gravar desfecho nenhum, exatamente o silêncio que este registro existe
        para acabar.
        """
        import extract_vendas_bi as mod

        loader = self.LoaderFake()

        def explode(ld, hoje, falhas):
            raise RuntimeError('HANA caiu')

        monkeypatch.setattr(mod, '_preparar', lambda falhas: loader)
        monkeypatch.setattr(mod, '_carga', explode)

        # Relançar é obrigatório: o agendador só sabe que deu errado pelo código
        # de saída do processo.
        with pytest.raises(RuntimeError):
            mod.main()

        assert loader.registros[0]['sucesso'] is False
        assert 'HANA caiu' in loader.registros[0]['erro']

    def test_sem_credencial_nao_ha_com_quem_registrar(self, monkeypatch):
        import extract_vendas_bi as mod

        monkeypatch.setattr(mod, '_preparar', lambda falhas: None)
        assert mod.main() is False  # e não explode por falta de loader

    def test_a_falha_diz_QUAL_tabela_e_que_a_poda_nao_rodou(self, monkeypatch):
        """Reproduz o incidente: o upsert da série recusado, e o desfecho contando.

        É este texto que teria apontado para `bi_vendas_serie_mensal` em 20/08 em
        vez de a gente descobrir um dia depois pelo carimbo das linhas.
        """
        import extract_vendas_bi as mod

        loader = self.LoaderFake(falhar_upsert_em=mod.TABELA_SERIE)
        monkeypatch.setattr(mod, 'SupabaseLoader', lambda *a, **k: loader)
        monkeypatch.setattr(mod, 'SAPExtractor', lambda *a, **k: _ExtratorFake())
        monkeypatch.setattr(
            mod,
            'montar_payload',
            lambda ex, schema, hoje: {
                mod.TABELA_SERIE: [{'x': 1}],
                mod.TABELA_KPI: [{'atualizado_em': QUANDO}],
                mod.TABELA_RANKING: [{'x': 1}],
            },
        )
        monkeypatch.setattr(mod, 'get_settings', lambda: _SettingsFake())

        assert mod.main() is False

        erro = loader.registros[0]['erro']
        assert loader.registros[0]['sucesso'] is False
        assert 'bi_vendas_serie_mensal' in erro
        # A poda PULADA é metade da história: é ela que deixa o ranking de "hoje"
        # com cliente de ontem. O desfecho tem de dizer as duas coisas.
        assert 'poda não executada' in erro
        assert loader.podas == []

    def test_carga_boa_poda_e_registra_sucesso(self, monkeypatch):
        import extract_vendas_bi as mod

        loader = self.LoaderFake()
        monkeypatch.setattr(mod, 'SupabaseLoader', lambda *a, **k: loader)
        monkeypatch.setattr(mod, 'SAPExtractor', lambda *a, **k: _ExtratorFake())
        monkeypatch.setattr(
            mod,
            'montar_payload',
            lambda ex, schema, hoje: {
                mod.TABELA_SERIE: [{'x': 1}],
                mod.TABELA_KPI: [{'atualizado_em': QUANDO}],
                mod.TABELA_RANKING: [{'x': 1}],
            },
        )
        monkeypatch.setattr(mod, 'get_settings', lambda: _SettingsFake())

        assert mod.main() is True
        assert loader.registros[0]['sucesso'] is True
        assert loader.registros[0]['erro'] is None
        assert loader.podas  # a poda rodou


class _ExtratorFake:
    def connect(self):
        return True

    def close(self):
        pass


class _SettingsFake:
    sap_host = 'h'
    sap_port = 30015
    sap_user = 'u'
    sap_password = 'p'
    sap_database = 'd'
    sap_schema = 'S'
    supabase_url = 'https://x.supabase.co'
    supabase_write_key = 'k'

    def sap_ready(self):
        return True

    def supabase_ready(self):
        return True
