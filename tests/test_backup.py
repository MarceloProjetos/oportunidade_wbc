"""operacao/backup.py and its routes (F7 of the web repo's PLANO_TEO_REDE_E_ROTINAS): the ALTHOST's hourly
report of the Veeam backups, pushed with a key of scope backup:relatar."""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from config import reset_settings
from operacao import backup

RELATO = {
    "versao_do_script": 1, "gerado_em": "2026-10-09T11:00:02", "host": "ALTHOST",
    "jobs": [{"nome": "Replica .11", "tipo": "Replica", "habilitado": True, "resultado": "Success", "estado": "Stopped",
              "inicio": "2026-10-08T23:00:01", "fim": "2026-10-08T23:14:40"},
             {"nome": "Replica .12", "tipo": "Replica", "habilitado": True, "resultado": "Failed", "estado": "Stopped",
              "inicio": "2026-10-08T23:15:00", "fim": None}],
    "replicas": [{"job": "Replica .11", "vm": "Integracao", "ultimo_ponto": "2026-10-08T23:14:00"}],
    "repositorios": [{"nome": "Default Backup Repository", "total_gb": 3725.9, "livre_gb": 3200.1}],
    "servicos": [{"nome": "VeeamBackupSvc", "estado": "Running"}],
    "erros": [],
}


def test_validar_guarda_so_a_forma_conhecida():
    r = backup.validar({**RELATO, "extra": "ignorado", "jobs": [{**RELATO["jobs"][0], "nome": "x" * 500,
                                                                  "<script>": "alert(1)"}]})
    assert "extra" not in r and len(r["jobs"][0]["nome"]) == backup.TEXTO_MAX and "<script>" not in r["jobs"][0]
    assert r["repositorios"][0]["livre_gb"] == 3200.1 and r["maquina"] == "ALTHOST"
    assert backup.validar({**RELATO, "jobs": [{**RELATO["jobs"][0], "nome": "Réplica\n<b>.11</b>"}]})["jobs"][0][
        "nome"] == "Réplica <b>.11</b>"                      # text stays text: cut, newlines folded, never parsed


@pytest.mark.parametrize("ruim", [
    [], {"jobs": []}, {**RELATO, "versao_do_script": "1"}, {**RELATO, "jobs": {"a": 1}},
    {**RELATO, "jobs": [1, 2]}, {**RELATO, "erros": {"a": 1}}, {**RELATO, "erros": "texto"},
    {**RELATO, "repositorios": [{"nome": "r", "livre_gb": float("nan")}]},
    {**RELATO, "repositorios": [{"nome": "r", "total_gb": float("inf")}]},
    {**RELATO, "gerado_em": "09/10/2026 11:00"}, {**RELATO, "repositorios": [{"nome": "r", "livre_gb": -1}]},
    {**RELATO, "replicas": [{"vm": {"nested": True}}]},
])
def test_validar_recusa(ruim):
    with pytest.raises(backup.RelatoInvalido):
        backup.validar(ruim)


def test_itens_demais_sao_cortados_e_ditos_nunca_perdem_o_relato():
    r = backup.validar({**RELATO, "jobs": [RELATO["jobs"][0]] * (backup.ITENS_MAX + 5)})
    assert len(r["jobs"]) == backup.ITENS_MAX
    assert r["erros"] == [f"jobs: {backup.ITENS_MAX + 5} itens, só os primeiros {backup.ITENS_MAX} foram guardados"]


def test_script_sinaliza_replica_nao_lida_e_corta_as_listas():
    ps = (backup.RAIZ / "maintenance" / "estado_backup.ps1").read_bytes()
    assert ps.isascii()                                      # ASCII = no BOM either (PS 5.1 reads it as ANSI)
    texto = ps.decode()
    assert "Get-VBRReplica" in texto and "nenhuma lida embora existam" in texto
    assert "Select-Object -First $ITENS_MAX" in texto and "Write-Error" not in texto


def test_gravar_e_ler_com_a_hora_da_11(tmp_path):
    arquivo = tmp_path / "backup.json"
    assert backup.ler(arquivo=arquivo)["relato"] is None
    backup.gravar(backup.validar(RELATO), "192.168.7.250", datetime(2026, 10, 9, 11, 0, 30), arquivo=arquivo)
    r = backup.ler(datetime(2026, 10, 9, 12, 15, 30), arquivo=arquivo)
    assert r["idade_min"] == 75 and r["relato"]["origem"] == "192.168.7.250"
    assert r["relato"]["recebido_em"] == "2026-10-09T11:00:30" and r["relato"]["gerado_em"] == "2026-10-09T11:00:02"
    arquivo.write_text("{lixo", encoding="utf-8")
    assert backup.ler(arquivo=arquivo)["relato"] is None


@pytest.fixture
def api(monkeypatch, tmp_path):
    pytest.importorskip("flask")
    import api as apimod
    from seguranca import credenciais

    monkeypatch.setenv("OS_API_KEY", "mestra-de-teste")
    reset_settings()
    apimod._rate_limiter.reset()
    apimod.app.config.update(TESTING=True)
    monkeypatch.setattr(backup, "ARQUIVO", tmp_path / "backup_althost.json")
    monkeypatch.setattr(backup.gravar, "__defaults__", (None, tmp_path / "backup_althost.json"))
    monkeypatch.setattr(backup.ler, "__defaults__", (None, tmp_path / "backup_althost.json"))
    relator = credenciais.criar("althost-backup", ["backup:relatar"])
    leitor = credenciais.criar("leitor", ["leitura"])
    return apimod.app.test_client(), {"X-API-Key": relator}, {"X-API-Key": leitor}


def _post(c, h, corpo, origem="192.168.7.250", **kw):
    return c.post("/operacao/backup/estado", data=corpo if isinstance(corpo, (bytes, str)) else json.dumps(corpo),
                  headers={**h, "Content-Type": "application/json"}, environ_base={"REMOTE_ADDR": origem}, **kw)


def test_rota_grava_e_a_leitura_devolve(api):
    c, relator, leitor = api
    r = _post(c, relator, RELATO)
    assert r.status_code == 200 and r.get_json() == {"ok": True, "jobs": 2, "replicas": 1}
    lido = c.get("/operacao/backup", headers=leitor).get_json()
    assert lido["ok"] and lido["relato"]["jobs"][1]["resultado"] == "Failed" and lido["idade_min"] == 0


def test_rota_recusa_escopo_origem_tamanho_e_forma(api):
    c, relator, leitor = api
    assert _post(c, leitor, RELATO).status_code == 403                     # wrong scope (reading key)
    assert c.post("/operacao/backup/estado", json=RELATO).status_code == 401
    assert _post(c, relator, RELATO, origem="192.168.0.47").status_code == 403
    import api as apimod

    grande = json.dumps({**RELATO, "erros": ["x" * 70000]})
    assert _post(c, relator, grande).status_code == 413
    apimod._rate_limiter.reset()                          # each refusal below is its own attempt (2/min)
    assert _post(c, relator, b"\xff\xfe nao e json").status_code == 400
    apimod._rate_limiter.reset()
    r = _post(c, relator, {**RELATO, "jobs": "<script>alert(1)</script>"})
    assert r.status_code == 400 and r.get_json()["error"] == "relato_invalido"
    assert c.get("/operacao/backup", headers=relator).status_code == 403   # the reporter cannot read


def test_rota_tem_teto_de_2_por_minuto(api):
    c, relator, _ = api
    assert [_post(c, relator, RELATO).status_code for _ in range(3)] == [200, 200, 429]
