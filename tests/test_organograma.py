"""The office's org chart (F1 of docs/PLANO_ORGANOGRAMA.md).

What these tests hold:
1. The chart in the repo is valid — editing it wrong fails the commit gate.
2. Every validation rule refuses a broken chart, with every problem listed at once.
3. THE RULE: a role never grants anything. No authorisation path imports the chart; a chart
   expecting a scope the credential lacks still gets 403; ``conferir`` never writes credentials.
4. A broken edit on disk keeps the last valid version in use.
"""
from __future__ import annotations

import ast
import copy
import inspect
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from operacao import organograma as org_mod
from operacao.organograma import OrganogramaInvalido
from seguranca import credenciais

RAIZ = Path(__file__).resolve().parents[1]
TEXTO_REAL = org_mod.ARQUIVO_PADRAO.read_text(encoding="utf-8")
DADOS_REAIS = tomllib.loads(TEXTO_REAL)


@pytest.fixture(autouse=True)
def _cache_limpo():
    org_mod._estados.clear()
    yield
    org_mod._estados.clear()


def _dados() -> dict:
    return copy.deepcopy(DADOS_REAIS)


def _membro(dados: dict, id_: str) -> dict:
    return next(m for m in dados["membro"] if m["id"] == id_)


def _tema(dados: dict, id_: str) -> dict:
    return next(t for t in dados["tema"] if t["id"] == id_)


def _erros(dados: dict) -> list[str]:
    with pytest.raises(OrganogramaInvalido) as exc:
        org_mod.de_dados(dados)
    return exc.value.erros


def _registrar(nome: str, escopos: list[str], *, agente: bool = True) -> None:
    credenciais.criar(nome, escopos, agente=agente)


# --- 1. the chart in the repo -----------------------------------------------------------

def test_ficha_do_repositorio_e_valida():
    org = org_mod.carregar(org_mod.ARQUIVO_PADRAO)
    assert {"mira", "teo", "marcelo", "felipe", "gabriel"} <= set(org.membros)
    assert {a.id for a in org.agentes()} == {"mira", "teo", "lia"}


def test_d2_felipe_e_gabriel_substituem_o_diretor():
    org = org_mod.carregar(org_mod.ARQUIVO_PADRAO)
    assert org.membros["marcelo"].escala_para == ("felipe", "gabriel")
    for tema in ("aprovacao_critica", "outro"):
        assert org.temas[tema].dono == "marcelo"
        assert org.temas[tema].substitutos == ("felipe", "gabriel")
    assert org.membros["felipe"].aprova == ("admin",) == org.membros["gabriel"].aprova


def test_ti_e_do_teo_e_pedido_e_da_mira():
    org = org_mod.carregar(org_mod.ARQUIVO_PADRAO)
    assert {t.id for t in org.temas.values() if t.dono == "teo"} == {"ti_servidor", "ti_acesso", "ti_backup"}
    assert org.quem_cuida_de("pedido").dono == "mira"


def test_tema_desconhecido_vai_para_o_geral():
    org = org_mod.carregar(org_mod.ARQUIVO_PADRAO)
    assert org.quem_cuida_de("financeiro").id == org_mod.TEMA_GERAL
    assert org.quem_cuida_de("financeiro").dono == "marcelo"


def test_como_dict_nao_mistura_campos_de_agente_e_de_pessoa():
    org = org_mod.carregar(org_mod.ARQUIVO_PADRAO).como_dict()
    mira = next(m for m in org["membros"] if m["id"] == "mira")
    marcelo = next(m for m in org["membros"] if m["id"] == "marcelo")
    assert "aprova" not in mira and "contato" not in mira
    assert "credencial" not in marcelo and "escopos_esperados" not in marcelo


# --- 2. validation rules ----------------------------------------------------------------

def _quebra_escala_inexistente(d):
    _membro(d, "mira")["escala_para"].append("ninguem")


def _quebra_escala_si_mesmo(d):
    _membro(d, "teo")["escala_para"] = ["teo", "marcelo"]


def _quebra_cuida_de_tema_inexistente(d):
    _membro(d, "mira")["cuida_de"].append("financeiro")


def _quebra_cuida_de_tema_alheio(d):
    _membro(d, "mira")["cuida_de"].append("ti_servidor")


def _quebra_dono_sem_cuida_de(d):
    _membro(d, "teo")["cuida_de"].remove("ti_backup")


def _quebra_dono_inexistente(d):
    _tema(d, "pedido")["dono"] = "ninguem"


def _quebra_substituto_inexistente(d):
    _tema(d, "pedido")["substitutos"] = ["ninguem"]


def _quebra_tema_so_com_agentes(d):
    _tema(d, "ti_backup")["substitutos"] = ["mira"]


def _quebra_sem_tema_geral(d):
    d["tema"] = [t for t in d["tema"] if t["id"] != "outro"]
    _membro(d, "marcelo")["cuida_de"].remove("outro")


def _quebra_tema_geral_de_agente(d):
    _tema(d, "outro")["dono"] = "teo"
    _membro(d, "marcelo")["cuida_de"].remove("outro")
    _membro(d, "teo")["cuida_de"].append("outro")


def _quebra_acao_fora_do_catalogo(d):
    _membro(d, "mira")["pede"].append("apagar_tudo")


def _quebra_acao_sem_o_escopo(d):
    _membro(d, "mira")["escopos_esperados"].remove("pedidos_wbc")


def _quebra_escopo_inexistente(d):
    _membro(d, "teo")["escopos_esperados"].append("root")


def _quebra_escopo_proibido(d):
    _membro(d, "teo")["escopos_esperados"].append("aprovar")


def _quebra_agente_aprova(d):
    _membro(d, "mira")["aprova"] = ["pcp"]


def _quebra_papel_desconhecido(d):
    _membro(d, "felipe")["aprova"] = ["dono"]


def _quebra_pessoa_com_sobrenome(d):
    _membro(d, "felipe")["nome"] = "Felipe Fulano"


def _quebra_telefone(d):
    _membro(d, "felipe")["cargo"] = "Substituto (11) 98765-4321"


def _quebra_arroba(d):
    _membro(d, "marcelo")["contato"] = "5511987654321@s.whatsapp.net"


def _quebra_contato_nao_apelido(d):
    _membro(d, "marcelo")["contato"] = "Marcelo Celular"


def _quebra_chave_desconhecida(d):
    _membro(d, "mira")["escala_pra"] = ["marcelo"]


def _quebra_chave_de_agente_em_pessoa(d):
    _membro(d, "marcelo")["credencial"] = "chave-mestra"


def _quebra_id_repetido(d):
    d["membro"].append(copy.deepcopy(_membro(d, "anderson")))


def _quebra_credencial_repetida(d):
    _membro(d, "teo")["credencial"] = "mira-agente"


def _quebra_agente_sem_credencial(d):
    del _membro(d, "teo")["credencial"]


def _quebra_agente_sem_escala(d):
    _membro(d, "teo")["escala_para"] = []


def _quebra_tipo(d):
    _membro(d, "anderson")["tipo"] = "consultor"


def _quebra_versao(d):
    d["versao"] = 2


QUEBRAS = [
    (_quebra_escala_inexistente, "escala para 'ninguem'"),
    (_quebra_escala_si_mesmo, "escala para si mesmo"),
    (_quebra_cuida_de_tema_inexistente, "'financeiro', que não é um tema"),
    (_quebra_cuida_de_tema_alheio, "o dono do tema é 'teo'"),
    (_quebra_dono_sem_cuida_de, "não lista o tema em 'cuida_de'"),
    (_quebra_dono_inexistente, "dono 'ninguem'"),
    (_quebra_substituto_inexistente, "substituto 'ninguem'"),
    (_quebra_tema_so_com_agentes, "nenhuma pessoa ou equipe entre dono e substitutos"),
    (_quebra_sem_tema_geral, "falta o tema 'outro'"),
    (_quebra_tema_geral_de_agente, "o dono tem de ser uma pessoa"),
    (_quebra_acao_fora_do_catalogo, "'apagar_tudo', que não está no acoes_agente.CATALOGO"),
    (_quebra_acao_sem_o_escopo, "precisa do escopo 'pedidos_wbc'"),
    (_quebra_escopo_inexistente, "escopo 'root' não existe"),
    (_quebra_escopo_proibido, "'aprovar' é proibido a agente"),
    (_quebra_agente_aprova, "um agente nunca aprova"),
    (_quebra_papel_desconhecido, "aprova 'dono'"),
    (_quebra_pessoa_com_sobrenome, "só com o primeiro nome"),
    (_quebra_telefone, "número longo"),
    (_quebra_arroba, "tem '@'"),
    (_quebra_contato_nao_apelido, "contato é um apelido"),
    (_quebra_chave_desconhecida, "chave 'escala_pra'"),
    (_quebra_chave_de_agente_em_pessoa, "chave 'credencial' não vale para tipo 'pessoa'"),
    (_quebra_id_repetido, "membro 'anderson' repetido"),
    (_quebra_credencial_repetida, "credencial 'mira-agente' já é de 'mira'"),
    (_quebra_agente_sem_credencial, "precisa de 'credencial'"),
    (_quebra_agente_sem_escala, "agente sem 'escala_para'"),
    (_quebra_tipo, "tipo 'consultor' inválido"),
    (_quebra_versao, "versao tem de ser 1"),
]


@pytest.mark.parametrize("quebra,esperado", QUEBRAS, ids=[q.__name__[len("_quebra_"):] for q, _ in QUEBRAS])
def test_ficha_quebrada_e_recusada(quebra, esperado):
    dados = _dados()
    quebra(dados)
    erros = _erros(dados)
    assert any(esperado in e for e in erros), erros


def test_todos_os_erros_vem_de_uma_vez():
    dados = _dados()
    _quebra_escala_inexistente(dados)
    _quebra_telefone(dados)
    _quebra_escopo_inexistente(dados)
    assert len(_erros(dados)) >= 3


def test_numero_de_pedido_e_codigo_do_sap_nao_sao_telefone():
    dados = _dados()
    _tema(dados, "pedido")["exemplos"] = ["pedido 84000", "erro 10001287 no SAP", "entrega em 02/11/2026",
                                          "pedidos 84000-84010", "pedidos 84000 84001 84002"]
    org_mod.de_dados(dados)


@pytest.mark.parametrize("texto", ["ligar (11) 98765-4321", "zap 11 98765 4321", "+55 11 987654321",
                                   "fone 1198765.4321"])
def test_telefone_em_texto_livre_e_recusado(texto):
    dados = _dados()
    _tema(dados, "pedido")["exemplos"] = [texto]
    assert any("tema 'pedido'.exemplos[1]: dado de contato" in e for e in _erros(dados))


@pytest.mark.parametrize("campo,valor", [("cargo", "Sub 11/98765/4321"), ("cargo", "1198765x4321"),
                                         ("nome", "Felipe\u200b1198765\u200b4321")])
def test_numero_longo_em_campo_de_identidade_e_recusado_de_qualquer_jeito(campo, valor):
    dados = _dados()
    _membro(dados, "felipe")[campo] = valor
    assert any("membro 'felipe'" in e and "número longo" in e for e in _erros(dados))


def test_contato_com_numero_e_recusado():
    dados = _dados()
    _membro(dados, "felipe")["contato"] = "11_98765_4321"
    assert any("contato é um apelido" in e for e in _erros(dados))


def test_erro_de_contato_nao_repete_o_contato():
    """Errors go to the log and (F2) to the API: they name the place, never the number."""
    dados = _dados()
    _membro(dados, "felipe")["cargo"] = "Substituto (11) 98765-4321"
    _membro(dados, "marcelo")["contato"] = "fulano@exemplo.com"
    texto = " ".join(_erros(dados))
    assert "98765" not in texto and "fulano" not in texto
    assert "membro 'felipe'.cargo" in texto and "membro 'marcelo'.contato" in texto


def test_chave_desconhecida_no_topo_e_recusada():
    dados = _dados()
    dados["membros"] = []
    assert any("chave 'membros' desconhecida no topo" in e for e in _erros(dados))


def test_versao_tem_de_ser_inteiro():
    dados = _dados()
    dados["versao"] = 1.0
    assert any("versao tem de ser 1" in e for e in _erros(dados))


def test_organograma_carregado_e_so_leitura():
    org = org_mod.carregar(org_mod.ARQUIVO_PADRAO)
    with pytest.raises(TypeError):
        org.membros["intruso"] = org.membros["mira"]


def test_laco_de_escalada_entre_agentes_e_recusado():
    dados = _dados()
    _membro(dados, "mira")["escala_para"] = ["teo"]
    _membro(dados, "teo")["escala_para"] = ["mira"]
    erros = _erros(dados)
    assert any("laço entre agentes" in e for e in erros), erros
    assert any("nunca chega a uma pessoa" in e for e in erros), erros


def test_agente_pode_escalar_para_agente_que_chega_a_uma_pessoa():
    dados = _dados()
    _membro(dados, "mira")["escala_para"] = ["teo"]
    org_mod.de_dados(dados)


def test_laco_entre_pessoas_e_substituicao_e_vale():
    # Marcelo -> Felipe -> Marcelo is a stand-in, not a ping-pong.
    org = org_mod.de_dados(_dados())
    assert "marcelo" in org.membros["felipe"].escala_para


def test_toml_invalido_e_recusado():
    with pytest.raises(OrganogramaInvalido, match="TOML inválido"):
        org_mod.ler("versao = = 1")


# --- 3. THE RULE: the role never grants -------------------------------------------------

#: Every place that decides who may do what. None of them may read the chart.
CAMINHOS_DE_AUTORIZACAO = [
    *sorted((RAIZ / "seguranca").glob("*.py")),
    RAIZ / "mcp" / "acesso_mcp.py",
    RAIZ / "casa" / "acesso.py",
    RAIZ / "controleproducao" / "core" / "acesso.py",
    RAIZ / "operacao" / "acoes_agente.py",
]


def test_carregar_os_caminhos_de_autorizacao_nao_carrega_o_organograma():
    """Catches what the AST scan cannot: a transitive or dynamic import (``operacao/__init__``,
    ``importlib``). A fresh interpreter, so this test's own import does not count."""
    codigo = ("import sys; sys.path.insert(0, '.'); "
              "import seguranca.credenciais, seguranca.agente, seguranca.aprovacoes, seguranca.auditoria, "
              "casa.acesso, controleproducao.core.acesso, operacao.acoes_agente; "
              "sys.path.append('mcp'); import acesso_mcp; "
              "assert 'operacao.organograma' not in sys.modules, 'organograma carregado'")
    proc = subprocess.run([sys.executable, "-c", codigo], cwd=RAIZ, capture_output=True, text=True,
                          env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize("arquivo", CAMINHOS_DE_AUTORIZACAO, ids=lambda p: str(p.relative_to(RAIZ)))
def test_nenhum_caminho_de_autorizacao_importa_o_organograma(arquivo):
    arvore = ast.parse(arquivo.read_text(encoding="utf-8"))
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            nomes = [a.name for a in no.names]
        elif isinstance(no, ast.ImportFrom):
            nomes = [no.module or "", *(a.name for a in no.names)]
        else:
            continue
        assert not any("organograma" in n for n in nomes), f"{arquivo.name} importa o organograma"


def test_requer_chave_da_api_nao_le_o_organograma():
    pytest.importorskip("flask")
    import api

    for nome in ("requer_chave", "_cliente", "_credencial_enviada", "_cabecalho_da_chave",
                 "_confere", "_por_cookie", "_mesma_origem"):
        assert "organograma" not in inspect.getsource(getattr(api, nome)), nome


def test_ficha_que_promete_escopo_nao_abre_a_rota(monkeypatch, tmp_path):
    """The chart says the Mira expects ``os:sincronizar``; her credential does not hold it.
    The credential wins: 403, and the check reports the gap."""
    pytest.importorskip("flask")
    import api
    from config import reset_settings

    texto = TEXTO_REAL.replace(
        'pede = ["processar_pedido", "encerrar_ops_pedidos_fechados", "forcar_carga", "reiniciar_servico"]',
        'pede = ["processar_pedido", "encerrar_ops_pedidos_fechados", "forcar_carga", "reiniciar_servico",'
        ' "sincronizar_os"]',
    ).replace(
        'escopos_esperados = ["leitura", "oportunidades:carga", "pedidos_wbc", "manutencao_op", "servico:reiniciar"]',
        'escopos_esperados = ["leitura", "oportunidades:carga", "pedidos_wbc", "manutencao_op", "servico:reiniciar",'
        ' "os:sincronizar"]',
    )
    assert texto != TEXTO_REAL
    arquivo = tmp_path / "organograma.toml"
    arquivo.write_text(texto, encoding="utf-8")
    monkeypatch.setenv("SIS_ORGANOGRAMA_ARQUIVO", str(arquivo))
    org = org_mod.carregar()
    assert "os:sincronizar" in org.membros["mira"].escopos_esperados

    monkeypatch.setenv("OS_API_KEY", "chave-mestra-de-teste")
    reset_settings()
    api._rate_limiter.reset()
    chave = credenciais.criar("mira-agente", ["leitura", "oportunidades:carga", "pedidos_wbc", "manutencao_op",
                                              "servico:reiniciar"], agente=True)
    api.app.config.update(TESTING=True)
    resposta = api.app.test_client().post("/ordens-servico/84080/sincronizar", headers={"X-API-Key": chave})
    assert resposta.status_code == 403
    assert resposta.get_json()["tipo"] == "sem_permissao"

    mira = next(a for a in org_mod.conferir(org)["agentes"] if a["membro"] == "mira")
    assert mira["faltam"] == ["os:sincronizar"]


def test_conferir_nunca_escreve_nas_credenciais(monkeypatch):
    _registrar("mira-agente", ["leitura", "pedidos_wbc"])
    _registrar("orfa-agente", ["leitura"])
    arquivo = Path(os.environ["SIS_CREDENCIAIS_ARQUIVO"])
    antes = (arquivo.read_bytes(), arquivo.stat().st_mtime_ns)

    def explode(*_a, **_k):
        raise AssertionError("a conferência tentou escrever nas credenciais")

    for nome in ("_gravar", "criar", "acrescentar_escopos", "revogar"):
        monkeypatch.setattr(credenciais, nome, explode)
    org_mod.conferir(org_mod.carregar(org_mod.ARQUIVO_PADRAO))
    assert (arquivo.read_bytes(), arquivo.stat().st_mtime_ns) == antes


# --- conferir: the cases ------------------------------------------------------------------

def _agente(resultado: dict, membro: str) -> dict:
    return next(a for a in resultado["agentes"] if a["membro"] == membro)


def test_credenciais_de_hoje_conferem():
    """The .11's real keys (SEGURANCA_11) against the reconciled chart (02/11/2026): the Mira's
    ``servico:reiniciar`` is expected -- on the .90 she forwards a person's restart and stands in for
    the Téo. The Lia never talks to the .11 and does not fail the check."""
    _registrar("mira-agente", ["leitura", "oportunidades:carga", "pedidos_wbc", "manutencao_op", "servico:reiniciar"])
    _registrar("ti-agente", ["leitura", "servico:reiniciar", "sap:reiniciar"])
    resultado = org_mod.conferir(org_mod.carregar(org_mod.ARQUIVO_PADRAO))
    assert _agente(resultado, "mira")["situacao"] == "ok"
    assert _agente(resultado, "teo")["situacao"] == "ok"
    lia = _agente(resultado, "lia")
    assert lia["situacao"] == "sem_acesso_a_11" and lia["credencial"] is None
    assert resultado["ok"] is True and resultado["credencial_sem_ficha"] == []


def test_escopo_a_mais_aparece_como_sobra_e_nao_e_retirado():
    """A scope the role does not expect is reported, never removed (D8)."""
    _registrar("mira-agente", ["leitura", "oportunidades:carga", "pedidos_wbc", "manutencao_op", "servico:reiniciar",
                               "os:sincronizar"])
    _registrar("ti-agente", ["leitura", "servico:reiniciar", "sap:reiniciar"])
    resultado = org_mod.conferir(org_mod.carregar(org_mod.ARQUIVO_PADRAO))
    mira = _agente(resultado, "mira")
    assert mira["situacao"] == "sobram"
    assert mira["sobram"] == ["os:sincronizar"] and mira["faltam"] == []
    assert resultado["ok"] is True      # a sobra is a warning, not a failure
    assert "os:sincronizar" in credenciais.carregar()[0]["escopos"]


def test_agente_sem_credencial_nao_pode_pedir():
    dados = _dados()
    _membro(dados, "lia")["pede"] = ["forcar_carga"]
    assert any("membro 'lia': agente que pede ou espera escopo precisa de 'credencial'" in e for e in _erros(dados))


def test_falta_de_escopo_reprova_a_conferencia():
    _registrar("mira-agente", ["leitura"])
    _registrar("ti-agente", ["leitura", "servico:reiniciar", "sap:reiniciar"])
    resultado = org_mod.conferir(org_mod.carregar(org_mod.ARQUIVO_PADRAO))
    assert _agente(resultado, "mira")["faltam"] == ["manutencao_op", "oportunidades:carga", "pedidos_wbc",
                                                     "servico:reiniciar"]
    assert resultado["ok"] is False


def test_sem_credencial_e_credencial_revogada():
    _registrar("ti-agente", ["leitura", "servico:reiniciar", "sap:reiniciar"])
    credenciais.revogar("ti-agente")
    resultado = org_mod.conferir(org_mod.carregar(org_mod.ARQUIVO_PADRAO))
    assert _agente(resultado, "mira")["situacao"] == "sem_credencial"
    assert _agente(resultado, "teo")["situacao"] == "sem_credencial"
    assert resultado["ok"] is False


def test_credencial_sem_marca_de_agente_reprova():
    """Without the agent flag the off switch and business hours would not apply to it."""
    _registrar("ti-agente", ["leitura", "servico:reiniciar", "sap:reiniciar"], agente=False)
    teo = _agente(org_mod.conferir(org_mod.carregar(org_mod.ARQUIVO_PADRAO)), "teo")
    assert teo["situacao"] == "credencial_nao_e_agente"
    assert teo["marcada_como_agente"] is False
    _registrar("mira-agente", ["leitura", "oportunidades:carga", "pedidos_wbc", "manutencao_op"])
    assert org_mod.conferir(org_mod.carregar(org_mod.ARQUIVO_PADRAO))["ok"] is False


@pytest.mark.parametrize("proibido", sorted(credenciais.PROIBIDOS_AO_AGENTE))
def test_escopo_proibido_editado_a_mao_reprova(proibido):
    """``criar`` refuses it; a hand edit of the JSON does not. The check must not call it a sobra."""
    clientes = [
        {"nome": "mira-agente", "agente": True, "ativo": True,
         "escopos": ["leitura", "oportunidades:carga", "pedidos_wbc", "manutencao_op", proibido]},
        {"nome": "ti-agente", "agente": True, "ativo": True,
         "escopos": ["leitura", "servico:reiniciar", "sap:reiniciar"]},
    ]
    resultado = org_mod.conferir(org_mod.carregar(org_mod.ARQUIVO_PADRAO), clientes)
    mira = _agente(resultado, "mira")
    assert mira["situacao"] == "escopo_proibido" and mira["proibidos"] == [proibido]
    assert resultado["ok"] is False


def test_admin_nao_cobre_falta_de_agente():
    clientes = [{"nome": "ti-agente", "agente": True, "ativo": True, "escopos": ["admin"]}]
    teo = _agente(org_mod.conferir(org_mod.carregar(org_mod.ARQUIVO_PADRAO), clientes), "teo")
    assert teo["faltam"] == ["leitura", "sap:reiniciar", "servico:reiniciar"]
    assert teo["situacao"] == "escopo_proibido"


def test_credencial_de_agente_sem_ficha_e_apontada():
    _registrar("vendas-agente", ["leitura"])
    _registrar("orcaview-90", ["leitura"], agente=False)       # a system, not an office member
    resultado = org_mod.conferir(org_mod.carregar(org_mod.ARQUIVO_PADRAO))
    assert resultado["credencial_sem_ficha"] == ["vendas-agente"]


# --- 4. last valid version ----------------------------------------------------------------

def test_edicao_quebrada_mantem_a_ultima_versao_valida(tmp_path, caplog):
    arquivo = tmp_path / "organograma.toml"
    arquivo.write_text(TEXTO_REAL, encoding="utf-8")
    primeira = org_mod.carregar(arquivo)

    arquivo.write_text(TEXTO_REAL.replace('dono = "teo"', 'dono = "ninguem"', 1), encoding="utf-8")
    os.utime(arquivo, ns=(arquivo.stat().st_atime_ns, arquivo.stat().st_mtime_ns + 1_000_000))
    with caplog.at_level("ERROR"):
        assert org_mod.carregar(arquivo) is primeira
    assert "Organograma invalido" in caplog.text
    estado = org_mod.situacao(arquivo)
    assert estado["em_uso"] is True and estado["arquivo_valido"] is False
    assert any("ninguem" in e for e in estado["erros"])

    arquivo.write_text(TEXTO_REAL, encoding="utf-8")
    os.utime(arquivo, ns=(arquivo.stat().st_atime_ns, arquivo.stat().st_mtime_ns + 2_000_000))
    assert org_mod.carregar(arquivo) is not primeira
    assert org_mod.situacao(arquivo)["arquivo_valido"] is True


def test_arquivo_sem_mudanca_nao_e_relido(tmp_path):
    arquivo = tmp_path / "organograma.toml"
    arquivo.write_text(TEXTO_REAL, encoding="utf-8")
    assert org_mod.carregar(arquivo) is org_mod.carregar(arquivo)


def test_arquivo_salvo_com_bom_e_aceito(tmp_path):
    """Notepad and PowerShell 5.1 (``Set-Content -Encoding UTF8``) write a BOM on the .11."""
    arquivo = tmp_path / "organograma.toml"
    arquivo.write_bytes(b"\xef\xbb\xbf" + TEXTO_REAL.replace("\n", "\r\n").encode("utf-8"))
    assert "mira" in org_mod.carregar(arquivo).membros


def test_arquivo_apagado_depois_de_carregar_mantem_a_versao(tmp_path):
    arquivo = tmp_path / "organograma.toml"
    arquivo.write_text(TEXTO_REAL, encoding="utf-8")
    primeira = org_mod.carregar(arquivo)
    arquivo.unlink()
    assert org_mod.carregar(arquivo) is primeira
    estado = org_mod.situacao(arquivo)
    assert estado["em_uso"] is True and estado["arquivo_valido"] is False
    assert "não consegui ler" in estado["erros"][0]


def test_sem_nenhuma_versao_valida_recusa(tmp_path):
    arquivo = tmp_path / "organograma.toml"
    arquivo.write_text("versao = 1", encoding="utf-8")
    with pytest.raises(OrganogramaInvalido):
        org_mod.carregar(arquivo)
    with pytest.raises(OrganogramaInvalido, match="não consegui ler"):
        org_mod.carregar(tmp_path / "nao_existe.toml")


# --- CLI ----------------------------------------------------------------------------------

def test_cli_mostrar(capsys):
    assert org_mod.main([]) == 0
    saida = capsys.readouterr().out
    assert "o cargo não dá permissão" in saida
    assert "Felipe" in saida and "ti_servidor" in saida


def test_cli_tema(capsys):
    assert org_mod.main(["tema", "ti_servidor"]) == 0
    assert "Téo" in capsys.readouterr().out
    assert org_mod.main(["tema", "financeiro"]) == 0
    assert "vai para 'outro'" in capsys.readouterr().out


def test_cli_conferir_codigos_de_saida(capsys):
    assert org_mod.main(["conferir"]) == 1              # no credentials at all
    assert "sem_credencial" in capsys.readouterr().out
    _registrar("mira-agente", ["leitura", "oportunidades:carga", "pedidos_wbc", "manutencao_op", "servico:reiniciar",
                               "os:sincronizar"])
    _registrar("ti-agente", ["leitura", "servico:reiniciar", "sap:reiniciar"])
    assert org_mod.main(["conferir"]) == 0
    saida = capsys.readouterr().out
    assert "sobram" in saida and "os:sincronizar" in saida
    assert "lia (sem credencial da .11): sem_acesso_a_11" in saida
    assert "nada é concedido nem revogado" in saida


def test_cli_ficha_invalida_sai_2(monkeypatch, tmp_path, capsys):
    arquivo = tmp_path / "organograma.toml"
    arquivo.write_text("versao = 1", encoding="utf-8")
    monkeypatch.setenv("SIS_ORGANOGRAMA_ARQUIVO", str(arquivo))
    assert org_mod.main([]) == 2
    assert "Organograma inválido" in capsys.readouterr().err
