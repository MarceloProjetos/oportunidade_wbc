"""F3/F4 of docs/PLANO_MIRA_AGENTE_11.md: the agent asks, a person approves, the .11 runs.

What these tests hold:
1. A request grants nothing; it is decided ONCE (two approvals never both run), and expires.
2. The agent can never hold ``aprovar`` (it would approve itself) — rule 1.
3. Who approves: the role the action asks (processar = PCP/admin, reiniciar = admin).
4. The approved run uses exactly what was shown; a changed plan is refused.
5. Restart: the CP busy and a stopped worker are refused; the API restarts itself detached.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

import pytest

from operacao import acoes_agente, reinicio
from seguranca import aprovacoes, credenciais

AGORA = datetime(2026, 10, 2, 14, 0)


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------
def _novo(**extra):
    return aprovacoes.criar("sincronizar_os", {"nped": 84080}, {"texto": "x"}, pedido_por="mira-agente",
                            agora=AGORA, **extra)


def test_pedido_nasce_pendente_com_codigo_de_4_digitos():
    r = _novo(em_nome_de="Ana", motivo="cliente ligou\nlinha 2")
    assert r["estado"] == "pendente" and r["codigo"].isdigit() and len(r["codigo"]) == 4
    assert r["expira_em"] == (AGORA + timedelta(minutes=aprovacoes.VALIDADE_MIN)).isoformat()
    assert r["motivo"] == "cliente ligou linha 2"            # no line breaks forged into a card
    assert aprovacoes.obter(r["codigo"], agora=AGORA)["id"] == r["id"]


def test_decide_uma_vez_so():
    r = _novo()
    aprovado = aprovacoes.decidir(r["codigo"], aprovar=True, pessoa="Marcelo", canal="tela", agora=AGORA)
    assert aprovado["estado"] == "executando" and aprovado["decidido_por"] == "Marcelo"
    with pytest.raises(aprovacoes.AprovacaoInvalida) as erro:
        aprovacoes.decidir(r["id"], aprovar=True, pessoa="Outro", canal="whatsapp", agora=AGORA)
    assert erro.value.tipo == "ja_decidido"
    assert aprovacoes.concluir(r["id"], ok=True, resultado={"http": 200})["estado"] == "executado"


def test_codigo_so_vale_enquanto_pendente_e_expira():
    r = _novo()
    depois = AGORA + timedelta(minutes=aprovacoes.VALIDADE_MIN, seconds=1)
    with pytest.raises(aprovacoes.AprovacaoInvalida) as erro:
        aprovacoes.decidir(r["id"], aprovar=True, pessoa="M", canal="tela", agora=depois)
    assert erro.value.tipo == "expirado"
    assert aprovacoes.obter(r["codigo"], agora=depois) is None      # the code is free again
    assert aprovacoes.listar("expirado", agora=depois)[0]["id"] == r["id"]


def test_recusa_guarda_o_motivo():
    r = _novo()
    recusado = aprovacoes.decidir(r["id"], aprovar=False, pessoa="M", canal="tela", motivo="agora não",
                                  agora=AGORA)
    assert recusado["estado"] == "recusado" and recusado["motivo_recusa"] == "agora não"


def test_teto_por_hora_conta_pedidos_recentes():
    for _ in range(3):
        _novo()
    assert aprovacoes.contar_recentes("sincronizar_os", agora=AGORA + timedelta(minutes=10)) == 3
    assert aprovacoes.contar_recentes("sincronizar_os", agora=AGORA + timedelta(minutes=61)) == 0


def test_agente_nunca_recebe_aprovar():
    with pytest.raises(credenciais.CredencialInvalida, match="aprovar"):
        credenciais.criar("mira-agente", ["leitura", "aprovar"], agente=True)
    credenciais.criar("mira-agente", ["leitura"], agente=True)
    with pytest.raises(credenciais.CredencialInvalida, match="aprovar"):
        credenciais.acrescentar_escopos("mira-agente", ["aprovar"])


def test_acrescentar_liga_declara_usuario_sem_trocar_chave():
    chave = credenciais.criar("mcp-servico", ["leitura"])
    credenciais.acrescentar_escopos("mcp-servico", ["pedidos_wbc"], declara_usuario=True)
    cliente = credenciais.identificar(chave)
    assert cliente.declara_usuario and cliente.pode("pedidos_wbc")


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------
def test_catalogo_fechado_e_papeis():
    assert set(acoes_agente.CATALOGO) == {"sincronizar_os", "forcar_carga", "processar_pedido", "reiniciar_servico"}
    pedido = acoes_agente.CATALOGO["processar_pedido"]
    assert acoes_agente.pode_aprovar(pedido, "pcp") and acoes_agente.pode_aprovar(pedido, "admin")
    assert not acoes_agente.pode_aprovar(pedido, None) and not acoes_agente.pode_aprovar(pedido, "vendas")
    assert not acoes_agente.pode_aprovar(acoes_agente.CATALOGO["reiniciar_servico"], "pcp")
    assert acoes_agente.pode_aprovar(acoes_agente.CATALOGO["sincronizar_os"], None)
    with pytest.raises(acoes_agente.AcaoInvalida):
        acoes_agente.CATALOGO["sincronizar_os"].validar({"nped": "84080; DROP"})


class _CP:
    """Stand-in for the loopback calls: the CP's list and plan."""

    def __init__(self, total=1000.0):
        self.total = total
        self.chamadas = []

    def __call__(self, base, metodo, caminho, *, corpo=None, ctx=None, tempo=60):
        self.chamadas.append((metodo, caminho, corpo, ctx))
        if caminho.startswith("/api/pedidos-wbc/pedidos"):
            return 200, {"pedidos": [{"pedido": 84453, "oportunidade": 9001, "wbc": "00125348",
                                      "cliente": "C1 — Cliente"}], "paginas": 1}
        if caminho.endswith("/conferir"):
            return 200, {"ok": True, "plano": {"token": "tok", "aviso": {"texto": "Cria OPs."},
                                               "itens": [{"pedido": 84453, "oportunidade": "9001",
                                                          "total": self.total}]}}
        if caminho.endswith("/executar"):
            return 202, {"ok": True, "execucao": {"id": "t1"}}
        return 404, {}


def test_processar_previa_e_execucao_com_o_mesmo_plano(monkeypatch):
    cp = _CP()
    monkeypatch.setattr(acoes_agente, "_chamar", cp)
    acao = acoes_agente.CATALOGO["processar_pedido"]
    previa = acao.previa(acao.validar({"pedido": "84453"}))
    assert previa["oportunidade"] == "9001" and "IRREVERSÍVEL" in previa["texto"]
    ok, resultado = acao.executar({"pedido": 84453}, previa, acoes_agente.Contexto("Marcelo", "4821", "mira-agente"))
    assert ok and resultado["execucao"]["id"] == "t1"
    metodo, caminho, corpo, ctx = cp.chamadas[-1]
    assert caminho.endswith("/processar/executar") and corpo["token"] == "tok"
    assert corpo["solicitante"] == "Marcelo (aprovou 4821, pedido de mira-agente)" and ctx.pessoa == "Marcelo"


def test_processar_recusa_plano_que_mudou(monkeypatch):
    monkeypatch.setattr(acoes_agente, "_chamar", _CP())
    acao = acoes_agente.CATALOGO["processar_pedido"]
    previa = acao.previa({"pedido": 84453})
    monkeypatch.setattr(acoes_agente, "_chamar", _CP(total=2000.0))
    ok, resultado = acao.executar({"pedido": 84453}, previa, acoes_agente.Contexto("M", "1", "a"))
    assert not ok and "plano mudou" in resultado["motivo"]


def test_processar_pedido_fora_da_lista(monkeypatch):
    monkeypatch.setattr(acoes_agente, "_chamar", _CP())
    with pytest.raises(acoes_agente.AcaoInvalida, match="não está em 'Pedidos novos'"):
        acoes_agente.CATALOGO["processar_pedido"].previa({"pedido": 1})


# ---------------------------------------------------------------------------
# Restart
# ---------------------------------------------------------------------------
@pytest.fixture
def nssm(monkeypatch):
    rodados = []
    monkeypatch.setattr(reinicio, "_nssm", lambda: "nssm")
    monkeypatch.setattr(reinicio, "_rodar", lambda args, tempo: rodados.append(args))
    monkeypatch.setattr(reinicio.time, "sleep", lambda s: None)
    return rodados


def test_reinicio_so_dos_6():
    with pytest.raises(reinicio.ReinicioRecusado, match="fora da lista"):
        reinicio.validar("Spooler")
    assert reinicio.validar("orcaview-mcp") == "OrcaView-MCP"


def test_cp_ocupado_nao_reinicia(nssm, monkeypatch):
    monkeypatch.setattr(reinicio, "_cp_ocupado", lambda: "1")
    monkeypatch.setattr(reinicio, "_estado", lambda nome: "running")
    with pytest.raises(reinicio.ReinicioRecusado, match="execução em andamento"):
        reinicio.reiniciar(reinicio.CP)
    assert nssm == []


def test_worker_parado_nao_e_ligado_e_rodando_para_por_arquivo(nssm, monkeypatch, tmp_path):
    monkeypatch.setattr(reinicio, "PARADA_DO_WORKER", tmp_path / "wbc_worker.stop")
    monkeypatch.setattr(reinicio, "_estado", lambda nome: "stopped")
    with pytest.raises(reinicio.ReinicioRecusado, match="parado de propósito"):
        reinicio.reiniciar(reinicio.WORKER)
    estados = iter(["running", "stopped", "running"])
    monkeypatch.setattr(reinicio, "_estado", lambda nome: next(estados, "running"))
    assert reinicio.reiniciar(reinicio.WORKER)["ok"] is True
    assert (tmp_path / "wbc_worker.stop").exists()
    assert nssm == [["nssm", "stop", reinicio.WORKER], ["nssm", "start", reinicio.WORKER]]


def test_api_se_reinicia_por_processo_destacado(nssm, monkeypatch):
    disparos = []
    monkeypatch.setattr(reinicio, "_reiniciar_a_propria_api", lambda caminho: disparos.append(caminho))
    r = reinicio.reiniciar("OrcaView-OS-API")
    assert r["disparado"] and disparos == ["nssm"] and nssm == []


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------
@pytest.fixture
def api(monkeypatch):
    pytest.importorskip("flask")
    import api as apimod
    from config import reset_settings

    monkeypatch.setenv("OS_API_KEY", "mestra-de-teste")
    reset_settings()
    apimod._rate_limiter.reset()
    apimod.app.config.update(TESTING=True)
    executados = []
    falsa = acoes_agente.Acao(
        "sincronizar_os", "Sincronizar as OS de um pedido", "os:sincronizar", None, 2,
        acoes_agente._validar_os, lambda p: {"texto": f"sync {p['nped']}"},
        lambda p, previa, ctx: (executados.append((p, ctx.pessoa)) or True, {"http": 200}))
    monkeypatch.setitem(acoes_agente.CATALOGO, "sincronizar_os", falsa)
    return apimod, apimod.app.test_client(), executados


def _esperar_fim(chave):
    for _ in range(100):
        r = aprovacoes.obter(chave)
        if r["estado"] not in ("pendente", "executando"):
            return r
        time.sleep(0.02)
    return aprovacoes.obter(chave)


def test_fluxo_pedir_aprovar_executar(api):
    _, c, executados = api
    agente_ = credenciais.criar("mcp-servico", ["leitura", "os:sincronizar"], declara_usuario=True)
    r = c.post("/aprovacoes", json={"acao": "sincronizar_os", "parametros": {"nped": 84080}, "motivo": "teste"},
               headers={"X-API-Key": agente_, "X-SIS-Pedido-Por": "mira-agente", "X-SIS-Usuario": "Ana"})
    assert r.status_code == 201
    pedido = r.get_json()["aprovacao"]
    assert pedido["pedido_por"] == "mira-agente" and pedido["em_nome_de"] == "Ana" and executados == []
    assert "aprovar " + pedido["codigo"] in r.get_json()["como_aprovar"]

    # the requester cannot approve (no 'aprovar'), a person on the screen can
    assert c.post(f"/aprovacoes/{pedido['codigo']}/aprovar", headers={"X-API-Key": agente_},
                  json={"pessoa": "Ana"}).status_code == 403
    r = c.post(f"/aprovacoes/{pedido['codigo']}/aprovar", headers={"X-API-Key": "mestra-de-teste"},
               json={"pessoa": "Marcelo"})
    assert r.status_code == 202
    fim = _esperar_fim(pedido["id"])
    assert fim["estado"] == "executado" and fim["decidido_por"] == "Marcelo"
    assert executados == [({"nped": 84080}, "Marcelo")]
    assert c.post(f"/aprovacoes/{pedido['id']}/aprovar", headers={"X-API-Key": "mestra-de-teste"},
                  json={"pessoa": "Marcelo"}).status_code == 409


def test_pedir_exige_o_escopo_da_acao_e_respeita_o_teto(api):
    _, c, _ = api
    leitor = credenciais.criar("leitor", ["leitura"])
    r = c.post("/aprovacoes", json={"acao": "sincronizar_os", "parametros": {"nped": 1}},
               headers={"X-API-Key": leitor})
    assert r.status_code == 403
    assert c.post("/aprovacoes", json={"acao": "rodar_comando"},
                  headers={"X-API-Key": "mestra-de-teste"}).status_code == 400
    for _ in range(2):
        assert c.post("/aprovacoes", json={"acao": "sincronizar_os", "parametros": {"nped": 1}},
                      headers={"X-API-Key": "mestra-de-teste"}).status_code == 201
    assert c.post("/aprovacoes", json={"acao": "sincronizar_os", "parametros": {"nped": 1}},
                  headers={"X-API-Key": "mestra-de-teste"}).status_code == 429


def test_aprovar_exige_pessoa_e_papel(api, monkeypatch):
    _, c, _ = api
    mira = credenciais.criar("orcaview-90", ["leitura", "aprovar", "servico:reiniciar"], declara_usuario=True)
    monkeypatch.setattr(reinicio, "previa", lambda nome: {"texto": "reiniciar"})
    r = c.post("/aprovacoes", json={"acao": "reiniciar_servico", "parametros": {"servico": "OrcaView-MCP"}},
               headers={"X-API-Key": mira})
    codigo = r.get_json()["aprovacao"]["codigo"]
    assert c.post(f"/aprovacoes/{codigo}/aprovar", headers={"X-API-Key": mira}).status_code == 400   # nobody
    r = c.post(f"/aprovacoes/{codigo}/aprovar", headers={"X-API-Key": mira, "X-SIS-Usuario": "Ana",
                                                          "X-SIS-Papel": "pcp"}, json={"canal": "whatsapp"})
    assert r.status_code == 403 and "admin" in r.get_json()["motivo"]
    r = c.post(f"/aprovacoes/{codigo}/recusar", headers={"X-API-Key": mira, "X-SIS-Usuario": "Ana"},
               json={"canal": "whatsapp", "motivo": "não agora"})
    assert r.status_code == 200
    assert r.get_json()["aprovacao"]["decidido_canal"] == "whatsapp"
    assert r.get_json()["aprovacao"]["estado"] == "recusado"


def test_tela_decide_so_da_mesma_origem(api):
    apimod, c, _ = api
    r = aprovacoes.criar("sincronizar_os", {"nped": 1}, {"texto": "x"}, pedido_por="mira-agente")
    from casa import acesso as casa_acesso
    c.set_cookie(casa_acesso.COOKIE_DE_ACESSO, casa_acesso.token_da_chave("mestra-de-teste"))
    assert c.post(f"/aprovacoes/{r['codigo']}/recusar", json={"pessoa": "Marcelo"}).status_code == 401
    resposta = c.post(f"/aprovacoes/{r['codigo']}/recusar", json={"pessoa": "Marcelo"},
                      headers={"Origin": "http://localhost"})
    assert resposta.status_code == 200 and resposta.get_json()["aprovacao"]["decidido_canal"] == "tela"
