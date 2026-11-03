"""The integration log — one source, two screens.

What `pendentes`, `ciclo` and `worker` report goes to `logging`, and from there to two places at once: the screen
of whoever ran the command and a file the panel reads. It is the same text in both, not two narratives of the same
event that can disagree.

Why a file and not the tracking database: tracking records what **happened** to each quote (the decision, the
document created, the error) and is queried per quote. The log records the **execution** — the order of events,
what came before the error, what the worker was doing at 3 a.m. They are different questions, and answering both
with the same table would make both worse.

File format (one line per event, fields separated by ` | `):

    2026-09-01 14:32:07 | INFO     | wbcpython.host.worker | ciclo concluído

The separator is explicit so the panel's reader does not have to guess where the message starts — messages
contain spaces and `:` all the time.
"""

from __future__ import annotations

import logging
import logging.handlers
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

#: Marca os handlers instalados por este módulo. Sem ela, chamar `configurar`
#: duas vezes no mesmo processo (o que os testes fazem, e o recarregador do
#: uvicorn também)
#: empilharia handlers e cada linha sairia repetida.
_MARCA = "wbcpython"

SEPARADOR = " | "
FORMATO_DO_ARQUIVO = (
    f"%(asctime)s{SEPARADOR}%(levelname)-8s{SEPARADOR}%(name)s{SEPARADOR}%(message)s"
)
FORMATO_DA_DATA = "%Y-%m-%d %H:%M:%S"

#: 5 MB por arquivo, 3 arquivos: cobre semanas de worker num disco de
#: desenvolvimento sem exigir rotação externa.
TAMANHO_MAXIMO = 5 * 1024 * 1024
ARQUIVOS_MANTIDOS = 3

NIVEIS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

#: Bibliotecas que registram uma linha INFO por requisição. No arquivo elas são
#: exatamente o que se quer quando algo dá errado — mostram a chamada que
#: precedeu o erro. Na tela, afogam o relatório: uma prévia de 700 orçamentos
#: intercalaria centenas de "HTTP Request: GET ..." entre as linhas que a pessoa
#: veio ler. Por isso o filtro é só do console.
RUIDOSAS = ("httpx", "httpcore", "hdbcli", "urllib3")


class _SoORelatorio(logging.Filter):
    """Lets application lines through; from libraries, only warnings and errors."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.WARNING:
            return True
        return not record.name.startswith(RUIDOSAS)


#: After a failed rotation (another process had the file open at that instant), wait this
#: long before trying again — and keep writing to the current file meanwhile.
ESPERA_APOS_ROTACAO_FALHA = 60.0


class _ArquivoQueRoda(logging.handlers.RotatingFileHandler):
    """The rotating file of the ONE process that rotates (the continuous worker).

    On Windows the rename of a rotation fails while any other process has the file open.
    The stock handler then drops the record, and tries again on the next one, and the next…
    — every line lost for as long as the other process keeps it (01/10/2026 review). Here a
    failed rotation keeps writing to the current file and retries after a pause.
    """

    _proxima_tentativa = 0.0

    def shouldRollover(self, record: logging.LogRecord) -> bool:  # noqa: N802 - stdlib name
        if time.monotonic() < self._proxima_tentativa:
            return False
        return bool(super().shouldRollover(record))

    def doRollover(self) -> None:  # noqa: N802 - stdlib name
        try:
            super().doRollover()
        except OSError:
            self._proxima_tentativa = time.monotonic() + ESPERA_APOS_ROTACAO_FALHA
            if self.stream is None:
                self.stream = self._open()


class _ArquivoCompartilhado(logging.Handler):
    """Appends to the shared log file, opening and closing it on every record.

    For every process but the continuous worker (the painel, the commands it starts, a CLI
    run): holding the file open for hours is what made the worker's rotation fail. One open
    per line is nothing at this volume.
    """

    terminator = "\n"

    def __init__(self, arquivo: Path) -> None:
        super().__init__()
        self.arquivo = arquivo
        with open(arquivo, "a", encoding="utf-8"):
            pass  # fail here (unwritable path), where `configurar` reports it, not per record

    def emit(self, record: logging.LogRecord) -> None:
        try:
            texto = self.format(record)
            with open(self.arquivo, "a", encoding="utf-8") as saida:
                saida.write(texto + self.terminator)
        except Exception:  # noqa: BLE001 - the stdlib contract: report, never raise
            self.handleError(record)


def configurar(
    *,
    nivel: str = "INFO",
    arquivo: Path | str | None = None,
    tela: bool = True,
    rotacionar: bool = False,
) -> Path | None:
    """Installs the handlers and returns the file in use (or `None`).

    `tela` writes to `sys.stdout` — not the `stderr` default of `logging` — so the command output is a single thing:
    a `>` redirect in a terminal must carry the whole report with it.

    The screen gets the report; the file gets **everything**, including the libraries' HTTP trail (see `RUIDOSAS`).
    That asymmetry is right: whoever is at the terminal wants to read the result, and whoever investigates an error
    later wants the call that came before it.

    If the file cannot be opened (read-only directory, for instance) the screen keeps working and a warning is
    logged. A read-only command must not fail because of the log.

    ``rotacionar``: only the continuous worker rotates the file; everyone else appends without holding it open (see
    `_ArquivoCompartilhado`). Several processes rotating one file on Windows lose lines.
    """
    raiz = logging.getLogger()
    raiz.setLevel(getattr(logging, nivel.upper(), logging.INFO))

    for handler in list(raiz.handlers):
        if getattr(handler, "_wbcpython", None) == _MARCA:
            raiz.removeHandler(handler)
            handler.close()

    if tela:
        # `%(message)s` puro: na tela o texto é o relatório que a pessoa veio
        # ler, e prefixar cada linha com data e nível o tornaria ilegível.
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(logging.Formatter("%(message)s"))
        console.addFilter(_SoORelatorio())
        console._wbcpython = _MARCA  # type: ignore[attr-defined]
        raiz.addHandler(console)

    if arquivo is None:
        return None

    destino = Path(arquivo)
    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        em_arquivo: logging.Handler = (
            _ArquivoQueRoda(
                destino, maxBytes=TAMANHO_MAXIMO, backupCount=ARQUIVOS_MANTIDOS, encoding="utf-8"
            )
            if rotacionar
            else _ArquivoCompartilhado(destino)
        )
    except OSError as exc:
        logging.getLogger(__name__).warning("Sem log em arquivo (%s): %s", destino, exc)
        return None

    em_arquivo.setFormatter(logging.Formatter(FORMATO_DO_ARQUIVO, datefmt=FORMATO_DA_DATA))
    em_arquivo._wbcpython = _MARCA  # type: ignore[attr-defined]
    raiz.addHandler(em_arquivo)
    return destino


#: Tema no começo da mensagem: `[porta-paletes] 00125442: 272 módulos lidos...`.
#: Minúsculas, dígitos e hífen, entre colchetes, seguido de espaço — é o que
#: `domain.linhas` e `application.processar` escrevem. O painel usa o tema
#: para colorir a linha e oferecer o filtro de um clique.
_TEMA = re.compile(r"^\[([a-z][a-z0-9-]{1,30})\] ")


@dataclass(frozen=True, slots=True)
class LinhaDeLog:
    """One line of the file, already split into fields."""

    momento: str = ""
    nivel: str = ""
    origem: str = ""
    mensagem: str = ""

    @property
    def grave(self) -> bool:
        return self.nivel in ("ERROR", "CRITICAL")

    @property
    def tema(self) -> str:
        """`porta-paletes` in `[porta-paletes] ...`; empty when there is no theme."""
        encontrado = _TEMA.match(self.mensagem)
        return encontrado.group(1) if encontrado else ""

    @property
    def texto(self) -> str:
        """The message without the leading `[tema] ` — the panel shows the theme separately."""
        return _TEMA.sub("", self.mensagem, count=1)


def ler(
    arquivo: Path | str,
    *,
    limite: int = 500,
    nivel: str | None = None,
    busca: str = "",
) -> list[LinhaDeLog]:
    """Last lines of the log, newest first.

    Reads the whole file and cuts at the end: with the 5 MB rotation that is cheap, and a backwards read (`seek` from
    the end) would complicate the code to save milliseconds on a screen nobody reloads in a loop.

    A line that does not match the format becomes a plain message — an exception traceback spans several lines and
    would vanish if the reader dropped what it does not recognize. Note that with `nivel` set those unrecognized
    lines are filtered out too (their level is empty).
    """
    origem = Path(arquivo)
    if not origem.exists():
        return []

    termo = busca.strip().lower()
    encontradas: list[LinhaDeLog] = []
    with origem.open(encoding="utf-8", errors="replace") as aberto:
        for bruta in aberto:
            linha = _separar(bruta.rstrip("\n"))
            if nivel and linha.nivel != nivel:
                continue
            if termo and termo not in bruta.lower():
                continue
            encontradas.append(linha)

    return list(reversed(encontradas[-limite:]))


def _separar(bruta: str) -> LinhaDeLog:
    partes = bruta.split(SEPARADOR, 3)
    if len(partes) < 4 or partes[1].strip() not in NIVEIS:
        return LinhaDeLog(mensagem=bruta)
    return LinhaDeLog(
        momento=partes[0].strip(),
        nivel=partes[1].strip(),
        origem=partes[2].strip(),
        mensagem=partes[3],
    )
