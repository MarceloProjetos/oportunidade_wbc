"""F2 of PLANO_MIRA_AGENTE_11.md (removed 2026-10-06): read-only views of the .11 (``operacao/``).

What these tests hold:
1. Services: the six NSSM names, state and "since when"; a missing one is said, not hidden.
2. Connections: ONLY listed destinations and their own ports (rule 5); ping counts a reply
   only with ``TTL=``; the one-line conclusion tells DNS / port / host apart.
3. Version and deploy log: commit from ``.git`` without a git process; the last run of
   ``deploy.log`` and how it ended.
4. Order history: the diff between SAP versions, who saved each one, person vs integration.
5. Worker log: only the lines of the asked quote, current file first, then the rotated one.
"""
from __future__ import annotations

import socket
import subprocess
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from config import reset_settings
from operacao import conexoes, historico_pedido, log_worker, servicos, versao


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------
class _NaoExiste(Exception):
    pass


def _psutil_falso(estados: dict[str, dict]):
    def win_service_get(nome):
        if nome not in estados:
            raise _NaoExiste(nome)
        return SimpleNamespace(as_dict=lambda: estados[nome])

    return SimpleNamespace(
        NoSuchProcess=_NaoExiste,
        win_service_get=win_service_get,
        Process=lambda pid: SimpleNamespace(create_time=lambda: datetime(2026, 10, 2, 11, 2).timestamp()),
    )


def test_servicos_estado_desde_quando_e_quem_falta(monkeypatch):
    estados = {nome: {"status": "running", "start_type": "automatic", "pid": 10}
               for nome, _ in servicos.SERVICOS}
    estados["OrcaView-WBC-Worker"] = {"status": "stopped", "start_type": "automatic", "pid": None}
    del estados["OrcaView-MCP"]
    monkeypatch.setattr(servicos, "_psutil", lambda: _psutil_falso(estados))

    r = servicos.estado_servicos(agora=datetime(2026, 10, 2, 11, 32))
    assert r["total"] == 6 and r["rodando"] == 4
    assert r["fora_do_ar"] == ["OrcaView-MCP", "OrcaView-WBC-Worker"]
    api = r["servicos"][0]
    assert api["nome"] == "OrcaView-OS-API" and api["desde"] == "2026-10-02T11:02:00" and api["ha_minutos"] == 30
    mcp = r["servicos"][1]
    assert mcp["instalado"] is False and "não instalado" in mcp["motivo"]


def test_servicos_fora_do_windows_diz_que_nao_sabe(monkeypatch):
    monkeypatch.setattr(servicos, "_psutil", lambda: None)
    assert servicos.estado_servicos() == {
        "disponivel": False, "motivo": "leitura de serviços só existe no Windows com psutil", "servicos": []}


# ---------------------------------------------------------------------------
# Connections (rule 5: closed list)
# ---------------------------------------------------------------------------
@pytest.fixture
def env_destinos(monkeypatch):
    monkeypatch.setenv("SAP_HOST", "hana.local")
    monkeypatch.setenv("SAP_PORT", "30015")
    monkeypatch.setenv("SQLSERVER_HOST", "wbc.local")
    monkeypatch.setenv("SUPABASE_URL", "https://abc.supabase.co")
    reset_settings()
    yield
    reset_settings()


def test_lista_fechada_inclui_os_do_env(env_destinos):
    d = conexoes.destinos()
    assert d["sap-hana"].host == "hana.local" and d["sap-hana"].portas == (30015,)
    assert d["supabase"].host == "abc.supabase.co" and d["supabase"].portas == (443,)
    assert d["sql-server-wbc"].host == "wbc.local"
    assert {"esta-maquina", "orcaview-90", "altamira-view", "sap-rdp-12", "github", "service-layer"} <= set(d)


def test_destino_ou_porta_fora_da_lista_e_recusado_antes_da_rede(env_destinos, monkeypatch):
    monkeypatch.setattr(conexoes, "_resolver", lambda host: pytest.fail("não podia ir à rede"))
    with pytest.raises(conexoes.DestinoInvalido, match="Destino desconhecido"):
        conexoes.testar("10.0.0.1")
    with pytest.raises(conexoes.DestinoInvalido, match="não está na lista"):
        conexoes.testar("sap-hana", 22)


def test_conclusoes(env_destinos, monkeypatch):
    monkeypatch.setattr(conexoes, "_resolver", lambda host: (None, 1.0, "getaddrinfo failed"))
    assert "não resolve o nome github.com" in conexoes.testar("github")["conclusao"]

    monkeypatch.setattr(conexoes, "_resolver", lambda host: ("10.1.1.1", 1.0, None))
    monkeypatch.setattr(conexoes, "_pingar", lambda ip: {"responde": True})
    monkeypatch.setattr(conexoes, "_abrir", lambda ip, p: {"porta": p, "aberta": p != 8078})
    r = conexoes.testar("esta-maquina")
    assert [p["porta"] for p in r["portas"]] == [8077, 8078, 8079, 8080]
    assert "não a(s) 8078" in r["conclusao"]
    assert "na(s) porta(s) 8077" in conexoes.testar("esta-maquina", 8077)["conclusao"]

    monkeypatch.setattr(conexoes, "_abrir", lambda ip, p: {"porta": p, "aberta": False})
    assert "responde ao ping, mas" in conexoes.testar("sap-hana")["conclusao"]
    monkeypatch.setattr(conexoes, "_pingar", lambda ip: {"responde": False})
    assert "não responde nem ao ping" in conexoes.testar("sap-hana")["conclusao"]


@pytest.mark.parametrize("saida,responde,ms", [
    ("Resposta de 192.168.0.90: bytes=32 tempo=1ms TTL=128\nResposta de 192.168.0.90: bytes=32 tempo<1ms TTL=128",
     True, 1.0),
    ("Resposta de 192.168.0.1: Host de destino inacessível.\nEsgotado o tempo limite do pedido.", False, None),
])
def test_ping_so_conta_resposta_com_ttl(monkeypatch, saida, responde, ms):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=saida))
    r = conexoes._pingar("192.168.0.90")
    assert r["responde"] is responde and r["ms_medio"] == ms


def test_porta_aberta_e_fechada_de_verdade():
    servidor = socket.socket()
    servidor.bind(("127.0.0.1", 0))
    servidor.listen(1)
    porta = servidor.getsockname()[1]
    try:
        assert conexoes._abrir("127.0.0.1", porta)["aberta"] is True
    finally:
        servidor.close()
    assert conexoes._abrir("127.0.0.1", porta)["aberta"] is False


# ---------------------------------------------------------------------------
# Version and deploy log
# ---------------------------------------------------------------------------
def test_commit_pela_ref_pelo_packed_refs_e_destacado(tmp_path):
    git = tmp_path / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/master\n", encoding="utf-8")
    (git / "refs" / "heads" / "master").write_text("a" * 40 + "\n", encoding="utf-8")
    assert versao.commit_no_disco(tmp_path) == "a" * 40

    (git / "refs" / "heads" / "master").unlink()
    (git / "packed-refs").write_text(f"# pack-refs\n{'b' * 40} refs/heads/master\n", encoding="utf-8")
    assert versao.commit_no_disco(tmp_path) == "b" * 40

    (git / "HEAD").write_text("c" * 40, encoding="utf-8")
    assert versao.commit_no_disco(tmp_path) == "c" * 40
    assert versao.commit_no_disco(tmp_path / "nada") is None


def test_reinicio_pendente_quando_o_disco_mudou(monkeypatch):
    monkeypatch.setattr(versao, "COMMIT_NO_INICIO", "1" * 40)
    monkeypatch.setattr(versao, "commit_no_disco", lambda raiz=None: "2" * 40)
    assert versao.versao() == {"commit": "1111111", "commit_no_disco": "2222222", "reinicio_pendente": True}


DEPLOYS = """\
01/10/2026 18:00:00,10 | inicio | admin em SAPBUSINESSONEI
01/10/2026 18:00:05,10 | abortado | git fetch falhou - rede, DNS ou proxy; nada parado
02/10/2026 11:01:00,00 | inicio | admin em SAPBUSINESSONEI
02/10/2026 11:01:30,00 | git | de abc para def
02/10/2026 11:01:31,00 | pip | dependencias mantidas
02/10/2026 11:02:00,00 | health | API 8077 HTTP 200
02/10/2026 11:02:01,00 | fim | OK
"""


def test_ultimo_deploy_e_a_ultima_execucao(tmp_path):
    arquivo = tmp_path / "deploy.log"
    arquivo.write_text(DEPLOYS, encoding="utf-8")
    r = versao.ultimo_deploy(arquivo)
    assert r["resultado"] == "fim: OK" and r["deploys_registrados"] == 2
    assert r["inicio"] == "02/10/2026 11:01:00,00" and r["quem"] == "admin em SAPBUSINESSONEI"
    assert [e["etapa"] for e in r["etapas"]] == ["inicio", "git", "pip", "health", "fim"]

    arquivo.write_text(DEPLOYS + "02/10/2026 12:00:00,00 | inicio | admin em X\n", encoding="utf-8")
    assert versao.ultimo_deploy(arquivo)["resultado"] == "em_andamento_ou_interrompido"
    assert versao.ultimo_deploy(tmp_path / "nao.log")["registrado"] is False


# ---------------------------------------------------------------------------
# Order history (ADOC/ADO1)
# ---------------------------------------------------------------------------
def _versao(n, usuario, codigo, **cab):
    base = {"LogInstanc": n, "UpdateDate": date(2026, 10, 1), "UpdateTS": 140000 + n,
            "CardCode": "C001", "DocDueDate": date(2026, 10, 20), "DocTotal": Decimal("1000.00"),
            "DocStatus": "O", "CANCELED": "N", "SlpCode": 5, "Confirmed": "Y",
            "DataVers": n, "_U_NAME": usuario, "_USER_CODE": codigo}
    return base | cab


def _linha(n, num, item, qtd, peso):
    return {"LogInstanc": n, "LineNum": num, "ItemCode": item, "Quantity": Decimal(qtd),
            "Weight1": Decimal(peso), "Price": Decimal("10"), "LineStatus": "O"}


def test_historico_diz_o_que_mudou_e_quem_mudou(monkeypatch):
    monkeypatch.setenv("SL_USERNAME", "orcaview")
    versoes = [_versao(1, "Integração", "orcaview"),
               _versao(2, "Adriano Silva", "adriano", DocDueDate=date(2026, 10, 25), U_INO_NOVO="x"),
               _versao(3, "Integração", "orcaview", DocDueDate=date(2026, 10, 25), U_INO_NOVO="x")]
    linhas = [_linha(1, 0, "MOD-A", "2", "124.5"), _linha(1, 1, "MOD-B", "1", "10"),
              _linha(2, 0, "MOD-A", "1", "62.25"), _linha(2, 1, "MOD-B", "1", "10"),
              _linha(3, 0, "MOD-A", "1", "62.25"), _linha(3, 1, "MOD-B", "1", "10"),
              _linha(3, 2, "MOD-C", "3", "5")]
    r = historico_pedido.montar(84453, 99001, versoes, linhas)

    assert r["total_versoes"] == 3 and [v["versao"] for v in r["versoes"]] == [3, 2, 1]
    criacao, pessoa, integracao = r["versoes"][2], r["versoes"][1], r["versoes"][0]
    assert criacao["resumo"] == "criado com 2 linha(s)" and criacao["pela_integracao"] is True
    assert pessoa["usuario"] == "Adriano Silva" and pessoa["pela_integracao"] is False
    assert pessoa["momento"].startswith("2026-10-01T14:00")
    assert {(m["onde"], m["campo"], m["antes"], m["depois"]) for m in pessoa["mudancas"]} == {
        ("cabeçalho", "data de entrega", "2026-10-20", "2026-10-25"),
        ("cabeçalho", "U_INO_NOVO", None, "x"),
        ("linha 0 (MOD-A)", "quantidade", 2.0, 1.0),
        ("linha 0 (MOD-A)", "peso", 124.5, 62.25),
    }
    assert integracao["mudancas"] == [{"onde": "linha 2", "campo": "linha incluída",
                                       "antes": None, "depois": "MOD-C"}]


def test_texto_longo_compara_inteiro_e_mostra_cortado():
    longo = "x" * 500
    r = historico_pedido.montar(1, 1, [_versao(1, "A", "a", Comments=longo),
                                       _versao(2, "A", "a", Comments=longo + "y")], [])
    (mudanca,) = r["versoes"][0]["mudancas"]
    assert mudanca["campo"] == "observações" and len(mudanca["depois"]) == historico_pedido.TEXTO_MAX + 1


def test_sem_usuario_da_integracao_no_env_nao_chuta(monkeypatch):
    monkeypatch.delenv("SL_USERNAME", raising=False)
    monkeypatch.delenv("OP_SL_USERNAME", raising=False)
    r = historico_pedido.montar(1, 1, [_versao(1, "Integração", "orcaview")], [])
    assert r["versoes"][0]["pela_integracao"] is None


def test_historico_limita_versoes_e_mudancas(monkeypatch):
    versoes = [_versao(n, "X", "x", DocTotal=Decimal(n)) for n in range(1, 31)]
    linhas = [_linha(n, num, f"I{num}", str(n), "1") for n in range(1, 31) for num in range(40)]
    r = historico_pedido.montar(1, 1, versoes, linhas, limite=5)
    assert len(r["versoes"]) == 5 and r["versoes_omitidas"] == 25 and r["versoes"][0]["versao"] == 30
    assert len(r["versoes"][0]["mudancas"]) == historico_pedido.MUDANCAS_POR_VERSAO
    assert r["versoes"][0]["mudancas_omitidas"] == 41 - historico_pedido.MUDANCAS_POR_VERSAO


def test_historico_consulta_com_parametro_e_pedido_inexistente(monkeypatch):
    consultas = []

    class Conexao:
        def close(self):
            consultas.append("close")

    def linhas(conn, sql, params=()):
        consultas.append((sql.split(" FROM ")[1].split()[0], params))
        return []

    monkeypatch.setattr(historico_pedido.hana, "_schema", lambda: "SBO")
    monkeypatch.setattr(historico_pedido.hana, "_conectar", Conexao)
    monkeypatch.setattr(historico_pedido.hana, "_linhas", linhas)
    with pytest.raises(historico_pedido.PedidoNaoEncontrado):
        historico_pedido.historico(84453)
    assert consultas == [('"SBO"."ORDR"', (84453,)), "close"]


# ---------------------------------------------------------------------------
# Worker log of one quote
# ---------------------------------------------------------------------------
def _log(*mensagens):
    return "".join(f"2026-10-02 10:0{i}:00 | {nivel:<8} | wbcpython | {texto}\n"
                   for i, (nivel, texto) in enumerate(mensagens))


def test_log_so_do_orcamento_atual_e_depois_o_rotacionado(tmp_path, monkeypatch):
    atual = tmp_path / "wbcpython.log"
    atual.write_text(_log(("INFO", "[ciclo] 00125348: pedido criado"),
                          ("INFO", "[ciclo] 00125999: nada a fazer")), encoding="utf-8")
    (tmp_path / "wbcpython.log.1").write_text(
        _log(("ERROR", "[porta-paletes] 00125348: módulo sem peso")), encoding="utf-8")
    monkeypatch.setenv("LOG_FILE", str(atual))

    r = log_worker.log_do_orcamento("125348")
    assert r["orcamento"] == "00125348" and r["graves"] == 1
    assert [linha["mensagem"] for linha in r["linhas"]] == [
        "[ciclo] 00125348: pedido criado", "[porta-paletes] 00125348: módulo sem peso"]
    assert len(log_worker.log_do_orcamento("125348", limite=1)["linhas"]) == 1


def test_log_desligado(monkeypatch):
    monkeypatch.setenv("LOG_FILE", "")
    assert log_worker.log_do_orcamento("1")["disponivel"] is False


# ---------------------------------------------------------------------------
# API routes (scope leitura) and MCP tools
# ---------------------------------------------------------------------------
@pytest.fixture
def api(monkeypatch):
    pytest.importorskip("flask")
    import api as apimod
    from seguranca import credenciais

    monkeypatch.setenv("OS_API_KEY", "mestra-de-teste")
    reset_settings()
    apimod._rate_limiter.reset()
    apimod.app.config.update(TESTING=True)
    chave = credenciais.criar("leitor", ["leitura"])
    return apimod, apimod.app.test_client(), {"X-API-Key": chave}


def test_rotas_pedem_leitura(api):
    _, c, _ = api
    for rota in ("/operacao/servicos", "/operacao/conexoes", "/operacao/conexoes/github",
                 "/operacao/deploy", "/pedidos/84453/historico", "/wbc/orcamentos/125348/log"):
        assert c.get(rota).status_code == 401, rota


def test_rota_de_conexao_recusa_fora_da_lista_e_tem_teto(api, monkeypatch):
    apimod, c, h = api
    r = c.get("/operacao/conexoes/10.0.0.1", headers=h)
    assert r.status_code == 400 and "github" in r.get_json()["destinos"]
    monkeypatch.setattr(conexoes, "testar", lambda nome, porta=None: {"conclusao": "ok", "porta": porta})
    assert c.get("/operacao/conexoes/github?porta=443", headers=h).get_json()["porta"] == 443
    for _ in range(apimod._RATE_CONEXAO_MAX):
        c.get("/operacao/conexoes/github", headers=h)
    assert c.get("/operacao/conexoes/github", headers=h).status_code == 429


def test_rota_do_historico(api, monkeypatch):
    apimod, c, h = api
    pedidas = []

    def historico(n, por_docentry=False, limite=20):
        pedidas.append((n, por_docentry, limite))
        if n == 1:
            raise historico_pedido.PedidoNaoEncontrado("Nenhum pedido")
        if n == 2:
            raise apimod.sit_ped_hana.SAPIndisponivel("HANA fora")
        return {"pedido": n, "versoes": []}

    monkeypatch.setattr(historico_pedido, "historico", historico)
    assert c.get("/pedidos/84453/historico?versoes=5&chave=docentry", headers=h).get_json()["pedido"] == 84453
    assert pedidas[-1] == (84453, True, 5)
    assert c.get("/pedidos/1/historico", headers=h).status_code == 404
    assert c.get("/pedidos/2/historico", headers=h).status_code == 503
    assert c.get("/pedidos/abc/historico", headers=h).status_code == 400


def test_rotas_de_servicos_deploy_e_log(api, monkeypatch):
    _, c, h = api
    monkeypatch.setattr(servicos, "estado_servicos", lambda: {"rodando": 6})
    assert c.get("/operacao/servicos", headers=h).get_json() == {"ok": True, "rodando": 6}
    monkeypatch.setattr(versao, "ultimo_deploy", lambda: {"registrado": False})
    corpo = c.get("/operacao/deploy", headers=h).get_json()
    assert corpo["ultimo_deploy"] == {"registrado": False} and "reinicio_pendente" in corpo["versao"]
    monkeypatch.setattr(log_worker, "log_do_orcamento", lambda n, limite: {"orcamento": n.zfill(8), "linhas": []})
    assert c.get("/wbc/orcamentos/125348/log", headers=h).get_json()["orcamento"] == "00125348"
    assert c.get("/wbc/orcamentos/12a/log", headers=h).status_code == 400


def test_status_completo_traz_a_versao_e_o_publico_nao(api, monkeypatch):
    apimod, c, h = api
    monkeypatch.setattr(apimod, "collect_status", lambda *_a, **_k: {"ok": True, "checks": {}, "alerts": []})
    assert "versao" in c.get("/status", headers=h).get_json()
    assert "versao" not in c.get("/status").get_json()


@pytest.fixture(scope="module")
def fachada():
    pytest.importorskip("mcp.server.fastmcp")
    import importlib.util
    from pathlib import Path

    caminho = Path(__file__).resolve().parents[1] / "mcp" / "mcp_server.py"
    spec = importlib.util.spec_from_file_location("_fachada_mcp_f2", caminho)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_ferramentas_chamam_a_rota_certa(fachada, monkeypatch):
    chamadas = []
    monkeypatch.setattr(fachada, "_get", lambda path, params=None: chamadas.append((path, params)) or {})
    fachada.estado_servicos()
    fachada.testar_conexao()
    fachada.testar_conexao("SAP-HANA", 30015)
    fachada.ultimo_deploy()
    fachada.historico_pedido(84453, versoes=500, chave="docentry")
    fachada.log_orcamento_wbc("00125348", linhas=0)
    assert chamadas == [
        ("/operacao/servicos", None), ("/operacao/conexoes", None),
        ("/operacao/conexoes/sap-hana", {"porta": 30015}), ("/operacao/deploy", None),
        ("/pedidos/84453/historico", {"versoes": 60, "chave": "docentry"}),
        ("/wbc/orcamentos/00125348/log", {"linhas": 1}),
    ]
    assert fachada._tempo_limite("GET", "/operacao/conexoes/github") == fachada._TEMPO_CONEXAO


# ---------------------------------------------------------------------------
# F6 of PLANO_AGENTE_TI (web repo): the .90 seen from the .11 — read only
# ---------------------------------------------------------------------------
from datetime import timedelta  # noqa: E402

from operacao import ronda_90  # noqa: E402


def _resultado(abertas: bool, ping: bool | None) -> dict:
    return {"portas": [{"porta": 443, "aberta": abertas}, {"porta": 8000, "aberta": abertas}],
            "ping": {"responde": ping}}


def test_ronda_classifica_ok_app_fora_e_desligado():
    assert ronda_90.classificar(_resultado(True, None)) == ("ok", None)
    assert ronda_90.classificar(_resultado(False, True)) == ("fora", "app fora")
    assert ronda_90.classificar(_resultado(False, False)) == ("fora", "desligado")
    assert ronda_90.classificar(_resultado(False, None)) == ("fora", None), "ping que nem rodou não é 'desligado'"


def _iso(t):
    return t.isoformat(timespec="seconds")


def test_ronda_fecha_o_periodo_com_hora_real_e_esquece_o_antigo():
    t = datetime(2026, 10, 7, 10, 0).astimezone()
    dados = ronda_90.aplicar(ronda_90._vazio(), "ok", None, t)
    dados = ronda_90.aplicar(dados, "fora", "app fora", t + timedelta(minutes=5))
    assert dados["estado"] == "ok", "uma leitura ruim ainda não é período"
    dados = ronda_90.aplicar(dados, "fora", "desligado", t + timedelta(minutes=10))
    assert dados["estado"] == "fora" and dados["desde"] == _iso(t + timedelta(minutes=5))
    assert dados["como"] == "desligado" and dados["periodos"] == []
    dados = ronda_90.aplicar(dados, "ok", None, t + timedelta(minutes=15))
    [p] = dados["periodos"]
    assert p == {"inicio": _iso(t + timedelta(minutes=5)), "fim": _iso(t + timedelta(minutes=15)), "como": "desligado"}
    depois = ronda_90.aplicar(dados, "ok", None, t + timedelta(days=ronda_90.DIAS_GUARDADOS + 1))
    assert depois["periodos"] == [], "período antigo sai"


def test_ronda_um_soluco_nao_vira_periodo():
    t = datetime(2026, 10, 7, 10, 0).astimezone()
    dados = ronda_90.aplicar(ronda_90._vazio(), "ok", None, t)
    dados = ronda_90.aplicar(dados, "fora", "desligado", t + timedelta(minutes=5))
    dados = ronda_90.aplicar(dados, "ok", None, t + timedelta(minutes=10))
    assert dados["estado"] == "ok" and dados["periodos"] == [] and dados["suspeita"] is None
    assert dados["desde"] == _iso(t), "o 'ok desde' não recomeça por um soluço"


def test_ronda_o_pior_visto_vence_no_boot():
    """At boot the machine answers ping before the app opens its ports: a night "desligado" must
    not close as "app fora"."""
    t = datetime(2026, 10, 7, 18, 0).astimezone()
    dados = ronda_90.aplicar(ronda_90._vazio(), "ok", None, t)
    for i, como in enumerate(["desligado", "desligado", "app fora"], start=1):
        dados = ronda_90.aplicar(dados, "fora", como, t + timedelta(minutes=5 * i))
    dados = ronda_90.aplicar(dados, "ok", None, t + timedelta(minutes=20))
    assert dados["periodos"][0]["como"] == "desligado"


def test_ronda_com_a_11_parada_diz_entre_quais_leituras():
    t = datetime(2026, 10, 7, 5, 0).astimezone()
    dados = ronda_90.aplicar(ronda_90._vazio(), "ok", None, t)
    # The .11 restarts (~06:12) and comes back long after its last reading.
    dados = ronda_90.aplicar(dados, "fora", "desligado", t + timedelta(hours=1, minutes=20))
    dados = ronda_90.aplicar(dados, "fora", "desligado", t + timedelta(hours=1, minutes=25))
    dados = ronda_90.aplicar(dados, "fora", "desligado", t + timedelta(hours=1, minutes=30))
    dados = ronda_90.aplicar(dados, "ok", None, t + timedelta(hours=2, minutes=40))
    [p] = dados["periodos"]
    assert p["inicio"] == _iso(t + timedelta(hours=1, minutes=20)) and p["inicio_apos"] == _iso(t)
    assert p["fim_apos"] == _iso(t + timedelta(hours=1, minutes=30))


def test_ronda_grava_e_le_o_arquivo_e_teste_quebrado_nao_muda_nada(tmp_path, monkeypatch):
    arquivo = tmp_path / "ronda_90.json"
    monkeypatch.setattr(conexoes, "testar", lambda nome, porta=None: _resultado(False, False))
    ronda_90.rodar_uma_vez(arquivo)
    assert ronda_90.rodar_uma_vez(arquivo)["estado"] == "fora"
    assert ronda_90.ler(arquivo)["como"] == "desligado"

    def _quebra(nome, porta=None):
        raise OSError("sem rede")

    monkeypatch.setattr(conexoes, "testar", _quebra)
    assert ronda_90.rodar_uma_vez(arquivo)["estado"] == "fora", "sem leitura: o estado fica como estava"
    arquivo.write_text("{lixo", encoding="utf-8")
    assert ronda_90.ler(arquivo)["estado"] is None, "arquivo ilegível = estado vazio, nunca exceção"


def test_ronda_gravacao_que_falha_nao_derruba_a_passada(tmp_path, monkeypatch):
    monkeypatch.setattr(conexoes, "testar", lambda nome, porta=None: _resultado(True, True))

    def _negado(dados, arquivo):
        raise PermissionError(32, "em uso")

    monkeypatch.setattr(ronda_90, "_gravar", _negado)
    assert ronda_90.rodar_uma_vez(tmp_path / "r.json")["estado"] == "ok", "nunca levanta"


def test_ronda_so_liga_na_11(monkeypatch, tmp_path):
    from wbcpython import safety

    monkeypatch.setattr(safety, "is_production_machine", lambda: False)
    assert ronda_90.iniciar(tmp_path / "r.json") is None


def test_ronda_nunca_age():
    """Read only, like the web's vigia (test_vigia_nunca_age): no process control, no restart,
    no Wake-on-LAN, no write to another machine."""
    import ast
    import pathlib

    fonte = pathlib.Path(ronda_90.__file__).read_text(encoding="utf-8")
    nomes = {n.id for n in ast.walk(ast.parse(fonte)) if isinstance(n, ast.Name)} | {
        n.attr for n in ast.walk(ast.parse(fonte)) if isinstance(n, ast.Attribute)}
    importados = {a.name for n in ast.walk(ast.parse(fonte)) if isinstance(n, (ast.Import, ast.ImportFrom))
                  for a in n.names} | {n.module for n in ast.walk(ast.parse(fonte)) if isinstance(n, ast.ImportFrom)}
    proibidos = {"subprocess", "system", "Popen", "reinicio", "reiniciar", "acoes_agente", "aprovacoes",
                 "wake_altservidor_ia", "post", "put", "delete", "requests", "httpx", "kill", "terminate"}
    assert not (nomes | importados) & proibidos, sorted((nomes | importados) & proibidos)
    assert "restart-" not in fonte.lower() and "stop-service" not in fonte.lower()


def test_rota_da_ronda_pede_leitura_e_devolve_o_estado(api, monkeypatch, tmp_path):
    _, c, h = api
    assert c.get("/operacao/ronda-90").status_code == 401
    monkeypatch.setattr(ronda_90, "ARQUIVO_PADRAO", tmp_path / "r.json")
    monkeypatch.setattr(ronda_90, "publico", lambda arquivo=None: {
        "estado": "ok", "desde": "x", "como": None, "ultima_leitura": "y", "periodos": [], "intervalo_s": 300,
        "destino": "orcaview-90"})
    corpo = c.get("/operacao/ronda-90", headers=h).get_json()
    assert corpo["ok"] is True and corpo["estado"] == "ok" and corpo["intervalo_s"] == 300
