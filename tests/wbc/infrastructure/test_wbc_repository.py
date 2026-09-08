"""Testes do repositório do WBC — offline, contra SQLite em memória.

As consultas usam apenas SQL padrão (`COALESCE`, parâmetros nomeados), então
rodam de verdade contra um SQLite com o mesmo esquema do WBC. Isso exercita o
SQL para valer — nomes de coluna, joins, ordenação — sem depender de um SQL
Server, e pega erros que um mock esconderia.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text

from wbcpython.infrastructure.wbc_sql import RepositorioOrcamentosWbcSql
from wbcpython.safety import ReadOnlyViolation

ESQUEMA = """
CREATE TABLE INTEGRACAO_ORCLST (
    ORCNUM TEXT, REVISAO TEXT, SITCOD INTEGER, CLINOM TEXT, REPCOD TEXT,
    CLIMUN TEXT, ESTCOD TEXT, ORCDAT TEXT, ORCALTDTH TEXT
);
-- INTEGRACAO_ORCIMP é desnormalizada: os campos de cabeçalho se repetem em
-- cada linha do orçamento. É dela que saem as linhas (ver queries.py) —
-- INTEGRACAO_ORCITM parou de receber dados no WBC real.
CREATE TABLE INTEGRACAO_ORCIMP (
    ORCNUM TEXT, ORCIMP_DATA_ULTIMA_ALTERACAO TEXT, ORCIMP_RETORNO NUMERIC,
    ORCIMP_NEGOCIACAO NUMERIC, ORCIMP_INDICE_VENDAS NUMERIC,
    GRPCOD INTEGER, SUBGRPCOD INTEGER, ORCITM INTEGER,
    ORCPRDCOD TEXT, ORCPRDQTD NUMERIC, ORCTXT TEXT, ORCVAL NUMERIC,
    ORCIPI NUMERIC, ORCICM NUMERIC, idIntegracao_OrcImp INTEGER,
    -- Campos de impressão que alimentam o snapshot do OrcDetalhe.
    ORCVALVND NUMERIC, ORCVALLST NUMERIC, ORCVALINV NUMERIC, ORCVALLUC NUMERIC,
    ORCVALEXP NUMERIC, ORCVALCOM NUMERIC, ORCPERCOM NUMERIC, ORCVALTRP NUMERIC,
    ORCVALEMB NUMERIC, ORCVALMON NUMERIC, ORCBAS1 NUMERIC, ORCBAS2 NUMERIC,
    ORCBAS3 NUMERIC, CLICOD INTEGER, CLICONCOD INTEGER, CLICON TEXT,
    PGTCOD TEXT, ORCPGT TEXT, TIPMONCOD TEXT, PRZENT INTEGER,
    ORCIMP_REVISAO TEXT, ORCIMP_EMAIL TEXT, ORCIMP_FONE TEXT,
    ORCIMP_CIDADE TEXT, ORCIMP_UF TEXT, ORCIMP_TIPO_VENDA TEXT,
    ORCIMP_TRANSPORTE TEXT, ORCIMP_ACABAMENTO TEXT, ORCIMP_MONTAGEM TEXT,
    ORCIMP_TABELA_PRECO TEXT
);
-- Situação real da proposta: uma linha por mudança de status.
CREATE TABLE INTEGRACAO_ORCSIT (
    ORCNUM TEXT, SITCOD INTEGER, ORCALTDTH TEXT, idIntegracao_OrcSit INTEGER
);
-- Árvore de produtos: o detalhamento de engenharia. Nem todo orçamento tem.
CREATE TABLE INTEGRACAO_ORCPRDARV (
    ORCNUM TEXT, GRPCOD INTEGER, SUBGRPCOD INTEGER, ORCITM INTEGER,
    PRDCOD TEXT, ORCPRDARV_NIVEL INTEGER, CORCOD TEXT, PRDDSC TEXT,
    ORCQTD NUMERIC, ORCTOT NUMERIC, ORCPES NUMERIC,
    idIntegracao_OrcPrdArv INTEGER
);
CREATE TABLE INTEGRACAO_ORCPRD (
    ORCNUM TEXT, GRPCOD INTEGER, SUBGRPCOD INTEGER, ORCITM INTEGER,
    PRDCOD TEXT, CORCOD TEXT, PRDDSC TEXT, ORCQTD NUMERIC, ORCTOT NUMERIC,
    ORCPES NUMERIC, idIntegracao_OrcPrd INTEGER
);
CREATE TABLE INTEGRACAO_ORCCAB (
    ORCNUM TEXT, ORCPERCOM NUMERIC, ORCVALCOM NUMERIC, ORCBAS2 NUMERIC
);
"""


@pytest.fixture
def repositorio() -> RepositorioOrcamentosWbcSql:
    engine = create_engine("sqlite://")
    with engine.begin() as conexao:
        for comando in ESQUEMA.strip().split(";"):
            if comando.strip():
                conexao.execute(text(comando))

        conexao.execute(
            text(
                "INSERT INTO INTEGRACAO_ORCLST VALUES "
                "('00123316','C',40,'BALTEAU PRODUTOS ELETRICOS','043',"
                "'ITAJUBA','MG','2026-08-01','2026-08-20 10:00:00')"
            )
        )
        conexao.execute(
            text("INSERT INTO INTEGRACAO_ORCCAB VALUES ('00123316', 5.0, 3631.44, 3500.0)")
        )
        # Itens propositalmente fora de ordem, para exercitar o ORDER BY.
        # O cabeçalho de impressão se repete em cada linha, como no WBC real.
        conexao.execute(
            text(
                "INSERT INTO INTEGRACAO_ORCIMP (ORCNUM, ORCIMP_DATA_ULTIMA_ALTERACAO, ORCIMP_RETORNO, ORCIMP_NEGOCIACAO, ORCIMP_INDICE_VENDAS, GRPCOD, SUBGRPCOD, ORCITM, ORCPRDCOD, ORCPRDQTD, ORCTXT, ORCVAL, ORCIPI, ORCICM, idIntegracao_OrcImp) VALUES "
                "('00123316','2026-08-20 09:00:00', 9.0, 0, 1.0,"
                "2,1,5,'PROT-COL',10,'PROTETORES',1500.50,3.25,12.0,902)"
            )
        )
        conexao.execute(
            text(
                "INSERT INTO INTEGRACAO_ORCIMP (ORCNUM, ORCIMP_DATA_ULTIMA_ALTERACAO, ORCIMP_RETORNO, ORCIMP_NEGOCIACAO, ORCIMP_INDICE_VENDAS, GRPCOD, SUBGRPCOD, ORCITM, ORCPRDCOD, ORCPRDQTD, ORCTXT, ORCVAL, ORCIPI, ORCICM, idIntegracao_OrcImp) VALUES "
                "('00123316','2026-08-20 09:00:00', 9.0, 0, 1.0,"
                "1,1,1,'PORTA-PALETE',9,'PORTA-PALETES',72628.83,3.25,12.0,901)"
            )
        )

        # Orçamento sem itens e sem cabeçalho comercial.
        conexao.execute(
            text(
                "INSERT INTO INTEGRACAO_ORCLST VALUES "
                "('00999999','',30,'CLIENTE SEM ITENS','001','SP','SP',NULL,NULL)"
            )
        )
    return RepositorioOrcamentosWbcSql(engine)


class TestBuscarOrcamento:
    def test_le_o_cabecalho(self, repositorio: RepositorioOrcamentosWbcSql) -> None:
        orc = repositorio.buscar_orcamento("00123316")
        assert orc is not None
        assert orc.orcnum == "00123316"
        assert orc.revisao == "C"
        assert orc.sitcode == 40
        assert orc.cliente_nome == "BALTEAU PRODUTOS ELETRICOS"
        assert orc.representante == "043"
        assert orc.uf == "MG"

    def test_le_os_dados_comerciais_e_de_impressao(
        self, repositorio: RepositorioOrcamentosWbcSql
    ) -> None:
        orc = repositorio.buscar_orcamento("00123316")
        assert orc is not None
        assert orc.percentual_comissao == Decimal("5.0")
        assert orc.valor_comissao == Decimal("3631.44")
        assert orc.base2 == Decimal("3500.0")
        assert orc.retorno == Decimal("9.0")
        assert orc.indice_vendas == Decimal("1.0")

    def test_agrupa_os_itens_sob_um_unico_cabecalho(
        self, repositorio: RepositorioOrcamentosWbcSql
    ) -> None:
        orc = repositorio.buscar_orcamento("00123316")
        assert orc is not None
        assert len(orc.itens) == 2

    def test_itens_vem_ordenados_por_sequencia(
        self, repositorio: RepositorioOrcamentosWbcSql
    ) -> None:
        orc = repositorio.buscar_orcamento("00123316")
        assert orc is not None
        assert [i.orcitm for i in orc.itens] == [1, 5]

    def test_le_os_campos_do_item(self, repositorio: RepositorioOrcamentosWbcSql) -> None:
        orc = repositorio.buscar_orcamento("00123316")
        assert orc is not None
        primeiro = orc.itens[0]
        assert primeiro.produto == "PORTA-PALETE"
        assert primeiro.quantidade == Decimal(9)
        assert primeiro.valor == Decimal("72628.83")
        assert primeiro.id_integracao == 901

    def test_quantidade_total(self, repositorio: RepositorioOrcamentosWbcSql) -> None:
        orc = repositorio.buscar_orcamento("00123316")
        assert orc is not None
        assert orc.quantidade_total_itens == Decimal(19)

    def test_orcamento_inexistente_devolve_none(
        self, repositorio: RepositorioOrcamentosWbcSql
    ) -> None:
        assert repositorio.buscar_orcamento("00000000") is None

    def test_orcamento_sem_itens_nao_inventa_item_vazio(
        self, repositorio: RepositorioOrcamentosWbcSql
    ) -> None:
        # O LEFT JOIN devolve uma linha com os campos de item nulos; ela não
        # pode virar um item fantasma.
        orc = repositorio.buscar_orcamento("00999999")
        assert orc is not None
        assert orc.itens == ()
        assert orc.quantidade_total_itens == Decimal(0)

    def test_ausencia_de_dados_comerciais_vira_zero(
        self, repositorio: RepositorioOrcamentosWbcSql
    ) -> None:
        orc = repositorio.buscar_orcamento("00999999")
        assert orc is not None
        assert orc.percentual_comissao == Decimal(0)
        assert orc.retorno == Decimal(0)


class TestSituacaoAtual:
    def test_devolve_sitcode_e_revisao(self, repositorio: RepositorioOrcamentosWbcSql) -> None:
        assert repositorio.situacao_atual("00123316") == (40, "C")

    def test_revisao_vazia(self, repositorio: RepositorioOrcamentosWbcSql) -> None:
        assert repositorio.situacao_atual("00999999") == (30, "")

    def test_inexistente_devolve_none(self, repositorio: RepositorioOrcamentosWbcSql) -> None:
        assert repositorio.situacao_atual("00000000") is None


class TestOrcamentosAlteradosDesde:
    def test_filtra_por_data(self, repositorio: RepositorioOrcamentosWbcSql) -> None:
        encontrados = repositorio.orcamentos_alterados_desde(datetime(2026, 8, 1))
        assert "00123316" in encontrados

    def test_ignora_alteracoes_anteriores(self, repositorio: RepositorioOrcamentosWbcSql) -> None:
        assert repositorio.orcamentos_alterados_desde(datetime(2026, 12, 1)) == []

    def test_respeita_o_sitcode_minimo(self, repositorio: RepositorioOrcamentosWbcSql) -> None:
        encontrados = repositorio.orcamentos_alterados_desde(
            datetime(2026, 8, 1), sitcode_minimo=50
        )
        assert "00123316" not in encontrados


class TestSomenteLeitura:
    def test_a_interface_nao_expoe_metodo_de_escrita(self) -> None:
        publicos = [nome for nome in dir(RepositorioOrcamentosWbcSql) if not nome.startswith("_")]
        proibidos = ("salvar", "inserir", "atualizar", "gravar", "excluir", "apagar")
        for nome in publicos:
            assert not any(nome.startswith(p) for p in proibidos), (
                f"'{nome}' parece um método de escrita — o WBC é somente leitura (Regra 5)."
            )

    def test_executar_recusa_sql_de_escrita(self, repositorio: RepositorioOrcamentosWbcSql) -> None:
        # Mesmo chamando o executor interno diretamente, a trava age.
        with pytest.raises(ReadOnlyViolation):
            repositorio._executar("UPDATE INTEGRACAO_ORCLST SET SITCOD = 99")

    def test_todas_as_consultas_do_modulo_sao_de_leitura(self) -> None:
        from wbcpython.infrastructure.wbc_sql import queries
        from wbcpython.safety import looks_like_write_sql

        for nome in dir(queries):
            if nome.startswith("_"):
                continue
            valor = getattr(queries, nome)
            if isinstance(valor, str) and "SELECT" in valor.upper():
                assert not looks_like_write_sql(valor), f"consulta '{nome}' não é de leitura"


class TestUrlDeConexao:
    @staticmethod
    def _url(senha: str = "segredo"):
        from pydantic import SecretStr

        from wbcpython.config import WbcSqlSettings

        return RepositorioOrcamentosWbcSql.montar_url(
            WbcSqlSettings(
                _env_file=None,  # type: ignore[call-arg]
                host="192.168.0.1",
                port=1433,
                database="WBCCAD",
                username="sap_user",
                password=SecretStr(senha),
            )
        )

    def test_monta_a_conexao_corretamente(self) -> None:
        url = self._url()
        assert url.database == "WBCCAD"
        assert url.host == "192.168.0.1"
        assert url.drivername == "mssql+pymssql"

    def test_nao_passa_parametros_que_o_pymssql_recusa(self) -> None:
        """Regressão: `ApplicationIntent` derrubava toda leitura do WBC.

        O SQLAlchemy repassa a query string ao driver, e o `pymssql` rejeita
        parâmetros que não conhece com `TypeError`. Só apareceu ao conectar no
        SQL Server real.
        """
        assert self._url().query == {}

    def test_senha_com_arroba_nao_vira_hostname(self) -> None:
        """Regressão: interpolação direta fazia a senha alterar o host.

        Com `s3nh@Altamira`, o trecho após o `@` era lido como servidor — a
        conexão ia para outro host e parte da senha aparecia no `repr()` da URL,
        que só mascara o que vem antes do `@`.
        """
        url = self._url("s3nh@Altamira")
        assert url.host == "192.168.0.1"
        assert url.password == "s3nh@Altamira"
        assert "Altamira" not in repr(url)

    def test_senha_com_porcento_nao_e_reinterpretada(self) -> None:
        # Regressão: '%41' virava 'A' e a autenticação usava senha errada.
        url = self._url("abc%41")
        assert url.password == "abc%41"

    def test_repr_mascara_a_senha(self) -> None:
        assert "segredo" not in repr(self._url())


class TestFanOutDoJoin:
    """Regressão: linhas duplicadas em ORCCAB multiplicavam os itens.

    O orçamento se junta a INTEGRACAO_ORCCAB por ORCNUM, e nada garante uma
    única linha lá. Com duas, o produto cartesiano repete cada linha do
    orçamento — e `quantidade_total_itens`, que alimenta a criação de cotação e
    pedido no SAP, sai dobrada. Era o que o GROUP BY do legado escondia.
    """

    @pytest.fixture
    def repositorio_com_impressao_duplicada(self) -> RepositorioOrcamentosWbcSql:
        engine = create_engine("sqlite://")
        with engine.begin() as conexao:
            for comando in ESQUEMA.strip().split(";"):
                if comando.strip():
                    conexao.execute(text(comando))
            conexao.execute(
                text(
                    "INSERT INTO INTEGRACAO_ORCLST VALUES "
                    "('00777777','A',40,'CLIENTE','001','SP','SP',NULL,NULL)"
                )
            )
            conexao.execute(
                text(
                    "INSERT INTO INTEGRACAO_ORCIMP (ORCNUM, ORCIMP_DATA_ULTIMA_ALTERACAO, ORCIMP_RETORNO, ORCIMP_NEGOCIACAO, ORCIMP_INDICE_VENDAS, GRPCOD, SUBGRPCOD, ORCITM, ORCPRDCOD, ORCPRDQTD, ORCTXT, ORCVAL, ORCIPI, ORCICM, idIntegracao_OrcImp) VALUES "
                    "('00777777','2026-01-01',1,0,1,1,1,1,'PROD',5,'TXT',100,0,0,901)"
                )
            )
            # Duas linhas comerciais para o mesmo orçamento.
            conexao.execute(
                text("INSERT INTO INTEGRACAO_ORCCAB VALUES ('00777777',1.0,10.0,100.0)")
            )
            conexao.execute(
                text("INSERT INTO INTEGRACAO_ORCCAB VALUES ('00777777',2.0,20.0,200.0)")
            )
        return RepositorioOrcamentosWbcSql(engine)

    def test_item_nao_e_duplicado(
        self, repositorio_com_impressao_duplicada: RepositorioOrcamentosWbcSql
    ) -> None:
        orc = repositorio_com_impressao_duplicada.buscar_orcamento("00777777")
        assert orc is not None
        assert len(orc.itens) == 1

    def test_quantidade_total_nao_dobra(
        self, repositorio_com_impressao_duplicada: RepositorioOrcamentosWbcSql
    ) -> None:
        orc = repositorio_com_impressao_duplicada.buscar_orcamento("00777777")
        assert orc is not None
        assert orc.quantidade_total_itens == Decimal(5)

    def test_itens_distintos_continuam_distintos(
        self, repositorio: RepositorioOrcamentosWbcSql
    ) -> None:
        # A deduplicação não pode engolir itens legitimamente diferentes.
        orc = repositorio.buscar_orcamento("00123316")
        assert orc is not None
        assert len(orc.itens) == 2
        assert {i.orcitm for i in orc.itens} == {1, 5}


class TestOrigemDasLinhas:
    """Regressão: as linhas vêm de ORCIMP, não de ORCITM.

    `INTEGRACAO_ORCITM` parou de receber dados no WBC — o maior ORCNUM presente
    nela é `00125478`, enquanto os orçamentos em processamento já passam de
    `00125535`. Ler dela devolvia orçamento **sem nenhuma linha**, e o SAP
    recusava o documento com `-5002 Document total value must be zero or
    greater than zero` — um erro que não aponta para a causa.

    O teste é construído para falhar exatamente nesse cenário: a tabela
    `INTEGRACAO_ORCITM` nem existe no esquema, e ORCIMP tem as linhas.
    """

    def test_o_esquema_de_teste_nao_tem_orcitm(self) -> None:
        """Se alguém reintroduzir ORCITM na consulta, o SQL quebra alto."""
        assert "CREATE TABLE INTEGRACAO_ORCITM" not in ESQUEMA

    def test_as_linhas_sao_lidas(self, repositorio: RepositorioOrcamentosWbcSql) -> None:
        orc = repositorio.buscar_orcamento("00123316")
        assert orc is not None
        assert len(orc.itens) == 2
        assert {item.grupo for item in orc.itens} == {1, 2}

    def test_id_de_integracao_vem_de_orcimp(self, repositorio: RepositorioOrcamentosWbcSql) -> None:
        """`idIntegracao_OrcImp`, não `idIntegracao_OrcItm`."""
        orc = repositorio.buscar_orcamento("00123316")
        assert orc is not None
        assert {item.id_integracao for item in orc.itens} == {901, 902}


class TestQuantidadeNula:
    """`ORCPRDQTD` é nula em 100% das linhas reais — o nulo precisa chegar."""

    @pytest.fixture
    def repositorio_sem_quantidade(self) -> RepositorioOrcamentosWbcSql:
        engine = create_engine("sqlite://")
        with engine.begin() as conexao:
            for comando in ESQUEMA.strip().split(";"):
                if comando.strip():
                    conexao.execute(text(comando))
            conexao.execute(
                text(
                    "INSERT INTO INTEGRACAO_ORCLST VALUES "
                    "('00125535','',40,'CLIENTE','043','ITAJUBA','MG',NULL,NULL)"
                )
            )
            conexao.execute(
                text(
                    "INSERT INTO INTEGRACAO_ORCIMP (ORCNUM, ORCIMP_DATA_ULTIMA_ALTERACAO, ORCIMP_RETORNO, ORCIMP_NEGOCIACAO, ORCIMP_INDICE_VENDAS, GRPCOD, SUBGRPCOD, ORCITM, ORCPRDCOD, ORCPRDQTD, ORCTXT, ORCVAL, ORCIPI, ORCICM, idIntegracao_OrcImp) VALUES "
                    "('00125535',NULL,0,0,0,2,1,1,NULL,NULL,'PORTA-PALETES',"
                    "41810.91,0,0,126979)"
                )
            )
        return RepositorioOrcamentosWbcSql(engine)

    def test_nulo_chega_como_none(
        self, repositorio_sem_quantidade: RepositorioOrcamentosWbcSql
    ) -> None:
        """Um COALESCE na consulta esconderia que a coluna nunca é preenchida."""
        orc = repositorio_sem_quantidade.buscar_orcamento("00125535")
        assert orc is not None
        assert orc.itens[0].quantidade is None

    def test_a_regra_de_negocio_transforma_em_um(
        self, repositorio_sem_quantidade: RepositorioOrcamentosWbcSql
    ) -> None:
        orc = repositorio_sem_quantidade.buscar_orcamento("00125535")
        assert orc is not None
        assert orc.itens[0].quantidade_para_documento == Decimal(1)
        assert orc.itens[0].preco_unitario == Decimal("41810.91")


class TestRevisaoNormalizada:
    """A revisão sai sempre em maiúscula e sem espaços.

    Importa em dois lugares: no UDF `U_INO_VERSAOWBC` do documento, lido por
    pessoas; e em `ordem_revisao()`, cuja fórmula ASCII-64 pressupõe maiúscula
    — uma minúscula produziria um número **maior**, fazendo uma revisão antiga
    parecer mais nova e disparando cancelamento e recriação de documento.
    """

    @pytest.fixture
    def repositorio_com_revisao_suja(self) -> RepositorioOrcamentosWbcSql:
        engine = create_engine("sqlite://")
        with engine.begin() as conexao:
            for comando in ESQUEMA.strip().split(";"):
                if comando.strip():
                    conexao.execute(text(comando))
            for orcnum, revisao in (
                ("00100001", "'b'"),
                ("00100002", "' C '"),
                ("00100003", "NULL"),
                ("00100004", "'A'"),
            ):
                conexao.execute(
                    text(
                        "INSERT INTO INTEGRACAO_ORCLST VALUES "
                        f"('{orcnum}',{revisao},40,'CLI','001','SP','SP',NULL,NULL)"
                    )
                )
                conexao.execute(
                    text(
                        "INSERT INTO INTEGRACAO_ORCIMP (ORCNUM, ORCIMP_DATA_ULTIMA_ALTERACAO, ORCIMP_RETORNO, ORCIMP_NEGOCIACAO, ORCIMP_INDICE_VENDAS, GRPCOD, SUBGRPCOD, ORCITM, ORCPRDCOD, ORCPRDQTD, ORCTXT, ORCVAL, ORCIPI, ORCICM, idIntegracao_OrcImp) VALUES "
                        f"('{orcnum}',NULL,0,0,0,2,1,1,NULL,NULL,'TXT',100,0,0,1)"
                    )
                )
        return RepositorioOrcamentosWbcSql(engine)

    @pytest.mark.parametrize(
        ("orcnum", "esperado"),
        [("00100001", "B"), ("00100002", "C"), ("00100003", ""), ("00100004", "A")],
    )
    def test_revisao_sai_maiuscula_e_sem_espacos(
        self,
        repositorio_com_revisao_suja: RepositorioOrcamentosWbcSql,
        orcnum: str,
        esperado: str,
    ) -> None:
        orc = repositorio_com_revisao_suja.buscar_orcamento(orcnum)
        assert orc is not None
        assert orc.revisao == esperado

    def test_situacao_atual_normaliza_igual(
        self, repositorio_com_revisao_suja: RepositorioOrcamentosWbcSql
    ) -> None:
        """As duas consultas precisam concordar, senão a comparação de revisão
        muda conforme o caminho que carregou o dado."""
        situacao = repositorio_com_revisao_suja.situacao_atual("00100001")
        assert situacao is not None
        assert situacao[1] == "B"

    def test_minuscula_nao_parece_mais_nova(
        self, repositorio_com_revisao_suja: RepositorioOrcamentosWbcSql
    ) -> None:
        from wbcpython.domain.revisao import ordem_revisao

        orc = repositorio_com_revisao_suja.buscar_orcamento("00100001")
        assert orc is not None
        # 'b' cru ficaria acima de qualquer maiúscula; normalizado, fica no lugar.
        assert ordem_revisao(orc.revisao) < ordem_revisao("C")
        assert ordem_revisao(orc.revisao) > ordem_revisao("A")


class TestArvoreDeProdutos:
    """A consulta da árvore roda de verdade — SQL, joins e subconsultas.

    É de `INTEGRACAO_ORCPRDARV` que saem as linhas do snapshot do OrcDetalhe
    quando há detalhamento de engenharia. A consulta faz três coisas não óbvias
    (resolver o código, sobrescrever o total, zerar o ORCITM) e cada uma tem
    caso aqui.
    """

    @pytest.fixture
    def repositorio_com_arvore(self) -> RepositorioOrcamentosWbcSql:
        engine = create_engine("sqlite://")
        with engine.begin() as conexao:
            for comando in ESQUEMA.strip().split(";"):
                if comando.strip():
                    conexao.execute(text(comando))
            conexao.execute(
                text(
                    "INSERT INTO INTEGRACAO_ORCLST VALUES "
                    "('00125476','A',40,'ADESC','043','UBERLANDIA','MG',NULL,NULL)"
                )
            )
            # Duas linhas de árvore, fora de ordem de id para exercitar o ORDER BY.
            for id_arv, prdcod, nivel, dsc, qtd, tot, pes in (
                (5002, "PPPLOZ", 2, "PERFIL LONG", 20, 1008.59, 146.348),
                (5001, "PPLLOZ", 1, "LONGARINA Z87", 20, 999.99, 165.27),
            ):
                conexao.execute(
                    text(
                        "INSERT INTO INTEGRACAO_ORCPRDARV VALUES "
                        f"('00125476',2,1,7,'{prdcod}',{nivel},'LA-LB','{dsc}',"
                        f"{qtd},{tot},{pes},{id_arv})"
                    )
                )
            # ORCPRD dá o total "oficial" da LONGARINA e zera o ORCITM do PPPLOZ.
            conexao.execute(
                text(
                    "INSERT INTO INTEGRACAO_ORCPRD VALUES "
                    "('00125476',2,1,1,'PPLLOZ-COMERCIAL','LA-LB','LONGARINA Z87',"
                    "20,2425.80,165.27,900)"
                )
            )
            conexao.execute(
                text(
                    "INSERT INTO INTEGRACAO_ORCPRD VALUES "
                    "('00125476',2,1,0,'PPPLOZ','LA-LB','OUTRA COISA',"
                    "1,0,0,901)"
                )
            )
        return RepositorioOrcamentosWbcSql(engine)

    def test_le_as_linhas_em_ordem_de_id(
        self, repositorio_com_arvore: RepositorioOrcamentosWbcSql
    ) -> None:
        linhas = repositorio_com_arvore.buscar_linhas_arvore("00125476")
        assert [linha.nivel for linha in linhas] == [1, 2]

    def test_codigo_e_resolvido_por_descricao_contra_orcprd(
        self, repositorio_com_arvore: RepositorioOrcamentosWbcSql
    ) -> None:
        """A árvore guarda um código que nem sempre é o comercial."""
        linhas = repositorio_com_arvore.buscar_linhas_arvore("00125476")
        assert linhas[0].produto == "PPLLOZ-COMERCIAL"

    def test_total_vem_de_orcprd_quando_casa_descricao_e_quantidade(
        self, repositorio_com_arvore: RepositorioOrcamentosWbcSql
    ) -> None:
        linhas = repositorio_com_arvore.buscar_linhas_arvore("00125476")
        assert linhas[0].total == Decimal("2425.80")

    def test_total_da_arvore_e_a_reserva(
        self, repositorio_com_arvore: RepositorioOrcamentosWbcSql
    ) -> None:
        """Sem correspondência em ORCPRD, vale o total da própria árvore."""
        linhas = repositorio_com_arvore.buscar_linhas_arvore("00125476")
        assert linhas[1].total == Decimal("1008.59")

    def test_orcitm_zera_quando_o_produto_existe_com_orcitm_zero(
        self, repositorio_com_arvore: RepositorioOrcamentosWbcSql
    ) -> None:
        linhas = repositorio_com_arvore.buscar_linhas_arvore("00125476")
        assert linhas[1].orcitm == 0  # PPPLOZ está em ORCPRD com ORCITM = 0
        assert linhas[0].orcitm == 7  # PPLLOZ não está, mantém o da árvore

    def test_preco_unitario_calculado(
        self, repositorio_com_arvore: RepositorioOrcamentosWbcSql
    ) -> None:
        linhas = repositorio_com_arvore.buscar_linhas_arvore("00125476")
        assert linhas[0].preco_unitario == Decimal("2425.80") / Decimal(20)

    def test_orcamento_carrega_a_arvore_junto(
        self, repositorio_com_arvore: RepositorioOrcamentosWbcSql
    ) -> None:
        """Quem chama `buscar_orcamento` recebe tudo o que o snapshot precisa."""
        orc = repositorio_com_arvore.buscar_orcamento("00125476")
        assert orc is not None
        assert len(orc.arvore) == 2

    def test_orcamento_sem_arvore_devolve_tupla_vazia(
        self, repositorio: RepositorioOrcamentosWbcSql
    ) -> None:
        """O caso comum: os orçamentos recentes não têm detalhamento."""
        orc = repositorio.buscar_orcamento("00123316")
        assert orc is not None
        assert orc.arvore == ()


class TestMarcadoresDeFormatacao:
    """`[CR]` e `[TAB]` são texto literal no dado do WBC, não controle.

    Vêm embutidos nos campos longos de impressão. Sem trocá-los por espaço, o
    acabamento chega ao SAP como `"[CR][CR][CR][CR]Cinza Padrão..."` — o que
    quem abre a cotação vê. O legado os remove na própria consulta.
    """

    @pytest.fixture
    def repositorio_com_marcadores(self) -> RepositorioOrcamentosWbcSql:
        engine = create_engine("sqlite://")
        with engine.begin() as conexao:
            for comando in ESQUEMA.strip().split(";"):
                if comando.strip():
                    conexao.execute(text(comando))
            conexao.execute(
                text(
                    "INSERT INTO INTEGRACAO_ORCLST VALUES "
                    "('00125476','C',40,'ADESC','043','UBERLANDIA','MG',NULL,NULL)"
                )
            )
            conexao.execute(
                text(
                    "INSERT INTO INTEGRACAO_ORCIMP "
                    "(ORCNUM, ORCIMP_ACABAMENTO, ORCPGT, ORCIMP_TRANSPORTE, "
                    " ORCIMP_MONTAGEM, ORCITM) VALUES "
                    "('00125476','[CR][CR]Cinza Padrão Altamira[CR]',"
                    "'NA ENTREGA[TAB]0[TAB]PERCENTUAL[TAB]100',"
                    "'[CR]FOB- Retira em nossa Fábrica[CR]',"
                    "'[CR]A combinar (não inclusa).[CR]', 1)"
                )
            )
        return RepositorioOrcamentosWbcSql(engine)

    def test_acabamento_perde_os_marcadores(
        self, repositorio_com_marcadores: RepositorioOrcamentosWbcSql
    ) -> None:
        orc = repositorio_com_marcadores.buscar_orcamento("00125476")
        assert orc is not None
        assert orc.impressao.acabamento == "Cinza Padrão Altamira"

    def test_tab_tambem_vira_espaco(
        self, repositorio_com_marcadores: RepositorioOrcamentosWbcSql
    ) -> None:
        orc = repositorio_com_marcadores.buscar_orcamento("00125476")
        assert orc is not None
        assert orc.impressao.pagamento_texto == "NA ENTREGA 0 PERCENTUAL 100"

    def test_transporte_e_montagem(
        self, repositorio_com_marcadores: RepositorioOrcamentosWbcSql
    ) -> None:
        orc = repositorio_com_marcadores.buscar_orcamento("00125476")
        assert orc is not None
        assert orc.impressao.transporte == "FOB- Retira em nossa Fábrica"
        assert orc.impressao.montagem == "A combinar (não inclusa)."

    def test_nenhum_marcador_sobra_no_payload(
        self, repositorio_com_marcadores: RepositorioOrcamentosWbcSql
    ) -> None:
        """A garantia que interessa: nada de `[CR]` chegando ao SAP."""
        from wbcpython.domain.mapeamento import montar_payload_orcdetalhe

        orc = repositorio_com_marcadores.buscar_orcamento("00125476")
        assert orc is not None
        texto = str(montar_payload_orcdetalhe(orc))
        assert "[CR]" not in texto
        assert "[TAB]" not in texto


class TestSitCodeVemDeOrcsit:
    """A situação real da proposta está em `INTEGRACAO_ORCSIT`.

    `ORCLST.SITCOD` é uma cópia que diverge: 14% dos orçamentos da base têm
    valor diferente entre as duas tabelas. Como o SitCode dirige toda a máquina
    de estados — inclusive o encerramento da oportunidade —, ler da tabela
    errada leva a decisão errada.
    """

    def _base(self, conexao, orcnum: str, sit_orclst: int) -> None:
        conexao.execute(
            text(
                "INSERT INTO INTEGRACAO_ORCLST VALUES "
                f"('{orcnum}','A',{sit_orclst},'CLI','001','SP','SP',NULL,NULL)"
            )
        )
        conexao.execute(
            text(
                "INSERT INTO INTEGRACAO_ORCIMP "
                "(ORCNUM, ORCITM, ORCVAL) VALUES "
                f"('{orcnum}', 1, 100)"
            )
        )

    @pytest.fixture
    def repositorio_sit(self) -> RepositorioOrcamentosWbcSql:
        engine = create_engine("sqlite://")
        with engine.begin() as conexao:
            for comando in ESQUEMA.strip().split(";"):
                if comando.strip():
                    conexao.execute(text(comando))

            # Diverge: ORCLST diz 40, ORCSIT diz 60.
            self._base(conexao, "00100001", 40)
            conexao.execute(
                text(
                    "INSERT INTO INTEGRACAO_ORCSIT VALUES ('00100001',60,'2026-08-20 10:00:00',900)"
                )
            )

            # Histórico: vale a linha mais recente.
            self._base(conexao, "00100002", 30)
            for sit, quando, ident in (
                (30, "2026-08-01 09:00:00", 901),
                (99, "2026-08-25 17:00:00", 902),
                (40, "2026-08-10 11:00:00", 903),
            ):
                conexao.execute(
                    text(
                        "INSERT INTO INTEGRACAO_ORCSIT VALUES "
                        f"('00100002',{sit},'{quando}',{ident})"
                    )
                )

            # Empate no horário: desempata pelo maior id.
            self._base(conexao, "00100003", 10)
            for sit, ident in ((55, 904), (70, 905)):
                conexao.execute(
                    text(
                        "INSERT INTO INTEGRACAO_ORCSIT VALUES "
                        f"('00100003',{sit},'2026-08-22 08:00:00',{ident})"
                    )
                )

            # Sem linha em ORCSIT: vale o de ORCLST.
            self._base(conexao, "00100004", 40)
        return RepositorioOrcamentosWbcSql(engine)

    def test_orcsit_tem_precedencia_sobre_orclst(
        self, repositorio_sit: RepositorioOrcamentosWbcSql
    ) -> None:
        orc = repositorio_sit.buscar_orcamento("00100001")
        assert orc is not None
        assert orc.sitcode == 60

    def test_vale_a_situacao_mais_recente(
        self, repositorio_sit: RepositorioOrcamentosWbcSql
    ) -> None:
        """As linhas foram inseridas fora de ordem cronológica de propósito."""
        orc = repositorio_sit.buscar_orcamento("00100002")
        assert orc is not None
        assert orc.sitcode == 99

    def test_empate_de_horario_desempata_pelo_id(
        self, repositorio_sit: RepositorioOrcamentosWbcSql
    ) -> None:
        """Sem desempate determinístico o resultado varia com o plano do banco."""
        orc = repositorio_sit.buscar_orcamento("00100003")
        assert orc is not None
        assert orc.sitcode == 70

    def test_sem_linha_em_orcsit_usa_orclst(
        self, repositorio_sit: RepositorioOrcamentosWbcSql
    ) -> None:
        """1.660 orçamentos da base não têm linha em ORCSIT.

        Cair para zero faria a integração ignorá-los em silêncio, por ficarem
        "abaixo do mínimo".
        """
        orc = repositorio_sit.buscar_orcamento("00100004")
        assert orc is not None
        assert orc.sitcode == 40

    def test_situacao_atual_usa_a_mesma_fonte(
        self, repositorio_sit: RepositorioOrcamentosWbcSql
    ) -> None:
        """As duas consultas precisam concordar, senão a decisão muda conforme
        o caminho que carregou o dado."""
        situacao = repositorio_sit.situacao_atual("00100002")
        assert situacao is not None
        assert situacao[0] == 99


class TestSituacoesEmLote:
    """A consulta em lote e a unitária precisam concordar — sempre."""

    def test_usa_a_mesma_fonte_de_sitcode(self) -> None:
        """Se divergissem, a decisão mudaria conforme o caminho que leu o dado.

        A comparação é textual de propósito: qualquer mudança numa das duas
        consultas que não seja feita na outra quebra este teste.
        """
        from wbcpython.infrastructure.wbc_sql import queries

        unitaria = queries.SITUACAO_POR_NUMERO.replace("WHERE A.ORCNUM = :orcnum", "").strip()
        lote = queries.SITUACOES_EM_LOTE.replace("WHERE A.ORCNUM IN :orcnums", "").strip()
        assert unitaria == lote

    def test_le_de_orcsit(self) -> None:
        from wbcpython.infrastructure.wbc_sql import queries

        assert "INTEGRACAO_ORCSIT" in queries.SITUACOES_EM_LOTE


class TestPesosPorItem:
    """O peso que vira `Weight1` na linha do pedido.

    A regra inteira está na consulta, e o que ela filtra é o que importa:
    `INTEGRACAO_ORCPRDARV` é uma estrutura de produto, e cada nível repõe a
    mesma massa decomposta. Somar os níveis conta o mesmo aço duas ou três
    vezes — no orçamento real `00125527` dá 2.980 kg no lugar de 1.095.
    """

    @pytest.fixture
    def repositorio_com_arvore_de_niveis(self) -> RepositorioOrcamentosWbcSql:
        engine = create_engine("sqlite://")
        with engine.begin() as conexao:
            for comando in ESQUEMA.strip().split(";"):
                if comando.strip():
                    conexao.execute(text(comando))
            # Item 1: uma coluna de 348,02 kg no nível 1, que reaparece no nível
            # 2 como 341,83 de aço + 6,19 de tinta. Mais um parafuso de 6,55.
            linhas = [
                ("00124853", 1, 1, "COLUNA", 348.02),
                ("00124853", 1, 2, "ACO", 341.833),
                ("00124853", 1, 2, "TINTA", 6.187),
                ("00124853", 1, 1, "PARAFUSO", 6.55),
                ("00124853", 5, 1, "PROTETOR", 45.13),
                ("00124853", 5, 2, "CHAPA", 47.085),
            ]
            for i, (orcnum, itm, nivel, prd, peso) in enumerate(linhas):
                conexao.execute(
                    text(
                        "INSERT INTO INTEGRACAO_ORCPRDARV "
                        "(ORCNUM, GRPCOD, SUBGRPCOD, ORCITM, PRDCOD, ORCPRDARV_NIVEL, "
                        " CORCOD, PRDDSC, ORCQTD, ORCTOT, ORCPES, idIntegracao_OrcPrdArv) "
                        "VALUES (:o, 2, 0, :itm, :prd, :n, '', :prd, 1, 0, :p, :id)"
                    ),
                    {"o": orcnum, "itm": itm, "prd": prd, "n": nivel, "p": peso, "id": i},
                )
        return RepositorioOrcamentosWbcSql(engine)

    def test_soma_apenas_o_nivel_1(
        self, repositorio_com_arvore_de_niveis: RepositorioOrcamentosWbcSql
    ) -> None:
        pesos = repositorio_com_arvore_de_niveis.pesos_por_item("00124853")

        assert pesos[1] == Decimal("354.57")  # 348,02 + 6,55 — sem os níveis 2
        assert pesos[5] == Decimal("45.13")

    def test_item_sem_nivel_1_fica_de_fora(
        self, repositorio_com_arvore_de_niveis: RepositorioOrcamentosWbcSql
    ) -> None:
        """Ausente é diferente de zero: é o que faz a linha não receber o campo,
        e o SAP manter o peso do cadastro."""
        pesos = repositorio_com_arvore_de_niveis.pesos_por_item("00124853")
        assert 9 not in pesos

    def test_orcamento_sem_arvore_devolve_vazio(
        self, repositorio: RepositorioOrcamentosWbcSql
    ) -> None:
        assert repositorio.pesos_por_item("00123316") == {}

    def test_peso_zero_nao_entra(self) -> None:
        """Zero na árvore não é peso — enviá-lo apagaria o do cadastro."""
        engine = create_engine("sqlite://")
        with engine.begin() as conexao:
            for comando in ESQUEMA.strip().split(";"):
                if comando.strip():
                    conexao.execute(text(comando))
            conexao.execute(
                text(
                    "INSERT INTO INTEGRACAO_ORCPRDARV "
                    "(ORCNUM, ORCITM, ORCPRDARV_NIVEL, ORCPES, idIntegracao_OrcPrdArv) "
                    "VALUES ('00999999', 1, 1, 0, 1)"
                )
            )
        assert RepositorioOrcamentosWbcSql(engine).pesos_por_item("00999999") == {}

    def test_a_consulta_e_de_leitura(self) -> None:
        """Guarda-corpo: o WBC é somente leitura, em qualquer ambiente."""
        from wbcpython.infrastructure.wbc_sql import queries
        from wbcpython.safety import assert_read_only_sql

        assert_read_only_sql(queries.PESOS_NIVEL_1_POR_ITEM, fonte="teste")
