"""The org chart on the API 8077 (F2 of docs/PLANO_ORGANOGRAMA.md).

What these tests hold:
1. Both routes need ``leitura``; without a key 401, without the scope 403.
2. The chart-x-credentials check goes only to an administrator (screen cookie, master key):
   a ``leitura`` client learns who handles what, not which scopes each agent holds.
3. ``?para=<agente>`` carries the ready role text, ending with the rule "the role grants nothing".
4. An unknown topic is never 404: it goes to the catch-all one, so there is always an owner.
5. A broken chart with no valid version is 503 with the errors, which never echo a contact.
"""
from __future__ import annotations

import pytest

pytest.importorskip("flask")

import api as apimod  # noqa: E402
from config import reset_settings  # noqa: E402
from operacao import organograma  # noqa: E402
from seguranca import credenciais  # noqa: E402

MESTRA = "chave-mestra-de-teste"


@pytest.fixture
def c(monkeypatch):
    monkeypatch.setenv("OS_API_KEY", MESTRA)
    monkeypatch.delenv("SIS_ORGANOGRAMA_ARQUIVO", raising=False)
    reset_settings()
    apimod._rate_limiter.reset()
    organograma._estados.clear()
    apimod.app.config.update(TESTING=True)
    yield apimod.app.test_client()
    organograma._estados.clear()


def _h(chave: str) -> dict:
    return {"X-API-Key": chave}


def test_sem_chave_401_e_sem_escopo_403(c):
    assert c.get("/operacao/organograma").status_code == 401
    assert c.get("/operacao/organograma/temas/pedido").status_code == 401
    rh = credenciais.criar("rh-app", ["rh"])
    for url in ("/operacao/organograma", "/operacao/organograma/temas/pedido"):
        resposta = c.get(url, headers=_h(rh))
        assert resposta.status_code == 403 and resposta.get_json()["tipo"] == "sem_permissao"


def test_rotas_declaram_leitura():
    assert apimod.operacao_organograma.escopo == "leitura"
    assert apimod.operacao_organograma_tema.escopo == "leitura"


def test_leitura_ve_o_organograma_sem_a_conferencia(c):
    leitura = credenciais.criar("altamira-view", ["leitura"])
    d = c.get("/operacao/organograma", headers=_h(leitura)).get_json()
    assert d["ok"] is True and d["versao"] == 1
    assert {m["id"] for m in d["membros"]} >= {"mira", "teo", "marcelo", "felipe", "gabriel"}
    assert {t["id"] for t in d["temas"]} >= {"pedido", "ti_servidor", "outro"}
    assert d["conferencia"] is None
    assert d["situacao"]["em_uso"] is True and d["situacao"]["arquivo_valido"] is True


def test_agente_le_mas_nao_ve_a_conferencia(c):
    mira = credenciais.criar("mira-agente", ["leitura", "pedidos_wbc"], agente=True)
    d = c.get("/operacao/organograma", headers=_h(mira)).get_json()
    assert d["ok"] is True and d["conferencia"] is None


def test_administrador_ve_a_conferencia_sem_hash(c):
    credenciais.criar("mira-agente", ["leitura", "oportunidades:carga", "pedidos_wbc", "manutencao_op",
                                      "servico:reiniciar"], agente=True)
    resposta = c.get("/operacao/organograma", headers=_h(MESTRA))
    d = resposta.get_json()
    mira = next(a for a in d["conferencia"]["agentes"] if a["membro"] == "mira")
    assert mira["situacao"] == "sobram" and mira["sobram"] == ["servico:reiniciar"]
    assert d["conferencia"]["ok"] is False          # the Téo has no credential here
    corpo = resposta.get_data(as_text=True)
    hash_mira = credenciais.carregar()[0]["hash"]
    assert hash_mira not in corpo and '"hash"' not in corpo


def test_conferencia_pela_tela_tambem(c, monkeypatch):
    monkeypatch.setattr(apimod, "_por_cookie", lambda: True)
    d = c.get("/operacao/organograma").get_json()
    assert d["conferencia"] is not None


def test_para_agente_traz_o_texto_do_cargo_com_a_regra(c):
    leitura = credenciais.criar("orcaview-90", ["leitura"], declara_usuario=True)
    d = c.get("/operacao/organograma?para=teo", headers=_h(leitura)).get_json()
    teo = d["membro"]
    assert teo["id"] == "teo" and teo["tipo"] == "agente"
    assert [e["nome"] for e in teo["escala_para"]] == ["Marcelo"]
    assert {p["acao"] for p in teo["pede"]} == {"reiniciar_servico", "reiniciar_sap_servertools"}
    texto = teo["texto_cargo"]
    assert texto.startswith("Você é Téo, TI.")
    assert texto.rstrip().endswith(organograma.REGRA_PARA_AGENTE)
    assert "pedido → Mira" in texto                 # the topics of others, so it knows where to send
    assert "ti_servidor →" not in texto             # its own topics are not "the others"
    assert {t["id"] for t in d["temas"]} >= {"pedido", "ti_backup"}


def test_para_pessoa_nao_tem_texto_de_cargo(c):
    d = c.get("/operacao/organograma?para=Felipe", headers=_h(MESTRA)).get_json()
    assert d["membro"]["id"] == "felipe" and d["membro"]["aprova"] == ["admin"]
    assert "texto_cargo" not in d["membro"]


def test_para_desconhecido_404(c):
    resposta = c.get("/operacao/organograma?para=ninguem", headers=_h(MESTRA))
    assert resposta.status_code == 404
    assert "mira" in resposta.get_json()["membros"]


def test_tema_conhecido_e_desconhecido(c):
    d = c.get("/operacao/organograma/temas/ti_servidor", headers=_h(MESTRA)).get_json()
    assert d["casou"] is True and d["dono"]["nome"] == "Téo"
    assert [s["id"] for s in d["substitutos"]] == ["marcelo"]

    d = c.get("/operacao/organograma/temas/Financeiro", headers=_h(MESTRA)).get_json()
    assert d["casou"] is False and d["pedido"] == "financeiro" and d["tema"] == "outro"
    assert d["dono"]["id"] == "marcelo"
    assert [s["id"] for s in d["substitutos"]] == ["felipe", "gabriel"]


def test_organograma_invalido_sem_versao_boa_responde_503(c, monkeypatch, tmp_path):
    arquivo = tmp_path / "organograma.toml"
    texto = organograma.ARQUIVO_PADRAO.read_text(encoding="utf-8")
    arquivo.write_text(texto.replace('cargo = "Substituto do diretor"', 'cargo = "Sub (11) 98765-4321"', 1),
                       encoding="utf-8")
    monkeypatch.setenv("SIS_ORGANOGRAMA_ARQUIVO", str(arquivo))
    for url in ("/operacao/organograma", "/operacao/organograma/temas/pedido"):
        resposta = c.get(url, headers=_h(MESTRA))
        assert resposta.status_code == 503
        d = resposta.get_json()
        assert d["error"] == "organograma_invalido"
        assert any("membro 'felipe'.cargo" in e for e in d["erros"])
        assert "98765" not in resposta.get_data(as_text=True)


def test_edicao_quebrada_continua_servindo_a_versao_boa(c, monkeypatch, tmp_path):
    import os

    arquivo = tmp_path / "organograma.toml"
    texto = organograma.ARQUIVO_PADRAO.read_text(encoding="utf-8")
    arquivo.write_text(texto, encoding="utf-8")
    monkeypatch.setenv("SIS_ORGANOGRAMA_ARQUIVO", str(arquivo))
    assert c.get("/operacao/organograma", headers=_h(MESTRA)).status_code == 200
    arquivo.write_text(texto.replace('dono = "teo"', 'dono = "ninguem"', 1), encoding="utf-8")
    os.utime(arquivo, ns=(arquivo.stat().st_atime_ns, arquivo.stat().st_mtime_ns + 1_000_000))
    d = c.get("/operacao/organograma", headers=_h(MESTRA)).get_json()
    assert d["ok"] is True and d["situacao"]["arquivo_valido"] is False
    assert any("ninguem" in e for e in d["situacao"]["erros"])


def test_inicio_tem_o_card_do_organograma(c, monkeypatch):
    monkeypatch.setattr(apimod, "_por_cookie", lambda: True)
    html = c.get("/inicio").get_data(as_text=True)
    assert '<h2 class="secao">Organograma</h2>' in html
    assert "pegar('/operacao/organograma')" in html
    assert "O cargo não dá permissão" in html
