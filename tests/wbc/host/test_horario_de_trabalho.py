"""O worker trabalha das 06:30 às 19:00; fora disso, fica vivo e parado.

A guarda vive no ciclo **agendado**, e não em `executar_ciclo`: o comando
`wbcpython ciclo` é alguém pedindo, e recusar um pedido explícito por causa do
relógio seria obstrução, não proteção.
"""

from __future__ import annotations

import logging
from datetime import datetime, time

import pytest

from wbcpython.config import Settings
from wbcpython.host import worker as mod_worker
from wbcpython.tracking import RepositorioTracking


def _worker(tmp_path, **env) -> mod_worker.WorkerIntegracao:
    return mod_worker.WorkerIntegracao(
        Settings(_env_file=None, **env),  # type: ignore[call-arg]
        tracking=RepositorioTracking.a_partir_da_url(f"sqlite:///{tmp_path}/t.db"),
    )


#: 2026-09-04 é uma sexta; 05 sábado, 06 domingo, 07 segunda. Os testes de
#: horário usam a sexta para não misturar as duas guardas.
SEXTA, SABADO, DOMINGO, SEGUNDA = 4, 5, 6, 7


@pytest.fixture
def relogio(monkeypatch: pytest.MonkeyPatch):
    """Congela o relógio do worker — sem isto o teste dependeria da hora real."""

    def em(hora: str, dia: int = SEXTA):
        h, m = map(int, hora.split(":"))
        monkeypatch.setattr(mod_worker, "_agora", lambda: datetime(2026, 9, dia, h, m))

    return em


@pytest.fixture
def rodou(monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    executou: list[bool] = []
    monkeypatch.setattr(
        mod_worker.WorkerIntegracao,
        "executar_ciclo",
        lambda self, **kw: executou.append(True) or mod_worker.ResultadoExecucao(),
    )
    return executou


class TestHorarioDeTrabalho:
    @pytest.mark.parametrize("hora", ["06:30", "07:00", "12:00", "18:59", "19:00"])
    def test_dentro_do_horario_executa(self, hora, relogio, rodou, tmp_path) -> None:
        """As pontas são inclusivas: às 06:30 já trabalha, às 19:00 ainda."""
        relogio(hora)
        _worker(tmp_path)._ciclo_agendado()

        assert rodou == [True]

    @pytest.mark.parametrize("hora", ["00:00", "06:29", "19:01", "23:59"])
    def test_fora_do_horario_nao_executa(self, hora, relogio, rodou, tmp_path) -> None:
        relogio(hora)
        _worker(tmp_path)._ciclo_agendado()

        assert rodou == []

    def test_o_comando_manual_nao_e_barrado(self, relogio, tmp_path, monkeypatch) -> None:
        """`wbcpython ciclo` às 22h tem de rodar: há uma pessoa pedindo, e foi
        assim que os defeitos de hoje foram investigados."""
        relogio("22:00")
        chamou: list[bool] = []
        monkeypatch.setattr(
            mod_worker.WorkerIntegracao,
            "_ciclo",
            lambda self, **kw: chamou.append(True) or mod_worker.ResultadoExecucao(),
        )
        _worker(tmp_path).executar_ciclo()

        assert chamou == [True]

    def test_o_horario_e_configuravel(self, relogio, rodou, tmp_path) -> None:
        relogio("21:00")
        w = _worker(
            tmp_path,
            worker_horario_inicio=time(20, 0),
            worker_horario_fim=time(23, 0),
        )
        w._ciclo_agendado()

        assert rodou == [True]

    def test_janela_que_atravessa_a_meia_noite(self, relogio, rodou, tmp_path) -> None:
        """`fim` antes de `inicio` é turno da noite, e não configuração inválida.

        Sem isto, quem configurasse 19:00–06:30 teria um worker que nunca roda
        e nunca diz por quê.
        """

        def noturno():
            return _worker(
                tmp_path,
                worker_horario_inicio=time(19, 0),
                worker_horario_fim=time(6, 30),
            )

        relogio("23:00")
        noturno()._ciclo_agendado()
        relogio("05:00")
        noturno()._ciclo_agendado()
        assert rodou == [True, True]

        rodou.clear()
        relogio("12:00")
        noturno()._ciclo_agendado()
        assert rodou == []

    def test_o_aviso_sai_uma_vez_por_transicao(
        self, relogio, rodou, tmp_path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Com ciclo de 3 minutos, avisar a cada volta encheria a noite com
        ~230 linhas iguais — e log que se repete assim deixa de ser lido."""
        relogio("22:00")
        w = _worker(tmp_path)
        with caplog.at_level(logging.INFO, logger="wbcpython.host.worker"):
            for _ in range(5):
                w._ciclo_agendado()

        fora = [r for r in caplog.records if "Fora do horário" in r.getMessage()]
        assert len(fora) == 1

    def test_avisa_ao_retomar(
        self, relogio, rodou, tmp_path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Quem lê o log de manhã precisa ver onde a integração voltou."""
        w = _worker(tmp_path)
        relogio("22:00")
        w._ciclo_agendado()
        with caplog.at_level(logging.INFO, logger="wbcpython.host.worker"):
            relogio("07:00")
            w._ciclo_agendado()

        assert any("retomados" in r.getMessage() for r in caplog.records)

    def test_o_padrao_e_seis_e_meia_as_dezenove(self) -> None:
        config = Settings(_env_file=None)  # type: ignore[call-arg]
        assert config.worker_horario_inicio == time(6, 30)
        assert config.worker_horario_fim == time(19, 0)


class TestFimDeSemana:
    """Sábado e domingo o worker não roda.

    Pedido do usuário. O dia é verificado **antes** da hora: num sábado às 10h,
    dizer "fora do horário" estaria tecnicamente errado e mandaria quem lê o log
    procurar no lugar errado.
    """

    @pytest.mark.parametrize("dia", [SABADO, DOMINGO])
    def test_nao_roda_no_fim_de_semana(self, dia, relogio, rodou, tmp_path) -> None:
        relogio("10:00", dia)
        _worker(tmp_path)._ciclo_agendado()

        assert rodou == []

    def test_nao_roda_no_fim_de_semana_nem_dentro_do_horario(
        self, relogio, rodou, tmp_path
    ) -> None:
        """A hora não salva o sábado: são duas guardas, e basta uma barrar."""
        for hora in ("06:30", "12:00", "19:00"):
            relogio(hora, SABADO)
            _worker(tmp_path)._ciclo_agendado()

        assert rodou == []

    @pytest.mark.parametrize("dia", [SEXTA, SEGUNDA])
    def test_dia_util_continua_rodando(self, dia, relogio, rodou, tmp_path) -> None:
        relogio("10:00", dia)
        _worker(tmp_path)._ciclo_agendado()

        assert rodou == [True]

    def test_o_motivo_no_log_diz_o_dia(
        self, relogio, rodou, tmp_path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """ "Fora do horário" num sábado às 10h seria mentira útil para ninguém."""
        relogio("10:00", SABADO)
        with caplog.at_level(logging.INFO, logger="wbcpython.host.worker"):
            _worker(tmp_path)._ciclo_agendado()

        mensagens = " ".join(r.getMessage() for r in caplog.records)
        assert "Sábado não é dia de trabalho" in mensagens
        assert "Fora do horário" not in mensagens

    def test_o_nome_do_dia_nao_depende_do_locale(self, relogio, rodou, tmp_path, caplog) -> None:
        """`%A` sairia "Saturday" numa máquina sem pt_BR — e o worker roda como
        serviço, onde o locale é o que o sistema deu."""
        relogio("10:00", DOMINGO)
        with caplog.at_level(logging.INFO, logger="wbcpython.host.worker"):
            _worker(tmp_path)._ciclo_agendado()

        assert "Domingo" in " ".join(r.getMessage() for r in caplog.records)

    def test_a_virada_de_motivo_e_anunciada(
        self, relogio, rodou, tmp_path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Sexta 19:01 para por horário; sábado 08:00 para por ser sábado.

        Guardar um booleano "está parado" faria o segundo aviso ser engolido, e
        quem abrisse o log na segunda não saberia por que a integração passou o
        fim de semana quieta.
        """
        w = _worker(tmp_path)
        with caplog.at_level(logging.INFO, logger="wbcpython.host.worker"):
            relogio("19:01", SEXTA)
            w._ciclo_agendado()
            relogio("08:00", SABADO)
            w._ciclo_agendado()

        mensagens = " ".join(r.getMessage() for r in caplog.records)
        assert "Fora do horário" in mensagens
        assert "Sábado não é dia de trabalho" in mensagens

    def test_o_mesmo_motivo_nao_se_repete(
        self, relogio, rodou, tmp_path, caplog: pytest.LogCaptureFixture
    ) -> None:
        w = _worker(tmp_path)
        with caplog.at_level(logging.INFO, logger="wbcpython.host.worker"):
            for hora in ("08:00", "10:00", "14:00", "18:00"):
                relogio(hora, SABADO)
                w._ciclo_agendado()

        avisos = [r for r in caplog.records if "não é dia de trabalho" in r.getMessage()]
        assert len(avisos) == 1

    def test_os_dias_sao_configuraveis(self, relogio, rodou, tmp_path) -> None:
        """Escala de sábado é decisão de negócio, não de código."""
        relogio("10:00", SABADO)
        _worker(tmp_path, worker_dias_de_trabalho="1,2,3,4,5,6")._ciclo_agendado()

        assert rodou == [True]

    def test_dia_invalido_e_recusado_na_partida(self) -> None:
        """Um `8` ignorado em silêncio viraria um worker que não roda num dia e
        ninguém sabe por quê."""
        with pytest.raises(ValueError, match="fora de 1–7"):
            Settings(_env_file=None, worker_dias_de_trabalho="1,8")  # type: ignore[call-arg]

    def test_lista_vazia_e_recusada(self) -> None:
        """Sem dia nenhum o worker nunca rodaria — e pareceria estar rodando."""
        with pytest.raises(ValueError, match="não rodaria nunca"):
            Settings(_env_file=None, worker_dias_de_trabalho="")  # type: ignore[call-arg]

    def test_o_padrao_e_de_segunda_a_sexta(self) -> None:
        config = Settings(_env_file=None)  # type: ignore[call-arg]
        assert config.dias_de_trabalho == frozenset({1, 2, 3, 4, 5})
        assert config.dias_de_trabalho_por_extenso == "seg, ter, qua, qui, sex"

    def test_le_do_env_de_verdade(self, tmp_path) -> None:
        """O teste que faltava, e o defeito que ele não pegou.

        Todos os outros constroem `Settings(...)` passando o valor pronto, o que
        **não** passa pela fonte do `.env`. Tipado como `frozenset[int]`, o
        `pydantic-settings` classificava o campo como complexo e tentava
        `json.loads("1,2,3,4,5")` antes de qualquer validador: `SettingsError` na
        partida, só no ambiente real. Quem pegou foi rodar em produção — este
        teste é para não depender disso de novo.
        """
        env = tmp_path / ".env"
        env.write_text(
            "WORKER_DIAS_DE_TRABALHO=1,2,3,4,5\nSL_USERNAME=u\nSL_PASSWORD=p\n",
            encoding="utf-8",
        )
        config = Settings(_env_file=str(env))  # type: ignore[call-arg]

        assert config.dias_de_trabalho == frozenset({1, 2, 3, 4, 5})

    def test_o_env_com_espacos_e_fora_de_ordem_funciona(self, tmp_path) -> None:
        """Quem edita o `.env` à mão escreve "5, 1 ,3"."""
        env = tmp_path / ".env"
        env.write_text("WORKER_DIAS_DE_TRABALHO=5, 1 ,3\n", encoding="utf-8")
        config = Settings(_env_file=str(env))  # type: ignore[call-arg]

        assert config.dias_de_trabalho == frozenset({1, 3, 5})
        assert config.dias_de_trabalho_por_extenso == "seg, qua, sex"

    def test_o_env_com_lixo_e_recusado(self, tmp_path) -> None:
        env = tmp_path / ".env"
        env.write_text("WORKER_DIAS_DE_TRABALHO=segunda,terça\n", encoding="utf-8")

        with pytest.raises(ValueError, match="não é uma lista de números"):
            Settings(_env_file=str(env))  # type: ignore[call-arg]

    def test_o_comando_manual_roda_no_domingo(self, relogio, tmp_path, monkeypatch) -> None:
        """Mesma razão do horário: `wbcpython ciclo` é alguém pedindo."""
        relogio("10:00", DOMINGO)
        chamou: list[bool] = []
        monkeypatch.setattr(
            mod_worker.WorkerIntegracao,
            "_ciclo",
            lambda self, **kw: chamou.append(True) or mod_worker.ResultadoExecucao(),
        )
        _worker(tmp_path).executar_ciclo()

        assert chamou == [True]
