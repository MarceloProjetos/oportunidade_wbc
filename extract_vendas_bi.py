"""ETL: SAP HANA → aggregates of the "Vendas" dashboard in Supabase.

Source
------
- ``VW_PEDIDO_ALTA``       → Pedidos. The measure is ``SUM("VlrPedido")``.
- ``VW_FATO_FATURAMENTO``  → Faturamento. The measure is ``SUM("Valor")``.

⚠️ **From 21/09/2026 Pedidos deliberately does NOT match Power BI.** Both queries over
``VW_PEDIDO_ALTA`` drop the orders in ``pedidos_bloqueados`` -- today only 84337 (NAVARRO,
R$ 93.531,74, 02/09/2026), closed by hand in SAP with no delivery and no invoice.
September/2026 comes out R$ 93.531,74 LOWER than Power BI, and that is the fix, not a
regression. Faturamento does not change: that order never became an invoice. Orders flagged
``ORDR.U_U_INO_NotaEspelho = 'Y'`` (mirror notes, 84534, 26/10/2026) are dropped too, see
:func:`_pedidos_unicos`.

Both measures were **checked against Power BI on 11/08/2026**, not deduced: August/2026
closes at R$ 1.314.876,11 with Clayton at R$ 527.389,46 gross, and the Power BI model's
``valorXindice`` (``VlrPedido * Indice_Pedido``) gives another number (R$ 1.309.079,46) --
so the dashboard does **not** use the index. Changing the measure here makes the phone screen
diverge from Power BI with no error showing up.

Design
------
Four aggregate queries in HANA and pure functions building the rows of the three tables.
What decides a value lives in the pure functions because that is what can be tested without
a database (``tests/test_extract_vendas_bi.py``).

Total volume: under a thousand rows. No individual order row leaves the server -- see the
header of the ``bi_vendas`` DDL (it is not in this repo's ``sql/``; the applied migrations are
in ``sql/migracoes/``).

⚠️ **The salesperson matches by NAME.** ``VW_PEDIDO_ALTA."CodVend"`` is already the name;
``VW_FATO_FATURAMENTO."CodVend"`` is the ``SlpCode`` and needs the join with ``OSLP``. On the
app side ``app_profiles.slp_name`` is a name too. Until 21/09/2026 the app's ``slp_code`` held
the representative code (``OSLP.U_RepCod``/``Memo``: Edson 6, Luiz Augusto 12, Robson 47)
instead of ``OSLP.SlpCode`` (7, 9, 11); that was fixed, but matching by code would still hand
one salesperson's numbers to another on a wrong profile. Name is the safe key.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from typing import Any

from config import get_settings
from pedidos_bloqueados import sql_nao_bloqueado
from pipeline_core import (
    FileLockTimeout,
    SupabaseLoader,
    agora_iso,
    validate_sql_identifier,
    vendas_bi_sync_lock,
)
from sap_connection import SAPExtractor

logger = logging.getLogger(__name__)

# Tabelas no Supabase. Fazem parte do contrato com o app — ver
# mobile_orcaview_V4/hooks/vendas/useVendasBI.ts.
TABELA_KPI = 'bi_vendas_kpi'
TABELA_SERIE = 'bi_vendas_serie_mensal'
TABELA_RANKING = 'bi_vendas_ranking'

#: Linha consolidada (todos os vendedores somados).
TOTAL = '__TOTAL__'

#: Os quatro recortes de tempo dos cartões. Ordem estável para os testes.
ESCOPOS = ('hoje', 'ontem', 'mes_atual', 'mes_passado')

#: Quantos anos de histórico alimentam os gráficos (inclui o ano corrente).
ANOS_HISTORICO = 3

#: Quantos clientes entram no ranking do mês.
TOP_CLIENTES = 20

#: As 27 UFs — régua do agregado por estado. O State1 do OCRD traz código
#: numérico p/ endereço estrangeiro ('001' na Guiana, visto em produção
#: 20/08): valor fora da lista vira 'EX' (exterior), vazio vira 'ND'.
UFS_BR = frozenset((
    'AC', 'AL', 'AP', 'AM', 'BA', 'CE', 'DF', 'ES', 'GO', 'MA', 'MT', 'MS',
    'MG', 'PA', 'PB', 'PR', 'PE', 'PI', 'RJ', 'RN', 'RS', 'RO', 'RR', 'SC',
    'SP', 'SE', 'TO',
))


def _uf_normalizada(bruta: Any) -> str:
    valor = str(bruta or '').strip().upper()
    if not valor:
        return 'ND'
    return valor if valor in UFS_BR else 'EX'


def ano_inicial(hoje: date) -> int:
    """First year of the history window (the current year counts as one).

    The SAME arithmetic is used to query and to prune: the monthly queries only bring
    ``ano >= ano_inicial``, so anything older left in ``bi_vendas_serie_mensal`` is garbage by
    definition -- no upsert ever touches it again. If the two computations diverged, either the
    prune eats live data or the garbage piles up again.
    """
    return hoje.year - (ANOS_HISTORICO - 1)


# ---------------------------------------------------------------- SQL (HANA)


def _pedidos_unicos(schema: str) -> str:
    """``VW_PEDIDO_ALTA`` with ONE row per ``DOC``, as a derived table.

    ⚠️ The view repeats some orders -- a join fan-out inside it (probed
    28/09/2026): 83891 comes 4× with two different ``AcaoContato``, 81568 4×,
    80628 2× with ``Primeiro_Pedido`` S and N. Summing the raw view counted
    R$ 49.899,33 and 3 orders too many in 2026 (R$ 105.229,56 and 4 in 2024).
    The columns read here are identical across the repeats, so ``MAX`` only picks
    among equal values. Web twin: ``resultados_estado_service.normalizar_pedidos``.

    Orders flagged ``ORDR.U_U_INO_NotaEspelho = 'Y'`` are mirrors of a sale
    already counted (84534, 26/10/2026), so they never enter the sums.
    """
    return f'''(SELECT "DOC", MAX("DATA") AS "DATA", MAX("CodVend") AS "CodVend",
                       MAX("CardCode") AS "CardCode", MAX("Cliente") AS "Cliente",
                       MAX("VlrPedido") AS "VlrPedido"
                  FROM "{schema}"."VW_PEDIDO_ALTA" v
                 WHERE NOT EXISTS (SELECT 1 FROM "{schema}"."ORDR" e
                                    WHERE e."DocNum" = v."DOC"
                                      AND e."U_U_INO_NotaEspelho" = 'Y')
                 GROUP BY "DOC")'''


def sql_pedidos_mensal(schema: str, ano_inicial: int) -> str:
    """Pedidos per year/month/salesperson.

    Date ceiling at ``CURRENT_DATE``: an order typed with a wrong year (2027) would enter the
    series and, since the app derives the chart axis **from the data itself**, the whole axis
    would slip -- 2025-2027, no 2024, with nothing explaining why.
    """
    validate_sql_identifier(schema)
    return f'''
        SELECT YEAR("DATA") AS ANO, MONTH("DATA") AS MES,
               "CodVend" AS VENDEDOR,
               SUM("VlrPedido") AS VALOR, COUNT(*) AS QTD
          FROM {_pedidos_unicos(schema)} u
         WHERE YEAR("DATA") >= {int(ano_inicial)}
           AND "DATA" < ADD_DAYS(CURRENT_DATE, 1)
           {sql_nao_bloqueado('"DOC"', prefixo="AND ")}
         GROUP BY YEAR("DATA"), MONTH("DATA"), "CodVend"
    '''


def sql_detalhe_recente(schema: str, de: date, ate: date) -> str:
    """Pedidos per day × salesperson × customer in the closed interval ``[de, ate]``.

    One query feeds **the four KPIs and the four rankings**, because the smallest window the
    screen needs (day 1 of last month up to today) fits in ~150 aggregated rows. Two separate
    queries -- one for the day, another for the month -- would diverge with every order entered
    between them, and the app would show a KPI that is not the sum of the ranking displayed right
    below it.
    """
    validate_sql_identifier(schema)
    # LEFT JOIN OCRD: a UF do cliente (State1, endereco de cobranca — o mesmo
    # campo que alimenta o espelho sap_clientes que o web usa). LEFT de
    # proposito: cliente sem cadastro de UF nao pode sumir do detalhe.
    return f'''
        SELECT TO_VARCHAR(p."DATA", 'YYYY-MM-DD') AS DIA,
               p."CodVend" AS VENDEDOR,
               p."CardCode" AS CHAVE, MAX(p."Cliente") AS NOME,
               MAX(c."State1") AS UF,
               SUM(p."VlrPedido") AS VALOR, COUNT(*) AS QTD
          FROM {_pedidos_unicos(schema)} p
          LEFT JOIN "{schema}"."OCRD" c ON c."CardCode" = p."CardCode"
         WHERE p."DATA" >= '{de.isoformat()} 00:00:00'
           AND p."DATA" <  '{(ate + timedelta(days=1)).isoformat()} 00:00:00'
           {sql_nao_bloqueado('p."DOC"', prefixo="AND ")}
         GROUP BY TO_VARCHAR(p."DATA", 'YYYY-MM-DD'), p."CodVend", p."CardCode"
    '''


def sql_faturamento_mensal(schema: str, ano_inicial: int) -> str:
    """Faturamento per year/month/salesperson.

    The join with ``OSLP`` is what translates the fact's ``SlpCode`` into a name -- the same key
    the app uses. It is a ``LEFT JOIN`` on purpose: an invoice of an unknown salesperson must not
    vanish from the consolidated figure.
    """
    validate_sql_identifier(schema)
    return f'''
        SELECT YEAR(f."DATA") AS ANO, MONTH(f."DATA") AS MES,
               COALESCE(v."SlpName", '?') AS VENDEDOR,
               SUM(f."Valor") AS VALOR, COUNT(DISTINCT f."DOC") AS QTD
          FROM "{schema}"."VW_FATO_FATURAMENTO" f
          LEFT JOIN "{schema}"."OSLP" v ON v."SlpCode" = f."CodVend"
         WHERE YEAR(f."DATA") >= {int(ano_inicial)}
           AND f."DATA" < ADD_DAYS(CURRENT_DATE, 1)
         GROUP BY YEAR(f."DATA"), MONTH(f."DATA"), COALESCE(v."SlpName", '?')
    '''


def sql_orcamentos_mensal(schema: str, ano_inicial: int) -> str:
    """Quotations (cotações) issued per year/month/salesperson.

    UNION of the two quotation views, deduplicated by ``Cotacao``. Both, and not just one,
    because each has what the other lacks (measured 24/08/2026, 3-year window):

    - ``VW_EVOL_ORCAMENTO_ALT`` carries the **cancelled** ones (``StatusWBC`` 99), which
      ``VW_ORCAMENTO_ALT`` has **none of**: **+607 quotations (+7,4%)** were missing from the
      chart, 4 to 92 per month. A cancelled quotation was still issued (Marcelo's rule, 24/08) --
      and since the axis became QUANTITY (web V117.834) the gap shows directly in the bar;
    - ``VW_ORCAMENTO_ALT`` has **25 old quotations** (all "Administração", low numbering) that
      **do not exist** in the evolution view. Swapping one view for the other would erase them
      from the history; the union keeps both ends -- measured: **zero months lose a quotation**.

    Deduplicating by ``Cotacao`` fixes a third error that existed before this change: **both views
    repeat the same quotation when it changes status** (64097 appears with 40 and with 60; 5151
    with 0 and with 60). The previous ``COUNT(*)`` counted them twice and ``SUM`` doubled the
    value -- the old docstring said "dedupe is not worth it" because only the VALUE mattered; with
    the axis in quantity it is.

    ``MAX(VALOR)`` in the inner group is safe: no quotation has a different value between the
    views (checked, 0 divergences in 3 years).

    ``Representante`` is in the same namespace as ``CodVend`` of VW_PEDIDO_ALTA in BOTH views.
    """
    validate_sql_identifier(schema)
    janela = (
        f'WHERE YEAR("DataCotacao") >= {int(ano_inicial)} '
        f'AND "DataCotacao" < ADD_DAYS(CURRENT_DATE, 1)'
    )
    return f'''
        SELECT ANO, MES, VENDEDOR, SUM(VALOR) AS VALOR, COUNT(*) AS QTD
          FROM (SELECT ANO, MES, VENDEDOR, COTACAO, MAX(VALOR) AS VALOR
                  FROM (SELECT YEAR("DataCotacao") AS ANO, MONTH("DataCotacao") AS MES,
                               "Representante" AS VENDEDOR, "Cotacao" AS COTACAO,
                               "Valor" AS VALOR
                          FROM "{schema}"."VW_EVOL_ORCAMENTO_ALT"
                         {janela}
                         UNION ALL
                        SELECT YEAR("DataCotacao"), MONTH("DataCotacao"),
                               "Representante", "Cotacao", "Valor"
                          FROM "{schema}"."VW_ORCAMENTO_ALT"
                         {janela})
                 GROUP BY ANO, MES, VENDEDOR, COTACAO)
         GROUP BY ANO, MES, VENDEDOR
    '''


def janelas(hoje: date) -> dict[str, tuple[date, date, date]]:
    """Closed interval of each scope plus its competence date: ``(de, ate, competencia)``.

    Last month is computed by stepping one day back from day 1 of the current month -- the year
    rollover comes for free and there is no month-0 arithmetic.
    """
    primeiro = date(hoje.year, hoje.month, 1)
    fim_passado = primeiro - timedelta(days=1)
    inicio_passado = date(fim_passado.year, fim_passado.month, 1)
    ontem = hoje - timedelta(days=1)
    return {
        'hoje': (hoje, hoje, hoje),
        'ontem': (ontem, ontem, ontem),
        'mes_atual': (primeiro, hoje, primeiro),
        'mes_passado': (inicio_passado, fim_passado, inicio_passado),
    }


# ------------------------------------------------------- montagem das linhas


def _num(v: Any) -> float:
    """HANA ``Decimal``/``None`` → float with 2 decimals. ``None`` becomes 0."""
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
    registros: Iterable[dict[str, Any]], metrica: str, quando: str
) -> list[dict[str, Any]]:
    """Rows of ``bi_vendas_serie_mensal``, consolidated row included.

    ``__TOTAL__`` is summed here, not in a second HANA query, so the consolidated figure and its
    parts come from the SAME result: two separate queries would diverge with every order entered
    between them, and the app would show a total that is not the sum of what it displays.
    """
    saida: dict[tuple, dict[str, Any]] = {}
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


def _no_intervalo(dia: str, de: date, ate: date) -> bool:
    """``YYYY-MM-DD`` inside the closed interval. String comparison is enough for ISO dates."""
    return bool(dia) and de.isoformat() <= dia <= ate.isoformat()


def linhas_kpi(
    detalhe: Iterable[dict[str, Any]],
    hoje: date,
    quando: str,
) -> list[dict[str, Any]]:
    """The four cards, per salesperson and consolidated.

    The four scopes come from the **same** daily result (:func:`sql_detalhe_recente`), not from
    separate queries: the "mês atual" card must be exactly the sum of the month ranking shown right
    below it on the screen.

    A salesperson with no sales in the period **gets a zeroed row**: without it the app cannot tell
    "no order today" from "the data did not arrive", and both paint the same empty screen.
    """
    linhas = list(detalhe)
    vendedores = {TOTAL} | {(r.get('VENDEDOR') or '?').strip() for r in linhas}

    saida: list[dict[str, Any]] = []
    for escopo, (de, ate, competencia) in janelas(hoje).items():
        acumulado: dict[str, dict[str, float]] = {
            v: {'valor': 0.0, 'qtd': 0} for v in vendedores
        }
        for r in linhas:
            if not _no_intervalo(str(r.get('DIA') or ''), de, ate):
                continue
            vendedor = (r.get('VENDEDOR') or '?').strip()
            for chave_vend in (vendedor, TOTAL):
                acumulado[chave_vend]['valor'] = round(
                    acumulado[chave_vend]['valor'] + _num(r.get('VALOR')), 2
                )
                acumulado[chave_vend]['qtd'] += _int(r.get('QTD'))
        for vendedor in sorted(vendedores):
            saida.append(
                {
                    'escopo': escopo,
                    'vendedor': vendedor,
                    'competencia': competencia.isoformat(),
                    'valor': acumulado[vendedor]['valor'],
                    'qtd_pedidos': int(acumulado[vendedor]['qtd']),
                    'atualizado_em': quando,
                }
            )
    return saida


def linhas_ranking(
    detalhe: Iterable[dict[str, Any]],
    hoje: date,
    quando: str,
    top_clientes: int = TOP_CLIENTES,
) -> list[dict[str, Any]]:
    """Ranking of salespeople, customers and UFs, **one set per scope**.

    ⚠️ ``tipo='uf'`` requires the widened CHECK on ``bi_vendas_ranking`` (its migration file is not
    in this repo's ``sql/``) -- **the ALTER goes BEFORE this code reaches production**: widening the
    domain does not break the old code, but the new code writing 'uf' against the old CHECK fails the
    whole load.

    The four scopes exist because on the screen the top card became a filter: tapping "ontem" must
    swap the whole ranking, and a one-day ranking cannot be derived from the month ranking.

    Two visibility scopes coexist in the ``vendedor`` column:

    - ``__TOTAL__`` -- company-wide board of salespeople **and** customers. Only admin and diretoria
      reach it (RLS).
    - each salesperson's name -- **only their own customers**. It is what the representative sees:
      without it the ranking card is born empty on their screen, which reads as a broken app. The
      salespeople board stays out of their reach -- it is their colleagues' numbers.
    """
    linhas = list(detalhe)
    saida: list[dict[str, Any]] = []

    for escopo, (de, ate, competencia) in janelas(hoje).items():
        do_periodo = [r for r in linhas if _no_intervalo(str(r.get('DIA') or ''), de, ate)]

        por_vendedor: dict[str, float] = {}
        # (escopo_visibilidade, cardcode) → {nome, valor}
        por_cliente: dict[tuple, dict[str, Any]] = {}
        # UF → valor, sobre TODOS os clientes do período (não o top 20): é o
        # agregado que a tabela "Clientes por UF" do web precisa para o
        # subtotal por estado ser o número inteiro, não o do recorte.
        por_uf: dict[str, float] = {}
        for r in do_periodo:
            vendedor = (r.get('VENDEDOR') or '?').strip()
            valor = _num(r.get('VALOR'))
            por_vendedor[vendedor] = round(por_vendedor.get(vendedor, 0.0) + valor, 2)

            uf = _uf_normalizada(r.get('UF'))
            por_uf[uf] = round(por_uf.get(uf, 0.0) + valor, 2)

            chave = str(r.get('CHAVE') or '').strip()
            if not chave:
                continue
            for visibilidade in (TOTAL, vendedor):
                cliente = por_cliente.setdefault(
                    (visibilidade, chave),
                    {'nome': str(r.get('NOME') or chave).strip(), 'valor': 0.0},
                )
                cliente['valor'] = round(cliente['valor'] + valor, 2)

        ordenados_v = sorted(por_vendedor.items(), key=lambda kv: kv[1], reverse=True)
        for i, (vendedor, valor) in enumerate(ordenados_v, start=1):
            saida.append(
                _linha_ranking(escopo, 'vendedor', TOTAL, vendedor, vendedor, valor, i, competencia, quando)
            )

        # tipo='uf': só visibilidade __TOTAL__ (dado da empresa; o representante
        # não alcança pela RLS). O app mobile filtra tipo em ('vendedor',
        # 'cliente') e IGNORA este — conferido em lib/vendas/montar.ts.
        ordenadas_uf = sorted(por_uf.items(), key=lambda kv: kv[1], reverse=True)
        for i, (uf, valor) in enumerate(ordenadas_uf, start=1):
            saida.append(
                _linha_ranking(escopo, 'uf', TOTAL, uf, uf, valor, i, competencia, quando)
            )

        # Um ranking de clientes por escopo de visibilidade, cada um numerado
        # do 1º ao N — a posição é dentro da lista que aquele usuário vê.
        visibilidades = {v for v, _ in por_cliente}
        for visibilidade in sorted(visibilidades):
            dessa = [(k, d) for (v, k), d in por_cliente.items() if v == visibilidade]
            dessa.sort(key=lambda kv: kv[1]['valor'], reverse=True)
            for i, (chave, dados) in enumerate(dessa[:top_clientes], start=1):
                saida.append(
                    _linha_ranking(
                        escopo,
                        'cliente',
                        visibilidade,
                        chave,
                        dados['nome'],
                        dados['valor'],
                        i,
                        competencia,
                        quando,
                    )
                )
    return saida


def _linha_ranking(
    escopo: str,
    tipo: str,
    visibilidade: str,
    chave: str,
    nome: str,
    valor: float,
    posicao: int,
    competencia: date,
    quando: str,
) -> dict[str, Any]:
    """One ranking row. ``visibilidade`` is who can reach it, not who sold."""
    return {
        'escopo': escopo,
        'competencia': competencia.isoformat(),
        'tipo': tipo,
        'vendedor': visibilidade,
        'chave': chave,
        'nome': nome,
        'valor': valor,
        'posicao': posicao,
        'atualizado_em': quando,
    }


# ------------------------------------------------------------------ pipeline


#: Payload key with the labels of the HANA queries that FAILED (not "came back empty").
CONSULTAS_FALHAS = '_consultas_falhas'


def _consultar(ex: SAPExtractor, sql: str, rotulo: str) -> list[dict[str, Any]] | None:
    """Run the query and return dicts; ``None`` when it FAILED.

    Failure used to come back as an empty list, the same as "no sales" (01/10/2026 review): with the
    detail query down, the load wrote the four zeroed ``__TOTAL__`` cards, pruned every per-seller
    KPI and the whole ranking by stamp, and logged "sucesso" -- the app showed R$ 0,00 for "Hoje"
    until the next load.
    """
    df = ex.execute_query(sql)
    if df is None:
        logger.error('[VENDAS_BI] consulta %s falhou', rotulo)
        return None
    logger.info('[VENDAS_BI] %s: %s linha(s)', rotulo, len(df))
    return df.to_dict('records')


def montar_payload(ex: SAPExtractor, schema: str, hoje: date) -> dict[str, list[dict[str, Any]]]:
    """Query HANA and return the ready rows of the three tables.

    The result also carries ``CONSULTAS_FALHAS``: the labels of the queries that failed. A failed
    detail query leaves the KPI and ranking lists empty (``_carga`` then keeps the previous rows);
    a failed monthly query only adds no series rows.
    """
    quando = agora_iso()
    desde = ano_inicial(hoje)
    falhas: list[str] = []

    def consulta(sql: str, rotulo: str) -> list[dict[str, Any]]:
        linhas = _consultar(ex, sql, rotulo)
        if linhas is None:
            falhas.append(rotulo)
            return []
        return linhas

    pedidos = consulta(sql_pedidos_mensal(schema, desde), 'pedidos mensal')
    faturamento = consulta(sql_faturamento_mensal(schema, desde), 'faturamento mensal')
    # Do 1º dia do mês passado até hoje — a menor janela que cobre os quatro
    # escopos dos cartões, que agora também filtram os rankings.
    inicio = janelas(hoje)['mes_passado'][0]
    detalhe = _consultar(ex, sql_detalhe_recente(schema, inicio, hoje), 'detalhe recente')
    if detalhe is None:
        falhas.append('detalhe recente')

    orcamentos = consulta(sql_orcamentos_mensal(schema, desde), 'orcamentos mensal')
    # A failed metric adds no rows: its months are not "read", so the series prune (by month
    # read, see `_podar_serie_meses_lidos`) leaves them alone.
    serie = (
        linhas_serie(pedidos, 'pedidos', quando)
        + linhas_serie(faturamento, 'faturamento', quando)
        + linhas_serie(orcamentos, 'orcamentos', quando)
    )
    return {
        TABELA_SERIE: serie,
        # No detail → no cards and no ranking: `_carga` keeps the previous load's rows
        # instead of writing zeros over them.
        TABELA_KPI: linhas_kpi(detalhe, hoje, quando) if detalhe is not None else [],
        TABELA_RANKING: linhas_ranking(detalhe, hoje, quando) if detalhe is not None else [],
        CONSULTAS_FALHAS: falhas,
    }


ROTINA_NOME = 'VENDAS_BI'
ROTINA_ROTULO = 'Agregados do dashboard Vendas'


def main(hoje: date | None = None) -> bool:
    """Full load of the Vendas aggregates. ``True`` if everything went in.

    Wrapper around :func:`_carga` that times the run and **records the outcome** in
    ``rotinas_execucao``. The record exists because, until 21/08/2026, this routine had its outcome
    written nowhere: a ``False`` returned from here was read by no one, and a partial failure lasted
    20 hours without lighting anything up.

    Raises:
        Exception: whatever ``_carga`` raises, re-raised after the failure is recorded.
    """
    inicio = datetime.now().astimezone()
    falhas: list[str] = []
    # O loader nasce AQUI, fora do `try`, e não lá dentro: uma consulta que
    # estoura no meio do HANA levaria junto o único objeto capaz de gravar o
    # desfecho, e a falha mais provável de todas terminaria — de novo — calada.
    loader = _preparar(falhas)
    if loader is None:
        return False
    try:
        ok = _carga(loader, hoje, falhas)
    except Exception as e:  # noqa: BLE001 — registra e relança, sem engolir
        falhas.append(f'{type(e).__name__}: {e}')
        _registrar_execucao(loader, inicio, False, falhas)
        raise
    _registrar_execucao(loader, inicio, ok, falhas)
    return ok


def _registrar_execucao(
    loader: SupabaseLoader | None,
    inicio: datetime,
    ok: bool,
    falhas: list[str],
) -> None:
    """Write the outcome, if there is a loader to write with. Never disturbs the load."""
    if loader is None:
        return
    loader.registrar_rotina(
        ROTINA_NOME,
        ROTINA_ROTULO,
        inicio=inicio,
        fim=datetime.now().astimezone(),
        sucesso=ok,
        erro='; '.join(falhas) if falhas else None,
    )


def _preparar(falhas: list[str]) -> SupabaseLoader | None:
    """Check the credentials and return the loader to write with. ``None`` = does not even start.

    Without the Supabase key there is neither a load **nor** a record: the "missing credential"
    outcome exists only in the machine's log.
    """
    s = get_settings()
    if not s.sap_ready():
        logger.error('[VENDAS_BI] credenciais SAP ausentes')
        falhas.append('credenciais SAP ausentes')
        return None
    if not s.supabase_ready():
        logger.error('[VENDAS_BI] credenciais Supabase ausentes')
        falhas.append('credenciais Supabase ausentes')
        return None
    return SupabaseLoader(s.supabase_url, s.supabase_write_key)


def _carga(loader: SupabaseLoader, hoje: date | None, falhas: list[str]) -> bool:
    """The load itself. Appends to ``falhas`` the name of whatever did not go in."""
    s = get_settings()
    hoje = hoje or date.today()
    ex = SAPExtractor(s.sap_host, s.sap_port, s.sap_user, s.sap_password, s.sap_database)
    logger.info('[VENDAS_BI] carga de %s', hoje.isoformat())
    if not ex.connect():
        logger.error('[VENDAS_BI] não conectou no HANA')
        falhas.append('não conectou no HANA')
        return False

    try:
        payload = montar_payload(ex, s.sap_schema, hoje)
    finally:
        ex.close()

    ok = True
    for tabela, chaves in (
        (TABELA_SERIE, 'metrica,vendedor,ano,mes'),
        (TABELA_KPI, 'escopo,vendedor'),
        (TABELA_RANKING, 'escopo,tipo,vendedor,chave'),
    ):
        linhas = payload[tabela]
        if not linhas:
            logger.warning('[VENDAS_BI] %s: nada a gravar', tabela)
            continue
        if not loader.upsert_data(tabela, linhas, on_conflict=chaves):
            # O NOME da tabela no desfecho é o que faltou em 21/08: o incidente
            # foi um lote da série recusado por CHECK, e a mensagem gravada teria
            # apontado direto para `bi_vendas_serie_mensal`.
            falhas.append(f'upsert falhou em {tabela}')
            ok = False

    consultas_falhas = payload.get(CONSULTAS_FALHAS) or []
    if consultas_falhas:
        falhas.append(
            'consulta falhou no HANA: ' + ', '.join(consultas_falhas)
            + ' (o que ela alimenta ficou com a carga anterior)'
        )
        ok = False

    # A poda vem DEPOIS da escrita, e só se ela deu certo: assim a tela nunca
    # fica sem dado — no pior caso mostra o anterior. A failed query also skips it: the
    # prune by stamp would delete what the failed query could not rewrite.
    if ok:
        carimbo = payload[TABELA_KPI][0]['atualizado_em'] if payload[TABELA_KPI] else None
        if carimbo:
            if not _podar(loader, carimbo, ano_inicial(hoje), payload[TABELA_SERIE]):
                falhas.append('poda falhou')
                ok = False
    else:
        # Vale registrar: a poda ser PULADA é o que faz o ranking de "hoje"
        # amanhecer com cliente de ontem. Sem esta linha, o desfecho contaria só
        # metade da história.
        falhas.append('poda não executada (carga incompleta)')
    return ok


def _podar(
    loader: SupabaseLoader,
    quando: str,
    desde: int,
    serie: Iterable[dict[str, Any]] = (),
) -> bool:
    """Delete what this run did NOT rewrite and no future run would rewrite.

    Why it exists: ``bi_vendas_kpi`` is keyed ``(escopo, vendedor)`` and ``bi_vendas_ranking``
    ``(escopo, tipo, vendedor, chave)`` -- **neither carries a date**. The upsert overwrites what
    reappeared and leaves the rest untouched, so whoever leaves the period **stays in the table
    forever**:

    - the "hoje" ranking wakes up with yesterday's customers and the card above it saying R$ 0,00;
    - on the 1st, "mês atual" shows the whole previous month;
    - a salesperson who goes a month without selling vanishes from the detail and freezes with the
      four cards of weeks ago, labelled "Hoje".

    Without the prune the table also grows without a ceiling (dozens of new customers per month)
    and the ``__TOTAL__`` cut ends up over the 1000 rows of PostgREST's ``db_max_rows``, which
    truncates **silently**.

    The criterion is the stamp: every row of this run carries the same ``atualizado_em``, so
    "different from my stamp" is exactly the rest.

    The monthly series is pruned by **year**, not by stamp. Its key has year and month, so inside
    the window each row is rewritten in place -- but the upsert only reaches ``ano >= desde``, and
    when the year turns, the year that left the window stays behind it: no run rewrites or deletes
    it, and it would freeze in the table forever, growing one year per rollover (finding of the
    19/08/2026 review -- the 2024 rows would stay from 01/01/2027). The stamp does not work for it:
    a failing HANA query becomes an empty list, and the stamp prune would delete the whole metric
    the run could not read.

    Inside the window the series IS ALSO pruned by stamp, but only in the months this run read (see
    :func:`_podar_serie_meses_lidos`).
    """
    ok = True
    for tabela in (TABELA_KPI, TABELA_RANKING):
        ok = loader.delete_nao_carimbadas(tabela, 'atualizado_em', quando) and ok
    ok = loader.delete_menor_que(TABELA_SERIE, 'ano', desde) and ok
    ok = _podar_serie_meses_lidos(loader, quando, serie) and ok
    return ok


def _podar_serie_meses_lidos(
    loader: SupabaseLoader, quando: str, serie: Iterable[dict[str, Any]]
) -> bool:
    """In the months this run read, delete the salesperson that disappeared from the source.

    Finding of 25/09/2026: the series upsert only rewrites the salesperson who CAME BACK in the HANA
    result. If a representative's only order of the month is cancelled, their row vanishes from the
    source and stays in the table forever with the old stamp -- the sum of the salespeople stops
    matching ``__TOTAL__`` and the app (Vendas mode reads the salesperson's own row) shows a ghost
    value. Real case: 2026/09 'pedidos' of Adilson Soares, R$ 318.029,68, stuck at 21/09 while the
    rest of the month had the new stamp.

    "Month read" = (metric, year, month) in which this run wrote ``__TOTAL__``. A month that did not
    come back (a failed query becomes an empty list) is left untouched -- which is why the series is
    not part of the whole-table stamp prune. One call per (metric, year), with the months in an IN.
    """
    lidos: dict[tuple[str, int], set[int]] = {}
    for linha in serie:
        if linha.get('vendedor') == TOTAL and linha.get('atualizado_em') == quando:
            lidos.setdefault((linha['metrica'], linha['ano']), set()).add(linha['mes'])
    ok = True
    for (metrica, ano), meses in sorted(lidos.items()):
        ok = (
            loader.delete_nao_carimbadas_no_recorte(
                TABELA_SERIE,
                'atualizado_em',
                quando,
                {'metrica': metrica, 'ano': ano},
                em=('mes', sorted(meses)),
            )
            and ok
        )
    return ok


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    # O lock fica no entrypoint, e não dentro de `main()`: a API e o agendador
    # já o pegam antes de chamar, e um `FileLock` aninhado sobre o mesmo arquivo
    # travaria os dois no Windows. Sem isto, a carga manual (`python -m
    # extract_vendas_bi`) corria por fora e podia atropelar a agendada — foi o
    # que deixou KPI e ranking de execuções diferentes na mesma tela.
    try:
        with vendas_bi_sync_lock(timeout=0):
            sys.exit(0 if main() else 1)
    except FileLockTimeout:
        logger.error('[VENDAS_BI] já há uma carga em andamento — nada a fazer')
        sys.exit(2)
