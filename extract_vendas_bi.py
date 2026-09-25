"""ETL: SAP HANA → agregados do dashboard "Vendas" no Supabase.

Origem
------
- ``VW_PEDIDO_ALTA``       → Pedidos. A medida é ``SUM("VlrPedido")``.
- ``VW_FATO_FATURAMENTO``  → Faturamento. A medida é ``SUM("Valor")``.

⚠️ **A partir de 21/09/2026 o Pedidos NÃO bate com o Power BI por desenho.** As
duas consultas da ``VW_PEDIDO_ALTA`` cortam os pedidos de ``pedidos_bloqueados``
— hoje só o 84337 (NAVARRO, R$ 93.531,74, 02/09/2026), fechado na mão no SAP sem
entrega nem nota. Setembro/2026 sai R$ 93.531,74 MENOR que o do Power BI, e isso
é a correção, não uma regressão. O Faturamento não muda: esse pedido nunca virou
nota.

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
# mobile_orcaview_V3/hooks/vendas/useVendasBI.ts.
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
    """Primeiro ano da janela de histórico (o corrente conta como um).

    É a MESMA conta para consultar e para podar: as consultas mensais só trazem
    ``ano >= ano_inicial``, então tudo o que ficou atrás disso na
    ``bi_vendas_serie_mensal`` é lixo por definição — nenhum upsert volta a
    tocá-lo. Se as duas contas divergirem, ou a poda come dado vivo, ou o lixo
    volta a acumular.
    """
    return hoje.year - (ANOS_HISTORICO - 1)


# ---------------------------------------------------------------- SQL (HANA)


def sql_pedidos_mensal(schema: str, ano_inicial: int) -> str:
    """Pedidos por ano/mês/vendedor.

    Teto de data em `CURRENT_DATE`: um pedido digitado com ano errado (2027)
    entraria na série e, como o app deriva a régua do gráfico **do próprio
    dado**, o eixo inteiro escorregaria — 2025-2027, sem 2024, sem nada
    explicando por quê.
    """
    validate_sql_identifier(schema)
    return f'''
        SELECT YEAR("DATA") AS ANO, MONTH("DATA") AS MES,
               "CodVend" AS VENDEDOR,
               SUM("VlrPedido") AS VALOR, COUNT(*) AS QTD
          FROM "{schema}"."VW_PEDIDO_ALTA"
         WHERE YEAR("DATA") >= {int(ano_inicial)}
           AND "DATA" < ADD_DAYS(CURRENT_DATE, 1)
           {sql_nao_bloqueado('"DOC"', prefixo="AND ")}
         GROUP BY YEAR("DATA"), MONTH("DATA"), "CodVend"
    '''


def sql_detalhe_recente(schema: str, de: date, ate: date) -> str:
    """Pedidos por dia × vendedor × cliente no intervalo fechado ``[de, ate]``.

    Uma consulta só alimenta **os quatro KPIs e os quatro rankings**, porque a
    janela mínima que a tela precisa (do 1º dia do mês passado até hoje) cabe em
    ~150 linhas agregadas. Duas consultas separadas — uma para o dia, outra para
    o mês — divergiriam a cada pedido lançado entre as duas, e o app mostraria um
    KPI que não é a soma do ranking exibido logo abaixo dele.
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
          FROM "{schema}"."VW_PEDIDO_ALTA" p
          LEFT JOIN "{schema}"."OCRD" c ON c."CardCode" = p."CardCode"
         WHERE p."DATA" >= '{de.isoformat()} 00:00:00'
           AND p."DATA" <  '{(ate + timedelta(days=1)).isoformat()} 00:00:00'
           {sql_nao_bloqueado('p."DOC"', prefixo="AND ")}
         GROUP BY TO_VARCHAR(p."DATA", 'YYYY-MM-DD'), p."CodVend", p."CardCode"
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
               SUM(f."Valor") AS VALOR, COUNT(DISTINCT f."DOC") AS QTD
          FROM "{schema}"."VW_FATO_FATURAMENTO" f
          LEFT JOIN "{schema}"."OSLP" v ON v."SlpCode" = f."CodVend"
         WHERE YEAR(f."DATA") >= {int(ano_inicial)}
           AND f."DATA" < ADD_DAYS(CURRENT_DATE, 1)
         GROUP BY YEAR(f."DATA"), MONTH(f."DATA"), COALESCE(v."SlpName", '?')
    '''


def sql_orcamentos_mensal(schema: str, ano_inicial: int) -> str:
    """Orçamentos (cotações) emitidos por ano/mês/vendedor.

    UNIÃO das duas views de cotação, deduplicada por ``Cotacao``. As duas, e não
    uma, porque cada uma tem o que falta na outra (medido em 24/08/2026, janela
    de 3 anos):

    - ``VW_EVOL_ORCAMENTO_ALT`` traz as **canceladas** (``StatusWBC`` 99), que a
      ``VW_ORCAMENTO_ALT`` **não tem nenhuma**: eram **+607 cotações (+7,4%)**
      faltando no gráfico, 4 a 92 por mês. Cotação cancelada não deixa de ter
      sido emitida (regra do Marcelo, 24/08) -- e desde que o eixo virou
      QUANTIDADE (web V117.834) essa ausência aparece direto na barra;
    - ``VW_ORCAMENTO_ALT`` tem **25 cotações antigas** (todas do "Administração",
      numeração baixa) que **não existem** na de evolução. Trocar uma view pela
      outra as apagaria do histórico; a união preserva as duas pontas -- medido:
      **zero meses perdem cotação**.

    A deduplicação por ``Cotacao`` conserta um terceiro erro, que existia antes
    desta mudança: **as duas views repetem a mesma cotação quando ela muda de
    status** (64097 aparece com 40 e com 60; 5151 com 0 e com 60). O ``COUNT(*)``
    anterior contava essas duas vezes e o ``SUM`` somava o valor em dobro -- o
    docstring antigo dizia "não vale dedupe" porque só o VALOR importava; com o
    eixo em quantidade, vale.

    ``MAX(VALOR)`` no grupo interno é seguro: nenhuma cotação tem valor diferente
    entre as views (conferido, 0 divergências em 3 anos).

    ``Representante`` está no mesmo espaço de nomes do ``CodVend`` da
    VW_PEDIDO_ALTA nas DUAS views.
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


def janelas(hoje: date) -> dict[str, tuple]:
    """Intervalo fechado ``(de, ate)`` de cada escopo, e a competência de cada um.

    Mês passado é calculado voltando um dia do dia 1 do mês corrente — a virada
    de ano sai de graça e não há aritmética de mês 0.
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
    registros: Iterable[dict[str, Any]], metrica: str, quando: str
) -> list[dict[str, Any]]:
    """Linhas de ``bi_vendas_serie_mensal``, com o consolidado incluído.

    O ``__TOTAL__`` é somado aqui, e não numa segunda consulta ao HANA, para que
    o consolidado e as partes venham do MESMO retorno: duas consultas separadas
    divergiriam a cada pedido lançado entre uma e outra, e o app mostraria um
    total que não é a soma do que ele exibe.
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
    """`YYYY-MM-DD` dentro do intervalo fechado. Comparação de string basta no ISO."""
    return bool(dia) and de.isoformat() <= dia <= ate.isoformat()


def linhas_kpi(
    detalhe: Iterable[dict[str, Any]],
    hoje: date,
    quando: str,
) -> list[dict[str, Any]]:
    """Os quatro cartões, por vendedor e consolidado.

    Os quatro escopos saem do **mesmo** retorno diário (:func:`sql_detalhe_recente`),
    e não de consultas separadas: o cartão "mês atual" tem de ser exatamente a
    soma do ranking do mês que aparece logo abaixo dele na tela.

    Vendedor sem venda no período **ganha linha zerada**: sem ela o app não
    distingue "nenhum pedido hoje" de "o dado não chegou", e as duas coisas
    pintam a mesma tela vazia.
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
    """Ranking de vendedores, de clientes e de UFs, **um conjunto por escopo**.

    ⚠️ ``tipo='uf'`` exige o CHECK ampliado em ``bi_vendas_ranking``
    (``sql/migracao_bi_vendas_ranking_uf.sql``) — **o ALTER vai ANTES deste
    código entrar em produção**: ampliar o domínio não quebra o código velho,
    mas o código novo gravando 'uf' no CHECK antigo falha a carga inteira.

    Os quatro escopos existem porque na tela o cartão do topo virou filtro: tocar
    em "ontem" tem de trocar o ranking inteiro, e ranking de um dia não se deriva
    do ranking do mês.

    Dois escopos de visibilidade convivem na coluna ``vendedor``:

    - ``__TOTAL__`` — placar de vendedores **e** de clientes da empresa. Só
      admin e diretoria alcançam (RLS).
    - o nome de cada vendedor — **apenas os clientes dele**. É o que o
      representante vê: sem isso o card do ranking nasce vazio na tela dele, o
      que se lê como app quebrado. O placar de vendedores continua fora do
      alcance dele — é o número dos colegas.
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
    """Uma linha de ranking. ``visibilidade`` é quem alcança, não quem vendeu."""
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


def _consultar(ex: SAPExtractor, sql: str, rotulo: str) -> list[dict[str, Any]]:
    """Roda a consulta e devolve dicionários. Falha vira lista vazia + log."""
    df = ex.execute_query(sql)
    if df is None:
        logger.error('[VENDAS_BI] consulta %s falhou', rotulo)
        return []
    logger.info('[VENDAS_BI] %s: %s linha(s)', rotulo, len(df))
    return df.to_dict('records')


def montar_payload(ex: SAPExtractor, schema: str, hoje: date) -> dict[str, list[dict[str, Any]]]:
    """Consulta o HANA e devolve as linhas prontas das três tabelas."""
    quando = agora_iso()
    desde = ano_inicial(hoje)

    pedidos = _consultar(ex, sql_pedidos_mensal(schema, desde), 'pedidos mensal')
    faturamento = _consultar(ex, sql_faturamento_mensal(schema, desde), 'faturamento mensal')
    # Do 1º dia do mês passado até hoje — a menor janela que cobre os quatro
    # escopos dos cartões, que agora também filtram os rankings.
    inicio = janelas(hoje)['mes_passado'][0]
    detalhe = _consultar(ex, sql_detalhe_recente(schema, inicio, hoje), 'detalhe recente')

    orcamentos = _consultar(
        ex, sql_orcamentos_mensal(schema, desde), 'orcamentos mensal'
    )
    serie = (
        linhas_serie(pedidos, 'pedidos', quando)
        + linhas_serie(faturamento, 'faturamento', quando)
        + linhas_serie(orcamentos, 'orcamentos', quando)
    )
    return {
        TABELA_SERIE: serie,
        TABELA_KPI: linhas_kpi(detalhe, hoje, quando),
        TABELA_RANKING: linhas_ranking(detalhe, hoje, quando),
    }


ROTINA_NOME = 'VENDAS_BI'
ROTINA_ROTULO = 'Agregados do dashboard Vendas'


def main(hoje: date | None = None) -> bool:
    """Carga completa dos agregados de Vendas. `True` se tudo entrou.

    Envelope de :func:`_carga` que cronometra a execução e **registra o desfecho**
    em ``rotinas_execucao``. O registro existe porque, até 21/08/2026, esta rotina
    não tinha desfecho gravado em lugar nenhum: um `False` devolvido daqui não era
    lido por ninguém, e uma falha parcial durou 20 horas sem acender nada.
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
    """Grava o desfecho, se houver com quem gravar. Nunca atrapalha a carga."""
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
    """Confere as credenciais e devolve com quem gravar. `None` = nem começa.

    Sem chave do Supabase não há carga **nem** registro: o desfecho de "faltou
    credencial" só existe no log da máquina.
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
    """A carga em si. Acrescenta a `falhas` o nome do que não entrou."""
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

    # A poda vem DEPOIS da escrita, e só se ela deu certo: assim a tela nunca
    # fica sem dado — no pior caso mostra o anterior.
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
        falhas.append('poda não executada (escrita incompleta)')
    return ok


def _podar(
    loader: SupabaseLoader,
    quando: str,
    desde: int,
    serie: Iterable[dict[str, Any]] = (),
) -> bool:
    """Apaga o que a execução NÃO reescreveu e nenhuma futura reescreveria.

    Por que existe: `bi_vendas_kpi` tem chave `(escopo, vendedor)` e
    `bi_vendas_ranking` tem `(escopo, tipo, vendedor, chave)` — **nenhuma das
    duas carrega data**. O upsert sobrescreve o que voltou a aparecer e deixa o
    resto intacto, então quem sai do período **fica na tabela para sempre**:

    - o ranking de "hoje" amanhece com os clientes de ontem e o cartão em cima
      dizendo R$ 0,00;
    - no dia 1º, "mês atual" mostra o mês anterior inteiro;
    - o vendedor que passa um mês sem vender some do detalhe e congela com os
      quatro cartões de semanas atrás, rotulados "Hoje".

    Sem a poda a tabela também cresce sem teto (dezenas de clientes novos por
    mês) e o recorte `__TOTAL__` acaba passando das 1000 linhas do `db_max_rows`
    do PostgREST — que trunca **calado**.

    O critério é o carimbo: toda linha desta execução levou o mesmo
    ``atualizado_em``, então "diferente do meu carimbo" é exatamente o resto.

    A série mensal é podada por **ano**, não por carimbo. A chave dela tem ano e
    mês, então dentro da janela cada linha é reescrita no lugar — mas o upsert
    só alcança ``ano >= desde``, e quando o ano vira, o ano que saiu da janela
    fica atrás dela: nenhuma execução o reescreve nem o apaga, e ele congelaria
    na tabela para sempre, crescendo um ano a cada virada (achado da revisão de
    19/08/2026 — as linhas de 2024 ficariam lá a partir de 01/01/2027). O
    carimbo não serve para ela: consulta HANA que falha vira lista vazia, e a
    poda por carimbo apagaria a métrica inteira que a execução não conseguiu
    ler.

    Dentro da janela a série TAMBÉM é podada por carimbo, mas só nos meses que
    esta execução leu (ver :func:`_podar_serie_meses_lidos`).
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
    """Apaga, nos meses que esta execução leu, o vendedor que sumiu da origem.

    Achado de 25/09/2026: o upsert da série só reescreve o vendedor que VOLTOU
    no retorno do HANA. Se o único pedido do mês de um representante é
    cancelado, a linha dele some da origem e fica na tabela para sempre com o
    carimbo antigo — a soma dos vendedores deixa de bater com o ``__TOTAL__`` e
    o app (modo Vendas lê a linha do próprio vendedor) mostra um valor fantasma.
    Caso real: 2026/09 'pedidos' do Adilson Soares, R$ 318.029,68, parado em
    21/09 enquanto o resto do mês tinha o carimbo novo.

    "Mês lido" = (métrica, ano, mês) em que esta execução gravou o
    ``__TOTAL__``. Mês que não voltou (consulta que falhou vira lista vazia)
    fica intocado — é o motivo de a série não entrar na poda por carimbo da
    tabela inteira. Uma chamada por (métrica, ano), com os meses em IN.
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
