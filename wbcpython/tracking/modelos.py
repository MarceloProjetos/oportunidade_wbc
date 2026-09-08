"""Modelo de dados do banco de acompanhamento.

Este banco é **da própria integração** — separado do WBC e do SAP. É ele que
alimenta o dashboard, e é aqui que mora todo estado de processamento.

Isso não é organização por gosto: o WBC é somente leitura (Regra 5), então
"este orçamento já foi processado" **não pode** ser gravado lá. E o dashboard
nunca consulta SAP nem WBC diretamente (`ai_spec/03_architecture.md`), para não
transformar cada abertura de tela em carga nos sistemas de origem.

Três tabelas, com papéis distintos:

* `acompanhamento` — uma linha por orçamento, o estado corrente;
* `eventos` — o histórico do que aconteceu com cada orçamento (o legado não
  guardava nada disso);
* `execucoes` — o histórico de execuções do worker, para auditoria e para
  saber se o processo está de pé.
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


def _enum(tipo: type[enum.StrEnum], nome: str) -> Enum:
    """Coluna de enumeração que devolve o próprio enum na leitura.

    Guardar como `String` faria o valor voltar do banco como `str` — comparações
    com o enum passariam por igualdade mas falhariam por identidade, e o tipo
    declarado (`Mapped[StatusIntegracao]`) seria mentira. `values_callable`
    grava o *valor* legível ('cotacao_criada') em vez do nome do membro.
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
    """Situação da integração de um orçamento, para exibição no dashboard."""

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

    @property
    def e_terminal(self) -> bool:
        return self in (StatusIntegracao.ENCERRADA,)


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
    """Estado corrente da integração de um orçamento."""

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
    """Uma coisa que aconteceu com um orçamento.

    O sistema legado não registrava nada disso — o `AddLog` estava com o corpo
    comentado. Sem histórico, não há como responder "por que este orçamento não
    virou pedido?", que é a pergunta que motiva o dashboard.
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
    """Uma execução do worker.

    Existe para responder duas perguntas que o legado deixava sem resposta: "o
    processo rodou?" e "o que ele fez?".
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

    @property
    def duracao_segundos(self) -> float | None:
        if self.fim is None:
            return None
        return (self.fim - self.inicio).total_seconds()


class Trava(Base):
    """Trava de execução única do worker.

    O sistema legado não tem nada disso: duas execuções sobrepostas processariam
    o mesmo orçamento ao mesmo tempo e poderiam criar documentos duplicados no
    SAP. A trava vive no banco (e não em memória) para funcionar mesmo se o
    worker rodar em mais de uma máquina.
    """

    __tablename__ = "travas"

    nome: Mapped[str] = mapped_column(String(64), primary_key=True)
    dono: Mapped[str] = mapped_column(String(200), default="")
    adquirida_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    expira_em: Mapped[datetime] = mapped_column(DateTime)
