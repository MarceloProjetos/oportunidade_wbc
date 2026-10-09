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
def ler(monkeypatch):
    chamadas = []
    monkeypatch.setattr(tr, "_cache", {"em": 0.0, "dados": None})
    monkeypatch.setattr(tr, "_boot", lambda: "2026-10-08T06:17:16")

    def _definir(travados, outros=(), erro="", erro_outros=""):
        def _ler(ids, maximo):
            chamadas.append((tuple(sorted(ids)), maximo))
            if ids == tr.SCM_TRAVADO:
                return (None, erro) if erro else (list(travados), "")
            return (None, erro_outros) if erro_outros else (list(outros), "")
        monkeypatch.setattr(tr, "_ler", _ler)
    return chamadas, _definir


def test_leitura_cache_e_falhas(ler):
    chamadas, definir = ler
    definir([e for e in TARDE if e["id"] in tr.SCM_TRAVADO], [e for e in TARDE if e["id"] not in tr.SCM_TRAVADO])
    r = tr.travamentos()
    assert r["disponivel"] is True and r["cascata"]["inicio"] == "2026-10-08T16:07:37" and r["janela_min"] == 60
    assert r["ligou"] == "2026-10-08T06:17:16" and r["servicos"]["Audiosrv"]["caiu"] and "parcial" not in r
    assert r["agora"] and tr.travamentos()["cache"] is True and len(chamadas) == 2   # 2 queries, then cache
    tr._cache.update(em=0.0, dados=None)
    definir([], [])                                # nothing in the hour: explicit empty answer
    assert tr.travamentos()["servicos"] == {} and tr._cache["dados"]["cascata"] is None
    tr._cache.update(em=0.0, dados=None)
    definir(TARDE[:3], erro_outros="acesso negado")  # the hangs are what matters: partial, said so
    r = tr.travamentos()
    assert r["disponivel"] is True and r["cascata"] is not None and r["parcial"] == "acesso negado"
    tr._cache.update(em=0.0, dados=None)
    definir([], erro="a leitura do log passou de 8s")
    assert tr.travamentos() == {"disponivel": False, "motivo": "a leitura do log passou de 8s"}
    assert tr.travamentos()["cache"] is True       # a failure is kept too: no process storm


#: wevtutil /f:xml as the .12 writes it (UTF-16 decoded): two events, no root element.
_XML = ("﻿<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'><System>"
        "<Provider Name='Service Control Manager'/><EventID Qualifiers='49152'>7011</EventID>"
        "<TimeCreated SystemTime='2026-10-09T16:03:46.1234567Z'/></System><EventData>"
        "<Data Name='param1'>30000</Data><Data Name='param2'>iphlpsvc</Data></EventData></Event>"
        "<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'><System>"
        "<Provider Name='Service Control Manager'/><EventID Qualifiers='49152'>7046</EventID>"
        "<TimeCreated SystemTime='2026-10-09T16:11:15.5Z'/></System><EventData>"
        "<Data Name='param1'>Redirecionador de Portas do Modo do Usuário</Data></EventData></Event>")


def test_xml_do_wevtutil_vira_eventos():
    eventos = tr.eventos_do_xml(_XML)
    assert [(e["id"], e["p"]) for e in eventos] == [(7011, ["30000", "iphlpsvc"]),
                                                   (7046, ["Redirecionador de Portas do Modo do Usuário"])]
    # UTC in the log, local time out (the rest of the module and the Téo read local time).
    from datetime import UTC, datetime
    esperado = datetime(2026, 10, 9, 16, 3, 46, tzinfo=UTC).astimezone().replace(tzinfo=None)
    assert eventos[0]["quando"] == esperado.isoformat(timespec="seconds")
    assert tr.agrupar(eventos)["iphlpsvc"]["travado_chave"] == [eventos[0]["quando"]]


def test_consulta_so_le_o_scm_da_ultima_hora():
    q = tr._consulta(tr.SCM_TRAVADO)
    assert "Service Control Manager" in q and "timediff(@SystemTime) <= 3600000" in q
    assert all(f"EventID={i}" in q for i in (7011, 7046, 7022)) and "EventID=7036" not in q


def test_leitura_em_andamento_nao_segura_quem_chega(ler):
    """Review of F5c1: a second caller never waits on the lock (a waiting caller holds a server thread)."""
    _chamadas, definir = ler
    definir(TARDE[:3])
    tr._trava.acquire()
    try:
        assert tr.travamentos() == {"disponivel": False, "motivo": "leitura em andamento"}
        tr._cache.update(em=0.0, dados={"disponivel": True, "servicos": {}})
        assert tr.travamentos() == {"disponivel": True, "servicos": {}, "cache": True}
    finally:
        tr._trava.release()
