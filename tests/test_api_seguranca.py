"""Scopes, agent rules and audit on the API 8077 (F1 of docs/PLANO_MIRA_AGENTE_11.md, 02/10/2026).

What these tests hold:
1. The master key and the screens keep passing everywhere — no current client breaks.
2. A registered key opens ONLY its scopes: a read-only client cannot sync, release OPs or
   delete history; RH needs its own scope.
3. Agent credentials obey the off switch and write only during business hours.
4. Every call with a credential (or refused) lands in the audit, without the key.
5. No guarded route exists without a declared scope.
"""
from __future__ import annotations

import pytest

pytest.importorskip("flask")

import api as apimod  # noqa: E402
from config import reset_settings  # noqa: E402
from seguranca import agente, auditoria, credenciais  # noqa: E402

MESTRA = "chave-mestra-de-teste"


@pytest.fixture
def c(monkeypatch):
    monkeypatch.setenv("OS_API_KEY", MESTRA)
    monkeypatch.delenv("STATUS_ID", raising=False)
    reset_settings()
    apimod._rate_limiter.reset()
    monkeypatch.setattr(apimod, "_fetch_log", lambda table, n: [])
    monkeypatch.setattr(apimod, "_clear_log", lambda table: 0)
    monkeypatch.setattr(apimod, "sync_os", lambda n: True)
    monkeypatch.setattr(apimod, "diagnosticar_nped", lambda n: {
        "tem_os": True, "cancelada": False, "pedido_existe": True, "pedido_cancelado": False,
        "pedido_status": "Aberto"})
    monkeypatch.setattr(apimod, "consultar_status_pedido", lambda n: None)
    monkeypatch.setattr(apimod, "_fetch_os_detalhe", lambda n: [])
    apimod.app.config.update(TESTING=True)
    return apimod.app.test_client()


def _h(chave: str, **extra) -> dict:
    return {"X-API-Key": chave, **extra}


def test_chave_mestra_continua_passando_em_tudo(c):
    assert c.get("/historico", headers=_h(MESTRA)).status_code == 200
    assert c.delete("/historico", headers=_h(MESTRA)).status_code == 200
    assert c.get(f"/historico?key={MESTRA}").status_code == 200      # query string, as before


def test_chave_de_leitura_le_mas_nao_grava_nem_apaga(c):
    leitura = credenciais.criar("orcaview-90", ["leitura"])
    assert c.get("/historico", headers=_h(leitura)).status_code == 200

    for resposta in (c.delete("/historico", headers=_h(leitura)),
                     c.post("/ordens-servico/84080/sincronizar", headers=_h(leitura)),
                     c.post("/oportunidades/sincronizar", headers=_h(leitura)),
                     c.post("/ordens-producao/1/status", json={"status": "boposReleased"}, headers=_h(leitura)),
                     c.get("/rh/colaboradores", headers=_h(leitura))):
        assert resposta.status_code == 403, resposta.request.path
        assert resposta.get_json()["tipo"] == "sem_permissao"
        assert "orcaview-90" in resposta.get_json()["motivo"]


def test_escopo_certo_abre_so_aquela_escrita(c):
    sync = credenciais.criar("sincroniza-os", ["os:sincronizar"])
    assert c.post("/ordens-servico/84080/sincronizar", headers=_h(sync)).status_code == 200
    assert c.get("/historico", headers=_h(sync)).status_code == 403          # no `leitura`


def test_chave_registrada_na_url_nao_vale(c):
    """A registered key in a URL ends up in logs and browser history: header only."""
    leitura = credenciais.criar("orcaview-90", ["leitura"])
    assert c.get(f"/historico?key={leitura}").status_code == 401


def test_chave_revogada_e_desconhecida_sao_401(c):
    leitura = credenciais.criar("orcaview-90", ["leitura"])
    credenciais.revogar("orcaview-90")
    assert c.get("/historico", headers=_h(leitura)).status_code == 401
    assert c.get("/historico", headers=_h("inventada")).status_code == 401


def test_status_completo_para_quem_tem_leitura(c, monkeypatch):
    monkeypatch.setattr(apimod, "collect_status", lambda *_a, **_k: {"ok": True, "checks": {}, "alerts": []})
    leitura = credenciais.criar("monitor", ["leitura"])
    assert "restrito" not in c.get("/status", headers=_h(leitura)).get_json()
    assert c.get("/status").get_json()["restrito"] is True


# ---------------------------------------------------------------------------
# Agent rules
# ---------------------------------------------------------------------------
def test_agente_desligado_nao_le_nem_grava(c):
    chave = credenciais.criar("mira-agente", ["leitura", "os:sincronizar"], agente=True)
    agente.desligar()
    resposta = c.get("/historico", headers=_h(chave))
    assert resposta.status_code == 403 and resposta.get_json()["tipo"] == "agente_bloqueado"
    assert c.get("/historico", headers=_h(MESTRA)).status_code == 200        # people unaffected


def test_agente_so_le_fora_do_expediente(c, monkeypatch):
    chave = credenciais.criar("mira-agente", ["leitura", "os:sincronizar"], agente=True)
    monkeypatch.setattr(agente, "no_expediente", lambda agora=None: False)
    assert c.get("/historico", headers=_h(chave)).status_code == 200
    grava = c.post("/ordens-servico/84080/sincronizar", headers=_h(chave))
    assert grava.status_code == 403 and "7h às 19h" in grava.get_json()["motivo"]


def test_agente_com_escritas_desligadas_ainda_le(c, monkeypatch):
    chave = credenciais.criar("mira-agente", ["leitura", "os:sincronizar"], agente=True)
    monkeypatch.setattr(agente, "no_expediente", lambda agora=None: True)
    agente.desligar(so_escrita=True)
    assert c.get("/historico", headers=_h(chave)).status_code == 200
    assert c.post("/ordens-servico/84080/sincronizar", headers=_h(chave)).status_code == 403


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------
def test_auditoria_registra_quem_o_que_e_o_resultado_sem_a_chave(c):
    chave = credenciais.criar("orcaview-90", ["leitura"], declara_usuario=True)
    c.get("/historico?limit=5", headers=_h(chave, **{"X-SIS-Usuario": "Ana Souza"}))
    c.delete("/historico", headers=_h(chave))
    c.get(f"/historico?key={MESTRA}")
    c.get("/historico", headers=_h("errada"))

    linhas = auditoria.ler("api")
    assert [(l["cliente"], l["metodo"], l["status"]) for l in linhas] == [
        ("orcaview-90", "GET", 200), ("orcaview-90", "DELETE", 403), ("chave-mestra", "GET", 200), (None, "GET", 401)]
    assert linhas[0]["usuario"] == "Ana Souza" and linhas[0]["escopo"] == "leitura"
    assert linhas[0]["rota"] == "/historico?limit=5"
    assert all(chave not in str(l) and MESTRA not in str(l) for l in linhas)
    assert linhas[2]["rota"] == "/historico?key=***"


def test_usuario_declarado_so_vale_de_quem_pode_declarar(c):
    chave = credenciais.criar("qualquer", ["leitura"])
    c.get("/historico", headers=_h(chave, **{"X-SIS-Usuario": "Diretor"}))
    assert auditoria.ler("api")[0]["usuario"] is None


def test_monitoramento_anonimo_nao_enche_a_auditoria(c, monkeypatch):
    monkeypatch.setattr(apimod, "collect_status", lambda *_a, **_k: {"ok": True, "checks": {}, "alerts": []})
    c.get("/health")
    c.get("/status")
    assert auditoria.ler("api") == []


# ---------------------------------------------------------------------------
# Coverage: every guarded route declares a scope
# ---------------------------------------------------------------------------
ROTAS_ABERTAS = {"/", "/health", "/status", "/favicon.ico", "/casa/<path:arquivo>", "/static/<path:filename>",
                 "/sincronizar", "/inicio", "/entrar", "/sair", "/orcaview", "/painel-wbc", "/controle-producao",
                 "/controle-producao/<tela>"}


def test_toda_rota_protegida_declara_um_escopo():
    sem_escopo = []
    for regra in apimod.app.url_map.iter_rules():
        if regra.rule in ROTAS_ABERTAS:
            continue
        funcao = apimod.app.view_functions[regra.endpoint]
        if getattr(funcao, "escopo", None) not in credenciais.ESCOPOS:
            sem_escopo.append(regra.rule)
    assert not sem_escopo, f"rotas sem @requer_chave(escopo): {sem_escopo}"
