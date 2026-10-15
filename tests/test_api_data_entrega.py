"""Data de entrega combinada on the 8077 (PLANO_DATA_ENTREGA_COMBINADA, F4).

Production asks for a new delivery date through the .11; Comercial decides on the .90. These
tests hold the contract of the three routes (docs/DESENHO_DATA_ENTREGA_COMBINADA.md §1):
the order of the checks (a retry is recognised before the rate limit and SAP), every refusal
key, who is recorded as the requester, the race on the one-pending index, and that nothing is
ever written to SAP. The base is an in-memory fake of the supabase-py chain; the view and the
ORDR are stubs; "today" is frozen.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

pytest.importorskip("flask")

import api as apimod  # noqa: E402
import entrega_combinada as entrega  # noqa: E402
import situacao_pedidos_hana as hana  # noqa: E402
from config import reset_settings  # noqa: E402
from seguranca import credenciais  # noqa: E402
from tests.test_api_situacao_pedidos import _row  # noqa: E402

MESTRA = "chave-mestra-de-teste"
HOJE = datetime(2026, 11, 10, 9, 0)
VIEW = [_row(DocEntry=1, DocNum=84146, Data_Entrega="2026-11-20", StatusPedido="Aberto"),
        _row(DocEntry=2, DocNum=84000, Data_Entrega="2026-11-20", StatusPedido="Fechado"),
        _row(DocEntry=3, DocNum=84001, Data_Entrega=None, StatusPedido="Aberto")]


# ── Fake base (supabase-py chain) ──────────────────────────────────────────────

class _Base:
    """``pedidos_entrega_alteracoes`` in memory, with the one-pending unique index."""

    def __init__(self) -> None:
        self.linhas: list[dict[str, Any]] = []
        self.inserts = 0
        self.falhar = False
        self.corrida: dict[str, Any] | None = None   # a pending that lands before our insert
        self.ops: list[str] = []

    def table(self, nome: str) -> _Q:
        assert nome == entrega.TABELA
        return _Q(self)


class _Q:
    def __init__(self, base: _Base) -> None:
        self.b = base
        self.filtros: list = []
        self.novo: dict | None = None

    def select(self, *_a, **_k):
        return self

    def eq(self, campo, valor):
        self.filtros.append(lambda r: str(r.get(campo)) == str(valor))
        return self

    def neq(self, campo, valor):
        self.filtros.append(lambda r: str(r.get(campo)) != str(valor))
        return self

    def gte(self, *_a):
        return self

    def or_(self, *_a):
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a):
        return self

    def insert(self, linha):
        self.novo = dict(linha)
        return self

    def execute(self):
        from postgrest.exceptions import APIError

        if self.b.falhar:
            raise RuntimeError("base fora")
        if self.novo is None:
            self.b.ops.append("select")
            return type("R", (), {"data": [r for r in self.b.linhas if all(f(r) for f in self.filtros)]})()
        self.b.ops.append("insert")
        self.b.inserts += 1
        if self.b.corrida is not None:
            self.b.linhas.append(self.b.corrida)
            self.b.corrida = None
        if any(r["doc_num"] == self.novo["doc_num"] and r["situacao"] == "pendente" for r in self.b.linhas):
            raise APIError({"code": "23505", "message": 'duplicate key value violates unique '
                            'constraint "pedidos_entrega_um_pendente"', "details": None, "hint": None})
        linha = {"id": len(self.b.linhas) + 1, "gera_atraso": self.novo["motivo"] != "alteracao_cliente",
                 "solicitado_em": "2026-11-10T12:00:00+00:00", "decidido_por": None,
                 "decidido_em": None, "motivo_recusa": None, **self.novo}
        self.b.linhas.append(linha)
        return type("R", (), {"data": [linha]})()


def _pendente(doc_num=84146, data="2026-11-27", motivo="quebra_maquina", id_=50):
    return {"id": id_, "doc_num": doc_num, "data_nova": data, "motivo": motivo, "situacao": "pendente",
            "gera_atraso": motivo != "alteracao_cliente", "solicitado_por": "ana (PCP)",
            "solicitado_cliente": "pcp", "solicitado_em": "2026-11-09T12:00:00+00:00",
            "decidido_por": None, "decidido_em": None, "origem": "producao_api"}


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def base(monkeypatch):
    b = _Base()
    monkeypatch.setattr(apimod, "_supabase", lambda: b)
    return b


@pytest.fixture
def c(monkeypatch, base):
    monkeypatch.setenv("OS_API_KEY", MESTRA)
    reset_settings()
    apimod._rate_limiter.reset()
    monkeypatch.setattr(hana, "fetch_status_pedidos", lambda recarregar=False: [dict(r) for r in VIEW])
    monkeypatch.setattr(hana, "idade_do_cache_s", lambda: 12.0)
    monkeypatch.setattr(apimod, "consultar_status_pedido",
                        lambda n: {"pedido_existe": False, "pedido_cancelado": False, "pedido_status": None})
    monkeypatch.setattr(apimod.sit_ped, "now_br", lambda: HOJE)
    apimod.app.config.update(TESTING=True)
    return apimod.app.test_client()


@pytest.fixture
def pcp():
    return {"X-API-Key": credenciais.criar("pcp", ["entrega:ler", "entrega:solicitar"])}


def _post(c, h, n=84146, **corpo):
    corpo.setdefault("data_nova", "2026-11-27")
    corpo.setdefault("motivo", "quebra_maquina")
    corpo.setdefault("solicitante", "ana (PCP)")
    return c.post(f"/pedidos/{n}/data-entrega/solicitacoes", json=corpo, headers=h)


# ── POST: who asked ────────────────────────────────────────────────────────────

def test_post_cria_pendente_201_com_data_anterior_e_data_sap(c, pcp, base):
    r = _post(c, pcp, observacao="Prensa 3 parada")
    assert r.status_code == 201
    b = r.get_json()
    s = b["solicitacao"]
    assert (s["situacao"], s["origem"], s["data_anterior"], s["data_sap"]) == (
        "pendente", "producao_api", "2026-11-20", "2026-11-20")
    assert s["solicitado_por"] == "ana (PCP)" and s["solicitado_cliente"] == "pcp"
    assert b["ja_existia"] is False and "Comercial" in b["aviso"]
    assert b["entrega"]["pendente"]["id"] == s["id"]
    assert b["entrega"]["data_vigente"] == "2026-11-20"     # nothing approved yet


def test_post_solicitado_cliente_vem_da_credencial_nao_do_corpo(c, pcp, base):
    _post(c, pcp, solicitado_cliente="outro", solicitante="ana")
    assert base.linhas[0]["solicitado_cliente"] == "pcp"


def test_post_mcp_servico_com_x_sis_pedido_por_grava_o_cliente_real(c, base):
    chave = credenciais.criar("mcp-servico", ["entrega:ler", "entrega:solicitar"], declara_usuario=True)
    r = _post(c, {"X-API-Key": chave, "X-SIS-Pedido-Por": "pcp-mcp", "X-SIS-Usuario": "Joao Lima"},
              solicitante="outro nome")
    assert r.status_code == 201
    assert base.linhas[0]["solicitado_cliente"] == "pcp-mcp"
    assert base.linhas[0]["solicitado_por"] == "Joao Lima"      # the vouched person wins


def test_post_cliente_comum_nao_forja_pedido_por_nem_usuario(c, pcp, base):
    _post(c, {**pcp, "X-SIS-Pedido-Por": "forjado", "X-SIS-Usuario": "forjado"}, solicitante="ana")
    assert base.linhas[0]["solicitado_cliente"] == "pcp" and base.linhas[0]["solicitado_por"] == "ana"


# ── POST: body ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("data", ["27/11/2026", "2026-02-30", "20261127", True, 20261127, None,
                                  "2026-W47-5", "２０２６-11-27"])
def test_post_data_invalida_400(c, pcp, data):
    r = _post(c, pcp, data_nova=data)
    assert r.status_code == 400 and r.get_json()["motivo"] == "data_invalida"


def test_post_motivo_fora_dos_4_lista_os_4(c, pcp):
    b = _post(c, pcp, motivo="chuva").get_json()
    assert b["motivo"] == "motivo_invalido" and len(b["motivos"]) == 4


@pytest.mark.parametrize("motivo", [["quebra_maquina"], {"a": 1}, 1, None])
def test_post_motivo_nao_texto_e_400_nunca_500(c, pcp, motivo):
    r = _post(c, pcp, motivo=motivo)
    assert (r.status_code, r.get_json()["motivo"]) == (400, "motivo_invalido")


def test_post_observacao_longa_ou_com_controle(c, pcp, base):
    assert _post(c, pcp, observacao="x" * 1001).get_json()["motivo"] == "observacao_longa"
    assert _post(c, pcp, observacao="a\x07b").get_json()["motivo"] == "observacao_invalida"
    assert _post(c, pcp, observacao="linha 1\r\nlinha 2").status_code == 201
    assert base.linhas[0]["observacao"] == "linha 1\nlinha 2"       # CRLF stored as LF


@pytest.mark.parametrize("pessoa", ["", "x" * 81, "ana\tlima"])
def test_post_solicitante_invalido(c, pcp, pessoa):
    assert _post(c, pcp, solicitante=pessoa).get_json()["motivo"] == "solicitante_invalido"


@pytest.mark.parametrize("data,motivo", [
    ("2026-11-09", "data_no_passado"),
    ("2027-11-11", "data_muito_distante"),
    ("2026-11-20", "data_igual_a_vigente"),
])
def test_post_regras_de_data_422(c, pcp, data, motivo):
    r = _post(c, pcp, data_nova=data)
    assert r.status_code == 422 and r.get_json()["motivo"] == motivo


def test_post_hoje_e_365_dias_aceitos_e_antecipar_tambem(c, pcp, base):
    assert _post(c, pcp, data_nova="2026-11-10").status_code == 201
    base.linhas.clear()
    assert _post(c, pcp, data_nova="2027-11-10").status_code == 201


# ── POST: retries and races ────────────────────────────────────────────────────

def test_post_repetido_mesmo_conteudo_200_ja_existia_antes_do_teto_e_do_sap(c, pcp, base, monkeypatch):
    base.linhas.append(_pendente())
    monkeypatch.setattr(apimod, "_RATE_ENTREGA_SOLICITAR_MAX", 0)
    monkeypatch.setattr(hana, "fetch_status_pedidos", lambda **_k: pytest.fail("leu o SAP"))
    r = _post(c, pcp)
    assert r.status_code == 200 and r.get_json()["ja_existia"] is True
    assert base.inserts == 0


def test_post_outra_pendente_409_ja_ha_pendente_com_quem_quando(c, pcp, base):
    base.linhas.append(_pendente(data="2026-12-01"))
    r = _post(c, pcp)
    b = r.get_json()
    assert r.status_code == 409 and b["motivo"] == "ja_ha_pendente"
    assert b["pendente"]["solicitado_por"] == "ana (PCP)" and b["pendente"]["data_nova"] == "2026-12-01"


def test_post_23505_vira_ja_existia_ou_ja_ha_pendente(c, pcp, base):
    base.corrida = _pendente()                       # same content lands in between
    assert _post(c, pcp).get_json()["ja_existia"] is True
    base.linhas.clear()
    base.corrida = _pendente(data="2026-12-03")      # another one lands in between
    assert _post(c, pcp).get_json()["motivo"] == "ja_ha_pendente"


def test_post_23505_com_releitura_vazia_tenta_uma_vez_e_depois_409(c, pcp, base, monkeypatch):
    from postgrest.exceptions import APIError

    def _sempre_23505(_cliente, _linha):
        raise APIError({"code": "23505", "message": 'violates "pedidos_entrega_um_pendente"',
                        "details": None, "hint": None})

    tentativas = []
    monkeypatch.setattr(entrega, "inserir", lambda cl, li: tentativas.append(1) or _sempre_23505(cl, li))
    r = _post(c, pcp)
    assert r.status_code == 409 and r.get_json()["motivo"] == "ja_ha_pendente"
    assert len(tentativas) == 2


def test_post_429_pelo_rate_configuravel(c, pcp, base, monkeypatch):
    monkeypatch.setattr(apimod, "_RATE_ENTREGA_SOLICITAR_MAX", 1)
    assert _post(c, pcp).status_code == 201
    base.linhas.clear()
    assert _post(c, pcp).status_code == 429


# ── POST: the order ────────────────────────────────────────────────────────────

def test_post_fechado_na_view_409(c, pcp):
    assert _post(c, pcp, n=84000).get_json()["motivo"] == "pedido_fechado"


def test_post_sem_data_sap_409(c, pcp):
    assert _post(c, pcp, n=84001).get_json()["motivo"] == "sem_data_sap"


@pytest.mark.parametrize("info,status,motivo", [
    (None, 503, "indeterminado"),
    ({"pedido_existe": False}, 404, "pedido_nao_encontrado"),
    ({"pedido_existe": True, "pedido_cancelado": True, "pedido_status": "Cancelado"}, 409, "pedido_cancelado"),
    ({"pedido_existe": True, "pedido_cancelado": False, "pedido_status": "Fechado"}, 409, "pedido_fechado"),
    ({"pedido_existe": True, "pedido_cancelado": False, "pedido_status": "Aberto"}, 404, "fora_do_recorte"),
])
def test_post_fora_da_view_pela_ordr(c, pcp, monkeypatch, info, status, motivo):
    monkeypatch.setattr(apimod, "consultar_status_pedido", lambda n: info)
    r = _post(c, pcp, n=70000)
    assert (r.status_code, r.get_json()["motivo"]) == (status, motivo)


def test_post_view_fora_503_indeterminado_e_nada_gravado(c, pcp, base, monkeypatch):
    def _cai(**_k):
        raise hana.SAPIndisponivel("HANA fora")

    monkeypatch.setattr(hana, "fetch_status_pedidos", _cai)
    r = _post(c, pcp)
    assert (r.status_code, r.get_json()["motivo"]) == (503, "indeterminado") and base.inserts == 0


def test_post_volume_da_view_vira_503_indeterminado(c, pcp, monkeypatch):
    def _guarda(**_k):
        raise apimod.sit_ped.ValidationError("a view devolveu demais")

    monkeypatch.setattr(hana, "fetch_status_pedidos", _guarda)
    r = _post(c, pcp)
    assert (r.status_code, r.get_json()["motivo"]) == (503, "indeterminado")


def test_post_resposta_sem_linha_gravada_e_503_confira(c, pcp, monkeypatch):
    class _Vazio:
        data: list = []

    class _Q:
        def insert(self, _l):
            return self

        def execute(self):
            return _Vazio()

    monkeypatch.setattr(entrega, "linhas_do_pedido", lambda cl, n: [])
    monkeypatch.setattr(apimod, "_supabase", lambda: type("B", (), {"table": lambda _s, _n: _Q()})())
    r = _post(c, pcp)
    assert (r.status_code, r.get_json()["motivo"]) == (503, "base_indisponivel")
    assert "GET" in r.get_json()["error"]


def test_numero_acima_de_int4_e_404(c, pcp):
    r = _post(c, pcp, n=99999999999)
    assert (r.status_code, r.get_json()["motivo"]) == (404, "pedido_nao_encontrado")
    assert c.get("/pedidos/99999999999/data-entrega", headers=pcp).status_code == 404


def test_post_docnum_ambiguo_409(c, pcp, monkeypatch):
    monkeypatch.setattr(hana, "fetch_status_pedidos",
                        lambda **_k: [dict(VIEW[0]), dict(VIEW[0], DocEntry=99)])
    assert _post(c, pcp).get_json()["motivo"] == "docnum_ambiguo"


# ── Base and access ────────────────────────────────────────────────────────────

def test_post_base_fora_503(c, pcp, base):
    base.falhar = True
    r = _post(c, pcp)
    assert (r.status_code, r.get_json()["motivo"]) == (503, "base_indisponivel")


def test_post_so_com_anon_key_503_base_nao_configurada(c, pcp, monkeypatch):
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    reset_settings()
    r = _post(c, pcp)
    assert (r.status_code, r.get_json()["motivo"]) == (503, "base_nao_configurada")


def test_sem_escopo_403_e_leitura_comum_nao_le_a_conversa(c):
    leitura = {"X-API-Key": credenciais.criar("orcaview-90", ["leitura"])}
    assert _post(c, leitura).status_code == 403
    assert c.get("/pedidos/84146/data-entrega", headers=leitura).status_code == 403
    assert c.get("/pedidos/data-entrega/solicitacoes", headers=leitura).status_code == 403


def test_chave_docentry_400(c, pcp):
    r = c.post("/pedidos/1/data-entrega/solicitacoes?chave=docentry", json={}, headers=pcp)
    assert r.get_json()["motivo"] == "chave_nao_suportada"


def test_post_so_le_e_insere_na_base(c, pcp, base):
    """The base side of "never writes SAP": the route only reads and inserts (no update or
    delete); SAP is only read, through the stubbed view and ORDR."""
    assert _post(c, pcp).status_code == 201
    assert set(base.ops) == {"select", "insert"}


# ── Reads ──────────────────────────────────────────────────────────────────────

def test_get_conversa_em_ordem_com_aprovada_conta_atraso(c, pcp, base):
    base.linhas += [
        {**_pendente(id_=1), "situacao": "aprovada", "decidido_por": "v@x.com",
         "decidido_em": "2026-11-09T15:00:00+00:00"},
        _pendente(id_=2, data="2026-12-10", motivo="alteracao_cliente"),
    ]
    b = c.get("/pedidos/84146/data-entrega", headers=pcp).get_json()
    assert [r["id"] for r in b["conversa"]] == [1, 2]
    assert (b["data_sap"], b["data_vigente"], b["atraso_nosso_dias"]) == ("2026-11-20", "2026-11-27", 7)
    assert (b["atrasado"], b["atrasado_fonte"]) == (True, "combinada")
    assert b["pendente"]["id"] == 2 and b["sap_disponivel"] is True


def test_get_sem_conversa_vigente_e_a_data_sap_e_atrasado_da_view(c, pcp):
    b = c.get("/pedidos/84146/data-entrega", headers=pcp).get_json()
    assert b["data_vigente"] == "2026-11-20" and (b["atrasado"], b["atrasado_fonte"]) == (False, "sap")


def test_get_fora_da_view_aberto_atrasado_null(c, pcp, base, monkeypatch):
    base.linhas.append({**_pendente(doc_num=70000, id_=1), "situacao": "aprovada",
                        "decidido_por": "v", "decidido_em": "2026-11-09T15:00:00+00:00"})
    monkeypatch.setattr(apimod, "consultar_status_pedido",
                        lambda n: {"pedido_existe": True, "pedido_cancelado": False, "pedido_status": "Aberto"})
    b = c.get("/pedidos/70000/data-entrega", headers=pcp).get_json()
    assert b["status_pedido"] == "Aberto" and b["data_sap"] is None and b["data_vigente"] == "2026-11-27"
    # No SAP date = no reference: an open order is NOT asserted as on time.
    assert b["atrasado"] is None


def test_get_ordr_none_sap_disponivel_false(c, pcp, base, monkeypatch):
    base.linhas.append(_pendente(doc_num=70000, id_=1))
    monkeypatch.setattr(apimod, "consultar_status_pedido", lambda n: None)
    b = c.get("/pedidos/70000/data-entrega", headers=pcp).get_json()
    assert b["sap_disponivel"] is False and b["atrasado"] is None


def test_get_inexistente_404_com_motivo(c, pcp):
    r = c.get("/pedidos/70000/data-entrega", headers=pcp)
    assert (r.status_code, r.get_json()["motivo"]) == (404, "pedido_nao_encontrado")


def test_lista_pendentes_e_dominio(c, pcp, base):
    base.linhas += [_pendente(id_=1), {**_pendente(doc_num=84000, id_=2), "situacao": "aprovada",
                                        "origem": "comercial", "decidido_por": "v",
                                        "decidido_em": "2026-11-09T15:00:00+00:00"}]
    b = c.get("/pedidos/data-entrega/solicitacoes", headers=pcp).get_json()
    assert b["total"] == 1 and b["solicitacoes"][0]["id"] == 1
    d = c.get("/pedidos/data-entrega/solicitacoes?situacao=decididas", headers=pcp).get_json()
    assert [s["origem"] for s in d["solicitacoes"]] == ["comercial"]
    for q in ("situacao=x", "dias=0", "dias=91", "limite=201", "dias=abc"):
        assert c.get(f"/pedidos/data-entrega/solicitacoes?{q}", headers=pcp).status_code == 422


# ── Agents ─────────────────────────────────────────────────────────────────────

def test_agente_nunca_recebe_entrega_solicitar():
    with pytest.raises(credenciais.CredencialInvalida):
        credenciais.criar("mira-x", ["leitura", "entrega:solicitar"], agente=True)
    credenciais.criar("mira-y", ["leitura"], agente=True)
    with pytest.raises(credenciais.CredencialInvalida):
        credenciais.acrescentar_escopos("mira-y", ["entrega:solicitar"])


def test_listar_monta_os_filtros_de_cada_situacao():
    """The fake base ignores gte/or_: this pins the filters the real PostgREST receives."""
    chamadas: list = []

    class _Q:
        def __getattr__(self, nome):
            def _f(*a, **k):
                chamadas.append((nome, a, k))
                return self
            return _f

        def execute(self):
            return type("R", (), {"data": []})()

    base = type("B", (), {"table": lambda _s, _n: _Q()})()
    desde = "2026-11-03T12:00:00+00:00"
    for situacao in ("pendente", "decididas", "todas"):
        chamadas.clear()
        entrega.listar(base, situacao=situacao, desde=desde, limite=50)
        nomes = [(n, a) for n, a, _k in chamadas]
        if situacao == "pendente":
            assert ("eq", ("situacao", "pendente")) in nomes
        elif situacao == "decididas":
            assert ("neq", ("situacao", "pendente")) in nomes and ("gte", ("decidido_em", desde)) in nomes
        else:
            assert ("or_", (f"solicitado_em.gte.{desde},decidido_em.gte.{desde}",)) in nomes
        assert ("limit", (50,)) in nomes


def test_get_pedido_fechado_fora_da_view_traz_a_data_de_entrega_do_sap(c, pcp, monkeypatch):
    """83411 (15/10/2026): closed, out of the view, no thread — the SAP delivery date still comes
    (ORDR.DocDueDate, informative), instead of an empty date that looked like a bad SAP entry."""
    monkeypatch.setattr(apimod, "consultar_status_pedido", lambda n: {
        "pedido_existe": True, "pedido_cancelado": False, "pedido_status": "Fechado",
        "data_entrega": "2025-12-18"})
    r = c.get("/pedidos/83411/data-entrega", headers=pcp)
    b = r.get_json()
    assert r.status_code == 200 and b["status_pedido"] == "Fechado"
    assert b["data_sap"] == b["data_vigente"] == "2025-12-18"
    assert b["alterada"] is False and b["atrasado"] is False     # a closed order is not late


def test_get_aberto_fora_da_view_com_data_do_sap_ja_tem_referencia(c, pcp, base, monkeypatch):
    base.linhas.append({**_pendente(doc_num=70000, id_=1), "situacao": "aprovada",
                        "decidido_por": "v", "decidido_em": "2026-11-09T15:00:00+00:00"})
    monkeypatch.setattr(apimod, "consultar_status_pedido", lambda n: {
        "pedido_existe": True, "pedido_cancelado": False, "pedido_status": "Aberto",
        "data_entrega": "2026-11-20"})
    b = c.get("/pedidos/70000/data-entrega", headers=pcp).get_json()
    assert (b["data_sap"], b["data_referencia"], b["atraso_nosso_dias"]) == ("2026-11-20", "2026-11-20", 7)
    assert b["atrasado"] is True
