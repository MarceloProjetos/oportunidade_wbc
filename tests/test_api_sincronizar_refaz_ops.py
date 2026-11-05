"""POST /ordens-servico/<n>/sincronizar brings the WBC's changes to the OPs (05/11/2026).

The Controle de Producao is doubled: this only asks it. What matters: agents and `so_atualizar`
never reach it, an order not in the mirror never reaches it, a redo in progress does not copy half
the OPs, and a Controle de Producao that is down costs the check, not the copy.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

pytest.importorskip("flask")

import api as apimod  # noqa: E402
from config import reset_settings  # noqa: E402
from seguranca import credenciais  # noqa: E402

ROTA = "/ordens-servico/84544/sincronizar"
LINHA = {"N_PED": 84544, "N_OP": 161071, "NomeClien": "X", "Status": "P",
         "data_hora_extracao": "2026-11-05T11:31:32", "id_execucao": "e", "TotalOrcam": 1}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("OS_API_KEY", raising=False)
    monkeypatch.delenv("STATUS_ID", raising=False)
    reset_settings()
    apimod._rate_limiter.reset()
    copiados: list[int] = []
    monkeypatch.setattr(apimod, "sync_os", lambda n: copiados.append(n) or True)
    monkeypatch.setattr(apimod, "diagnosticar_nped", lambda n: {
        "tem_os": True, "cancelada": False, "pedido_existe": True,
        "pedido_cancelado": False, "pedido_status": "Aberto"})
    monkeypatch.setattr(apimod, "consultar_status_pedido", lambda n: None)
    monkeypatch.setattr(apimod, "_fetch_os_detalhe", lambda n: [dict(LINHA)])
    apimod.app.config.update(TESTING=True)
    c = apimod.app.test_client()
    c.copiados = copiados
    return c


def _cp(monkeypatch, corpo=None, http=200, erro=None) -> MagicMock:
    resposta = MagicMock(status_code=http)
    resposta.json.return_value = corpo if corpo is not None else {}
    post = MagicMock(side_effect=erro) if erro else MagicMock(return_value=resposta)
    monkeypatch.setattr(apimod.httpx, "post", post)
    return post


def test_refazendo_nao_copia_e_o_motivo_chega_em_resultado(client, monkeypatch) -> None:
    post = _cp(monkeypatch, {"ok": True, "situacao": "refazendo", "motivo": "estao sendo refeitas",
                             "execucao": {"id": "t", "estado": "/api/pedidos-wbc/execucoes/t"}}, 202)
    r = client.post(ROTA)
    corpo = r.get_json()
    assert r.status_code == 200 and corpo["ok"] is False
    assert corpo["resultado"]["tipo"] == "refazendo_ops"
    assert corpo["resultado"]["motivo"] == "estao sendo refeitas"
    assert corpo["execucao"]["id"] == "t"
    assert client.copiados == []
    assert post.call_args.args[0].endswith("/api/pedidos-wbc/refazer/84544")


def test_ja_produzido_copia_e_avisa(client, monkeypatch) -> None:
    _cp(monkeypatch, {"ok": True, "situacao": "ja_produzido", "motivo": "ja tem OP em producao", "ops": [1]})
    r = client.post(ROTA)
    corpo = r.get_json()
    assert r.status_code == 200 and corpo["ok"] is False
    assert corpo["resultado"]["tipo"] == "ja_produzido"
    assert corpo["resultado"]["motivo"] == "ja tem OP em producao"
    assert corpo["resultado"]["sincronizado"] is True
    assert corpo["resumo"]["num_ops"] == 1
    assert client.copiados == [84544]


def test_em_dia_e_o_sincronizar_de_sempre(client, monkeypatch) -> None:
    _cp(monkeypatch, {"ok": True, "situacao": "em_dia", "motivo": None})
    r = client.post(ROTA)
    corpo = r.get_json()
    assert r.status_code == 200 and corpo["ok"] is True
    assert corpo["resultado"]["ok"] is True and corpo["resultado"]["tipo"] is None
    assert "aviso" not in corpo
    assert client.copiados == [84544]


def test_cp_ocupado_copia_e_avisa(client, monkeypatch) -> None:
    _cp(monkeypatch, {"ok": False, "tipo": "ocupado", "motivo": "x"}, 409)
    corpo = client.post(ROTA).get_json()
    assert corpo["ok"] is False and corpo["resultado"]["tipo"] == "ocupado"
    assert "outra execucao" in corpo["resultado"]["motivo"]
    assert client.copiados == [84544]


@pytest.mark.parametrize("kw", [
    {"erro": httpx.ConnectError("recusou")},
    {"corpo": {"ok": False, "tipo": "sap_indisponivel", "motivo": "x"}, "http": 502},
    {"corpo": {"detail": "bloqueado"}, "http": 503},
])
def test_cp_fora_nao_impede_a_copia(client, monkeypatch, kw) -> None:
    _cp(monkeypatch, **kw)
    r = client.post(ROTA)
    corpo = r.get_json()
    assert r.status_code == 200 and corpo["ok"] is True
    assert "nao foi possivel conferir" in corpo["aviso"]
    assert client.copiados == [84544]


def test_so_atualizar_nao_pergunta_ao_cp(client, monkeypatch) -> None:
    post = _cp(monkeypatch, {"ok": True, "situacao": "refazendo"})
    assert client.post(f"{ROTA}?so_atualizar=1").get_json()["ok"] is True
    post.assert_not_called()
    assert client.copiados == [84544]


def test_pedido_fora_do_espelho_nao_pergunta_ao_cp(client, monkeypatch) -> None:
    """The consumers' search also lands here when it misses: finding an order cancels nothing."""
    post = _cp(monkeypatch, {"ok": True, "situacao": "refazendo"})
    monkeypatch.setattr(apimod, "_fetch_os_detalhe", lambda n: [])
    assert client.post(ROTA).status_code == 200
    post.assert_not_called()
    assert client.copiados == [84544]


def test_agente_nunca_pergunta_ao_cp(client, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OS_API_KEY", "mestra")
    reset_settings()
    post = _cp(monkeypatch, {"ok": True, "situacao": "refazendo"})
    monkeypatch.setattr(apimod.seguranca_agente, "recusa", lambda *_a, **_k: None)
    chave = credenciais.criar("agente-teste", ["leitura", "os:sincronizar"], agente=True)
    r = client.post(ROTA, headers={"X-API-Key": chave})
    assert r.status_code == 200
    post.assert_not_called()
    assert client.copiados == [84544]


def test_quem_pediu_vai_junto(client, monkeypatch) -> None:
    post = _cp(monkeypatch, {"ok": True, "situacao": "em_dia"})
    client.post(ROTA, json={"solicitante": "Ana"})
    assert post.call_args.kwargs["json"] == {"solicitante": "Ana via API 8077"}
    client.post(ROTA)
    assert post.call_args.kwargs["json"] == {"solicitante": "API 8077 (aberto)"}
