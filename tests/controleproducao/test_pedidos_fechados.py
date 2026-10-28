"""Pedidos fechados com OPs abertas (28/10/2026, PLANO_ENCERRAR_OPS_PEDIDOS_FECHADOS).

What these tests protect:

1. Only the marked orders are closed, and only through a checked plan + single-use token.
2. The fixes around a failed order: zero-cost raw materials get the cost (default 0.01) and
   the order is retried ONCE; semi-finished items are the query's job (``NOT IN`` the order's
   own OPs); inactive items are activated only when asked and always restored; a dropped
   Service Layer connection is retried once.
3. The existing Manutenção de OP page keeps rendering as before (the card lives beside it).
"""
from __future__ import annotations

import asyncio
import inspect
import re
import time
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from controleproducao.core.confirmacao import PLANOS
from controleproducao.core.recusa import Recusa
from controleproducao.core.tarefas import TAREFAS
from controleproducao.main import app
from controleproducao.modules.manutencao_op import fechados

M = "controleproducao.modules.manutencao_op"

LISTA = [
    {"pedido": 84104, "cliente": "Cliente A", "entrega": "2026-10-01", "atrasado": False,
     "ops_abertas": 17, "planejadas": 0, "total_os": 17, "total_os_fechadas": 0},
    {"pedido": 84106, "cliente": "Cliente B", "entrega": None, "atrasado": False,
     "ops_abertas": 10, "planejadas": 2, "total_os": 10, "total_os_fechadas": 0},
]


def _settings():
    return SimpleNamespace(is_production=False, sl_company_db="SBOALTAMIRAHOMOLOG",
                           hana_schema="SBOALTAMIRAHOMOLOG", sl_business_place_id=1)


@pytest.fixture(autouse=True)
def _limpa_registros():
    def _zera():
        for job in list(TAREFAS._jobs.values()):
            job.cancel()
        TAREFAS._jobs.clear()
        TAREFAS._tarefas.clear()
        TAREFAS._ordem.clear()
        TAREFAS._em_execucao.clear()
        PLANOS._planos.clear()

    _zera()
    yield
    _zera()


@pytest.fixture
def cliente(monkeypatch):
    from controleproducao.config import get_settings
    from wbcpython.dashboard.acesso import COOKIE_DE_ACESSO, token_da_chave

    monkeypatch.setenv("OS_API_KEY", "chave-de-teste")
    get_settings.cache_clear()
    with TestClient(app, headers={"Origin": "http://testserver"}) as c:
        c.cookies.set(COOKIE_DE_ACESSO, token_da_chave("chave-de-teste"))
        yield c


@pytest.fixture
def isolado():
    """Everything the routes and the run touch outside the process, doubled."""
    encerra = AsyncMock(return_value={"finalizadas": [{"doc_num": 1}], "com_erro": [], "puladas": [],
                                      "interrompidas": [], "ajustes": [], "tentativas": 1})
    patches = [
        patch("controleproducao.core.web.get_settings", return_value=_settings()),
        patch(f"{M}.router.get_settings", return_value=_settings()),
        patch(f"{M}.router.HanaDirectReader", MagicMock()),
        patch(f"{M}.fechados_router.HanaDirectReader", MagicMock()),
        patch(f"{M}.api_router.HanaDirectReader", MagicMock()),
        patch(f"{M}.fechados.get_settings", return_value=_settings()),
        patch(f"{M}.fechados.HanaDirectReader", MagicMock()),
        patch(f"{M}.fechados.ServiceLayerClient", MagicMock()),
        patch(f"{M}.fechados.pedidos_fechados_com_op", return_value=LISTA),
        patch(f"{M}.fechados.encerra_pedido_com_ajustes", encerra),
    ]
    for p in patches:
        p.start()
    yield encerra
    for p in reversed(patches):
        p.stop()


def _token(html: str) -> str:
    achado = re.search(r'name="token" value="([^"]+)"', html)
    assert achado, "a tela de confirmação não trouxe token"
    return achado.group(1)


def _espera(cliente, tarefa_id: str) -> dict:
    estado: dict = {}
    for _ in range(100):
        estado = cliente.get(f"/tarefas/{tarefa_id}/estado").json()
        if estado.get("terminada"):
            break
        time.sleep(0.02)
    return estado


# ---------------------------------------------------------------------------
# Write gate coverage for the new route files (the existing test scans the old ones)
# ---------------------------------------------------------------------------
def test_rotas_novas_que_gravam_passam_pela_trava():
    from controleproducao.modules.manutencao_op import fechados_api, fechados_router

    so_leem = {"/manutencao-op/pedidos-fechados/encerrar/conferir",
               "/api/manutencao-op/pedidos-fechados/encerrar/conferir"}
    faltando = [
        rota.path
        for modulo in (fechados_router, fechados_api) for rota in modulo.router.routes
        if "POST" in rota.methods and rota.path not in so_leem
        and "avisa_escrita" not in inspect.getsource(rota.endpoint)
    ]
    assert not faltando


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("valor, esperado", [
    ("", Decimal("0.01")), (None, Decimal("0.01")), ("  ", Decimal("0.01")),
    ("0,01", Decimal("0.01")), ("0.01", Decimal("0.01")), ("1,5", Decimal("1.5")), (2, Decimal("2")),
])
def test_custo_vazio_vira_0_01_e_virgula_e_aceita(valor, esperado):
    assert fechados.le_custo(valor) == esperado


@pytest.mark.parametrize("valor", ["0", "-1", "abc", "nan", "inf"])
def test_custo_invalido_e_recusado(valor):
    with pytest.raises(Recusa):
        fechados.le_custo(valor)


def test_pedidos_vazios_ou_invalidos_sao_recusados():
    for ruim in ([], None, ["84a"], [True], [0]):
        with pytest.raises(Recusa):
            fechados.le_pedidos(ruim)
    assert fechados.le_pedidos(["84104", 84106, 84104]) == [84104, 84106]


def test_consulta_passa_o_filtro_de_atrasados_e_traduz_as_linhas():
    leitor = MagicMock()
    leitor.fetch_all.return_value = [{"Pedido": 84104, "Cliente": "X", "Entrega": "2026-10-01 00:00:00",
                                      "Atrasado": "N", "Total_OS": 17, "Total_OS_Fechadas": 0,
                                      "Abertas": 17, "Planejadas": 3}]
    linhas = fechados.pedidos_fechados_com_op(leitor, incluir_atrasados=False)
    assert leitor.fetch_all.call_args.args[1] == (0,)
    assert linhas[0] == {"pedido": 84104, "cliente": "X", "entrega": "2026-10-01", "atrasado": False,
                         "ops_abertas": 17, "planejadas": 3, "total_os": 17, "total_os_fechadas": 0}
    fechados.pedidos_fechados_com_op(leitor, incluir_atrasados=True)
    assert leitor.fetch_all.call_args.args[1] == (1,)


def test_sql_tem_o_filtro_pedido_e_exclui_semiacabado():
    assert "\"StatusPedido\" = 'Fechado'" in fechados.PEDIDOS_FECHADOS_COM_OP
    assert "\"Atrasado\" = 'N'" in fechados.PEDIDOS_FECHADOS_COM_OP
    # The 28/10 lesson: an item an open OP of the same order produces is not a raw material.
    assert 'NOT IN' in fechados.INSUMOS_SEM_CUSTO and 'O."OriginNum" = T."OriginNum"' in fechados.INSUMOS_SEM_CUSTO


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------
def test_plano_recusa_pedido_que_saiu_da_lista():
    with patch(f"{M}.fechados.pedidos_fechados_com_op", return_value=LISTA):
        with pytest.raises(Recusa) as erro:
            fechados.monta_plano(MagicMock(), [84104, 99999], Decimal("0.01"), False)
    assert erro.value.tipo == "fora_da_lista" and "99999" in erro.value.mensagem
    assert not PLANOS._planos


def test_plano_leva_so_os_marcados_com_custo_e_opcao():
    with patch(f"{M}.fechados.pedidos_fechados_com_op", return_value=LISTA):
        plano = fechados.monta_plano(MagicMock(), [84106], Decimal("0.5"), True)
    assert [i["_pedido"] for i in plano.itens] == [84106]
    assert plano.itens[0]["_custo"] == "0.5" and plano.itens[0]["_ativar_inativos"] is True
    assert plano.tipo == fechados.TIPO


# ---------------------------------------------------------------------------
# One order with its fixes
# ---------------------------------------------------------------------------
def _resultado(ok=(), erros=()):
    return {"finalizadas": [{"doc_num": n} for n in ok],
            "com_erro": [{"doc_num": n, "etapa": "saída de insumo", "motivo": m} for n, m in erros],
            "puladas": [], "interrompidas": [], "ignoradas": []}


async def _roda(tentativas, *, ativar=False, insumos=(("ALPRGA", "01"),), inativos=("FLR",)):
    sl = MagicMock()
    sl.post = AsyncMock(return_value={"DocNum": 7})
    sl.get_by_key = AsyncMock(return_value={"Valid": "tNO", "Frozen": "tYES"})
    sl.update_entity = AsyncMock()
    with patch(f"{M}.fechados._encerra_pedido", AsyncMock(side_effect=tentativas)) as encerra, \
         patch(f"{M}.fechados.insumos_sem_custo", return_value=list(insumos)), \
         patch(f"{M}.fechados.itens_inativos", return_value=list(inativos)):
        r = await fechados.encerra_pedido_com_ajustes(sl, MagicMock(), 84104, 1, Decimal("0.01"), ativar)
    return r, sl, encerra


def test_sem_erro_e_uma_tentativa_so():
    r, sl, encerra = asyncio.run(_roda([_resultado(ok=[1, 2])]))
    assert encerra.await_count == 1 and r["tentativas"] == 1
    sl.post.assert_not_awaited()


def test_sem_custo_grava_0_01_e_tenta_uma_vez_mais():
    r, sl, encerra = asyncio.run(_roda([
        _resultado(ok=[1], erros=[(2, "10001287 - Item cost not found for one or more items")]),
        _resultado(ok=[2]),
    ]))
    assert encerra.await_count == 2 and r["tentativas"] == 2
    corpo = sl.post.await_args.args[1]
    assert sl.post.await_args.args[0] == "MaterialRevaluation" and corpo["RevalType"] == "P"
    assert corpo["MaterialRevaluationLines"] == [{"ItemCode": "ALPRGA", "WarehouseCode": "01", "Price": 0.01}]
    assert [o["doc_num"] for o in r["finalizadas"]] == [1, 2]  # the 1st attempt's closings count


def test_segunda_falha_nao_vira_laco():
    erro = [(2, "10001287 - Item cost not found")]
    r, _sl, encerra = asyncio.run(_roda([_resultado(erros=erro), _resultado(erros=erro)]))
    assert encerra.await_count == 2 and r["com_erro"]


def test_inativo_sem_a_opcao_nao_mexe_no_cadastro():
    r, sl, encerra = asyncio.run(_roda([_resultado(erros=[(2, "10001069 - Item FLR is inactive")])]))
    assert encerra.await_count == 1
    sl.update_entity.assert_not_awaited()
    assert any("ativar itens inativos" in a for a in r["ajustes"])


def test_inativo_com_a_opcao_ativa_e_sempre_restaura():
    # The retry itself blows up: the item must still go back to inactive.
    sl = MagicMock()
    sl.get_by_key = AsyncMock(return_value={"Valid": "tNO", "Frozen": "tYES"})
    sl.update_entity = AsyncMock()
    with patch(f"{M}.fechados._encerra_pedido",
               AsyncMock(side_effect=[_resultado(erros=[(2, "10001069 - Item FLR is inactive")]),
                                      RuntimeError("SL caiu")])), \
         patch(f"{M}.fechados.insumos_sem_custo", return_value=[]), \
         patch(f"{M}.fechados.itens_inativos", return_value=["FLR"]):
        with pytest.raises(RuntimeError):
            asyncio.run(fechados.encerra_pedido_com_ajustes(sl, MagicMock(), 84000, 1, Decimal("0.01"), True))
    chamadas = [c.args[2] for c in sl.update_entity.await_args_list]
    assert chamadas == [{"Valid": "tYES", "Frozen": "tNO"}, {"Valid": "tNO", "Frozen": "tYES"}]


def test_service_layer_caiu_tenta_de_novo_uma_vez():
    r, sl, encerra = asyncio.run(_roda([
        _resultado(erros=[(2, "Server disconnected without sending a response.")]),
        _resultado(ok=[2]),
    ]))
    assert encerra.await_count == 2 and not r["com_erro"]
    sl.post.assert_not_awaited()


# ---------------------------------------------------------------------------
# Screen
# ---------------------------------------------------------------------------
def test_pagina_atual_continua_sem_a_lista(cliente, isolado):
    html = cliente.get("/manutencao-op").text
    assert "Consultar pedidos fechados com OPs abertas" in html
    assert 'id="form-fechados"' not in html
    assert 'action="/manutencao-op/buscar"' in html  # the existing sections are still there


def test_consultar_lista_com_checkbox(cliente, isolado):
    html = cliente.get("/manutencao-op/pedidos-fechados").text
    assert 'name="pedidos" value="84104"' in html and 'name="pedidos" value="84106"' in html
    assert "Encerrar selecionados" in html and 'placeholder="0,01"' in html


def test_tela_encerra_so_os_marcados(cliente, isolado):
    conferir = cliente.post("/manutencao-op/pedidos-fechados/encerrar/conferir",
                            data={"pedidos": ["84106"], "custo": ""})
    assert conferir.status_code == 200 and "Confirmar e executar" in conferir.text
    r = cliente.post("/manutencao-op/pedidos-fechados/encerrar/executar",
                     data={"token": _token(conferir.text)}, follow_redirects=False)
    assert r.status_code == 303
    estado = _espera(cliente, r.headers["location"].rsplit("/", 1)[-1])
    assert estado.get("terminada")
    pedidos = [c.args[2] for c in isolado.await_args_list]
    custos = {c.args[4] for c in isolado.await_args_list}
    assert pedidos == [84106] and custos == {Decimal("0.01")}


def test_tela_sem_marcar_nada_e_recusada(cliente, isolado):
    r = cliente.post("/manutencao-op/pedidos-fechados/encerrar/conferir", data={"custo": ""})
    assert r.status_code == 400 and "Marque ao menos um pedido" in r.text


def test_token_nao_serve_duas_vezes(cliente, isolado):
    conferir = cliente.post("/manutencao-op/pedidos-fechados/encerrar/conferir", data={"pedidos": ["84104"]})
    token = _token(conferir.text)
    primeiro = cliente.post("/manutencao-op/pedidos-fechados/encerrar/executar", data={"token": token},
                            follow_redirects=False)
    _espera(cliente, primeiro.headers["location"].rsplit("/", 1)[-1])
    segundo = cliente.post("/manutencao-op/pedidos-fechados/encerrar/executar", data={"token": token})
    assert segundo.status_code == 400


def test_token_do_outro_encerramento_nao_serve_aqui(cliente, isolado):
    plano = PLANOS.criar(operacao="Encerrar OPs", resumo={}, itens=[], tipo="encerrar")
    r = cliente.post("/manutencao-op/pedidos-fechados/encerrar/executar", data={"token": plano.token})
    assert r.status_code == 400
    isolado.assert_not_awaited()


# ---------------------------------------------------------------------------
# JSON API
# ---------------------------------------------------------------------------
H = {"X-API-Key": "chave-de-teste"}


def test_api_lista(cliente, isolado):
    r = cliente.get("/api/manutencao-op/pedidos-fechados", headers=H).json()
    assert r["ok"] and r["total_pedidos"] == 2 and r["total_ops_abertas"] == 27


def test_api_sem_chave_e_recusada(cliente, isolado):
    cliente.cookies.clear()
    assert cliente.get("/api/manutencao-op/pedidos-fechados").status_code == 401


def test_api_confere_e_executa_so_os_marcados(cliente, isolado):
    plano = cliente.post("/api/manutencao-op/pedidos-fechados/encerrar/conferir", headers=H,
                         json={"pedidos": [84104], "custo": "0,05"}).json()
    assert plano["ok"] and plano["plano"]["resumo"]["custo"] == "0.05"
    sem_solicitante = cliente.post("/api/manutencao-op/pedidos-fechados/encerrar/executar", headers=H,
                                   json={"token": plano["plano"]["token"]})
    assert sem_solicitante.status_code == 400
    r = cliente.post("/api/manutencao-op/pedidos-fechados/encerrar/executar", headers=H,
                     json={"token": plano["plano"]["token"], "solicitante": "teste"})
    assert r.status_code == 202
    _espera(cliente, r.json()["execucao"]["id"])
    assert [c.args[2] for c in isolado.await_args_list] == [84104]


def test_api_pedido_fora_da_lista_e_409(cliente, isolado):
    r = cliente.post("/api/manutencao-op/pedidos-fechados/encerrar/conferir", headers=H,
                     json={"pedidos": [12345]})
    assert r.status_code == 409 and r.json()["tipo"] == "fora_da_lista"
