"""operacao/hyperv.py and its routes (F5c3 of the web repo's PLANO_TEO_REDE_E_ROTINAS): each Hyper-V host's
hourly report, pushed with a key of scope hyperv:relatar; the origin names the host."""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from config import reset_settings
from operacao import backup, hyperv

# The ALTSAP as read by hand on 09/10 (Get-VM, Get-VMSnapshot, Get-VMHardDiskDrive, IPv6 neighbours).
RELATO = {
    "versao_do_script": 1, "gerado_em": "2026-10-09T15:00:04", "host": "ALTSAP",
    "vms": [{"nome": "RDS", "estado": "Running", "status": "Operating normally", "heartbeat": "OkApplicationsUnknown",
             "uptime_h": 1.7, "cpu_pct": 1, "memoria_gb": 64.0},
            {"nome": "OneHana", "estado": "Running", "status": "Operating normally",
             "heartbeat": "OkApplicationsUnknown", "uptime_h": 8.9, "cpu_pct": 4, "memoria_gb": 256.0}],
    "checkpoints": [{"vm": "RDS", "nome": "Veeam Recovery Checkpoint (f976.20250206000037)", "tipo": "Recovery",
                     "criado_em": "2025-02-06T00:00:36"}],
    "discos": [{"vm": "RDS", "arquivo": "RDS-new_B107019C.avhdx", "diferencial": True, "cadeia": 3,
                "tamanho_gb": 212.4}],
    "volumes": [{"letra": "D", "total_gb": 7450.0, "livre_gb": 2100.5}],
    "switches": [{"nome": "LAN_VMs-10GB", "tipo": "External", "placa": "Broadcom NetXtreme E-Series 10Gb"}],
    "vmq": [{"placa": "LAN_10GB-1", "ligado": False}],
    "eventos": [{"log": "System", "fonte": "Microsoft-Windows-DNS-Client", "id": 8011, "nivel": "2", "n": 4,
                 "ultimo": "2026-10-09T13:05:08"}],
    "ipv6": {"desligado": False, "descoberta_roteador": False, "rotas": [],
             "vizinhos": [{"ip": "fe80::1", "mac": "D8-44-89-1B-68-F0"}],
             "enderecos": [{"ip": "fd00::d7ab:2a3b:4de2:3b17", "origem": "RouterAdvertisement"}]},
    "erros": [],
}


def test_validar_guarda_so_a_forma_conhecida():
    r = hyperv.validar({**RELATO, "extra": 1, "vms": [{**RELATO["vms"][0], "nome": "x" * 500, "<script>": 1}]})
    assert "extra" not in r and len(r["vms"][0]["nome"]) == backup.TEXTO_MAX and "<script>" not in r["vms"][0]
    assert r["discos"][0]["diferencial"] is True and r["discos"][0]["cadeia"] == 3.0
    assert r["ipv6"]["vizinhos"] == [{"ip": "fe80::1", "mac": "D8-44-89-1B-68-F0"}] and r["maquina"] == "ALTSAP"
    vazio = hyperv.validar({"versao_do_script": 1})          # every block may come empty (the try/catch ate it)
    assert vazio["vms"] == [] and vazio["ipv6"]["rotas"] == [] and vazio["ipv6"]["desligado"] is None


@pytest.mark.parametrize("ruim", [
    [], {"vms": []}, {**RELATO, "versao_do_script": True}, {**RELATO, "vms": {"a": 1}}, {**RELATO, "ipv6": []},
    {**RELATO, "erros": "x"}, {**RELATO, "discos": [{"diferencial": "sim"}]},
    {**RELATO, "volumes": [{"livre_gb": float("nan")}]}, {**RELATO, "checkpoints": [{"criado_em": "06/02/2025"}]},
    {**RELATO, "ipv6": {"vizinhos": "fe80::1"}}, {**RELATO, "vms": [{"cpu_pct": -1}]},
])
def test_validar_recusa(ruim):
    with pytest.raises(hyperv.RelatoInvalido):
        hyperv.validar(ruim)


def test_listas_longas_sao_cortadas_e_ditas():
    r = hyperv.validar({**RELATO, "ipv6": {"vizinhos": [{"ip": "fe80::1"}] * 15},
                        "eventos": [RELATO["eventos"][0]] * (backup.ITENS_MAX + 1)})
    assert len(r["ipv6"]["vizinhos"]) == 10 and len(r["eventos"]) == backup.ITENS_MAX
    assert any("ipv6.vizinhos: 15 itens" in e for e in r["erros"]) and any(e.startswith("eventos:") for e in r["erros"])


def test_script_e_ascii_so_le_e_corta():
    ps = (backup.RAIZ / "maintenance" / "estado_hyperv.ps1").read_bytes()
    assert ps.isascii()
    texto = ps.decode()
    for leitura in ("Get-VM", "Get-VMSnapshot", "Get-VHD", "Get-VMSwitch", "Get-NetNeighbor", "Get-WinEvent"):
        assert leitura in texto
    for escrita in ("Set-VM", "Remove-VM", "Stop-VM", "Restart-", "Merge-VHD", "Set-ItemProperty", "Write-Error"):
        assert escrita not in texto
    assert "Select-Object -First $ITENS_MAX" in texto and "hyperv_chave.txt" in texto


def test_gravar_por_origem_e_ler_todos(tmp_path):
    assert hyperv.ler(pasta=tmp_path)["hosts"]["altsap"]["relato"] is None
    assert hyperv.gravar(hyperv.validar(RELATO), "192.168.7.253", datetime(2026, 10, 9, 15, 0, 9),
                         pasta=tmp_path) == "altsap"
    lido = hyperv.ler(datetime(2026, 10, 9, 15, 30, 9), pasta=tmp_path)["hosts"]
    assert lido["altsap"]["idade_min"] == 30 and lido["altsap"]["relato"]["vms"][0]["nome"] == "RDS"
    assert lido["althost"]["relato"] is None and "ALTHOST ainda não mandou" in lido["althost"]["motivo"]
    with pytest.raises(KeyError):
        hyperv.gravar(hyperv.validar(RELATO), "192.168.0.47", pasta=tmp_path)


@pytest.fixture
def api(monkeypatch, tmp_path):
    pytest.importorskip("flask")
    import api as apimod
    from seguranca import credenciais

    monkeypatch.setenv("OS_API_KEY", "mestra-de-teste")
    reset_settings()
    apimod._rate_limiter.reset()
    apimod.app.config.update(TESTING=True)
    monkeypatch.setattr(hyperv, "PASTA", tmp_path)
    relator = credenciais.criar("altsap-hyperv", ["hyperv:relatar"])
    backup_ = credenciais.criar("althost-backup", ["backup:relatar"])
    leitor = credenciais.criar("leitor", ["leitura"])
    return apimod.app.test_client(), {"X-API-Key": relator}, {"X-API-Key": leitor}, {"X-API-Key": backup_}


def _post(c, h, corpo, origem="192.168.7.253"):
    return c.post("/operacao/hyperv/estado", data=corpo if isinstance(corpo, (bytes, str)) else json.dumps(corpo),
                  headers={**h, "Content-Type": "application/json"}, environ_base={"REMOTE_ADDR": origem})


def test_rota_grava_pelo_host_da_origem(api):
    c, relator, leitor, _ = api
    r = _post(c, relator, RELATO)
    assert r.status_code == 200 and r.get_json() == {"ok": True, "host": "altsap", "vms": 2, "checkpoints": 1}
    assert _post(c, relator, {**RELATO, "host": "ALTSAP-falso"}, origem="192.168.7.250").get_json()["host"] == "althost"
    lido = c.get("/operacao/hyperv", headers=leitor).get_json()
    assert lido["hosts"]["altsap"]["relato"]["checkpoints"][0]["criado_em"] == "2025-02-06T00:00:36"
    assert lido["hosts"]["althost"]["relato"]["maquina"] == "ALTSAP-falso"   # name = data; file = origin


def test_rota_recusa_escopo_origem_tamanho_e_forma(api):
    c, relator, leitor, chave_backup = api
    assert _post(c, leitor, RELATO).status_code == 403
    assert _post(c, chave_backup, RELATO).status_code == 403                     # the backup key cannot report a host
    assert c.post("/operacao/hyperv/estado", json=RELATO).status_code == 401
    assert _post(c, relator, RELATO, origem="192.168.7.11").status_code == 403
    assert _post(c, relator, json.dumps({**RELATO, "erros": ["x" * 70000]})).status_code == 413
    import api as apimod

    apimod._rate_limiter.reset()
    r = _post(c, relator, {**RELATO, "vms": "<script>"})
    assert r.status_code == 400 and r.get_json()["error"] == "relato_invalido"
    assert c.get("/operacao/hyperv", headers=relator).status_code == 403


def test_rota_tem_teto_de_4_por_minuto(api):
    c, relator, _, _ = api
    assert [_post(c, relator, RELATO).status_code for _ in range(5)] == [200, 200, 200, 200, 429]
