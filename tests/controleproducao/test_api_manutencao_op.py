"""JSON API of the Manutenção de OP (29/09/2026, F3 of docs/PLANO_API_MANUTENCAO_OP.md).

What these tests hold, most important first:

1. **The API decides nothing on its own.** A refusal carries the screen's text, word for
   word — the parity tests post the same thing to both and compare.
2. **Nothing writes without the gate:** no key configured → 503; a production write off
   the .11 → 503; and the service is never reached.
3. **Only ``X-API-Key`` opens ``/api/``** — not the screen's cookie, not ``?key=``.
4. **The single-use token is spent only when the plan can actually start** (busy module,
   missing requester or refused write leave it valid).
5. **Executions started here are the screen's executions:** same one-per-module lock, same
   Execuções list — plus who asked.

Everything outside the process is doubled: HANA readers are MagicMocks, the Service Layer is
`_SLFalso`, and the service functions that would talk to either are patched per test.
"""
from __future__ import annotations

import asyncio
import html
import logging
import re
import threading
import time
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from controleproducao.config import get_settings
from controleproducao.core.confirmacao import PLANOS
from controleproducao.core.tarefas import TAREFAS, Tarefa
from controleproducao.main import app
from wbcpython.dashboard.acesso import COOKIE_DE_ACESSO, token_da_chave

CHAVE = "chave-de-teste"
API = "/api/manutencao-op"
CABECALHO = {"X-API-Key": CHAVE}
SVC = "controleproducao.modules.manutencao_op.service"
ACOES = "controleproducao.modules.manutencao_op.acoes"


# ---------------------------------------------------------------------------
# Scaffolding
# ---------------------------------------------------------------------------
def _settings(producao: bool = False):
    return SimpleNamespace(
        is_production=producao,
        sl_company_db="SBOALTAMIRAPROD" if producao else "SBOALTAMIRAHOMOLOG",
        hana_schema="SBOALTAMIRAHOMOLOG",
        sl_business_place_id=1,
    )


class _SLFalso:
    """Stands in for `ServiceLayerClient` in the executions: never opens a socket."""

    def __init__(self, *_a, **_k) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


def _op(doc_entry: int, doc_num: int, status: str, planejada: float = 10.0, apontada: float = 0.0,
        item: str = "PAR000PADRA000000000") -> dict:
    return {"doc_entry": doc_entry, "doc_num": doc_num, "status": status, "item_code": item,
            "planejada": planejada, "apontada": apontada, "pedido": 84245}


@pytest.fixture(autouse=True)
def _limpa_registros():
    """Plans and tasks are process globals; a task left running would make the next test
    meet "já existe execução em andamento" (see the same fixture in test_web_modulos.py)."""
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
def c(monkeypatch):
    """Key configured; the client ALSO carries the screen's cookie and a same-origin header,
    so one client drives both the screen (cookie) and the API (``headers=CABECALHO``)."""
    monkeypatch.setenv("OS_API_KEY", CHAVE)
    get_settings.cache_clear()
    with TestClient(app, headers={"Origin": "http://testserver"}) as cliente:
        cliente.cookies.set(COOKIE_DE_ACESSO, token_da_chave(CHAVE))
        yield cliente


@pytest.fixture
def ambiente():
    """Non-production target and no real HANA reader, for the API and the screen."""
    with patch("controleproducao.core.web.get_settings", return_value=_settings()), \
         patch("controleproducao.modules.manutencao_op.api_router.HanaDirectReader", MagicMock()), \
         patch("controleproducao.modules.manutencao_op.router.HanaDirectReader", MagicMock()), \
         patch("controleproducao.modules.manutencao_op.router.get_settings", return_value=_settings()):
        yield


@pytest.fixture
def execucao_falsa():
    """What an execution touches outside the process, doubled (kept open until it ends)."""
    with patch(f"{ACOES}.get_settings", return_value=_settings()), \
         patch(f"{ACOES}.ServiceLayerClient", _SLFalso), \
         patch(f"{ACOES}.HanaDirectReader", MagicMock()):
        yield


def _espera(c, tarefa_id: str, teto: int = 200) -> dict:
    estado: dict = {}
    for _ in range(teto):
        estado = c.get(f"{API}/execucoes/{tarefa_id}", headers=CABECALHO).json()["execucao"]
        if estado.get("terminada"):
            break
        time.sleep(0.01)
    return estado


def _texto(pagina: str) -> str:
    """HTML without tags, entities decoded, spaces normalised — what a person reads."""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", pagina)).split())


class _Trava:
    """A `muda_status` that holds the module busy until `solta()` — across threads, because
    the task runs on the TestClient's loop thread."""

    def __init__(self) -> None:
        self.evento = threading.Event()

    async def __call__(self, sl, ops, acao):
        while not self.evento.is_set():
            await asyncio.sleep(0.005)
        return {"alteradas": list(ops), "com_erro": [], "ignoradas": []}

    def solta(self) -> None:
        self.evento.set()


# ---------------------------------------------------------------------------
# 1. The key: only X-API-Key opens /api/
# ---------------------------------------------------------------------------
def test_sem_x_api_key_e_401_mesmo_com_o_cookie_da_tela(c, ambiente):
    sem_cabecalho = c.get(f"{API}/pedidos/84245/ops")           # the cookie IS on the client
    errada = c.get(f"{API}/pedidos/84245/ops", headers={"X-API-Key": "outra"})
    na_url = c.get(f"{API}/pedidos/84245/ops?key={CHAVE}")

    for resposta in (sem_cabecalho, errada, na_url):
        assert resposta.status_code == 401
        assert resposta.json() == {"ok": False, "tipo": "sem_chave", "motivo": "X-API-Key ausente ou incorreta."}


def test_sem_chave_configurada_le_mas_nao_grava(ambiente):
    """Same rule as the screen: no OS_API_KEY → open for reading, every write 503."""
    with TestClient(app) as aberto, \
         patch(f"{SVC}.buscar_ops", return_value=[]), \
         patch(f"{SVC}.levanta_ops") as levanta:
        leitura = aberto.get(f"{API}/pedidos/84245/ops")
        escrita = aberto.post(f"{API}/liberar", json={"ops": [9001], "solicitante": "joao"})

    assert leitura.status_code == 200
    assert escrita.status_code == 503
    assert escrita.json()["tipo"] == "escrita_desabilitada"
    assert "OS_API_KEY" in escrita.json()["motivo"]
    levanta.assert_not_called()


def test_producao_fora_da_11_nao_grava(c):
    """The IP gate: the conftest pins PRODUCTION_MACHINE_IP to a never-local address."""
    with patch("controleproducao.core.web.get_settings", return_value=_settings(producao=True)), \
         patch(f"{SVC}.levanta_ops") as levanta:
        resposta = c.post(f"{API}/liberar", json={"ops": [9001], "solicitante": "joao"}, headers=CABECALHO)

    assert resposta.status_code == 503
    assert resposta.json()["tipo"] == "escrita_desabilitada"
    assert "máquina de produção" in resposta.json()["motivo"]
    levanta.assert_not_called()


def test_erros_da_propria_api_saem_no_mesmo_formato(c, ambiente):
    rota = c.get(f"{API}/nao-existe", headers=CABECALHO)
    metodo = c.get(f"{API}/liberar", headers=CABECALHO)
    nao_json = c.post(f"{API}/liberar", content=b"isto nao e json",
                      headers={**CABECALHO, "Content-Type": "application/json"})
    lista = c.post(f"{API}/liberar", json=[9001], headers=CABECALHO)

    assert (rota.status_code, rota.json()["tipo"]) == (404, "nao_encontrada")
    assert (metodo.status_code, metodo.json()["tipo"]) == (405, "metodo_invalido")
    assert (nao_json.status_code, nao_json.json()["tipo"]) == (400, "invalido")
    assert "objeto JSON" in nao_json.json()["motivo"]
    assert (lista.status_code, lista.json()["motivo"]) == (400, "O corpo deve ser um objeto JSON.")
    for resposta in (rota, metodo, nao_json, lista):
        assert resposta.json()["ok"] is False


# ---------------------------------------------------------------------------
# 2. Search
# ---------------------------------------------------------------------------
def _linha(numero: int, status: str, planejada, apontada) -> dict:
    return {
        "Número OP": numero, "Status": status, "Cód. Produto": "PAR000", "Produto": "CONJ PARAFUSO",
        "Qtde. Planejada": planejada, "Qtde. Apontada": apontada,
        "Qtde. Restante": planejada - apontada, "Data Pedido": datetime(2026, 9, 22),
        "Data inicio": "2026-09-23 00:00:00", "Data Vencimento": None,
        "Cod. Cliente": "C0001", "Cliente": "CLIENTE TESTE", "Status (descrição)": "x",
    }


def test_busca_devolve_as_ops_com_as_acoes_possiveis(c, ambiente):
    linhas = [
        _linha(155747, "P", Decimal("20"), Decimal("0")),
        _linha(155744, "R", Decimal("20"), Decimal("5.5")),
        _linha(155746, "L", Decimal("20"), Decimal("20")),
    ]
    with patch(f"{SVC}.buscar_ops", return_value=linhas) as buscar:
        resposta = c.get(f"{API}/pedidos/84245/ops?status_de=L&status_ate=R", headers=CABECALHO)

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert (corpo["ok"], corpo["pedido"], corpo["total"]) == (True, 84245, 3)
    primeira = corpo["ops"][0]
    assert primeira == {
        "op": 155747, "status": "P", "status_desc": "Planejada", "item": "PAR000",
        "produto": "CONJ PARAFUSO", "planejada": 20, "apontada": 0, "restante": 20,
        "data_pedido": "2026-09-22", "data_inicio": "2026-09-23", "data_vencimento": None,
        "cliente_codigo": "C0001", "cliente": "CLIENTE TESTE",
        "acoes_possiveis": ["liberar", "encerrar"],
    }
    assert corpo["ops"][1]["apontada"] == 5.5 and corpo["ops"][1]["acoes_possiveis"] == ["encerrar"]
    assert corpo["ops"][2]["acoes_possiveis"] == []
    # The filters reach the screen's own search function untouched.
    assert buscar.call_args.args[1:] == ("84245", None, None, "L", "R")


def test_busca_com_faixa_invertida_devolve_a_mensagem_da_tela(c, ambiente):
    """Real `service.buscar_ops`: the range is refused before any query, with its own text."""
    api = c.get(f"{API}/pedidos/84245/ops?op_de=9010&op_ate=9001", headers=CABECALHO)
    tela = c.post("/manutencao-op/buscar", data={"doc_num": "84245", "op_de": "9010", "op_ate": "9001"})

    assert api.status_code == 400 and api.json()["tipo"] == "invalido"
    assert "Faixa de OP invertida" in api.json()["motivo"]
    assert api.json()["motivo"] in _texto(tela.text)


def test_busca_com_hana_fora_e_502_sem_vazar_detalhe(c, ambiente):
    with patch(f"{SVC}.buscar_ops", side_effect=RuntimeError("socket 10.0.0.9:30015 recusado")):
        resposta = c.get(f"{API}/pedidos/84245/ops", headers=CABECALHO)

    assert resposta.status_code == 502
    assert resposta.json()["tipo"] == "sap_indisponivel"
    assert "10.0.0.9" not in resposta.text


# ---------------------------------------------------------------------------
# 3. Liberar
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("solicitante", [None, "", "   ", "x" * 81, "joao\nOP 1 encerrada", 42])
def test_liberar_exige_solicitante_valido(c, ambiente, solicitante):
    corpo = {"ops": [9001]} if solicitante is None else {"ops": [9001], "solicitante": solicitante}
    with patch(f"{SVC}.levanta_ops") as levanta:
        resposta = c.post(f"{API}/liberar", json=corpo, headers=CABECALHO)

    assert resposta.status_code == 400
    assert resposta.json()["tipo"] == "invalido" and "solicitante" in resposta.json()["motivo"]
    levanta.assert_not_called()


@pytest.mark.parametrize("ops,trecho", [
    (None, "Nenhuma OP selecionada."),
    ([], "Nenhuma OP selecionada."),
    ("9001", "deve ser uma lista"),
    ([True], "deve conter números"),
    ([9.5], "deve conter números"),
])
def test_liberar_valida_a_lista_de_ops(c, ambiente, ops, trecho):
    corpo = {"solicitante": "joao"} if ops is None else {"ops": ops, "solicitante": "joao"}
    with patch(f"{SVC}.levanta_ops") as levanta:
        resposta = c.post(f"{API}/liberar", json=corpo, headers=CABECALHO)
    assert resposta.status_code == 400 and trecho in resposta.json()["motivo"]
    levanta.assert_not_called()


def test_numero_de_op_com_letra_usa_a_mensagem_do_servico(c, ambiente):
    """Real `levanta_ops`: the digits are validated there, with the text the screen shows."""
    resposta = c.post(f"{API}/liberar", json={"ops": ["12a"], "solicitante": "joao"}, headers=CABECALHO)
    assert resposta.status_code == 400
    assert "Número da OP inválido" in resposta.json()["motivo"]


def test_op_terminal_recusa_o_lote_inteiro_com_o_texto_da_tela(c, ambiente):
    ops = [_op(1, 155747, "P"), _op(2, 155746, "L")]
    with patch(f"{SVC}.levanta_ops", return_value=ops), \
         patch(f"{SVC}.muda_status", AsyncMock()) as mudar:
        api = c.post(f"{API}/liberar", json={"ops": [155747, 155746], "solicitante": "joao"},
                     headers=CABECALHO)
        tela = c.post("/manutencao-op/status", data={"op_docnums": ["155747", "155746"], "acao": "l"})

    assert api.status_code == 409
    corpo = api.json()
    assert corpo["tipo"] == "status_terminal"
    assert corpo["detalhes"] == [{"op": 155746, "item": "PAR000PADRA000000000", "status": "Encerrada"}]
    assert tela.status_code == 400
    assert corpo["motivo"] in _texto(tela.text)            # word for word
    mudar.assert_not_called()


def test_nenhuma_op_encontrada_e_404(c, ambiente):
    with patch(f"{SVC}.levanta_ops", return_value=[]):
        resposta = c.post(f"{API}/liberar", json={"ops": [9001], "solicitante": "joao"}, headers=CABECALHO)
    assert resposta.status_code == 404
    assert resposta.json() == {"ok": False, "tipo": "nao_encontrada",
                               "motivo": "Nenhuma das OPs informadas foi encontrada."}


def test_liberar_roda_em_segundo_plano_e_aparece_nas_execucoes(c, ambiente, execucao_falsa, caplog):
    ops = [_op(1, 9001, "P"), _op(2, 9002, "P")]
    resultado = {"alteradas": ops, "com_erro": [], "ignoradas": []}
    with patch(f"{SVC}.levanta_ops", return_value=ops), \
         patch(f"{SVC}.muda_status", AsyncMock(return_value=resultado)) as mudar, \
         caplog.at_level(logging.WARNING, logger=ACOES):
        resposta = c.post(f"{API}/liberar", json={"ops": [9001, 9002], "solicitante": "joao.silva"},
                          headers=CABECALHO)
        assert resposta.status_code == 202, resposta.text
        execucao = resposta.json()["execucao"]
        estado = _espera(c, execucao["id"])

    assert execucao["nome"] == "Liberar OPs" and execucao["descricao"] == "9001, 9002"
    assert (execucao["origem"], execucao["solicitante"]) == ("api", "joao.silva")
    assert execucao["estado"] == f"{API}/execucoes/{execucao['id']}"
    assert estado["terminada"] and estado["desfecho"] == "ok"
    assert mudar.await_args.args[1:] == (ops, "l")
    assert (f"Execução {execucao['id']} iniciada: Liberar OPs (9001, 9002) · origem api · "
            "solicitante joao.silva") in caplog.text
    # The screen's Execuções list shows it, with who asked.
    lista = _texto(c.get("/tarefas").text)
    assert "Liberar OPs" in lista and "por joao.silva · API" in lista
    assert "por joao.silva · API" in _texto(c.get(f"/tarefas/{execucao['id']}").text)


def test_modulo_ocupado_devolve_a_execucao_em_andamento(c, ambiente, execucao_falsa):
    trava = _Trava()
    with patch(f"{SVC}.levanta_ops", return_value=[_op(1, 9001, "P")]), \
         patch(f"{SVC}.muda_status", trava):
        primeira = c.post(f"{API}/liberar", json={"ops": [9001], "solicitante": "joao"}, headers=CABECALHO)
        segunda = c.post(f"{API}/liberar", json={"ops": [9001], "solicitante": "maria"}, headers=CABECALHO)
        trava.solta()
        _espera(c, primeira.json()["execucao"]["id"])

    assert primeira.status_code == 202
    assert segunda.status_code == 409 and segunda.json()["tipo"] == "ocupado"
    andamento = segunda.json()["execucao_em_andamento"]
    assert andamento["id"] == primeira.json()["execucao"]["id"]
    assert andamento["solicitante"] == "joao" and andamento["estado"].endswith(andamento["id"])


# ---------------------------------------------------------------------------
# 4. Encerrar — checked plan + single-use token
# ---------------------------------------------------------------------------
_OPS_DO_PEDIDO = [
    _op(11, 9001, "P"),
    _op(12, 9002, "R", apontada=4),
    _op(13, 9003, "L"),
    _op(14, 9004, "R", apontada=10),
]


def _conferir(c, corpo: dict):
    with patch(f"{SVC}.levanta_ops", return_value=[dict(o) for o in _OPS_DO_PEDIDO]), \
         patch(f"{SVC}._componentes_por_op", return_value={}):
        return c.post(f"{API}/encerrar/conferir", json=corpo, headers=CABECALHO)


@pytest.mark.parametrize("corpo", [{}, {"pedido": 84245, "ops": [9001]}])
def test_conferir_exige_ops_ou_pedido_com_o_texto_da_tela(c, ambiente, corpo):
    api = c.post(f"{API}/encerrar/conferir", json=corpo, headers=CABECALHO)
    tela = c.post("/manutencao-op/encerrar/conferir",
                  data={"pedido": str(corpo.get("pedido", "")), "op_docnums": [str(o) for o in corpo.get("ops", [])]})
    assert api.status_code == 400
    assert api.json()["motivo"] == "Selecione OPs na lista OU informe um pedido — não os dois."
    assert api.json()["motivo"] in _texto(tela.text)


def test_conferir_devolve_o_plano_com_token_sem_gravar(c, ambiente):
    with patch(f"{SVC}.finalizar_ops", AsyncMock()) as finalizar:
        resposta = _conferir(c, {"pedido": "84245"})

    assert resposta.status_code == 200, resposta.text
    plano = resposta.json()["plano"]
    assert plano["operacao"] == "Encerrar OPs do pedido 84245"
    assert plano["resumo"] == {"a_encerrar": 2, "a_liberar_antes": 1, "listadas": 4}
    assert [i["op"] for i in plano["itens"]] == [9001, 9002, 9003, 9004]   # ordena_por_dependencia
    assert plano["itens"][0] == {
        "ordem": 1, "op": 9001, "doc_entry": 11, "item": "PAR000PADRA000000000", "planejada": 10,
        "apontada": 0, "status": "P", "status_atual": "Planejada",
        "acao": "LIBERAR + saída + entrada + encerrar", "processar": True,
    }
    assert [i["processar"] for i in plano["itens"]] == [True, True, False, False]
    assert plano["itens"][3]["acao"] == "ignorada (apontada = planejada)"
    assert datetime.fromisoformat(plano["valido_ate"]) > datetime.now()
    assert PLANOS.obter(plano["token"]) is not None
    finalizar.assert_not_called()


def test_conferir_com_ciclo_e_409_com_as_ops(c, ambiente):
    ops = [_op(1, 101, "P", item="A"), _op(2, 102, "P", item="B")]
    with patch(f"{SVC}.levanta_ops", return_value=ops), \
         patch(f"{SVC}._componentes_por_op", return_value={1: {"B"}, 2: {"A"}}):
        resposta = c.post(f"{API}/encerrar/conferir", json={"pedido": 84245}, headers=CABECALHO)

    assert resposta.status_code == 409 and resposta.json()["tipo"] == "ciclo"
    assert resposta.json()["detalhes"] == [{"op": 101, "item": "A"}, {"op": 102, "item": "B"}]


def test_conferir_sem_nada_a_encerrar_explica_cada_op(c, ambiente):
    with patch(f"{SVC}.levanta_ops", return_value=[_op(1, 9003, "L"), _op(2, 9005, "C")]), \
         patch(f"{SVC}._componentes_por_op", return_value={}):
        resposta = c.post(f"{API}/encerrar/conferir", json={"ops": [9003, 9005]}, headers=CABECALHO)

    assert resposta.status_code == 409 and resposta.json()["tipo"] == "nada_a_encerrar"
    assert [i["acao"] for i in resposta.json()["itens"]] == [
        "já encerrada — ignorada", "cancelada — não pode ser encerrada"]


def test_executar_roda_so_o_que_o_plano_marcou_e_o_token_nao_vale_de_novo(c, ambiente, execucao_falsa):
    token = _conferir(c, {"pedido": 84245}).json()["plano"]["token"]
    resultado = {"finalizadas": [], "com_erro": [], "ignoradas": [], "puladas": []}
    with patch(f"{SVC}.levanta_ops", return_value=[dict(o) for o in _OPS_DO_PEDIDO]), \
         patch(f"{SVC}._componentes_por_op", return_value={}), \
         patch(f"{SVC}.finalizar_ops", AsyncMock(return_value=resultado)) as finalizar:
        resposta = c.post(f"{API}/encerrar/executar", json={"token": token, "solicitante": "pcp.ana"},
                          headers=CABECALHO)
        assert resposta.status_code == 202, resposta.text
        estado = _espera(c, resposta.json()["execucao"]["id"])
        de_novo = c.post(f"{API}/encerrar/executar", json={"token": token, "solicitante": "pcp.ana"},
                         headers=CABECALHO)

    assert estado["terminada"] and estado["nome"] == "Encerrar OPs do pedido 84245"
    assert (estado["origem"], estado["solicitante"]) == ("api", "pcp.ana")
    assert [o["doc_num"] for o in finalizar.await_args.args[2]] == [9001, 9002]
    assert de_novo.status_code == 409 and de_novo.json()["tipo"] == "confirmacao_invalida"
    assert "já utilizada" in de_novo.json()["motivo"]


def test_token_vencido_e_409(c, ambiente):
    token = _conferir(c, {"pedido": 84245}).json()["plano"]["token"]
    PLANOS._planos[token].criado_em = datetime.now() - timedelta(minutes=30)
    resposta = c.post(f"{API}/encerrar/executar", json={"token": token, "solicitante": "joao"},
                      headers=CABECALHO)
    assert resposta.status_code == 409 and "venceu" in resposta.json()["motivo"]


@pytest.mark.parametrize("corpo", [{"solicitante": "joao"}, {"token": "x"}])
def test_executar_sem_token_ou_sem_solicitante_e_400_e_nao_gasta_o_token(c, ambiente, corpo):
    token = _conferir(c, {"pedido": 84245}).json()["plano"]["token"]
    resposta = c.post(f"{API}/encerrar/executar", json=corpo, headers=CABECALHO)
    assert resposta.status_code == 400 and resposta.json()["tipo"] == "invalido"
    assert PLANOS.obter(token) is not None


def test_modulo_ocupado_nao_gasta_o_token(c, ambiente, execucao_falsa):
    """29/09/2026: the token used to be spent before the busy check, so the operator had to
    check the plan again. Now it survives and works once the module is free."""
    token = _conferir(c, {"pedido": 84245}).json()["plano"]["token"]
    trava = _Trava()
    resultado = {"finalizadas": [], "com_erro": [], "ignoradas": [], "puladas": []}

    def _levanta(_leitor, op_docnums=None, doc_num_pedido=None):
        # The Liberar that occupies the module asks for 9001 only; the closing, for its plan.
        if op_docnums == ["9001"]:
            return [_op(11, 9001, "P")]
        return [dict(o) for o in _OPS_DO_PEDIDO]

    with patch(f"{SVC}.levanta_ops", side_effect=_levanta), \
         patch(f"{SVC}._componentes_por_op", return_value={}), \
         patch(f"{SVC}.muda_status", trava), \
         patch(f"{SVC}.finalizar_ops", AsyncMock(return_value=resultado)):
        ocupando = c.post(f"{API}/liberar", json={"ops": [9001], "solicitante": "joao"}, headers=CABECALHO)
        assert ocupando.status_code == 202, ocupando.text
        recusada = c.post(f"{API}/encerrar/executar", json={"token": token, "solicitante": "ana"},
                          headers=CABECALHO)
        valido_ainda = PLANOS.obter(token) is not None
        trava.solta()
        _espera(c, ocupando.json()["execucao"]["id"])
        aceita = c.post(f"{API}/encerrar/executar", json={"token": token, "solicitante": "ana"},
                        headers=CABECALHO)
        _espera(c, aceita.json()["execucao"]["id"])

    assert recusada.status_code == 409 and recusada.json()["tipo"] == "ocupado"
    assert valido_ainda
    assert aceita.status_code == 202


def test_escrita_recusada_nao_gasta_o_token(c):
    """The gate runs before the token: a production write off the .11 leaves the plan valid."""
    with patch("controleproducao.core.web.get_settings", return_value=_settings()), \
         patch("controleproducao.modules.manutencao_op.api_router.HanaDirectReader", MagicMock()):
        token = _conferir(c, {"pedido": 84245}).json()["plano"]["token"]
    with patch("controleproducao.core.web.get_settings", return_value=_settings(producao=True)):
        resposta = c.post(f"{API}/encerrar/executar", json={"token": token, "solicitante": "joao"},
                          headers=CABECALHO)
    assert resposta.status_code == 503
    assert PLANOS.obter(token) is not None


# ---------------------------------------------------------------------------
# 5. Following and stopping an execution
# ---------------------------------------------------------------------------
def _terminada_de(modulo: str) -> Tarefa:
    agora = datetime.now()
    tarefa = Tarefa(id="0123456789ab", nome="Processar novos", descricao="pedido 84433",
                    criada_em=agora, modulo=modulo, situacao="concluída", iniciada_em=agora,
                    terminada_em=agora)
    TAREFAS._guardar(tarefa)
    return tarefa


def test_estado_so_enxerga_execucoes_deste_modulo(c, ambiente):
    _terminada_de("pedidos_wbc")
    outra = c.get(f"{API}/execucoes/0123456789ab", headers=CABECALHO)
    parar = c.post(f"{API}/execucoes/0123456789ab/cancelar", json={"solicitante": "joao"}, headers=CABECALHO)
    inexistente = c.get(f"{API}/execucoes/fedcba987654", headers=CABECALHO)

    for resposta in (outra, parar, inexistente):
        assert resposta.status_code == 404 and resposta.json()["tipo"] == "nao_encontrada"


def test_estado_de_execucao_terminada_deste_modulo(c, ambiente):
    _terminada_de("manutencao_op")
    resposta = c.get(f"{API}/execucoes/0123456789ab", headers=CABECALHO)
    execucao = resposta.json()["execucao"]
    assert resposta.status_code == 200
    assert execucao["terminada"] and execucao["desfecho"] == "ok" and execucao["origem"] == "tela"


def test_estado_com_historico_fora_e_503(c, ambiente):
    class _Fora:
        def obter(self, _id):
            raise RuntimeError("supabase fora")

    TAREFAS.historico = _Fora()
    resposta = c.get(f"{API}/execucoes/0123456789ab", headers=CABECALHO)
    assert resposta.status_code == 503 and resposta.json()["tipo"] == "historico_indisponivel"


def test_cancelar_exige_solicitante_e_registra_quem_pediu(c, ambiente, execucao_falsa, caplog):
    trava = _Trava()
    with patch(f"{SVC}.levanta_ops", return_value=[_op(1, 9001, "P")]), \
         patch(f"{SVC}.muda_status", trava), \
         caplog.at_level(logging.WARNING, logger="controleproducao.modules.manutencao_op.api_router"):
        tarefa_id = c.post(f"{API}/liberar", json={"ops": [9001], "solicitante": "joao"},
                           headers=CABECALHO).json()["execucao"]["id"]
        sem_quem = c.post(f"{API}/execucoes/{tarefa_id}/cancelar", headers=CABECALHO)
        parou = c.post(f"{API}/execucoes/{tarefa_id}/cancelar", json={"solicitante": "maria"},
                       headers=CABECALHO)
        estado = _espera(c, tarefa_id)
        trava.solta()

    assert sem_quem.status_code == 400
    assert parou.status_code == 200 and parou.json() == {"ok": True, "cancelada": True}
    assert estado["situacao"] == "cancelada"
    assert f"Execução {tarefa_id}: interrupção pedida pela API · solicitante maria" in caplog.text


def test_tela_e_api_dividem_a_trava_do_modulo(c, ambiente, execucao_falsa):
    """An execution started by the API blocks the screen, which links to it."""
    trava = _Trava()
    with patch(f"{SVC}.levanta_ops", return_value=[_op(1, 9001, "P")]), \
         patch(f"{SVC}.muda_status", trava):
        pela_api = c.post(f"{API}/liberar", json={"ops": [9001], "solicitante": "joao"}, headers=CABECALHO)
        pela_tela = c.post("/manutencao-op/status", data={"op_docnums": ["9001"], "acao": "l"})
        trava.solta()
        _espera(c, pela_api.json()["execucao"]["id"])

    assert pela_tela.status_code == 400
    assert "já tem uma execução em andamento" in _texto(pela_tela.text)
    assert f'href="/tarefas/{pela_api.json()["execucao"]["id"]}"' in pela_tela.text
