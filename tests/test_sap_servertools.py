"""web PLANO_RESET_SAP_TEO: the SAP VM's ``sapb1servertools`` restarted over SSH, after approval.

What these tests hold:
1. Only the .11 runs it; without the key it says how to make one.
2. A late approval never restarts a healthy SAP: port 40000 answering = refused (preview and run).
3. The SSH call is fixed (BatchMode, pinned host key, the one command) and a refusal is told apart
   from a systemctl failure.
4. Success = port 40000 back within the wait; otherwise "not back", never "ok".
5. The key never enters this public repo.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from operacao import acoes_agente
from operacao import sap_servertools as st

RAIZ = Path(__file__).resolve().parents[1]


class _Relogio:
    def __init__(self) -> None:
        self.t = 0.0

    def monotonic(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


@pytest.fixture
def na_11(monkeypatch, tmp_path):
    monkeypatch.setattr(st, "_na_11", lambda: True)
    monkeypatch.setattr(st, "CHAVE", tmp_path / "k")
    monkeypatch.setattr(st, "CONHECIDOS", tmp_path / "kh")
    monkeypatch.setattr(st, "_ssh", lambda: "ssh")
    monkeypatch.setattr(st, "host", lambda: "sapvm")
    (tmp_path / "k").write_text("x")
    (tmp_path / "kh").write_text("x")
    return tmp_path


def _ssh(monkeypatch, codigo=0, chamadas=None):
    def run(cmd, **_kw):
        if chamadas is not None:
            chamadas.append(cmd)
        return subprocess.CompletedProcess(cmd, codigo, "", "Permission denied" if codigo else "")
    monkeypatch.setattr(st.subprocess, "run", run)


def test_fora_da_11_recusa(monkeypatch):
    monkeypatch.setattr(st, "_na_11", lambda: False)
    with pytest.raises(st.Recusado, match="Só a .11"):
        st.reiniciar()


def test_sem_chave_diz_como_preparar(na_11):
    (na_11 / "k").unlink()
    with pytest.raises(st.Recusado, match="--preparar"):
        st.reiniciar()


def test_sap_de_pe_nunca_reinicia(monkeypatch, na_11):
    chamadas: list = []
    _ssh(monkeypatch, chamadas=chamadas)
    monkeypatch.setattr(st, "porta_aberta", lambda *_a, **_k: True)
    with pytest.raises(st.Recusado, match="já responde"):
        st.reiniciar()
    with pytest.raises(st.Recusado, match="de pé"):
        st.previa()
    with pytest.raises(acoes_agente.AcaoInvalida):
        acoes_agente.CATALOGO["reiniciar_sap_servertools"].previa({})
    assert chamadas == []


def test_reinicia_e_espera_a_40000(monkeypatch, na_11):
    chamadas: list = []
    _ssh(monkeypatch, chamadas=chamadas)
    leituras = iter([False, False, False, True])          # before; then 3 polls until it opens
    monkeypatch.setattr(st, "porta_aberta", lambda *_a, **_k: next(leituras))
    r = st.reiniciar(relogio=_Relogio())
    assert r == {"ok": True, "segundos": 20, "porta_40000": True}
    (cmd,) = chamadas
    assert "BatchMode=yes" in cmd and "StrictHostKeyChecking=yes" in cmd and cmd[-2:] == [
        "root@sapvm", "/usr/bin/systemctl restart sapb1servertools"]


def test_chave_recusada_e_nao_voltou_nunca_sao_ok(monkeypatch, na_11):
    monkeypatch.setattr(st, "porta_aberta", lambda *_a, **_k: False)
    _ssh(monkeypatch, codigo=255)
    r = st.reiniciar(relogio=_Relogio())
    assert r["ok"] is False and "recusou a chave" in r["motivo"]
    _ssh(monkeypatch, codigo=0)
    r = st.reiniciar(relogio=_Relogio())
    assert r["ok"] is False and "não abriu" in r["motivo"]
    monkeypatch.setattr(st, "time", _Relogio())
    ctx = acoes_agente.Contexto("M", "1", "ti")
    ok, dados = acoes_agente.CATALOGO["reiniciar_sap_servertools"].executar({}, {}, ctx)
    assert ok is False and "não abriu" in dados["motivo"]


def test_ensaio_reinicia_mesmo_com_o_sap_de_pe(monkeypatch, na_11):
    chamadas: list = []
    _ssh(monkeypatch, chamadas=chamadas)
    monkeypatch.setattr(st, "porta_aberta", lambda *_a, **_k: True)
    assert st.reiniciar(ensaio=True, relogio=_Relogio())["ok"] is True and len(chamadas) == 1


def test_a_chave_nunca_entra_no_repo():
    assert "state/ssh/" in (RAIZ / ".gitignore").read_text(encoding="utf-8")
    assert st.PASTA == RAIZ / "state" / "ssh"
