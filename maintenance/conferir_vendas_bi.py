"""Conferência ponta a ponta do dashboard "Vendas": Supabase x SAP HANA.

Este script é **somente leitura**. Ele não escreve nada, em lugar nenhum — nem no
Supabase, nem no HANA, nem em arquivo. É a rede de segurança do risco número um
do projeto, escrito no ``mobile_orcaview_V3/docs/PLANO_VENDAS_BI.md`` §4 e §8:
**fonte dupla de verdade**. A tela do celular mostra dinheiro (faturamento e
pedidos da empresa) a partir de três tabelinhas agregadas no Supabase; se o
pipeline da ``.11`` errar uma soma, nada quebra, nenhum log acende, e o número
errado simplesmente aparece bonito no aparelho de quem decide.

POR QUE AS CONSULTAS AO HANA SÃO ESCRITAS DE NOVO AQUI
-----------------------------------------------------
De propósito não se importa ``sql_pedidos_mensal``/``sql_faturamento_mensal``/
``janelas`` do ``extract_vendas_bi.py``. Reaproveitar o SQL do pipeline compararia
o pipeline **consigo mesmo**: um ``GROUP BY`` errado, uma janela de data trocada
ou um ``JOIN`` que duplica linha passariam nos dois lados e a conferência diria
PASSOU. Aqui as agregações são feitas com outro recorte a cada vez:

- **Pedidos/Faturamento mensais**: agregados no servidor SEM a coluna de vendedor.
  O pipeline soma o consolidado em Python, empilhando ``__TOTAL__`` linha a linha
  (``linhas_serie``); aqui quem soma é o HANA. Se a acumulação do Python contar
  alguém duas vezes, os dois números divergem.
- **Faturamento**: **sem** o ``LEFT JOIN`` com ``OSLP``. O join só existe no
  pipeline para traduzir ``SlpCode`` em nome, e um dia em que ele vire ``INNER``
  (ou em que o ``OSLP`` ganhe ``SlpCode`` repetido) o total muda sem aviso — este
  é o único teste que enxerga isso.
- **KPIs**: quatro consultas com ``WHERE`` de intervalo, uma por cartão. O
  pipeline pega UM retorno diário e fatia os quatro escopos em Python; aqui o
  filtro é do banco.
- **Janelas de data**: recalculadas neste arquivo (:func:`janelas_independentes`),
  não importadas. A ``competencia`` que o pipeline gravou é conferida contra a
  esperada — é o que pegaria uma virada de mês/ano feita errado.

A DATA DE REFERÊNCIA NÃO É "HOJE"
---------------------------------
"Hoje" e "ontem" são o dia em que **o pipeline rodou**, não o dia em que a
conferência roda. Tomar ``date.today()`` faria o script acusar divergência em
todo mês virado e em toda madrugada em que a ``.11`` estivesse parada — ruído que
esconderia o erro de verdade. A referência sai da própria ``competencia`` gravada
no escopo ``hoje``, e a idade do dado vai no cabeçalho do relatório.

Consequência honesta: os escopos ``hoje`` e ``mes_atual`` cobrem um período que
ainda está andando. Um pedido lançado no SAP **depois** da última carga aparece no
HANA e não no Supabase, e vira DIVERGIU legítimo — a diferença é o pedido novo,
não um defeito. O cabeçalho mostra há quanto tempo a carga rodou justamente para
essa leitura. ``ontem`` e ``mes_passado`` são períodos fechados: ali divergência é
defeito, ponto.

Uso
---
    python maintenance/conferir_vendas_bi.py [--vendedor NOME] [--so-erros]

``--vendedor`` escolhe de quem é o ranking de clientes conferido (o padrão é o
primeiro colocado do mês). ``--so-erros`` imprime só o que divergiu — é a forma
de virar checagem automática. Sai com **código 1** se houver qualquer divergência
acima de R$ 0,01, e 2 se a conferência não conseguiu nem rodar.

Credenciais: ``config.get_settings()`` para o SAP e ``SUPABASE_SERVICE_ROLE_KEY``
do ``.env`` para o Supabase. A chave de serviço passa por cima da RLS **de
propósito**: a conferência precisa enxergar as linhas de todos os vendedores para
somar as partes e comparar com o ``__TOTAL__``. Ela nunca é impressa.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

_RAIZ = Path(__file__).resolve().parent.parent
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

from config import get_settings  # noqa: E402
from db_utils import read_dbapi_query  # noqa: E402
from sap_connection import SAPExtractor  # noqa: E402

# Acentos no relatório num console cp850 (PowerShell 5.1) derrubariam o script no
# meio da impressão — trocar o caractere é melhor que perder a conferência.
try:  # pragma: no cover - depende do console
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except (AttributeError, OSError):
    pass

#: Diferença aceita numa comparação de dinheiro. Acima disso é DIVERGIU.
#: Existe por causa do arredondamento: o pipeline grava cada grupo com 2 casas e
#: o HANA soma em decimal cheio.
TOLERANCIA = Decimal('0.01')

#: Linha consolidada das três tabelas (todos os vendedores somados).
TOTAL = '__TOTAL__'

ESCOPOS: Tuple[str, ...] = ('hoje', 'ontem', 'mes_atual', 'mes_passado')

#: Teto do ranking de clientes gravado pelo pipeline (``TOP_CLIENTES``). Usado para
#: saber se uma ausência é "corte do top N" ou "cliente sumido".
TOP_CLIENTES = 20

#: Tamanho da página na leitura do Supabase. O PostgREST tem teto de linhas por
#: resposta (``db_max_rows``); ler sem paginar devolveria um pedaço da tabela
#: **sem erro nenhum**, e a conferência somaria menos vendedores do que existem.
PAGINA = 1000


# --------------------------------------------------------------- formatação


def brl(v: Optional[Decimal]) -> str:
    """Decimal -> ``R$ 1.314.876,11``. ``None`` vira travessão."""
    if v is None:
        return '-'
    s = f'{v:,.2f}'
    return 'R$ ' + s.replace(',', '\x00').replace('.', ',').replace('\x00', '.')


def dec(v: Any) -> Decimal:
    """Qualquer coisa -> Decimal, passando por ``str``.

    Por ``str`` de propósito: ``float`` de dinheiro reintroduz o erro binário que
    a conferência existe para não ter (``Decimal(0.1)`` é 0,1000000000000000055…).
    O PostgREST devolve ``numeric`` ora como número, ora como string, dependendo da
    versão — os dois caminhos entram aqui iguais.
    """
    if v is None:
        return Decimal('0')
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal('0')


def idade(carimbo: Optional[str]) -> str:
    """``atualizado_em`` ISO -> "há 2h13" legível. Vazio devolve '?'."""
    if not carimbo:
        return '?'
    try:
        quando = datetime.fromisoformat(carimbo.replace('Z', '+00:00'))
    except ValueError:
        return '?'
    if quando.tzinfo is None:
        quando = quando.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - quando
    minutos = int(delta.total_seconds() // 60)
    if minutos < 60:
        return f'há {minutos}min'
    return f'há {minutos // 60}h{minutos % 60:02d}'


# ------------------------------------------------------------------ relatório


@dataclass
class Relatorio:
    """Coletor de conferências. Imprime enquanto roda e conta as divergências."""

    so_erros: bool = False
    total: int = 0
    falhas: List[str] = field(default_factory=list)

    def secao(self, titulo: str) -> None:
        print(f'\n{titulo}')
        print('-' * len(titulo))

    def dinheiro(
        self,
        nome: str,
        supabase: Optional[Decimal],
        hana: Optional[Decimal],
        nota: str = '',
    ) -> bool:
        """Compara dois valores em reais. Ausência de um dos lados é divergência."""
        self.total += 1
        if supabase is None or hana is None:
            ok = False
            dif: Optional[Decimal] = None
        else:
            dif = supabase - hana
            ok = abs(dif) <= TOLERANCIA
        detalhe = (
            f'Supabase {brl(supabase):>18} | HANA {brl(hana):>18} | '
            f'dif {brl(dif) if dif is not None else "-":>14}'
        )
        self._linha(ok, nome, detalhe, nota)
        return ok

    def inteiro(self, nome: str, supabase: Optional[int], hana: Optional[int]) -> bool:
        """Compara duas contagens (quantidade de pedidos). Igualdade exata."""
        self.total += 1
        ok = supabase is not None and hana is not None and supabase == hana
        dif = '-' if supabase is None or hana is None else f'{supabase - hana:+d}'
        self._linha(ok, nome, f'Supabase {supabase!s:>18} | HANA {hana!s:>18} | dif {dif:>14}')
        return ok

    def igual(self, nome: str, obtido: Any, esperado: Any, nota: str = '') -> bool:
        """Compara dois valores quaisquer por igualdade (datas, textos)."""
        self.total += 1
        ok = obtido == esperado
        self._linha(ok, nome, f'gravado {obtido!s:>18} | esperado {esperado!s:>18}', nota)
        return ok

    def _linha(self, ok: bool, nome: str, detalhe: str, nota: str = '') -> None:
        marca = 'PASSOU  ' if ok else 'DIVERGIU'
        if not ok:
            self.falhas.append(nome)
        if ok and self.so_erros:
            return
        print(f'  {marca} {nome:<44} {detalhe}')
        if nota:
            print(f'           {nota}')

    def observacao(self, texto: str) -> None:
        """Informação que não é conferência (contexto para ler o resultado)."""
        if not self.so_erros:
            print(f'  ... {texto}')

    def fechar(self) -> int:
        print('\n' + '=' * 78)
        if self.falhas:
            print(f'RESULTADO: {len(self.falhas)} DIVERGÊNCIA(S) em {self.total} conferências')
            for nome in self.falhas:
                print(f'  - {nome}')
        else:
            print(f'RESULTADO: as {self.total} conferências passaram')
        print('=' * 78)
        return 1 if self.falhas else 0


# ------------------------------------------------------------------ Supabase


def cliente_supabase():
    """Cliente do Supabase com a chave de serviço lida do ``.env``.

    A chave nunca é impressa nem logada; só o tamanho, quando falta, para o
    diagnóstico não virar adivinhação.
    """
    from supabase import create_client  # import tardio: pesa ~1s

    url = os.getenv('SUPABASE_URL')
    chave = os.getenv('SUPABASE_SERVICE_ROLE_KEY')
    if not url or not chave:
        raise SystemExit(
            'ERRO: SUPABASE_URL e/ou SUPABASE_SERVICE_ROLE_KEY ausentes no .env '
            '(a conferência precisa passar por cima da RLS para somar as partes).'
        )
    return create_client(url, chave)


def ler_tabela(cli: Any, tabela: str) -> List[Dict[str, Any]]:
    """Lê a tabela inteira, paginando. Ver :data:`PAGINA` para o porquê."""
    linhas: List[Dict[str, Any]] = []
    inicio = 0
    while True:
        resp = cli.table(tabela).select('*').range(inicio, inicio + PAGINA - 1).execute()
        lote = resp.data or []
        linhas.extend(lote)
        if len(lote) < PAGINA:
            return linhas
        inicio += PAGINA


# ---------------------------------------------------------------------- HANA


def janelas_independentes(ref: date) -> Dict[str, Tuple[date, date, date]]:
    """``escopo -> (de, ate, competencia)``, recalculado sem olhar o pipeline.

    Intervalo fechado nos dois lados. ``mes_passado`` sai de "um dia antes do dia
    1 do mês da referência" — a virada de ano vem de graça e não há mês 0. É
    escrito aqui, e não importado de ``extract_vendas_bi.janelas``, porque é
    exatamente esta aritmética que a conferência precisa desconfiar.
    """
    primeiro = date(ref.year, ref.month, 1)
    fim_passado = primeiro - timedelta(days=1)
    inicio_passado = date(fim_passado.year, fim_passado.month, 1)
    ontem = ref - timedelta(days=1)
    return {
        'hoje': (ref, ref, ref),
        'ontem': (ontem, ontem, ontem),
        'mes_atual': (primeiro, ref, primeiro),
        'mes_passado': (inicio_passado, fim_passado, inicio_passado),
    }


def _consulta(ex: SAPExtractor, sql: str, params: Optional[Sequence[Any]] = None) -> List[Dict[str, Any]]:
    """Roda SQL de leitura no HANA e devolve dicionários.

    Usa ``read_dbapi_query`` direto (e não ``SAPExtractor.execute_query``) porque
    é o único caminho com ``?`` parametrizado — o nome do vendedor vem de
    argumento de linha de comando e não entra em SQL por concatenação.
    """
    df = read_dbapi_query(sql, ex.connection, params)
    return df.to_dict('records')


def _de_ate(de: date, ate: date) -> Tuple[str, str]:
    """Intervalo fechado de datas -> par de literais ``[de 00:00, ate+1 00:00)``.

    A view expõe ``DATA`` como TIMESTAMP: comparar com ``<= ate`` perderia tudo o
    que foi lançado depois da meia-noite do último dia.
    """
    return f'{de.isoformat()} 00:00:00', f'{(ate + timedelta(days=1)).isoformat()} 00:00:00'


def hana_total_periodo(
    ex: SAPExtractor, schema: str, de: date, ate: date
) -> Tuple[Decimal, int]:
    """``SUM(VlrPedido)`` e contagem de pedidos no intervalo — o número do cartão.

    A medida é o bruto, **sem** ``Indice_Pedido``: medido contra o Power BI em
    11/08/2026 (agosto fecha em R$ 1.314.876,11 no bruto e R$ 1.309.079,46 pelo
    índice). Trocar isto aqui faria a conferência abençoar a medida errada.
    """
    ini, fim = _de_ate(de, ate)
    linhas = _consulta(
        ex,
        f'''SELECT SUM("VlrPedido") AS VALOR, COUNT(*) AS QTD
              FROM "{schema}"."VW_PEDIDO_ALTA"
             WHERE "DATA" >= ? AND "DATA" < ?''',
        (ini, fim),
    )
    if not linhas:
        return Decimal('0'), 0
    return dec(linhas[0].get('VALOR')), int(linhas[0].get('QTD') or 0)


def hana_serie_mensal(
    ex: SAPExtractor, schema: str, metrica: str, ano_inicial: int
) -> Dict[Tuple[int, int], Decimal]:
    """``(ano, mes) -> valor`` agregado pelo HANA, sem passar por vendedor.

    É o contraponto do ``__TOTAL__`` que o pipeline empilha em Python. No
    faturamento vai **sem** o ``LEFT JOIN`` com ``OSLP`` de propósito: o join é
    enfeite para o total (só traduz código em nome), então se algum dia ele
    multiplicar linha, o total daqui continua certo e o de lá não.
    """
    if metrica == 'pedidos':
        sql = f'''SELECT YEAR("DATA") AS ANO, MONTH("DATA") AS MES, SUM("VlrPedido") AS VALOR
                    FROM "{schema}"."VW_PEDIDO_ALTA"
                   WHERE YEAR("DATA") >= {int(ano_inicial)}
                   GROUP BY YEAR("DATA"), MONTH("DATA")'''
    else:
        # ValorAdiant NÃO entra — decisão do §9 do plano, conferida contra o PBI.
        sql = f'''SELECT YEAR("DATA") AS ANO, MONTH("DATA") AS MES, SUM("Valor") AS VALOR
                    FROM "{schema}"."VW_FATO_FATURAMENTO"
                   WHERE YEAR("DATA") >= {int(ano_inicial)}
                   GROUP BY YEAR("DATA"), MONTH("DATA")'''
    saida: Dict[Tuple[int, int], Decimal] = {}
    for r in _consulta(ex, sql):
        saida[(int(r['ANO']), int(r['MES']))] = dec(r.get('VALOR'))
    return saida


def hana_clientes_do_vendedor(
    ex: SAPExtractor, schema: str, vendedor: str, de: date, ate: date
) -> Dict[str, Decimal]:
    """``CardCode -> valor`` dos pedidos de UM vendedor no período.

    ``VW_PEDIDO_ALTA."CodVend"`` já guarda o NOME do vendedor (é o
    ``OSLP.SlpName``); casar por código entregaria a carteira de um vendedor para
    outro — ``app_profiles.slp_code`` diverge do ``OSLP.SlpCode`` em produção.
    O nome entra parametrizado (``?``), nunca concatenado.
    """
    ini, fim = _de_ate(de, ate)
    linhas = _consulta(
        ex,
        f'''SELECT "CardCode" AS CHAVE, SUM("VlrPedido") AS VALOR
              FROM "{schema}"."VW_PEDIDO_ALTA"
             WHERE "DATA" >= ? AND "DATA" < ? AND "CodVend" = ?
             GROUP BY "CardCode"''',
        (ini, fim, vendedor),
    )
    return {str(r['CHAVE']).strip(): dec(r.get('VALOR')) for r in linhas}


# ---------------------------------------------------------------- conferências


def conferir_kpis(
    rel: Relatorio,
    ex: SAPExtractor,
    schema: str,
    kpis: List[Dict[str, Any]],
    janelas: Dict[str, Tuple[date, date, date]],
) -> None:
    """Os quatro cartões do topo, consolidados, contra o HANA.

    São o número mais visível da tela — o que o Marcelo confere de relance contra
    o Power BI. Além do valor, confere-se a ``competencia`` gravada: é ela que diz
    aos dois gráficos qual mês destacar quando o cartão vira filtro, e uma
    competência errada acende o mês errado sem mexer em nenhum valor.

    A quantidade de pedidos entra porque aparece escrita no cartão ("3 pedidos"):
    valor certo com contagem errada é dado duplicado que se anulou na soma.
    """
    rel.secao('[1] KPIs do consolidado (__TOTAL__) x HANA')
    por_escopo = {k['escopo']: k for k in kpis if k.get('vendedor') == TOTAL}

    for escopo in ESCOPOS:
        de, ate, competencia = janelas[escopo]
        linha = por_escopo.get(escopo)
        valor_hana, qtd_hana = hana_total_periodo(ex, schema, de, ate)
        janela = f'{de.isoformat()} a {ate.isoformat()}'
        movel = ' (período em curso: pedido lançado após a carga entra só no HANA)'

        rel.dinheiro(
            f'KPI {escopo} — valor',
            dec(linha['valor']) if linha else None,
            valor_hana,
            nota=f'janela {janela}' + (movel if escopo in ('hoje', 'mes_atual') else ''),
        )
        rel.inteiro(
            f'KPI {escopo} — qtd pedidos',
            int(linha['qtd_pedidos']) if linha else None,
            qtd_hana,
        )
        rel.igual(
            f'KPI {escopo} — competência',
            linha.get('competencia') if linha else None,
            competencia.isoformat(),
        )


def conferir_series(
    rel: Relatorio, ex: SAPExtractor, schema: str, series: List[Dict[str, Any]]
) -> None:
    """As duas séries mensais, mês a mês, contra o HANA.

    Mês a mês e não só o total do ano: dois meses trocados entre si somam o mesmo
    ano e desenham o gráfico errado. A comparação é feita na UNIÃO das chaves dos
    dois lados — mês que existe no HANA e não no Supabase é o defeito mais
    perigoso, porque a tela não desenha buraco nenhum: a linha simplesmente pula.
    """
    for metrica in ('pedidos', 'faturamento'):
        rel.secao(f'[2] Série mensal de {metrica} (__TOTAL__) x HANA')
        da_metrica = [
            s for s in series if s.get('metrica') == metrica and s.get('vendedor') == TOTAL
        ]
        if not da_metrica:
            rel.dinheiro(f'série {metrica} — existe no Supabase', None, Decimal('0'))
            continue

        sup = {(int(s['ano']), int(s['mes'])): dec(s['valor']) for s in da_metrica}
        ano_inicial = min(a for a, _ in sup)
        han = hana_serie_mensal(ex, schema, metrica, ano_inicial)
        rel.observacao(
            f'anos carregados: {ano_inicial}..{max(a for a, _ in sup)} — '
            f'{len(sup)} meses no Supabase, {len(han)} no HANA'
        )

        for chave in sorted(set(sup) | set(han)):
            ano, mes = chave
            rel.dinheiro(f'{metrica} {ano}-{mes:02d}', sup.get(chave), han.get(chave))


def conferir_ranking_fecha_com_kpi(
    rel: Relatorio, kpis: List[Dict[str, Any]], ranking: List[Dict[str, Any]]
) -> None:
    """A soma do ranking de vendedores tem de dar o KPI do mesmo período.

    Esta é a única conferência que não precisa do HANA — e é a que o usuário faz
    sozinho, com o dedo: o cartão "Mês atual" fica logo acima da lista de
    vendedores, e se somar a lista não der o cartão, a tela perde a confiança
    inteira num segundo. Os dois números nascem do mesmo retorno diário no
    pipeline exatamente para fechar; se pararem de fechar, alguém separou as
    consultas.
    """
    rel.secao('[3] Soma do ranking de vendedores x KPI do mesmo período')
    por_escopo = {k['escopo']: k for k in kpis if k.get('vendedor') == TOTAL}

    for escopo in ESCOPOS:
        soma = sum(
            (
                dec(r['valor'])
                for r in ranking
                if r.get('escopo') == escopo
                and r.get('tipo') == 'vendedor'
                and r.get('vendedor') == TOTAL
            ),
            Decimal('0'),
        )
        linha = por_escopo.get(escopo)
        rel.dinheiro(
            f'ranking vendedores {escopo}',
            soma,
            dec(linha['valor']) if linha else None,
            nota='(esquerda = soma do ranking, direita = cartão)',
        )


def conferir_clientes_do_vendedor(
    rel: Relatorio,
    ex: SAPExtractor,
    schema: str,
    ranking: List[Dict[str, Any]],
    janelas: Dict[str, Tuple[date, date, date]],
    vendedor: str,
) -> None:
    """O ranking de clientes de um vendedor, contra o HANA filtrado por ele.

    É a linha do RLS onde o erro é mais caro: esta lista é o que o representante
    vê no aparelho dele. Se o pipeline vazar cliente de outro vendedor para cá,
    nenhuma policy do banco corrige — a linha foi gravada com o escopo de
    visibilidade errado.

    Duas verificações por período:

    1. cada cliente listado vale exatamente o que o HANA diz para aquele vendedor;
    2. **o corte está certo** — nenhum cliente do HANA com valor maior que o
       último colocado ficou de fora. Sem isso, uma lista com os 20 clientes
       errados passaria só por somar direito. Quando a lista vem cheia (o teto de
       ``TOP_CLIENTES``), o que está abaixo do último colocado é corte legítimo,
       não ausência.
    """
    rel.secao(f'[4] Ranking de clientes de "{vendedor}" x HANA filtrado por ele')

    for escopo in ESCOPOS:
        de, ate, _ = janelas[escopo]
        sup = {
            str(r['chave']).strip(): dec(r['valor'])
            for r in ranking
            if r.get('escopo') == escopo
            and r.get('tipo') == 'cliente'
            and r.get('vendedor') == vendedor
        }
        han = hana_clientes_do_vendedor(ex, schema, vendedor, de, ate)

        problemas: List[str] = []
        for chave, valor in sorted(sup.items()):
            if chave not in han:
                problemas.append(f'{chave}: {brl(valor)} no Supabase, inexistente no HANA')
            elif abs(valor - han[chave]) > TOLERANCIA:
                problemas.append(f'{chave}: {brl(valor)} x {brl(han[chave])} no HANA')

        piso = min(sup.values()) if sup else None
        cheio = len(sup) >= TOP_CLIENTES
        for chave, valor in sorted(han.items(), key=lambda kv: kv[1], reverse=True):
            if chave in sup:
                continue
            fora_do_corte = cheio and piso is not None and valor <= piso + TOLERANCIA
            if not fora_do_corte:
                problemas.append(f'{chave}: {brl(valor)} no HANA, ausente do ranking')

        soma_sup = sum(sup.values(), Decimal('0'))
        soma_han = sum((han[c] for c in sup if c in han), Decimal('0'))
        rel.dinheiro(
            f'clientes {escopo} — soma dos {len(sup)} listados',
            soma_sup,
            soma_han if sup else Decimal('0'),
            nota=f'{len(han)} cliente(s) do vendedor no HANA no período',
        )
        rel.igual(
            f'clientes {escopo} — lista bate item a item',
            'ok' if not problemas else f'{len(problemas)} problema(s)',
            'ok',
            nota='; '.join(problemas[:8]) if problemas else '',
        )


def conferir_partes_somam_total(
    rel: Relatorio, kpis: List[Dict[str, Any]], series: List[Dict[str, Any]]
) -> None:
    """A soma das linhas por vendedor tem de dar a linha ``__TOTAL__``.

    Conferência interna do Supabase, sem HANA. Ela protege o outro lado da RLS:
    o admin lê ``__TOTAL__`` e o representante lê a linha dele, e as duas leituras
    saem da MESMA tabela. Se as partes não somarem o total, um dos dois papéis está
    vendo um número que o outro não reconhece — e ninguém descobre, porque nenhum
    usuário enxerga as duas visões ao mesmo tempo.
    """
    rel.secao('[5] Soma das linhas por vendedor x linha __TOTAL__')

    partes_kpi: Dict[str, Decimal] = {}
    total_kpi: Dict[str, Decimal] = {}
    for k in kpis:
        escopo = str(k.get('escopo'))
        if k.get('vendedor') == TOTAL:
            total_kpi[escopo] = dec(k['valor'])
        else:
            partes_kpi[escopo] = partes_kpi.get(escopo, Decimal('0')) + dec(k['valor'])
    for escopo in ESCOPOS:
        rel.dinheiro(
            f'KPI {escopo} — partes x total',
            partes_kpi.get(escopo, Decimal('0')),
            total_kpi.get(escopo),
        )

    partes_serie: Dict[Tuple[str, int, int], Decimal] = {}
    total_serie: Dict[Tuple[str, int, int], Decimal] = {}
    for s in series:
        chave = (str(s.get('metrica')), int(s['ano']), int(s['mes']))
        if s.get('vendedor') == TOTAL:
            total_serie[chave] = dec(s['valor'])
        else:
            partes_serie[chave] = partes_serie.get(chave, Decimal('0')) + dec(s['valor'])
    for chave in sorted(set(partes_serie) | set(total_serie)):
        metrica, ano, mes = chave
        rel.dinheiro(
            f'{metrica} {ano}-{mes:02d} — partes x total',
            partes_serie.get(chave),
            total_serie.get(chave),
        )


# ---------------------------------------------------------------------- main


def escolher_vendedor(ranking: List[Dict[str, Any]], pedido: Optional[str]) -> Optional[str]:
    """Quem terá o ranking de clientes conferido.

    Sem ``--vendedor``, escolhe o primeiro colocado do mês: é o vendedor com mais
    linhas de cliente, portanto o que mais chance tem de expor um erro de corte
    ou de escopo de visibilidade.
    """
    if pedido:
        return pedido
    candidatos = [
        r
        for r in ranking
        if r.get('escopo') == 'mes_atual'
        and r.get('tipo') == 'vendedor'
        and r.get('vendedor') == TOTAL
    ]
    if not candidatos:
        return None
    return str(min(candidatos, key=lambda r: int(r.get('posicao') or 99))['chave'])


def data_de_referencia(kpis: Iterable[Dict[str, Any]]) -> Optional[date]:
    """O "hoje" do pipeline, tirado da competência gravada no escopo ``hoje``."""
    for k in kpis:
        if k.get('escopo') == 'hoje' and k.get('vendedor') == TOTAL and k.get('competencia'):
            try:
                return date.fromisoformat(str(k['competencia'])[:10])
            except ValueError:
                return None
    return None


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description='Confere os agregados de Vendas: Supabase x HANA.')
    ap.add_argument('--vendedor', help='de quem é o ranking de clientes conferido')
    ap.add_argument('--so-erros', action='store_true', help='imprime só o que divergiu')
    args = ap.parse_args(argv)

    s = get_settings()
    if not s.sap_ready():
        print('ERRO: credenciais SAP ausentes no .env')
        return 2

    cli = cliente_supabase()
    kpis = ler_tabela(cli, 'bi_vendas_kpi')
    series = ler_tabela(cli, 'bi_vendas_serie_mensal')
    ranking = ler_tabela(cli, 'bi_vendas_ranking')

    if not kpis or not series or not ranking:
        print(
            'ERRO: alguma das três tabelas veio vazia '
            f'(kpi={len(kpis)}, serie={len(series)}, ranking={len(ranking)}). '
            'O pipeline nunca rodou, ou rodou e não gravou.'
        )
        return 2

    ref = data_de_referencia(kpis)
    if ref is None:
        print('ERRO: sem linha (hoje, __TOTAL__) no bi_vendas_kpi — sem data de referência.')
        return 2

    carimbos = sorted(
        str(l.get('atualizado_em')) for l in (kpis + series + ranking) if l.get('atualizado_em')
    )
    vendedores = sorted({str(k.get('vendedor')) for k in kpis} - {TOTAL})

    rel = Relatorio(so_erros=args.so_erros)
    print('=' * 78)
    print('CONFERÊNCIA DO DASHBOARD VENDAS — Supabase x SAP HANA (somente leitura)')
    print('=' * 78)
    print(f'Data de referência (competência do KPI "hoje"): {ref.isoformat()}')
    print(f'Data de hoje nesta máquina                    : {date.today().isoformat()}')
    print(f'Carga mais antiga: {carimbos[0]} ({idade(carimbos[0])})')
    print(f'Carga mais recente: {carimbos[-1]} ({idade(carimbos[-1])})')
    print(f'Linhas: kpi={len(kpis)}  serie={len(series)}  ranking={len(ranking)}')
    print(f'Vendedores nas tabelas ({len(vendedores)}): {", ".join(vendedores)}')
    print(f'Tolerância: {brl(TOLERANCIA)} por conferência')
    if ref != date.today():
        print(
            '\nATENÇÃO: a referência não é hoje — o pipeline da .11 não rodou desde '
            f'{ref.isoformat()}. Os cartões "hoje"/"ontem" da tela estão parados nesse dia.'
        )

    ex = SAPExtractor(s.sap_host, s.sap_port, s.sap_user, s.sap_password, s.sap_database)
    if not ex.connect():
        print('ERRO: não conectou no HANA')
        return 2

    janelas = janelas_independentes(ref)
    try:
        conferir_kpis(rel, ex, s.sap_schema, kpis, janelas)
        conferir_series(rel, ex, s.sap_schema, series)
        conferir_ranking_fecha_com_kpi(rel, kpis, ranking)
        vendedor = escolher_vendedor(ranking, args.vendedor)
        if vendedor:
            conferir_clientes_do_vendedor(rel, ex, s.sap_schema, ranking, janelas, vendedor)
        else:
            rel.secao('[4] Ranking de clientes de um vendedor x HANA')
            rel.observacao('nenhum vendedor no ranking do mês — conferência pulada')
        conferir_partes_somam_total(rel, kpis, series)
    finally:
        ex.close()

    return rel.fechar()


if __name__ == '__main__':
    sys.exit(main())
