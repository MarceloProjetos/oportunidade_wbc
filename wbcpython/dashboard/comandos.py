"""The CLI commands available on the panel's "Executar" tab.

## Why a subprocess and not a direct call

The panel runs the **real CLI** (`python -m wbcpython ...`) in a subprocess. Calling the internal functions would
be faster and give already-structured results, and is still the wrong choice here:

1. **No divergence.** What the panel does is literally the command. There is no "panel path" that can start
   behaving differently from the terminal path at the first new rule — exactly the defect the preview and the
   cycle would have if they duplicated the decision (see `docs/wbc/DECISOES.md`).
2. **Same output.** What appears on screen is the text that would appear in the terminal, in the same order. It
   is the natural extension of "one source, two screens" (`wbcpython.logs`).
3. **Isolation.** A cycle that blows up does not take the panel down, and it can be interrupted — which an
   in-process call does not allow.

The cost is that the panel must run **on the worker's machine**, with the drivers installed and access to SAP and
WBC. That is where it already runs.

## Concurrency

One execution at a time, enforced here (`Executor`). That is a screen convenience: the guarantee that matters is
the **single-execution lock** in the tracking database, taken by `executar_ciclo` and valid across processes — the
panel and the worker contend for the same lock, and whoever loses does not run (the scheduled cycle never waits; a
per-quote run waits up to `ESPERA_PELA_TRAVA_DIRIGIDA`). Without it, a click on the panel while the worker is busy
could create the same document twice.
"""

from __future__ import annotations

import shlex
import subprocess
import sys
import tempfile
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


def _agora() -> datetime:
    """Naive local time, like the rest of the project.

    The tracking database and the file log write it this way on purpose; a UTC stamp among them would be a
    three-hour difference nobody notices until comparing with a document in SAP.
    """
    return datetime.now()  # noqa: DTZ005


#: Quantas execuções passadas o painel guarda para exibir.
HISTORICO = 20

#: Teto de linhas lidas da saída de uma execução. O `doctor` cabe em 40; um
#: ciclo sobre a janela inteira passa de mil.
TETO_DE_SAIDA = 2000


@dataclass(frozen=True, slots=True)
class Campo:
    """An argument the command accepts, as it appears in the form."""

    nome: str
    rotulo: str
    ajuda: str = ""
    tipo: str = "text"
    marcado: bool = False
    #: The form does not go without it (checked again by the route, not only by the browser).
    obrigatorio: bool = False

    @property
    def bandeira(self) -> str:
        """The CLI option: always `--<nome>` (an override field nobody ever set was removed)."""
        return f"--{self.nome.replace('_', '-')}"


@dataclass(frozen=True, slots=True)
class Comando:
    """A command exposed on the screen.

    `escreve` marks those that touch SAP: they require the password and are unavailable when the panel points at
    production — except the `dirigido` ones (one quote typed by the operator; see the field). `escreve_tracking` marks
    those that only touch the tracking database: they ask for the password too, but weigh less — none of it reaches SAP.
    """

    id: str
    rotulo: str
    resumo: str
    argv: tuple[str, ...]
    escreve: bool = False
    escreve_tracking: bool = False
    campos: tuple[Campo, ...] = ()
    demora: str = ""
    aviso: str = ""
    #: Id de um segundo comando que divide o MESMO cartão com este.
    #:
    #: Existe porque os dois testes de conexão eram cartões quase idênticos —
    #: mesmo selo, mesma demora, uma linha de texto cada — e a grade ficava com
    #: sete onde cabem seis. Juntos eles ocupam um cartão só, mas **cada um
    #: mantém o próprio título, os próprios selos e o próprio botão**: são dois
    #: testes diferentes, e um rótulo guarda-chuva ("Testar as conexões") fazia
    #: quem procurava o HANA não achar.
    alternativo: str = ""

    #: Não ganha cartão próprio: existe para ser o `alternativo` de outro.
    #: Continua em `POR_ID`, porque é ele que o executor dispara.
    oculto: bool = False

    #: Acts on ONE quote the operator types — never on the window (owner, 01/10/2026).
    #:
    #: The password and the production block exist because a click used to fire a FULL
    #: cycle: the first one in production would cancel 27 quotes and create 4 orders, the
    #: 13-month rehearsal showed 144 writes (RISCOS_PRODUCAO.md, DECISOES 14/09/2026). A
    #: directed command cannot reach that: the quote number is mandatory and the HANA read
    #: brings only that quote (`pendentes_de_integracao(orcamento=…)`). What it writes is
    #: what the worker — already running in production every few minutes — would write for
    #: that quote anyway. So: no password, allowed in production, and the operator's NAME
    #: is still required — it is the audit trail, not a key.
    dirigido: bool = False

    @property
    def protegido(self) -> bool:
        """Behind the password and blocked when the painel points at production."""
        return (self.escreve or self.escreve_tracking) and not self.dirigido


CATALOGO: tuple[Comando, ...] = (
    Comando(
        id="pendentes",
        rotulo="Verificar pendentes",
        resumo=(
            "Mostra o que o ciclo faria na próxima passada, sem executar nada. "
            "Não cria, não altera e não cancela."
        ),
        argv=("pendentes",),
        demora="alguns segundos",
        campos=(
            Campo(
                nome="orcamento",
                rotulo="Orçamento (opcional)",
                ajuda="Avalia só este. Em branco, a janela inteira.",
            ),
            # O ensaio da janela maior **antes** de armá-la.
            #
            # Sem este campo, descobrir o que 13 meses fariam exigia armar o
            # pedido — e o ciclo automático pode disparar em menos de um
            # intervalo, escrevendo em produção o que ninguém chegou a ler. Aqui
            # a janela é só um parâmetro de leitura: nada é gravado, e o ciclo
            # segue na janela em vigor.
            Campo(
                nome="meses",
                rotulo="Ensaiar com outra janela (opcional)",
                ajuda="Ex.: 13. Só para ver o resultado — não arma nada.",
            ),
            Campo(
                nome="com-acao",
                rotulo="Listar só quem resultaria em escrita",
                tipo="checkbox",
            ),
            # Ligado por padrão: sem isto a verificação só imprime na tela e a
            # aba "Próximo ciclo" continua mostrando o retrato antigo — que é o
            # erro que a aba existe para evitar.
            Campo(
                nome="exportar",
                rotulo="Atualizar a aba “Próximo ciclo”",
                ajuda="Grava o retrato em previsao.json, que a aba lê.",
                tipo="checkbox",
                marcado=True,
            ),
        ),
    ),
    Comando(
        id="env",
        rotulo="Ambiente",
        resumo="Para qual company DB e schema a aplicação está apontada.",
        argv=("env",),
        demora="imediato",
        alternativo="doctor",
    ),
    Comando(
        id="doctor",
        rotulo="Diagnóstico da instalação",
        resumo="Configuração, dependências opcionais e prontidão de cada fase.",
        argv=("doctor",),
        demora="imediato",
        oculto=True,
    ),
    Comando(
        id="check-sap",
        rotulo="Testar conexão com o SAP",
        resumo="Abre uma sessão no Service Layer. Só leitura.",
        argv=("check-sap",),
        demora="alguns segundos",
        alternativo="check-hana",
    ),
    Comando(
        id="check-hana",
        rotulo="Testar o HANA",
        resumo="Conecta e diz em qual schema as views existem. Só leitura.",
        argv=("check-hana",),
        demora="alguns segundos",
        oculto=True,
    ),
    # The full-window cycle is the worker's job; from the painel, only one quote at a time
    # (owner, 01/10/2026). "Simular um ciclo" left the screen the same day — the CLI keeps
    # `ciclo --simular`.
    Comando(
        id="ciclo",
        rotulo="Ciclo de integração",
        resumo=(
            "Processa só o orçamento informado e sai: cria ou atualiza a cotação e o pedido "
            "dele no SAP, espelha o status e encerra a oportunidade, se for o caso. O resto "
            "da janela continua com o worker."
        ),
        argv=("ciclo",),
        escreve=True,
        dirigido=True,
        demora="alguns segundos",
        aviso=(
            "Cancelamento de cotação e criação de pedido não se desfazem. Confira antes em "
            "“Verificar pendentes”, com o mesmo orçamento."
        ),
        campos=(
            Campo(
                nome="orcamento",
                rotulo="Orçamento",
                ajuda="Ex.: 00123566",
                obrigatorio=True,
            ),
            Campo(
                nome="cancelados",
                rotulo="Só se estiver cancelado no WBC (SitCode 99)",
                ajuda="Só age se o orçamento estiver cancelado no WBC.",
                tipo="checkbox",
            ),
        ),
    ),
    Comando(
        id="pesos",
        rotulo="Recalcular pesos de um pedido",
        resumo=(
            "Recalcula o Weight1 das linhas a partir da árvore de produtos do WBC. "
            "A prévia é obrigatória antes de aplicar."
        ),
        argv=("pesos",),
        escreve=True,
        demora="alguns segundos",
        campos=(
            Campo(
                nome="pedido", rotulo="Pedido (DocNum)", ajuda="Informe o pedido ou o orçamento."
            ),
            Campo(nome="orcamento", rotulo="Orçamento", ajuda="Alternativa ao número do pedido."),
        ),
    ),
    Comando(
        id="datas-de-abertura",
        rotulo="Preencher datas de abertura",
        resumo=(
            "Completa a data de abertura nas linhas antigas do acompanhamento, "
            "para o painel poder aplicar a janela. Não toca no SAP."
        ),
        argv=("datas-de-abertura",),
        escreve_tracking=True,
        demora="alguns segundos",
    ),
)

POR_ID = {comando.id: comando for comando in CATALOGO}


class JaEmExecucao(RuntimeError):
    """A command started from the panel is already running."""


class PreviaObrigatoria(RuntimeError):
    """`pesos` only applies after a preview of the same target."""


#: Commands that stop through the stop file instead of being terminated: the cycle writes to
#: SAP and checks the file between quotes. See `Executor.interromper`.
PARADA_ENTRE_ORCAMENTOS = frozenset({"ciclo"})


@dataclass
class Execucao:
    """One execution started by the panel."""

    id: int
    comando: str
    rotulo: str
    linha: str
    solicitante: str
    inicio: datetime
    arquivo: Path
    fim: datetime | None = None
    codigo: int | None = None
    interrompida: bool = False
    #: The child's own stop file (``WORKER_ARQUIVO_DE_PARADA`` in its environment). The cycle
    #: reads it between quotes, so "Interromper" never cuts a write in the middle.
    parada: Path | None = None
    parada_pedida: bool = False
    _processo: subprocess.Popen[bytes] | None = field(default=None, repr=False)

    @property
    def rodando(self) -> bool:
        return self.fim is None

    @property
    def duracao(self) -> float:
        return ((self.fim or _agora()) - self.inicio).total_seconds()

    @property
    def situacao(self) -> str:
        if self.rodando:
            return "rodando"
        if self.interrompida:
            return "interrompida"
        return "ok" if self.codigo == 0 else "falhou"


def montar_argv(comando: Comando, valores: dict[str, str]) -> list[str]:
    """Translates the form into CLI arguments.

    Only what is in the catalog gets in: a field the `Comando` does not declare is ignored, and the value never goes
    to a shell — the subprocess receives the argument list directly. That is what keeps a text field on the screen
    from becoming arbitrary command execution on the worker's machine.
    """
    argv = list(comando.argv)
    for campo in comando.campos:
        bruto = (valores.get(campo.nome) or "").strip()
        if campo.tipo == "checkbox":
            if bruto:
                argv.append(campo.bandeira)
            continue
        if bruto:
            argv += [campo.bandeira, bruto]
    return argv


def _ajustar_exportar(comando: Comando, argv: list[str], arquivo_do_retrato: str) -> list[str]:
    """On the screen `--exportar` is a checkbox; on the CLI it is an option with a path.

    A rehearsal (`--meses`) **never** exports, even with the box checked. The "Próximo ciclo" tab exists to show what
    the next pass will do; writing there the snapshot of a window nobody armed would make the screen promise a cycle
    that will not happen — and that is the very tab someone reads to decide whether to let the worker run.
    """
    if comando.id != "pendentes" or "--exportar" not in argv:
        return argv
    if "--meses" in argv:
        posicao = argv.index("--exportar")
        return argv[:posicao] + argv[posicao + 1 :]
    posicao = argv.index("--exportar")
    return argv[: posicao + 1] + [arquivo_do_retrato] + argv[posicao + 1 :]


class Executor:
    """Runs one command at a time and keeps the output.

    Serialization here is a screen concern — stopping two clicks from starting two cycles. The cross-process guarantee
    is the lock in the tracking database.
    """

    def __init__(self, *, arquivo_do_retrato: str = "state/wbc_previsao.json") -> None:
        self._trava = threading.Lock()
        self._atual: Execucao | None = None
        self._historico: deque[Execucao] = deque(maxlen=HISTORICO)
        self._proximo_id = 1
        self._arquivo_do_retrato = arquivo_do_retrato
        #: Alvo da última prévia de peso bem-sucedida. É o que autoriza aplicar.
        self._previa_de_peso: str | None = None

    # ---------------------------------------------------------------- leitura

    def atual(self) -> Execucao | None:
        with self._trava:
            self._colher()
            return self._atual

    def historico(self) -> list[Execucao]:
        with self._trava:
            self._colher()
            return list(reversed(self._historico))

    def saida(self, execucao: Execucao, *, limite: int = TETO_DE_SAIDA) -> list[str]:
        try:
            texto = execucao.arquivo.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        linhas = texto.splitlines()
        return linhas[-limite:]

    def alvo_da_previa_de_peso(self) -> str | None:
        return self._previa_de_peso

    # --------------------------------------------------------------- execução

    def iniciar(
        self,
        comando: Comando,
        valores: dict[str, str],
        *,
        solicitante: str,
        aplicar_pesos: bool = False,
    ) -> Execucao:
        """Starts the command.

        Raises `JaEmExecucao` if another is still running, `PreviaObrigatoria` if `pesos` is applied without
        a preview of the same target, `ValueError` if a `dirigido` command lacks its mandatory field, and lets
        `OSError` through when the subprocess cannot be spawned.
        """
        if comando.dirigido and any(
            c.obrigatorio and not (valores.get(c.nome) or "").strip() for c in comando.campos
        ):
            # Second lock behind the route's check: without the quote the CLI would run the
            # whole window — exactly what skipping the password relies on never happening.
            raise ValueError(f"“{comando.rotulo}” só roda com o orçamento informado.")
        argv = montar_argv(comando, valores)
        argv = _ajustar_exportar(comando, argv, self._arquivo_do_retrato)

        if comando.id == "pesos":
            argv = self._pesos(argv, valores, aplicar=aplicar_pesos)

        with self._trava:
            self._colher()
            if self._atual is not None:
                raise JaEmExecucao(
                    f"“{self._atual.rotulo}” ainda está rodando "
                    f"(há {self._atual.duracao:.0f}s, pedido por {self._atual.solicitante})."
                )

            destino = Path(
                tempfile.NamedTemporaryFile(  # noqa: SIM115 - fechado abaixo
                    prefix="wbcpython-painel-", suffix=".txt", delete=False
                ).name
            )
            saida = destino.open("wb")
            parada = destino.with_suffix(".stop")
            ambiente = self._ambiente()
            ambiente["WORKER_ARQUIVO_DE_PARADA"] = str(parada)
            # `python -m wbcpython` e não o script `wbcpython`: assim usa
            # exatamente o interpretador do painel, sem depender de PATH.
            # Sem shell e com a lista de argumentos montada só a partir do
            # catálogo: um campo de texto da tela não vira comando na máquina.
            processo = subprocess.Popen(
                [sys.executable, "-m", "wbcpython", *argv],
                stdout=saida,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env=ambiente,
            )
            saida.close()

            execucao = Execucao(
                id=self._proximo_id,
                comando=comando.id,
                rotulo=comando.rotulo,
                linha="wbcpython " + " ".join(shlex.quote(a) for a in argv),
                solicitante=solicitante,
                inicio=_agora(),
                arquivo=destino,
                parada=parada,
                _processo=processo,
            )
            self._proximo_id += 1
            self._atual = execucao
            return execucao

    def interromper(self) -> Execucao | None:
        """Pede para parar o que está rodando.

        The cycle is asked through its stop file and stops between quotes: it finishes the
        quote in progress, releases the execution lock, closes the execution row and logs out
        of the Service Layer. ``terminate`` used to be called here on the assumption that "the
        cycle handles the signal" — on Windows it is ``TerminateProcess``, which runs no
        handler: the lock stayed held for 30 min, the execution stayed "em andamento" forever,
        and a kill between the POST and the link left a quotation unlinked (01/10/2026 review).
        Read-only commands have nothing to protect and are still terminated.
        """
        with self._trava:
            self._colher()
            execucao = self._atual
            if execucao is None or execucao._processo is None:
                return None
            execucao.interrompida = True
            if execucao.comando in PARADA_ENTRE_ORCAMENTOS and execucao.parada is not None:
                if not execucao.parada_pedida:
                    execucao.parada.write_text("interrompido pelo painel", encoding="utf-8")
                    execucao.parada_pedida = True
                return execucao
            execucao._processo.terminate()
            return execucao

    # ---------------------------------------------------------------- interno

    def _ambiente(self) -> dict[str, str]:
        """The subprocess environment, without the panel password.

        The password authorizes the click; it has nothing to do inside the command, and leaving it in a child's
        environment is a free leak — it would be visible to anything the command ends up running.
        """
        import os

        ambiente = dict(os.environ)
        ambiente.pop("PAINEL_SENHA", None)
        return ambiente

    def _pesos(self, argv: list[str], valores: dict[str, str], *, aplicar: bool) -> list[str]:
        """Mandatory preview: applying requires a simulation of the **same** target.

        Not applying (`aplicar=False`) appends `--simular` and forgets any earlier preview. What releases applying is
        having seen the preview of that very order: the command alters the weight of an already-created
        document, and the difference between the tree's net weight and the shipping one (x1.10) is exactly
        the kind of thing you check by looking, not by trusting.
        """
        alvo = self._alvo_de_peso(valores)
        if not aplicar:
            self._previa_de_peso = None
            return [*argv, "--simular"]
        if self._previa_de_peso != alvo:
            raise PreviaObrigatoria(
                "Rode a prévia deste pedido primeiro: o painel só aplica peso "
                "depois de mostrar o que mudaria."
            )
        return argv

    @staticmethod
    def _alvo_de_peso(valores: dict[str, str]) -> str:
        pedido = (valores.get("pedido") or "").strip()
        orcamento = (valores.get("orcamento") or "").strip()
        return f"pedido:{pedido}|orcamento:{orcamento}"

    def _colher(self) -> None:
        """Closes the current execution if its process has already ended.

        Called inside the lock, on every read: no thread watches the process, and it is the screen's own query that
        notices the end. A panel nobody is looking at does not need to notice anything. A successful `pesos --simular`
        records its target as the authorized preview.
        """
        execucao = self._atual
        if execucao is None or execucao._processo is None:
            return
        codigo = execucao._processo.poll()
        if codigo is None:
            return
        execucao.codigo = codigo
        execucao.fim = _agora()
        execucao._processo = None
        if execucao.parada is not None:
            execucao.parada.unlink(missing_ok=True)
        if execucao.comando == "pesos" and codigo == 0 and "--simular" in execucao.linha:
            self._previa_de_peso = self._alvo_da_linha(execucao.linha)
        self._historico.append(execucao)
        self._atual = None

    @staticmethod
    def _alvo_da_linha(linha: str) -> str:
        partes = shlex.split(linha)

        def valor(bandeira: str) -> str:
            return partes[partes.index(bandeira) + 1] if bandeira in partes else ""

        return f"pedido:{valor('--pedido')}|orcamento:{valor('--orcamento')}"
