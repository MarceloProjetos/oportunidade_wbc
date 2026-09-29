"""Testes das otimizações de 16/09/2026 (ver seção 7.14 do migration_guide.md).

Duas mudanças feitas a partir do perfil da execução do orçamento 00125192, onde 18,9s se
dividiam em 9,9s de Service Layer (52%) e 3,7s abrindo conexões HANA (20%):

1. `HanaDirectReader` reaproveita a conexão entre consultas (62 aberturas viram 1);
2. `marca_op_nas_linhas` grava `U_INO_OP` em todas as linhas do grupo numa única chamada
   (eram 6 PATCHes somando 4,1s).

Mais o teste do bug encontrado junto: a chave da OP criada vinha `0` porque o código lia
`DocEntry` e a Service Layer devolve `AbsoluteEntry` em `ProductionOrders` — o que fazia o
pedido ser marcado com `U_INO_OP = 0`.
"""
import asyncio
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# 1. Reúso de conexão no HanaDirectReader
# ---------------------------------------------------------------------------
class _ErroFake(Exception):
    """Faz o papel de `hdbcli.dbapi.Error`."""


class _CursorFake:
    def __init__(self, conexao):
        self.conexao = conexao
        self.description = [("A",)]

    def execute(self, sql, params=()):
        if self.conexao.morta and "SET SCHEMA" not in sql:
            raise _ErroFake("connection closed")
        if "ERRO_DE_SQL" in sql:
            raise _ErroFake("invalid column name")
        self.conexao.consultas.append(sql)

    def fetchall(self):
        return [(1,)]

    def close(self):
        pass


class _ConexaoFake:
    def __init__(self):
        self.morta = False
        self.fechada = False
        self.consultas = []

    def cursor(self):
        return _CursorFake(self)

    def isconnected(self):
        return not self.morta

    def close(self):
        self.fechada = True


@pytest.fixture
def hana_falso(monkeypatch):
    """Instala um `hdbcli` falso e devolve (fábrica_de_leitor, lista_de_conexões_abertas)."""
    abertas: list[_ConexaoFake] = []

    def connect(**_kwargs):
        conexao = _ConexaoFake()
        abertas.append(conexao)
        return conexao

    modulo = types.ModuleType("hdbcli")
    dbapi = types.ModuleType("hdbcli.dbapi")
    dbapi.connect = connect
    dbapi.Error = _ErroFake
    modulo.dbapi = dbapi
    monkeypatch.setitem(sys.modules, "hdbcli", modulo)
    monkeypatch.setitem(sys.modules, "hdbcli.dbapi", dbapi)

    from controleproducao.core.hana_reader import HanaDirectReader

    settings = MagicMock(
        hana_host="h", hana_port=30015, hana_username="u", hana_password="p", hana_schema="SCH"
    )
    return (lambda: HanaDirectReader(settings)), abertas


def test_reusa_a_mesma_conexao_entre_consultas(hana_falso):
    """O ponto da otimização: 10 consultas não podem abrir 10 conexões."""
    criar_leitor, abertas = hana_falso
    leitor = criar_leitor()

    for i in range(10):
        leitor.fetch_all(f"SELECT {i}")

    assert len(abertas) == 1
    assert len(abertas[0].consultas) == 11  # 10 consultas + o SET SCHEMA da abertura


def test_nao_conecta_ate_a_primeira_consulta(hana_falso):
    criar_leitor, abertas = hana_falso
    criar_leitor()
    assert abertas == []


def test_reconecta_sozinho_se_a_conexao_cair(hana_falso):
    """Reusar conexão só é aceitável se a queda dela for tratada — execuções aqui duram
    minutos, tempo suficiente para o servidor derrubar uma conexão ociosa."""
    criar_leitor, abertas = hana_falso
    leitor = criar_leitor()
    leitor.fetch_all("SELECT 1")

    abertas[0].morta = True
    leitor.fetch_all("SELECT 2")  # não pode levantar exceção

    assert len(abertas) == 2
    assert abertas[0].fechada


def test_erro_de_sql_com_conexao_viva_nao_reconecta(hana_falso):
    """30/09/2026: a SQL error fails the same way twice — retrying it only threw away a good
    connection and paid a new login."""
    from controleproducao.core.exceptions import WbcDatabaseError

    criar_leitor, abertas = hana_falso
    leitor = criar_leitor()
    leitor.fetch_all("SELECT 1")
    with pytest.raises(WbcDatabaseError, match="invalid column"):
        leitor.fetch_all("SELECT ERRO_DE_SQL")
    assert len(abertas) == 1 and not abertas[0].fechada


def test_leitor_recusa_escrita_e_schema_invalido(hana_falso, monkeypatch):
    criar_leitor, abertas = hana_falso
    leitor = criar_leitor()
    with pytest.raises(ValueError, match="só lê"):
        leitor.fetch_all('UPDATE "ORDR" SET "Comments" = ?', ("x",))
    assert abertas == []                      # refused before even connecting

    leitor._settings.hana_schema = 'SCH"; DROP'
    with pytest.raises(ValueError, match="schema inválido"):
        leitor.fetch_all("SELECT 1")


def test_close_encerra_a_conexao(hana_falso):
    criar_leitor, abertas = hana_falso
    leitor = criar_leitor()
    leitor.fetch_all("SELECT 1")
    leitor.close()
    assert abertas[0].fechada


def test_funciona_como_context_manager(hana_falso):
    criar_leitor, abertas = hana_falso
    with criar_leitor() as leitor:
        leitor.fetch_all("SELECT 1")
    assert abertas[0].fechada


# ---------------------------------------------------------------------------
# 2. Gravação de U_INO_OP em lote
# ---------------------------------------------------------------------------
def test_marca_todas_as_linhas_numa_unica_chamada():
    from controleproducao.modules.pedidos_wbc import service as svc

    sl, hana = AsyncMock(), MagicMock()
    hana.fetch_all = MagicMock(return_value=[
        {"LineNum": 0, "U_INO_ORCITM": "1"},
        {"LineNum": 1, "U_INO_ORCITM": "2"},
        {"LineNum": 2, "U_INO_ORCITM": "3"},
        {"LineNum": 3, "U_INO_ORCITM": "4"},
    ])

    asyncio.run(svc.marca_op_nas_linhas(sl, hana, ["1", "2", "3", "4"], 151030, "19124"))

    assert hana.fetch_all.call_count == 1, "as linhas devem ser resolvidas numa consulta só"
    assert sl.update_entity.await_count == 1, "as 4 linhas devem ir num único PATCH"
    assert sl.update_entity.await_args.args[2] == {
        "DocumentLines": [{"LineNum": n, "U_INO_OP": 151030} for n in range(4)]
    }


def test_nao_repete_a_mesma_linha_no_corpo():
    from controleproducao.modules.pedidos_wbc import service as svc

    sl, hana = AsyncMock(), MagicMock()
    hana.fetch_all = MagicMock(return_value=[
        {"LineNum": 7, "U_INO_ORCITM": "9"},
        {"LineNum": 7, "U_INO_ORCITM": "9"},
    ])

    asyncio.run(svc.marca_op_nas_linhas(sl, hana, ["9", "9", "9"], 500, "19124"))

    assert sl.update_entity.await_args.args[2] == {"DocumentLines": [{"LineNum": 7, "U_INO_OP": 500}]}


def test_nao_grava_vinculo_quando_a_op_nao_tem_numero():
    """O bug do `DocEntry=0`: marcar o pedido com `U_INO_OP = 0` é pior que não marcar,
    porque deixa o pedido apontando para uma OP que não existe."""
    from controleproducao.modules.pedidos_wbc import service as svc

    sl, hana = AsyncMock(), MagicMock()
    asyncio.run(svc.marca_op_nas_linhas(sl, hana, ["1"], 0, "19124"))

    assert sl.update_entity.await_count == 0
    assert hana.fetch_all.call_count == 0


def test_nao_grava_quando_nenhuma_linha_corresponde():
    from controleproducao.modules.pedidos_wbc import service as svc

    sl, hana = AsyncMock(), MagicMock()
    hana.fetch_all = MagicMock(return_value=[])

    asyncio.run(svc.marca_op_nas_linhas(sl, hana, ["99"], 1, "19124"))

    assert sl.update_entity.await_count == 0


# ---------------------------------------------------------------------------
# 3. Chave do documento criado (o bug que gerava DocEntry=0)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "resposta, esperado",
    [
        # ProductionOrders na Service Layer usa AbsoluteEntry (DocEntry é o nome da COLUNA
        # em OWOR). Era exatamente isso que devolvia 0 e zerava o vínculo com o pedido.
        ({"AbsoluteEntry": 4567, "DocNum": 151030}, 4567),
        ({"DocEntry": 263791}, 263791),
        ({"AbsoluteEntry": 0, "DocEntry": 99}, 99),
        ({"Status": "sem chave nenhuma"}, 0),
    ],
)
def test_chave_do_documento_criado(resposta, esperado):
    from controleproducao.modules.pedidos_wbc.service import _chave_do_documento_criado

    assert _chave_do_documento_criado(resposta, "ProductionOrders") == esperado


# ---------------------------------------------------------------------------
# 3. Leituras do pedido uma vez por pedido (30/09/2026)
# ---------------------------------------------------------------------------
def test_leituras_do_pedido_saem_uma_vez_e_as_do_grupo_continuam_por_grupo():
    """Three groups of one order used to re-read the order's DocEntry (twice per group),
    DocNum, multiple delivery, the SAP item, the WBC costs and the max price — and a
    discarded "order version". Now once per order; the group's own line (quantity and
    value, `BUSCA_MAX_ITEM_LINHA`) is still read per group."""
    from collections import Counter
    from types import SimpleNamespace

    from controleproducao.core.sql_ligado import ligar
    from controleproducao.modules.pedidos_wbc import queries as q
    from controleproducao.modules.pedidos_wbc import service as svc

    nomes = {
        ligar(q.DOC_NUM_POR_DOC_ENTRY, doc_entry=1)[0]: "doc_num",
        ligar(q.ENTREGA_MULTIPLA, doc_entry=1)[0]: "entrega_multipla",
        ligar(q.SELECT_ORC_ITEM_SAP, grp_code=1)[0]: "item_sap",
        ligar(q.MAX_PRECO_PEDIDO, doc_num="1")[0]: "max_preco",
    }
    respostas = {
        "doc_num": [{"DocNum": 84433}], "entrega_multipla": [{"E": "N"}],
        "item_sap": [{"U_INO_ItemSAP": "I000003"}], "max_preco": [{"M": 10}],
    }
    lidas: Counter = Counter()

    def fetch_all(sql, params=()):
        nome = nomes.get(sql, "outra")
        lidas[nome] += 1
        return respostas.get(nome, [])

    hana = MagicMock()
    hana.fetch_all.side_effect = fetch_all
    hana.fetch_all_values.side_effect = lambda sql, params=(): [[0, 5.0, 500.0]]
    wbc = MagicMock()
    wbc.fetch_all.return_value = [{"CUSTO": 1.0}]
    doc_entry = AsyncMock(return_value="20176")
    marca = AsyncMock()

    def _grupo(orc_itm):
        return [SimpleNamespace(orc_num="00125460", grp_code=7, prd_desc="PP", prd_code="X",
                                linha=orc_itm, quantidade=1, id_integracao_orc=1, linha_orc=1)]

    with patch.object(svc, "_pega_doc_entry_ped", doc_entry), \
         patch.object(svc, "_ops_existentes_para_item", AsyncMock(return_value=[])), \
         patch.object(svc, "cria_recurso_rateio", AsyncMock(return_value="GGF_X")), \
         patch.object(svc, "cria_ordem_producao", AsyncMock(return_value=(0, 900))), \
         patch.object(svc, "checa_semi_acabado", AsyncMock()), \
         patch.object(svc, "marca_op_nas_linhas", marca), \
         patch.object(svc, "_atualiza_doc", AsyncMock()), \
         patch.object(svc, "preenche_log", AsyncMock()):
        contexto: dict = {}
        for orc_itm in ("1", "2", "7"):
            g = _grupo(orc_itm)
            asyncio.run(svc._processa_grupo_producao(
                MagicMock(), wbc, hana, "00125460", 7, int(orc_itm), g, g, False, contexto
            ))

    assert doc_entry.await_count == 1                        # was 6 (twice per group)
    assert {k: lidas[k] for k in respostas} == {k: 1 for k in respostas}
    assert lidas["outra"] == 0                               # GET_VERSAO_PEDIDO is gone
    assert wbc.fetch_all.call_count == 1                     # PEGA_VALORES_RECURSOS
    assert hana.fetch_all_values.call_count == 3             # the group's own line, per group
    assert all(c.args[-1] == "20176" for c in marca.await_args_list)   # doc_entry_final


def test_filial_e_series_uma_vez_por_encerramento():
    """Closing N OPs re-read the active branches and both series per OP (~3N reads)."""
    from controleproducao.modules.manutencao_op import queries as q
    from controleproducao.modules.manutencao_op import service as svc

    lidas: dict[str, int] = {"filiais": 0, "serie": 0}

    def fetch_all(sql, params=()):
        if sql == q.FILIAIS_ATIVAS:
            lidas["filiais"] += 1
            return [{"BPLId": 1}]
        if sql == q.SERIE_DO_DOCUMENTO:
            lidas["serie"] += 1
            return [{"Series": 20, "SeriesName": "Primário"}]
        if sql == q.FILIAL_CANDIDATA_DA_OP:
            return [{"filial_deposito_op": 1}]
        return []

    hana = MagicMock()
    hana.fetch_all.side_effect = fetch_all
    cache: dict = {}
    for doc_entry in (101, 102, 103, 104):
        filial = svc._filial_do_movimento(hana, doc_entry, cache=cache)
        svc._serie_do_documento(hana, svc.TIPO_OBJETO_SAIDA_MERCADORIA, filial, cache=cache)
        svc._serie_do_documento(hana, svc.TIPO_OBJETO_ENTRADA_MERCADORIA, filial, cache=cache)
    assert lidas == {"filiais": 1, "serie": 2}               # was 4 and 8
