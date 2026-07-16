"""Testes do windows_update.py.

O foco é a invariante nº 1 do PLANO_WINDOWS_UPDATE, que a .12 provou não ser teórica:
com o agente sem varrer, a busca RESPONDE (22,5s), diz **0** e mente. Não há exceção
para capturar. Publicar esse 0 faria a Mira exibir "0 updates pendentes ✅" num servidor
com 611 dias de atraso.

Portado do repo SAP_RDP junto com o módulo — os dois devem ser mantidos diffáveis.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

import windows_update as wu
from config import reset_settings


def _agora() -> datetime:
    """UTC ingenuo, como o modulo faz (utcnow() e deprecado no 3.12+)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture(autouse=True)
def _limpo(monkeypatch):
    monkeypatch.delenv("WU_VARREDURA_MAX_D", raising=False)
    monkeypatch.delenv("WU_DELAY_START_S", raising=False)
    monkeypatch.delenv("WU_ENABLED", raising=False)
    reset_settings()
    wu.reset_cache()
    yield
    reset_settings()
    wu.reset_cache()


def _fake_ps(monkeypatch, payload: dict, returncode: int = 0):
    """Substitui o subprocess do powershell — os testes nunca disparam PS de verdade."""
    def _run(cmd, capture_output=False, timeout=None):
        assert cmd[0] == "powershell.exe"
        assert "-NonInteractive" in cmd
        saida = json.dumps(payload).encode("utf-8")
        return subprocess.CompletedProcess(cmd, returncode, saida, b"")

    monkeypatch.setattr(wu.subprocess, "run", _run)


# ── A invariante nº 1: nunca "0" quando é "não sei" ──────────────────────


def test_varredura_velha_nao_publica_contagem(monkeypatch):
    """O caso REAL da .12: varredura de 610 dias -> a contagem seria mentira.

    O PowerShell nem chega a rodar a busca cara nesse caso (ele checa a data antes),
    então `pendentes` chega None com o motivo. O que NÃO pode acontecer é virar 0.
    """
    velha = (_agora() - timedelta(days=610)).isoformat()
    _fake_ps(monkeypatch, {
        "ok": True, "erro": None,
        "ultima_varredura": velha,
        "pendentes": None,
        "pendentes_motivo": "o agente nao varre ha tempo demais - a contagem seria mentira",
        "ultimo_patch": (_agora() - timedelta(days=610)).isoformat(),
        "ultimo_patch_kb": "KB5046615",
    })
    r = wu.coletar_updates()
    assert r["pendentes"] is None, "contagem de varredura velha NÃO pode ser publicada"
    assert "mentira" in r["pendentes_motivo"]
    assert r["varredura_dias"] == pytest.approx(610, abs=1)
    assert r["dias_sem_patch"] == pytest.approx(610, abs=1)
    assert r["ultimo_patch_kb"] == "KB5046615"


def test_varredura_recente_publica_contagem(monkeypatch):
    """Controle: com varredura de hoje, o número é verdade e vai para o payload.

    Os valores são os REAIS medidos nesta máquina (.11) na F0: 3 pendentes, patch de 16 dias.
    """
    _fake_ps(monkeypatch, {
        "ok": True, "erro": None,
        "ultima_varredura": _agora().isoformat(),
        "pendentes": 3,
        "pendentes_motivo": None,
        "ultimo_patch": (_agora() - timedelta(days=16)).isoformat(),
        "ultimo_patch_kb": "KB5094147",
    })
    r = wu.coletar_updates()
    assert r["pendentes"] == 3
    assert r["pendentes_motivo"] is None
    assert r["dias_sem_patch"] == pytest.approx(16, abs=1)


def test_zero_pendentes_com_varredura_recente_e_verdade(monkeypatch):
    """0 legítimo (varreu hoje e não há nada) precisa passar — senão o campo é inútil."""
    _fake_ps(monkeypatch, {
        "ok": True, "erro": None,
        "ultima_varredura": _agora().isoformat(),
        "pendentes": 0, "pendentes_motivo": None,
        "ultimo_patch": None, "ultimo_patch_kb": None,
    })
    r = wu.coletar_updates()
    assert r["pendentes"] == 0  # zero de verdade != None


def test_sem_varredura_nenhuma_nao_publica(monkeypatch):
    _fake_ps(monkeypatch, {
        "ok": True, "erro": None, "ultima_varredura": None,
        "pendentes": None, "pendentes_motivo": "o agente nao varre ha tempo demais",
        "ultimo_patch": None, "ultimo_patch_kb": None,
    })
    r = wu.coletar_updates()
    assert r["pendentes"] is None
    assert r["ultima_varredura"] is None
    assert r["varredura_dias"] is None


# ── Falha da coleta nunca vira 0 ─────────────────────────────────────────


def test_powershell_falhou_nao_vira_zero(monkeypatch):
    def _run(cmd, capture_output=False, timeout=None):
        return subprocess.CompletedProcess(cmd, 1, b"", b"boom")
    monkeypatch.setattr(wu.subprocess, "run", _run)
    r = wu.coletar_updates()
    assert r["pendentes"] is None
    assert r["erro"] and "boom" in r["erro"]


def test_timeout_nao_vira_zero(monkeypatch):
    def _run(cmd, capture_output=False, timeout=None):
        raise subprocess.TimeoutExpired(cmd, timeout)
    monkeypatch.setattr(wu.subprocess, "run", _run)
    r = wu.coletar_updates()
    assert r["pendentes"] is None
    assert "passou de" in r["erro"]


def test_saida_nao_json_nao_vira_zero(monkeypatch):
    def _run(cmd, capture_output=False, timeout=None):
        return subprocess.CompletedProcess(cmd, 0, b"isto nao e json", b"")
    monkeypatch.setattr(wu.subprocess, "run", _run)
    r = wu.coletar_updates()
    assert r["pendentes"] is None
    assert "não-JSON" in r["erro"]


# ── Reboot pendente: tri-estado (erro NUNCA vira False) ──────────────────


def test_reboot_erro_de_leitura_nao_vira_false(monkeypatch):
    """Acesso negado tem de virar None. Colapsar em False faz a API AFIRMAR
    "sem reboot pendente" — falso negativo silencioso no dado mais crítico."""
    monkeypatch.setattr(wu, "_chave_existe", lambda _c: None)
    monkeypatch.setattr(wu, "_valor", lambda *_a: (_ for _ in ()).throw(OSError("negado")))
    r = wu.reboot_pendente()
    assert r["pendente"] is None, "erro de leitura NÃO pode virar 'sem reboot pendente'"
    assert r["erro"]


def test_reboot_confirmado_vence_sonda_que_falhou(monkeypatch):
    """Se UMA sonda confirma, sabemos que HÁ reboot — mesmo com outra falhando."""
    monkeypatch.setattr(wu, "_chave_existe",
                        lambda c: True if "Component Based" in c else None)
    monkeypatch.setattr(wu, "_valor", lambda *_a: (_ for _ in ()).throw(OSError("negado")))
    r = wu.reboot_pendente()
    assert r["pendente"] is True
    assert "CBS" in r["motivos"]


def test_reboot_ausencia_de_chave_e_fato(monkeypatch):
    monkeypatch.setattr(wu, "_chave_existe", lambda _c: False)
    monkeypatch.setattr(wu, "_valor", lambda *_a: (_ for _ in ()).throw(FileNotFoundError()))
    r = wu.reboot_pendente()
    assert r["pendente"] is False and r["erro"] is None


def test_pendente_file_rename_entra_como_motivo(monkeypatch):
    """O caso REAL desta máquina na F0: reboot pendente por PFRO(32)."""
    monkeypatch.setattr(wu, "_chave_existe", lambda _c: False)
    monkeypatch.setattr(wu, "_valor", lambda *_a: ["a", "b", "c"])
    r = wu.reboot_pendente()
    assert r["pendente"] is True
    assert "PendingFileRenameOperations(3)" in r["motivos"]


# ── patching_automatico: Disabled != Manual ──────────────────────────────


@pytest.mark.parametrize(("start", "esperado"), [(2, True), (3, True), (4, False)])
def test_patching_automatico_so_disabled_e_false(start, esperado, monkeypatch):
    """Start=3 (Manual/trigger-start) é o DEFAULT e não é problema. Só o 4 (Disabled)
    significa que a máquina não se atualiza — foi assim que a .12 ficou 610 dias."""
    monkeypatch.setattr(wu, "_valor", lambda *_a: start)
    assert wu.patching_automatico() is esperado


def test_patching_automatico_erro_vira_none(monkeypatch):
    monkeypatch.setattr(wu, "_valor", lambda *_a: (_ for _ in ()).throw(OSError()))
    assert wu.patching_automatico() is None


# ── estado_updates: o que o /status publica ──────────────────────────────


def test_antes_da_primeira_coleta_diz_coletando(monkeypatch):
    monkeypatch.setattr(wu, "patching_automatico", lambda: True)
    wu.reset_cache()
    wu._coletando = True
    e = wu.estado_updates()
    assert e["estado"] == "coletando"
    assert e["pendentes"] is None  # nunca 0 antes de saber
    assert "ainda não terminou" in e["pendentes_motivo"]


def test_sem_coleta_nenhuma_diz_indisponivel(monkeypatch):
    monkeypatch.setattr(wu, "patching_automatico", lambda: True)
    e = wu.estado_updates()
    assert e["estado"] == "indisponivel"
    assert e["pendentes"] is None


def test_estado_updates_nao_coleta_nunca(monkeypatch):
    """O /status NÃO pode pagar os 3-30s da busca: estado_updates só LÊ o cache."""
    chamou = {"n": 0}

    def _boom(cmd, **_k):
        chamou["n"] += 1
        raise AssertionError("estado_updates() disparou PowerShell — isso trava o /status")

    monkeypatch.setattr(wu.subprocess, "run", _boom)
    monkeypatch.setattr(wu, "patching_automatico", lambda: True)
    wu.estado_updates()
    assert chamou["n"] == 0


def test_coletor_desabilitado_nao_cria_thread(monkeypatch):
    monkeypatch.setenv("WU_ENABLED", "false")
    reset_settings()
    assert wu.iniciar_coletor() is None


def test_worker_guarda_no_cache_e_estado_le(monkeypatch):
    monkeypatch.setattr(wu, "patching_automatico", lambda: False)
    _fake_ps(monkeypatch, {
        "ok": True, "erro": None,
        "ultima_varredura": _agora().isoformat(),
        "pendentes": 7, "pendentes_motivo": None,
        "ultimo_patch": (_agora() - timedelta(days=2)).isoformat(),
        "ultimo_patch_kb": "KB1",
    })
    wu._worker(0.0, wu.get_settings())
    e = wu.estado_updates()
    assert e["estado"] == "ok"
    assert e["pendentes"] == 7
    assert e["patching_automatico"] is False  # o campo vem do winreg, não do cache


# ── Defesa em profundidade: o Python NÃO confia cegamente no PowerShell ──


def test_python_recusa_contagem_mesmo_se_o_ps_mandar_numero(monkeypatch):
    """Se alguém quebrar a checagem de frescor no _PS_COLETA, a invariante ainda vale.

    Cenário: o PS (bugado/adulterado) devolve pendentes=0 COM varredura de 610 dias.
    Sem a dupla checagem, a Mira exibiria "0 updates pendentes ✅" na .12.
    """
    _fake_ps(monkeypatch, {
        "ok": True, "erro": None,
        "ultima_varredura": (_agora() - timedelta(days=610)).isoformat(),
        "pendentes": 0,            # <- o PS mentiu (ou foi adulterado)
        "pendentes_motivo": None,
        "ultimo_patch": None, "ultimo_patch_kb": None,
    })
    r = wu.coletar_updates()
    assert r["pendentes"] is None, "o Python tem de recusar mesmo o PS mandando número"
    assert "610 dias" in r["pendentes_motivo"]


def test_python_recusa_contagem_sem_varredura_mesmo_com_numero(monkeypatch):
    _fake_ps(monkeypatch, {
        "ok": True, "erro": None, "ultima_varredura": None,
        "pendentes": 42, "pendentes_motivo": None,
        "ultimo_patch": None, "ultimo_patch_kb": None,
    })
    r = wu.coletar_updates()
    assert r["pendentes"] is None
    assert "nunca varreu" in r["pendentes_motivo"]


def test_contagem_lixo_nao_vira_verdade(monkeypatch):
    _fake_ps(monkeypatch, {
        "ok": True, "erro": None,
        "ultima_varredura": _agora().isoformat(),
        "pendentes": "muitos", "pendentes_motivo": None,
        "ultimo_patch": None, "ultimo_patch_kb": None,
    })
    r = wu.coletar_updates()
    assert r["pendentes"] is None
    assert "inesperada" in r["pendentes_motivo"]


def test_data_de_varredura_invalida_nao_vira_recente(monkeypatch):
    """ISO quebrado -> _iso_para_dt devolve None -> tem de virar 'não sei', não 'fresca'."""
    _fake_ps(monkeypatch, {
        "ok": True, "erro": None, "ultima_varredura": "nao-e-data",
        "pendentes": 0, "pendentes_motivo": None,
        "ultimo_patch": None, "ultimo_patch_kb": None,
    })
    r = wu.coletar_updates()
    assert r["pendentes"] is None
    assert r["varredura_dias"] is None


# ── O limite do .env: sem truncar e sem injeção ─────────────────────────


def test_limite_nao_e_truncado_no_powershell(monkeypatch):
    """7.9 no .env tem de chegar 7.9 ao PS. int() fazia o PS usar 7 e divergir daqui."""
    monkeypatch.setenv("WU_VARREDURA_MAX_D", "7.9")
    reset_settings()
    visto = {}

    def _run(cmd, capture_output=False, timeout=None):
        visto["script"] = cmd[-1]
        return subprocess.CompletedProcess(cmd, 0, b'{"ok":true}', b"")

    monkeypatch.setattr(wu.subprocess, "run", _run)
    wu.coletar_updates()
    assert "7.9" in visto["script"] and "__MAX_DIAS__" not in visto["script"]


def test_limite_malicioso_no_env_nao_injeta_powershell(monkeypatch):
    """O valor entra no script por replace: só pode ser literal numérico.

    `config._env_float` rejeita lixo e cai no default — esta é a guarda. Se alguém trocar
    por leitura crua um dia, este teste quebra e explica o porquê.
    """
    monkeypatch.setenv("WU_VARREDURA_MAX_D", r"7; Remove-Item C:\ -Recurse -Force; #")
    reset_settings()
    visto = {}

    def _run(cmd, capture_output=False, timeout=None):
        visto["script"] = cmd[-1]
        return subprocess.CompletedProcess(cmd, 0, b'{"ok":true}', b"")

    monkeypatch.setattr(wu.subprocess, "run", _run)
    wu.coletar_updates()
    assert "Remove-Item" not in visto["script"]
    assert "7.0" in visto["script"]  # caiu no default


# ── Bugs achados pela revisão adversarial (F1) ──────────────────────────


def test_delay_negativo_nao_mata_a_thread(monkeypatch):
    """`_env_float` aceita -1, mas `time.sleep(-1)` levanta ValueError.

    Sem o saneamento a thread morria ANTES do _guardar, `_coletando` ficava preso em True
    e o /status dizia "coletando..." PARA SEMPRE - mentira permanente e silenciosa causada
    por um .env torto. (nan/inf caem no default de 300s: cobertos no teste unitario de
    _delay_valido, que nao precisa esperar 5 minutos para provar.)
    """
    monkeypatch.setenv("WU_DELAY_START_S", "-1")
    reset_settings()
    _fake_ps(monkeypatch, {
        "ok": True, "erro": None, "ultima_varredura": _agora().isoformat(),
        "pendentes": 1, "pendentes_motivo": None,
        "ultimo_patch": None, "ultimo_patch_kb": None,
    })
    t = wu.iniciar_coletor()
    t.join(timeout=5)
    assert not t.is_alive(), "a thread morreu no sleep e travou o estado em 'coletando'"
    e = wu.estado_updates()
    assert e["estado"] == "ok" and e["pendentes"] == 1


def test_delay_valido_sanea_todos_os_modos():
    """Os 3 modos reproduzidos: -1 -> ValueError, nan -> ValueError, inf -> OverflowError."""
    assert wu._delay_valido(-5.0) == 0.0
    assert wu._delay_valido(float("nan")) == wu.WU_DELAY_START_S_DEFAULT
    assert wu._delay_valido(float("inf")) == wu.WU_DELAY_START_S_DEFAULT
    assert wu._delay_valido(float("-inf")) == wu.WU_DELAY_START_S_DEFAULT
    assert wu._delay_valido(12.5) == 12.5


def test_excecao_na_coleta_libera_o_coletando(monkeypatch):
    """Se a coleta explodir, o estado não pode ficar preso em 'coletando'."""
    monkeypatch.setenv("WU_DELAY_START_S", "0")
    reset_settings()
    monkeypatch.setattr(wu, "coletar_updates", lambda _s: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(wu, "patching_automatico", lambda: True)
    t = wu.iniciar_coletor()
    t.join(timeout=5)
    e = wu.estado_updates()
    assert e["estado"] != "coletando"
    assert e["pendentes"] is None
    assert "boom" in e["pendentes_motivo"]


def test_powershell_forca_utf8_na_primeira_linha():
    """O default do PS é cp850 — medido. Sem forçar, "Atualização" vira "Atualiza??o".

    O campo `erro` vem de mensagem do Windows, que é LOCALIZADA (na .12 os títulos saem
    em português mesmo com o SO em en-US). É o mojibake do quser de novo.
    """
    primeira = wu._PS_COLETA.strip().splitlines()[0]
    assert "OutputEncoding" in primeira and "UTF8" in primeira


@pytest.mark.parametrize("saida", [b"[1,2,3]", b"3", b'"texto"', b"null"])
def test_json_que_nao_e_objeto_nao_explode_a_thread(saida, monkeypatch):
    """ConvertTo-Json de lista/escalar não é dict — um .get() nele mataria a thread."""
    def _run(cmd, capture_output=False, timeout=None):
        return subprocess.CompletedProcess(cmd, 0, saida, b"")
    monkeypatch.setattr(wu.subprocess, "run", _run)
    r = wu.coletar_updates()
    assert r["pendentes"] is None
    assert r["erro"]


def test_motivo_da_varredura_velha_diz_QUANTOS_dias(monkeypatch):
    """A mensagem daqui vence a do PS: ela sabe o número de dias e tem acento.

    A do PS é genérica e ASCII ("o agente nao varre ha tempo demais") — é o texto que o
    usuário lê no chat, e "há tempo demais" faz perguntar quanto. Antes, um `motivo or ...`
    deixava a do PS ganhar e o número só aparecia por acaso, na linha do último patch.
    """
    _fake_ps(monkeypatch, {
        "ok": True, "erro": None,
        "ultima_varredura": (_agora() - timedelta(days=610)).isoformat(),
        "pendentes": None,
        "pendentes_motivo": "o agente nao varre ha tempo demais - a contagem seria mentira",
        "ultimo_patch": None, "ultimo_patch_kb": None,
    })
    r = wu.coletar_updates()
    assert r["pendentes"] is None
    assert "610 dias" in r["pendentes_motivo"], "o motivo tem de dizer QUANTOS dias"
    assert "há" in r["pendentes_motivo"]        # com acento, não o ASCII do PS
    assert "tempo demais" not in r["pendentes_motivo"]


def test_busca_falhou_ainda_repassa_o_motivo_do_ps(monkeypatch):
    """O ramo específico do PS não pode ser engolido pelo ajuste acima: com varredura
    RECENTE e busca com erro, o motivo detalhado do PS continua chegando."""
    _fake_ps(monkeypatch, {
        "ok": True, "erro": None,
        "ultima_varredura": _agora().isoformat(),
        "pendentes": None,
        "pendentes_motivo": "busca falhou: 0x80240438",
        "ultimo_patch": None, "ultimo_patch_kb": None,
    })
    r = wu.coletar_updates()
    assert r["pendentes"] is None
    assert "0x80240438" in r["pendentes_motivo"]
