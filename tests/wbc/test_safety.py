"""Testes da trava de segurança de ambiente.

Estes testes protegem a regra mais importante do projeto
(ai_spec/04_environment_constraints.md): nenhuma escrita pode atingir a
company DB de produção. Se algum destes testes falhar, o projeto está
inseguro — não prossiga com o desenvolvimento até corrigir.
"""

from __future__ import annotations

import pytest

from wbcpython.safety import (
    ProductionWriteBlocked,
    assert_http_call_allowed,
    assert_sql_allowed,
    assert_write_allowed,
    is_production,
    looks_like_write_sql,
)

PROD = "SBOALTAMIRAPROD"
HOMOLOG = "SBOALTAMIRAHOMOLOG"


class TestIsProduction:
    @pytest.mark.parametrize(
        "valor",
        ["SBOALTAMIRAPROD", "sboaltamiraprod", "  SBOAltamiraProd  ", "SBOAltamiraPROD"],
    )
    def test_reconhece_producao_ignorando_caixa_e_espacos(self, valor: str) -> None:
        assert is_production(valor, PROD) is True

    @pytest.mark.parametrize("valor", [HOMOLOG, "SBOALTAMIRATESTE", "", None])
    def test_nao_confunde_outros_ambientes_com_producao(self, valor: str | None) -> None:
        assert is_production(valor, PROD) is False

    def test_nao_trata_prefixo_como_producao(self) -> None:
        # SBOALTAMIRAPRODUCAO_ANTIGO não é a company DB de produção.
        assert is_production("SBOALTAMIRAPRODUCAO_ANTIGO", PROD) is False


class TestAssertWriteAllowed:
    def test_bloqueia_escrita_em_producao(self) -> None:
        with pytest.raises(ProductionWriteBlocked) as exc:
            assert_write_allowed(PROD, production_company_db=PROD)
        assert PROD in str(exc.value)

    def test_permite_escrita_em_homologacao(self) -> None:
        assert_write_allowed(HOMOLOG, production_company_db=PROD)

    def test_trava_desligada_permite_producao(self) -> None:
        # Caminho de escape deliberado, exigindo decisão explícita de quem chama.
        assert_write_allowed(PROD, production_company_db=PROD, block_production_writes=False)

    def test_alvo_nao_informado_bloqueia(self) -> None:
        """A trava falha FECHADA: destino desconhecido é recusado.

        Antes esta função liberava a escrita quando o destino era None — o que
        daria passe livre a um schema HANA não configurado, por exemplo.
        """
        with pytest.raises(ProductionWriteBlocked, match="destino não foi informado"):
            assert_write_allowed(None, production_company_db=PROD)

    def test_alvo_vazio_bloqueia(self) -> None:
        with pytest.raises(ProductionWriteBlocked):
            assert_write_allowed("   ", production_company_db=PROD)

    def test_nome_da_producao_vazio_bloqueia_tudo(self) -> None:
        """Sem saber qual é a produção, a trava não pode deixar nada passar.

        Este era o furo mais barato de todos: bastava `WBC_PRODUCTION_COMPANY_DB`
        vazio (ou com erro de digitação) para nenhum destino ser reconhecido como
        produção, e as ferramentas de diagnóstico ainda reportarem "trava ATIVA".
        """
        with pytest.raises(ProductionWriteBlocked, match="não está configurado"):
            assert_write_allowed("QUALQUER_COISA", production_company_db="")

    def test_trava_desligada_ignora_configuracao_incompleta(self) -> None:
        # Com a trava explicitamente desligada, nada é verificado — é o
        # comportamento pedido de quem desligou conscientemente.
        assert_write_allowed(None, production_company_db="", block_production_writes=False)


class TestAssertHttpCallAllowed:
    @pytest.mark.parametrize("metodo", ["POST", "PATCH", "PUT", "DELETE", "post", "patch"])
    def test_bloqueia_metodos_de_escrita_em_producao(self, metodo: str) -> None:
        with pytest.raises(ProductionWriteBlocked):
            assert_http_call_allowed(metodo, PROD, production_company_db=PROD)

    @pytest.mark.parametrize("metodo", ["GET", "get", "HEAD", "OPTIONS"])
    def test_permite_leitura_em_producao(self, metodo: str) -> None:
        # A regra do projeto proíbe alterar produção, não consultá-la.
        assert_http_call_allowed(metodo, PROD, production_company_db=PROD)

    @pytest.mark.parametrize("metodo", ["GET", "POST", "PATCH", "DELETE"])
    def test_permite_tudo_em_homologacao(self, metodo: str) -> None:
        assert_http_call_allowed(metodo, HOMOLOG, production_company_db=PROD)


class TestLooksLikeWriteSql:
    @pytest.mark.parametrize(
        "sql",
        [
            "INSERT INTO T VALUES (1)",
            "update t set a = 1",
            "DELETE FROM T",
            "MERGE INTO T USING S ON (1=1)",
            "TRUNCATE TABLE T",
            "DROP TABLE T",
            "ALTER TABLE T ADD C INT",
            "CREATE TABLE T (C INT)",
            "CALL MINHA_PROC()",
            "EXEC sp_qualquer",
            "  \n  insert into t values (1)  ",
        ],
    )
    def test_detecta_escrita(self, sql: str) -> None:
        assert looks_like_write_sql(sql) is True

    @pytest.mark.parametrize(
        "sql",
        [
            'SELECT * FROM "VW_EVOL_OPORTUNIDADE_ALT"',
            "select n_wbc, statuswbc from vw_evol_oportunidade_alt where n_wbc = ?",
            "  WITH x AS (SELECT 1 FROM DUMMY) SELECT * FROM x  ",
            "",
            "   ",
        ],
    )
    def test_nao_marca_leitura_como_escrita(self, sql: str) -> None:
        assert looks_like_write_sql(sql) is False

    def test_cte_que_termina_em_insert_e_escrita(self) -> None:
        assert looks_like_write_sql("WITH x AS (SELECT 1 FROM DUMMY) INSERT INTO T SELECT * FROM x")

    def test_ignora_comentario_de_linha(self) -> None:
        assert looks_like_write_sql("-- INSERT INTO T\nSELECT 1 FROM DUMMY") is False


class TestAssertSqlAllowed:
    def test_bloqueia_sql_de_escrita_em_producao(self) -> None:
        with pytest.raises(ProductionWriteBlocked):
            assert_sql_allowed(
                'INSERT INTO "SBOALTAMIRAPROD"."T" VALUES (1)',
                PROD,
                production_company_db=PROD,
            )

    def test_permite_select_nas_views_de_producao(self) -> None:
        # Caso real: as views HANA de leitura podem residir no schema de produção
        # (ver ai_spec/02_data_model.md, seção 3). Leitura é permitida.
        assert_sql_allowed(
            'SELECT N_WBC, StatusWBC FROM "SBOALTAMIRAPROD"."VW_EVOL_OPORTUNIDADE_ALT"',
            PROD,
            production_company_db=PROD,
        )

    def test_permite_escrita_em_homologacao(self) -> None:
        assert_sql_allowed("INSERT INTO T VALUES (1)", HOMOLOG, production_company_db=PROD)
