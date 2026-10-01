"""The login gate of the Controle de Produção screen (28/09/2026).

Same cookie and HMAC as the painel WBC: the cookie the painel issues must open this app,
and the one this app issues must be byte-identical. With no key the screen is open for
reading and writes answer 503 (fail-closed); with a key, pages need the cookie or
``X-API-Key``, and a POST by cookie must come from the same host (CSRF).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from controleproducao.config import get_settings
from controleproducao.main import app
from wbcpython.dashboard.acesso import COOKIE_DE_ACESSO, token_da_chave

CHAVE = "chave-da-api-8077"


def _com_chave(monkeypatch: pytest.MonkeyPatch, chave: str = CHAVE) -> None:
    monkeypatch.setenv("OS_API_KEY", chave)
    get_settings.cache_clear()


@pytest.fixture
def aberto() -> TestClient:
    """Sem OS_API_KEY (o conftest já a deixa vazia)."""
    return TestClient(app)


@pytest.fixture
def fechado(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    _com_chave(monkeypatch)
    return TestClient(app)


@pytest.fixture
def logado(fechado: TestClient) -> TestClient:
    """The painel's cookie, exactly as the painel would issue it (same key → same HMAC)."""
    fechado.cookies.set(COOKIE_DE_ACESSO, token_da_chave(CHAVE))
    return fechado


class TestSemChave:
    def test_a_pagina_abre_direto(self, aberto: TestClient) -> None:
        assert aberto.get("/").status_code == 200

    def test_entrar_manda_de_volta(self, aberto: TestClient) -> None:
        assert aberto.get("/entrar", follow_redirects=False).headers["location"] == "/"

    def test_sem_botao_de_sair(self, aberto: TestClient) -> None:
        assert 'action="/sair"' not in aberto.get("/").text

    def test_escrita_e_503_fail_closed(self, aberto: TestClient) -> None:
        """A screen nobody logs into must not create OPs: Liberar writes on the first POST."""
        resposta = aberto.post("/manutencao-op/status", data={"acao": "l", "op_docnums": "1"})
        assert resposta.status_code == 503
        assert "OS_API_KEY" in resposta.json()["detail"]

    def test_health_diz_que_nao_ha_chave(self, aberto: TestClient) -> None:
        assert aberto.get("/health").json()["chave_configurada"] is False


class TestComChave:
    def test_a_pagina_pede_a_chave(self, fechado: TestClient) -> None:
        resposta = fechado.get("/pedidos-wbc?x=1", follow_redirects=False)
        assert resposta.status_code == 303
        assert resposta.headers["location"] == "/entrar?proximo=%2Fpedidos-wbc%3Fx%3D1"

    @pytest.mark.parametrize("caminho", ["/health", "/health/ocupado", "/entrar", "/favicon.ico"])
    def test_rotas_abertas(self, fechado: TestClient, caminho: str) -> None:
        assert fechado.get(caminho, follow_redirects=False).status_code in (200, 204)

    def test_painel_wbc_redireciona_sem_chave(self, fechado: TestClient) -> None:
        resposta = fechado.get("/painel-wbc", follow_redirects=False)
        assert resposta.status_code == 302
        assert resposta.headers["location"] == "http://testserver:8079/"

    def test_painel_wbc_url_configurada_ganha(self, fechado: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WBC_PAINEL_URL", "http://192.168.7.11:8079/")
        get_settings.cache_clear()
        assert fechado.get("/painel-wbc", follow_redirects=False).headers["location"] == "http://192.168.7.11:8079/"

    def test_sincronizacao_redireciona_sem_chave(self, fechado: TestClient) -> None:
        # The API 8077 painel: same host, OS_API_PORT, /sincronizar (its root bounces back).
        resposta = fechado.get("/sincronizacao", follow_redirects=False)
        assert resposta.status_code == 302
        assert resposta.headers["location"] == "http://testserver:8077/sincronizar"

    def test_sincronizacao_url_configurada_ganha(self, fechado: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SIS_PAINEL_URL", "http://192.168.7.11:8077/sincronizar")
        get_settings.cache_clear()
        destino = fechado.get("/sincronizacao", follow_redirects=False).headers["location"]
        assert destino == "http://192.168.7.11:8077/sincronizar"

    def test_menu_tem_os_links_juntos(self, logado: TestClient) -> None:
        # Owner, 01/10/2026: the screen links in a row, Sincronização between Manutenção de OP
        # and Painel WBC; only Sair and the theme toggle on the right.
        html = logado.get("/manutencao-op").text
        nav = html[html.index('<nav class="ov-nav">'):html.index("</nav>")]
        ordem = [nav.index(h) for h in ('href="/manutencao-op"', 'href="/sincronizacao"',
                                        'href="/painel-wbc"', 'href="/tarefas"',
                                        'class="direita"')]
        assert ordem == sorted(ordem)

    def test_orcaview_redireciona_sem_chave(self, fechado: TestClient) -> None:
        # The way back to the OrçaView home must work for someone who has no key.
        resposta = fechado.get("/orcaview", follow_redirects=False)
        assert resposta.status_code == 302
        assert resposta.headers["location"] == "http://192.168.0.90:8000/"

    def test_orcaview_url_configurada_ganha(self, fechado: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ORCAVIEW_URL", "http://localhost:8000/")
        get_settings.cache_clear()
        assert fechado.get("/orcaview", follow_redirects=False).headers["location"] == "http://localhost:8000/"

    def test_link_para_o_orcaview_na_barra(self, fechado: TestClient) -> None:
        # Even the key prompt shows it: that is exactly where one gets stuck. Right after the
        # "Controle de Produção" brand, with a green arrow (owner's calls, 30/09/2026).
        html = fechado.get("/entrar").text
        assert html.index("Controle de Produção</span>") < html.index('href="/orcaview"')
        assert 'class="item voltar" href="/orcaview"' in html
        css = fechado.get("/static/style.css").text
        assert ".ov-nav a.item.voltar .icone { color: var(--color-success); }" in css

    def test_docs_fechados(self, logado: TestClient) -> None:
        for caminho in ("/docs", "/redoc", "/openapi.json"):
            assert logado.get(caminho).status_code == 404

    def test_estado_da_tarefa_tambem_pede_a_chave(self, fechado: TestClient) -> None:
        """The JSON the browser polls and the cancel button stay behind the gate."""
        assert fechado.get("/tarefas/x/estado", follow_redirects=False).status_code == 303
        assert fechado.post("/tarefas/x/cancelar", follow_redirects=False).status_code == 303

    def test_chave_errada_e_401_e_nao_grava_cookie(self, fechado: TestClient) -> None:
        resposta = fechado.post("/entrar", data={"chave": "errada", "proximo": "/"}, follow_redirects=False)
        assert resposta.status_code == 401
        assert COOKIE_DE_ACESSO not in fechado.cookies

    def test_chave_certa_vira_o_mesmo_cookie_do_painel(self, fechado: TestClient) -> None:
        resposta = fechado.post(
            "/entrar", data={"chave": CHAVE, "proximo": "/manutencao-op"}, follow_redirects=False
        )
        assert resposta.status_code == 303
        assert resposta.headers["location"] == "/manutencao-op"
        assert fechado.cookies[COOKIE_DE_ACESSO] == token_da_chave(CHAVE)
        assert CHAVE not in fechado.cookies[COOKIE_DE_ACESSO]
        assert fechado.get("/").status_code == 200
        assert 'action="/sair"' in fechado.get("/").text

    def test_proximo_nao_redireciona_para_fora(self, fechado: TestClient) -> None:
        resposta = fechado.post(
            "/entrar", data={"chave": CHAVE, "proximo": "http://mal.example/"}, follow_redirects=False
        )
        assert resposta.headers["location"] == "/"

    def test_cookie_do_painel_entra_aqui(self, logado: TestClient) -> None:
        assert logado.get("/").status_code == 200

    def test_cookie_forjado_nao_entra(self, fechado: TestClient) -> None:
        fechado.cookies.set(COOKIE_DE_ACESSO, "x" * 64)
        assert fechado.get("/", follow_redirects=False).status_code == 303

    def test_x_api_key_entra_sem_cookie(self, fechado: TestClient) -> None:
        assert fechado.get("/", headers={"X-API-Key": CHAVE}).status_code == 200

    def test_sair_esquece_a_chave(self, logado: TestClient) -> None:
        resposta = logado.post("/sair", headers={"Origin": "http://testserver"}, follow_redirects=False)
        assert resposta.status_code == 303 and resposta.headers["location"] == "/entrar"
        # The browser drops the cookie on this header; the TestClient jar keeps one set by
        # hand, so the proof is the header itself.
        apagado = resposta.headers["set-cookie"]
        assert apagado.startswith(f"{COOKIE_DE_ACESSO}=") and ("Max-Age=0" in apagado or "expires=" in apagado.lower())

    def test_trocar_a_chave_derruba_o_cookie(self, logado: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        _com_chave(monkeypatch, "outra")
        assert logado.get("/", follow_redirects=False).status_code == 303


class TestOrigem:
    """A POST by cookie must be same-origin; by key header it does not matter."""

    def test_post_por_cookie_sem_origin_e_403(self, logado: TestClient) -> None:
        assert logado.post("/tarefas/x/cancelar").status_code == 403

    def test_post_por_cookie_de_outro_host_e_403(self, logado: TestClient) -> None:
        assert logado.post("/tarefas/x/cancelar", headers={"Origin": "http://mal.example"}).status_code == 403

    # 200 + {"cancelada": false}: the gate passed and the route itself answered (no such task).
    def test_post_por_cookie_do_mesmo_host_passa(self, logado: TestClient) -> None:
        resposta = logado.post("/tarefas/x/cancelar", headers={"Origin": "http://testserver"})
        assert resposta.status_code == 200 and resposta.json() == {"cancelada": False, "entre_etapas": False}

    def test_post_por_cookie_de_outra_porta_do_mesmo_host_e_403(self, logado: TestClient) -> None:
        """30/09/2026: host-only matching let the 8077/8078/8079 pages of this machine through."""
        assert logado.post("/tarefas/x/cancelar", headers={"Origin": "http://testserver:8079"}).status_code == 403

    def test_referer_vale_como_origin(self, logado: TestClient) -> None:
        resposta = logado.post("/tarefas/x/cancelar", headers={"Referer": "http://testserver/tarefas/x"})
        assert resposta.status_code == 200

    def test_post_por_chave_sem_origin_passa(self, fechado: TestClient) -> None:
        assert fechado.post("/tarefas/x/cancelar", headers={"X-API-Key": CHAVE}).status_code == 200


class TestChamadaDeScript:
    """A fetch from the page must SEE the refusal (401 JSON), never a silent redirect."""

    def test_polling_sem_cookie_recebe_401_json(self, fechado: TestClient) -> None:
        resposta = fechado.get("/tarefas/x/estado", headers={"X-Requested-With": "fetch"}, follow_redirects=False)
        assert resposta.status_code == 401
        assert resposta.json()["entrar"] == "/entrar"

    def test_interromper_sem_cookie_recebe_401_json(self, fechado: TestClient) -> None:
        resposta = fechado.post("/tarefas/x/cancelar", headers={"X-Requested-With": "fetch"}, follow_redirects=False)
        assert resposta.status_code == 401

    def test_quem_pede_json_tambem_recebe_401(self, fechado: TestClient) -> None:
        resposta = fechado.get("/tarefas/x/estado", headers={"Accept": "application/json"}, follow_redirects=False)
        assert resposta.status_code == 401

    def test_navegacao_normal_continua_com_o_redirect(self, fechado: TestClient) -> None:
        assert fechado.get("/tarefas/x", follow_redirects=False).status_code == 303

    def test_origin_null_e_403(self, logado: TestClient) -> None:
        assert logado.post("/tarefas/x/cancelar", headers={"Origin": "null"}).status_code == 403


class TestHealth:
    def test_health_e_json_sem_segredo(self, aberto: TestClient) -> None:
        corpo = aberto.get("/health").json()
        assert corpo["ok"] is True and corpo["servico"] == "controleproducao"
        assert corpo["ocupado"] is False and corpo["tarefas_ativas"] == 0
        assert "company_db" in corpo and "producao" in corpo
        assert not any(k in corpo for k in ("sl_password", "senha", "chave"))

    def test_ocupado_e_texto_puro(self, aberto: TestClient) -> None:
        resposta = aberto.get("/health/ocupado")
        assert resposta.text == "0"
        assert resposta.headers["content-type"].startswith("text/plain")

    def test_ocupado_ve_tarefa_em_andamento(self, aberto: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        from controleproducao.core import acesso

        monkeypatch.setattr(acesso, "tarefas_ativas", lambda: 2)
        assert aberto.get("/health/ocupado").text == "1"
        assert aberto.get("/health").json()["ocupado"] is True

    def test_ocupado_ve_gravacao_do_historico_em_andamento(
        self, aberto: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """30/09/2026: a deploy right after a run ended could kill the Supabase write."""
        from controleproducao.core.tarefas import TAREFAS

        monkeypatch.setattr(TAREFAS, "gravacoes_pendentes", lambda: 1)
        assert aberto.get("/health/ocupado").text == "1"
        corpo = aberto.get("/health").json()
        assert corpo["ocupado"] is True and corpo["gravacoes_pendentes"] == 1 and corpo["tarefas_ativas"] == 0
