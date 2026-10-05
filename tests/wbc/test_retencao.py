"""Retenção do acompanhamento: decisão repetida não grava; faxina apaga só decisão velha.

Medido em produção em 08/09/2026: 934.634 eventos de decisão em 6 dias (99,4 % da
tabela, ~95 MB/dia), dos quais 12.559 eram mudanças de verdade. O resto era o
ciclo de 3 minutos regravando "nada a fazer" para cada um dos 1.682 orçamentos.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import update

from wbcpython.cli import main
from wbcpython.config import Settings, get_settings
from wbcpython.host import worker as mod_worker
from wbcpython.tracking import RepositorioTracking, TipoEvento
from wbcpython.tracking.modelos import Evento


@pytest.fixture
def repo(tmp_path) -> RepositorioTracking:
    return RepositorioTracking.a_partir_da_url(f"sqlite:///{tmp_path}/tracking.db")


def _envelhecer(repo: RepositorioTracking, orcnum: str, *, dias: int) -> None:
    """Data os eventos do orçamento para `dias` atrás (o relógio real não espera)."""
    with repo._sessao() as s, s.begin():
        s.execute(
            update(Evento)
            .where(Evento.orcnum == orcnum)
            .values(momento=datetime.now() - timedelta(days=dias))
        )


class TestDecisaoRepetidaNaoGrava:
    def test_a_mesma_decisao_duas_vezes_e_um_evento(self, repo: RepositorioTracking) -> None:
        assert repo.registrar_evento("X", regra="revisao_congelada", mensagem="nada a fazer") is True
        assert repo.registrar_evento("X", regra="revisao_congelada", mensagem="nada a fazer") is False
        assert len(repo.eventos("X")) == 1

    def test_decisao_diferente_grava(self, repo: RepositorioTracking) -> None:
        repo.registrar_evento("X", regra="revisao_congelada", mensagem="nada a fazer")
        repo.registrar_evento("X", regra="emitido_sem_cotacao", mensagem="vai criar cotação")
        assert [e.regra for e in repo.eventos("X")] == ["emitido_sem_cotacao", "revisao_congelada"]

    def test_detalhes_diferentes_gravam(self, repo: RepositorioTracking) -> None:
        """Mesma regra e mensagem com detalhe diferente é mudança — não é repetição."""
        repo.registrar_evento("X", regra="r", mensagem="m", detalhes={"acoes": []})
        repo.registrar_evento("X", regra="r", mensagem="m", detalhes={"acoes": ["criar_pedido"]})
        assert len(repo.eventos("X")) == 2

    def test_depois_de_uma_acao_a_mesma_decisao_volta_a_ser_gravada(
        self, repo: RepositorioTracking
    ) -> None:
        """A comparação é com o ÚLTIMO evento do orçamento, não com a última decisão:
        decisão → ação → a mesma decisão é a história de verdade ("voltou a não ter
        nada a fazer depois de agir")."""
        repo.registrar_evento("X", regra="r", mensagem="m")
        repo.registrar_evento("X", tipo=TipoEvento.ACAO, mensagem="criou cotação")
        assert repo.registrar_evento("X", regra="r", mensagem="m") is True
        assert len(repo.eventos("X")) == 3

    def test_acao_e_erro_sempre_gravam(self, repo: RepositorioTracking) -> None:
        repo.registrar_evento("X", tipo=TipoEvento.ACAO, mensagem="vinculou")
        repo.registrar_evento("X", tipo=TipoEvento.ACAO, mensagem="vinculou")
        repo.registrar_erro("X", "falhou")
        repo.registrar_erro("X", "falhou")
        assert len(repo.eventos("X")) == 4

    def test_orcamentos_diferentes_nao_se_misturam(self, repo: RepositorioTracking) -> None:
        repo.registrar_evento("A", regra="r", mensagem="m")
        assert repo.registrar_evento("B", regra="r", mensagem="m") is True


class TestFaxina:
    def test_apaga_so_decisao_mais_velha_que_o_prazo(self, repo: RepositorioTracking) -> None:
        repo.registrar_evento("VELHO", regra="r", mensagem="decisão antiga")
        repo.registrar_evento("VELHO", tipo=TipoEvento.ACAO, mensagem="ação antiga")
        repo.registrar_erro("VELHO", "erro antigo")
        _envelhecer(repo, "VELHO", dias=10)
        repo.registrar_evento("NOVO", regra="r", mensagem="decisão de hoje")

        apagados = repo.faxina_de_eventos(dias=6)

        assert apagados == 1
        assert [e.tipo for e in repo.eventos("VELHO")] == [TipoEvento.ERRO, TipoEvento.ACAO]
        assert len(repo.eventos("NOVO")) == 1

    def test_sem_nada_a_apagar_devolve_zero(self, repo: RepositorioTracking) -> None:
        repo.registrar_evento("X", regra="r", mensagem="m")
        assert repo.faxina_de_eventos(dias=6) == 0
        assert len(repo.eventos("X")) == 1

    def test_prazo_negativo_e_recusado(self, repo: RepositorioTracking) -> None:
        with pytest.raises(ValueError):
            repo.faxina_de_eventos(dias=-1)

    def test_o_acompanhamento_do_orcamento_fica(self, repo: RepositorioTracking) -> None:
        """Apaga eventos, nunca a linha do orçamento: o painel continua a listá-lo."""
        repo.registrar_verificacao("X", cliente="BALTEAU")
        repo.registrar_evento("X", regra="r", mensagem="m")
        _envelhecer(repo, "X", dias=30)
        repo.faxina_de_eventos(dias=6)
        registro = repo.obter("X")
        assert registro is not None and registro.cliente == "BALTEAU"


class TestConfiguracao:
    def test_padrao_e_seis_dias(self) -> None:
        assert Settings(_env_file=None).eventos_retencao_dias == 6  # type: ignore[call-arg]

    def test_vem_do_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EVENTOS_RETENCAO_DIAS", "30")
        assert Settings(_env_file=None).eventos_retencao_dias == 30  # type: ignore[call-arg]

    def test_negativo_e_recusado(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EVENTOS_RETENCAO_DIAS", "-1")
        with pytest.raises(ValueError):
            Settings(_env_file=None)  # type: ignore[call-arg]


class TestWorkerFazFaxinaUmaVezPorDia:
    def _worker(self, tmp_path, monkeypatch, **env) -> mod_worker.WorkerIntegracao:
        # Sexta 04/09/2026 às 10:00: dia e horário de trabalho do padrão.
        monkeypatch.setattr(mod_worker, "_agora", lambda: datetime(2026, 9, 4, 10, 0))
        worker = mod_worker.WorkerIntegracao(
            Settings(_env_file=None, **env),  # type: ignore[call-arg]
            tracking=RepositorioTracking.a_partir_da_url(f"sqlite:///{tmp_path}/t.db"),
        )
        # O ciclo em si não é o assunto aqui.
        monkeypatch.setattr(worker, "executar_ciclo", lambda **kw: mod_worker.ResultadoExecucao())
        return worker

    def test_roda_depois_do_ciclo_e_so_uma_vez_no_dia(self, tmp_path, monkeypatch) -> None:
        worker = self._worker(tmp_path, monkeypatch)
        chamadas: list[int] = []
        monkeypatch.setattr(
            worker._tracking, "faxina_de_eventos", lambda *, dias: chamadas.append(dias) or 0
        )
        worker._ciclo_agendado()
        worker._ciclo_agendado()
        worker._ciclo_agendado()
        assert chamadas == [6]

    def test_apaga_de_verdade(self, tmp_path, monkeypatch) -> None:
        worker = self._worker(tmp_path, monkeypatch)
        repo = worker._tracking
        repo.registrar_evento("X", regra="r", mensagem="m")
        _envelhecer(repo, "X", dias=10)
        worker._ciclo_agendado()
        assert repo.eventos("X") == []

    def test_zero_desliga(self, tmp_path, monkeypatch) -> None:
        worker = self._worker(tmp_path, monkeypatch, EVENTOS_RETENCAO_DIAS="0")
        chamadas: list[int] = []
        monkeypatch.setattr(
            worker._tracking, "faxina_de_eventos", lambda *, dias: chamadas.append(dias) or 0
        )
        worker._ciclo_agendado()
        assert chamadas == []

    def test_falha_na_faxina_nao_derruba_o_ciclo(self, tmp_path, monkeypatch, caplog) -> None:
        worker = self._worker(tmp_path, monkeypatch)

        def explode(*, dias):
            raise RuntimeError("disco cheio")

        monkeypatch.setattr(worker._tracking, "faxina_de_eventos", explode)
        with caplog.at_level("WARNING", logger="wbcpython.host.worker"):
            resultado = worker._ciclo_agendado()
        assert isinstance(resultado, mod_worker.ResultadoExecucao)
        assert "disco cheio" in caplog.text


class TestComandoFaxina:
    @pytest.fixture(autouse=True)
    def _ambiente(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        monkeypatch.setenv("TRACKING_DB_URL", f"sqlite:///{tmp_path}/cli.db")
        monkeypatch.setenv("LOG_FILE", "")
        get_settings.cache_clear()
        yield
        get_settings.cache_clear()

    def test_apaga_e_relata(self, tmp_path, capsys: pytest.CaptureFixture) -> None:
        repo = RepositorioTracking.a_partir_da_url(f"sqlite:///{tmp_path}/cli.db")
        repo.registrar_evento("X", regra="r", mensagem="m")
        _envelhecer(repo, "X", dias=10)
        assert main(["faxina", "--dias", "6"]) == 0
        assert "1 evento(s) de decisão" in capsys.readouterr().out
        assert repo.eventos("X") == []

    def test_sem_dias_usa_a_retencao_do_env(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        monkeypatch.setenv("EVENTOS_RETENCAO_DIAS", "2")
        get_settings.cache_clear()
        assert main(["faxina"]) == 0
        assert "mais de 2 dia(s)" in capsys.readouterr().out

    def test_retencao_zero_nao_apaga(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        monkeypatch.setenv("EVENTOS_RETENCAO_DIAS", "0")
        get_settings.cache_clear()
        repo = RepositorioTracking.a_partir_da_url(f"sqlite:///{tmp_path}/cli.db")
        repo.registrar_evento("X", regra="r", mensagem="m")
        _envelhecer(repo, "X", dias=10)
        assert main(["faxina"]) == 0
        assert "desligada" in capsys.readouterr().out
        assert len(repo.eventos("X")) == 1
