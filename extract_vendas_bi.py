"""ETL: SAP HANA → agregados do dashboard "Vendas" no Supabase.

Origem
------
- ``VW_PEDIDO_ALTA``       → Pedidos. A medida é ``SUM("VlrPedido")``.
- ``VW_FATO_FATURAMENTO``  → Faturamento. A medida é ``SUM("Valor")``.

As duas foram **conferidas contra o Power BI em 11/08/2026**, não deduzidas:
agosto/2026 fecha em R$ 1.314.876,11 com Clayton em R$ 527.389,46 pelo bruto, e
o `valorXindice` do modelo do PBI (``VlrPedido * Indice_Pedido``) dá outro número
(R$ 1.309.079,46) — ou seja, o dashboard **não** usa o índice. Trocar a medida
aqui faz a tela do celular divergir da do Power BI sem nenhum erro aparecer.

Desenho
-------
Quatro consultas agregadas no HANA e funções puras montando as linhas das três
tabelas. O que decide valor mora nas funções puras porque é o que dá para testar
sem banco (``tests/test_extract_vendas_bi.py``).

Volume total: menos de mil linhas. Nenhuma linha de pedido individual sai do
servidor — ver o cabeçalho de ``sql/bi_vendas.sql``.

⚠️ **Vendedor casa por NOME.** ``VW_PEDIDO_ALTA."CodVend"`` já é o nome;
``VW_FATO_FATURAMENTO."CodVend"`` é o ``SlpCode`` e precisa do join com ``OSLP``.
Do lado do app, ``app_profiles.slp_name`` também é nome — e o ``slp_code`` de lá
**diverge** do ``OSLP.SlpCode`` (Edson 6≠7, Luiz Augusto 12≠9, Robson 47≠11).
Casar por código entrega o número de um vendedor para outro.
"""

from __future__ import annotations

import logging
import sys
from datetime import date, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence

from config import get_settings
from pipeline_core import (
    SupabaseLoader,
    agora_iso,
    validate_sql_identifier,
)
from sap_connection import SAPExtractor

logger = logging.getLogger(__name__)

# Tabelas no Supabase. Fazem parte do contrato com o app — ver
# mobile_orcaview_V3/hooks/vendas/useVendasBI.ts.
TABELA_KPI = 'bi_vendas_kpi'
TABELA_SERIE = 'bi_vendas_serie_mensal'
TABELA_RANKING = 'bi_vendas_ranking'

#: Linha consolidada (todos os vendedores somados).
TOTAL = '__TOTAL__'

#: Quantos anos de histórico alimentam os gráficos (inclui o ano corrente).
ANOS_HISTORICO = 3

#: Quantos clientes entram no ranking do mês.
TOP_CLIENTES = 20


# ---------------------------------------------------------------- SQL (HANA)


def sql_pedidos_mensal(schema: str, ano_inicial: int) -> str:
    """Pedidos por ano/mês/vendedor."""
    validate_sql_identifier(schema)
    return f'''
        SELECT YEAR("DATA") AS ANO, MONTH("DATA") AS MES,
               "CodVend" AS VENDEDOR,
               SUM("VlrPedido") AS VALOR, COUNT(*) AS QTD
          FROM "{schema}"."VW_PEDIDO_ALTA"
         WHERE YEAR("DATA") >= {int(ano_inicial)}
         GROUP BY YEAR("DATA"), MONTH("DATA"), "CodVend"
    '''


def sql_pedidos_dia(schema: str, de: date, ate: date) -> str:
    """Pedidos por dia/vendedor num intervalo fechado — alimenta hoje e ontem."""
    validate_sql_identifier(schema)
    return f'''
        SELECT TO_VARCHAR("DATA", 'YYYY-MM-DD') AS DIA,
               "CodVend" AS VENDEDOR,
               SUM("VlrPedido") AS VALOR, COUNT(*) AS QTD
          FROM "{schema}"."VW_PEDIDO_ALTA"
         WHERE "DATA" >= '{de.isoformat()} 00:00:00'
           AND "DATA" <  '{(ate + timedelta(days=1)).isoformat()} 00:00:00'
         GROUP BY TO_VARCHAR("DATA", 'YYYY-MM-DD'), "CodVend"
    '''


def sql_faturamento_mensal(schema: str, ano_inicial: int) -> str:
    """Faturamento por ano/mês/vendedor.

    O join com ``OSLP`` é o que traduz o ``SlpCode`` da fato em nome — a mesma
    chave que o app usa. ``LEFT JOIN`` de propósito: nota de vendedor
    desconhecido não pode sumir do consolidado.
    """
    validate_sql_identifier(schema)
    return f'''
        SELECT YEAR(f."DATA") AS ANO, MONTH(f."DATA") AS MES,
               COALESCE(v."SlpName", '?') AS VENDEDOR,
               SUM(f."Valor") AS VALOR, COUNT(*) AS QTD
          FROM "{schema}"."VW_FATO_FATURAMENTO" f
          LEFT JOIN "{schema}"."OSLP" v ON v."SlpCode" = f."CodVend"
         WHERE YEAR(f."DATA") >= {int(ano_inicial)}
         GROUP BY YEAR(f."DATA"), MONTH(f."DATA"), COALESCE(v."SlpName", '?')
    '''


def sql_ranking_clientes(schema: str, competencia: date) -> str:
    """Clientes do mês, do maior para o menor."""
    validate_sql_identifier(schema)
    return f'''
        SELECT "CardCode" AS CHAVE, MAX("Cliente") AS NOME, SUM("VlrPedido") AS VALOR
          FROM "{schema}"."VW_PEDIDO_ALTA"
         WHERE YEAR("DATA") = {competencia.year} AND MONTH("DATA") = {competencia.month}
         GROUP BY "CardCode"
         ORDER BY 3 DESC
    '''


# ------------------------------------------------------- montagem das linhas


def _num(v: Any) -> float:
    """Decimal/None do HANA → float com 2 casas. `None` vira 0."""
    if v is None:
        return 0.0
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return 0.0


def _int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def linhas_serie(
    registros: Iterable[Dict[str, Any]], metrica: str, quando: str
) -> List[Dict[str, Any]]:
    """Linhas de ``bi_vendas_serie_mensal``, com o consolidado incluído.

    O ``__TOTAL__`` é somado aqui, e não numa segunda consulta ao HANA, para que
    o consolidado e as partes venham do MESMO retorno: duas consultas separadas
    divergiriam a cada pedido lançado entre uma e outra, e o app mostraria um
    total que não é a soma do que ele exibe.
    """
    saida: Dict[tuple, Dict[str, Any]] = {}
    for r in registros:
        ano, mes = _int(r.get('ANO')), _int(r.get('MES'))
        if not ano or not mes:
            continue
        vendedor = (r.get('VENDEDOR') or '?').strip()
        valor, qtd = _num(r.get('VALOR')), _int(r.get('QTD'))
        for chave_vend in (vendedor, TOTAL):
            k = (metrica, chave_vend, ano, mes)
            linha = saida.setdefault(
                k,
                {
                    'metrica': metrica,
                    'vendedor': chave_vend,
                    'ano': ano,
                    'mes': mes,
                    'valor': 0.0,
                    'qtd_pedidos': 0,
                    'atualizado_em': quando,
                },
            )
            linha['valor'] = round(linha['valor'] + valor, 2)
            linha['qtd_pedidos'] += qtd
    return list(saida.values())


def linhas_kpi(
    diarios: Iterable[Dict[str, Any]],
    serie_pedidos: Sequence[Dict[str, Any]],
    hoje: date,
    quando: str,
) -> List[Dict[str, Any]]:
    """Os quatro cartões, por vendedor e consolidado.

    ``hoje``/``ontem`` saem do retorno diário; ``mes_atual``/``mes_passado``
    saem da série mensal já montada — não de uma consulta nova, pelo mesmo
    motivo do ``__TOTAL__`` em :func:`linhas_serie`.

    Vendedor sem venda no dia **ganha linha zerada**: sem ela o app não
    distingue "nenhum pedido hoje" de "o dado não chegou", e as duas coisas
    pintam a mesma tela vazia.
    """
    ontem = hoje - timedelta(days=1)
    mes_passado = date(hoje.year, hoje.month, 1) - timedelta(days=1)

    por_dia: Dict[tuple, Dict[str, float]] = {}
    vendedores = {TOTAL}
    for r in diarios:
        dia = str(r.get('DIA') or '')
        vendedor = (r.get('VENDEDOR') or '?').strip()
        vendedores.add(vendedor)
        for chave_vend in (vendedor, TOTAL):
            acc = por_dia.setdefault((dia, chave_vend), {'valor': 0.0, 'qtd': 0})
            acc['valor'] = round(acc['valor'] + _num(r.get('VALOR')), 2)
            acc['qtd'] += _int(r.get('QTD'))

    por_mes: Dict[tuple, Dict[str, float]] = {}
    for linha in serie_pedidos:
        if linha['metrica'] != 'pedidos':
            continue
        vendedores.add(linha['vendedor'])
        k = (linha['ano'], linha['mes'], linha['vendedor'])
        por_mes[k] = {'valor': linha['valor'], 'qtd': linha['qtd_pedidos']}

    saida: List[Dict[str, Any]] = []
    for vendedor in sorted(vendedores):
        alvos = (
            ('hoje', hoje, por_dia.get((hoje.isoformat(), vendedor))),
            ('ontem', ontem, por_dia.get((ontem.isoformat(), vendedor))),
            (
                'mes_atual',
                date(hoje.year, hoje.month, 1),
                por_mes.get((hoje.year, hoje.month, vendedor)),
            ),
            (
                'mes_passado',
                date(mes_passado.year, mes_passado.month, 1),
                por_mes.get((mes_passado.year, mes_passado.month, vendedor)),
            ),
        )
        for escopo, competencia, achado in alvos:
            saida.append(
                {
                    'escopo': escopo,
                    'vendedor': vendedor,
                    'competencia': competencia.isoformat(),
                    'valor': _num(achado['valor']) if achado else 0.0,
                    'qtd_pedidos': _int(achado['qtd']) if achado else 0,
                    'atualizado_em': quando,
                }
            )
    return saida


def linhas_ranking(
    serie_pedidos: Sequence[Dict[str, Any]],
    clientes: Iterable[Dict[str, Any]],
    competencia: date,
    quando: str,
    top_clientes: int = TOP_CLIENTES,
) -> List[Dict[str, Any]]:
    """Ranking de vendedores (visível a quem vê tudo) e de clientes (só total).

    O ranking de clientes fica **apenas** no escopo ``__TOTAL__`` porque a
    decisão de 11/08/2026 é que representante não vê a carteira dos outros —
    e o recorte por vendedor exigiria uma consulta por vendedor para um dado
    que ninguém pediu.
    """
    saida: List[Dict[str, Any]] = []

    do_mes = [
        linha
        for linha in serie_pedidos
        if linha['metrica'] == 'pedidos'
        and linha['ano'] == competencia.year
        and linha['mes'] == competencia.month
        and linha['vendedor'] != TOTAL
    ]
    do_mes.sort(key=lambda linha: linha['valor'], reverse=True)
    for i, linha in enumerate(do_mes, start=1):
        saida.append(
            {
                'competencia': competencia.replace(day=1).isoformat(),
                'tipo': 'vendedor',
                'vendedor': TOTAL,
                'chave': linha['vendedor'],
                'nome': linha['vendedor'],
                'valor': linha['valor'],
                'posicao': i,
                'atualizado_em': quando,
            }
        )

    ordenados = sorted(clientes, key=lambda r: _num(r.get('VALOR')), reverse=True)
    for i, r in enumerate(ordenados[:top_clientes], start=1):
        chave = str(r.get('CHAVE') or '').strip()
        if not chave:
            continue
        saida.append(
            {
                'competencia': competencia.replace(day=1).isoformat(),
                'tipo': 'cliente',
                'vendedor': TOTAL,
                'chave': chave,
                'nome': str(r.get('NOME') or chave).strip(),
                'valor': _num(r.get('VALOR')),
                'posicao': i,
                'atualizado_em': quando,
            }
        )
    return saida


# ------------------------------------------------------------------ pipeline


def _consultar(ex: SAPExtractor, sql: str, rotulo: str) -> List[Dict[str, Any]]:
    """Roda a consulta e devolve dicionários. Falha vira lista vazia + log."""
    df = ex.execute_query(sql)
    if df is None:
        logger.error('[VENDAS_BI] consulta %s falhou', rotulo)
        return []
    logger.info('[VENDAS_BI] %s: %s linha(s)', rotulo, len(df))
    return df.to_dict('records')


def montar_payload(ex: SAPExtractor, schema: str, hoje: date) -> Dict[str, List[Dict[str, Any]]]:
    """Consulta o HANA e devolve as linhas prontas das três tabelas."""
    quando = agora_iso()
    ano_inicial = hoje.year - (ANOS_HISTORICO - 1)

    pedidos = _consultar(ex, sql_pedidos_mensal(schema, ano_inicial), 'pedidos mensal')
    faturamento = _consultar(
        ex, sql_faturamento_mensal(schema, ano_inicial), 'faturamento mensal'
    )
    diarios = _consultar(
        ex, sql_pedidos_dia(schema, hoje - timedelta(days=1), hoje), 'pedidos do dia'
    )
    clientes = _consultar(ex, sql_ranking_clientes(schema, hoje), 'ranking clientes')

    serie = linhas_serie(pedidos, 'pedidos', quando) + linhas_serie(
        faturamento, 'faturamento', quando
    )
    return {
        TABELA_SERIE: serie,
        TABELA_KPI: linhas_kpi(diarios, serie, hoje, quando),
        TABELA_RANKING: linhas_ranking(serie, clientes, hoje, quando),
    }


def main(hoje: Optional[date] = None) -> bool:
    """Carga completa dos agregados de Vendas. `True` se tudo entrou."""
    s = get_settings()
    if not s.sap_ready():
        logger.error('[VENDAS_BI] credenciais SAP ausentes')
        return False
    if not s.supabase_ready():
        logger.error('[VENDAS_BI] credenciais Supabase ausentes')
        return False

    hoje = hoje or date.today()
    ex = SAPExtractor(s.sap_host, s.sap_port, s.sap_user, s.sap_password, s.sap_database)
    if not ex.connect():
        logger.error('[VENDAS_BI] não conectou no HANA')
        return False

    try:
        payload = montar_payload(ex, s.sap_schema, hoje)
    finally:
        ex.close()

    loader = SupabaseLoader(s.supabase_url, s.supabase_write_key)
    ok = True
    for tabela, chaves in (
        (TABELA_SERIE, 'metrica,vendedor,ano,mes'),
        (TABELA_KPI, 'escopo,vendedor'),
        (TABELA_RANKING, 'competencia,tipo,vendedor,chave'),
    ):
        linhas = payload[tabela]
        if not linhas:
            logger.warning('[VENDAS_BI] %s: nada a gravar', tabela)
            continue
        ok = loader.upsert_data(tabela, linhas, on_conflict=chaves) and ok
    return ok


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    sys.exit(0 if main() else 1)
