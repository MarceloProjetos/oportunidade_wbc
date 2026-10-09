"""operacao/logins_sap.py (F6 of the web repo's PLANO_TEO_REDE_E_ROTINAS): who logs into the SAP.

The rows below have the USR5 shape read on 09/10/2026 (``Time`` is an HHMMSS integer); the robot is
the 07/10 case: ``financeiro04`` from ``WBCServConsole.exe`` on the .12 every 4 min, no Windows user."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

import situacao_pedidos_hana as hana
from operacao import logins_sap as ls


def _usr5(usuario, proc, ip, maquina, win, acao, hhmmss):
    return {"UserCode": usuario, "ProcName": proc, "ClientIP": ip, "ClientName": maquina, "WinUsrName": win,
            "Action": acao, "Time": hhmmss}


ROBO = [_usr5("financeiro04", "WBCServConsole.exe", "192.168.7.12", "SAPBUSINESSONER", "", "I", 80000 + 400 * i)
        for i in range(8)]
GENTE = [_usr5("financeiro01", "SAP Business One.exe", "192.168.7.12", "SAPBusinessOneR", "altamira_05", "I", t)
         for t in (64826, 64828, 64842)] + [
    _usr5("financeiro01", "SAP Business One.exe", "192.168.7.12", "SAPBusinessOneR", "altamira_05", "O", 120000),
    _usr5("financeiro01", "SAP Business One.exe", "192.168.7.12", "SAPBusinessOneR", "altamira_05", "F", 64800)]


def test_hora_e_ritmo():
    assert ls.hora(94003) == "09:40:03" and ls.hora(None) is None
    assert ls.ritmo([80000, 80400, 80800, 81200]) == ("4m00s", True)
    assert ls.ritmo([80000, 80400]) == ("", False)
    assert ls.ritmo([80000, 80013, 81500, 90000])[1] is False


def test_o_robo_real_de_07_10_com_um_buraco():
    """First real reading of F6b: the WBCServConsole.exe of 07/10 logged every 4 min from 06:28 to
    10:00, with ONE 8-min gap after 06:20:12 -- the old standard deviation called it irregular."""
    def hhmmss(s: int) -> int:
        return s // 3600 * 10000 + s % 3600 // 60 * 100 + s % 60

    horas = [62012] + [hhmmss(6 * 3600 + 28 * 60 + 3 + 240 * i) for i in range(53)]   # 06:20:12, 06:28:03…
    assert ls.ritmo(horas) == ("4m00s", True)
    assert ls.agrupar([{"UserCode": "financeiro04", "ProcName": "WBCServConsole.exe", "ClientIP": "192.168.7.12",
                        "ClientName": "SAPBusinessOneR", "WinUsrName": "", "Action": "I", "Time": h}
                       for h in horas])[0]["parece_robo"] is True
    # A burst of logins in seconds (a person opening the client, an add-on) is no schedule.
    assert ls.ritmo([80000, 80002, 80004, 80006, 80008, 80010])[1] is False
    # ... but a loop of dozens of logins every 20 s is (the kind that drains the Service Layer).
    assert ls.ritmo([hhmmss(8 * 3600 + 20 * i) for i in range(30)]) == ("0m20s", True)


def test_agrupa_e_acha_o_robo():
    grupos = ls.agrupar(ROBO + GENTE)
    robo, gente = grupos
    assert (robo["usuario"], robo["logins"], robo["sem_logout"], robo["ritmo"], robo["parece_robo"]) == (
        "financeiro04", 8, 8, "4m00s", True)
    assert robo["usuario_windows"] is None and robo["primeiro"] == "08:00:00" and robo["ultimo"] == "08:28:00"
    assert (gente["logins"], gente["logouts"], gente["sem_logout"], gente["falhas"]) == (3, 1, 2, 1)
    assert gente["parece_robo"] is False
    # With a Windows user it is a person, however regular.
    pessoa = [{**r, "WinUsrName": "fulano"} for r in ROBO]
    assert ls.agrupar(pessoa)[0]["parece_robo"] is False


@pytest.fixture
def hana_falso(monkeypatch):
    consultas = []
    conexoes = []

    class _Conn:
        def close(self):
            pass

    def _conectar():
        conexoes.append(1)
        return _Conn()

    respostas: dict[str, list] = {}

    def _linhas(_conn, texto, params):
        consultas.append((texto, params))
        for chave, linhas in respostas.items():
            if chave in texto:
                return linhas
        return []
    monkeypatch.setattr(hana, "_schema", lambda: "SBOALTAMIRAPROD")
    monkeypatch.setattr(hana, "_conectar", _conectar)
    monkeypatch.setattr(hana, "_linhas", _linhas)
    ls.limpar_cache()
    yield consultas, respostas, conexoes
    ls.limpar_cache()


def test_resumo_com_parametro_ligado_e_cache(hana_falso):
    consultas, respostas, _conexoes = hana_falso
    respostas['"USR5"'] = ROBO + GENTE
    r = ls.ler("resumo", dia="2026-10-09") if date.today() >= date(2026, 10, 9) else None
    if r is None:
        pytest.skip("clock before the fixture day")
    assert r["eventos"] == 13 and [g["usuario"] for g in r["parecem_robos"]] == ["financeiro04"]
    assert r["cache"] is False and r["truncado"] is False
    texto, params = consultas[0]
    assert '"SBOALTAMIRAPROD"."USR5"' in texto and "?" in texto and params == (date(2026, 10, 9),)
    assert f"LIMIT {ls.RESUMO_MAX_LINHAS + 1}" in texto
    assert ls.ler("resumo", dia="2026-10-09")["cache"] is True and len(consultas) == 1


def test_eventos_e_escritas(hana_falso):
    consultas, respostas, conexoes = hana_falso
    respostas['"OUSR"'] = [{"USERID": 7, "USER_CODE": "financeiro04"}]
    respostas['"USR5"'] = [{"Date": "2026-10-08 00:00:00", "Time": 172611, "Action": "I", "ClientIP": "192.168.7.12",
                            "ClientName": "SAPBusinessOneR", "WinUsrName": "admin.anderson", "WinSessnID": -1,
                            "ProcName": "SAP Business One.exe", "ProcessID": 20444, "Source": "SBO_DI_API"}]
    r = ls.ler("eventos", usuario="FINANCEIRO04", limite="500")
    assert r["eventos"][0]["hora"] == "17:26:11" and r["eventos"][0]["acao"] == "login"
    assert r["usuario"] == "financeiro04"                       # the code as the SAP stores it
    texto, params = consultas[-1]
    # Review of F6a: exact code (no LOWER on the big table), a date bound, capped and bound params.
    assert "LIMIT 200" in texto and 'LOWER("UserCode")' not in texto and '"Date" >= ?' in texto
    assert params[0] == "financeiro04" and len(conexoes) == 1
    respostas['"OQUT"'] = [{"Dia": "2026-10-08", "Criados": 3, "Alterados": 1}]
    hoje = date.today()
    antes = len(conexoes)
    r = ls.escritas("financeiro04", hoje - timedelta(days=2), hoje=hoje)
    assert r["tabelas"]["cotacoes"] == [{"dia": "2026-10-08", "criados": 3, "alterados": 1}]
    assert r["tabelas"]["pedidos"] == [] and "mínima" in r["aviso"]
    assert len(conexoes) == antes + 1                           # five queries, ONE connection
    respostas.pop('"OUSR"')
    ls.limpar_cache()
    with pytest.raises(ls.UsuarioNaoEncontrado):
        ls.escritas("ninguem", hoje - timedelta(days=2), hoje=hoje)


@pytest.mark.parametrize(("modo", "kw"), [
    ("qualquer", {}),
    ("eventos", {"usuario": "x'; DROP TABLE OUSR--"}),
    ("eventos", {"usuario": "financeiro04", "limite": "muitos"}),
    ("escritas", {"usuario": "financeiro04", "desde": "2020-01-01"}),
    ("resumo", {"dia": "09/10/2026"}),
])
def test_parametros_fora_da_faixa(hana_falso, modo, kw):
    consultas, _, _ = hana_falso
    with pytest.raises(ls.PedidoInvalido):
        ls.ler(modo, **kw)
    assert consultas == []          # refused before any HANA connection


def test_resumo_cortado(hana_falso, monkeypatch):
    _, respostas, _ = hana_falso
    monkeypatch.setattr(ls, "RESUMO_MAX_LINHAS", 5)
    respostas['"USR5"'] = ROBO
    r = ls.resumo(date(2026, 10, 9))
    assert r["truncado"] is True and r["eventos"] == 5 and r["grupos"][0]["logins"] == 5


def test_falha_da_consulta_vira_sap_indisponivel(monkeypatch):
    class _Conn:
        def close(self):
            pass

    def _quebra(*_a):
        raise RuntimeError("SQL error 258: insufficient privilege")
    monkeypatch.setattr(hana, "_schema", lambda: "SBOALTAMIRAPROD")
    monkeypatch.setattr(hana, "_conectar", lambda: _Conn())
    monkeypatch.setattr(hana, "_linhas", _quebra)
    ls.limpar_cache()
    with pytest.raises(hana.SAPIndisponivel) as exc:
        ls.ler("resumo")
    assert "RuntimeError" in str(exc.value) and "privilege" not in str(exc.value)
