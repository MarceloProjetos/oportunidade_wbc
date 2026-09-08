"""A entrada do painel: chave de acesso e o caminho para o Painel de Sincronização.

O que muda com `OS_API_KEY` no `.env` é só isto — sem ela o painel continua
exatamente como era (aberto). Por isso o primeiro teste é o de que nada mudou.

A chave é a mesma da API 8077, de propósito: uma chave para as duas telas.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from wbcpython.config import Settings
from wbcpython.dashboard.web import COOKIE_DE_ACESSO, _destino_local, _token_da_chave, criar_app
from wbcpython.tracking import RepositorioTracking

CHAVE = "chave-da-api-8077"


@pytest.fixture
def repo(tmp_path: Path) -> RepositorioTracking:
    return RepositorioTracking.a_partir_da_url(f"sqlite:///{tmp_path}/painel.db")


def _config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **extras: str) -> Settings:
    monkeypatch.setenv("TRACKING_DB_URL", f"sqlite:///{tmp_path}/painel.db")
    monkeypatch.setenv("SL_BASE_URL", "https://exemplo:50000/b1s/v1")
    monkeypatch.setenv("SL_COMPANY_DB", "SBOALTAMIRAHOMOLOG")
    monkeypatch.setenv("SL_USERNAME", "u")
    monkeypatch.setenv("SL_PASSWORD", "p")
    monkeypatch.setenv("LOG_FILE", "")
    for nome, valor in extras.items():
        monkeypatch.setenv(nome, valor)
    return Settings()


@pytest.fixture
def aberto(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, repo: RepositorioTracking) -> TestClient:
    """Sem OS_API_KEY: o painel de sempre."""
    return TestClient(criar_app(settings=_config(monkeypatch, tmp_path), tracking=repo))


@pytest.fixture
def fechado(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, repo: RepositorioTracking) -> TestClient:
    """Com OS_API_KEY: pede a chave uma vez."""
    config = _config(monkeypatch, tmp_path, OS_API_KEY=CHAVE)
    assert config.painel_exige_chave
    return TestClient(criar_app(settings=config, tracking=repo))


class TestSemChaveNadaMuda:
    def test_a_pagina_abre_direto(self, aberto: TestClient) -> None:
        assert aberto.get("/").status_code == 200

    def test_fragmentos_abrem_direto(self, aberto: TestClient) -> None:
        assert aberto.get("/fragmentos/kpis").status_code == 200

    def test_entrar_manda_de_volta_para_a_pagina(self, aberto: TestClient) -> None:
        resposta = aberto.get("/entrar", follow_redirects=False)
        assert resposta.status_code == 303
        assert resposta.headers["location"] == "/"

    def test_sem_botao_de_sair(self, aberto: TestClient) -> None:
        assert 'action="/sair"' not in aberto.get("/").text


class TestComChave:
    def test_a_pagina_pede_a_chave(self, fechado: TestClient) -> None:
        resposta = fechado.get("/?aba=log", follow_redirects=False)
        assert resposta.status_code == 303
        assert resposta.headers["location"] == "/entrar?proximo=%2F%3Faba%3Dlog"

    def test_o_fragmento_htmx_manda_o_navegador_inteiro_para_a_entrada(
        self, fechado: TestClient
    ) -> None:
        """Devolver a tela de entrada dentro de um bloco da página seria uma
        tela quebrada; o HTMX obedece ao cabeçalho HX-Redirect."""
        resposta = fechado.get("/fragmentos/kpis", headers={"HX-Request": "true"})
        assert resposta.status_code == 401
        assert resposta.headers["HX-Redirect"] == "/entrar"

    def test_o_css_da_entrada_abre_sem_chave(self, fechado: TestClient) -> None:
        assert fechado.get("/static/painel.css").status_code == 200

    def test_a_tela_de_entrada_explica_que_chave_e(self, fechado: TestClient) -> None:
        texto = fechado.get("/entrar").text
        assert "OS_API_KEY" in texto
        assert "Painel de Sincronização" in texto

    def test_chave_errada_e_401_e_nao_grava_cookie(self, fechado: TestClient) -> None:
        resposta = fechado.post("/entrar", data={"chave": "errada", "proximo": "/"})
        assert resposta.status_code == 401
        assert "Chave incorreta" in resposta.text
        assert COOKIE_DE_ACESSO not in fechado.cookies

    def test_chave_certa_vira_cookie_e_a_pagina_abre(self, fechado: TestClient) -> None:
        resposta = fechado.post(
            "/entrar", data={"chave": CHAVE, "proximo": "/?aba=log"}, follow_redirects=False
        )
        assert resposta.status_code == 303
        assert resposta.headers["location"] == "/?aba=log"
        assert fechado.cookies[COOKIE_DE_ACESSO] == _token_da_chave(CHAVE)
        assert CHAVE not in fechado.cookies[COOKIE_DE_ACESSO]  # token, nunca a chave
        assert fechado.get("/").status_code == 200
        assert 'action="/sair"' in fechado.get("/").text

    def test_o_cookie_e_httponly(self, fechado: TestClient) -> None:
        resposta = fechado.post("/entrar", data={"chave": CHAVE}, follow_redirects=False)
        assert "httponly" in resposta.headers["set-cookie"].lower()

    def test_cookie_forjado_nao_entra(self, fechado: TestClient) -> None:
        fechado.cookies.set(COOKIE_DE_ACESSO, "qualquer-coisa")
        assert fechado.get("/", follow_redirects=False).status_code == 303

    def test_cabecalho_x_api_key_entra_sem_cookie(self, fechado: TestClient) -> None:
        """Scripts e curl: a mesma X-API-Key da API 8077."""
        assert fechado.get("/", headers={"X-API-Key": CHAVE}).status_code == 200
        assert fechado.get("/", headers={"X-API-Key": "nao"}, follow_redirects=False).status_code == 303

    def test_key_na_url_entra_como_no_navegador_da_api(self, fechado: TestClient) -> None:
        assert fechado.get(f"/?key={CHAVE}").status_code == 200

    def test_sair_esquece_a_chave(self, fechado: TestClient) -> None:
        fechado.post("/entrar", data={"chave": CHAVE}, follow_redirects=False)
        assert fechado.get("/").status_code == 200
        resposta = fechado.post("/sair", follow_redirects=False)
        assert resposta.status_code == 303
        assert fechado.get("/", follow_redirects=False).status_code == 303

    def test_a_entrada_nao_redireciona_para_fora(self, fechado: TestClient) -> None:
        """`proximo` vem da URL: a tela de entrada não pode virar redirecionador."""
        resposta = fechado.post(
            "/entrar", data={"chave": CHAVE, "proximo": "//outro-host/x"}, follow_redirects=False
        )
        assert resposta.headers["location"] == "/"


class TestDestinoLocal:
    @pytest.mark.parametrize(
        "proximo, esperado",
        [
            ("/", "/"),
            ("/?aba=log", "/?aba=log"),
            ("", "/"),
            ("//outro-host", "/"),
            ("http://outro-host/", "/"),
            ("/\\\\outro", "/"),
            ("relativo", "/"),
        ],
    )
    def test_so_caminhos_desta_pagina(self, proximo: str, esperado: str) -> None:
        assert _destino_local(proximo) == esperado


class TestCaminhoParaASincronizacao:
    def test_o_botao_esta_na_pagina(self, aberto: TestClient) -> None:
        texto = aberto.get("/").text
        assert 'href="/sincronizacao"' in texto
        assert "Sincronização SAP" in texto

    def test_sem_configuracao_e_o_mesmo_host_na_porta_da_api(self, aberto: TestClient) -> None:
        resposta = aberto.get("/sincronizacao", follow_redirects=False)
        assert resposta.status_code == 302
        assert resposta.headers["location"] == "http://testserver:8077/"

    def test_porta_da_api_vem_do_env(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, repo: RepositorioTracking
    ) -> None:
        cliente = TestClient(
            criar_app(settings=_config(monkeypatch, tmp_path, OS_API_PORT="9000"), tracking=repo)
        )
        assert cliente.get("/sincronizacao", follow_redirects=False).headers["location"] == (
            "http://testserver:9000/"
        )

    def test_url_configurada_ganha(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, repo: RepositorioTracking
    ) -> None:
        cliente = TestClient(
            criar_app(
                settings=_config(monkeypatch, tmp_path, SIS_PAINEL_URL="http://192.168.7.11:8077/"),
                tracking=repo,
            )
        )
        assert cliente.get("/sincronizacao", follow_redirects=False).headers["location"] == (
            "http://192.168.7.11:8077/"
        )

    def test_abre_mesmo_com_chave_exigida(self, fechado: TestClient) -> None:
        """Quem não tem a chave ainda consegue ir para a outra tela."""
        assert fechado.get("/sincronizacao", follow_redirects=False).status_code == 302
