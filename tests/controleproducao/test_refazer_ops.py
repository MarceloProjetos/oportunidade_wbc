"""The sync redoes an order's OPs when the WBC tree moved past them (05/11/2026, order 84544).

What matters most, in order: nothing is cancelled when an OP has started, a false "changed" never
cancels anything, and a refusal that only Processar would give is caught before Reprocessar.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from controleproducao.config import get_settings
from controleproducao.core.tarefas import TAREFAS, Tarefa
from controleproducao.main import app
from controleproducao.modules.pedidos_wbc import queries as q
from controleproducao.modules.pedidos_wbc import refazer

CHAVE = "chave-de-teste"
ROTA = "/api/pedidos-wbc/refazer/84544"
CABECALHO = {"X-API-Key": CHAVE}
CORPO = {"solicitante": "API 8077 (teste)"}


def _linha(descricao, quantidade=14.0, cor="CZ-PAD", nivel=1):
    return {"Descricao": descricao, "Cor": cor, "Nivel": nivel, "Quantidade": quantidade}


ARVORE_300 = [_linha("DISTANCIADOR PPL 300MM C/ PARAF."), _linha("AÇO ROLO FQ", 8.572, "S/ COR", 3)]
ARVORE_400 = [_linha("DISTANCIADOR PPL 400MM C/ PARAF."), _linha("AÇO ROLO FQ", 10.77, "S/ COR", 3)]
PLANEJADA = {"DocNum": 161071, "Status": "P", "Concluida": 0, "Baixada": 0}
PEDIDO = {"Orcamento": "00125063", "Detalhe": 547801, "ProcessWBC": "Y", "DocStatus": "O",
          "CANCELED": "N", "NotaEspelho": "N"}


def _leitores(pedido=PEDIDO, ops=(PLANEJADA,), detalhe=ARVORE_300, wbc=ARVORE_400, sitcod=60):
    """HANA and WBC doubles that answer by query, so a wrong query shows up as no data."""
    respostas_hana = {q.SITUACAO_PARA_REFAZER: [pedido] if pedido else [],
                      q.ARVORE_DO_DETALHE: list(detalhe), q.OPS_VIVAS_DO_PEDIDO: list(ops)}
    respostas_wbc = {q.SITUACAO_NO_WBC: [{"REVISAO": "B", "SITCOD": sitcod}], q.ARVORE_NO_WBC: list(wbc)}

    def por_consulta(respostas):
        def responde(sql, _params=()):
            for modelo, linhas in respostas.items():
                if modelo.split("WHERE")[0].replace("{", "").strip()[:40] in sql:
                    return linhas
            return []
        return responde

    hana, leitor_wbc = MagicMock(), MagicMock()
    hana.fetch_all.side_effect = por_consulta(respostas_hana)
    leitor_wbc.fetch_all.side_effect = por_consulta(respostas_wbc)
    return hana, leitor_wbc


def _avalia(orfas=None, **kw):
    hana, wbc = _leitores(**kw)
    with patch.object(refazer.service, "ops_vivas_de_pedidos_cancelados", AsyncMock(return_value=orfas or {})):
        return refazer.avalia(84544, hana, wbc)


# ---------------------------------------------------------------------------
# Comparing the trees: a false "changed" would cancel OPs
# ---------------------------------------------------------------------------
def test_arvore_do_84544_mudou() -> None:
    assert refazer.arvore_mudou(ARVORE_300, ARVORE_400)


@pytest.mark.parametrize("wbc", [
    [_linha("distanciador  ppl 300mm c/ paraf."), _linha("AO ROLO FQ", 8.572, "S/ COR", 3)],  # accent lost
    [_linha("DISTANCIADOR PPL 300MM C/ PARAF.", 14.000001), _linha("AÇO ROLO FQ", 8.57200001, "s/ cor", 3)],
    list(reversed(ARVORE_300)),
    ARVORE_300 + [ARVORE_300[0]],  # the snapshot's SELECT DISTINCT drops repeated lines
])
def test_diferenca_de_formato_nao_e_mudanca(wbc) -> None:
    assert not refazer.arvore_mudou(ARVORE_300, wbc)


@pytest.mark.parametrize("wbc", [
    ARVORE_300[:1],
    ARVORE_300 + [_linha("PECA NOVA")],
    [_linha("DISTANCIADOR PPL 300MM C/ PARAF.", 15.0), ARVORE_300[1]],
    [_linha("DISTANCIADOR PPL 300MM C/ PARAF.", cor="LA-LB"), ARVORE_300[1]],
])
def test_mudanca_real(wbc) -> None:
    assert refazer.arvore_mudou(ARVORE_300, wbc)


# ---------------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------------
def test_arvore_nova_e_nada_iniciado_refaz() -> None:
    avaliacao = _avalia()
    assert avaliacao.situacao == refazer.REFAZER
    assert avaliacao.orc_num == "00125063" and avaliacao.revisao == "B"


@pytest.mark.parametrize("op", [
    {**PLANEJADA, "Status": "R"},
    {**PLANEJADA, "Status": "L"},
    {**PLANEJADA, "Baixada": 1.5},
    {**PLANEJADA, "Concluida": 2},
])
def test_uma_op_iniciada_impede_e_avisa(op) -> None:
    avaliacao = _avalia(ops=[{**PLANEJADA, "DocNum": 161072}, op])
    assert avaliacao.situacao == refazer.JA_PRODUZIDO
    assert avaliacao.ops == [161071] and "161071" in avaliacao.motivo


def test_arvore_igual_so_atualiza() -> None:
    assert _avalia(wbc=ARVORE_300).situacao == refazer.EM_DIA


@pytest.mark.parametrize("kw", [
    {"pedido": None},
    {"pedido": {**PEDIDO, "ProcessWBC": "N"}},
    {"pedido": {**PEDIDO, "DocStatus": "C"}},
    {"pedido": {**PEDIDO, "CANCELED": "Y"}},
    {"pedido": {**PEDIDO, "Orcamento": None}},
    {"pedido": {**PEDIDO, "Detalhe": None}},
    {"sitcod": 30},        # the quote is being edited
    {"wbc": []},           # could not compare
    {"detalhe": []},       # could not compare
    {"ops": []},           # nothing to redo
])
def test_sem_base_para_refazer_so_atualiza(kw) -> None:
    assert _avalia(**kw).situacao == refazer.EM_DIA


def test_nota_espelho_recusa_antes_de_cancelar() -> None:
    avaliacao = _avalia(pedido={**PEDIDO, "NotaEspelho": "Y"})
    assert avaliacao.situacao == refazer.NAO_APLICADA and "Nota Espelho" in avaliacao.motivo


def test_op_orfa_recusa_antes_de_cancelar() -> None:
    avaliacao = _avalia(orfas={"00125063": [{"pedido": 84500, "op": 150000, "status": "P"}]})
    assert avaliacao.situacao == refazer.NAO_APLICADA and "150000" in avaliacao.motivo


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------
def _tarefa() -> Tarefa:
    return Tarefa(id="t", nome=refazer.NOME_REFAZER, descricao="Pedido 84544", criada_em=datetime.now(),
                  modulo="pedidos_wbc", parada_combinada=True)


def test_reprocessa_e_depois_processa_sem_forcar() -> None:
    roda = AsyncMock(side_effect=[{"atualizados": ["00125063"]}, {"processados": ["00125063"]}])
    with patch.object(refazer.acoes, "_roda_pedidos", roda):
        asyncio.run(refazer.corrotina(84544, "00125063")(_tarefa()))
    assert [c.kwargs["modo"] for c in roda.await_args_list] == ["reprocessar", "processar"]
    assert all(c.kwargs["force"] is False for c in roda.await_args_list)


def test_reprocessar_com_erro_nao_processa_e_avisa() -> None:
    roda = AsyncMock(return_value={"com_erro": [{"motivo": "x"}]})
    tarefa = _tarefa()
    with patch.object(refazer.acoes, "_roda_pedidos", roda):
        asyncio.run(refazer.corrotina(84544, "00125063")(tarefa))
    assert roda.await_count == 1
    assert any("NAO foram recriadas" in linha for linha in tarefa.linhas)


# ---------------------------------------------------------------------------
# The route
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _limpa_tarefas():
    def _zera():
        for job in list(TAREFAS._jobs.values()):
            job.cancel()
        TAREFAS._jobs.clear()
        TAREFAS._tarefas.clear()
        TAREFAS._ordem.clear()
        TAREFAS._em_execucao.clear()

    _zera()
    yield
    _zera()


@pytest.fixture
def c(monkeypatch):
    monkeypatch.setenv("OS_API_KEY", CHAVE)
    get_settings.cache_clear()
    with TestClient(app) as cliente:
        yield cliente


def test_rota_exige_chave_e_solicitante(c) -> None:
    assert c.post(ROTA, json=CORPO).status_code == 401
    assert c.post(ROTA, headers=CABECALHO, json={}).status_code == 400


def test_rota_nao_dispara_quando_ja_produzido(c) -> None:
    avaliacao = refazer.Avaliacao(refazer.JA_PRODUZIDO, "ja tem OP", "00125063", "B", [161071])
    with patch.object(refazer, "avalia_agora", return_value=avaliacao), \
         patch.object(refazer.acoes, "dispara") as dispara:
        r = c.post(ROTA, headers=CABECALHO, json=CORPO)
    assert r.status_code == 200
    assert r.json()["situacao"] == refazer.JA_PRODUZIDO and r.json()["motivo"] == "ja tem OP"
    dispara.assert_not_called()


def test_rota_dispara_quando_mudou(c) -> None:
    avaliacao = refazer.Avaliacao(refazer.REFAZER, None, "00125063", "B", [161071])
    corrotina = AsyncMock(return_value={})
    with patch.object(refazer, "avalia_agora", return_value=avaliacao), \
         patch("controleproducao.modules.pedidos_wbc.api_router.avisa_escrita") as trava, \
         patch.object(refazer, "corrotina", return_value=corrotina) as monta:
        r = c.post(ROTA, headers=CABECALHO, json=CORPO)
    assert r.status_code == 202
    assert r.json()["situacao"] == refazer.REFAZENDO and r.json()["execucao"]["id"]
    trava.assert_called_once()
    monta.assert_called_once_with(84544, "00125063")


def test_rota_com_a_trava_de_escrita_nao_dispara(c) -> None:
    avaliacao = refazer.Avaliacao(refazer.REFAZER, None, "00125063", "B", [161071])
    with patch.object(refazer, "avalia_agora", return_value=avaliacao), \
         patch("controleproducao.modules.pedidos_wbc.api_router.avisa_escrita", side_effect=RuntimeError("x")), \
         patch.object(refazer.acoes, "dispara") as dispara, \
         pytest.raises(RuntimeError):
        c.post(ROTA, headers=CABECALHO, json=CORPO)
    dispara.assert_not_called()


def test_rota_durante_o_proprio_refazer_nao_reavalia(c) -> None:
    rodando = MagicMock(nome=None, descricao=refazer.descricao(84544), id="t", parada_combinada=True)
    rodando.nome = refazer.NOME_REFAZER
    rodando.para_json.return_value = {"id": "t"}
    with patch.object(TAREFAS, "em_execucao", return_value=rodando), \
         patch.object(refazer, "avalia_agora") as avalia:
        r = c.post(ROTA, headers=CABECALHO, json=CORPO)
    assert r.status_code == 200 and r.json()["situacao"] == refazer.REFAZENDO
    avalia.assert_not_called()


def test_rota_sap_fora_nao_dispara(c) -> None:
    with patch.object(refazer, "avalia_agora", side_effect=OSError("sem rede")), \
         patch.object(refazer.acoes, "dispara") as dispara:
        r = c.post(ROTA, headers=CABECALHO, json=CORPO)
    assert r.status_code == 502
    dispara.assert_not_called()
