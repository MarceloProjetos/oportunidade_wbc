"""F3/F4 of PLANO_MIRA_AGENTE_11.md (removed 2026-10-06): the agent asks, a person approves, the .11 runs.

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


def test_cabecalho_da_pessoa_vai_em_ascii(monkeypatch):
    """1st real approval (02/10): "Marcelo Miranda · aprovação 6634" broke httpx before the call."""
    enviados = []

    def request(metodo, url, json=None, headers=None, timeout=None, trust_env=None):
        enviados.append(headers)
        for valor in headers.values():
            valor.encode("ascii")
        return type("R", (), {"status_code": 200, "json": lambda self: {"ok": True}})()

    monkeypatch.setattr(acoes_agente.httpx, "request", request)
    ctx = acoes_agente.Contexto("João Conceição", "6634", "mcp-marcelo")
    ok, _ = acoes_agente.CATALOGO["sincronizar_os"].executar({"nped": 84455}, {}, ctx)
    assert ok and enviados[0]["X-SIS-Usuario"] == "Joao Conceicao - aprovacao 6634"


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
        lambda p, previa, ctx: (executados.append((p, ctx.pessoa)) or True, {"http": 200}),
        alvo=acoes_agente.CATALOGO["sincronizar_os"].alvo)
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
    # Distinct targets: a repeat of an OPEN request is the same request (F4), never a new one.
    for nped in (1, 2):
        assert c.post("/aprovacoes", json={"acao": "sincronizar_os", "parametros": {"nped": nped}},
                      headers={"X-API-Key": "mestra-de-teste"}).status_code == 201
    assert c.post("/aprovacoes", json={"acao": "sincronizar_os", "parametros": {"nped": 3}},
                  headers={"X-API-Key": "mestra-de-teste"}).status_code == 429
    # Past the cap, a repeat still gets its request back (a retry is never a 429).
    repetido = c.post("/aprovacoes", json={"acao": "sincronizar_os", "parametros": {"nped": 1}},
                      headers={"X-API-Key": "mestra-de-teste"})
    assert repetido.status_code == 200 and repetido.get_json()["ja_existia"] is True


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


# ---------------------------------------------------------------------------
# F4 of the web's PLANO_AGENTES_INDEPENDENTES: one owner per action
# ---------------------------------------------------------------------------
def _reinicio(**extra):
    return aprovacoes.criar("reiniciar_servico", {"servico": "OrcaView-MCP"}, {"texto": "x"},
                            pedido_por="ti-agente", alvo="OrcaView-MCP", agora=AGORA, **extra)


def test_mesmo_alvo_aberto_e_o_mesmo_pedido():
    a = _reinicio()
    b = aprovacoes.criar("reiniciar_servico", {"servico": "OrcaView-MCP"}, {"texto": "y"},
                         pedido_por="mira-agente", alvo="OrcaView-MCP", agora=AGORA)
    assert not a["ja_existia"] and b["ja_existia"] and b["id"] == a["id"]
    outro = aprovacoes.criar("reiniciar_servico", {"servico": "OrcaView-WBC-Painel"}, {"texto": "z"},
                             pedido_por="mira-agente", alvo="OrcaView-WBC-Painel", agora=AGORA)
    assert not outro["ja_existia"], "outro alvo é outro pedido"
    # Once decided and finished, the target is free again.
    aprovacoes.decidir(a["id"], aprovar=False, pessoa="M", canal="tela", agora=AGORA)
    assert not _reinicio()["ja_existia"]


def test_chave_repetida_devolve_o_mesmo_pedido_em_qualquer_estado():
    a = _reinicio(chave_idem="k-1234567890")
    aprovacoes.decidir(a["id"], aprovar=True, pessoa="M", canal="tela", agora=AGORA)
    aprovacoes.concluir(a["id"], ok=True, resultado={"ok": True}, agora=AGORA)
    b = _reinicio(chave_idem="k-1234567890")
    assert b["ja_existia"] and b["id"] == a["id"] and b["estado"] == "executado", "nunca executa 2x"


def test_duas_threads_concorrentes_uma_linha_so():
    import threading

    ids, erros = [], []

    def _pedir():
        try:
            for _ in range(50):
                ids.append(_reinicio()["id"])
        except Exception as exc:  # pragma: no cover - surfaced by the assert below
            erros.append(exc)

    threads = [threading.Thread(target=_pedir) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not erros and len(set(ids)) == 1
    assert len(aprovacoes.listar("pendente", agora=AGORA)) == 1


def test_teto_conta_depois_da_repeticao():
    for servico in ("OrcaView-MCP", "OrcaView-WBC-Painel", "OrcaView-Scheduler"):
        aprovacoes.criar("reiniciar_servico", {"servico": servico}, {}, pedido_por="m", alvo=servico,
                         por_hora=3, agora=AGORA)
    with pytest.raises(aprovacoes.AprovacaoInvalida) as erro:
        aprovacoes.criar("reiniciar_servico", {"servico": "OrcaView-WBC-Worker"}, {}, pedido_por="m",
                         alvo="OrcaView-WBC-Worker", por_hora=3, agora=AGORA)
    assert erro.value.tipo == "limite"
    repetido = aprovacoes.criar("reiniciar_servico", {"servico": "OrcaView-MCP"}, {}, pedido_por="m",
                                alvo="OrcaView-MCP", por_hora=3, agora=AGORA)
    assert repetido["ja_existia"], "uma repetição nunca vira 429"


def test_executando_sem_desfecho_libera_o_alvo():
    a = _reinicio()
    aprovacoes.decidir(a["id"], aprovar=True, pessoa="M", canal="tela", agora=AGORA)
    assert _reinicio()["ja_existia"], "executando segura o alvo"
    depois = AGORA + timedelta(minutes=aprovacoes.ORFA_MIN, seconds=1)
    aprovacoes.varrer_orfaos(agora=depois)
    orfao = aprovacoes.obter(a["id"], agora=depois)
    assert orfao["estado"] == "falhou" and orfao["resultado"] == {"motivo": "sem desfecho"}
    assert not aprovacoes.criar("reiniciar_servico", {"servico": "OrcaView-MCP"}, {}, pedido_por="m",
                                alvo="OrcaView-MCP", agora=depois)["ja_existia"]


def test_banco_antigo_com_duplicata_aberta_sobe_sem_o_indice(tmp_path, caplog):
    import sqlite3

    arquivo = tmp_path / "velho.db"
    conn = sqlite3.connect(arquivo)
    conn.execute("""CREATE TABLE aprovacoes (
        id TEXT PRIMARY KEY, codigo TEXT NOT NULL, acao TEXT NOT NULL, parametros TEXT NOT NULL,
        previa TEXT NOT NULL, motivo TEXT, pedido_por TEXT NOT NULL, em_nome_de TEXT,
        criado_em TEXT NOT NULL, expira_em TEXT NOT NULL, estado TEXT NOT NULL,
        decidido_por TEXT, decidido_canal TEXT, decidido_papel TEXT, decidido_cliente TEXT,
        decidido_em TEXT, motivo_recusa TEXT, resultado TEXT, concluido_em TEXT)""")
    for i in ("a", "b"):
        conn.execute("INSERT INTO aprovacoes VALUES (?, ?, 'reiniciar_servico', '{}', '{}', NULL, 'm', NULL, ?, ?, "
                     "'pendente', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)",
                     (i, f"000{i}", AGORA.isoformat(), (AGORA + timedelta(minutes=30)).isoformat()))
    conn.commit()
    conn.execute("ALTER TABLE aprovacoes ADD COLUMN alvo TEXT")
    conn.execute("UPDATE aprovacoes SET alvo = 'OrcaView-MCP'")
    conn.commit()
    conn.close()
    with caplog.at_level("ERROR"):
        assert len(aprovacoes.listar(arquivo=arquivo, agora=AGORA)) == 2, "nenhum pedido apagado"
    assert "NAO criado" in caplog.text
    # The transaction still answers with the open one.
    r = aprovacoes.criar("reiniciar_servico", {}, {}, pedido_por="m", alvo="OrcaView-MCP", agora=AGORA,
                         arquivo=arquivo)
    assert r["ja_existia"]


def test_rota_devolve_200_ja_existia_e_valida_a_chave(api):
    _, c, executados = api
    corpo = {"acao": "sincronizar_os", "parametros": {"nped": 84080}}
    cab = {"X-API-Key": "mestra-de-teste", "Idempotency-Key": "chave-de-teste-01"}
    primeiro = c.post("/aprovacoes", json=corpo, headers=cab)
    segundo = c.post("/aprovacoes", json=corpo, headers=cab)
    assert primeiro.status_code == 201 and primeiro.get_json()["ja_existia"] is False
    assert segundo.status_code == 200 and segundo.get_json()["ja_existia"] is True
    assert segundo.get_json()["aprovacao"]["id"] == primeiro.get_json()["aprovacao"]["id"]
    ruim = c.post("/aprovacoes", json=corpo, headers={**cab, "Idempotency-Key": "curta"})
    assert ruim.status_code == 400 and executados == []


def test_toda_acao_do_catalogo_declara_o_alvo():
    """An action without a target would never be reserved: each one names what it holds."""
    exemplos = {"sincronizar_os": {"nped": 84080}, "forcar_carga": {}, "processar_pedido": {"pedido": 84455},
                "reiniciar_servico": {"servico": "OrcaView-MCP"}}
    assert set(exemplos) == set(acoes_agente.CATALOGO)
    alvos = {nome: acao.alvo(exemplos[nome]) for nome, acao in acoes_agente.CATALOGO.items()}
    assert alvos == {"sincronizar_os": "84080", "forcar_carga": "oportunidades", "processar_pedido": "84455",
                     "reiniciar_servico": "OrcaView-MCP"}


def test_concorrencia_sem_a_trava_do_processo(monkeypatch):
    """The transaction and the index hold without the in-process lock (another process)."""
    import contextlib
    import threading

    monkeypatch.setattr(aprovacoes, "_trava", contextlib.nullcontext())
    ids, erros = [], []

    def _pedir():
        try:
            for _ in range(50):
                ids.append(_reinicio()["id"])
        except Exception as exc:  # pragma: no cover - surfaced by the assert below
            erros.append(exc)

    threads = [threading.Thread(target=_pedir) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not erros and len(set(ids)) == 1


def test_chave_reusada_em_outro_pedido_e_recusada():
    _reinicio(chave_idem="k-1234567890")
    with pytest.raises(aprovacoes.AprovacaoInvalida) as erro:
        aprovacoes.criar("reiniciar_servico", {"servico": "OrcaView-WBC-Painel"}, {}, pedido_por="m",
                         alvo="OrcaView-WBC-Painel", chave_idem="k-1234567890", agora=AGORA)
    assert erro.value.tipo == "chave_reusada"


def test_subida_da_api_fecha_todo_executando_e_o_reinicio_dela_vale():
    api_ = aprovacoes.criar("reiniciar_servico", {"servico": "OrcaView-OS-API"}, {}, pedido_por="m",
                            alvo="OrcaView-OS-API", agora=AGORA)
    mcp = _reinicio()
    for r in (api_, mcp):
        aprovacoes.decidir(r["id"], aprovar=True, pessoa="M", canal="tela", agora=AGORA)
    aprovacoes.varrer_orfaos(agora=AGORA + timedelta(seconds=30))
    assert aprovacoes.obter(api_["id"])["estado"] == "executado"
    assert aprovacoes.obter(mcp["id"])["estado"] == "falhou"
    # A real outcome that arrives late still wins over "sem desfecho".
    assert aprovacoes.concluir(mcp["id"], ok=True, resultado={"ok": True})["estado"] == "executado"


def test_arquivo_recriado_no_mesmo_processo_ganha_as_colunas(tmp_path):
    arquivo = tmp_path / "a.db"
    aprovacoes.listar(arquivo=arquivo, agora=AGORA)
    arquivo.unlink()
    r = aprovacoes.criar("forcar_carga", {}, {}, pedido_por="m", alvo="oportunidades", agora=AGORA, arquivo=arquivo)
    assert r["alvo"] == "oportunidades"


def test_rota_responde_409_para_chave_de_outro_pedido(api):
    _, c, _ = api
    cab = {"X-API-Key": "mestra-de-teste", "Idempotency-Key": "chave-de-teste-02"}
    assert c.post("/aprovacoes", json={"acao": "sincronizar_os", "parametros": {"nped": 1}},
                  headers=cab).status_code == 201
    assert c.post("/aprovacoes", json={"acao": "sincronizar_os", "parametros": {"nped": 2}},
                  headers=cab).status_code == 409
