"""The security base shared by the API, the Controle de Produção and the MCP (F1 of
PLANO_MIRA_AGENTE_11.md (removed 2026-10-06)). Each rule of the plan has a test here or next to its route."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pytest

from seguranca import agente, auditoria, credenciais
from seguranca.__main__ import main as cli
from seguranca.credenciais import Cliente, CredencialInvalida


# ---------------------------------------------------------------------------
# Credentials (rule 2)
# ---------------------------------------------------------------------------
def test_criar_guarda_so_o_resumo_e_a_chave_identifica_o_cliente(_seguranca_isolada):
    chave = credenciais.criar("mira-agente", ["leitura", "os:sincronizar"], agente=True)
    arquivo = (_seguranca_isolada / "credenciais.json").read_text(encoding="utf-8")

    assert chave not in arquivo                      # never stored in clear
    assert credenciais.resumo(chave) in arquivo
    cliente = credenciais.identificar(chave)
    assert cliente == Cliente("mira-agente", frozenset({"leitura", "os:sincronizar"}), agente=True)
    assert cliente.pode("leitura") and not cliente.pode("op:status")


def test_chave_errada_ou_vazia_nao_identifica_ninguem():
    credenciais.criar("equipe-pedidos", ["pedidos_wbc"])
    for chave in ("", None, "outra-coisa"):
        assert credenciais.identificar(chave) is None


def test_chave_mestra_continua_valendo_como_admin():
    """No current client breaks on the day this goes live."""
    cliente = credenciais.identificar("a-mestra", mestra="a-mestra")
    assert cliente.nome == "chave-mestra" and cliente.pode("op:status") and cliente.pode("historico:apagar")


def test_revogar_corta_a_chave_na_hora_e_guarda_o_registro():
    chave = credenciais.criar("equipe-pedidos", ["pedidos_wbc"])
    assert credenciais.revogar("equipe-pedidos") is True
    assert credenciais.identificar(chave) is None
    registro = credenciais.carregar()[0]
    assert registro["ativo"] is False and "revogado_em" in registro
    assert credenciais.revogar("equipe-pedidos") is False


@pytest.mark.parametrize("nome,escopos,agente_,trecho", [
    ("", ["leitura"], False, "Nome inválido"),
    ("com espaço", ["leitura"], False, "Nome inválido"),
    ("x", [], False, "Escopo"),
    ("x", ["tudo"], False, "Escopo"),
    ("x", ["admin"], True, "nunca recebe 'admin'"),
])
def test_criar_recusa_o_que_nao_faz_sentido(nome, escopos, agente_, trecho):
    with pytest.raises(CredencialInvalida, match=trecho):
        credenciais.criar(nome, escopos, agente=agente_)


def test_nome_repetido_e_recusado():
    credenciais.criar("orcaview-90", ["leitura"])
    with pytest.raises(CredencialInvalida, match="Já existe um cliente ativo"):
        credenciais.criar("orcaview-90", ["leitura"])


def test_acrescentar_escopo_mantem_a_chave_e_nao_tira_nada():
    chave = credenciais.criar("altamira-view", ["leitura", "os:sincronizar"])
    assert credenciais.acrescentar_escopos("altamira-view", ["rh", "leitura"]) == ["leitura", "os:sincronizar", "rh"]
    cliente = credenciais.identificar(chave)
    assert cliente.pode("rh") and cliente.pode("os:sincronizar")
    with pytest.raises(CredencialInvalida, match="Nenhum cliente ativo"):
        credenciais.acrescentar_escopos("inexistente", ["rh"])
    with pytest.raises(CredencialInvalida, match="Escopo"):
        credenciais.acrescentar_escopos("altamira-view", ["tudo"])


def test_cli_acrescentar(capsys):
    credenciais.criar("altamira-view", ["leitura"])
    assert cli(["acrescentar", "altamira-view", "--escopos", "rh"]) == 0
    assert "leitura, rh" in capsys.readouterr().out


def test_nome_revogado_pode_ser_recriado_e_a_chave_velha_segue_sem_valer():
    """A key that leaked is replaced under the same name; the old entry stays for the record."""
    velha = credenciais.criar("altamira-view", ["leitura"])
    credenciais.revogar("altamira-view")
    nova = credenciais.criar("altamira-view", ["leitura"])
    assert credenciais.identificar(velha) is None
    assert credenciais.identificar(nova).nome == "altamira-view"
    assert [c["ativo"] for c in credenciais.carregar()] == [False, True]


def test_arquivo_ilegivel_nao_derruba_nada(_seguranca_isolada):
    (_seguranca_isolada / "credenciais.json").write_text("{nao é json", encoding="utf-8")
    assert credenciais.identificar("qualquer") is None


def test_escopo_desconhecido_no_arquivo_e_ignorado(_seguranca_isolada):
    """A typo in a hand-edited file must not grant anything."""
    chave = "k" * 43
    (_seguranca_isolada / "credenciais.json").write_text(json.dumps({"clientes": [
        {"nome": "x", "hash": credenciais.resumo(chave), "escopos": ["leitura", "admim"], "ativo": True}]}),
        encoding="utf-8")
    assert credenciais.identificar(chave).escopos == frozenset({"leitura"})


# ---------------------------------------------------------------------------
# Audit (rule 4) — 30 days, owner 02/10/2026
# ---------------------------------------------------------------------------
def test_registrar_acrescenta_uma_linha_json_por_chamada():
    auditoria.registrar("api", cliente="mira-agente", rota="/status", status=200)
    auditoria.registrar("api", cliente="mira-agente", rota="/pedidos/1/situacao", status=200)
    linhas = auditoria.ler("api")
    assert [l["rota"] for l in linhas] == ["/status", "/pedidos/1/situacao"]
    assert linhas[0]["servico"] == "api" and "ts" in linhas[0]


def test_retencao_apaga_so_o_que_passou_de_30_dias(_seguranca_isolada):
    pasta = _seguranca_isolada / "auditoria"
    pasta.mkdir()
    hoje = date(2026, 10, 2)
    for dias in (31, 30, 1):
        (pasta / f"api-{hoje - timedelta(days=dias):%Y-%m-%d}.jsonl").write_text("{}\n", encoding="utf-8")
    (pasta / "anotacao.txt").write_text("fica", encoding="utf-8")

    assert auditoria.limpar_antigos(pasta, hoje) == 1
    assert sorted(p.name for p in pasta.iterdir()) == ["anotacao.txt", "api-2026-09-02.jsonl", "api-2026-10-01.jsonl"]


def test_registrar_nunca_derruba_a_chamada(monkeypatch, _seguranca_isolada):
    arquivo = _seguranca_isolada / "um-arquivo"
    arquivo.write_text("x", encoding="utf-8")
    monkeypatch.setenv("SIS_AUDITORIA_PASTA", str(arquivo))       # a file where the folder should be
    auditoria.registrar("api", rota="/x")            # must not raise


def test_separador_de_linha_unicode_na_rota_nao_quebra_a_leitura():
    """U+2028/U+0085/\x1e in a crafted URL used to split one JSON line in two (07/10/2026)."""
    rota = "/historico?x=a\u2028b\u2029c\x85d\x1ce\x1df\x1eg"
    auditoria.registrar("api", rota=rota)
    auditoria.registrar("api", rota="/depois")
    assert [linha["rota"] for linha in auditoria.ler("api")] == [rota, "/depois"]


def test_recusa_anonima_grava_uma_linha_por_ip_e_alvo_por_minuto():
    for _ in range(50):
        auditoria.registrar_recusa_anonima("api", "10.0.0.9", alvo="GET /historico", rota="/historico", status=401)
    auditoria.registrar_recusa_anonima("api", "10.0.0.8", alvo="GET /historico", rota="/historico", status=401)
    auditoria.registrar_recusa_anonima("api", "10.0.0.9", alvo="POST /sincronizar-os", status=401)
    auditoria.registrar_recusa_anonima("mcp", "10.0.0.9", resultado="recusado")
    assert [linha["ip"] for linha in auditoria.ler("api")] == ["10.0.0.9", "10.0.0.8", "10.0.0.9"]
    assert len(auditoria.ler("mcp")) == 1


def test_recusa_anonima_volta_a_gravar_depois_da_janela(monkeypatch):
    agora = [1000.0]
    monkeypatch.setattr(auditoria.time, "monotonic", lambda: agora[0])
    auditoria.registrar_recusa_anonima("api", "10.0.0.9", rota="/x")
    agora[0] += auditoria._ANONIMOS_JANELA_S - 1
    auditoria.registrar_recusa_anonima("api", "10.0.0.9", rota="/x")
    agora[0] += 2
    auditoria.registrar_recusa_anonima("api", "10.0.0.9", rota="/x")
    assert len(auditoria.ler("api")) == 2


def test_texto_longo_e_cortado():
    auditoria.registrar("mcp", argumentos="x" * 5000)
    assert len(auditoria.ler("mcp")[0]["argumentos"]) <= auditoria.LIMITE_TEXTO + 1


# ---------------------------------------------------------------------------
# Agent rules (rules 7 and 8)
# ---------------------------------------------------------------------------
AGENTE = Cliente("mira-agente", frozenset({"leitura", "os:sincronizar"}), agente=True)
PESSOA = Cliente("equipe-pedidos", frozenset({"pedidos_wbc"}))
QUINTA_10H = datetime(2026, 10, 1, 10, 0)


def test_interruptor_desliga_tudo_ou_so_a_escrita():
    assert agente.recusa(AGENTE, escrita=True, agora=QUINTA_10H) is None
    agente.desligar(so_escrita=True)
    assert agente.recusa(AGENTE, escrita=False, agora=QUINTA_10H) is None
    assert "escritas do agente estão desligadas" in agente.recusa(AGENTE, escrita=True, agora=QUINTA_10H)
    agente.desligar()
    assert "desligado" in agente.recusa(AGENTE, escrita=False, agora=QUINTA_10H)
    assert agente.religar() is True
    assert agente.recusa(AGENTE, escrita=True, agora=QUINTA_10H) is None


@pytest.mark.parametrize("agora", [
    datetime(2026, 10, 1, 6, 59), datetime(2026, 10, 1, 19, 0),    # before 7h / at 19h
    datetime(2026, 10, 3, 10, 0),                                  # Saturday
    datetime(2026, 10, 12, 10, 0),                                 # national holiday
])
def test_agente_so_grava_no_expediente(agora):
    assert "dia útil, das 7h às 19h" in agente.recusa(AGENTE, escrita=True, agora=agora)
    assert agente.recusa(AGENTE, escrita=False, agora=agora) is None     # reading is always fine


def test_regras_do_agente_nao_valem_para_pessoas():
    agente.desligar()
    assert agente.recusa(PESSOA, escrita=True, agora=datetime(2026, 10, 3, 2, 0)) is None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def test_cli_cria_mostra_a_chave_uma_vez_e_lista_sem_ela(capsys):
    assert cli(["criar", "mira-agente", "--escopos", "leitura,mcp", "--agente", "--declara-usuario"]) == 0
    saida = capsys.readouterr().out
    chave = saida.split("\n\n    ")[1].split()[0]
    assert credenciais.identificar(chave).nome == "mira-agente"

    assert cli(["listar"]) == 0
    listagem = capsys.readouterr().out
    assert "mira-agente" in listagem and chave not in listagem and "agente declara-usuario" in listagem


def test_cli_recusa_escopo_invalido(capsys):
    assert cli(["criar", "x", "--escopos", "tudo"]) == 2
    assert "Escopo" in capsys.readouterr().err
