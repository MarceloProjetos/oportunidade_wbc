"""Parada por arquivo: o worker termina o que está fazendo e sai, sem console nem sinal.

Nasceu do deploy das 10:14 de 09/09/2026, que matou o worker 3 s dentro do
ciclo #133 (ver `host/parada.py`). Os testes de `PedidoDeParada` rodam em
qualquer máquina; os do worker precisam do `apscheduler`.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from wbcpython.host.parada import PedidoDeParada


class TestPedidoDeParada:
    def test_sem_arquivo_nao_ha_pedido(self, tmp_path: Path) -> None:
        assert not PedidoDeParada(tmp_path / "w.stop").pendente()

    def test_arquivo_presente_e_pedido(self, tmp_path: Path) -> None:
        pedido = PedidoDeParada(tmp_path / "state" / "w.stop")
        pedido.pedir(motivo="deploy")
        assert pedido.pendente()
        assert (tmp_path / "state" / "w.stop").read_text(encoding="utf-8") == "deploy"

    def test_anuncia_uma_vez_por_pedido(self, tmp_path: Path, caplog) -> None:
        """Com voltas de 1 s no laço principal, avisar a cada volta encheria o log."""
        pedido = PedidoDeParada(tmp_path / "w.stop")
        pedido.pedir()
        with caplog.at_level(logging.INFO, logger="wbcpython.host.parada"):
            assert pedido.pendente() and pedido.pendente() and pedido.pendente()
        assert sum("Parada pedida por arquivo" in r.message for r in caplog.records) == 1

    def test_limpar_remove_e_diz_se_havia(self, tmp_path: Path) -> None:
        pedido = PedidoDeParada(tmp_path / "w.stop")
        assert not pedido.limpar(motivo="partida")
        pedido.pedir()
        assert pedido.limpar(motivo="partida")
        assert not pedido.pendente()

    def test_depois_de_limpo_um_pedido_novo_e_anunciado_de_novo(self, tmp_path: Path, caplog) -> None:
        pedido = PedidoDeParada(tmp_path / "w.stop")
        with caplog.at_level(logging.INFO, logger="wbcpython.host.parada"):
            pedido.pedir()
            pedido.pendente()
            pedido.limpar(motivo="partida")
            pedido.pendente()
            pedido.pedir()
            pedido.pendente()
        assert sum("Parada pedida por arquivo" in r.message for r in caplog.records) == 2


class TestNoWorker:
    """Fiação: o worker lê o arquivo do `Settings`, limpa na partida e para no laço."""

    @pytest.fixture
    def mod_worker(self):
        return pytest.importorskip("wbcpython.host.worker")

    def _worker(self, mod_worker, tmp_path: Path, arquivo: Path):
        from wbcpython.config import Settings
        from wbcpython.tracking import RepositorioTracking

        return mod_worker.WorkerIntegracao(
            Settings(_env_file=None, WORKER_ARQUIVO_DE_PARADA=str(arquivo)),  # type: ignore[call-arg]
            tracking=RepositorioTracking.a_partir_da_url(f"sqlite:///{tmp_path}/t.db"),
        )

    def test_o_caminho_vem_da_configuracao(self, mod_worker, tmp_path: Path) -> None:
        arquivo = tmp_path / "state" / "wbc_worker.stop"
        worker = self._worker(mod_worker, tmp_path, arquivo)
        assert worker._pedido_de_parada.arquivo == arquivo

    def test_o_padrao_e_state_wbc_worker_stop(self) -> None:
        from wbcpython.config import Settings

        assert Settings(_env_file=None).worker_arquivo_de_parada == Path("state/wbc_worker.stop")  # type: ignore[call-arg]

    def test_arquivo_presente_vira_parada_solicitada(self, mod_worker, tmp_path: Path) -> None:
        arquivo = tmp_path / "wbc_worker.stop"
        worker = self._worker(mod_worker, tmp_path, arquivo)
        assert not worker.parada_solicitada()
        PedidoDeParada(arquivo).pedir()
        assert worker.parada_solicitada()
        # Promovido a sinal: o laço principal acorda e o processo termina.
        assert worker._parar.is_set()

    def test_ciclo_agendado_nao_comeca_com_parada_pedida(
        self, mod_worker, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        arquivo = tmp_path / "wbc_worker.stop"
        worker = self._worker(mod_worker, tmp_path, arquivo)
        executou: list[bool] = []
        monkeypatch.setattr(
            mod_worker.WorkerIntegracao,
            "executar_ciclo",
            lambda self, **kw: executou.append(True) or mod_worker.ResultadoExecucao(),
        )
        PedidoDeParada(arquivo).pedir()
        worker._ciclo_agendado()
        assert executou == []

    def test_a_partida_remove_o_arquivo_esquecido(
        self, mod_worker, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """O deploy grava o arquivo, para e religa: o processo novo não pode ler
        o pedido antigo e sair na primeira volta."""
        arquivo = tmp_path / "wbc_worker.stop"
        worker = self._worker(mod_worker, tmp_path, arquivo)
        PedidoDeParada(arquivo).pedir(motivo="deploy anterior")
        monkeypatch.setattr(
            mod_worker.WorkerIntegracao,
            "_ciclo_agendado",
            lambda self: self.solicitar_parada() or mod_worker.ResultadoExecucao(),
        )
        worker.rodar_continuamente()
        assert not arquivo.exists()
