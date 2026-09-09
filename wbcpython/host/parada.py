"""Pedido de parada por arquivo — o canal que não depende de console nem de sinal.

Por que existe (09/09/2026): o segundo `deploy_update.bat` do dia pegou o
worker 3 s dentro do ciclo #133 e o processo sumiu em menos de 20 s sem passar
pelo tratador de Ctrl+C — sem "sinal recebido", sem "ciclo interrompido", sem
"worker encerrado". A trava ficou em nome de um PID morto por 30 minutos e a
execução #133 ficou "em andamento" para sempre. Duas causas prováveis, que se
somam: o `python.exe` direto no serviço NSSM não tem console, então o
`GenerateConsoleCtrlEvent` falha e o NSSM cai no `TerminateProcess`; e o
`Event.wait()` sem timeout não é interrompido por sinal no Windows.

O arquivo resolve as duas: o deploy grava `state/wbc_worker.stop` **antes** do
`nssm stop`, o worker o vê entre orçamentos e entre ciclos, termina o que está
fazendo (libera a trava, fecha a execução, faz `Logout`) e sai — e o NSSM
encontra o processo já encerrado. Sem console, sem sinal, sem corrida.

Este módulo é puro de propósito: não importa o `apscheduler`, e por isso é
testável em qualquer máquina.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("wbcpython.host.parada")


class PedidoDeParada:
    """Observa um arquivo cuja existência significa "pare quando puder"."""

    def __init__(self, arquivo: Path) -> None:
        self._arquivo = Path(arquivo)
        self._anunciado = False

    @property
    def arquivo(self) -> Path:
        return self._arquivo

    def pendente(self) -> bool:
        """`True` enquanto o arquivo existir. Loga uma vez por pedido, não por volta."""
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
        """Remove o arquivo. `True` se havia um.

        Chamado na **partida** do worker: o deploy grava o arquivo, para o serviço
        e o religa; sem esta limpeza o processo novo leria o pedido antigo e
        sairia na primeira volta. Também vale para um arquivo esquecido por
        alguém que parou o worker à mão.
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
        """Grava o pedido — o que o deploy faz em `.bat`; aqui serve a testes e à CLI."""
        self._arquivo.parent.mkdir(parents=True, exist_ok=True)
        self._arquivo.write_text(motivo or "parada solicitada", encoding="utf-8")
