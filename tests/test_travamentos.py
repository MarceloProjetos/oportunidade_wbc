"""operacao/travamentos.py (F5b of web PLANO_TEO_REDE_E_ROTINAS). Sibling test, IDENTICAL in SAP_RDP and
ServidorIntegracaoSAP: the hangs below are the .12's real afternoon of 08/10."""
from __future__ import annotations

import pytest

from operacao import travamentos as tr


def _e(quando, id_, *p):
    return {"quando": quando, "id": id_, "p": list(p)}


#: SCM as the .12 logged it on 08/10 (7011 carries "30000" first and the service second; 7046 the display name).
TARDE = [
    _e("2026-10-08T16:07:37", 7011, "30000", "iphlpsvc"),
    _e("2026-10-08T16:08:07", 7011, "30000", "NlaSvc"),
    _e("2026-10-08T16:08:40", 7011, "30000", "Schedule"),
    _e("2026-10-08T16:09:02", 7011, "30000", "UmRdpService"),
    _e("2026-10-08T16:09:40", 7011, "30000", "RasMan"),
    _e("2026-10-08T16:10:10", 7011, "30000", "Schedule"),
    _e("2026-10-08T16:10:40", 7011, "30000", "Spooler"),
    _e("2026-10-08T16:15:59", 7046, "Redirecionador de Portas do Modo do Usuário dos Serviços de Área de Trabalho"),
    _e("2026-10-08T16:20:00", 7031, "Audiosrv"),
    _e("2026-10-09T00:10:18", 7000, "B1Workflow"),
]


def test_so_o_7011_conta_na_cascata():
    """Review of F5b: 7046/7022 carry the DISPLAY name -- "Agendador de Tarefas" next to "Schedule" would
    be two services. Three display-name hangs are no cascade; the single-service count still sees them."""
    nomes = ["Agendador de Tarefas", "Serviço de Log para Acesso de Usuário", "Spooler de Impressão"]
    grupos = tr.agrupar([_e(f"2026-10-08T16:0{i}:00", 7046, n) for i, n in enumerate(nomes)])
    assert tr.cascata(grupos) is None and grupos["Agendador de Tarefas"]["travado"]
    assert grupos["Agendador de Tarefas"]["travado_chave"] == []


def test_a_cascata_de_08_10_comeca_pelo_ip_helper():
    grupos = tr.agrupar(TARDE)
    assert grupos["Schedule"]["travado"] == ["2026-10-08T16:10:10", "2026-10-08T16:08:40"]
    assert grupos["Audiosrv"]["caiu"] == ["2026-10-08T16:20:00"] and grupos["B1Workflow"]["nao_iniciou"]
    c = tr.cascata(grupos)
    assert c["inicio"] == "2026-10-08T16:07:37" and c["fim"] == "2026-10-08T16:10:40" and c["rede"] is True
    assert [s["nome"] for s in c["servicos"]][:4] == ["iphlpsvc", "NlaSvc", "Schedule", "UmRdpService"]


def test_tres_servicos_ja_sao_cascata_e_dois_nao():
    """By 16:08:40 the cascade existed: 3 services in 63 s -- 2 min before the monitor API hung."""
    assert tr.cascata(tr.agrupar(TARDE[:3]))["servicos"][-1]["nome"] == "Schedule"
    assert tr.cascata(tr.agrupar(TARDE[:2])) is None
    # The same service hanging again is one service, not a cascade.
    assert tr.cascata(tr.agrupar([_e(f"2026-10-08T16:0{i}:00", 7011, "30000", "Schedule") for i in range(5)])) is None


def test_travamentos_longe_um_do_outro_nao_sao_uma_cascata():
    eventos = [_e("2026-10-08T10:00:00", 7011, "30000", "A"), _e("2026-10-08T10:20:00", 7011, "30000", "B"),
               _e("2026-10-08T10:40:00", 7011, "30000", "C")]
    assert tr.cascata(tr.agrupar(eventos)) is None
    assert tr.cascata(tr.agrupar([*eventos, _e("2026-10-08T10:45:00", 7011, "30000", "D"),
                                  _e("2026-10-08T10:50:00", 7011, "30000", "E")]))["inicio"] == "2026-10-08T10:40:00"


def test_primeiro_que_nao_e_de_rede():
    c = tr.cascata(tr.agrupar([_e("2026-10-08T16:07:00", 7011, "30000", "Spooler"), *TARDE[:3]]))
    assert c["servicos"][0]["nome"] == "Spooler" and c["rede"] is False


@pytest.fixture
def ps(monkeypatch):
    chamadas = []
    monkeypatch.setattr(tr, "_cache", {"em": 0.0, "dados": None})

    def _definir(dados, erro=None):
        def _rodar(script, timeout):
            chamadas.append((script, timeout))
            return dados, erro
        monkeypatch.setattr(tr.windows_update, "_rodar_ps", _rodar)
    return chamadas, _definir


def test_leitura_cache_e_falhas(ps):
    chamadas, definir = ps
    definir({"boot": "2026-10-08T06:17:16", "travados": [e for e in TARDE if e["id"] in tr.SCM_TRAVADO],
             "outros": [e for e in TARDE if e["id"] not in tr.SCM_TRAVADO]})
    r = tr.travamentos()
    assert r["disponivel"] is True and r["cascata"]["inicio"] == "2026-10-08T16:07:37" and r["janela_min"] == 60
    assert r["ligou"] == "2026-10-08T06:17:16" and r["servicos"]["Audiosrv"]["caiu"]
    assert r["agora"] and tr.travamentos()["cache"] is True and chamadas == [(tr._PS, tr.TIMEOUT_S)]
    tr._cache.update(em=0.0, dados=None)
    definir({"travados": {}, "outros": {}})        # nothing in the hour: explicit empty answer
    assert tr.travamentos()["servicos"] == {} and tr._cache["dados"]["cascata"] is None
    tr._cache.update(em=0.0, dados=None)
    definir({"travados": [], "outros": {"erro": "acesso negado"}})
    assert tr.travamentos() == {"disponivel": False, "motivo": "acesso negado"}
    tr._cache.update(em=0.0, dados=None)
    definir(None, "a coleta passou de 15s")
    assert tr.travamentos() == {"disponivel": False, "motivo": "a coleta passou de 15s"}
    assert tr.travamentos()["cache"] is True       # a failure is kept too: no PowerShell storm


def test_script_e_ascii_sem_aspas_duplas():
    assert tr._PS.isascii() and '"' not in tr._PS
    for evento_id in (7011, 7046, 7022, 7031, 7034, 7000, 7009):
        assert str(evento_id) in tr._PS
