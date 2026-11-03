"""Data layer of the dashboard.

Kept apart from the interface on purpose, so the presentation logic (aggregations, labels, formatting)
is testable without starting any server.

**The dashboard never queries SAP or WBC** — only the tracking database (layer map in `docs/wbc/README.md`;
the old `ai_spec/03_architecture.md` is gone). That keeps the screen fast and stops opening the panel from
becoming load on the source systems.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from wbcpython.tracking import (
    Acompanhamento,
    Execucao,
    StatusExecucao,
    StatusIntegracao,
)

#: Situações que provam que o ciclo **fez** alguma coisa com o orçamento.
#: `SEM_ACAO` e `PENDENTE` ficam de fora: são o resultado de olhar, não de agir.
STATUS_COM_ACAO = frozenset(
    {
        StatusIntegracao.COTACAO_CRIADA,
        StatusIntegracao.COTACAO_ATUALIZADA,
        StatusIntegracao.PEDIDO_CRIADO,
        StatusIntegracao.PEDIDO_ATUALIZADO,
        StatusIntegracao.ENCERRADA,
    }
)


@dataclass(frozen=True, slots=True)
class Recorte:
    """One of the numbers at the top, seen as a filter of the list.

    Each panel indicator is a question ("which ones failed?") and the number alone does not answer it — it only
    says how many. The cut is the same definition used to **count**, reused to **list**: that is what guarantees
    that an indicator saying 8 opens a list of 8, not 9.
    """

    id: str
    rotulo: str
    explicacao: str = ""
    #: `None` quer dizer "qualquer situação" — o recorte de "Avaliados".
    situacoes: frozenset[StatusIntegracao] | None = None
    exige_cotacao: bool = False
    exige_pedido: bool = False

    def cobre(self, registro: Acompanhamento) -> bool:
        if self.situacoes is not None and registro.status not in self.situacoes:
            return False
        if self.exige_cotacao and not registro.cotacao_docentry:
            return False
        return not (self.exige_pedido and not registro.pedido_docentry)


#: Os recortes, na ordem em que os indicadores aparecem na tela.
#:
#: A ordem não é decorativa: é a mesma do `_kpis.html`, e manter as duas juntas
#: é o que evita um indicador clicar no filtro do vizinho.
RECORTES: tuple[Recorte, ...] = (
    Recorte(
        id="todos",
        rotulo="Avaliados",
        explicacao="tudo que o ciclo olhou na janela",
    ),
    Recorte(
        id="com_acao",
        rotulo="Com ação",
        explicacao="cotação, pedido ou encerramento pelo ciclo",
        # A mesma constante que os indicadores usam para contar. Repetir a
        # lista aqui faria o número e a lista divergirem no dia em que um
        # status novo entrasse em uma e não na outra.
        situacoes=STATUS_COM_ACAO,
    ),
    Recorte(
        id="sem_acao",
        rotulo="Sem ação",
        explicacao="avaliados e já sincronizados: nada a fazer",
        situacoes=frozenset({StatusIntegracao.SEM_ACAO}),
    ),
    Recorte(
        id="encerradas",
        rotulo="Encerradas",
        explicacao="oportunidade fechada ou cancelada pelo ciclo",
        situacoes=frozenset({StatusIntegracao.ENCERRADA}),
    ),
    Recorte(
        id="com_erro",
        rotulo="Com erro",
        explicacao="o ciclo tentou e o SAP ou o WBC recusou",
        situacoes=frozenset({StatusIntegracao.ERRO}),
    ),
    Recorte(
        id="com_cotacao",
        rotulo="Cotações no SAP",
        explicacao="têm cotação vinculada no SAP",
        exige_cotacao=True,
    ),
    Recorte(
        id="com_pedido",
        rotulo="Pedidos no SAP",
        explicacao="têm pedido vinculado no SAP",
        exige_pedido=True,
    ),
)

_POR_ID = {recorte.id: recorte for recorte in RECORTES}

#: "Avaliados" — o recorte que não recorta nada.
RECORTE_PADRAO = _POR_ID["todos"]


def recorte(identificador: str | None) -> Recorte:
    """An unknown cut falls back to "Avaliados" instead of blowing up.

    The identifier comes from the address, which may have been pasted, hand-edited or bookmarked from an older
    version of the panel. Showing everything is the worst acceptable case; an error page over a parameter is not.
    """
    return _POR_ID.get((identificador or "").strip(), RECORTE_PADRAO)


def aplicar(registros: list[Acompanhamento], corte: Recorte) -> list[Acompanhamento]:
    """Filters in Python, not in SQL, on purpose.

    The indicators count over **this same set** of rows (what the window brought, up to the cap). Cutting here
    guarantees the number on the indicator and the list count are the same number. A separate `WHERE` query would
    be right today and diverge the day the row cap came into play — and the divergence would read as "the panel is
    lying".
    """
    if corte is RECORTE_PADRAO:
        return registros
    return [registro for registro in registros if corte.cobre(registro)]


@dataclass(frozen=True, slots=True)
class Kpis:
    """Numbers at the top of the panel.

    `total` counts what the cycle **evaluated**; `com_acao`, what it touched. The distinction exists because the
    worker reads the whole window on purpose — without it the panel announced hundreds of "integrated" quotes when
    the cycle had touched none.
    """

    total: int = 0
    com_acao: int = 0
    sem_acao: int = 0
    com_erro: int = 0
    encerradas: int = 0
    com_pedido: int = 0
    com_cotacao: int = 0
    valor_em_pedidos: Decimal = Decimal(0)

    @property
    def taxa_de_erro(self) -> float:
        return (self.com_erro / self.total * 100) if self.total else 0.0

    @property
    def saude(self) -> str:
        """Simple traffic light for quick reading.

        The thresholds are deliberately conservative: any error already takes it out of green, because in a flow that
        creates financial documents an isolated error deserves attention — it is not statistical noise.
        """
        if self.total == 0:
            return "sem_dados"
        if self.taxa_de_erro >= 10:
            return "critico"
        if self.com_erro > 0:
            return "atencao"
        return "ok"


def calcular_kpis(registros: list[Acompanhamento]) -> Kpis:
    """Aggregates the indicators from the tracking rows."""
    com_pedido = [r for r in registros if r.pedido_docentry]
    return Kpis(
        total=len(registros),
        com_acao=sum(1 for r in registros if r.status in STATUS_COM_ACAO),
        sem_acao=sum(1 for r in registros if r.status is StatusIntegracao.SEM_ACAO),
        com_erro=sum(1 for r in registros if r.status is StatusIntegracao.ERRO),
        encerradas=sum(1 for r in registros if r.status is StatusIntegracao.ENCERRADA),
        com_pedido=len(com_pedido),
        com_cotacao=sum(1 for r in registros if r.cotacao_docentry),
        valor_em_pedidos=sum((r.pedido_valor or Decimal(0) for r in com_pedido), Decimal(0)),
    )


def linha_para_tabela(registro: Acompanhamento) -> dict[str, Any]:
    """Converts a record into the row shown in the list."""
    return {
        "Orçamento": registro.orcnum,
        "Cliente": registro.cliente,
        "Vendedor": registro.vendedor,
        "UF": registro.uf,
        "Aberta em": _dia(registro.data_abertura),
        "SitCode": registro.sitcode_wbc,
        "Rev.": registro.revisao_wbc,
        "Situação": registro.status.rotulo,
        "Cotação": registro.cotacao_docnum or "",
        "Pedido": registro.pedido_docnum or "",
        "Valor do pedido": _moeda(registro.pedido_valor),
        "Última verificação": _momento(registro.ultima_verificacao),
    }


def _moeda(valor: Decimal | None) -> str:
    if valor is None:
        return ""
    inteiro, _, centavos = f"{valor:.2f}".partition(".")
    with_milhar = f"{int(inteiro):,}".replace(",", ".")
    return f"R$ {with_milhar},{centavos}"


def _momento(valor: datetime | None) -> str:
    return valor.strftime("%d/%m/%Y %H:%M") if valor else "—"


def _dia(valor: date | None) -> str:
    """Rows older than the column have no date — and say so."""
    return valor.strftime("%d/%m/%Y") if valor else "—"


def resumo_de_execucao(execucao: Execucao) -> dict[str, Any]:
    duracao = execucao.duracao_segundos
    return {
        "Início": _momento(execucao.inicio),
        "Duração": f"{duracao:.1f}s" if duracao is not None else "em andamento",
        "Situação": {
            StatusExecucao.EM_ANDAMENTO: "Em andamento",
            StatusExecucao.CONCLUIDA: "Concluída",
            StatusExecucao.FALHOU: "Falhou",
        }[execucao.status],
        "Processados": execucao.processados,
        "Sucessos": execucao.sucessos,
        "Erros": execucao.erros,
        # Desde que a janela virou pedido, "este ciclo escreveu 600 documentos"
        # só faz sentido ao lado de "ele rodou com 12 meses". `None` é execução
        # anterior à coluna — e "—" diz isso melhor do que um 6 inventado.
        "Janela": f"{execucao.meses_da_janela} meses" if execucao.meses_da_janela else "—",
    }
