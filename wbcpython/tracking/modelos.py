"""Data model of the tracking database.

This database belongs to the integration itself, separate from the WBC and SAP, and holds all processing state.
The separation is required: the WBC is read-only (Rule 5), so "this quote was already processed" cannot be
stored there; and the dashboard never queries SAP or the WBC directly (see `docs/wbc/ai_spec/00_index.md`), so
that opening a screen does not load the source systems.

Tables:

* `acompanhamento`: one row per quote, the current state;
* `eventos`: history of what happened to each quote (the legacy kept none of it);
* `execucoes`: history of worker runs, for audit and to tell whether the process is alive;
* `pedido_de_janela`: the single-row request for the extended window (`PedidoDeJanela`);
* `travas`: the worker's single-execution lock (`Trava`).
"""

from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from wbcpython.domain.janela import EstadoDaJanela


def _enum(tipo: type[enum.StrEnum], nome: str) -> Enum:
    """Enum column that returns the enum itself on read.

    Storing a plain `String` would bring the value back as `str`: comparisons with the enum would pass by equality
    but fail by identity, and the declared type (`Mapped[StatusIntegracao]`) would lie. `values_callable` stores the
    readable *value* ('cotacao_criada') instead of the member name.
    """
    return Enum(
        tipo,
        name=nome,
        native_enum=False,
        length=32,
        values_callable=lambda e: [m.value for m in e],
    )


class Base(DeclarativeBase):
    pass


class StatusIntegracao(enum.StrEnum):
    """Integration status of a quote, for display on the dashboard."""

    PENDENTE = "pendente"
    SEM_ACAO = "sem_acao"
    COTACAO_CRIADA = "cotacao_criada"
    COTACAO_ATUALIZADA = "cotacao_atualizada"
    PEDIDO_CRIADO = "pedido_criado"
    PEDIDO_ATUALIZADO = "pedido_atualizado"
    ENCERRADA = "encerrada"
    ERRO = "erro"

    @property
    def rotulo(self) -> str:
        return {
            StatusIntegracao.PENDENTE: "Pendente",
            StatusIntegracao.SEM_ACAO: "Sem ação",
            StatusIntegracao.COTACAO_CRIADA: "Cotação criada",
            StatusIntegracao.COTACAO_ATUALIZADA: "Cotação atualizada",
            StatusIntegracao.PEDIDO_CRIADO: "Pedido criado",
            StatusIntegracao.PEDIDO_ATUALIZADO: "Pedido atualizado",
            StatusIntegracao.ENCERRADA: "Encerrada",
            StatusIntegracao.ERRO: "Erro",
        }[self]

class TipoEvento(enum.StrEnum):
    DECISAO = "decisao"
    ACAO = "acao"
    ERRO = "erro"
    REPROCESSAMENTO = "reprocessamento"


class StatusExecucao(enum.StrEnum):
    EM_ANDAMENTO = "em_andamento"
    CONCLUIDA = "concluida"
    FALHOU = "falhou"


class Acompanhamento(Base):
    """Current integration state of a quote."""

    __tablename__ = "acompanhamento"

    orcnum: Mapped[str] = mapped_column(String(32), primary_key=True)
    """Número do orçamento no WBC — a chave de negócio de toda a integração."""

    oppr_id: Mapped[int | None] = mapped_column(Integer, default=None)
    cliente: Mapped[str] = mapped_column(String(200), default="")
    vendedor: Mapped[str] = mapped_column(String(32), default="")
    municipio: Mapped[str] = mapped_column(String(120), default="")
    uf: Mapped[str] = mapped_column(String(8), default="")

    #: `OOPR.OpenDate` — a data que define a janela do worker.
    #:
    #: Está aqui para que o painel possa aplicar **o mesmo** corte que o ciclo
    #: aplica. Sem ela o acompanhamento vira um arquivo que nunca esquece: ao
    #: encolher a janela de 6 para 3 meses, 167 orçamentos de maio continuaram
    #: aparecendo no painel como se o worker ainda os olhasse — e ele não olha.
    data_abertura: Mapped[date | None] = mapped_column(Date, default=None)

    sitcode_wbc: Mapped[int] = mapped_column(Integer, default=0)
    revisao_wbc: Mapped[str] = mapped_column(String(8), default="")
    sitcode_sap: Mapped[str] = mapped_column(String(8), default="")

    status: Mapped[StatusIntegracao] = mapped_column(
        _enum(StatusIntegracao, "status_integracao"), default=StatusIntegracao.PENDENTE
    )
    regra_aplicada: Mapped[str] = mapped_column(String(120), default="")

    cotacao_docentry: Mapped[int | None] = mapped_column(Integer, default=None)
    cotacao_docnum: Mapped[int | None] = mapped_column(Integer, default=None)
    cotacao_valor: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), default=None)

    pedido_docentry: Mapped[int | None] = mapped_column(Integer, default=None)
    pedido_docnum: Mapped[int | None] = mapped_column(Integer, default=None)
    pedido_valor: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), default=None)

    ultima_verificacao: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    ultimo_erro: Mapped[str] = mapped_column(Text, default="")

    criado_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )

    eventos: Mapped[list[Evento]] = relationship(
        back_populates="acompanhamento",
        cascade="all, delete-orphan",
        order_by="Evento.momento.desc()",
    )

    __table_args__ = (
        Index("ix_acompanhamento_status", "status"),
        Index("ix_acompanhamento_verificacao", "ultima_verificacao"),
    )

    @property
    def tem_erro(self) -> bool:
        return self.status is StatusIntegracao.ERRO


class Evento(Base):
    """Something that happened to a quote.

    The legacy recorded none of this (`AddLog` had its body commented out). Without history there is no way to
    answer "why did this quote not become an order?", the question that motivates the dashboard.
    """

    __tablename__ = "eventos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    orcnum: Mapped[str] = mapped_column(
        String(32), ForeignKey("acompanhamento.orcnum", ondelete="CASCADE")
    )
    momento: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    tipo: Mapped[TipoEvento] = mapped_column(
        _enum(TipoEvento, "tipo_evento"), default=TipoEvento.DECISAO
    )
    regra: Mapped[str] = mapped_column(String(120), default="")
    mensagem: Mapped[str] = mapped_column(Text, default="")
    detalhes: Mapped[str] = mapped_column(Text, default="")

    acompanhamento: Mapped[Acompanhamento] = relationship(back_populates="eventos")

    __table_args__ = (Index("ix_eventos_orcnum_momento", "orcnum", "momento"),)


class Execucao(Base):
    """One worker run.

    Answers two questions the legacy left open: "did the process run?" and "what did it do?".
    """

    __tablename__ = "execucoes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    inicio: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    fim: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    status: Mapped[StatusExecucao] = mapped_column(
        _enum(StatusExecucao, "status_execucao"), default=StatusExecucao.EM_ANDAMENTO
    )
    processados: Mapped[int] = mapped_column(Integer, default=0)
    sucessos: Mapped[int] = mapped_column(Integer, default=0)
    erros: Mapped[int] = mapped_column(Integer, default=0)
    detalhe: Mapped[str] = mapped_column(Text, default="")

    #: Com que janela e com que teto esta execução rodou.
    #:
    #: Anuláveis porque a base já existia antes da janela sob demanda, e o
    #: `_acrescentar_colunas_novas` só acrescenta coluna anulável — execuções
    #: antigas ficam com `None`, que aqui quer dizer "não registrado", e não 0.
    #:
    #: Registradas porque, desde que a janela mudou de constante para pedido, "o
    #: ciclo das 14h37 escreveu 600 documentos" só faz sentido ao lado de "ele
    #: rodou com 12 meses". Sem isso, a auditoria de uma leva grande obriga a
    #: cruzar o log com o estado atual do pedido — que já mudou.
    meses_da_janela: Mapped[int | None] = mapped_column(Integer, default=None)
    teto_de_escrita: Mapped[int | None] = mapped_column(Integer, default=None)

    @property
    def duracao_segundos(self) -> float | None:
        if self.fim is None:
            return None
        return (self.fim - self.inicio).total_seconds()


class PedidoDeJanela(Base):
    """The extended-window request: a single row, always `id=1` (`LINHA_UNICA`).

    Why in the database and not in a variable: the panel and the worker are **separate processes** (distinct NSSM
    services on the .11) with no shared memory, and the tracking database is the only place both already touch;
    the `travas` table next to it coordinates the two the same way.

    Why not a file like `state/wbc_worker.stop`, the panel-to-worker signal precedent: that file carries one bit
    ("stop"). This needs a value with an owner, a date, an attempt counter and an answer deadline, and it must show
    on the screen, which reads the database, not the disk.

    A single row because the request is a global worker state, not a queue: two simultaneous requests would mean
    nothing (the next pass is one). The fixed `id` lets the primary key forbid a second row, instead of a code rule
    someone may forget to apply.
    """

    __tablename__ = "pedido_de_janela"

    #: Sempre `LINHA_UNICA`. Ver a docstring: é a chave primária que garante a
    #: unicidade, não uma convenção.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)

    estado: Mapped[EstadoDaJanela] = mapped_column(
        _enum(EstadoDaJanela, "estado_da_janela"), default=EstadoDaJanela.OCIOSO
    )

    #: A janela pedida, em meses. Sem significado quando o estado é `OCIOSO` —
    #: aí quem manda é o `MESES_DE_JANELA` da configuração.
    meses: Mapped[int] = mapped_column(Integer, default=0)

    pedido_por: Mapped[str] = mapped_column(String(120), default="")
    pedido_em: Mapped[datetime | None] = mapped_column(DateTime, default=None)

    #: Quantas oportunidades ficaram de fora quando o ciclo bateu no teto — o
    #: número que a pergunta na tela mostra.
    faltaram: Mapped[int] = mapped_column(Integer, default=0)

    #: Extended cycles that ended in error. Three in a row give the request back: a
    #: network error must not cost the request, but a persistent one must not become a
    #: heavy cycle every interval (`WORKER_INTERVAL_SECONDS`, 300 s by default).
    tentativas: Mapped[int] = mapped_column(Integer, default=0)

    #: Prazo da resposta, só em `AGUARDANDO`. `ARMADO` não expira de propósito:
    #: um pedido feito às 19h de sexta só é atendido na segunda, porque o worker
    #: não roda fora do expediente.
    expira_em: Mapped[datetime | None] = mapped_column(DateTime, default=None)

    #: Onde o último ciclo estendido parou, ou o erro que o derrubou. É o que
    #: sobra quando o pedido expira — sem isto, a expiração seria silenciosa.
    detalhe: Mapped[str] = mapped_column(Text, default="")

    atualizado_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class Trava(Base):
    """Single-execution lock of the worker.

    The legacy had none: overlapping runs would process the same quote at once and could create duplicate documents
    in SAP. The lock lives in the database (not in memory) so it works even if the worker runs on more than one
    machine.
    """

    __tablename__ = "travas"

    nome: Mapped[str] = mapped_column(String(64), primary_key=True)
    dono: Mapped[str] = mapped_column(String(200), default="")
    adquirida_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    expira_em: Mapped[datetime] = mapped_column(DateTime)
