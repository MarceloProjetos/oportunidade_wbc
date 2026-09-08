"""Camada de dados do dashboard.

Separada da interface de propósito: assim a lógica de apresentação
(agregações, rótulos, formatação) é testável sem subir servidor nenhum.

**O dashboard nunca consulta SAP nem WBC** — só o banco de acompanhamento
(`ai_spec/03_architecture.md`). Isso mantém a tela rápida e evita que abrir o
painel vire carga nos sistemas de origem.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from wbcpython.tracking import (
    Acompanhamento,
    Execucao,
    RepositorioTracking,
    StatusExecucao,
    StatusIntegracao,
    TipoEvento,
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
    """Um dos números do topo, visto como filtro da lista.

    Cada indicador do painel é uma pergunta ("quais deram erro?"), e o número
    sozinho não responde — só diz quantos são. O recorte é a mesma definição
    usada para **contar**, reaproveitada para **listar**: é o que garante que o
    indicador dizendo 8 abra uma lista de 8, e não de 9.
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
    """Um recorte desconhecido cai em "Avaliados", em vez de estourar.

    O identificador vem do endereço, que pode ter sido colado, editado à mão ou
    guardado num favorito de uma versão anterior do painel. Mostrar tudo é o
    pior caso aceitável; uma tela de erro por causa de um parâmetro não é.
    """
    return _POR_ID.get((identificador or "").strip(), RECORTE_PADRAO)


def aplicar(registros: list[Acompanhamento], corte: Recorte) -> list[Acompanhamento]:
    """Filtra em Python, e não no SQL, de propósito.

    Os indicadores contam sobre **este mesmo conjunto** de linhas (o que a
    janela trouxe, até o teto). Recortar aqui garante que o número no
    indicador e a contagem da lista sejam o mesmo número. Uma consulta separada
    com `WHERE` acertaria hoje e divergiria no dia em que o teto de linhas
    entrasse em jogo — e a divergência apareceria como "o painel está mentindo".
    """
    if corte is RECORTE_PADRAO:
        return registros
    return [registro for registro in registros if corte.cobre(registro)]


@dataclass(frozen=True, slots=True)
class Kpis:
    """Números do topo do painel.

    `total` conta o que o ciclo **avaliou**; `com_acao`, o que ele tocou. A
    distinção existe porque o worker lê a janela inteira de propósito — sem ela,
    o painel anunciava centenas de orçamentos "integrados" quando o ciclo não
    havia mexido em nenhum.
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
        """Semáforo simples, para leitura rápida.

        Os limites são deliberadamente conservadores: qualquer erro já tira do
        verde, porque num fluxo que cria documentos financeiros um erro isolado
        merece atenção — não é ruído estatístico.
        """
        if self.total == 0:
            return "sem_dados"
        if self.taxa_de_erro >= 10:
            return "critico"
        if self.com_erro > 0:
            return "atencao"
        return "ok"


def calcular_kpis(registros: list[Acompanhamento]) -> Kpis:
    """Agrega os indicadores a partir das linhas de acompanhamento."""
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
    """Converte um registro na linha exibida na lista."""
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
    """Linhas anteriores à coluna existir não têm data — e dizem isso."""
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
    }


def registrar_reprocessamento(
    tracking: RepositorioTracking, orcnum: str, *, solicitante: str
) -> None:
    """Marca um pedido manual de reprocessamento.

    Auditoria não é opcional aqui: uma ação manual que resulta em documentos
    criados no SAP precisa deixar rastro de **quem** pediu e **quando** — é o
    tipo de pergunta que aparece semanas depois.
    """
    tracking.registrar_evento(
        orcnum,
        tipo=TipoEvento.REPROCESSAMENTO,
        mensagem=f"Reprocessamento solicitado manualmente por {solicitante}.",
        detalhes={"solicitante": solicitante},
    )
