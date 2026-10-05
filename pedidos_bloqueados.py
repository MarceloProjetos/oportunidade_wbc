"""pedidos_bloqueados -- a lista fixa de pedidos de venda que não contam.

Pedido que está aqui é tratado como se não existisse: não aparece em tela, não
entra em soma nenhuma e não se propaga para os agregados do Supabase nem para o
app. A regra é **hardcode de propósito** (decisão do Marcelo, 21/09/2026), e não
uma flag no ``.env``: é correção de documentos específicos da produção, não um
recurso que se liga e desliga.

Por que o ``84337`` (NAVARRO, DocEntry 19696, R$ 93.531,74, 02/09/2026) está
aqui: o pedido está FECHADO no SAP (``DocStatus='C'``) **sem entrega e sem nota**
(``RDR1.TargetType = -1``, nada em ``DLN1``/``INV1``) — foi fechado na mão. Mesmo
assim pesava no total de Pedidos do mês, no cartão "Mês atual" e no ranking de
clientes do celular, lido como uma venda que não aconteceu.

**Onde é aplicado nesta máquina:**

* ``extract_vendas_bi.py`` — as duas consultas da ``VW_PEDIDO_ALTA`` (série
  mensal e detalhe recente), que é o que alimenta ``bi_vendas_kpi``,
  ``bi_vendas_ranking`` e ``bi_vendas_serie_mensal``. Como a carga **poda** o que
  ela mesma não reescreveu, a linha antiga do ranking sai sozinha na corrida
  seguinte;
* ``maintenance/conferir_vendas_bi.py`` — o conferidor lê a MESMA view; sem o
  mesmo corte ele acusaria divergência contra um Supabase que está certo;
* ``situacao_pedidos.normalizar`` — o gêmeo do web (ver o aviso lá).

⚠️ **Há uma cópia desta lista no web** (``web_orcaview_V118/backend/services/
pedidos_bloqueados.py``) e outra no app (``mobile_orcaview_V4/lib/pedidos/
bloqueados.ts``). São repositórios diferentes lendo fontes diferentes (HANA
direto aqui, Supabase no celular); acrescentar um pedido exige mexer nos três.
"""
from __future__ import annotations

from typing import Any, Iterable

__all__ = [
    "PEDIDOS_BLOQUEADOS_DOCNUM",
    "docnum_bloqueado",
    "sql_nao_bloqueado",
]

#: Os pedidos bloqueados, pelo número que aparece na tela (``ORDR.DocNum``, que
#: na ``VW_PEDIDO_ALTA`` se chama ``DOC``).
PEDIDOS_BLOQUEADOS_DOCNUM: frozenset[int] = frozenset({84337})


def _como_int(valor: Any) -> int | None:
    """``'84337'``/``84337``/``84337.0`` -> ``84337``; o resto vira ``None``."""
    if valor is None or isinstance(valor, bool):
        return None
    try:
        return int(str(valor).strip())
    except (TypeError, ValueError):
        return None


def docnum_bloqueado(valor: Any) -> bool:
    """``True`` quando esse número de pedido está na lista."""
    return _como_int(valor) in PEDIDOS_BLOQUEADOS_DOCNUM


def _lista(valores: Iterable[int]) -> str:
    return ", ".join(str(int(v)) for v in sorted(valores))


def sql_nao_bloqueado(coluna: str, *, prefixo: str = " AND ") -> str:
    """Pedaço de SQL que corta os pedidos da lista, já com o ``AND`` na frente.

    Interpolar é seguro aqui e só aqui: os valores são inteiros de uma constante
    do módulo, nunca entrada de usuário. ``coluna`` vem citada pronta pelo
    chamador (``"DOC"`` na view, ``p."DOC"`` quando há alias). Devolve ``''`` com
    a lista vazia, para esvaziá-la não deixar um ``WHERE`` quebrado para trás.
    """
    if not PEDIDOS_BLOQUEADOS_DOCNUM:
        return ""
    return f'{prefixo}{coluna} NOT IN ({_lista(PEDIDOS_BLOQUEADOS_DOCNUM)})'
