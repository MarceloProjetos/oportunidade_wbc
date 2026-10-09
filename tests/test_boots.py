"""operacao/boots.py (F5 of web PLANO_TEO_REDE_E_ROTINAS). Sibling test, IDENTICAL in SAP_RDP and
ServidorIntegracaoSAP: the readings below are the real shapes seen on the .12 (08-09/10) and on a
Windows 11 notebook (1074 by shutdown.exe and by the Start menu)."""
from __future__ import annotations

import pytest

from operacao import boots

KG = boots.KERNEL_GENERAL


def _e(quando, id_, fonte, p=(), caiu=None):
    return {"quando": quando, "id": id_, "fonte": fonte, "p": list(p), "caiu": caiu}


#: The .12 on 08/10-09/10: a morning boot, two forced power-offs from Hyper-V, the nightly boot.
DOZE = [
    _e("2026-10-08T09:54:10", 12, KG),
    _e("2026-10-08T09:54:13", 42, "Microsoft-Windows-Hyper-V-Hypervisor"),          # noise, ignored
    _e("2026-10-08T16:22:20", 12, KG),
    _e("2026-10-08T16:22:36", 41, "Microsoft-Windows-Kernel-Power", ["0", "0"]),
    _e("2026-10-08T16:22:59", 6008, "EventLog", ["16:16:02", "\u200e08/\u200e10/\u200e2026"], "2026-10-08T16:16:02"),
    _e("2026-10-08T16:47:10", 12, KG),
    _e("2026-10-08T16:47:23", 41, "Microsoft-Windows-Kernel-Power", ["0", "0"]),
    _e("2026-10-08T16:47:44", 6008, "EventLog", ["16:46:59", "\u200e08/\u200e10/\u200e2026"], "2026-10-08T16:46:59"),
    _e("2026-10-09T00:05:50", 6006, "EventLog"),
    _e("2026-10-09T00:06:39", 12, KG),
    _e("2026-10-09T00:06:55", 6005, "EventLog"),
    _e("2026-10-09T00:06:41", 12, "Microsoft-Windows-Wininit", ["4"]),                 # same id, other source
]


def test_a_12_em_08_10_dois_desligamentos_forcados_e_o_boot_da_noite():
    lista = boots.por_boot(DOZE, "2026-10-09T00:06:39")
    assert [b["ligou"] for b in lista] == ["2026-10-09T00:06:39", "2026-10-08T16:47:10", "2026-10-08T16:22:20",
                                           "2026-10-08T09:54:10"]
    noite, segundo, primeiro, manha = lista
    assert (noite["como"], noite["desligou_em"], noite["fora_min"]) == ("normal", "2026-10-09T00:05:50", 1)
    assert (segundo["como"], segundo["desligou_em"], segundo["fora_min"]) == ("forcado", "2026-10-08T16:46:59", 0)
    assert (primeiro["como"], primeiro["fora_min"]) == ("forcado", 6)
    assert "à força (no Hyper-V" in primeiro["frase"] and primeiro["pedido"] is None
    assert manha["como"] == "sem_registro"   # its previous life is older than the window


def test_pedido_por_conta_de_usuario_e_pelo_menu_iniciar():
    lista = boots.por_boot([
        _e("2026-10-07T18:16:36", 1074, "User32", [r"C:\WINDOWS\system32\shutdown.exe (TECINFO02)", "TECINFO02",
                                                    "Não foi encontrado um título para esta razão", "0x800000ff",
                                                    "Desligar o sistema", "", r"GRUPO_ALTAMIRA\marcelo.miranda"]),
        _e("2026-10-07T18:17:49", 6006, "EventLog"),
        _e("2026-10-07T18:17:55", 13, KG, ["10/07/2026 18:17:55"]),
        _e("2026-10-08T07:23:06", 12, KG),
        _e("2026-10-08T10:27:50", 1074, "User32", [
            r"C:\WINDOWS\SystemApps\Microsoft.Windows.StartMenuExperienceHost_cw5n1h2txyewy"
            r"\StartMenuExperienceHost.exe (TECINFO02)", "TECINFO02", "Outro (não planejada)", "0x0", "reiniciar", "",
            r"GRUPO_ALTAMIRA\marcelo.miranda"]),
        _e("2026-10-08T10:28:05", 6006, "EventLog"),
        _e("2026-10-08T10:28:28", 12, KG),
    ], "2026-10-08T10:28:27")
    reinicio, manha = lista
    assert reinicio["como"] == "pedido" and reinicio["pedido"]["categoria"] == "usuario"
    assert reinicio["pedido"]["tipo"] == "reiniciar" and reinicio["frase"] == "reiniciada por uma conta de usuário"
    assert reinicio["pedido"]["processo"] == "StartMenuExperienceHost.exe"
    assert reinicio["pedido"]["conta"] == r"GRUPO_ALTAMIRA\marcelo.miranda"
    assert "marcelo" not in reinicio["frase"] and "GRUPO" not in reinicio["frase"]
    assert manha["como"] == "pedido" and manha["pedido"]["tipo"] == "desligar"
    assert manha["desligou_em"] == "2026-10-07T18:17:55" and manha["fora_min"] == 785


@pytest.mark.parametrize(("processo", "motivo", "comentario", "conta", "esperada"), [
    (r"C:\Windows\servicing\TrustedInstaller.exe", "Sistema operacional: Service pack (planejado)", "",
     r"NT AUTHORITY\SYSTEM", "atualizacao"),
    (r"C:\Windows\System32\MoUsoCoreWorker.exe", "Outro (planejado)", "", r"AUTORIDADE NT\SISTEMA", "atualizacao"),
    (r"C:\Windows\System32\svchost.exe", "Outro (planejado)", "Initiated by Hyper-V shutdown", r"NT AUTHORITY\SYSTEM",
     "hyperv"),
    (r"C:\Windows\system32\shutdown.exe", "Outro (planejado)", "Fim do dia", r"NT AUTHORITY\SYSTEM", "tarefa"),
    (r"C:\Windows\system32\winlogon.exe", "Outro (planejado)", "", r"AUTORIDADE NT\SISTEMA", "sistema"),
    (r"C:\Windows\system32\shutdown.exe", "Outro (planejado)", "", r"SAPBUSINESSONER\administrador", "usuario"),
    (r"C:\Windows\system32\svchost.exe", "Outro (planejado)", "", r"NT AUTHORITY\NETWORK SERVICE", "sistema"),
    (r"C:\Windows\system32\winlogon.exe", "Outro (planejado)", "", r"AUTORIDADE NT\SISTEMA", "sistema"),
    (r"C:\Windows\system32\svchost.exe", "Outro (planejado)", "", r"AUTORIDADE NT\SERVIÇO LOCAL", "sistema"),
])
def test_categoria_de_quem_pediu(processo, motivo, comentario, conta, esperada):
    assert boots.categoria(processo, motivo, comentario, conta) == esperada


def test_hyperv_desliga_a_vm_pelo_svchost():
    """Read on the .12 on 09/10: the host's nightly shutdown and the stops of 08/10 come as svchost.exe,
    SYSTEM, "Outro (planejado)", 0x80000000, no comment -- the Hyper-V guest shutdown service."""
    lista = boots.por_boot([
        _e("2026-10-08T06:17:16", 12, KG),
        _e("2026-10-08T09:49:55", 1074, "User32", [r"C:\Windows\system32\svchost.exe (SAPBUSINESSONER)",
                                                    "SAPBUSINESSONER", "Outro (planejado)", "0x80000000", "desligar",
                                                    "", r"AUTORIDADE NT\SISTEMA"]),
        _e("2026-10-08T09:50:12", 6006, "EventLog"),
        _e("2026-10-08T09:54:13", 12, KG),
    ], None)
    assert lista[0]["como"] == "pedido" and lista[0]["pedido"]["categoria"] == "hyperv"
    assert lista[0]["frase"] == "desligada pelo Hyper-V (host)" and lista[0]["pedido"]["codigo_motivo"] == "0x80000000"
    # Same svchost with another reason code (an update's restart) is not the Hyper-V.
    assert boots.categoria(r"C:\Windows\system32\svchost.exe", "Outro (planejado)", "", r"NT AUTHORITY\SYSTEM",
                           "0x80020010") == "sistema"


def test_pedido_que_nao_terminou_e_tela_azul():
    lista = boots.por_boot([
        _e("2026-10-08T08:00:00", 12, KG),
        _e("2026-10-08T12:00:00", 1074, "User32", [r"C:\Windows\servicing\TrustedInstaller.exe (X)", "X",
                                                    "Sistema operacional: Service pack (planejado)", "0x80020010",
                                                    "reiniciar", "", r"NT AUTHORITY\SYSTEM"]),
        _e("2026-10-08T12:30:00", 12, KG),
        _e("2026-10-08T12:30:20", 41, "Microsoft-Windows-Kernel-Power"),
        _e("2026-10-08T15:00:00", 12, KG),
        _e("2026-10-08T15:00:40", 1001, "Microsoft-Windows-WER-SystemErrorReporting",
           ["0x0000009f (0x0000000000000003, 0xffff)", "C:\\Windows\\MEMORY.DMP", "x"]),
        _e("2026-10-08T15:00:41", 1001, "Windows Error Reporting", ["outro"]),       # Application-style source
    ], None)
    azul, travou, _manha = lista
    assert (azul["como"], azul["tela_azul"]) == ("tela_azul", "0x0000009f")
    assert azul["frase"] == "o Windows travou com tela azul (0x0000009f)"
    assert travou["como"] == "pedido_travou" and travou["pedido"]["categoria"] == "atualizacao"
    assert travou["frase"].startswith("o desligamento foi pedido por uma atualização do Windows, mas não terminou")


def test_boot_sem_kernel_general_e_formatos_do_convertto_json():
    # A 6005 with no 12 next to it is a boot; LastBootUpTime alone too; a 1-item array comes as str
    # (and with no account the requester is the system).
    lista = boots.por_boot([
        _e("2026-10-08T09:00:00", 6005, "EventLog"),
        {"quando": "2026-10-08T11:00:00", "id": 1074, "fonte": "User32", "p": "processo.exe (X)", "caiu": None},
    ], "2026-10-08T11:05:00")
    assert [b["ligou"] for b in lista] == ["2026-10-08T11:05:00", "2026-10-08T09:00:00"]
    assert lista[0]["como"] == "pedido" and lista[0]["pedido"]["processo"] == "processo.exe"
    assert lista[0]["pedido"]["tipo"] is None and lista[0]["frase"] == "desligada pelo próprio Windows"
    assert boots.por_boot([], None) == []


def test_pedido_de_horas_antes_nao_explica_a_queda_forcada():
    """Review of F5a: a shutdown asked (and cancelled) at 09:00 and a forced power-off at 16:24 is
    "forcado", not "the shutdown was asked and did not finish"; a 41 stamped before the 12 counts."""
    lista = boots.por_boot([
        _e("2026-10-08T08:00:00", 12, KG),
        _e("2026-10-08T09:00:00", 1074, "User32", [r"C:\Windows\system32\shutdown.exe (X)", "X", "Outro", "0x0",
                                                    "reiniciar", "", r"GRUPO\fulano"]),
        _e("2026-10-08T16:23:50", 41, "Microsoft-Windows-Kernel-Power"),
        _e("2026-10-08T16:24:00", 12, KG),
        _e("2026-10-08T16:24:30", 6008, "EventLog", ["16:20:00", "08/10/2026"], "2026-10-08T16:20:00"),
    ], None)
    assert lista[0]["como"] == "forcado" and lista[0]["pedido"] is None
    assert lista[0]["desligou_em"] == "2026-10-08T16:20:00"


def test_laco_de_reinicio_cada_41_e_de_um_boot_so():
    """Two boots 5 min apart: the 41 stamped just before the second belongs to the second only."""
    lista = boots.por_boot([
        _e("2026-10-08T10:00:00", 12, KG),
        _e("2026-10-08T10:04:30", 6006, "EventLog"),
        _e("2026-10-08T10:04:50", 41, "Microsoft-Windows-Kernel-Power"),
        _e("2026-10-08T10:05:00", 12, KG),
    ], None)
    assert [b["como"] for b in lista] == ["forcado", "sem_registro"]


def test_6008_fora_da_vida_e_ignorado():
    """A 6008 whose parsed time does not fall in the life it belongs to (another culture) is dropped."""
    lista = boots.por_boot([
        _e("2026-10-08T08:00:00", 12, KG),
        _e("2026-10-08T16:47:10", 12, KG),
        _e("2026-10-08T16:47:44", 6008, "EventLog", ["16:46:59", "10/08/2026"], "2026-08-10T16:46:59"),
    ], None)
    assert lista[0]["como"] == "forcado" and lista[0]["desligou_em"] is None and lista[0]["fora_min"] is None


@pytest.fixture
def ps(monkeypatch):
    chamadas = []
    monkeypatch.setattr(boots, "_cache", {"em": 0.0, "dados": None})

    def _definir(dados, erro=None):
        def _rodar(script, timeout):
            chamadas.append((script, timeout))
            return dados, erro
        monkeypatch.setattr(boots.windows_update, "_rodar_ps", _rodar)
    return chamadas, _definir


def test_boots_cache_e_erros(ps):
    chamadas, definir = ps
    definir({"boot": "2026-10-09T00:06:39", "eventos": DOZE})
    r = boots.boots()
    assert r["disponivel"] is True and r["ultimo_boot"] == "2026-10-09T00:06:39" and len(r["boots"]) == 4
    assert r["eventos_lidos"] == len(DOZE) and r["cache"] is False
    assert boots.boots()["cache"] is True and chamadas == [(boots._PS, boots.TIMEOUT_S)]
    boots._cache.update(em=0.0, dados=None)
    definir({"boot": "2026-10-09T00:06:39", "eventos": {"erro": "acesso negado"}})
    r = boots.boots()
    assert r["boots"] == [] and r["motivo_eventos"] == "acesso negado" and r["ultimo_boot"] == "2026-10-09T00:06:39"
    boots._cache.update(em=0.0, dados=None)
    definir(None, "a coleta passou de 25s")
    assert boots.boots() == {"disponivel": False, "motivo": "a coleta passou de 25s"}
    antes = len(chamadas)
    assert boots.boots()["cache"] is True and len(chamadas) == antes   # a hung log is not asked again at once


def test_script_e_ascii_sem_aspas_duplas():
    assert boots._PS.isascii() and '"' not in boots._PS
    for evento_id in (12, 13, 41, 1001, 1074, 1076, 6005, 6006, 6008):
        assert str(evento_id) in boots._PS
    assert {i for _f, i in boots.EVENTOS} == {12, 13, 41, 1001, 1074, 1076, 6005, 6006, 6008}
