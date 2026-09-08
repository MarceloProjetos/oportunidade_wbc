"""Testes do repositório das views do HANA — offline, com conexão falsa.

Não exigem o driver `hdbcli` nem rede: a fábrica de conexão é injetável, e uma
conexão de mentira registra o SQL executado. Isso permite verificar o texto
exato das consultas — que é justamente onde mora o risco desta fase, já que o
nome do schema é interpolado no SQL (não pode ser parâmetro de bind).
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

import pytest
from pydantic import SecretStr

from wbcpython.config import HanaSettings
from wbcpython.infrastructure.hana import (
    VIEW_EVOLUCAO,
    VIEW_MUNICIPIO,
    IdentificadorInvalido,
    RepositorioViewsHanaSql,
    citar_identificador,
    validar_identificador,
)
from wbcpython.safety import ProductionWriteBlocked, ReadOnlyViolation

PROD = "SBOALTAMIRAPROD"
HOMOLOG = "SBOALTAMIRAHOMOLOG"


def colunas_projetadas(sql: str) -> list[str]:
    """Nomes das colunas do `SELECT` externo, na ordem em que saem.

    Existe para que o `description` do cursor falso venha do **SQL de verdade**,
    e não de uma lista escrita à mão no teste. Uma lista à mão acompanharia o
    tradutor sem nunca discordar dele; assim, mexer no `SELECT` sem mexer no
    tradutor quebra o teste — que foi exatamente o defeito que passou.
    """
    inicio = sql.upper().index("SELECT") + len("SELECT")
    profundidade = 0
    fim = inicio
    for posicao in range(inicio, len(sql)):
        caractere = sql[posicao]
        if caractere == "(":
            profundidade += 1
        elif caractere == ")":
            profundidade -= 1
        elif profundidade == 0 and sql.upper().startswith("FROM", posicao):
            fim = posicao
            break

    itens: list[str] = []
    atual = ""
    profundidade = 0
    for caractere in sql[inicio:fim]:
        if caractere == "(":
            profundidade += 1
        elif caractere == ")":
            profundidade -= 1
        if caractere == "," and profundidade == 0:
            itens.append(atual)
            atual = ""
        else:
            atual += caractere
    itens.append(atual)

    return [re.findall(r'"([^"]+)"', item)[-1] for item in itens if item.strip()]


class CursorFalso:
    def __init__(self, conexao: ConexaoFalsa) -> None:
        self._conexao = conexao
        self._resultado: list[tuple[Any, ...]] = []
        self._sql = ""

    def execute(self, sql: str, parametros: tuple[Any, ...] = ()) -> None:
        self._conexao.executados.append((sql, parametros))
        self._resultado = self._conexao.proximo_resultado
        self._sql = sql

    @property
    def description(self) -> list[tuple[str, ...]]:
        return [(nome,) for nome in colunas_projetadas(self._sql)]

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._resultado

    def close(self) -> None:
        self._conexao.cursores_fechados += 1


class ConexaoFalsa:
    def __init__(self, resultado: list[tuple[Any, ...]] | None = None) -> None:
        self.executados: list[tuple[str, tuple[Any, ...]]] = []
        self.proximo_resultado = resultado or []
        self.cursores_fechados = 0
        self.fechada = False

    def cursor(self) -> CursorFalso:
        return CursorFalso(self)

    def close(self) -> None:
        self.fechada = True

    @property
    def ultimo_sql(self) -> str:
        return self.executados[-1][0]

    @property
    def ultimos_parametros(self) -> tuple[Any, ...]:
        return self.executados[-1][1]


def _settings(schema: str = HOMOLOG) -> HanaSettings:
    return HanaSettings(
        _env_file=None,  # type: ignore[call-arg]
        host="sapbusinessonehana-vm",
        port=30015,
        username="usuario",
        password=SecretStr("senha_secreta"),
        HANA_SCHEMA=schema,
    )


def _repo(conexao: ConexaoFalsa, schema: str = HOMOLOG) -> RepositorioViewsHanaSql:
    return RepositorioViewsHanaSql(
        _settings(schema),
        production_company_db=PROD,
        fabrica_de_conexao=lambda: conexao,
    )


class TestValidacaoDeIdentificador:
    """O schema é interpolado no SQL — é o único ponto assim no projeto."""

    @pytest.mark.parametrize("nome", [HOMOLOG, PROD, "SCHEMA_1", "_interno", "A$B#C"])
    def test_aceita_nomes_validos(self, nome: str) -> None:
        assert validar_identificador(nome) == nome

    @pytest.mark.parametrize(
        "nome",
        [
            'X"."Y',  # sairia do schema e apontaria outro objeto
            "X; DROP TABLE T",
            "X--comentário",
            "X Y",
            "1COMECA_COM_DIGITO",
            "",
            "   ",
            "X'Y",
            "X/*Y*/",
        ],
    )
    def test_recusa_nomes_perigosos(self, nome: str) -> None:
        with pytest.raises(IdentificadorInvalido):
            validar_identificador(nome)

    def test_citacao_envolve_em_aspas(self) -> None:
        assert citar_identificador("MEU_SCHEMA") == '"MEU_SCHEMA"'

    def test_schema_invalido_falha_na_construcao_do_repositorio(self) -> None:
        # Melhor estourar aqui, com mensagem clara, do que no meio de uma consulta.
        with pytest.raises(IdentificadorInvalido, match="HANA_SCHEMA"):
            RepositorioViewsHanaSql(
                _settings('X"."Y'),
                production_company_db=PROD,
                fabrica_de_conexao=lambda: ConexaoFalsa(),
            )


#: Linha completa de VW_EVOL_OPORTUNIDADE_ALT, na ordem que o repositório lê.
def _linha_evolucao(**sobrescreve):
    valores = {
        "n_wbc": "00123316",
        "status": "Entrada",
        "num_oport": 500,
        "num_doc": 900,
        "tipo_doc": "COT",
        "cotacao": 10,
        "cod_pn": "C001",
        "nome_pn": "BALTEAU",
        "repres": "043",
        "municipio": "ITAJUBA",
        "uf": "MG",
        "valor": 72628.83,
        "data_oport": None,
        "data_cot": None,
        "pct": 5.0,
        "retorno": "9.0000",
        "indice": "1.0000",
        "negociacao": "0.0000",
    }
    valores.update(sobrescreve)
    return tuple(valores.values())


class TestEvolucaoDoOrcamento:
    def test_le_todos_os_campos(self) -> None:
        evolucao = _repo(ConexaoFalsa([_linha_evolucao()])).evolucao_do_orcamento("00123316")

        assert evolucao is not None
        assert evolucao.n_wbc == "00123316"
        assert evolucao.status_wbc == "Entrada"
        assert evolucao.nome_pn == "BALTEAU"
        assert evolucao.municipio == "ITAJUBA"
        assert evolucao.uf == "MG"
        assert evolucao.valor == Decimal("72628.83")
        assert evolucao.pct_comissao == Decimal("5.0")

    def test_campos_textuais_sao_normalizados(self) -> None:
        """Retorno/Indice/Negociacao são NVARCHAR com separador inconsistente."""
        evolucao = _repo(
            ConexaoFalsa([_linha_evolucao(retorno="9,0000", indice="1.0000")])
        ).evolucao_do_orcamento("00123316")
        assert evolucao is not None
        assert evolucao.retorno == Decimal(9)
        assert evolucao.indice == Decimal(1)

    def test_campos_ausentes_viram_none(self) -> None:
        # Em dados reais, só parte dos status traz esses percentuais.
        evolucao = _repo(
            ConexaoFalsa([_linha_evolucao(pct=None, retorno=None, indice="", negociacao=None)])
        ).evolucao_do_orcamento("00123316")
        assert evolucao is not None
        assert evolucao.pct_comissao is None
        assert evolucao.retorno is None
        assert evolucao.indice is None

    def test_orcamento_inexistente_devolve_none(self) -> None:
        assert _repo(ConexaoFalsa([])).evolucao_do_orcamento("00000000") is None

    def test_usa_parametro_de_bind_para_o_numero(self) -> None:
        conexao = ConexaoFalsa([])
        _repo(conexao).evolucao_do_orcamento("00123316")
        assert "?" in conexao.ultimo_sql
        assert conexao.ultimos_parametros == ("00123316",)
        # O número do orçamento nunca é concatenado no texto da consulta.
        assert "00123316" not in conexao.ultimo_sql

    def test_schema_configurado_aparece_citado_na_consulta(self) -> None:
        conexao = ConexaoFalsa([])
        _repo(conexao, schema="OUTRO_SCHEMA").evolucao_do_orcamento("X")
        assert f'"OUTRO_SCHEMA"."{VIEW_EVOLUCAO}"' in conexao.ultimo_sql

    def test_nome_do_schema_nao_fica_fixo_no_codigo(self) -> None:
        # Regra 4: trocar de ambiente é mudança de configuração, não de código.
        c1, c2 = ConexaoFalsa([]), ConexaoFalsa([])
        _repo(c1, schema=HOMOLOG).evolucao_do_orcamento("X")
        _repo(c2, schema=PROD).evolucao_do_orcamento("X")
        assert HOMOLOG in c1.ultimo_sql
        assert PROD in c2.ultimo_sql


#: Linha real de VW_CLIENTE_MUNICIPIO_ALTA (AbsId, Name, Name_N, State, Code, Ibge, Country).
LINHA_MUNICIPIO = (4321, "Cotia", "COTIA", "SP", "643", "3513009", "BR")


class TestMunicipio:
    def test_busca_por_nome(self) -> None:
        conexao = ConexaoFalsa([LINHA_MUNICIPIO])
        municipio = _repo(conexao).municipio("Cotia")
        assert municipio is not None
        assert municipio.municipio == "Cotia"
        assert municipio.municipio_normalizado == "COTIA"
        assert municipio.abs_id == 4321
        assert municipio.codigo_ibge == "3513009"
        assert conexao.ultimos_parametros == ("Cotia",)

    def test_busca_pelo_nome_normalizado_e_nao_pelo_acentuado(self) -> None:
        """O município vem do WBC sem acento; comparar com Name erraria."""
        conexao = ConexaoFalsa([])
        _repo(conexao).municipio("SAO PAULO")
        assert '"Name_N"' in conexao.ultimo_sql
        assert 'UPPER("Name")' not in conexao.ultimo_sql

    def test_uf_e_opcional_e_vira_segundo_parametro(self) -> None:
        conexao = ConexaoFalsa([LINHA_MUNICIPIO])
        _repo(conexao).municipio("Cotia", "SP")
        assert conexao.ultimos_parametros == ("Cotia", "SP")
        assert conexao.ultimo_sql.count("?") == 2

    def test_sem_uf_usa_apenas_um_parametro(self) -> None:
        conexao = ConexaoFalsa([])
        _repo(conexao).municipio("Cotia")
        assert conexao.ultimo_sql.count("?") == 1

    def test_uf_filtra_pela_coluna_State(self) -> None:
        conexao = ConexaoFalsa([LINHA_MUNICIPIO])
        _repo(conexao).municipio("Cotia", "SP")
        assert '"State"' in conexao.ultimo_sql

    def test_municipio_inexistente(self) -> None:
        assert _repo(ConexaoFalsa([])).municipio("NAO EXISTE") is None


class TestSomenteLeitura:
    def test_interface_nao_expoe_escrita(self) -> None:
        publicos = [n for n in dir(RepositorioViewsHanaSql) if not n.startswith("_")]
        proibidos = ("salvar", "inserir", "atualizar", "gravar", "excluir", "apagar")
        for nome in publicos:
            assert not any(nome.startswith(p) for p in proibidos), nome

    def test_executar_recusa_sql_de_escrita(self) -> None:
        repo = _repo(ConexaoFalsa([]))
        with pytest.raises(ReadOnlyViolation):
            repo._executar("UPDATE T SET A = 1")

    def test_todas_as_consultas_emitidas_sao_de_leitura(self) -> None:
        from wbcpython.safety import looks_like_write_sql

        conexao = ConexaoFalsa([])
        repo = _repo(conexao)
        repo.evolucao_do_orcamento("X")
        repo.municipio("Y", "SP")
        repo.views_existem()

        assert conexao.executados, "nenhuma consulta foi emitida"
        for sql, _ in conexao.executados:
            assert not looks_like_write_sql(sql), f"consulta de escrita emitida: {sql}"

    def test_leitura_no_schema_de_producao_e_permitida(self) -> None:
        """Caso real: as views podem residir só no schema de produção.

        A Regra 1 proíbe alterar produção, não consultá-la — e é isso que
        viabiliza este acesso.
        """
        conexao = ConexaoFalsa([_linha_evolucao()])
        evolucao = _repo(conexao, schema=PROD).evolucao_do_orcamento("00123316")
        assert evolucao is not None

    def test_escrita_no_schema_de_producao_e_bloqueada(self) -> None:
        repo = _repo(ConexaoFalsa([]), schema=PROD)
        with pytest.raises((ReadOnlyViolation, ProductionWriteBlocked)):
            repo._executar("DELETE FROM T")


class TestViewsExistem:
    def test_detecta_as_duas_views(self) -> None:
        conexao = ConexaoFalsa([(VIEW_EVOLUCAO,), (VIEW_MUNICIPIO,), ("OUTRA_VIEW",)])
        assert _repo(conexao).views_existem() == {
            VIEW_EVOLUCAO: True,
            VIEW_MUNICIPIO: True,
        }

    def test_detecta_ausencia(self) -> None:
        conexao = ConexaoFalsa([("OUTRA_VIEW",)])
        assert _repo(conexao).views_existem() == {
            VIEW_EVOLUCAO: False,
            VIEW_MUNICIPIO: False,
        }

    def test_consulta_o_catalogo_e_nao_as_views(self) -> None:
        conexao = ConexaoFalsa([])
        _repo(conexao).views_existem()
        assert "SYS" in conexao.ultimo_sql
        assert VIEW_EVOLUCAO not in conexao.ultimo_sql

    def test_falha_de_catalogo_nao_derruba_o_diagnostico(self) -> None:
        class ConexaoQueFalha(ConexaoFalsa):
            def cursor(self) -> CursorFalso:
                raise RuntimeError("sem permissão em SYS.VIEWS")

        resultado = _repo(ConexaoQueFalha()).views_existem()
        assert resultado == {VIEW_EVOLUCAO: False, VIEW_MUNICIPIO: False}


class TestCicloDeVidaDaConexao:
    def test_conexao_e_reaproveitada(self) -> None:
        conexao = ConexaoFalsa([])
        repo = _repo(conexao)
        repo.evolucao_do_orcamento("A")
        repo.evolucao_do_orcamento("B")
        assert len(conexao.executados) == 2

    def test_cursor_e_fechado_sempre(self) -> None:
        conexao = ConexaoFalsa([])
        repo = _repo(conexao)
        repo.evolucao_do_orcamento("A")
        assert conexao.cursores_fechados == 1

    def test_context_manager_fecha_a_conexao(self) -> None:
        conexao = ConexaoFalsa([])
        with _repo(conexao) as repo:
            repo.evolucao_do_orcamento("A")
        assert conexao.fechada is True

    def test_close_sem_conexao_aberta_nao_falha(self) -> None:
        _repo(ConexaoFalsa([])).close()
