"""Uma senha por sistema: as credenciais do WBC caem nos nomes do SIS quando as dele faltam.

Decisão do Marcelo em 09/09/2026, na hora de rotacionar as senhas: o `.env` da .11
descrevia o mesmo HANA, o mesmo SQL Server e o mesmo usuário do Service Layer duas vezes,
com nomes diferentes — e trocar uma senha era editar duas linhas e esquecer uma.

O que este arquivo crava: (1) só o nome do SIS presente → o WBC o usa; (2) os dois
presentes → o do WBC ganha; (3) a company de escrita e o ambiente NUNCA caem no SIS.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wbcpython.config import HanaSettings, ServiceLayerSettings, Settings, WbcSqlSettings


class TestServiceLayer:
    def test_cai_no_op_sl_quando_sl_falta(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OP_SL_USERNAME", "orcaview")
        monkeypatch.setenv("OP_SL_PASSWORD", "segredo-op")
        s = ServiceLayerSettings(_env_file=None)  # type: ignore[call-arg]
        assert s.username == "orcaview"
        assert s.password.get_secret_value() == "segredo-op"

    def test_o_nome_do_wbc_ganha(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OP_SL_USERNAME", "orcaview")
        monkeypatch.setenv("OP_SL_PASSWORD", "segredo-op")
        monkeypatch.setenv("SL_USERNAME", "outro")
        monkeypatch.setenv("SL_PASSWORD", "segredo-sl")
        s = ServiceLayerSettings(_env_file=None)  # type: ignore[call-arg]
        assert s.username == "outro"
        assert s.password.get_secret_value() == "segredo-sl"

    def test_a_company_nao_cai_no_op_sl(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Apontar a ESCRITA para produção é um ato explícito no bloco WBC — herdar a company
        de outro módulo em silêncio faria um `.env` de homologação escrever em produção."""
        monkeypatch.setenv("OP_SL_COMPANY_DB", "SBOALTAMIRAPROD")
        monkeypatch.setenv("OP_SL_USERNAME", "orcaview")
        s = ServiceLayerSettings(_env_file=None)  # type: ignore[call-arg]
        assert s.company_db == "SBOALTAMIRAHOMOLOG"


class TestSqlServerDoWbc:
    def test_cai_em_sql_e_sqlserver(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SQL_HOST", "192.168.0.1")
        monkeypatch.setenv("SQLSERVER_PORT", "1434")
        monkeypatch.setenv("SQL_USER", "sap_user")
        monkeypatch.setenv("SQL_PASSWORD", "segredo-sql")
        s = WbcSqlSettings(_env_file=None)  # type: ignore[call-arg]
        assert (s.host, s.port, s.username) == ("192.168.0.1", 1434, "sap_user")
        assert s.password.get_secret_value() == "segredo-sql"
        assert s.database == "WBCCAD"  # o default do WBC, quando ninguém diz

    def test_o_nome_do_wbc_ganha(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SQL_HOST", "10.0.0.1")
        monkeypatch.setenv("WBC_SQL_HOST", "192.168.0.1")
        assert WbcSqlSettings(_env_file=None).host == "192.168.0.1"  # type: ignore[call-arg]


class TestHana:
    def test_cai_em_sap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SAP_HOST", "sapbusinessonehana-vm")
        monkeypatch.setenv("SAP_USER", "ALTAMIRA")
        monkeypatch.setenv("SAP_PASSWORD", "segredo-hana")
        monkeypatch.setenv("SAP_SCHEMA", "SBOALTAMIRAPROD")
        s = HanaSettings(_env_file=None)  # type: ignore[call-arg]
        assert (s.host, s.username, s.schema_name) == ("sapbusinessonehana-vm", "ALTAMIRA", "SBOALTAMIRAPROD")
        assert s.password.get_secret_value() == "segredo-hana"

    def test_hana_schema_ganha_de_sap_schema(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SAP_SCHEMA", "SBOALTAMIRAPROD")
        monkeypatch.setenv("HANA_SCHEMA", "SBOALTAMIRAHOMOLOG")
        assert HanaSettings(_env_file=None).schema_name == "SBOALTAMIRAHOMOLOG"  # type: ignore[call-arg]

    def test_construir_por_nome_de_campo_continua_valendo(self) -> None:
        """Os testes de infraestrutura constroem `HanaSettings(host=..., HANA_SCHEMA=...)`."""
        s = HanaSettings(_env_file=None, host="h", username="u", HANA_SCHEMA="X")  # type: ignore[call-arg]
        assert (s.host, s.username, s.schema_name) == ("h", "u", "X")


class TestPeloArquivoEnv:
    def test_um_env_so_com_os_nomes_do_sis_configura_o_wbc(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """O caso da .11: um `.env` só no cwd, lido pelo worker sem o `load_dotenv()` do SIS.

        `Settings()` sem argumento, e não `Settings(_env_file=...)`: as configurações
        aninhadas nascem de `default_factory` e leem o `.env` do cwd — um caminho
        passado ao `Settings` de cima não chega nelas (só o `None` é propagado).
        """
        monkeypatch.chdir(tmp_path)
        env = tmp_path / ".env"
        env.write_text(
            "SAP_HOST=hana\nSAP_USER=ALTAMIRA\nSAP_PASSWORD=ph\nSAP_SCHEMA=SBOALTAMIRAPROD\n"
            "SQL_HOST=192.168.0.1\nSQL_USER=sap_user\nSQL_PASSWORD=ps\n"
            "OP_SL_USERNAME=orcaview\nOP_SL_PASSWORD=po\n"
            "SL_COMPANY_DB=SBOALTAMIRAPROD\nWBC_ENVIRONMENT=prod\nWBC_BLOCK_PRODUCTION_WRITES=false\n",
            encoding="utf-8",
        )
        s = Settings()
        assert s.hana.username == "ALTAMIRA" and s.hana.schema_name == "SBOALTAMIRAPROD"
        assert s.wbc_sql.host == "192.168.0.1" and s.wbc_sql.username == "sap_user"
        assert s.service_layer.username == "orcaview"
        assert s.service_layer.password.get_secret_value() == "po"
        assert s.service_layer.company_db == "SBOALTAMIRAPROD"
        assert s.targets_production and not s.block_production_writes

    def test_env_file_none_nao_le_nada(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("SAP_USER=nao-deveria\n", encoding="utf-8")
        assert HanaSettings(_env_file=None).username == ""  # type: ignore[call-arg]
