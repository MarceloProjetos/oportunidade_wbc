"""Testes da configuração.

Garantem que a configuração é realmente parametrizável por ambiente (nada de
nome de company DB fixo no código) e que o resumo de ambiente nunca vaza
credenciais.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from wbcpython.config import Settings


@pytest.fixture(autouse=True)
def _ambiente_limpo(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Isola os testes do `.env` da máquina — inclusive das configurações aninhadas.

    O `monkeypatch.chdir` é o que faz o trabalho, e é ele que faltava. As
    variáveis apagadas abaixo protegiam só o `Settings` de cima; as aninhadas
    (`ServiceLayerSettings`, `HanaSettings`, …) nascem de `default_factory`,
    **sem** o `_env_file=None`, e por isso liam o `.env` do diretório corrente
    mesmo com o topo isolado.

    Isso passou despercebido enquanto todo `.env` de máquina apontava para
    homologação: o valor que vazava era igual ao esperado. Na virada para
    produção a máscara caiu e três testes ficaram vermelhos — sem nenhuma
    mudança de código, só porque a máquina passou a apontar para outro lugar.

    Um teste que muda de resultado conforme a configuração da máquina é pior do
    que um teste ausente: torna a suíte inútil justamente quando o ambiente
    muda, que é quando ela mais precisa ser confiável.
    """
    monkeypatch.chdir(tmp_path)
    for var in (
        "WBC_ENVIRONMENT",
        "WBC_BLOCK_PRODUCTION_WRITES",
        "WBC_PRODUCTION_COMPANY_DB",
        "SL_COMPANY_DB",
        "SL_USERNAME",
        "SL_PASSWORD",
        "HANA_SCHEMA",
        "MESES_DE_JANELA",
        "LIMITE_DE_ESCRITA_POR_CICLO",
    ):
        monkeypatch.delenv(var, raising=False)


def _settings(**env: str) -> Settings:
    # `_env_file=None` isola o `Settings` de cima; as aninhadas dependem do
    # `chdir` do fixture, porque `default_factory` as constrói sem este
    # argumento. As duas defesas juntas.
    return Settings(_env_file=None, **env)  # type: ignore[call-arg]


class TestIsolamentoDaConfiguracao:
    """A suíte não pode mudar de resultado conforme o `.env` da máquina."""

    def test_env_do_diretorio_nao_vaza_para_as_aninhadas(self, tmp_path) -> None:
        """Regressão da virada para produção.

        Com um `.env` apontando para produção no diretório corrente, os testes
        de `Settings` ficavam vermelhos — `service_layer.company_db` vinha do
        arquivo, mesmo com `_env_file=None` no topo.
        """
        (tmp_path / ".env").write_text(
            "SL_COMPANY_DB=SBOALTAMIRAPROD\nWBC_ENVIRONMENT=prod\n", encoding="utf-8"
        )
        s = _settings()
        assert s.service_layer.company_db != "SBOALTAMIRAPROD"
        assert s.targets_production is False


class TestPadroesSeguros:
    def test_padrao_e_homologacao_com_trava_ligada(self) -> None:
        s = _settings()
        assert s.environment == "homolog"
        assert s.block_production_writes is True
        assert s.targets_production is False

    def test_company_db_padrao_nao_e_producao(self) -> None:
        s = _settings()
        assert s.service_layer.company_db.casefold() != s.production_company_db.casefold()


class TestDeteccaoDeProducao:
    def test_detecta_quando_apontado_para_producao(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SL_COMPANY_DB", "SBOALTAMIRAPROD")
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert s.targets_production is True

    def test_nome_de_producao_e_configuravel(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # A trava não depende de a string estar escrita no código-fonte.
        monkeypatch.setenv("WBC_PRODUCTION_COMPANY_DB", "OUTRA_PROD")
        monkeypatch.setenv("SL_COMPANY_DB", "OUTRA_PROD")
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert s.targets_production is True


class TestDescribeEnvironment:
    def test_nao_vaza_credenciais(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SL_USERNAME", "usuario_secreto")
        monkeypatch.setenv("SL_PASSWORD", "senha_secreta")
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        resumo = s.describe_environment()
        assert "senha_secreta" not in resumo
        assert "usuario_secreto" not in resumo

    def test_sinaliza_claramente_producao(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SL_COMPANY_DB", "SBOALTAMIRAPROD")
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert "PRODUÇÃO" in s.describe_environment()

    def test_sinaliza_homologacao(self) -> None:
        assert "homologação" in _settings().describe_environment()


class TestJanelaDeBusca:
    """A janela é configurável porque o valor certo ainda não é conhecido.

    O legado montava a data misturando -6 meses no ano com -9 no mês; seis é
    hipótese, não certeza. Enquanto o negócio não responde, o operador ajusta
    pelo `.env` em vez de esperar uma nova versão.
    """

    def test_padrao_e_seis_meses(self) -> None:
        assert Settings(_env_file=None).meses_de_janela == 6  # type: ignore[call-arg]

    def test_vem_do_ambiente(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MESES_DE_JANELA", "9")
        assert Settings(_env_file=None).meses_de_janela == 9  # type: ignore[call-arg]

    @pytest.mark.parametrize("valor", ["0", "-1"])
    def test_recusa_janela_vazia_ou_negativa(
        self, valor: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Zero traria só o mês corrente; negativo, uma data no futuro.

        Os dois fariam a integração parar de enxergar quase tudo — e sem erro,
        que é o pior jeito de falhar. Melhor recusar no arranque.
        """
        monkeypatch.setenv("MESES_DE_JANELA", valor)
        with pytest.raises(ValidationError):
            Settings(_env_file=None)  # type: ignore[call-arg]

    def test_o_worker_usa_a_configuracao(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Regressão: a constante do módulo não pode voltar a mandar."""
        from wbcpython.host.worker import WorkerIntegracao
        from wbcpython.tracking import RepositorioTracking

        monkeypatch.setenv("MESES_DE_JANELA", "9")
        worker = WorkerIntegracao(
            Settings(_env_file=None),  # type: ignore[call-arg]
            tracking=RepositorioTracking.a_partir_da_url(f"sqlite:///{tmp_path}/t.db"),
        )
        assert worker._meses == 9

    def test_parametro_explicito_vence_a_configuracao(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from wbcpython.host.worker import WorkerIntegracao
        from wbcpython.tracking import RepositorioTracking

        monkeypatch.setenv("MESES_DE_JANELA", "9")
        worker = WorkerIntegracao(
            Settings(_env_file=None),  # type: ignore[call-arg]
            tracking=RepositorioTracking.a_partir_da_url(f"sqlite:///{tmp_path}/t.db"),
            meses_de_janela=3,
        )
        assert worker._meses == 3
