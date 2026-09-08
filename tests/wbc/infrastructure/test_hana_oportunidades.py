"""Testes da leitura de oportunidades no HANA — offline, com conexão falsa.

O que se protege aqui é o que substituiu ~900 requisições ao Service Layer por
uma consulta. Dois pontos merecem teste explícito:

* o **schema** tem de ser o da company que recebe a escrita, e não o
  `HANA_SCHEMA` das views de relatório;
* o formato devolvido tem de ser o mesmo do Service Layer, senão a máquina de
  estados decide diferente só porque a leitura mudou de lugar.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from pydantic import SecretStr

from tests.wbc.infrastructure.test_hana_repository import ConexaoFalsa, colunas_projetadas
from wbcpython.config import HanaSettings
from wbcpython.infrastructure.hana.identificadores import IdentificadorInvalido
from wbcpython.infrastructure.hana.oportunidades import RepositorioOportunidadesHana

HOMOLOG = "SBOALTAMIRAHOMOLOG"
PROD = "SBOALTAMIRAPROD"


def _settings() -> HanaSettings:
    return HanaSettings(
        _env_file=None,  # type: ignore[call-arg]
        host="sapbusinessonehana-vm",
        port=30015,
        username="usuario",
        password=SecretStr("senha_secreta"),
        HANA_SCHEMA=PROD,
    )


def _repo(conexao: ConexaoFalsa, company_db: str = HOMOLOG) -> RepositorioOportunidadesHana:
    return RepositorioOportunidadesHana(
        _settings(), company_db=company_db, fabrica_de_conexao=lambda: conexao
    )


#: Colunas do `SELECT`, na ordem. Escrito à mão de propósito: é a declaração de
#: qual é o contrato, contra a qual tanto o SQL quanto o tradutor são medidos.
_CAMPOS_ESPERADOS = (
    "OpprId",
    "U_ORCNUM_WBC",
    "CardCode",
    "CardName",
    "SlpCode",
    "CprCode",
    "OpenDate",
    "Status",
    "U_INO_StatusWBC",
    "U_INO_Update",
    "U_INO_PN_Correc",
    "COT_DOCENTRY",
    "COT_DOCNUM",
    "COT_REVISAO",
    "COT_STATUS",
    "PED_DOCENTRY",
    "PED_DOCNUM",
    "PED_REVISAO",
    "PED_CARDCODE",
    "PED_STATUS",
    "PED_CONGELADO",
)


def _linha(**campos: Any) -> tuple[Any, ...]:
    """Linha na ordem exata em que o `SELECT` a devolve.

    As chaves aqui são os nomes das colunas projetadas: o cursor falso deriva o
    seu `description` do próprio SQL, então esta ordem tem de bater com a do
    `SELECT`. Uma coluna nova no meio da consulta quebra este teste — que é o
    ponto, porque é o que passou despercebido quando `Status` entrou.
    """
    base: dict[str, Any] = {
        "OpprId": 15146,
        "U_ORCNUM_WBC": "00125532",
        "CardCode": "C001",
        "CardName": "BALTEAU",
        "SlpCode": 11,
        "CprCode": 9308,
        "OpenDate": date(2026, 8, 20),
        "Status": "O",
        "U_INO_StatusWBC": "40",
        "U_INO_Update": "N",
        "U_INO_PN_Correc": "",
        "COT_DOCENTRY": None,
        "COT_DOCNUM": None,
        "COT_REVISAO": None,
        "COT_STATUS": None,
        "PED_DOCENTRY": None,
        "PED_DOCNUM": None,
        "PED_REVISAO": None,
        "PED_CARDCODE": None,
        "PED_STATUS": None,
        "PED_CONGELADO": None,
    }
    base.update(campos)
    return tuple(base.values())


class TestSchema:
    """A parte perigosa: ler de uma company e escrever noutra."""

    def test_usa_a_company_da_escrita_e_nao_o_hana_schema(self) -> None:
        """`HANA_SCHEMA` aponta para produção mesmo em homologação.

        Se a consulta usasse o `HANA_SCHEMA`, o ciclo leria o estado de produção
        e escreveria em homologação — criando documentos com base no que outra
        empresa já tem, sem nenhum sintoma visível.
        """
        conexao = ConexaoFalsa([])
        _repo(conexao, company_db=HOMOLOG).pendentes_de_integracao(desde=date(2026, 3, 1))

        assert f'"{HOMOLOG}"."OOPR"' in conexao.ultimo_sql
        assert PROD not in conexao.ultimo_sql

    def test_company_invalida_e_recusada_na_construcao(self) -> None:
        """O nome vai literal no SQL; falhar cedo é melhor que interpolar."""
        with pytest.raises(IdentificadorInvalido):
            _repo(ConexaoFalsa([]), company_db='X"."Y')


class TestConsulta:
    def test_filtra_janela_e_participacao_na_integracao(self) -> None:
        conexao = ConexaoFalsa([])
        _repo(conexao).pendentes_de_integracao(desde=date(2026, 3, 1))

        sql = conexao.ultimo_sql
        assert "\"U_INO_IntegrouWBC\" = 'Y'" in sql
        assert '"OpenDate" >= ?' in sql
        assert conexao.ultimos_parametros[0] == date(2026, 3, 1)

    def test_orcamento_especifico_vira_parametro(self) -> None:
        """Reprocessar um caso não pode virar concatenação de SQL."""
        conexao = ConexaoFalsa([])
        _repo(conexao).pendentes_de_integracao(desde=date(2026, 3, 1), orcamento="00125532")
        assert conexao.ultimos_parametros == (date(2026, 3, 1), "00125532", "00125532")

    def test_so_considera_documentos_nao_cancelados(self) -> None:
        """Mesmo critério do Service Layer (`Cancelled eq 'tNO'`)."""
        conexao = ConexaoFalsa([])
        _repo(conexao).pendentes_de_integracao(desde=date(2026, 3, 1))
        assert conexao.ultimo_sql.count("\"CANCELED\" = 'N'") == 2

    def test_escolhe_o_documento_mais_recente(self) -> None:
        conexao = ConexaoFalsa([])
        _repo(conexao).pendentes_de_integracao(desde=date(2026, 3, 1))
        assert conexao.ultimo_sql.count('ORDER BY "DocEntry" DESC') == 2

    def test_traz_o_status_da_oportunidade(self) -> None:
        """Sem `Status` não há como saber se o encerramento já aconteceu — e o
        `U_INO_StatusWBC` não serve, porque é espelhado a cada ciclo."""
        conexao = ConexaoFalsa([])
        _repo(conexao).pendentes_de_integracao(desde=date(2026, 3, 1))
        assert 'O."Status"' in conexao.ultimo_sql

    def test_todas_as_colunas_projetadas_sao_traduzidas(self) -> None:
        """A guarda contra o defeito do deslocamento.

        A tradução lê por nome, mas isso só protege se todo nome projetado for
        de fato consumido. Uma coluna nova no `SELECT` que ninguém traduz é
        trabalho invisível; uma coluna traduzida que saiu do `SELECT` é
        `KeyError` em produção. Este teste liga as duas pontas.
        """
        conexao = ConexaoFalsa([_linha()])
        _repo(conexao).pendentes_de_integracao(desde=date(2026, 3, 1))
        projetadas = colunas_projetadas(conexao.ultimo_sql)

        assert projetadas == list(_CAMPOS_ESPERADOS)

    def test_nao_escreve(self) -> None:
        """Guarda-corpo: este caminho é de leitura, e só.

        Os nomes de coluna são retirados antes da conferência — `U_INO_Update`
        contém "UPDATE" e faria o teste acusar escrita onde não há.
        """
        import re

        conexao = ConexaoFalsa([])
        _repo(conexao).pendentes_de_integracao(desde=date(2026, 3, 1))
        sem_identificadores = re.sub(r'"[^"]*"', " ", conexao.ultimo_sql).upper()

        for proibido in ("INSERT", "UPDATE", "DELETE", "MERGE", "CREATE", "DROP"):
            assert proibido not in sem_identificadores


class TestFormato:
    """O formato tem de ser o do Service Layer — a decisão não pode mudar."""

    def test_traduz_para_os_nomes_do_service_layer(self) -> None:
        conexao = ConexaoFalsa([_linha()])
        registros = _repo(conexao).pendentes_de_integracao(desde=date(2026, 3, 1))

        assert registros[0]["SequentialNo"] == 15146
        assert registros[0]["U_ORCNUM_WBC"] == "00125532"
        assert registros[0]["CardCode"] == "C001"
        assert registros[0]["SalesPerson"] == 11
        assert registros[0]["ContactPerson"] == 9308
        assert registros[0]["U_INO_StatusWBC"] == "40"
        assert registros[0]["Status"] == "O"

    def test_sem_documento_o_bloco_vem_nulo(self) -> None:
        conexao = ConexaoFalsa([_linha()])
        registros = _repo(conexao).pendentes_de_integracao(desde=date(2026, 3, 1))
        assert registros[0]["documentos"] == {"cotacao": None, "pedido": None}

    def test_documentos_resolvidos_viajam_junto(self) -> None:
        """É o que dispensa quatro requisições por orçamento ao SAP."""
        conexao = ConexaoFalsa(
            [
                _linha(
                    COT_DOCENTRY=101901,
                    COT_DOCNUM=77829,
                    COT_REVISAO="A",
                    PED_DOCENTRY=19489,
                    PED_DOCNUM=84315,
                    PED_REVISAO="",
                    PED_CARDCODE="C011081",
                    COT_STATUS="O",
                    PED_STATUS="C",
                )
            ]
        )
        registros = _repo(conexao).pendentes_de_integracao(desde=date(2026, 3, 1))

        assert registros[0]["documentos"]["cotacao"] == {
            "DocEntry": 101901,
            # `DocNum` é só apresentação — o número que as pessoas citam. A
            # decisão continua olhando `DocEntry` e a revisão.
            "DocNum": 77829,
            "U_INO_VERSAOWBC": "A",
            "CardCode": "",
            "DocStatus": "O",
        }
        # A cotação **não** ganha `U_INO_Congelado`: o UDF é do `ORDR`, e uma
        # chave vazia ali sugeriria um campo que a cotação não tem.
        assert "U_INO_Congelado" not in registros[0]["documentos"]["cotacao"]
        assert registros[0]["documentos"]["pedido"]["U_INO_Congelado"] == ""
        assert registros[0]["documentos"]["pedido"] == {
            "DocEntry": 19489,
            "DocNum": 84315,
            "U_INO_Congelado": "",
            "U_INO_VERSAOWBC": "",
            # É o `ChecaPNPedido` do legado: sem o CardCode do pedido não há
            # como saber se a troca de parceiro já foi aplicada.
            "CardCode": "C011081",
            # `'C'` = fechado. Sem esta coluna a integração tentava alterar
            # pedido fechado, e o SAP recusava — ver o orçamento `00124268`.
            "DocStatus": "C",
        }

    def test_texto_nulo_vira_string_vazia(self) -> None:
        """A máquina de estados compara strings; `None` mudaria a decisão."""
        conexao = ConexaoFalsa(
            [_linha(U_INO_StatusWBC=None, U_INO_Update=None, U_INO_PN_Correc=None)]
        )
        registros = _repo(conexao).pendentes_de_integracao(desde=date(2026, 3, 1))

        assert registros[0]["U_INO_StatusWBC"] == ""
        assert registros[0]["U_INO_Update"] == ""
        assert registros[0]["U_INO_PN_Correc"] == ""


class TestDatasDeAbertura:
    """A varredura que preenche o histórico do acompanhamento."""

    def test_le_sem_filtro_de_janela(self) -> None:
        """O alvo são justamente os que já saíram da janela — filtrar por ela
        deixaria de fora exatamente quem precisa da data."""
        conexao = ConexaoFalsa([("00124619", "2026-05-05")])
        _repo(conexao).datas_de_abertura(["00124619"])

        assert "OpenDate" in conexao.ultimo_sql
        assert ">=" not in conexao.ultimo_sql

    def test_orcamentos_viajam_como_parametro(self) -> None:
        """Números de orçamento não podem virar concatenação de SQL."""
        conexao = ConexaoFalsa([])
        _repo(conexao).datas_de_abertura(["00000001", "00000002"])

        assert conexao.ultimos_parametros == ("00000001", "00000002")
        assert "00000001" not in conexao.ultimo_sql

    def test_lista_vazia_nao_consulta(self) -> None:
        conexao = ConexaoFalsa([])
        assert _repo(conexao).datas_de_abertura([]) == {}
        assert conexao.executados == []

    def test_usa_a_company_da_escrita(self) -> None:
        conexao = ConexaoFalsa([])
        _repo(conexao, company_db=HOMOLOG).datas_de_abertura(["00000001"])
        assert f'"{HOMOLOG}"."OOPR"' in conexao.ultimo_sql
        assert PROD not in conexao.ultimo_sql
