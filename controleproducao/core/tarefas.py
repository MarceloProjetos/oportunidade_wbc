"""Background execution with progress tracking (22/09/2026).

Anderson's decision for the web layer: long operations run in the background and the user
follows them. Concretely, `pedidos-wbc processar-novos` takes ~7 s on an average quote
(section 7.14) and `manutencao-op encerrar --pedido` on 68 OPs makes ~270 Service Layer
calls (minutes) — none of that fits in an HTTP request with the page waiting.

**Deliberate scope: in-process, in-memory, no external queue.** Internal app, operations
triggered manually by one person at a time — the legacy addon's model, and the "manual
buttons" decision dates from 14/09. Celery/RQ would add a broker, workers and their own
deploy for a problem we do not have yet.

Since 29/09/2026 a finished execution is also written to Supabase (``core/historico.py``,
the last 30, on the .11 only), so a restart no longer empties the Execuções screen. The
execution itself still lives only here: a restart in the middle of one loses its live
progress (``deploy_update.bat`` refuses to stop the service while ``/health/ocupado`` = 1).

⚠️ Tasks write to the SAP, but this module does not decide what may run; it runs what it is
given. The gates (production machine, confirmation of irreversible operations) live in the
layer that creates the task.
"""
from __future__ import annotations

import asyncio
import logging
import time
import traceback
import uuid
from collections import deque
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# Quantas tarefas terminadas ficam no histórico. Sem limite, um processo de longa duração
# acumularia resultados até o fim dos tempos.
MAX_TAREFAS = 200

# Linhas de log guardadas por tarefa. O `encerrar --pedido` de um pedido grande gera uma
# linha por OP por etapa; o suficiente para acompanhar, não para virar arquivo de log.
MAX_LINHAS_LOG = 500

# While no history is attached, how often to ask again whether this machine has one.
REVER_HISTORICO_S = 300.0

# Finished executions on the Execuções screen — and, on the .11, how many the Supabase
# history keeps (`core/historico.py` reads this, so the two cannot drift apart).
MAX_NA_TELA = 30

# How an outcome is painted: the list pill, the detail pill and the detail bar all read these
# (templates via Jinja globals, the page script via `tojson`). There were three copies, and on
# 29/09/2026 the list painted a cancelled run GREEN while the detail showed it amber.
CLASSE_DA_PILULA = {"erro": "is-erro", "falhas": "is-warn", "cancelada": "is-warn",
                    "ok": "is-ok", "rodando": "is-aberto", "fila": "is-neutro"}
CLASSE_DA_BARRA = {"erro": "is-erro", "falhas": "is-warn", "cancelada": "is-erro",
                   "ok": "is-ok", "rodando": "", "fila": ""}

# Where an execution was asked from — the same values the history table's CHECK accepts.
ORIGEM_TELA = "tela"
ORIGEM_API = "api"
ORIGENS = (ORIGEM_TELA, ORIGEM_API)


def desfecho_de(situacao: str, com_falhas: bool) -> str:
    """The one reading of a task's outcome: erro | falhas | cancelada | ok | rodando | fila."""
    if situacao == "erro":
        return "erro"
    if com_falhas:
        return "falhas"
    if situacao == "cancelada":
        return "cancelada"
    if situacao == "concluída":
        return "ok"
    if situacao == "executando":
        return "rodando"
    return "fila"


#: Opens every error or warning line of an execution log (see `Tarefa.anota`).
MARCA_PROBLEMA = "⚠"


@dataclass
class Tarefa:
    """One execution, running or finished."""

    id: str
    nome: str
    descricao: str
    criada_em: datetime
    modulo: str = ""
    situacao: str = "na fila"  # na fila | executando | concluída | erro | cancelada
    iniciada_em: datetime | None = None
    terminada_em: datetime | None = None
    passo: str = ""
    passos_feitos: int = 0
    passos_total: int = 0
    linhas: deque[str] = field(default_factory=lambda: deque(maxlen=MAX_LINHAS_LOG))
    resultado: Any = None
    erro: str | None = None
    # True when rebuilt from the Supabase history (read-only; nothing runs behind it).
    guardada: bool = False
    # Who asked and through what (29/09/2026, PLANO_API_MANUTENCAO_OP F2). The JSON API
    # requires `solicitante`; the screen has no per-person identity (one shared key), so its
    # executions keep None. It is what the caller declared, not a verified identity.
    solicitante: str | None = None
    origem: str = ORIGEM_TELA
    # Stop between steps instead of cutting the coroutine (29/09/2026, D5 of
    # PLANO_API_MANUTENCAO_OP.md (removed 2026-10-06)): set by whoever creates a task whose steps must not be
    # split — the closing of OPs, where one "step" is issue → receipt → close. `cancelar` then
    # only raises `parada_pedida`, and the task body checks it before each step.
    parada_combinada: bool = False
    parada_pedida: bool = False

    @property
    def terminada(self) -> bool:
        return self.situacao in {"concluída", "erro", "cancelada"}

    @property
    def com_falhas(self) -> bool:
        """The task ended without raising, but its result carries errors.

        "concluída" only means the coroutine raised nothing; a green CONCLUÍDA over a result
        where all four pedidos failed is worse than no indicator (users read the colour, not
        the JSON). Project convention: service functions return failures in a `com_erro`
        list (same shape in `muda_status`, `finalizar_ops`, `processar_*`).
        """
        return bool(isinstance(self.resultado, dict) and self.resultado.get("com_erro"))

    @property
    def desfecho(self) -> str:
        return desfecho_de(self.situacao, self.com_falhas)

    @property
    def duracao_segundos(self) -> float | None:
        if not self.iniciada_em:
            return None
        fim = self.terminada_em or datetime.now()
        return round((fim - self.iniciada_em).total_seconds(), 1)

    def anota(self, linha: str, *, problema: bool = False) -> None:
        """Append a timestamped line to the live log, visible to the user while it runs.

        ``problema`` (errors and warnings, 29/09/2026) prefixes the line with ``MARCA_PROBLEMA``:
        the Execuções screen paints those lines red, and the mark survives the Supabase
        history and the API, where the lines stay plain text.
        """
        marca = f"{MARCA_PROBLEMA} " if problema else ""
        self.linhas.append(f"{datetime.now():%H:%M:%S}  {marca}{linha}")

    def avanca(
        self, passo: str, feitos: int | None = None, total: int | None = None, *, problema: bool = False
    ) -> None:
        """Set the current step and, optionally, the progress counters.

        ``problema`` marks the log line like ``anota`` does (01/10/2026): a pedido's "ERRO" or
        "ATENÇÃO" step is also the step shown, and it used to reach the log without the mark.
        """
        self.passo = passo
        if feitos is not None:
            self.passos_feitos = feitos
        if total is not None:
            self.passos_total = total
        self.anota(passo, problema=problema)

    def para_json(self) -> dict:
        return {
            "id": self.id,
            "nome": self.nome,
            "descricao": self.descricao,
            "situacao": self.situacao,
            "terminada": self.terminada,
            "com_falhas": self.com_falhas,
            "desfecho": self.desfecho,
            "passo": self.passo,
            "passos_feitos": self.passos_feitos,
            "passos_total": self.passos_total,
            "percentual": (
                round(100 * self.passos_feitos / self.passos_total)
                if self.passos_total else None
            ),
            "criada_em": self.criada_em.isoformat(timespec="seconds"),
            "duracao_segundos": self.duracao_segundos,
            "linhas": list(self.linhas),
            "resultado": self.resultado,
            "erro": self.erro,
            "solicitante": self.solicitante,
            "origem": self.origem,
            "parada_pedida": self.parada_pedida,
        }


class RegistroDeTarefas:
    """Holds the tasks and runs one at a time PER MODULE.

    Per module, not globally: two runs of the same module in parallel would fight over the
    same pedidos and OPs (that is how a duplicate OP is created), while different modules are
    independent and serialising everything would make the user wait for nothing.
    """

    def __init__(self, historico: Any = None) -> None:
        self._tarefas: dict[str, Tarefa] = {}
        self._ordem: deque[str] = deque(maxlen=MAX_TAREFAS)
        self._em_execucao: dict[str, str] = {}  # módulo -> id da tarefa
        self._jobs: dict[str, asyncio.Task] = {}
        # `core.historico.HistoricoSupabase` on the .11 (see `usar_historico`), None elsewhere.
        self._historico = historico
        self._resolver: Callable[[], Any] | None = None
        self._resolvido_em: float | None = None
        self._gravacoes: set[asyncio.Future] = set()

    @property
    def historico(self) -> Any:
        """The stored history, asking the resolver again at most every `REVER_HISTORICO_S`
        while there is none: the service starts at boot, possibly before the .11's address is
        bound, and a None frozen at startup would keep the screen memory-only all day."""
        if self._historico is None and self._resolver is not None:
            agora = time.monotonic()
            if self._resolvido_em is None or agora - self._resolvido_em >= REVER_HISTORICO_S:
                self._resolvido_em = agora
                self._historico = self._resolver()
        return self._historico

    @historico.setter
    def historico(self, valor: Any) -> None:
        # An explicit value (tests, a fake) wins and stops the re-checks.
        self._historico = valor
        self._resolver = None

    def usar_historico(self, resolver: Callable[[], Any]) -> None:
        """Attach the history by asking `resolver` now, and again later while it says None."""
        self._historico = None
        self._resolver = resolver
        self._resolvido_em = None
        _ = self.historico

    def em_execucao(self, modulo: str) -> Tarefa | None:
        """The unfinished task of that module, if any."""
        tarefa_id = self._em_execucao.get(modulo)
        tarefa = self._tarefas.get(tarefa_id) if tarefa_id else None
        if tarefa and tarefa.terminada:
            self._em_execucao.pop(modulo, None)
            return None
        return tarefa

    def confere_livre(self, modulo: str) -> None:
        """Raise `RuntimeError` (the message the screens show) if the module is busy.

        Split out of `criar` (29/09/2026) so a caller can check BEFORE spending something
        that cannot be given back — the single-use token of a checked plan.
        """
        ocupada = self.em_execucao(modulo)
        if ocupada:
            raise RuntimeError(
                f"O módulo '{modulo}' já tem uma execução em andamento "
                f"({ocupada.nome}, iniciada às {ocupada.criada_em:%H:%M:%S}). "
                "Duas execuções simultâneas no mesmo módulo disputariam os mesmos "
                "pedidos e OPs."
            )

    def criar(
        self,
        modulo: str,
        nome: str,
        descricao: str,
        corrotina: Callable[[Tarefa], Awaitable[Any]],
        *,
        solicitante: str | None = None,
        origem: str = ORIGEM_TELA,
        parada_combinada: bool = False,
    ) -> Tarefa:
        """Register and start the task.

        Raises:
            ValueError: `origem` is not one of `ORIGENS`.
            RuntimeError: the module already has a running task (see `confere_livre`).
        """
        if origem not in ORIGENS:
            raise ValueError(f"origem inválida: {origem!r} (esperado {', '.join(ORIGENS)})")
        self.confere_livre(modulo)

        tarefa = Tarefa(
            id=uuid.uuid4().hex[:12], nome=nome, descricao=descricao, criada_em=datetime.now(),
            modulo=modulo, solicitante=solicitante, origem=origem,
            parada_combinada=parada_combinada,
        )
        self._guardar(tarefa)
        self._em_execucao[modulo] = tarefa.id
        self._jobs[tarefa.id] = asyncio.create_task(self._executar(tarefa, modulo, corrotina))
        return tarefa

    async def _executar(
        self, tarefa: Tarefa, modulo: str, corrotina: Callable[[Tarefa], Awaitable[Any]]
    ) -> None:
        tarefa.situacao = "executando"
        tarefa.iniciada_em = datetime.now()
        try:
            tarefa.resultado = await corrotina(tarefa)
            # A combined stop ends the body normally, with what it did in `resultado`; it is
            # still an interruption. The body clears `parada_pedida` when nothing was left out.
            tarefa.situacao = "cancelada" if tarefa.parada_pedida else "concluída"
        except asyncio.CancelledError:
            tarefa.situacao = "cancelada"
            tarefa.anota("Execução cancelada.")
            raise
        except Exception as exc:  # noqa: BLE001 - a falha é o resultado da tarefa
            tarefa.situacao = "erro"
            tarefa.erro = str(exc)
            tarefa.anota(f"ERRO: {exc}", problema=True)
            # O traceback vai para o log do servidor, não para a tela: a mensagem o usuário
            # já tem, e o rastro completo é para quem for investigar.
            logger.error("Tarefa %s (%s) falhou:\n%s", tarefa.id, tarefa.nome, traceback.format_exc())
        finally:
            tarefa.terminada_em = datetime.now()
            self._em_execucao.pop(modulo, None)
            self._jobs.pop(tarefa.id, None)
            self._arquivar(tarefa)

    def _arquivar(self, tarefa: Tarefa) -> None:
        """Hand the finished task to the stored history, off the event loop.

        Fire and forget: `guardar` never raises and does blocking HTTP, so it runs in the
        default executor and the task (and the module lock) is released right away.
        """
        if self.historico is None:
            return
        gravacao = asyncio.get_running_loop().run_in_executor(None, self.historico.guardar, tarefa)
        # Strong reference until done: shutdown waits on these (`aguardar_gravacoes`).
        self._gravacoes.add(gravacao)
        gravacao.add_done_callback(self._gravacoes.discard)

    def gravacoes_pendentes(self) -> int:
        """History writes still in flight. `/health/ocupado` counts them: a deploy that
        stops the service mid-write loses exactly the row the history exists to keep."""
        return sum(1 for g in self._gravacoes if not g.done())

    async def aguardar_gravacoes(self, timeout: float = 15.0) -> None:
        """Let pending history writes finish before the process exits (service stop)."""
        if self._gravacoes:
            await asyncio.wait(set(self._gravacoes), timeout=timeout)

    def obter(self, tarefa_id: str) -> Tarefa | None:
        return self._tarefas.get(tarefa_id)

    async def obter_ou_guardada(self, tarefa_id: str) -> Tarefa | None:
        """Memory first (live progress), then the stored history.

        Raises when the history cannot be read: "not found" and "could not look" are
        different answers and the screen must not merge them.
        """
        tarefa = self.obter(tarefa_id)
        if tarefa is not None or self.historico is None:
            return tarefa
        return await asyncio.to_thread(self.historico.obter, tarefa_id)

    def listar(self, limite: int = 20) -> list[Tarefa]:
        """Most recent first."""
        ids = list(self._ordem)[-limite:]
        return [self._tarefas[i] for i in reversed(ids) if i in self._tarefas]

    async def recentes(self, limite: int = MAX_NA_TELA) -> tuple[list[Any], str | None]:
        """What the Execuções screen lists, and a notice when the list is partial.

        Running or queued tasks first, then the ``limite`` most recent finished ones from
        memory and from the stored history together (a task finished in this process is in
        both; memory wins). When the history cannot be read the notice says so: a shorter
        list presented as complete would hide executions that did write to the SAP.
        """
        em_memoria = self.listar(limite=MAX_TAREFAS)
        rodando = [t for t in em_memoria if not t.terminada]
        terminadas: list[Any] = [t for t in em_memoria if t.terminada]
        aviso = None
        if self.historico is not None:
            try:
                guardadas = await asyncio.to_thread(self.historico.listar, limite)
            except Exception as exc:  # noqa: BLE001 - the screen degrades to memory, with a notice
                logger.warning("Histórico de execuções indisponível na leitura: %s", exc)
                aviso = (
                    "Não foi possível ler o histórico guardado no Supabase agora — esta lista "
                    "mostra só as execuções desde o último reinício do serviço."
                )
            else:
                vistas = {t.id for t in em_memoria}
                terminadas += [g for g in guardadas if g.id not in vistas]
        terminadas.sort(key=lambda t: t.criada_em, reverse=True)
        return rodando + terminadas[:limite], aviso

    async def cancelar(self, tarefa_id: str) -> bool:
        """Request cancellation; returns whether there was anything to cancel.

        ⚠️ Cancelling does NOT undo what was already written to the SAP: it interrupts between
        steps and documents already created stay. The caller must make that clear on screen.

        A task created with ``parada_combinada`` is not cut: it is asked to stop, and stops
        before its next step (the closing of OPs: after the OP in progress). Asking twice is
        harmless.
        """
        job = self._jobs.get(tarefa_id)
        if not job or job.done():
            return False
        tarefa = self._tarefas.get(tarefa_id)
        if tarefa is not None and tarefa.parada_combinada:
            if not tarefa.parada_pedida:
                tarefa.parada_pedida = True
                tarefa.anota("Interrupção pedida: a etapa em curso termina e a próxima não começa.")
            return True
        job.cancel()
        return True

    def _guardar(self, tarefa: Tarefa) -> None:
        if len(self._ordem) == MAX_TAREFAS:
            mais_antiga = self._ordem[0]
            self._tarefas.pop(mais_antiga, None)
        self._tarefas[tarefa.id] = tarefa
        self._ordem.append(tarefa.id)


class _PonteDeLog(logging.Handler):
    """Mirrors a module's log into a task's live log."""

    def __init__(self, tarefa: Tarefa) -> None:
        super().__init__()
        self._tarefa = tarefa

    def emit(self, registro: logging.LogRecord) -> None:
        try:
            self._tarefa.anota(registro.getMessage(), problema=registro.levelno >= logging.WARNING)
        except Exception:  # noqa: BLE001 - log nunca derruba a execução
            pass


@contextmanager
def acompanha_log(tarefa: Tarefa, *modulos: str, nivel: int = logging.INFO) -> Iterator[None]:
    """Make the `logger.info` of the given modules show up in the task's live log.

    Why a bridge instead of new progress calls: the services already narrate what they do
    ("grupo 2/5", "criando OP do item X") with `logger.info`, and the CLI shows it on the
    console with `--perfil`. Duplicating those as `tarefa.anota(...)` would create two
    narratives of the same process that drift apart; the web listens to the existing one.

    The level is set on the handler, not the logger: changing a module logger's level would
    alter the whole process, including another task running in parallel in another module.
    The logger level is lowered only when needed and restored on exit.
    """
    ponte = _PonteDeLog(tarefa)
    ponte.setLevel(nivel)
    alvos = [logging.getLogger(nome) for nome in modulos]
    anteriores = [(alvo, alvo.level) for alvo in alvos]
    try:
        for alvo in alvos:
            alvo.addHandler(ponte)
            # Se o logger estiver acima do nível desejado, o registro nem chega ao
            # handler. Só abaixamos quando necessário, e devolvemos no fim.
            if alvo.level == logging.NOTSET or alvo.level > nivel:
                alvo.setLevel(nivel)
        yield
    finally:
        for alvo, nivel_anterior in anteriores:
            alvo.removeHandler(ponte)
            alvo.setLevel(nivel_anterior)


# Registro único do processo. A camada web importa este objeto.
TAREFAS = RegistroDeTarefas()
