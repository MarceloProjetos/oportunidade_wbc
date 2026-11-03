"""Stop request by file: the channel that depends on neither a console nor a signal.

Why it exists (09/09/2026): the day's second `deploy_update.bat` caught the worker 3 s into
cycle #133 and the process vanished in under 20 s without passing through the Ctrl+C handler:
no "signal received", no "cycle interrupted", no "worker stopped". The lock stayed under a
dead PID for 30 minutes and execution #133 stayed "in progress" forever. Two probable causes,
which add up: the `python.exe` run directly by the NSSM service has no console, so
`GenerateConsoleCtrlEvent` fails and NSSM falls back to `TerminateProcess`; and `Event.wait()`
without a timeout is not interrupted by a signal on Windows.

The file fixes both: the deploy writes `state/wbc_worker.stop` **before** `nssm stop`, the
worker sees it between quotes and between cycles, finishes what it is doing (releases the
lock, closes the execution, logs out) and exits, so NSSM finds the process already gone.

Pure on purpose: it does not import `apscheduler`, so it is testable on any machine.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("wbcpython.host.parada")


class PedidoDeParada:
    """Watches a file whose existence means "stop when you can"."""

    def __init__(self, arquivo: Path) -> None:
        self._arquivo = Path(arquivo)
        self._anunciado = False

    @property
    def arquivo(self) -> Path:
        return self._arquivo

    def pendente(self) -> bool:
        """`True` while the file exists. Logs once per request, not once per poll."""
        if not self._arquivo.exists():
            self._anunciado = False
            return False
        if not self._anunciado:
            self._anunciado = True
            logger.info(
                "Parada pedida por arquivo (%s) — o worker termina o que está fazendo e sai.",
                self._arquivo,
            )
        return True

    def limpar(self, *, motivo: str) -> bool:
        """Removes the file. Returns ``True`` if there was one.

        Called at worker **start**: the deploy writes the file, stops the service and starts it
        again; without this cleanup the new process would read the old request and exit on its first
        poll. It also covers a file left behind by someone who stopped the worker by hand.
        """
        try:
            self._arquivo.unlink()
        except FileNotFoundError:
            return False
        except OSError as exc:  # noqa: BLE001 - partida do worker não pode explodir por isso
            logger.warning("Não consegui remover %s (%s): %s", self._arquivo, motivo, exc)
            return False
        logger.info("Arquivo de parada removido na partida (%s): %s", motivo, self._arquivo)
        return True

    def pedir(self, *, motivo: str = "") -> None:
        """Writes the request: what the deploy does in `.bat`; here it serves tests and the CLI."""
        self._arquivo.parent.mkdir(parents=True, exist_ok=True)
        self._arquivo.write_text(motivo or "parada solicitada", encoding="utf-8")
