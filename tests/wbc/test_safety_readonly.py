"""Testes da trava de SOMENTE LEITURA (Regra 5).

Diretiva do projeto: a integração nunca escreve no SQL Server do WBC, em
ambiente nenhum. Quem escreve no WBC é o próprio sistema WBC.

Diferente da trava de produção, esta não tem chave de desligamento — não existe
parâmetro que a contorne, e é isso que os testes abaixo garantem.
"""

from __future__ import annotations

import ast
import inspect

import pytest

from wbcpython import safety
from wbcpython.safety import (
    ProductionWriteBlocked,
    ReadOnlyViolation,
    SafetyViolation,
    assert_read_only_sql,
    looks_like_write_sql,
)


class TestHierarquiaDeExcecoes:
    def test_ambas_derivam_de_safety_violation(self) -> None:
        assert issubclass(ReadOnlyViolation, SafetyViolation)
        assert issubclass(ProductionWriteBlocked, SafetyViolation)

    def test_sao_distintas_entre_si(self) -> None:
        # Permite tratar/observar separadamente uma violação de leitura e uma
        # tentativa de escrita em produção.
        assert not issubclass(ReadOnlyViolation, ProductionWriteBlocked)
        assert not issubclass(ProductionWriteBlocked, ReadOnlyViolation)


class TestBloqueiaEscrita:
    @pytest.mark.parametrize(
        "sql",
        [
            "INSERT INTO INTEGRACAO_ORCLST VALUES (1)",
            "UPDATE INTEGRACAO_ORCCAB SET ORCPERCOM = 5",
            "DELETE FROM INTEGRACAO_ORCITM WHERE ORCCOD = 1",
            "TRUNCATE TABLE INTEGRACAO_ORCPRD",
            "DROP TABLE INTEGRACAO_ORCLST",
            "ALTER TABLE INTEGRACAO_ORCLST ADD C INT",
            "CREATE TABLE T (C INT)",
            "EXEC sp_executesql N'update t set a=1'",
            "MERGE INTO T USING S ON (1=1) WHEN MATCHED THEN UPDATE SET a = 1",
        ],
    )
    def test_bloqueia_comandos_de_escrita(self, sql: str) -> None:
        with pytest.raises(ReadOnlyViolation):
            assert_read_only_sql(sql)

    def test_bloqueia_select_into_que_cria_tabela(self) -> None:
        with pytest.raises(ReadOnlyViolation):
            assert_read_only_sql("SELECT * INTO nova_tabela FROM INTEGRACAO_ORCLST")

    def test_bloqueia_comando_de_escrita_escondido_apos_ponto_e_virgula(self) -> None:
        # Caso clássico de injeção: o primeiro comando é inofensivo.
        with pytest.raises(ReadOnlyViolation):
            assert_read_only_sql("SELECT 1; DROP TABLE INTEGRACAO_ORCLST")

    def test_bloqueia_escrita_apos_cte(self) -> None:
        with pytest.raises(ReadOnlyViolation):
            assert_read_only_sql("WITH x AS (SELECT 1) INSERT INTO T SELECT * FROM x")

    def test_mensagem_explica_a_regra(self) -> None:
        with pytest.raises(ReadOnlyViolation) as exc:
            assert_read_only_sql("DELETE FROM T")
        mensagem = str(exc.value)
        assert "SOMENTE LEITURA" in mensagem
        assert "Regra 5" in mensagem

    def test_fonte_aparece_na_mensagem(self) -> None:
        with pytest.raises(ReadOnlyViolation) as exc:
            assert_read_only_sql("DELETE FROM T", fonte="HANA")
        assert "HANA" in str(exc.value)


class TestPermiteLeitura:
    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM INTEGRACAO_ORCLST",
            "select orccod, sitcode from integracao_orclst where sitcode = %(sitcode)s",
            "SELECT a.*, b.* FROM INTEGRACAO_ORCCAB a JOIN INTEGRACAO_ORCITM b ON a.C = b.C",
            "WITH ultimos AS (SELECT MAX(DocEntry) d FROM T) SELECT * FROM ultimos",
            "SELECT TOP 10 * FROM INTEGRACAO_ORCPRDARV ORDER BY NIVEL",
            "SELECT COUNT(*) FROM INTEGRACAO_ORCIMP",
        ],
    )
    def test_permite_consultas(self, sql: str) -> None:
        assert_read_only_sql(sql)

    def test_nao_confunde_palavra_dentro_de_literal(self) -> None:
        # Regressão: 'update' aparecia só dentro do texto, não como comando.
        assert_read_only_sql("SELECT * FROM T WHERE OBS = 'favor update urgente'")

    def test_nao_confunde_palavra_dentro_de_nome_de_coluna(self) -> None:
        assert_read_only_sql("SELECT update_date, create_user FROM INTEGRACAO_ORCLST")

    def test_ignora_comentario_de_bloco(self) -> None:
        assert_read_only_sql("/* INSERT INTO T */ SELECT 1 FROM T")

    def test_ignora_comentario_de_linha(self) -> None:
        assert_read_only_sql("-- DELETE FROM T\nSELECT 1 FROM T")

    def test_sql_vazio_nao_e_escrita(self) -> None:
        assert_read_only_sql("")
        assert looks_like_write_sql("   ") is False


class TestTravaNaoTemChaveDeDesligamento:
    def test_assinatura_nao_aceita_parametro_para_desligar(self) -> None:
        """A ausência de um 'block=False' é a garantia estrutural da diretiva."""
        parametros = set(inspect.signature(assert_read_only_sql).parameters)
        assert parametros == {"sql", "fonte"}

    def test_modulo_nao_le_ambiente_nem_configuracao(self) -> None:
        """Garantia estrutural: `safety` não importa `os` nem a configuração.

        Se a trava não tem como ler variável de ambiente ou config, não há como
        desligá-la por configuração — só alterando o código, o que é uma
        mudança revisável.
        """
        arvore = ast.parse(inspect.getsource(safety))
        importados: set[str] = set()
        for no in ast.walk(arvore):
            if isinstance(no, ast.Import):
                importados.update(alias.name.split(".")[0] for alias in no.names)
            elif isinstance(no, ast.ImportFrom) and no.module:
                importados.add(no.module.split(".")[0])

        assert "os" not in importados
        assert not any(nome.startswith("wbcpython") for nome in importados)

    def test_nao_ha_ramo_condicional_que_libere_a_escrita(self) -> None:
        """A única saída da função é o `if` que levanta a exceção."""
        (funcao,) = [
            no
            for no in ast.parse(inspect.getsource(safety)).body
            if isinstance(no, ast.FunctionDef) and no.name == "assert_read_only_sql"
        ]
        condicoes = [no for no in ast.walk(funcao) if isinstance(no, ast.If)]
        assert len(condicoes) == 1
