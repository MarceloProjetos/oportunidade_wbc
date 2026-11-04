"""Results that used to say "done" when part of the run produced nothing (2026-11-03 review).

Processar: an empty WBC structure must be found before the order is marked processed; a
Service Layer refusal of one group's OP must not abort the other groups; `EntregaMultipla='Y'`
(no parent OP, as in the legacy) must show up in `sem_op`. Encerrar pedidos fechados: the
errors of each order must reach the task's top-level `com_erro`, and a first-attempt error
the retry did not touch must not vanish.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from controleproducao.core.exceptions import ServiceLayerAuthError, ServiceLayerError
from controleproducao.core.sql_ligado import ligar
from controleproducao.modules.pedidos_wbc import queries as q
from controleproducao.modules.pedidos_wbc import service as svc


# ---------------------------------------------------------------------------
# Processar: empty structure
# ---------------------------------------------------------------------------
def test_estrutura_vazia_nao_marca_o_pedido_como_processado():
    atualiza = AsyncMock()
    preenche = AsyncMock()
    with patch.object(svc, "_busca_orcamentos_wbc", AsyncMock(return_value=[])), \
         patch.object(svc, "_get_id_orcamentos_pedido", AsyncMock(return_value={
             "DocEntry": 1, "CardCode": "C1", "DocNum": 84500, "NotaEspelho": "N"})), \
         patch.object(svc, "ops_vivas_de_pedidos_cancelados", AsyncMock(return_value={})), \
         patch.object(svc, "_verifica_process_wbc", AsyncMock(return_value="N")), \
         patch.object(svc, "busca_estrutura_produto", AsyncMock(return_value=[])), \
         patch.object(svc, "preenche_tabela", preenche), \
         patch.object(svc, "atualiza_pedido_tabela", atualiza), \
         patch.object(svc, "preenche_log", AsyncMock()):
        resultado = asyncio.run(svc.processar_pedidos_novos(MagicMock(), MagicMock(), MagicMock(), ["00125000"]))

    assert resultado["com_erro"][0]["motivo"] == "sem dados no WBC (estrutura vazia)"
    assert atualiza.await_count == 0          # ProcessWBC='Y' never written
    assert preenche.await_count == 0          # nor the OrcDetalhe snapshot


def test_pedido_ja_processado_nao_manda_forcar():
    """Forcing duplicates the OPs; the refusal used to suggest it."""
    with patch.object(svc, "_busca_orcamentos_wbc", AsyncMock(return_value=[])), \
         patch.object(svc, "_get_id_orcamentos_pedido", AsyncMock(return_value={
             "DocEntry": 1, "CardCode": "C1", "DocNum": 84500, "NotaEspelho": "N"})), \
         patch.object(svc, "ops_vivas_de_pedidos_cancelados", AsyncMock(return_value={})), \
         patch.object(svc, "_verifica_process_wbc", AsyncMock(return_value="Y")):
        resultado = asyncio.run(svc.processar_pedidos_novos(MagicMock(), MagicMock(), MagicMock(), ["00125000"]))

    motivo = resultado["com_erro"][0]["motivo"]
    assert "--force" not in motivo
    assert "reprocessar-integrados" in motivo and "duplicadas" in motivo


# ---------------------------------------------------------------------------
# Processar: one group
# ---------------------------------------------------------------------------
def _hana():
    nomes = {
        ligar(q.DOC_NUM_POR_DOC_ENTRY, doc_entry=1)[0]: [{"DocNum": 84433}],
        ligar(q.ENTREGA_MULTIPLA, doc_entry=1)[0]: [{"E": "N"}],
        ligar(q.SELECT_ORC_ITEM_SAP, grp_code=1)[0]: [{"U_INO_ItemSAP": "I000003"}],
        ligar(q.MAX_PRECO_PEDIDO, doc_num="1")[0]: [{"M": 10}],
    }
    hana = MagicMock()
    hana.fetch_all.side_effect = lambda sql, params=(): nomes.get(sql, [])
    hana.fetch_all_values.side_effect = lambda sql, params=(): [[0, 5.0, 500.0]]
    return hana


def _roda_grupo(cria_op, ops_previas=()):
    grupo = [SimpleNamespace(orc_num="00125460", grp_code=7, prd_desc="PP", prd_code="X",
                             linha="1", quantidade=1, id_integracao_orc=1, linha_orc=1)]
    wbc = MagicMock()
    wbc.fetch_all.return_value = [{"CUSTO": 1.0}]
    atualiza_doc = AsyncMock()
    with patch.object(svc, "_pega_doc_entry_ped", AsyncMock(return_value="20176")), \
         patch.object(svc, "_ops_existentes_para_item", AsyncMock(return_value=list(ops_previas))), \
         patch.object(svc, "cria_recurso_rateio", AsyncMock(return_value="GGF_X")), \
         patch.object(svc, "cria_ordem_producao", cria_op), \
         patch.object(svc, "checa_semi_acabado", AsyncMock()), \
         patch.object(svc, "marca_op_nas_linhas", AsyncMock()), \
         patch.object(svc, "_atualiza_doc", atualiza_doc), \
         patch.object(svc, "preenche_log", AsyncMock()):
        motivo = asyncio.run(svc._processa_grupo_producao(
            MagicMock(), wbc, _hana(), "00125460", 7, 1, grupo, grupo, False, {}
        ))
    return motivo, atualiza_doc


def test_op_criada_nao_tem_motivo():
    motivo, atualiza_doc = _roda_grupo(AsyncMock(return_value=(0, 900)))
    assert motivo is None
    assert atualiza_doc.await_args.args[-1] == "Sucesso"


def test_recusa_da_service_layer_vira_motivo_e_nao_aborta():
    motivo, atualiza_doc = _roda_grupo(AsyncMock(side_effect=ServiceLayerError("item inativo")))
    assert "recusou a OP do item I000003" in motivo and "item inativo" in motivo
    assert atualiza_doc.await_count == 0      # ProcessWBC untouched


def test_sessao_recusada_continua_abortando():
    with pytest.raises(ServiceLayerAuthError):
        _roda_grupo(AsyncMock(side_effect=ServiceLayerAuthError("401")))


def test_erro_de_transporte_continua_abortando():
    """A timeout may hide a created OP: guessing "no OP" there is worse than stopping."""
    with pytest.raises(TimeoutError):
        _roda_grupo(AsyncMock(side_effect=TimeoutError("read timeout")))


def test_entrega_multipla_aparece_como_grupo_sem_op():
    motivo, _ = _roda_grupo(AsyncMock(return_value=(0, 0)))
    assert motivo and "entrega múltipla" in motivo


def test_op_previa_nao_manda_forcar():
    motivo, _ = _roda_grupo(AsyncMock(return_value=(0, 900)), ops_previas=[555])
    assert "555" in motivo and "--force" not in motivo


# ---------------------------------------------------------------------------
# Encerrar pedidos fechados
# ---------------------------------------------------------------------------
def _vazio(**k):
    base = {"finalizadas": [], "com_erro": [], "puladas": [], "interrompidas": [], "ignoradas": []}
    return {**base, **k}


def test_erro_da_primeira_tentativa_que_a_segunda_pulou_continua_erro():
    from controleproducao.modules.manutencao_op import fechados

    primeira = _vazio(
        finalizadas=[{"doc_entry": 1, "doc_num": 101}],
        com_erro=[{"doc_entry": 2, "doc_num": 102, "etapa": "encerrar OP", "motivo": "Server disconnected"}],
    )
    segunda = _vazio(ignoradas=[{"doc_entry": 2, "doc_num": 102}])
    with patch.object(fechados, "_encerra_pedido", AsyncMock(side_effect=[primeira, segunda])):
        r = asyncio.run(fechados.encerra_pedido_com_ajustes(
            MagicMock(), MagicMock(), 84396, 1, Decimal("0.01"), False))

    assert r["tentativas"] == 2
    assert [e["doc_num"] for e in r["com_erro"]] == [102]
    assert r["com_erro"][0]["motivo"].startswith("1ª tentativa:")
    assert [o["doc_num"] for o in r["finalizadas"]] == [101]


def test_erro_resolvido_na_segunda_tentativa_nao_volta():
    from controleproducao.modules.manutencao_op import fechados

    primeira = _vazio(com_erro=[{"doc_entry": 2, "doc_num": 102, "etapa": "saída", "motivo": "Server disconnected"}])
    segunda = _vazio(finalizadas=[{"doc_entry": 2, "doc_num": 102}])
    with patch.object(fechados, "_encerra_pedido", AsyncMock(side_effect=[primeira, segunda])):
        r = asyncio.run(fechados.encerra_pedido_com_ajustes(
            MagicMock(), MagicMock(), 84396, 1, Decimal("0.01"), False))

    assert r["com_erro"] == []


def test_lote_com_falha_nao_termina_verde():
    from controleproducao.core.tarefas import Tarefa
    from controleproducao.modules.manutencao_op import fechados

    plano = SimpleNamespace(itens=[{"_pedido": 84396, "_custo": "0.01"}])
    falha = {**_vazio(com_erro=[{"doc_entry": 2, "doc_num": 102, "etapa": "saída", "motivo": "x"}]),
             "ajustes": [], "tentativas": 1}
    tarefa = Tarefa(id="t", nome="n", descricao="d", criada_em=__import__("datetime").datetime.now(),
                    modulo="manutencao_op")
    with patch.object(fechados, "get_settings", return_value=SimpleNamespace(sl_business_place_id=1)), \
         patch.object(fechados, "HanaDirectReader", MagicMock()), \
         patch.object(fechados, "ServiceLayerClient", MagicMock(return_value=AsyncMock())), \
         patch.object(fechados, "encerra_pedido_com_ajustes", AsyncMock(return_value=falha)):
        tarefa.resultado = asyncio.run(fechados.corrotina(plano)(tarefa))
    tarefa.situacao = "concluída"

    assert tarefa.resultado["com_erro"] == [{"pedido": 84396, "op": 102, "etapa": "saída", "motivo": "x"}]
    assert tarefa.com_falhas
