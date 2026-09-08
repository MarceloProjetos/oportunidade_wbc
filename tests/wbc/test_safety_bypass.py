"""Testes adversariais da trava de SQL — tentativas reais de burlá-la.

Cada caso aqui é um bypass que **funcionou** numa versão anterior do
`looks_like_write_sql`, quando a limpeza do SQL era feita por expressões
regulares aplicadas em sequência: comentários eram removidos antes dos literais,
então um `--`, `''` ou `/*` **dentro de uma string** apagava o resto do comando
e escondia a escrita que vinha depois.

A correção foi trocar as regex por um scanner de estado que percorre o texto uma
vez sabendo, a cada caractere, se está dentro de literal, comentário ou
identificador citado.

Estes testes existem para que essa classe de problema não volte.
"""

from __future__ import annotations

import pytest

from wbcpython.safety import ReadOnlyViolation, assert_read_only_sql, looks_like_write_sql


class TestBypassPorLiteralComComentario:
    """Escrita escondida atrás de um marcador de comentário dentro de literal."""

    @pytest.mark.parametrize(
        ("sql", "descricao"),
        [
            ("SELECT 'a--b' FROM t; DROP TABLE Alvo", "-- dentro de literal escondia o DROP"),
            (
                "SELECT 'x--y' AS c INTO NovaTabela FROM T",
                "-- dentro de literal escondia o SELECT INTO",
            ),
            (
                "SELECT * FROM t WHERE s='/*'; DELETE FROM t WHERE 1=1; SELECT '*/'",
                "/* dentro de literal escondia o DELETE",
            ),
        ],
    )
    def test_escrita_escondida_e_detectada(self, sql: str, descricao: str) -> None:
        assert looks_like_write_sql(sql) is True, f"bypass ativo: {descricao}"

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT 'a--b' FROM t; DROP TABLE Alvo",
            "SELECT 'x--y' AS c INTO NovaTabela FROM T",
            "SELECT * FROM t WHERE s='/*'; DELETE FROM t WHERE 1=1; SELECT '*/'",
        ],
    )
    def test_trava_de_leitura_recusa(self, sql: str) -> None:
        with pytest.raises(ReadOnlyViolation):
            assert_read_only_sql(sql)


class TestLiteraisContinuamSendoIgnorados:
    """A correção não pode ter criado falsos positivos no caminho normal."""

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM T WHERE OBS = 'favor update urgente'",
            "SELECT * FROM T WHERE OBS = 'não deletar este registro'",
            "SELECT * FROM T WHERE C = 'a--b'",
            "SELECT * FROM T WHERE C = 'x''y'",
            "SELECT * FROM T WHERE C = '/* nada */'",
            "-- DELETE FROM T\nSELECT 1 FROM T",
            "/* INSERT INTO T */ SELECT 1 FROM T",
        ],
    )
    def test_consultas_legitimas_passam(self, sql: str) -> None:
        assert looks_like_write_sql(sql) is False
        assert_read_only_sql(sql)

    def test_aspas_escapadas_formam_um_literal_unico(self) -> None:
        """Parece um bypass, mas não é — e a diferença importa.

        Em `SELECT 'a''; DROP TABLE T; SELECT ''b' FROM t`, as aspas duplicadas
        são o escape de uma aspas simples: o texto inteiro entre a primeira e a
        última aspas é **um único literal**. Não há segundo comando; o `DROP`
        é só conteúdo de string e nunca é executado.

        Classificar isso como escrita seria um falso positivo — recusaria uma
        consulta legítima. O scanner acompanha o escape corretamente.
        """
        sql = "SELECT 'a''; DROP TABLE T; SELECT ''b' FROM t"
        assert looks_like_write_sql(sql) is False
        assert_read_only_sql(sql)


class TestIdentificadoresCitados:
    """Aspas duplas são identificadores (HANA usa muito), não comandos."""

    def test_identificador_com_nome_suspeito_nao_e_escrita(self) -> None:
        # Uma coluna chamada "insert" não torna a consulta uma escrita.
        assert looks_like_write_sql('SELECT "insert" FROM "minha_tabela"') is False

    def test_view_do_hana_com_schema_citado(self) -> None:
        sql = 'SELECT N_WBC, StatusWBC FROM "SBOALTAMIRAPROD"."VW_EVOL_OPORTUNIDADE_ALT"'
        assert looks_like_write_sql(sql) is False
        assert_read_only_sql(sql)

    def test_escrita_com_identificadores_citados_ainda_e_detectada(self) -> None:
        assert looks_like_write_sql('DELETE FROM "SCHEMA"."TABELA"') is True


class TestComentarioNaoEngoleOComando:
    def test_comentario_de_linha_termina_na_quebra(self) -> None:
        assert looks_like_write_sql("SELECT 1 -- comentário\n; DELETE FROM T") is True

    def test_comentario_de_bloco_termina_no_fechamento(self) -> None:
        assert looks_like_write_sql("/* nota */ DELETE FROM T") is True

    def test_comentario_de_bloco_nao_fechado_nao_libera_escrita(self) -> None:
        # Bloco aberto e nunca fechado: tudo depois é comentário, então não há
        # comando de escrita visível — e também não há comando nenhum.
        assert looks_like_write_sql("SELECT 1 /* DELETE FROM T") is False
