"""Access to the tracking database.

Unlike the WBC and HANA repositories, **this database is writable**: it is the only place where the integration
stores its own state. The project's safety locks do not apply here; they protect production SAP and the WBC, and
this database is neither.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Engine, create_engine, delete, event, func, inspect, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from wbcpython.domain import janela as jn
from wbcpython.domain.janela import EstadoDaJanela
from wbcpython.tracking.modelos import (
    Acompanhamento,
    Base,
    Evento,
    Execucao,
    PedidoDeJanela,
    StatusExecucao,
    StatusIntegracao,
    TipoEvento,
    Trava,
)

logger = logging.getLogger(__name__)

TRAVA_WORKER = "worker_integracao"

#: A única linha de `pedido_de_janela` — ver a docstring do modelo.
LINHA_UNICA = 1


def _tornar_ocioso(pedido: PedidoDeJanela, agora: datetime) -> None:
    """Return the request to the default (`OCIOSO`), preserving `detalhe` and `meses`.

    `meses` is left behind on purpose: the screen shows it as "the last extended window was N months". It has no
    effect in `OCIOSO`: the window is then decided by the `MESES_DE_JANELA` setting.
    """
    pedido.estado = EstadoDaJanela.OCIOSO
    pedido.faltaram = 0
    pedido.tentativas = 0
    pedido.expira_em = None
    pedido.atualizado_em = agora


def _minutos(pedido: PedidoDeJanela) -> str:
    """The deadline length (`expira_em` minus `atualizado_em`) as text, for the log line."""
    if pedido.expira_em is None or pedido.atualizado_em is None:
        return "prazo"
    minutos = round((pedido.expira_em - pedido.atualizado_em).total_seconds() / 60)
    return f"{minutos} min"


#: Seconds between two renewals of the execution lock (see `trava_de_execucao`).
INTERVALO_DE_RENOVACAO_DA_TRAVA = 60.0


def _sqlite_em_wal(conexao: Any, _registro: Any) -> None:
    """WAL + a busy timeout on every SQLite connection of the tracking DB.

    01/10/2026 review: two transactions per quote in rollback-journal mode cost 8.4 s of the
    ~18 s cycle (1,880 quotes, measured); WAL brings it to 3.1 s, and readers (painel, the
    /status check in ``mode=ro`` — tested) no longer block the writer. ``synchronous`` stays
    FULL (the default): NORMAL gained 0.1 s and could lose the last commit on a power cut, and
    this DB is part of what keeps a quote from getting a duplicate document. The mode is
    persistent in the file; if switching fails (DB busy), the worker still starts.
    """
    cursor = conexao.cursor()
    try:
        cursor.execute("PRAGMA busy_timeout = 5000")
        cursor.execute("PRAGMA journal_mode = WAL")
    except Exception as exc:  # noqa: BLE001 - a journal mode must never stop the worker
        logger.warning("Acompanhamento: não consegui ligar o WAL no SQLite (%s); segue no modo atual.", exc)
    finally:
        cursor.close()


class TravaNaoObtida(RuntimeError):
    """Another worker run is already in progress."""


def identidade_do_processo() -> str:
    """Identify who holds the lock (`host:pid`), so a stuck lock can be diagnosed.

    Without it a stuck lock only shows "busy"; with host and PID you can tell which process holds it and whether
    it still exists.
    """
    return f"{socket.gethostname()}:{os.getpid()}"


class RepositorioTracking:
    """Repository of the tracking database."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._sessao = sessionmaker(engine, expire_on_commit=False)

    @classmethod
    def a_partir_da_url(cls, url: str, *, criar_tabelas: bool = True) -> RepositorioTracking:
        engine = create_engine(url, pool_pre_ping=True)
        if engine.dialect.name == "sqlite":
            event.listen(engine, "connect", _sqlite_em_wal)
        repo = cls(engine)
        if criar_tabelas:
            repo.criar_tabelas()
        return repo

    def criar_tabelas(self) -> None:
        Base.metadata.create_all(self._engine)
        self._acrescentar_colunas_novas()

    def _acrescentar_colunas_novas(self) -> None:
        """Add columns the model gained after the database already existed.

        `create_all` creates missing tables but does **not** alter existing ones: on a tracking database in use, a new
        model column becomes `OperationalError: no such column` on the first query. The tracking data is operational
        history (who decided what, and when), so recreating the database to follow the model is not an option.

        Only `ADD COLUMN` of columns that are **nullable and have no server default**, the one change SQLite and
        PostgreSQL accept without rewriting the table and that cannot lose data. Anything deeper (rename, drop, type
        change) is left out on purpose: that is where a silent automatic migration destroys history. A required column
        that is missing is logged as an error and skipped (add it manually with `ALTER TABLE`).
        """
        inspetor = inspect(self._engine)
        for tabela in Base.metadata.sorted_tables:
            if not inspetor.has_table(tabela.name):
                continue
            existentes = {c["name"] for c in inspetor.get_columns(tabela.name)}
            for coluna in tabela.columns:
                if coluna.name in existentes:
                    continue
                if not coluna.nullable:
                    # Falha em voz alta em vez de deixar a consulta estourar
                    # depois com "no such column", que não diz o que fazer.
                    logger.error(
                        "Acompanhamento: a coluna %s.%s é obrigatória e não existe na base. "
                        "Acrescente-a manualmente (ALTER TABLE) antes de rodar o ciclo.",
                        tabela.name,
                        coluna.name,
                    )
                    continue
                tipo = coluna.type.compile(self._engine.dialect)
                with self._engine.begin() as conexao:
                    conexao.execute(
                        text(f'ALTER TABLE "{tabela.name}" ADD COLUMN "{coluna.name}" {tipo}')
                    )
                logger.info(
                    "Acompanhamento: coluna %s.%s criada na base existente.",
                    tabela.name,
                    coluna.name,
                )

    @contextmanager
    def sessao(self) -> Iterator[Session]:
        with self._sessao() as s:
            yield s

    # --------------------------------------------------------- acompanhamento

    def registrar_verificacao(
        self,
        orcnum: str,
        *,
        oppr_id: int | None = None,
        cliente: str = "",
        vendedor: str = "",
        municipio: str = "",
        uf: str = "",
        sitcode_wbc: int = 0,
        revisao_wbc: str = "",
        sitcode_sap: str = "",
        status: StatusIntegracao | None = None,
        regra: str = "",
        data_abertura: date | None = None,
    ) -> Acompanhamento:
        """Create or update the tracking row of the quote.

        Only overwrites the fields that were informed: a check that does not know the client name must not erase what
        was already there.
        """
        with self._sessao() as s, s.begin():
            registro = s.get(Acompanhamento, orcnum)
            if registro is None:
                registro = Acompanhamento(orcnum=orcnum)
                s.add(registro)

            if oppr_id is not None:
                registro.oppr_id = oppr_id
            if cliente:
                registro.cliente = cliente
            if vendedor:
                registro.vendedor = vendedor
            if municipio:
                registro.municipio = municipio
            if uf:
                registro.uf = uf
            if data_abertura is not None:
                registro.data_abertura = data_abertura
            if sitcode_wbc:
                registro.sitcode_wbc = sitcode_wbc
            if revisao_wbc:
                registro.revisao_wbc = revisao_wbc
            if sitcode_sap:
                registro.sitcode_sap = sitcode_sap
            if status is not None:
                registro.status = status
                if status is not StatusIntegracao.ERRO:
                    registro.ultimo_erro = ""
            if regra:
                registro.regra_aplicada = regra

            registro.ultima_verificacao = datetime.now()
            s.flush()
            return registro

    def registrar_documento(
        self,
        orcnum: str,
        *,
        tipo: str,
        doc_entry: int | None,
        doc_num: int | None = None,
        valor: Decimal | None = None,
    ) -> None:
        """Store the SAP document generated for the quote.

        `tipo` is "cotacao" or "pedido"; anything else raises `ValueError`.
        """
        if tipo not in ("cotacao", "pedido"):
            raise ValueError(f"tipo de documento desconhecido: {tipo!r}")

        with self._sessao() as s, s.begin():
            registro = s.get(Acompanhamento, orcnum)
            if registro is None:
                registro = Acompanhamento(orcnum=orcnum)
                s.add(registro)
            setattr(registro, f"{tipo}_docentry", doc_entry)
            if doc_num is not None:
                setattr(registro, f"{tipo}_docnum", doc_num)
            if valor is not None:
                setattr(registro, f"{tipo}_valor", valor)

    def registrar_erro(self, orcnum: str, mensagem: str, *, detalhes: str = "") -> None:
        with self._sessao() as s, s.begin():
            registro = s.get(Acompanhamento, orcnum)
            if registro is None:
                registro = Acompanhamento(orcnum=orcnum)
                s.add(registro)
            registro.status = StatusIntegracao.ERRO
            registro.ultimo_erro = mensagem
            registro.ultima_verificacao = datetime.now()
            s.add(
                Evento(
                    orcnum=orcnum,
                    tipo=TipoEvento.ERRO,
                    mensagem=mensagem,
                    detalhes=detalhes,
                )
            )

    def registrar_evento(
        self,
        orcnum: str,
        *,
        tipo: TipoEvento = TipoEvento.DECISAO,
        regra: str = "",
        mensagem: str = "",
        detalhes: dict[str, Any] | None = None,
    ) -> bool:
        """Record the event. Returns `False` when a DECISAO repeats the last one and so was not recorded.

        A decision equal to the quote's last one ("revision frozen, nothing to do") is not a new happening: it is the
        cycle passing again. Recording it every cycle cost 934,634 rows in 6 days of production, 99.4% of the table,
        of which only 12,559 were real changes (measured on 08/09/2026, with 1,682 opportunities and a 3-minute cycle,
        the interval at that time). What stays is the **first** time the decision came into effect;
        `acompanhamento.ultima_verificacao` says the cycle keeps looking. Action, error and reprocessing events are
        always recorded: each one is a happening.
        """
        detalhes_json = (
            json.dumps(detalhes, ensure_ascii=False, default=str) if detalhes else ""
        )
        with self._sessao() as s, s.begin():
            if s.get(Acompanhamento, orcnum) is None:
                s.add(Acompanhamento(orcnum=orcnum))
                s.flush()
            if tipo == TipoEvento.DECISAO:
                ultimo = s.scalars(
                    select(Evento)
                    .where(Evento.orcnum == orcnum)
                    .order_by(Evento.id.desc())
                    .limit(1)
                ).first()
                if (
                    ultimo is not None
                    and ultimo.tipo == TipoEvento.DECISAO
                    and ultimo.regra == regra
                    and ultimo.mensagem == mensagem
                    and ultimo.detalhes == detalhes_json
                ):
                    return False
            s.add(
                Evento(
                    orcnum=orcnum,
                    tipo=tipo,
                    regra=regra,
                    mensagem=mensagem,
                    detalhes=detalhes_json,
                )
            )
            return True

    def faxina_de_eventos(self, *, dias: int) -> int:
        """Delete DECISAO events older than `dias` days. Returns how many were deleted.

        Action, error and reprocessing events **stay**: they are the history that answers "why did this quote become
        (or not become) an order?". The repeated decision is what filled the database; with `registrar_evento` no longer
        repeating it, the cleanup handles what was already recorded and what still really changes. On SQLite it
        compacts the file when something was deleted (a `DELETE` alone does not give space back to the disk).
        Raises `ValueError` if `dias` is negative.
        """
        if dias < 0:
            raise ValueError("dias não pode ser negativo")
        corte = datetime.now() - timedelta(days=dias)
        with self._sessao() as s, s.begin():
            resultado = s.execute(
                delete(Evento).where(Evento.tipo == TipoEvento.DECISAO, Evento.momento < corte)
            )
            apagados = int(resultado.rowcount or 0)
        if apagados and self._engine.dialect.name == "sqlite":
            with self._engine.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
                c.execute(text("VACUUM"))
        return apagados

    # ---------------------------------------------------------------- leitura

    def obter(self, orcnum: str) -> Acompanhamento | None:
        with self._sessao() as s:
            return s.get(Acompanhamento, orcnum)

    def preencher_datas_de_abertura(self, datas: dict[str, Any]) -> int:
        """Fill `data_abertura` where it is null. Returns how many rows changed.

        Only fills what is empty: the date coming from the company never overwrites one already stored by the cycle.
        See `cli._cmd_datas_de_abertura` for why this exists.
        """
        preenchidas = 0
        with self._sessao() as s, s.begin():
            for orcnum, valor in datas.items():
                data = valor.date() if isinstance(valor, datetime) else valor
                if not isinstance(data, date):
                    continue
                registro = s.get(Acompanhamento, orcnum)
                if registro is None or registro.data_abertura is not None:
                    continue
                registro.data_abertura = data
                preenchidas += 1
        return preenchidas

    def listar(
        self,
        *,
        status: StatusIntegracao | None = None,
        busca: str = "",
        limite: int = 200,
        aberto_desde: date | None = None,
    ) -> list[Acompanhamento]:
        """List tracking rows, newest update first, at most `limite`.

        `aberto_desde` applies the **same** window as the worker (`OOPR.OpenDate`). Rows without `data_abertura` are the
        ones from before the column existed; they pass the filter on purpose: vanishing from the panel for lack of data
        that was never stored would be worse than showing up, since the operator would read it as "these quotes do not
        exist" instead of "the date is unknown".
        """
        consulta = select(Acompanhamento)
        if status is not None:
            consulta = consulta.where(Acompanhamento.status == status)
        if aberto_desde is not None:
            consulta = consulta.where(
                (Acompanhamento.data_abertura >= aberto_desde)
                | (Acompanhamento.data_abertura.is_(None))
            )
        if busca:
            padrao = f"%{busca}%"
            consulta = consulta.where(
                Acompanhamento.orcnum.like(padrao) | Acompanhamento.cliente.like(padrao)
            )
        consulta = consulta.order_by(Acompanhamento.atualizado_em.desc()).limit(limite)
        with self._sessao() as s:
            return list(s.scalars(consulta))

    def eventos(self, orcnum: str, *, limite: int = 100) -> list[Evento]:
        consulta = (
            select(Evento)
            .where(Evento.orcnum == orcnum)
            .order_by(Evento.momento.desc(), Evento.id.desc())
            .limit(limite)
        )
        with self._sessao() as s:
            return list(s.scalars(consulta))

    def contagem_por_status(self) -> dict[StatusIntegracao, int]:
        consulta = select(Acompanhamento.status, func.count()).group_by(Acompanhamento.status)
        with self._sessao() as s:
            return {StatusIntegracao(status): total for status, total in s.execute(consulta)}

    # -------------------------------------------------------------- execuções

    def iniciar_execucao(self) -> int:
        with self._sessao() as s, s.begin():
            execucao = Execucao()
            s.add(execucao)
            s.flush()
            return execucao.id

    def finalizar_execucao(
        self,
        execucao_id: int,
        *,
        processados: int,
        sucessos: int,
        erros: int,
        status: StatusExecucao = StatusExecucao.CONCLUIDA,
        detalhe: str = "",
        meses_da_janela: int | None = None,
        teto_de_escrita: int | None = None,
    ) -> None:
        with self._sessao() as s, s.begin():
            execucao = s.get(Execucao, execucao_id)
            if execucao is None:
                return
            execucao.fim = datetime.now()
            execucao.processados = processados
            execucao.sucessos = sucessos
            execucao.erros = erros
            execucao.status = status
            execucao.detalhe = detalhe
            # `None` é "não informado" e preserva o que já estava lá: quem
            # chamar sem estes campos (um teste antigo, uma rota que só corrige
            # o resumo) não apaga a auditoria da janela.
            if meses_da_janela is not None:
                execucao.meses_da_janela = meses_da_janela
            if teto_de_escrita is not None:
                execucao.teto_de_escrita = teto_de_escrita

    def ultimas_execucoes(self, *, limite: int = 20) -> list[Execucao]:
        consulta = select(Execucao).order_by(Execucao.inicio.desc()).limit(limite)
        with self._sessao() as s:
            return list(s.scalars(consulta))

    def nomes_que_ja_pediram(self, *, limite: int = 20) -> list[str]:
        """Who armed the window before, most recent first.

        It is the fallback of the card when the OrcaView profile list does not come (local API down): the field still
        offers names, only those that already went through here. Empty on first use, and improves by itself.

        It comes from the **events**, not from a new column: `pedido_de_janela` is a single row and only keeps the last
        request. The real history is already in the `detalhes` of each reprocessing event (`solicitante` key); using it
        costs one JSON parse and no new table.
        """
        consulta = (
            select(Evento.detalhes)
            .where(Evento.tipo == TipoEvento.REPROCESSAMENTO, Evento.detalhes != "")
            .order_by(Evento.id.desc())
            .limit(limite * 10)
        )
        vistos: dict[str, None] = {}
        with self._sessao() as s:
            for bruto in s.scalars(consulta):
                try:
                    nome = (json.loads(bruto) or {}).get("solicitante") or ""
                except (ValueError, TypeError):
                    # Evento antigo, ou `detalhes` que não é objeto. Não é erro:
                    # nem todo reprocessamento foi gravado com solicitante.
                    continue
                nome = nome.strip()
                if nome and nome not in vistos:
                    vistos[nome] = None
                    if len(vistos) >= limite:
                        break
        return list(vistos)

    # ------------------------------------------------- janela sob demanda

    def janela_pedida(self, *, agora: datetime | None = None) -> PedidoDeJanela:
        """The current window request, with expiry already applied.

        Expiry is lazy, applied here on read, because nothing runs between two cycles to watch the clock. It is the same
        design as the expired lock in `_adquirir_trava`: whoever arrives after the deadline cleans up. The panel and the
        worker both call this, so either can be the trigger, and the screen never shows a question that already expired.
        """
        agora = agora or datetime.now()
        with self._sessao() as s, s.begin():
            pedido = s.get(PedidoDeJanela, LINHA_UNICA)
            if pedido is None:
                pedido = PedidoDeJanela(id=LINHA_UNICA, estado=EstadoDaJanela.OCIOSO)
                s.add(pedido)
                s.flush()
                return pedido
            if jn.expirou(pedido.estado, pedido.expira_em, agora=agora):
                # A frase de onde o ciclo parou é preservada e recebe o aviso —
                # expirar calado apagaria justamente o que alguém precisaria ler
                # para decidir se rearma.
                logger.warning(
                    "Janela de %d meses devolvida ao padrão: ninguém respondeu em %s. %s",
                    pedido.meses,
                    _minutos(pedido),
                    pedido.detalhe or "(sem registro do ponto de parada)",
                )
                pedido.detalhe = (
                    f"Expirou sem resposta. {pedido.detalhe}".strip()
                    if pedido.detalhe
                    else "Expirou sem resposta."
                )
                _tornar_ocioso(pedido, agora)
            return pedido

    def armar_janela(self, meses: int, *, por: str, agora: datetime | None = None) -> PedidoDeJanela:
        """Arm the extended window for the **next** pass of the cycle.

        Resets `tentativas` and `faltaram`: a new request does not inherit the previous one's score, otherwise
        yesterday's third error would give today's request back on its first stumble.
        """
        agora = agora or datetime.now()
        with self._sessao() as s, s.begin():
            pedido = s.get(PedidoDeJanela, LINHA_UNICA) or PedidoDeJanela(id=LINHA_UNICA)
            pedido.estado = EstadoDaJanela.ARMADO
            pedido.meses = meses
            pedido.pedido_por = por
            pedido.pedido_em = agora
            pedido.faltaram = 0
            pedido.tentativas = 0
            pedido.expira_em = None
            pedido.detalhe = ""
            pedido.atualizado_em = agora
            s.add(pedido)
            s.flush()
            return pedido

    def limpar_janela(self, *, motivo: str, agora: datetime | None = None) -> PedidoDeJanela:
        """Return the window to the default (`OCIOSO`), recording why in `detalhe`.

        Called from three places that are the same thing seen from different angles: the cycle fulfilled the request
        (`host.worker`), the user gave up (CLI and panel), or the worker restarted. A fourth way out, the attempts
        running out, goes through `registrar_erro_da_janela`. All end in `OCIOSO`, which guarantees the wide window is
        never left on by forgetfulness.
        """
        agora = agora or datetime.now()
        with self._sessao() as s, s.begin():
            pedido = s.get(PedidoDeJanela, LINHA_UNICA) or PedidoDeJanela(id=LINHA_UNICA)
            pedido.detalhe = motivo
            _tornar_ocioso(pedido, agora)
            s.add(pedido)
            s.flush()
            return pedido

    def janela_aguardando_resposta(
        self,
        *,
        faltaram: int,
        detalhe: str,
        espera: timedelta,
        agora: datetime | None = None,
    ) -> PedidoDeJanela:
        """The extended cycle hit the ceiling: store the stopping point and ask.

        While it stays here, the automatic cycles go back to the default: the worker's `_janela_do_ciclo` only sees an
        extended window in `ARMADO`. Without that, the worker would keep sweeping the wide window every interval while
        the question waits for an answer. `expira_em` is set to `agora + espera`.
        """
        agora = agora or datetime.now()
        with self._sessao() as s, s.begin():
            pedido = s.get(PedidoDeJanela, LINHA_UNICA) or PedidoDeJanela(id=LINHA_UNICA)
            pedido.estado = EstadoDaJanela.AGUARDANDO
            pedido.faltaram = faltaram
            pedido.detalhe = detalhe
            pedido.tentativas = 0
            pedido.expira_em = agora + espera
            pedido.atualizado_em = agora
            s.add(pedido)
            s.flush()
            return pedido

    def registrar_erro_da_janela(
        self,
        *,
        detalhe: str,
        limite: int,
        agora: datetime | None = None,
    ) -> PedidoDeJanela:
        """An extended cycle failed: count the attempt and decide whether to give the request back.

        An error does **not** consume the request: a network drop in the middle of the cycle must not cost the
        request of whoever is waiting. But a repeating error must not become a heavy cycle every worker interval:
        at `limite` attempts the window returns to the default with the error recorded.
        """
        agora = agora or datetime.now()
        with self._sessao() as s, s.begin():
            pedido = s.get(PedidoDeJanela, LINHA_UNICA) or PedidoDeJanela(id=LINHA_UNICA)
            pedido.tentativas += 1
            pedido.detalhe = detalhe
            pedido.atualizado_em = agora
            if pedido.tentativas >= limite:
                logger.warning(
                    "Janela de %d meses devolvida ao padrão após %d tentativa(s) com erro: %s",
                    pedido.meses,
                    pedido.tentativas,
                    detalhe,
                )
                tentativas = pedido.tentativas
                _tornar_ocioso(pedido, agora)
                pedido.detalhe = f"Devolvida após {tentativas} tentativa(s) com erro. {detalhe}"
            s.add(pedido)
            s.flush()
            return pedido

    # ----------------------------------------------------------------- trava

    @contextmanager
    def trava_de_execucao(
        self, *, validade: timedelta = timedelta(minutes=30), nome: str = TRAVA_WORKER
    ) -> Iterator[Callable[[], None]]:
        """Guarantee single execution of the worker.

        The lock has a validity: if a process dies without releasing it, it expires by itself and the next run takes
        over. Without that, a crashed worker would leave the integration stopped forever. Raises `TravaNaoObtida` when
        another run holds an unexpired lock.

        Yields ``renovar``: called between quotes, it pushes ``expira_em`` forward (at most once a minute, one cheap
        UPDATE). 01/10/2026 review: the lock was never renewed, so a long cycle (an extended window allows 1,800 writes)
        could outlive its 30 min, and the next process (now also the painel's per-quote cycle, which waits for the lock)
        would take the "expired" lock and run over the same snapshot at the same time.
        """
        dono = identidade_do_processo()
        self._adquirir_trava(nome, dono, validade)
        ultima = [time.monotonic()]

        def renovar() -> None:
            if time.monotonic() - ultima[0] < INTERVALO_DE_RENOVACAO_DA_TRAVA:
                return
            with self._sessao() as s, s.begin():
                s.execute(
                    update(Trava)
                    .where(Trava.nome == nome, Trava.dono == dono)
                    .values(expira_em=datetime.now() + validade)
                )
            ultima[0] = time.monotonic()

        try:
            yield renovar
        finally:
            with self._sessao() as s, s.begin():
                s.execute(delete(Trava).where(Trava.nome == nome, Trava.dono == dono))

    def _adquirir_trava(self, nome: str, dono: str, validade: timedelta) -> None:
        """Acquire the lock letting the **primary key arbitrate**, not a SELECT.

        A "query whether it exists, otherwise insert" has a classic race: two processes query at the same time, both see
        empty and both insert. One gets an `IntegrityError` which, if unhandled, rises as a generic error instead of
        "an execution is already running", and may even bring down the winning worker as a side effect.

        Here the `INSERT` alone decides: whoever manages to write the primary key got the lock, and `IntegrityError` is
        the expected answer of the loser. If the existing lock is expired it is removed conditionally and one more
        attempt is made. Raises `TravaNaoObtida` otherwise.
        """
        agora = datetime.now()

        if self._tentar_inserir_trava(nome, dono, agora, validade):
            return

        # Perdeu a corrida ou já havia trava. Se a existente estiver vencida,
        # remove-a de forma condicional e tenta uma única vez mais.
        with self._sessao() as s:
            existente = s.get(Trava, nome)
            if existente is not None and existente.expira_em > agora:
                raise TravaNaoObtida(
                    f"Já existe uma execução em andamento (detida por {existente.dono}, "
                    f"expira em {existente.expira_em:%d/%m/%Y %H:%M:%S})."
                )
            dono_anterior = existente.dono if existente else "(desconhecido)"

        with self._sessao() as s, s.begin():
            # A condição no DELETE garante que só some a trava realmente vencida:
            # sem ela, dois processos poderiam apagar a trava um do outro.
            s.execute(delete(Trava).where(Trava.nome == nome, Trava.expira_em <= agora))

        logger.warning(
            "Trava expirada de %s assumida por %s — o processo anterior "
            "provavelmente caiu sem liberá-la.",
            dono_anterior,
            dono,
        )

        if not self._tentar_inserir_trava(nome, dono, datetime.now(), validade):
            raise TravaNaoObtida(
                "Não foi possível obter a trava de execução: outro processo a assumiu "
                "no mesmo instante."
            )

    def _tentar_inserir_trava(
        self, nome: str, dono: str, agora: datetime, validade: timedelta
    ) -> bool:
        """Try to store the lock. `False` means someone got there first."""
        try:
            with self._sessao() as s, s.begin():
                s.add(
                    Trava(
                        nome=nome,
                        dono=dono,
                        adquirida_em=agora,
                        expira_em=agora + validade,
                    )
                )
            return True
        except IntegrityError:
            return False
