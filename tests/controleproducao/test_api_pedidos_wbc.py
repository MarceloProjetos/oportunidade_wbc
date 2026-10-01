"""JSON API of the Pedidos WBC (01/10/2026, F3 of docs/PLANO_API_PEDIDOS_WBC.md).

The API exists so another team can clone the screen *Integração de Pedidos (WBC)* with the
same results. What these tests hold, most important first:

1. **Parity with the screen:** the same list, the same page, the same plan, the same refusal
   texts — the tests ask both and compare.
2. **No "forçar" through the API** (owner, 01/10/2026), and the service is always called
   with ``force=False`` from here.
3. **Nothing writes without the gate, and the token is spent only when the run can start**
   (missing requester, busy module, refused write, token of another operation).
4. **Executions started here are the screen's executions:** one per module, same Execuções,
   plus who asked.

Everything outside the process is doubled: HANA readers are MagicMocks, the Service Layer is
`_SLFalso`, and the service functions are patched per test.
"""
from __future__ import annotations

import asyncio
import html
import re
import threading
import time
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from controleproducao.config import get_settings
from controleproducao.core.confirmacao import PLANOS
from controleproducao.core.tarefas import TAREFAS, Tarefa
from controleproducao.main import app
from controleproducao.modules.pedidos_wbc import acoes
from controleproducao.modules.pedidos_wbc.schemas import PedidoParaIntegrar
from wbcpython.dashboard.acesso import COOKIE_DE_ACESSO, token_da_chave

CHAVE = "chave-de-teste"
API = "/api/pedidos-wbc"
CABECALHO = {"X-API-Key": CHAVE}
SVC = "controleproducao.modules.pedidos_wbc.service"
ACOES = "controleproducao.modules.pedidos_wbc.acoes"


# ---------------------------------------------------------------------------
# Scaffolding
# ---------------------------------------------------------------------------
def _settings(producao: bool = False):
    return SimpleNamespace(
        is_production=producao,
        sl_company_db="SBOALTAMIRAPROD" if producao else "SBOALTAMIRAHOMOLOG",
        hana_schema="SBOALTAMIRAHOMOLOG",
        sl_business_place_id=1,
    )


class _SLFalso:
    def __init__(self, *_a, **_k) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


def _pedido(n: int) -> PedidoParaIntegrar:
    return PedidoParaIntegrar(
        opp_id=4300 + n, doc_num=84200 + n, cod_cliente=f"C{n:04d}",
        nome_cliente=f"Cliente {n}", total_pedido=1234.5 + n,
        data_lancamento=f"2026-09-{(n % 28) + 1:02d} 00:00:00",
        orc_num_masc=f"0012{n:04d}",
    )


PEDIDOS = [_pedido(n) for n in range(1, 41)]


@pytest.fixture(autouse=True)
def _limpa_registros():
    def _zera():
        for job in list(TAREFAS._jobs.values()):
            job.cancel()
        TAREFAS._jobs.clear()
        TAREFAS._tarefas.clear()
        TAREFAS._ordem.clear()
        TAREFAS._em_execucao.clear()
        PLANOS._planos.clear()

    _zera()
    yield
    _zera()


@pytest.fixture
def c(monkeypatch):
    """Key configured; the client ALSO carries the screen's cookie, so one client drives both
    the screen (cookie) and the API (``headers=CABECALHO``)."""
    monkeypatch.setenv("OS_API_KEY", CHAVE)
    get_settings.cache_clear()
    with TestClient(app, headers={"Origin": "http://testserver"}) as cliente:
        cliente.cookies.set(COOKIE_DE_ACESSO, token_da_chave(CHAVE))
        yield cliente


@pytest.fixture
def lista():
    """The eligible list, the same for the screen and the API (a mutable list per test)."""
    pedidos = list(PEDIDOS)
    with patch(f"{SVC}.buscar_pedidos_para_integrar", AsyncMock(side_effect=lambda *_a, **_k: list(pedidos))):
        yield pedidos


@pytest.fixture
def ambiente(lista):
    """Non-production target and no real HANA reader, for the API and the screen."""
    with patch("controleproducao.core.web.get_settings", return_value=_settings()), \
         patch("controleproducao.modules.pedidos_wbc.api_router.HanaDirectReader", MagicMock()), \
         patch("controleproducao.modules.pedidos_wbc.router.HanaDirectReader", MagicMock()), \
         patch("controleproducao.modules.pedidos_wbc.router.get_settings", return_value=_settings()):
        yield lista


@pytest.fixture
def execucao_falsa():
    """What a run touches outside the process, doubled (kept open until it ends)."""
    with patch(f"{ACOES}.get_settings", return_value=_settings()), \
         patch(f"{ACOES}.ServiceLayerClient", _SLFalso), \
         patch(f"{ACOES}.HanaDirectReader", MagicMock()), \
         patch(f"{ACOES}.WbcSqlServerClient", MagicMock()):
        yield


def _espera(c, tarefa_id: str, teto: int = 300) -> dict:
    estado: dict = {}
    for _ in range(teto):
        estado = c.get(f"{API}/execucoes/{tarefa_id}", headers=CABECALHO).json()["execucao"]
        if estado.get("terminada"):
            break
        time.sleep(0.01)
    return estado


def _texto(pagina: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", pagina)).split())


def _conferir(c, oportunidades, modo="processar", **extra):
    return c.post(f"{API}/{modo}/conferir", json={"oportunidades": oportunidades, **extra},
                  headers=CABECALHO)


def _token(c, oportunidades=(4301,), modo="processar") -> str:
    resposta = _conferir(c, list(oportunidades), modo)
    assert resposta.status_code == 200, resposta.text
    return resposta.json()["plano"]["token"]


class _Trava:
    """A service call that holds the module busy until `solta()` (the task runs on the
    TestClient's loop thread)."""

    def __init__(self) -> None:
        self.evento = threading.Event()
        self.recebidos: list[tuple[list, bool]] = []

    async def __call__(self, sl, wbc, hana, ids, force=False):
        self.recebidos.append((list(ids), force))
        while not self.evento.is_set():
            await asyncio.sleep(0.005)
        return {"processados": list(ids), "com_erro": []}

    def solta(self) -> None:
        self.evento.set()


# ---------------------------------------------------------------------------
# 1. The key and the transport
# ---------------------------------------------------------------------------
def test_sem_x_api_key_e_401_mesmo_com_o_cookie_da_tela(c, ambiente):
    for resposta in (c.get(f"{API}/pedidos"), c.get(f"{API}/pedidos", headers={"X-API-Key": "x"})):
        assert resposta.status_code == 401
        assert resposta.json() == {"ok": False, "tipo": "sem_chave", "motivo": "X-API-Key ausente ou incorreta."}


def test_outra_origem_pode_chamar_e_o_json_e_utf8(c, ambiente):
    origem = {"Origin": "http://192.168.0.90:8000"}
    preflight = c.options(f"{API}/processar/conferir", headers={
        **origem, "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "x-api-key, content-type",
    })
    leitura = c.get(f"{API}/pedidos", headers={**origem, **CABECALHO})
    assert preflight.status_code == 200
    assert leitura.status_code == 200
    assert leitura.headers["content-type"] == "application/json; charset=utf-8"
    assert "access-control-allow-credentials" not in leitura.headers


# ---------------------------------------------------------------------------
# 2. The list — same rows, same page, same labels as the screen
# ---------------------------------------------------------------------------
def test_lista_novos_traz_a_pagina_o_kpi_e_o_botao_da_tela(c, ambiente):
    corpo = c.get(f"{API}/pedidos", headers=CABECALHO).json()

    assert corpo["ok"] is True and corpo["modo"] == "novos"
    assert corpo["kpi"] == {"valor": 40, "rotulo": "Novos"}
    assert (corpo["total"], corpo["pagina"], corpo["paginas"], corpo["por_pagina"]) == (40, 1, 3, 15)
    assert (corpo["primeiro"], corpo["ultimo"]) == (1, 15)
    assert len(corpo["pedidos"]) == 15
    assert corpo["pedidos"][0] == {
        "pedido": 84201, "oportunidade": 4301, "wbc": "00120001", "cliente_codigo": "C0001",
        "cliente_nome": "Cliente 1", "cliente": "C0001 — Cliente 1", "total": 1235.5,
        "criado": "2026-09-02",
    }
    assert corpo["acao"] == {
        "tipo": "processar", "verbo": "Processar", "botao": "Processar selecionados…",
        "conferir": f"{API}/processar/conferir", "aviso": None,
    }
    assert corpo["vazio"] is None and corpo["execucao_em_andamento"] is None


def test_lista_integrados_traz_o_aviso_do_reprocessar(c, ambiente):
    corpo = c.get(f"{API}/pedidos?modo=integrados", headers=CABECALHO).json()
    assert corpo["kpi"]["rotulo"] == "Integrados"
    assert corpo["acao"]["botao"] == "Reprocessar selecionados…"
    assert corpo["acao"]["conferir"] == f"{API}/reprocessar/conferir"
    assert "não recria" in corpo["acao"]["aviso"]


def test_mesma_pagina_na_tela_e_na_api(c, ambiente):
    api = c.get(f"{API}/pedidos?pagina=2", headers=CABECALHO).json()
    tela = c.get("/pedidos-wbc?buscar=1&pagina=2").text

    valores_na_tela = re.findall(r'name="opp_ids" value="(\d+)"', tela)
    assert [str(p["oportunidade"]) for p in api["pedidos"]] == valores_na_tela
    assert f"{api['primeiro']}–{api['ultimo']} de {api['total']} pedido(s)" in _texto(tela)


def test_pagina_alem_do_fim_vira_a_ultima_como_na_tela(c, ambiente):
    corpo = c.get(f"{API}/pedidos?pagina=99", headers=CABECALHO).json()
    assert (corpo["pagina"], corpo["primeiro"], corpo["ultimo"]) == (3, 31, 40)


def test_lista_vazia_traz_o_texto_da_tela(c, ambiente):
    ambiente.clear()
    corpo = c.get(f"{API}/pedidos", headers=CABECALHO).json()
    assert corpo["total"] == 0 and corpo["pedidos"] == []
    assert corpo["vazio"] == "Nenhum pedido neste modo."
    assert (corpo["pagina"], corpo["paginas"], corpo["primeiro"], corpo["ultimo"]) == (1, 1, 0, 0)


@pytest.mark.parametrize("consulta,trecho", [
    ("modo=todos", "'modo'"), ("pagina=0", "'pagina'"), ("pagina=dois", "'pagina'"),
])
def test_parametros_invalidos_sao_400(c, ambiente, consulta, trecho):
    resposta = c.get(f"{API}/pedidos?{consulta}", headers=CABECALHO)
    assert resposta.status_code == 400
    assert resposta.json()["tipo"] == "invalido" and trecho in resposta.json()["motivo"]


def test_hana_fora_e_502_sem_vazar_detalhe(c, ambiente):
    with patch(f"{SVC}.buscar_pedidos_para_integrar", AsyncMock(side_effect=RuntimeError("senha=xyz"))):
        resposta = c.get(f"{API}/pedidos", headers=CABECALHO)
    assert resposta.status_code == 502
    assert resposta.json()["tipo"] == "sap_indisponivel"
    assert "xyz" not in resposta.text


# ---------------------------------------------------------------------------
# 3. The checked plan — same as the confirmation screen, never writes
# ---------------------------------------------------------------------------
def test_conferir_devolve_o_plano_da_tela(c, ambiente):
    with patch(f"{SVC}.processar_pedidos_novos") as servico:
        corpo = _conferir(c, [4301, "4302"]).json()
    servico.assert_not_called()

    plano = corpo["plano"]
    assert plano["tipo"] == "processar" and plano["operacao"] == "Processar pedidos novos"
    assert plano["resumo"] == {"pedidos": 2}
    assert plano["resumo_tela"] == [{"valor": 2, "rotulo": "pedido(s) selecionado(s)"}]
    assert plano["aviso"] == {"destaque": "Cria Ordens de Produção", "texto": acoes.AVISO_PROCESSAR}
    assert plano["executar"] == f"{API}/processar/executar"
    assert plano["itens"][0] == {
        "pedido": 84201, "oportunidade": 4301, "wbc": "00120001", "cliente_codigo": "C0001",
        "cliente_nome": "Cliente 1", "cliente": "C0001 — Cliente 1", "total": 1235.5,
        "total_texto": "1.235,50",
    }
    assert datetime.fromisoformat(plano["valido_ate"]) > datetime.now()


def test_conferir_reprocessar_traz_o_aviso_completo(c, ambiente):
    plano = _conferir(c, [4301], "reprocessar").json()["plano"]
    assert plano["tipo"] == "reprocessar"
    assert plano["aviso"] == {"destaque": "", "texto": acoes.AVISO_REPROCESSAR}
    assert "NÃO recria as OPs" in plano["aviso"]["texto"]


def test_plano_da_api_e_o_mesmo_da_tela(c, ambiente):
    """The screen's confirmation table and the API's items, row by row."""
    tela = _texto(c.post("/pedidos-wbc/processar/conferir", data={"opp_ids": ["4301"]}).text)
    item = _conferir(c, [4301]).json()["plano"]["itens"][0]
    for valor in (item["pedido"], item["oportunidade"], item["wbc"], item["cliente"], item["total_texto"]):
        assert str(valor) in tela
    assert acoes.AVISO_PROCESSAR.strip(", ") in tela


@pytest.mark.parametrize("corpo", [{}, {"oportunidades": []}, {"oportunidades": None}])
def test_conferir_sem_selecao_e_400_com_o_texto_da_tela(c, ambiente, corpo):
    resposta = c.post(f"{API}/processar/conferir", json=corpo, headers=CABECALHO)
    tela = c.post("/pedidos-wbc/processar/conferir", data={})
    assert resposta.status_code == 400
    assert resposta.json()["motivo"] == "Nenhum pedido selecionado."
    assert "Nenhum pedido selecionado." in _texto(tela.text)


@pytest.mark.parametrize("valor", [["43a"], "4301", [True], [{"x": 1}]])
def test_conferir_com_lista_invalida_e_400(c, ambiente, valor):
    resposta = c.post(f"{API}/processar/conferir", json={"oportunidades": valor}, headers=CABECALHO)
    assert resposta.status_code == 400 and resposta.json()["tipo"] == "invalido"


def test_pedido_fora_da_lista_e_409_com_o_texto_da_tela(c, ambiente):
    resposta = _conferir(c, [4301, 9999])
    tela = _texto(c.post("/pedidos-wbc/processar/conferir", data={"opp_ids": ["4301", "9999"]}).text)

    assert resposta.status_code == 409
    corpo = resposta.json()
    assert corpo["tipo"] == "fora_da_lista" and corpo["fora"] == ["9999"]
    assert corpo["motivo"] in tela


@pytest.mark.parametrize("chave", ["force", "forcar", "forçar"])
def test_forcar_nao_existe_na_api(c, ambiente, chave):
    resposta = _conferir(c, [4301], **{chave: True})
    assert resposta.status_code == 400
    assert "não existe nesta API" in resposta.json()["motivo"]


# ---------------------------------------------------------------------------
# 4. Running — token, requester, gate, lock
# ---------------------------------------------------------------------------
def test_executar_roda_pelo_orcamento_sem_forcar_e_aparece_nas_execucoes(c, ambiente, execucao_falsa):
    recebido = {}

    async def _falso(sl, wbc, hana, ids, force=False):
        recebido["ids"], recebido["force"] = list(ids), force
        return {"processados": list(ids), "com_erro": []}

    token = _token(c, (4301,))
    with patch(f"{SVC}.processar_pedidos_novos", _falso):
        resposta = c.post(f"{API}/processar/executar", json={"token": token, "solicitante": "Ana"},
                          headers=CABECALHO)
        assert resposta.status_code == 202, resposta.text
        execucao = resposta.json()["execucao"]
        estado = _espera(c, execucao["id"])

    assert recebido == {"ids": ["00120001"], "force": False}
    assert execucao["estado"] == f"{API}/execucoes/{execucao['id']}"
    assert (estado["origem"], estado["solicitante"]) == ("api", "Ana")
    assert estado["parada_combinada"] is True
    assert estado["situacao"] == "concluída" and estado["desfecho"] == "ok"
    assert "Pedido 84201 (WBC 00120001): concluído." in "\n".join(estado["linhas"])
    assert estado["resultado"]["processados"] == [{"doc_num": "84201", "orc_num": "00120001"}]

    de_novo = c.post(f"{API}/processar/executar", json={"token": token, "solicitante": "Ana"},
                     headers=CABECALHO)
    assert (de_novo.status_code, de_novo.json()["tipo"]) == (409, "confirmacao_invalida")


def test_reprocessar_pela_api(c, ambiente, execucao_falsa):
    chamado = AsyncMock(return_value={"atualizados": ["00120001"], "com_erro": []})
    token = _token(c, (4301,), "reprocessar")
    with patch(f"{SVC}.reprocessar_pedidos_integrados", chamado):
        resposta = c.post(f"{API}/reprocessar/executar", json={"token": token, "solicitante": "Ana"},
                          headers=CABECALHO)
        estado = _espera(c, resposta.json()["execucao"]["id"])
    assert chamado.await_args.args[3] == ["00120001"]
    assert "processe de novo" in "\n".join(estado["linhas"])


def test_erro_do_servico_termina_com_falhas(c, ambiente, execucao_falsa):
    falha = AsyncMock(return_value={"processados": [], "com_erro": [
        {"orc_num": "00120001", "motivo": "sem pedido vinculado"}]})
    token = _token(c, (4301,))
    with patch(f"{SVC}.processar_pedidos_novos", falha):
        resposta = c.post(f"{API}/processar/executar", json={"token": token, "solicitante": "Ana"},
                          headers=CABECALHO)
        estado = _espera(c, resposta.json()["execucao"]["id"])
    assert (estado["situacao"], estado["desfecho"], estado["com_falhas"]) == ("concluída", "falhas", True)
    # Marked ⚠ (painted red on both screens) — it used to reach the log unmarked.
    assert any("⚠" in linha and "ERRO — sem pedido vinculado" in linha for linha in estado["linhas"])


def test_peso_diferente_vem_no_resultado_com_a_causa(c, ambiente, execucao_falsa):
    """Pedido 84453 (01/10/2026): the API returns WHO changed the line in the SAP, as fields."""
    causa = {
        "tipo": "quantidade_mudada_no_sap", "usuario": "Adriano Fonseca", "momento": "2026-10-01T14:00:06",
        "quantidade_antes": 2.0, "quantidade_depois": 1.0, "peso_antes": 176.9, "peso_depois": 88.45,
        "integracao_gravou_certo": True, "texto": "CAUSA: Adriano Fonseca mudou a quantidade…",
    }
    servico = AsyncMock(return_value={"processados": ["00120001"], "com_erro": [], "sem_op": [], "sem_rateio": [],
                                      "pesos_diferentes": [{"orc_num": "00120001", "linha": 0, "item": "I000003",
                                                            "quantidade": 1.0, "peso_sap": 88.45,
                                                            "peso_esperado": 176.9, "arvore_wbc": 160.82,
                                                            "causa": causa}]})
    token = _token(c, (4301,))
    with patch(f"{SVC}.processar_pedidos_novos", servico):
        resposta = c.post(f"{API}/processar/executar", json={"token": token, "solicitante": "Ana"},
                          headers=CABECALHO)
        estado = _espera(c, resposta.json()["execucao"]["id"])

    item = estado["resultado"]["pesos_diferentes"][0]
    assert item["doc_num"] == "84201" and item["causa"]["usuario"] == "Adriano Fonseca"
    assert estado["passo"] == ("Pedido 84201 (WBC 00120001): ATENÇÃO — 1 linha(s) com peso diferente "
                               "da árvore do WBC (veja a CAUSA acima)")
    assert estado["desfecho"] == "ok"       # a weight is a warning, not a failed pedido


def test_token_de_processar_nao_executa_reprocessar_e_continua_valendo(c, ambiente, execucao_falsa):
    token = _token(c, (4301,))
    errado = c.post(f"{API}/reprocessar/executar", json={"token": token, "solicitante": "Ana"},
                    headers=CABECALHO)
    assert (errado.status_code, errado.json()["tipo"]) == (409, "confirmacao_invalida")
    assert PLANOS.obter(token) is not None


@pytest.mark.parametrize("corpo", [
    {"token": "x"}, {"solicitante": "Ana"}, {"token": "x", "solicitante": "  "},
    {"token": "x", "solicitante": "a\nb"}, {"token": "x", "solicitante": "a" * 81},
])
def test_executar_sem_token_ou_solicitante_e_400_e_nao_gasta_o_token(c, ambiente, corpo):
    token = _token(c, (4301,))
    if corpo.get("token") == "x":
        corpo = {**corpo, "token": token}
    resposta = c.post(f"{API}/processar/executar", json=corpo, headers=CABECALHO)
    assert resposta.status_code == 400
    assert PLANOS.obter(token) is not None


def test_executar_com_forcar_e_recusado(c, ambiente):
    token = _token(c, (4301,))
    resposta = c.post(f"{API}/processar/executar",
                      json={"token": token, "solicitante": "Ana", "force": True}, headers=CABECALHO)
    assert resposta.status_code == 400
    assert PLANOS.obter(token) is not None


def test_escrita_recusada_nao_gasta_o_token(c, lista):
    with patch("controleproducao.modules.pedidos_wbc.api_router.HanaDirectReader", MagicMock()), \
         patch("controleproducao.core.web.get_settings", return_value=_settings()):
        token = _token(c, (4301,))
    with patch("controleproducao.core.web.get_settings", return_value=_settings(producao=True)), \
         patch(f"{SVC}.processar_pedidos_novos") as servico:
        resposta = c.post(f"{API}/processar/executar", json={"token": token, "solicitante": "Ana"},
                          headers=CABECALHO)
    assert resposta.status_code == 503
    assert resposta.json()["tipo"] == "escrita_desabilitada"
    servico.assert_not_called()
    assert PLANOS.obter(token) is not None


def test_modulo_ocupado_nao_gasta_o_token_e_aponta_a_execucao(c, ambiente, execucao_falsa):
    trava = _Trava()
    primeiro, segundo = _token(c, (4301,)), _token(c, (4302,))
    with patch(f"{SVC}.processar_pedidos_novos", trava):
        rodando = c.post(f"{API}/processar/executar", json={"token": primeiro, "solicitante": "Ana"},
                         headers=CABECALHO).json()["execucao"]
        recusa = c.post(f"{API}/processar/executar", json={"token": segundo, "solicitante": "Bia"},
                        headers=CABECALHO)
        lista = c.get(f"{API}/pedidos", headers=CABECALHO).json()
        trava.solta()
        _espera(c, rodando["id"])

    assert recusa.status_code == 409
    corpo = recusa.json()
    assert corpo["tipo"] == "ocupado"
    assert corpo["execucao_em_andamento"]["id"] == rodando["id"]
    assert corpo["execucao_em_andamento"]["solicitante"] == "Ana"
    assert lista["execucao_em_andamento"]["id"] == rodando["id"]
    assert PLANOS.obter(segundo) is not None


def test_tela_e_api_dividem_a_trava_do_modulo(c, ambiente, execucao_falsa):
    trava = _Trava()
    token_api = _token(c, (4301,))
    conferencia = c.post("/pedidos-wbc/processar/conferir", data={"opp_ids": ["4302"]}).text
    token_tela = re.search(r'name="token" value="([^"]+)"', conferencia).group(1)
    with patch(f"{SVC}.processar_pedidos_novos", trava):
        rodando = c.post(f"{API}/processar/executar", json={"token": token_api, "solicitante": "Ana"},
                         headers=CABECALHO).json()["execucao"]
        tela = c.post("/pedidos-wbc/processar/executar", data={"token": token_tela}, follow_redirects=False)
        trava.solta()
        _espera(c, rodando["id"])

    assert tela.status_code == 400
    assert f'href="/tarefas/{rodando["id"]}"' in tela.text


# ---------------------------------------------------------------------------
# 5. The guide and the reference page cannot drift from the routes
# ---------------------------------------------------------------------------
def test_guia_e_pagina_pronta_so_citam_rotas_que_existem():
    """API_PEDIDOS_WBC.md and docs/exemplos/pedidos_wbc_clone.html are what the other team
    copies: a route renamed here and not there would break their clone in silence."""
    from pathlib import Path

    from controleproducao.modules.pedidos_wbc import api_router

    raiz = Path(__file__).resolve().parents[2]
    padroes = [re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", r.path) + "$") for r in api_router.router.routes]
    for arquivo in ("API_PEDIDOS_WBC.md", "docs/exemplos/pedidos_wbc_clone.html"):
        texto = (raiz / arquivo).read_text(encoding="utf-8")
        citadas = {re.sub(r"\?.*$", "", c).rstrip(".,:;`)")
                   for c in re.findall(r"/api/pedidos-wbc/[\w\-/{}$]+(?:\?[\w=&]*)?", texto)}
        citadas = {re.sub(r"\$\{[^}]*\}|\{id\}", "x", c) for c in citadas}
        assert citadas, arquivo
        sobrando = sorted(c for c in citadas if not any(p.match(c) for p in padroes))
        assert not sobrando, f"{arquivo} cita rota que não existe: {sobrando}"
    pagina = (raiz / "docs/exemplos/pedidos_wbc_clone.html").read_text(encoding="utf-8")
    assert 'const API_BASE = "http://192.168.7.11:8080";' in pagina
    assert "X-API-Key" in pagina and "sessionStorage" in pagina
    assert not re.search(r"\.innerHTML\s*[+]?=", pagina), "texto da API vai por textContent"


# ---------------------------------------------------------------------------
# 6. Following and interrupting
# ---------------------------------------------------------------------------
def _terminada_de(modulo: str) -> Tarefa:
    tarefa = Tarefa(id=f"t-{modulo}", nome="n", descricao="d", criada_em=datetime.now() - timedelta(minutes=1),
                    modulo=modulo, situacao="concluída")
    TAREFAS._tarefas[tarefa.id] = tarefa
    TAREFAS._ordem.append(tarefa.id)
    return tarefa


def test_estado_so_enxerga_execucoes_deste_modulo(c, ambiente):
    _terminada_de("manutencao_op")
    deste = _terminada_de("pedidos_wbc")
    outro = c.get(f"{API}/execucoes/t-manutencao_op", headers=CABECALHO)
    proprio = c.get(f"{API}/execucoes/{deste.id}", headers=CABECALHO)
    assert (outro.status_code, outro.json()["tipo"]) == (404, "nao_encontrada")
    assert proprio.status_code == 200 and proprio.json()["execucao"]["situacao"] == "concluída"


def test_interromper_termina_o_pedido_em_curso_e_nao_comeca_o_proximo(c, ambiente, execucao_falsa, caplog):
    trava = _Trava()
    token = _token(c, (4301, 4302, 4303))
    with patch(f"{SVC}.processar_pedidos_novos", trava):
        execucao = c.post(f"{API}/processar/executar", json={"token": token, "solicitante": "Ana"},
                          headers=CABECALHO).json()["execucao"]
        while not trava.recebidos:
            time.sleep(0.005)
        sem_nome = c.post(f"{API}/execucoes/{execucao['id']}/cancelar", json={}, headers=CABECALHO)
        pedido = c.post(f"{API}/execucoes/{execucao['id']}/cancelar", json={"solicitante": "Bia"},
                        headers=CABECALHO)
        trava.solta()
        estado = _espera(c, execucao["id"])

    assert sem_nome.status_code == 400
    assert pedido.json() == {"ok": True, "cancelada": True, "entre_etapas": True}
    assert [ids for ids, _ in trava.recebidos] == [["00120001"]]
    assert estado["situacao"] == "cancelada"
    assert [p["doc_num"] for p in estado["resultado"]["nao_iniciados"]] == ["84202", "84203"]
    assert "Bia" in caplog.text
