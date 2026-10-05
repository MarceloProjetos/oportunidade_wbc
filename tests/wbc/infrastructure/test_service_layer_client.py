"""Testes do cliente do Service Layer — offline, via httpx.MockTransport.

Nenhum destes testes acessa a rede: um transporte falso responde no lugar do
SAP. Isso permite cobrir login, expiração de sessão, erros do SAP e a trava de
segurança sem depender do ambiente da Altamira.

O smoke test contra o SAP real está em `test_service_layer_integration.py`.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from wbcpython.config import ServiceLayerSettings
from wbcpython.infrastructure.service_layer import (
    ServiceLayerClient,
    ServiceLayerError,
    ServiceLayerLoginError,
)
from wbcpython.safety import ProductionWriteBlocked

PROD = "SBOALTAMIRAPROD"
HOMOLOG = "SBOALTAMIRAHOMOLOG"


def _settings(company_db: str = HOMOLOG, **extra: Any) -> ServiceLayerSettings:
    valores: dict[str, Any] = {
        "base_url": "https://sap-falso:50000/b1s/v1",
        "company_db": company_db,
        "username": "usuario",
        "password": SecretStr("senha_secreta"),
        "verify_ssl": False,
    }
    valores.update(extra)
    return ServiceLayerSettings(_env_file=None, **valores)


class Gravador:
    """Transporte falso que registra as requisições e devolve respostas roteirizadas."""

    def __init__(self, roteiro: list[httpx.Response] | None = None) -> None:
        self.requisicoes: list[httpx.Request] = []
        self.roteiro = roteiro or []
        self.padrao = httpx.Response(200, json={"value": []})

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requisicoes.append(request)
        if self.roteiro:
            return self.roteiro.pop(0)
        return self.padrao

    @property
    def caminhos(self) -> list[str]:
        return [r.url.path for r in self.requisicoes]

    @property
    def metodos(self) -> list[str]:
        return [r.method for r in self.requisicoes]


def _cliente(
    gravador: Gravador,
    *,
    company_db: str = HOMOLOG,
    block: bool = True,
) -> ServiceLayerClient:
    return ServiceLayerClient(
        _settings(company_db),
        production_company_db=PROD,
        block_production_writes=block,
        transport=httpx.MockTransport(gravador),
    )


class TestLogin:
    def test_login_envia_credenciais_e_company_db(self) -> None:
        g = Gravador([httpx.Response(200, json={"SessionId": "abc"})])
        cliente = _cliente(g)
        cliente.login()

        assert cliente.autenticado is True
        assert g.caminhos == ["/b1s/v1/Login"]
        import json as _json

        corpo = _json.loads(g.requisicoes[0].content)
        assert corpo["CompanyDB"] == HOMOLOG
        assert corpo["UserName"] == "usuario"
        assert corpo["Password"] == "senha_secreta"

    def test_login_invalido_levanta_erro_de_login(self) -> None:
        g = Gravador(
            [
                httpx.Response(
                    401,
                    json={
                        "error": {"code": -304, "message": {"value": "Invalid user or password"}}
                    },
                )
            ]
        )
        with pytest.raises(ServiceLayerLoginError) as exc:
            _cliente(g).login()
        assert exc.value.sap_code == -304
        assert "Invalid user or password" in str(exc.value)

    def test_sem_credenciais_falha_antes_de_qualquer_requisicao(self) -> None:
        g = Gravador()
        cliente = ServiceLayerClient(
            _settings(username="", password=SecretStr("")),
            production_company_db=PROD,
            transport=httpx.MockTransport(g),
        )
        with pytest.raises(ServiceLayerLoginError) as exc:
            cliente.login()
        assert "SL_USERNAME" in str(exc.value)
        assert g.requisicoes == []  # não chegou a sair da máquina


class TestSessao:
    def test_primeira_requisicao_faz_login_automaticamente(self) -> None:
        g = Gravador(
            [
                httpx.Response(200, json={"SessionId": "abc"}),
                httpx.Response(200, json={"value": [{"DocEntry": 1}]}),
            ]
        )
        cliente = _cliente(g)
        cliente.get("OrcDetalhe")
        assert g.caminhos == ["/b1s/v1/Login", "/b1s/v1/OrcDetalhe"]

    def test_sessao_expirada_refaz_login_e_repete_a_requisicao(self) -> None:
        g = Gravador(
            [
                httpx.Response(200, json={"SessionId": "abc"}),  # login inicial
                httpx.Response(401, json={"error": {"code": -304}}),  # sessão expirou
                httpx.Response(200, json={"SessionId": "def"}),  # novo login
                httpx.Response(200, json={"value": []}),  # repetição
            ]
        )
        cliente = _cliente(g)
        cliente.login()
        resposta = cliente.get("OrcDetalhe")

        assert resposta.status_code == 200
        assert g.caminhos == [
            "/b1s/v1/Login",
            "/b1s/v1/OrcDetalhe",
            "/b1s/v1/Login",
            "/b1s/v1/OrcDetalhe",
        ]

    def test_401_persistente_levanta_erro(self) -> None:
        g = Gravador(
            [
                httpx.Response(200, json={"SessionId": "abc"}),
                httpx.Response(401, json={"error": {"code": -304}}),
                httpx.Response(200, json={"SessionId": "def"}),
                httpx.Response(401, json={"error": {"code": -304}}),
            ]
        )
        cliente = _cliente(g)
        cliente.login()
        with pytest.raises(ServiceLayerError) as exc:
            cliente.get("OrcDetalhe")
        assert exc.value.sessao_expirada is True

    def test_context_manager_loga_na_primeira_requisicao_e_desloga_na_saida(self) -> None:
        g = Gravador()
        with _cliente(g) as cliente:
            assert cliente.autenticado is False  # entrar no `with` não autentica
            cliente.get("OrcDetalhe")
            assert cliente.autenticado is True
        assert g.caminhos[0] == "/b1s/v1/Login"
        assert "/b1s/v1/Logout" in g.caminhos

    def test_context_manager_sem_requisicao_nao_toca_o_service_layer(self) -> None:
        """Um ciclo do worker sem escrita — a maioria — não pode custar um login:
        eram ~260 Login/Logout por dia só para descobrir que não havia nada a fazer."""
        g = Gravador()
        with _cliente(g) as cliente:
            assert cliente.autenticado is False
        assert g.caminhos == []

    def test_logout_sem_sessao_nao_faz_requisicao(self) -> None:
        g = Gravador()
        _cliente(g).logout()
        assert g.requisicoes == []


class TestTravaDeSeguranca:
    def test_bloqueia_post_em_producao_antes_de_sair_da_maquina(self) -> None:
        g = Gravador()
        cliente = _cliente(g, company_db=PROD)
        with pytest.raises(ProductionWriteBlocked):
            cliente.post("OrcDetalhe", json={"U_INO_COD": "00000001"})
        # O essencial: nenhuma requisição foi enviada — nem o login.
        assert g.requisicoes == []

    @pytest.mark.parametrize("metodo", ["POST", "PATCH", "DELETE"])
    def test_bloqueia_todos_os_metodos_de_escrita_em_producao(self, metodo: str) -> None:
        g = Gravador()
        cliente = _cliente(g, company_db=PROD)
        with pytest.raises(ProductionWriteBlocked):
            cliente.request(metodo, "Quotations(1)", json={})
        assert g.requisicoes == []

    def test_permite_leitura_em_producao(self) -> None:
        # A regra proíbe alterar produção, não consultá-la.
        g = Gravador(
            [
                httpx.Response(200, json={"SessionId": "abc"}),
                httpx.Response(200, json={"value": []}),
            ]
        )
        cliente = _cliente(g, company_db=PROD)
        cliente.get("OrcDetalhe")
        assert g.metodos == ["POST", "GET"]  # POST aqui é o /Login

    def test_permite_escrita_em_homologacao(self) -> None:
        g = Gravador(
            [
                httpx.Response(200, json={"SessionId": "abc"}),
                httpx.Response(201, json={"DocEntry": 99}),
            ]
        )
        cliente = _cliente(g)
        resposta = cliente.post("OrcDetalhe", json={"U_INO_COD": "00000001"})
        assert resposta.status_code == 201


class TestErrosDoSap:
    def test_traduz_envelope_de_erro_do_sap(self) -> None:
        g = Gravador(
            [
                httpx.Response(200, json={"SessionId": "abc"}),
                httpx.Response(
                    400,
                    json={
                        "error": {
                            "code": -5002,
                            "message": {"lang": "en-us", "value": "This CNPJ already exists"},
                        }
                    },
                ),
            ]
        )
        cliente = _cliente(g)
        with pytest.raises(ServiceLayerError) as exc:
            cliente.post("BusinessPartners", json={})
        assert exc.value.sap_code == -5002
        assert exc.value.status_code == 400
        assert "This CNPJ already exists" in str(exc.value)
        assert "POST" in str(exc.value)

    def test_tolera_resposta_que_nao_e_json(self) -> None:
        g = Gravador(
            [
                httpx.Response(200, json={"SessionId": "abc"}),
                httpx.Response(502, text="<html>Bad Gateway</html>"),
            ]
        )
        cliente = _cliente(g)
        with pytest.raises(ServiceLayerError) as exc:
            cliente.get("OrcDetalhe")
        assert exc.value.status_code == 502
        assert "Bad Gateway" in str(exc.value)

    def test_erro_nao_vaza_a_senha(self) -> None:
        g = Gravador([httpx.Response(401, json={"error": {"code": -304}})])
        with pytest.raises(ServiceLayerLoginError) as exc:
            _cliente(g).login()
        assert "senha_secreta" not in str(exc.value)


class TestListar:
    def test_monta_parametros_odata(self) -> None:
        g = Gravador(
            [
                httpx.Response(200, json={"SessionId": "abc"}),
                httpx.Response(200, json={"value": [{"DocEntry": 513925}]}),
            ]
        )
        cliente = _cliente(g)
        registros = cliente.listar(
            "OrcDetalhe",
            filtro="U_INO_COD eq '00123316'",
            ordenar_por="DocEntry desc",
            top=1,
            selecionar="DocEntry",
        )

        assert registros == [{"DocEntry": 513925}]
        consulta = dict(g.requisicoes[-1].url.params)
        assert consulta["$filter"] == "U_INO_COD eq '00123316'"
        assert consulta["$orderby"] == "DocEntry desc"
        assert consulta["$top"] == "1"
        assert consulta["$select"] == "DocEntry"

    def test_pede_o_tamanho_de_pagina_ao_servidor(self) -> None:
        """Sem o `Prefer`, o Service Layer entrega 20 e ignora o `$top`."""
        g = Gravador(
            [
                httpx.Response(200, json={"SessionId": "abc"}),
                httpx.Response(200, json={"value": []}),
            ]
        )
        _cliente(g).listar("SalesOpportunities", top=200)
        assert g.requisicoes[-1].headers["Prefer"] == "odata.maxpagesize=100"

    def test_atravessa_as_paginas_ate_a_pagina_curta(self) -> None:
        """Regressão do corte silencioso: o ciclo via 20 de uma janela de 1.785.

        Página cheia significa "pode haver mais"; página curta é o fim. O
        `@odata.nextLink` não serve de sinal aqui — o Service Layer o omite
        quando há `$top` na consulta.
        """
        cheia = [{"SequentialNo": i} for i in range(100)]
        g = Gravador(
            [
                httpx.Response(200, json={"SessionId": "abc"}),
                httpx.Response(200, json={"value": cheia}),
                httpx.Response(200, json={"value": cheia}),
                httpx.Response(200, json={"value": [{"SequentialNo": 999}]}),
            ]
        )
        registros = _cliente(g).listar("SalesOpportunities", top=500)

        assert len(registros) == 201
        saltos = [dict(r.url.params).get("$skip") for r in g.requisicoes[1:]]
        assert saltos == [None, "100", "200"]

    def test_para_no_teto_mesmo_com_paginas_cheias(self) -> None:
        """O teto por ciclo é real: não adianta o servidor ter mais."""
        cheia = [{"SequentialNo": i} for i in range(100)]
        g = Gravador(
            [
                httpx.Response(200, json={"SessionId": "abc"}),
                httpx.Response(200, json={"value": cheia}),
                httpx.Response(200, json={"value": cheia[:50]}),
            ]
        )
        registros = _cliente(g).listar("SalesOpportunities", top=150)

        assert len(registros) == 150
        # A última página pede só o que falta, não uma página inteira.
        assert dict(g.requisicoes[-1].url.params)["$top"] == "50"

    def test_uma_pagina_so_nao_faz_requisicao_extra(self) -> None:
        g = Gravador(
            [
                httpx.Response(200, json={"SessionId": "abc"}),
                httpx.Response(200, json={"value": [{"DocEntry": 1}]}),
            ]
        )
        _cliente(g).listar("OrcDetalhe", top=1)
        assert len(g.requisicoes) == 2  # login + uma consulta

    def test_resposta_sem_value_levanta_erro_claro(self) -> None:
        g = Gravador(
            [
                httpx.Response(200, json={"SessionId": "abc"}),
                httpx.Response(200, json={"algo": "inesperado"}),
            ]
        )
        with pytest.raises(ServiceLayerError, match="Resposta inesperada"):
            _cliente(g).listar("OrcDetalhe")


class TestRespostaNaoJson:
    """Regressão: 200 com corpo não-JSON estourava JSONDecodeError cru.

    É o sintoma clássico de SL_BASE_URL errado ou proxy no caminho — justamente
    quando a pessoa mais precisa de uma pista do que houve.
    """

    def test_200_com_html_vira_erro_legivel(self) -> None:
        g = Gravador(
            [
                httpx.Response(200, json={"SessionId": "abc"}),
                httpx.Response(200, html="<html>Portal de autenticação</html>"),
            ]
        )
        with pytest.raises(ServiceLayerError) as exc:
            _cliente(g).get_json("OrcDetalhe")
        mensagem = str(exc.value)
        assert "não é JSON" in mensagem
        assert "SL_BASE_URL" in mensagem

    def test_erro_inclui_amostra_do_corpo(self) -> None:
        g = Gravador(
            [
                httpx.Response(200, json={"SessionId": "abc"}),
                httpx.Response(200, text="corpo inesperado do proxy"),
            ]
        )
        with pytest.raises(ServiceLayerError, match="corpo inesperado"):
            _cliente(g).get_json("OrcDetalhe")

    def test_json_valido_continua_funcionando(self) -> None:
        g = Gravador(
            [
                httpx.Response(200, json={"SessionId": "abc"}),
                httpx.Response(200, json={"value": [{"DocEntry": 1}]}),
            ]
        )
        assert _cliente(g).get_json("OrcDetalhe") == {"value": [{"DocEntry": 1}]}


class TestRenovacaoPorCodigoSap:
    """Regressão: o retry só olhava HTTP 401 e ignorava o código SAP -304.

    A própria docstring do módulo dizia que algumas versões do Service Layer
    sinalizam sessão inválida com -304 sob outro status — mas esse caso não
    tinha renovação.
    """

    def test_codigo_304_sob_status_400_refaz_login(self) -> None:
        g = Gravador(
            [
                httpx.Response(200, json={"SessionId": "abc"}),  # login inicial
                httpx.Response(400, json={"error": {"code": -304}}),  # sessão inválida
                httpx.Response(200, json={"SessionId": "def"}),  # novo login
                httpx.Response(200, json={"value": []}),  # repetição
            ]
        )
        cliente = _cliente(g)
        cliente.login()
        resposta = cliente.get("OrcDetalhe")

        assert resposta.status_code == 200
        assert g.caminhos.count("/b1s/v1/Login") == 2

    def test_erro_de_negocio_nao_dispara_novo_login(self) -> None:
        # Um -5002 (CNPJ duplicado) não é sessão expirada: refazer login seria
        # inútil e mascararia o erro real.
        g = Gravador(
            [
                httpx.Response(200, json={"SessionId": "abc"}),
                httpx.Response(400, json={"error": {"code": -5002, "message": "CNPJ existe"}}),
            ]
        )
        cliente = _cliente(g)
        cliente.login()
        with pytest.raises(ServiceLayerError):
            cliente.post("BusinessPartners", json={})
        assert g.caminhos.count("/b1s/v1/Login") == 1
