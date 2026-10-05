"""The rescaled-weight step of the scheduled cycle: simulation by default, never breaks the worker."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import date, datetime

import pytest

from wbcpython.config import Settings
from wbcpython.host import worker as mod_worker
from wbcpython.safety import ProductionWriteBlocked
from wbcpython.tracking import RepositorioTracking, TravaNaoObtida


class RepoFalso:
    ultimo: RepoFalso
    erro: Exception | None = None

    def __init__(self) -> None:
        self.fechado = False
        RepoFalso.ultimo = self

    @classmethod
    def de_settings(cls, settings):
        return cls()

    def linhas(self, *, desde):
        if RepoFalso.erro:
            raise RepoFalso.erro
        self.desde = desde
        return ["linha"]

    def close(self) -> None:
        self.fechado = True


class Cliente:
    def __init__(self, *a, **k) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a) -> None:
        return None


@pytest.fixture
def passo(monkeypatch: pytest.MonkeyPatch, tmp_path):
    chamadas: list[dict] = []
    RepoFalso.erro = None
    monkeypatch.setattr(mod_worker, "RepositorioPesosReescaladosHana", RepoFalso)
    monkeypatch.setattr(mod_worker.pesos_reescalados, "conferir", lambda linhas, **kw: chamadas.append(kw))
    monkeypatch.setattr(mod_worker, "_agora", lambda: datetime(2026, 10, 5, 10, 0))
    worker = mod_worker.WorkerIntegracao(
        Settings(_env_file=None),  # type: ignore[call-arg]
        tracking=RepositorioTracking.a_partir_da_url(f"sqlite:///{tmp_path}/t.db"),
    )
    return worker, chamadas


def test_simulacao_nao_tem_como_gravar(passo) -> None:
    worker, chamadas = passo
    worker._pesos_reescalados()
    assert chamadas[0]["gravar"] is None and chamadas[0]["a_partir_de"] is None
    assert chamadas[0]["memoria"] is worker._pesos
    assert RepoFalso.ultimo.desde == date(2026, 10, 2) and RepoFalso.ultimo.fechado


def test_com_inicio_definido_a_escrita_abre_sob_a_trava(passo, monkeypatch) -> None:
    worker, chamadas = passo
    inicio = datetime(2026, 10, 13, 8, 0)
    travas: list[bool] = []

    @contextmanager
    def trava(**kw):
        travas.append(True)
        yield lambda: None

    monkeypatch.setattr(mod_worker.pesos_reescalados, "GRAVA_A_PARTIR_DE", inicio)
    monkeypatch.setattr(mod_worker, "ServiceLayerClient", Cliente)
    monkeypatch.setattr(worker._tracking, "trava_de_execucao", trava)
    worker._pesos_reescalados()
    assert chamadas[0]["a_partir_de"] == inicio and chamadas[0]["parar"] == worker.parada_solicitada
    assert travas == []  # opened only by `conferir`, when there is a line to write
    with chamadas[0]["gravar"]() as documentos:
        assert travas == [True] and hasattr(documentos, "atualizar_pesos")


def test_trava_ocupada_fica_para_o_proximo_ciclo(passo, monkeypatch, caplog) -> None:
    worker, _ = passo

    def conferir(linhas, **kw):
        raise TravaNaoObtida("ciclo do painel em andamento")

    monkeypatch.setattr(mod_worker.pesos_reescalados, "conferir", conferir)
    worker._pesos_reescalados()
    assert "falhou" not in caplog.text and RepoFalso.ultimo.fechado


def test_parada_solicitada_nem_comeca(passo) -> None:
    worker, chamadas = passo
    worker.solicitar_parada()
    worker._pesos_reescalados()
    assert chamadas == []


def test_falha_vira_aviso_uma_vez_e_o_worker_segue(passo, caplog) -> None:
    worker, _ = passo
    RepoFalso.erro = RuntimeError("HANA fora")
    worker._pesos_reescalados()
    worker._pesos_reescalados()
    assert caplog.text.count("Conferência dos pesos reescalados falhou: HANA fora") == 1
    assert RepoFalso.ultimo.fechado


def test_trava_de_producao_sai_como_erro(passo, caplog) -> None:
    worker, _ = passo
    RepoFalso.erro = ProductionWriteBlocked("escrita em produção fora da .11")
    worker._pesos_reescalados()
    assert caplog.records[-1].levelno == logging.ERROR


def test_o_ciclo_agendado_confere_os_pesos(passo, monkeypatch) -> None:
    worker, _ = passo
    feito: list[str] = []
    monkeypatch.setattr(type(worker), "executar_ciclo", lambda self, **kw: mod_worker.ResultadoExecucao())
    monkeypatch.setattr(type(worker), "_pesos_reescalados", lambda self: feito.append("pesos"))
    worker._ciclo_agendado()
    assert feito == ["pesos"]
