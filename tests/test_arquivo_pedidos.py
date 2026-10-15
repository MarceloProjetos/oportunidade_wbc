"""arquivo_pedidos: keep who changed each sales order before the SAP drops it (99 versions).

Web docs/PLANO_PEDIDOS_QUEM_MEXEU.md, F2. Pure parts + one cycle against fakes: the suite
never reaches the HANA nor the Supabase.
"""
from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal

import pytest

import arquivo_pedidos as ap
from operacao import historico_pedido as hp


@pytest.fixture(autouse=True)
def _integracao(monkeypatch):
    monkeypatch.setenv("SL_USERNAME", "orcaview")
    monkeypatch.delenv("OP_SL_USERNAME", raising=False)


def _versao(n, codigo, hora=140000, **cab):
    base = {"LogInstanc": n, "DocNum": 84999, "UpdateDate": date(2026, 10, 15), "UpdateTS": hora,
            "CardCode": "C001", "DocDueDate": date(2026, 11, 3), "DocTotal": Decimal("1000"),
            "CANCELED": "N", "_U_NAME": codigo.title(), "_USER_CODE": codigo}
    return base | cab


def _linha(n, qtd="1"):
    return {"LogInstanc": n, "LineNum": 0, "ItemCode": "MOD-A", "Quantity": Decimal(qtd),
            "Weight1": Decimal("10"), "Price": Decimal("10"), "LineStatus": "O"}


def _plano(desde=None, mn=1, mx=3):
    return {"doc_entry": 20500, "doc_num": 84999, "min": mn, "max": mx, "desde": desde}


# ── planning ──────────────────────────────────────────────────────────────────

def test_planeja_os_acompanhados_com_versao_nova_e_depois_os_nunca_vistos():
    sonda = [{"DocEntry": 1, "DocNum": 11, "Min": 1, "Max": 5, "Ultima": "2026-10-01"},
             {"DocEntry": 2, "DocNum": 12, "Min": 1, "Max": 3, "Ultima": "2026-10-14"},
             {"DocEntry": 3, "DocNum": 13, "Min": 1, "Max": 9, "Ultima": "2026-10-10"},
             {"DocEntry": 4, "DocNum": 14, "Min": 1, "Max": 2, "Ultima": "2026-10-15"}]
    plano = ap.planejar(sonda, cursor={1: 4, 2: 3}, novos=1)
    assert [(p["doc_entry"], p["desde"]) for p in plano] == [(1, 4), (4, None)]


# ── rows of one order ─────────────────────────────────────────────────────────

def test_primeira_leitura_guarda_desde_a_criacao():
    versoes = [_versao(1, "orcaview"), _versao(2, "vendas01", DocDueDate=date(2026, 12, 1)),
               _versao(3, "vendas01")]
    linhas, perdidas = ap.linhas_do_pedido(_plano(), versoes, [_linha(n) for n in (1, 2, 3)], [])
    assert perdidas == 0
    assert [(r["log_instanc"], r["log_instanc_ate"]) for r in linhas] == [(1, 1), (2, 2), (3, 3)]
    assert linhas[0]["resumo"] == "criado com 1 linha(s)"
    assert linhas[1]["quebra"] is True and linhas[1]["quebra_em"] == ["entrega"]
    assert linhas[1]["doc_num"] == 84999 and linhas[1]["fonte"] == "sap_adoc"


def test_primeira_leitura_de_pedido_ja_cortado_registra_a_lacuna():
    versoes = [_versao(1948, "projeto06"), _versao(1949, "vendas01", DocDueDate=date(2026, 12, 1))]
    linhas, perdidas = ap.linhas_do_pedido(_plano(mn=1948, mx=1949), versoes,
                                           [_linha(1948), _linha(1949)], [])
    assert perdidas == 1947
    lacuna = linhas[0]
    assert lacuna["lacuna"] is True and (lacuna["log_instanc"], lacuna["log_instanc_ate"]) == (1, 1947)
    assert lacuna["repeticoes"] == 1947 and lacuna["resumo"].startswith("1947 versão(ões) apagadas")
    assert linhas[1]["resumo"] == hp.MAIS_ANTIGA


def test_ciclo_seguinte_descarta_a_versao_base_ja_guardada():
    versoes = [_versao(3, "vendas01"), _versao(4, "vendas01", CardCode="C999")]
    linhas, perdidas = ap.linhas_do_pedido(_plano(desde=3, mx=4), versoes, [_linha(3), _linha(4)], [])
    assert perdidas == 0 and len(linhas) == 1
    assert linhas[0]["log_instanc"] == 4 and linhas[0]["quebra_em"] == ["cliente"]


def test_base_apagada_antes_da_leitura_vira_lacuna_no_meio():
    versoes = [_versao(10, "vendas01"), _versao(11, "vendas01")]
    linhas, perdidas = ap.linhas_do_pedido(_plano(desde=5, mn=10, mx=11), versoes,
                                           [_linha(10), _linha(11)], [])
    assert perdidas == 4
    assert (linhas[0]["log_instanc"], linhas[0]["log_instanc_ate"]) == (6, 9) and linhas[0]["lacuna"]


def test_ruido_dobrado_vira_uma_linha_com_intervalo():
    versoes = [_versao(1, "orcaview")] + [_versao(n, "projeto06") for n in range(2, 6)]
    linhas, _ = ap.linhas_do_pedido(_plano(mx=5), versoes, [_linha(n) for n in range(1, 6)], [])
    dobrada = linhas[-1]
    assert (dobrada["log_instanc"], dobrada["log_instanc_ate"]) == (2, 5)
    assert dobrada["repeticoes"] == 4 and dobrada["sem_mudanca"] == 4


def test_versao_da_integracao_vira_da_pessoa_que_gravou_pela_tela():
    versoes = [_versao(1, "orcaview", hora=100000),
               _versao(2, "orcaview", hora=101200, DocDueDate=date(2026, 12, 1))]
    tela = {"id": 77, "pessoa": "adriano.fonseca@altamira.com.br", "acao": "gravar-data-entrega",
            "momento": "2026-10-15T10:11:40-03:00", "resultado": "ok"}
    linhas, _ = ap.linhas_do_pedido(_plano(mx=2), versoes, [_linha(1), _linha(2)], [tela])
    r = linhas[1]
    assert r["origem"] == "pessoa" and r["pessoa"] == "adriano.fonseca@altamira.com.br"
    assert r["tela_id"] == 77 and r["quebra_em"] == ["entrega"]
    # Far from any screen write = still the integration.
    longe, _ = ap.linhas_do_pedido(_plano(mx=2), versoes, [_linha(1), _linha(2)],
                                   [tela | {"momento": "2026-10-15T09:00:00-03:00"}])
    assert longe[1]["origem"] == "integracao" and longe[1]["pessoa"] is None


def test_tela_que_falhou_nao_explica_versao():
    versoes = [_versao(1, "orcaview", hora=100000), _versao(2, "orcaview", hora=101200, CardCode="C9")]
    tela = {"id": 1, "pessoa": "x@y", "acao": "a", "momento": "2026-10-15T10:12:00-03:00",
            "resultado": "falhou"}
    linhas, _ = ap.linhas_do_pedido(_plano(mx=2), versoes, [_linha(1), _linha(2)], [tela])
    assert linhas[1]["origem"] == "integracao"


# ── worker events ─────────────────────────────────────────────────────────────

def test_eventos_do_worker_viram_linhas_com_fuso_de_sao_paulo(tmp_path):
    db = tmp_path / "wbc.db"
    con = sqlite3.connect(db)
    con.executescript(
        "CREATE TABLE acompanhamento (orcnum TEXT, pedido_docentry INT, pedido_docnum INT);"
        "CREATE TABLE eventos (id INTEGER PRIMARY KEY, orcnum TEXT, momento TEXT, tipo TEXT,"
        " regra TEXT, mensagem TEXT);"
        "INSERT INTO acompanhamento VALUES ('00125001', 20500, 84999), ('00125002', NULL, NULL);"
        "INSERT INTO eventos VALUES (1, '00125001', '2026-10-15 09:30:00', 'decisao', 'pedido_corrigido_a_mao',"
        " 'Pedido já corrigido em nome de outro parceiro (C011627)');"
        "INSERT INTO eventos VALUES (2, '00125001', '2026-10-15 09:31:00', 'decisao', 'atualiza_pedido', 'x');"
        "INSERT INTO eventos VALUES (3, '00125002', '2026-10-15 09:32:00', 'decisao', 'troca_de_pn', 'y');")
    con.commit()
    con.close()
    eventos = ap.ler_eventos_worker(str(db), depois_de=0)
    assert [e["id"] for e in eventos] == [1]  # other rules and orders without a pedido stay out
    (linha,) = ap.linhas_do_worker(eventos)
    assert linha["momento"] == "2026-10-15T09:30:00-03:00"
    assert linha["evento_id"] == "1@2026-10-15T09:30:00-03:00"
    assert linha["categorias"] == ["cliente"] and linha["doc_entry"] == 20500
    assert ap.ler_eventos_worker(str(db), depois_de=1) == []
    assert ap.ler_eventos_worker(None, depois_de=0) == []


# ── one cycle against fakes ───────────────────────────────────────────────────

class _Cliente:
    """Records upserts; answers the cursor rebuild and the screen rows with nothing."""

    def __init__(self):
        self.upserts: list[tuple[list, str]] = []

    def table(self, nome):
        assert nome == ap.TABELA
        return _Consulta(self)


class _Consulta:
    def __init__(self, cliente):
        self.cliente = cliente
        self._upsert = None

    def __getattr__(self, nome):  # select / eq / order / range / limit
        return lambda *a, **k: self

    def upsert(self, linhas, on_conflict, ignore_duplicates):
        assert ignore_duplicates is True
        # PostgREST refuses a batch with different keys per object (PGRST102).
        assert len({frozenset(r) for r in linhas}) == 1, "lote com chaves diferentes"
        self._upsert = (linhas, on_conflict)
        return self

    def execute(self):
        if self._upsert is not None:
            self.cliente.upserts.append(self._upsert)
        return type("R", (), {"data": []})()


def _hana_falso(monkeypatch, versoes):
    def linhas(conn, sql, params=()):
        if "GROUP BY" in sql:
            return [{"DocEntry": 20500, "DocNum": 84999, "Min": versoes[0]["LogInstanc"],
                     "Max": versoes[-1]["LogInstanc"], "Ultima": "2026-10-15"}]
        desde = params[1] if len(params) > 1 else 0
        if '"ADOC"' in sql:
            return [v for v in versoes if v["LogInstanc"] >= desde]
        return [_linha(v["LogInstanc"]) for v in versoes if v["LogInstanc"] >= desde]
    monkeypatch.setattr(ap.hana, "_linhas", linhas)


def test_ciclo_grava_uma_vez_e_o_seguinte_nao_repete(monkeypatch, tmp_path):
    versoes = [_versao(1, "orcaview"), _versao(2, "vendas01", DocDueDate=date(2026, 12, 1))]
    _hana_falso(monkeypatch, versoes)
    cliente, estado = _Cliente(), tmp_path / "estado.json"
    r1 = ap.ciclo(object(), "SBO", cliente, estado_path=estado)
    assert r1["lidos"] == 1 and r1["linhas"] == 2 and r1["quebras"] == 1
    assert cliente.upserts[0][1] == "fonte,doc_entry,log_instanc"
    assert ap.ler_estado(estado)["adoc"] == {20500: 2}
    r2 = ap.ciclo(object(), "SBO", cliente, estado_path=estado)
    assert r2["lidos"] == 0 and len(cliente.upserts) == 1


def test_lote_com_lacuna_leva_as_mesmas_colunas(monkeypatch, tmp_path):
    # 84377 on 15/10/2026: the gap row had fewer keys than the version rows and PostgREST
    # refused the whole batch, stopping every cycle at that order.
    versoes = [_versao(12, "vendas01"), _versao(13, "vendas01", CardCode="C9")]
    _hana_falso(monkeypatch, versoes)
    cliente = _Cliente()
    r = ap.ciclo(object(), "SBO", cliente, estado_path=tmp_path / "e.json")
    (lote, _), = cliente.upserts
    assert r["versoes_perdidas"] == 11 and lote[0]["lacuna"] is True and len(lote) == 3
    assert lote[0]["usuario_codigo"] is None and lote[1]["lacuna"] is False


def test_coluna_desconhecida_e_recusada_antes_da_rede():
    with pytest.raises(ValueError):
        ap.completar({"fonte": "sap_adoc", "coluna_errada": 1})


def test_simulacao_nao_grava_nem_avanca_cursor(monkeypatch, tmp_path):
    _hana_falso(monkeypatch, [_versao(1, "orcaview"), _versao(2, "vendas01", CardCode="C9")])
    cliente, estado = _Cliente(), tmp_path / "estado.json"
    r = ap.ciclo(object(), "SBO", cliente, estado_path=estado, simular=True)
    assert r["linhas"] == 2 and r["exemplo"] and cliente.upserts == [] and not estado.exists()


def test_job_fora_da_11_nao_faz_nada(monkeypatch):
    def proibido(*a, **k):
        raise AssertionError("tocou no SAP ou na base fora da .11")
    monkeypatch.setattr(ap, "_cliente_supabase", proibido)
    monkeypatch.setattr(ap.hana, "_conectar", proibido)
    assert ap.job() is None


# ── the history route merges the archive ──────────────────────────────────────

def test_historico_junta_arquivo_e_sap_vivo():
    arquivo = [
        {"fonte": "sap_adoc", "log_instanc": 1, "log_instanc_ate": 1, "momento": "2026-09-01T10:00:00-03:00",
         "usuario_codigo": "orcaview", "origem": "integracao", "resumo": "criado com 1 linha(s)"},
        {"fonte": "sap_adoc", "log_instanc": 2, "log_instanc_ate": 40, "momento": "2026-09-02T10:00:00-03:00",
         "usuario_codigo": "projeto06", "origem": "criacao_op", "resumo": "processamento de OP",
         "repeticoes": 39, "sem_mudanca": 30},
        {"fonte": "sap_adoc", "log_instanc": 41, "log_instanc_ate": 60, "lacuna": True,
         "momento": "2026-09-03T10:00:00-03:00", "resumo": "20 versão(ões) apagadas"},
        {"fonte": "sap_adoc", "log_instanc": 61, "log_instanc_ate": 61, "momento": "2026-09-04T10:00:00-03:00",
         "usuario_codigo": "vendas01", "origem": "pessoa", "quebra": True, "quebra_em": ["preco"],
         "resumo": "1 mudança(s)"},
        {"fonte": "worker", "acao": "pedido_corrigido_a_mao", "orcamento": "00125001",
         "momento": "2026-09-05T08:00:00-03:00", "resumo": "Pedido já corrigido…"},
    ]
    # The SAP still has 60..62: 60/61 are archived, 62 is new.
    vivas = [_versao(60, "vendas01"), _versao(61, "vendas01"), _versao(62, "vendas01", CardCode="C9")]
    r = hp.montar(84999, 20500, vivas, [_linha(n) for n in (60, 61, 62)], arquivo=arquivo)
    assert r["total_versoes"] == 62 and r["arquivo"] == {"linhas": 4, "ate_versao": 61}
    assert [e["versao"] for e in r["versoes"]] == [62, 61, 60, 40, 1]
    assert r["versoes"][0]["quebra_em"] == ["cliente"] and "arquivado" not in r["versoes"][0]
    assert r["versoes"][1]["arquivado"] is True and r["quebras"] == 2
    assert r["versoes"][3]["versao_de"] == 2 and r["versoes"][3]["repeticoes"] == 39
    assert r["lacuna"]["versoes_apagadas"] == 20 and "20 no meio" in r["lacuna"]["texto"]
    assert r["eventos_worker"][0]["regra"] == "pedido_corrigido_a_mao"


def test_historico_continua_se_o_arquivo_falhar(monkeypatch):
    monkeypatch.setattr(hp.hana, "_schema", lambda: "SBO")
    monkeypatch.setattr(hp.hana, "_conectar", lambda: type("C", (), {"close": lambda self: None})())

    def linhas(conn, sql, params=()):
        if '"ORDR"' in sql:
            return [{"DocEntry": 20500, "DocNum": 84999}]
        return [_versao(1, "orcaview")] if '"ADOC"' in sql else []
    monkeypatch.setattr(hp.hana, "_linhas", linhas)

    def quebrado(doc_entry):
        raise ConnectionError("base fora")
    r = hp.historico(84999, ler_arquivo=quebrado)
    assert r["total_entradas"] == 1 and "indisponível" in r["aviso_arquivo"]
