"""Testes da camada web dos módulos 2 e 3 (22/09/2026).

O que estes testes protegem, em ordem de importância:

1. **Nenhuma rota que grava escapa do aviso de ambiente.** O teste de cobertura abaixo
   varre as rotas POST e falha se alguma gravar sem passar por `avisa_escrita`. A trava de
   produção saiu em 22/09/2026 (a pedido do Anderson) e a chamada virou só aviso + log —
   mas o teste continua valendo, porque o log é o que sobra para auditoria e porque uma
   rota de escrita nova passando despercebida é exatamente o que ninguém revisa.
2. **Confirmação de operação irreversível não pode ser contornável.** Sem token, com token
   já usado, com token vencido, com o texto de produção errado: tudo recusado, e nada
   chega ao serviço.
3. **A tela não age sobre o que não foi conferido.** O que o formulário manda é relido; o
   que saiu da lista elegível é recusado em vez de processado.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from controleproducao.core.confirmacao import PLANOS
from controleproducao.core.tarefas import TAREFAS
from controleproducao.main import app
from controleproducao.modules.pedidos_wbc import router as r_wbc_mod
from controleproducao.modules.pedidos_wbc.schemas import PedidoParaIntegrar

SVC_OP = "controleproducao.modules.manutencao_op.service"

# ---------------------------------------------------------------------------
# Andaimes
# ---------------------------------------------------------------------------
def _settings(producao: bool = False):
    return SimpleNamespace(
        is_production=producao,
        sl_company_db="SBOALTAMIRAPROD" if producao else "SBOALTAMIRAHOMOLOG",
        hana_schema="SBOALTAMIRAHOMOLOG",
        hana_schema_legado="SBOALTAMIRAPROD",
        sl_business_place_id=1,
    )


PEDIDO = PedidoParaIntegrar(
    opp_id=4321, doc_num=84245, cod_cliente="C0001", nome_cliente="Cliente Teste",
    total_pedido=1234.5, data_lancamento="2026-09-01", orc_num_masc="00120634",
)


@pytest.fixture(autouse=True)
def _limpa_registros():
    """Planos e tarefas são globais do processo — sem limpar, um teste vê o token do outro.

    Os `asyncio.Task` pendentes também são descartados: o registro aceita **uma execução
    por módulo**, então uma tarefa deixada rodando por um teste anterior faz o próximo
    receber "já existe execução em andamento" em vez do redirecionamento — e isso aparece
    como teste intermitente, que é o pior tipo de teste.
    """
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
    """A logged-in browser: key configured, the painel's cookie, same-origin POSTs.

    Since 28/09/2026 the screen sits behind the painel WBC's cookie gate and refuses
    writes with no key (503) or cross-origin POSTs (403) — see `core/acesso.py`.
    """
    from controleproducao.config import get_settings
    from wbcpython.dashboard.acesso import COOKIE_DE_ACESSO, token_da_chave

    monkeypatch.setenv("OS_API_KEY", "chave-de-teste")
    get_settings.cache_clear()
    with TestClient(app, headers={"Origin": "http://testserver"}) as c:
        c.cookies.set(COOKIE_DE_ACESSO, token_da_chave("chave-de-teste"))
        yield c


def _patches(producao: bool = False, pedidos=None):
    """Tudo que a rota toca fora do processo, desligado de uma vez."""
    pedidos = [PEDIDO] if pedidos is None else pedidos
    return [
        patch("controleproducao.core.web.get_settings", return_value=_settings(producao)),
        patch("controleproducao.modules.pedidos_wbc.router.get_settings", return_value=_settings(producao)),
        patch("controleproducao.modules.pedidos_wbc.router.HanaDirectReader", MagicMock()),
        patch("controleproducao.modules.pedidos_wbc.service.buscar_pedidos_para_integrar",
              AsyncMock(return_value=pedidos)),
    ]


class _Ligado:
    def __init__(self, patches):
        self._patches = patches

    def __enter__(self):
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.stop()


def _token(html: str) -> str:
    achado = re.search(r'name="token" value="([^"]+)"', html)
    assert achado, "a tela de confirmação não trouxe token"
    return achado.group(1)


def _tela_ops(*extras, execucao: bool = False):
    """The Manutenção de OP screen with what it touches outside the process doubled —
    settings, the write gate's settings and the HANA reader — plus the test's own patches.
    ``execucao=True`` also doubles what a background run touches (settings and Service
    Layer of `acoes`). The same idea as `_patches` for Pedidos WBC (30/09/2026)."""
    base = [
        patch("controleproducao.modules.manutencao_op.router.get_settings", return_value=_settings()),
        patch("controleproducao.core.web.get_settings", return_value=_settings()),
        patch("controleproducao.modules.manutencao_op.router.HanaDirectReader", MagicMock()),
    ]
    if execucao:
        base += [
            patch("controleproducao.modules.manutencao_op.acoes.get_settings", return_value=_settings()),
            patch("controleproducao.modules.manutencao_op.acoes.ServiceLayerClient", MagicMock()),
        ]
    return _Ligado([*base, *extras])


def _espera_terminar(cliente, tarefa_id: str, teto: int = 50) -> dict:
    """Polls `/tarefas/{id}/estado` until `terminada` (or `teto` rounds) and returns the state.

    The task runs on the TestClient loop thread; the caller must keep its inner `patch(...)`
    open until this returns, otherwise `patch.__exit__` on the main thread races the task and
    it may reach the real service (the source of the two flaky tests fixed on 2026-09-28).
    """
    estado: dict = {}
    for _ in range(teto):
        estado = cliente.get(f"/tarefas/{tarefa_id}/estado").json()
        if estado.get("terminada"):
            break
        time.sleep(0.02)
    return estado


# ---------------------------------------------------------------------------
# 1. Cobertura da trava — o teste que pega a rota esquecida
# ---------------------------------------------------------------------------
ROTAS_QUE_NAO_GRAVAM = {
    # Etapas de conferência: leem e montam o plano, não gravam. A trava é aplicada na
    # execução, que é onde a gravação acontece.
    "/pedidos-wbc/processar/conferir",
    "/pedidos-wbc/reprocessar/conferir",
    "/pedidos-wbc/cancelar-ops/conferir",
    "/manutencao-op/buscar",
    "/manutencao-op/encerrar/conferir",
    # Interrompe uma execução entre passos; não cria documento nenhum (e não desfaz os
    # que já foram criados — a tela avisa isso).
    "/tarefas/{tarefa_id}/cancelar",
    # JSON API (29/09/2026): the same two cases — the closing plan only reads, and cancel
    # only interrupts.
    "/api/manutencao-op/encerrar/conferir",
    "/api/manutencao-op/execucoes/{tarefa_id}/cancelar",
}


def test_toda_rota_post_que_grava_passa_pela_trava():
    """Varre o código das rotas POST e exige `avisa_escrita` nas que gravam.

    Depois que a trava saiu (22/09/2026), a chamada não impede nada — registra o alvo em
    log e devolve o aviso. Continua obrigatória por dois motivos: o log é o rastro que
    sobra de uma gravação em produção, e acrescentar uma rota de escrita sem ela é o tipo
    de esquecimento que só aparece quando alguém procura o registro e não acha.
    """
    import inspect

    from controleproducao.core import tarefas_router as r_tar
    from controleproducao.modules.manutencao_op import api_router as r_api
    from controleproducao.modules.manutencao_op import router as r_mop
    from controleproducao.modules.pedidos_wbc import router as r_wbc

    faltando = []
    for modulo in (r_wbc, r_mop, r_tar, r_api):
        for rota in modulo.router.routes:
            if "POST" not in rota.methods:
                continue
            caminho = rota.path
            if caminho in ROTAS_QUE_NAO_GRAVAM:
                continue
            fonte = inspect.getsource(rota.endpoint)
            # A rota pode chamar a trava direto ou através do seu helper (`_consome`),
            # que é onde ela vive nos módulos com plano de confirmação.
            if "avisa_escrita" not in fonte and "_consome" not in fonte:
                faltando.append(caminho)

    assert not faltando, (
        "Estas rotas POST gravam sem passar pelo aviso de ambiente: "
        + ", ".join(faltando)
        + ". Aplique `avisa_escrita` ou declare-as em ROTAS_QUE_NAO_GRAVAM "
          "dizendo por que não gravam."
    )


# ---------------------------------------------------------------------------
# 2. Confirmação de operação irreversível
# ---------------------------------------------------------------------------
def test_executar_sem_token_nao_chama_o_servico(cliente):
    with _Ligado(_patches()):
        with patch("controleproducao.modules.pedidos_wbc.service.processar_pedidos_novos",
                   AsyncMock()) as servico:
            resposta = cliente.post("/pedidos-wbc/processar/executar", data={"token": ""})
    assert resposta.status_code == 400
    servico.assert_not_called()


def test_token_so_vale_uma_vez(cliente):
    with _Ligado(_patches()):
        conferir = cliente.post("/pedidos-wbc/processar/conferir", data={"opp_ids": ["4321"]})
        token = _token(conferir.text)

        with patch("controleproducao.modules.pedidos_wbc.router._dispara", return_value=MagicMock()):
            primeira = cliente.post("/pedidos-wbc/processar/executar", data={"token": token})
        assert primeira.status_code < 400

        with patch("controleproducao.modules.pedidos_wbc.service.processar_pedidos_novos",
                   AsyncMock()) as servico:
            segunda = cliente.post("/pedidos-wbc/processar/executar", data={"token": token})
    assert segunda.status_code == 400
    servico.assert_not_called()


def test_token_vencido_e_recusado(cliente):
    with _Ligado(_patches()):
        conferir = cliente.post("/pedidos-wbc/processar/conferir", data={"opp_ids": ["4321"]})
        token = _token(conferir.text)
        # Envelhece o plano além da validade: ele foi calculado sobre um estado do SAP que
        # já pode não valer mais, e executar sobre isso é como cancelar a OP errada.
        PLANOS._planos[token].criado_em = datetime.now() - timedelta(minutes=30)

        with patch("controleproducao.modules.pedidos_wbc.service.processar_pedidos_novos",
                   AsyncMock()) as servico:
            resposta = cliente.post("/pedidos-wbc/processar/executar", data={"token": token})
    assert resposta.status_code == 400
    assert "venceu" in resposta.text
    servico.assert_not_called()


def test_em_producao_a_conferencia_nao_pede_digitacao(cliente, como_a_11):
    """A tela de conferência avisa o ambiente e executa com o token — sem segundo fator.

    A digitação da company DB saiu em 22/09/2026 junto com a trava. Since 28/09 the gate is
    the machine (`como_a_11`): off the .11 this same POST answers 503 (see
    `test_em_producao_fora_da_11_a_execucao_e_recusada`).
    """
    with _Ligado(_patches(producao=True)):
        conferir = cliente.post("/pedidos-wbc/processar/conferir", data={"opp_ids": ["4321"]})
        # 30/09/2026: no production notice on the card any more (owner); the notice starts
        # with the bold "Cria Ordens de Produção", without "Operação irreversível.".
        assert "Esta gravação é em" not in conferir.text
        assert "<strong>Cria Ordens de Produção</strong>, itens e recursos no SAP" in conferir.text
        assert "Operação irreversível." not in conferir.text
        assert 'name="confirmacao"' not in conferir.text   # ...mas não há o que digitar
        token = _token(conferir.text)

        with patch.object(r_wbc_mod, "_dispara", return_value=MagicMock()) as disparo:
            resposta = cliente.post("/pedidos-wbc/processar/executar", data={"token": token})

    assert resposta.status_code < 400
    disparo.assert_called_once()


def test_em_producao_fora_da_11_a_execucao_e_recusada(cliente):
    """The IP gate (28/09/2026): a production write from any machine but the .11 is refused
    with 503 before the task exists — the conftest pins PRODUCTION_MACHINE_IP to a
    never-local address, so this test IS "any other machine"."""
    with _Ligado(_patches(producao=True)):
        conferir = cliente.post("/pedidos-wbc/processar/conferir", data={"opp_ids": ["4321"]})
        token = _token(conferir.text)
        with patch.object(r_wbc_mod, "_dispara", return_value=MagicMock()) as disparo:
            resposta = cliente.post("/pedidos-wbc/processar/executar", data={"token": token})

    assert resposta.status_code == 503
    assert "máquina de produção" in resposta.json()["detail"]
    disparo.assert_not_called()


# ---------------------------------------------------------------------------
# 3. A tela não age sobre o que não foi conferido
# ---------------------------------------------------------------------------
def test_pedido_fora_da_lista_elegivel_e_recusado(cliente):
    """O número vem do navegador; a lista elegível vem do banco. Vale a do banco.

    É o mesmo tema recorrente da migração: no legado, a proteção morava no filtro da
    grade. Toda vez que uma camada aceita um identificador direto, a checagem que a tela
    fazia por filtragem desaparece.
    """
    with _Ligado(_patches()):
        with patch("controleproducao.modules.pedidos_wbc.service.processar_pedidos_novos",
                   AsyncMock()) as servico:
            resposta = cliente.post(
                "/pedidos-wbc/processar/conferir", data={"opp_ids": ["9999"]}
            )
    assert resposta.status_code == 400
    assert "9999" in resposta.text
    servico.assert_not_called()


def test_conferir_sem_selecao_e_recusado(cliente):
    with _Ligado(_patches()):
        resposta = cliente.post("/pedidos-wbc/processar/conferir", data={})
    assert resposta.status_code == 400


def test_conferir_nao_grava_nada(cliente):
    with _Ligado(_patches()):
        with patch("controleproducao.modules.pedidos_wbc.service.processar_pedidos_novos",
                   AsyncMock()) as servico:
            resposta = cliente.post("/pedidos-wbc/processar/conferir", data={"opp_ids": ["4321"]})
    assert resposta.status_code == 200
    servico.assert_not_called()


# ---------------------------------------------------------------------------
# 4. Cancelamento de OPs — bloqueante impede o plano inteiro
# ---------------------------------------------------------------------------
def test_op_bloqueante_impede_o_plano_de_cancelamento(cliente):
    levantamento = {
        "pedido": {"DocNum": 84245, "DocEntry": 19253},
        "ops": [{"doc_num": 1, "status": "R"}, {"doc_num": 2, "status": "P"}],
        "bloqueantes": [{"doc_num": 1, "item_code": "X", "status": "R", "descricao": "Liberada"}],
        "a_cancelar": [{"doc_num": 2, "item_code": "Y", "status": "P", "descricao": "Planejada"}],
        "ja_canceladas": [],
    }
    with _Ligado(_patches()):
        with patch("controleproducao.modules.pedidos_wbc.service.levanta_ops_para_cancelamento",
                   AsyncMock(return_value=levantamento)):
            resposta = cliente.post(
                "/pedidos-wbc/cancelar-ops/conferir", data={"doc_num": "84245"}
            )
    assert resposta.status_code == 400
    # Nada de token: havendo bloqueante, nem as planejadas são canceladas — cancelar
    # metade deixa o pedido num estado que ninguém sabe descrever depois.
    assert 'name="token"' not in resposta.text


# ---------------------------------------------------------------------------
# 5. Manutenção de OP — ciclo de dependência recusa a operação inteira
# ---------------------------------------------------------------------------
def test_ciclo_de_dependencia_recusa_o_encerramento(cliente):
    ops = [
        {"doc_entry": 1, "doc_num": 101, "status": "P", "item_code": "A",
         "planejada": 1.0, "apontada": 0.0, "pedido": 84245},
        {"doc_entry": 2, "doc_num": 102, "status": "P", "item_code": "B",
         "planejada": 1.0, "apontada": 0.0, "pedido": 84245},
    ]
    finalizar = AsyncMock()
    with _tela_ops(
        patch(f"{SVC_OP}.levanta_ops", return_value=ops),
        patch(f"{SVC_OP}._componentes_por_op", return_value={}),
        patch(f"{SVC_OP}.ordena_por_dependencia", return_value=([], ops)),
        patch(f"{SVC_OP}.finalizar_ops", finalizar),
    ):
        resposta = cliente.post("/manutencao-op/encerrar/conferir", data={"pedido": "84245"})

    assert resposta.status_code == 400
    assert "ciclo" in resposta.text.lower()
    finalizar.assert_not_called()


def test_encerrar_exige_ops_ou_pedido_mas_nao_os_dois(cliente):
    with patch("controleproducao.core.web.get_settings", return_value=_settings()):
        ambos = cliente.post(
            "/manutencao-op/encerrar/conferir",
            data={"pedido": "84245", "op_docnums": ["101"]},
        )
        nenhum = cliente.post("/manutencao-op/encerrar/conferir", data={})
    assert ambos.status_code == 400
    assert nenhum.status_code == 400


# ---------------------------------------------------------------------------
# 6. Paginação (22/09/2026)
# ---------------------------------------------------------------------------
def _texto(html: str) -> str:
    """HTML sem marcação e com espaços normalizados.

    Afirmar sobre o HTML cru amarrava o teste ao `<b>` da paginação — e foi isso que
    quebrou três asserções quando a tela recebeu o estilo novo. O que o teste precisa
    garantir é a frase que o usuário lê, não como ela está marcada.
    """
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def _pedidos(quantos: int):
    return [
        PedidoParaIntegrar(
            opp_id=1000 + n, doc_num=84000 + n, cod_cliente=f"C{n:04d}",
            nome_cliente=f"Cliente {n}", total_pedido=100.0 + n,
            data_lancamento="2026-09-01 00:00:00", orc_num_masc=f"0012{n:04d}",
        )
        for n in range(quantos)
    ]


def test_pagina_mostra_no_maximo_quinze(cliente):
    from controleproducao.modules.pedidos_wbc.router import POR_PAGINA

    with _Ligado(_patches(pedidos=_pedidos(40))):
        resposta = cliente.get("/pedidos-wbc?buscar=1")
    assert resposta.text.count('name="opp_ids"') == POR_PAGINA
    assert "de 40 pedido(s)" in _texto(resposta.text)


def test_segunda_pagina_traz_os_seguintes(cliente):
    with _Ligado(_patches(pedidos=_pedidos(40))):
        primeira = cliente.get("/pedidos-wbc?buscar=1&pagina=1")
        segunda = cliente.get("/pedidos-wbc?buscar=1&pagina=2")
    # A ordenação é responsabilidade do serviço (aqui substituído por um dublê); o que
    # esta rota promete é fatiar sem repetir nem pular.
    ids_1 = set(re.findall(r'name="opp_ids" value="(\d+)"', primeira.text))
    ids_2 = set(re.findall(r'name="opp_ids" value="(\d+)"', segunda.text))
    assert len(ids_1) == len(ids_2) == 15
    assert not (ids_1 & ids_2)
    assert "página 2 de 3" in _texto(segunda.text)


def test_pagina_fora_do_intervalo_cai_na_ultima(cliente):
    """O número vem da URL e pode estar velho — a lista encolhe conforme se processa.

    Melhor a última página válida do que uma tabela vazia que parece "sumiu tudo".
    """
    with _Ligado(_patches(pedidos=_pedidos(40))):
        resposta = cliente.get("/pedidos-wbc?buscar=1&pagina=99")
    assert "página 3 de 3" in _texto(resposta.text)


def test_buscar_0_nao_consulta_o_banco(cliente):
    with _Ligado(_patches()):
        with patch("controleproducao.modules.pedidos_wbc.service.buscar_pedidos_para_integrar",
                   AsyncMock()) as busca:
            cliente.get("/pedidos-wbc?buscar=0")
            cliente.get("/pedidos-wbc?modo=integrados")          # integrados: only on click
    busca.assert_not_called()


def test_abrir_a_pagina_ja_busca_os_pedidos_novos(cliente):
    """30/09/2026 (owner): the page opens with "Pedidos novos" loaded — no click."""
    with _Ligado(_patches(pedidos=_pedidos(2))):
        with patch("controleproducao.modules.pedidos_wbc.service.buscar_pedidos_para_integrar",
                   AsyncMock(return_value=_pedidos(2))) as busca:
            html = cliente.get("/pedidos-wbc").text
    assert busca.await_args.kwargs == {"integrados": False}
    assert html.count('name="opp_ids"') == 2
    assert 'class="ov-kpi"' in _linha_topo(html)


def test_busca_automatica_que_falha_abre_a_pagina_com_aviso(cliente):
    with _Ligado(_patches()):
        with patch("controleproducao.modules.pedidos_wbc.service.buscar_pedidos_para_integrar",
                   AsyncMock(side_effect=RuntimeError("HANA fora"))):
            resposta = cliente.get("/pedidos-wbc")
    assert resposta.status_code == 200
    assert "Não foi possível carregar os pedidos novos agora" in _texto(resposta.text)
    assert "Busque os pedidos para selecionar." in _texto(resposta.text)


# ---------------------------------------------------------------------------
# 7. As duas numerações (22/09/2026)
# ---------------------------------------------------------------------------
def test_execucao_recebe_o_numero_do_orcamento_e_nao_a_chave_da_oportunidade(cliente):
    """A lista traz as duas lado a lado; o serviço só aceita uma.

    `Oportunidade` (15024) é `OPR1.OpprId`, a chave no SAP; `WBC` (00125431) é
    `OOPR.U_ORCNUM_MASC`, o nº do orçamento — e é ele que `GetOrcsWBC` e
    `GetIdOrcamentosPedido` procuram. Passar a primeira dá "sem pedido vinculado" em todas
    as linhas, que foi exatamente o que aconteceu na primeira execução da tela.
    """
    recebido = {}

    async def _falso(sl, wbc, hana, ids, force=False):
        recebido["ids"] = list(ids)
        return {"processados": [], "com_erro": []}

    with _Ligado(_patches()):
        conferir = cliente.post("/pedidos-wbc/processar/conferir", data={"opp_ids": ["4321"]})
        token = _token(conferir.text)
        with patch("controleproducao.modules.pedidos_wbc.service.processar_pedidos_novos", _falso), \
             patch("controleproducao.modules.pedidos_wbc.router.ServiceLayerClient", MagicMock()), \
             patch("controleproducao.modules.pedidos_wbc.router.WbcSqlServerClient", MagicMock()):
            cliente.post("/pedidos-wbc/processar/executar", data={"token": token})

    # `orc_num_masc` do PEDIDO de teste, não o `opp_id`.
    assert recebido.get("ids") == [PEDIDO.orc_num_masc]
    assert recebido["ids"] != [str(PEDIDO.opp_id)]


def test_acompanhamento_identifica_pelo_pedido_do_sap(cliente):
    """Ao serviço vai o orçamento WBC; à tela vai o nº do pedido, que é o que se abre no B1."""
    async def _falso(sl, wbc, hana, ids, force=False):
        return {"processados": list(ids), "com_erro": []}

    with _Ligado(_patches()):
        conferir = cliente.post("/pedidos-wbc/processar/conferir", data={"opp_ids": ["4321"]})
        token = _token(conferir.text)
        with patch("controleproducao.modules.pedidos_wbc.service.processar_pedidos_novos", _falso), \
             patch("controleproducao.modules.pedidos_wbc.router.ServiceLayerClient", MagicMock()), \
             patch("controleproducao.modules.pedidos_wbc.router.WbcSqlServerClient", MagicMock()):
            resposta = cliente.post(
                "/pedidos-wbc/processar/executar", data={"token": token}, follow_redirects=False
            )
            assert resposta.status_code == 303, resposta.text[:400]
            tarefa_id = resposta.headers["location"].rsplit("/", 1)[-1]
            estado = _espera_terminar(cliente, tarefa_id)

    assert str(PEDIDO.doc_num) in estado["descricao"]
    assert f"Pedido {PEDIDO.doc_num} (WBC {PEDIDO.orc_num_masc})" in "\n".join(estado["linhas"])
    # O resultado carrega o nº do pedido junto, para não obrigar a traduzir de volta.
    assert estado["resultado"]["processados"][0]["doc_num"] == str(PEDIDO.doc_num)


def test_tarefa_concluida_com_erros_nao_se_anuncia_como_sucesso():
    """Verde sobre um resultado que só teve erro é pior que indicador nenhum."""
    from datetime import datetime as _dt

    from controleproducao.core.tarefas import Tarefa

    t = Tarefa(id="x", nome="n", descricao="d", criada_em=_dt.now())
    t.situacao = "concluída"
    t.resultado = {"processados": [], "com_erro": [{"orc_num": "1", "motivo": "x"}]}
    assert t.com_falhas is True
    assert t.para_json()["com_falhas"] is True

    t.resultado = {"processados": ["1"], "com_erro": []}
    assert t.com_falhas is False


# ---------------------------------------------------------------------------
# 8. PATCH parcial de linhas (22/09/2026)
# ---------------------------------------------------------------------------
def test_zerar_u_ino_op_manda_so_linenum_e_o_campo():
    """Reler o pedido e devolver `DocumentLines` inteiro reenvia UDFs de outros add-ons
    que voltam vazias no GET e que o SAP recusa na escrita
    (`'' is not a valid value for property 'U_B1SYS_RevenueInd2'`).

    Já tinha quebrado `marca_op_nas_linhas` em 15/09 e voltou em `_update_tab_pedido_cong`
    em 22/09, no caminho do `reprocessar-integrados`. Este teste trava o formato do corpo
    para não haver uma terceira vez nesta função.
    """
    import asyncio

    from controleproducao.modules.pedidos_wbc import service as svc

    sl = MagicMock()
    sl.get_by_key = AsyncMock(return_value={
        "DocumentLines": [
            {"LineNum": 0, "ItemCode": "X", "U_INO_OP": 55, "U_B1SYS_RevenueInd2": ""},
            {"LineNum": 1, "ItemCode": "Y", "U_INO_OP": 56, "U_B1SYS_RevenueInd2": ""},
        ]
    })
    sl.update_entity = AsyncMock(return_value={})

    asyncio.run(svc._update_tab_pedido_cong(sl, [], "00125534", "19954", 0, "N"))

    corpo = sl.update_entity.await_args.args[2]
    assert corpo["DocumentLines"] == [
        {"LineNum": 0, "U_INO_OP": 0},
        {"LineNum": 1, "U_INO_OP": 0},
    ]
    enviado = str(corpo)
    assert "U_B1SYS_RevenueInd2" not in enviado
    assert "ItemCode" not in enviado


# ---------------------------------------------------------------------------
# 9. Log detalhado do reprocessamento (22/09/2026)
# ---------------------------------------------------------------------------
def test_reprocessar_narra_cada_passo_no_acompanhamento():
    """Reprocessar era a única etapa muda do módulo.

    São quatro gravações no SAP em sequência — espelho do orçamento, atualização do
    pedido, vínculo com a Oportunidade, cancelamento das OPs antigas — e nenhuma delas
    dizia nada. Na web isso vira minutos de tela parada; em caso de falha, ninguém sabe
    em qual das quatro parou.
    """
    import asyncio
    from datetime import date
    from datetime import datetime as _dt

    from controleproducao.core.tarefas import Tarefa, acompanha_log
    from controleproducao.modules.pedidos_wbc import service as svc
    from controleproducao.modules.pedidos_wbc.schemas import OportunidadeDoc

    tarefa = Tarefa(id="t", nome="n", descricao="d", criada_em=_dt.now())

    sl = MagicMock()
    sl.get_by_key = AsyncMock(side_effect=lambda ent, key, **k: (
        {"DocTotal": 61659.77} if ent == "Orders"
        else {"SalesOpportunitiesLines": [{"DocumentType": "bodt_Order"}]}
    ))
    sl.update_entity = AsyncMock()

    hana = MagicMock()
    hana.fetch_all = lambda sql, *a, **k: (
        [{"DocEntry": 19914, "CardCode": "C011953", "DocNum": 84371}] if "U_INO_COTWBC" in sql
        else [{"OpprId": 15024}] if "OpprId" in sql
        else [{"U_INO_Congelado": "Y"}] if "U_INO_Congelado" in sql
        else [
            {"DocEntry": 156001, "DocNum": 156001, "ItemCode": "PAR000",
             "ItemName": "CONJ PARAFUSO PADRAO 5/16 x 5/8 Gr 8", "PlannedQty": 640.0},
            {"DocEntry": 156002, "DocNum": 156002, "ItemCode": "PPLCOL0008",
             "ItemName": "COLUNA NORMAL 80 mm ch. 1,80 4800MM", "PlannedQty": 26.0},
        ]
    )
    orcamento = OportunidadeDoc(
        orc_num="00125431", versao="1", sit_code=3, cli_nom="X", recp_cod="", cli_mun="",
        est_cod="", orc_date=date(2026, 9, 11), orc_alt_dth=date(2026, 9, 11), grp_code=1,
        sub_grup_code=1, orc_item=1, orc_prod_code="P", orc_prod_quantidade=1, orc_txt="",
        orc_val=1, orc_ipi=0, orc_icm=0, id_integracao=1,
    )

    with patch.object(svc, "_busca_orcamentos_wbc", AsyncMock(return_value=[orcamento])), \
         patch.object(svc, "preenche_tabela", AsyncMock(return_value=7788)), \
         patch.object(svc, "_cancela_op", AsyncMock()), \
         patch.object(svc, "preenche_log", AsyncMock()), \
         patch.object(svc, "_update_tab_pedido_cong", AsyncMock(return_value=0)):
        with acompanha_log(tarefa, svc.__name__):
            asyncio.run(svc.reprocessar_pedidos_integrados(sl, MagicMock(), hana, ["00125431"]))

    log = "\n".join(tarefa.linhas)
    # As quatro gravações, cada uma anunciada antes de acontecer.
    assert "Montando a tabela do orçamento" in log
    assert "Atualizando o pedido 84371" in log
    assert "Vinculando o pedido 84371 à Oportunidade 15024" in log
    assert "2 OP(s) planejada(s)" in log
    # E o progresso dentro do passo mais longo: uma linha por OP, dizendo QUAL OP —
    # número, item e descrição. "DocEntry 156001" sozinho não identifica nada para quem
    # está olhando a tela.
    assert "OP 156001 cancelada (1/2): PAR000 — CONJ PARAFUSO PADRAO 5/16 x 5/8 Gr 8 (640 un)" in log
    assert "OP 156002 cancelada (2/2)" in log
    assert "COLUNA NORMAL 80 mm" in log
    # Qual dos três caminhos de atualização foi tomado: eles gravam coisas bem diferentes.
    assert "Pedido congelado" in log


# ---------------------------------------------------------------------------
# 10. OP em status terminal não é selecionável (22/09/2026)
# ---------------------------------------------------------------------------
def _ops_com_terminais():
    base = {
        "Cód. Produto": "PAR000", "Produto": "CONJ PARAFUSO", "Qtde. Planejada": 20,
        "Qtde. Apontada": 0, "Qtde. Restante": 20, "Data Pedido": "2026-09-22 00:00:00",
        "Data inicio": "2026-09-22 00:00:00", "Data Vencimento": "2026-10-12 00:00:00",
        "Cod. Cliente": "C1", "Cliente": "GARRETT",
    }
    return [
        {"Número OP": 155747, "Status": "P", **base, "Status (descrição)": "Planejada"},
        {"Número OP": 155746, "Status": "L", **base, "Status (descrição)": "Encerrada"},
        {"Número OP": 155745, "Status": "C", **base, "Status (descrição)": "Cancelada"},
        {"Número OP": 155744, "Status": "R", **base, "Status (descrição)": "Liberada"},
    ]


def test_tela_nao_oferece_caixa_para_op_terminal(cliente):
    """Oferecer uma ação que só levaria a uma recusa é pior que não oferecer."""
    import re as _re

    with _tela_ops(patch(f"{SVC_OP}.buscar_ops", return_value=_ops_com_terminais())):
        resposta = cliente.post("/manutencao-op/buscar", data={"doc_num": "84376"})

    selecionaveis = _re.findall(r'name="op_docnums" value="(\d+)"', resposta.text)
    assert selecionaveis == ["155747", "155744"]      # só Planejada e Liberada
    assert resposta.text.count("<input type=\"checkbox\" disabled") == 2
    # D4 (29/09/2026): Replanejar is back beside Liberar.
    assert 'name="acao" value="l"' in resposta.text
    assert 'name="acao" value="p"' in resposta.text


def test_post_com_op_terminal_barra_o_lote_inteiro(cliente):
    """A tela desabilita a caixa; receber o número mesmo assim quer dizer que a lista
    mudou (alguém encerrou a OP nesse meio-tempo) ou que o POST não veio da tela.

    Nos dois casos o certo é o usuário reconferir — processar as demais em silêncio
    esconderia justamente a mudança que ele precisa ver.
    """
    ops = [
        {"doc_entry": 1, "doc_num": 155747, "status": "P", "item_code": "A",
         "planejada": 1.0, "apontada": 0.0, "pedido": 84376},
        {"doc_entry": 2, "doc_num": 155746, "status": "L", "item_code": "B",
         "planejada": 1.0, "apontada": 1.0, "pedido": 84376},
    ]
    mudar = AsyncMock()
    with _tela_ops(patch(f"{SVC_OP}.levanta_ops", return_value=ops), patch(f"{SVC_OP}.muda_status", mudar)):
        resposta = cliente.post(
            "/manutencao-op/status",
            data={"op_docnums": ["155747", "155746"], "acao": "l"},
        )

    assert resposta.status_code == 400
    assert "terminal" in resposta.text.lower()
    assert "155746" in resposta.text
    mudar.assert_not_called()


def test_reprocessar_volta_a_tela_com_aviso_e_executa(cliente):
    """30/09/2026: Reprocessar is back on the screen (D8 reversed by the owner), behind the
    same checked plan + single-use token as Processar — and the confirmation says what it
    really does: cancels the planned OPs of any origin and recreates NONE (the pre-D8 text
    promised "before recreating them")."""
    chamado: dict = {}

    async def _falso(sl, wbc, hana, ids):
        chamado["ids"] = list(ids)
        return {"atualizados": list(ids), "com_erro": []}

    with _Ligado(_patches()):
        conferir = cliente.post("/pedidos-wbc/reprocessar/conferir", data={"opp_ids": ["4321"]})
        assert conferir.status_code == 200
        texto = _texto(conferir.text)
        assert "Reprocessar pedidos integrados" in texto
        assert "NÃO recria as OPs" in texto and "inclusive as do addon" in texto
        assert "antes de recriá-las" not in texto
        token = _token(conferir.text)
        with patch("controleproducao.modules.pedidos_wbc.service.reprocessar_pedidos_integrados", _falso), \
             patch("controleproducao.modules.pedidos_wbc.router.ServiceLayerClient", MagicMock()), \
             patch("controleproducao.modules.pedidos_wbc.router.WbcSqlServerClient", MagicMock()):
            resposta = cliente.post(
                "/pedidos-wbc/reprocessar/executar", data={"token": token}, follow_redirects=False
            )
            assert resposta.status_code == 303, resposta.text[:400]
            estado = _espera_terminar(cliente, resposta.headers["location"].rsplit("/", 1)[-1])

    assert chamado["ids"] == [PEDIDO.orc_num_masc]          # the service gets the WBC quote
    assert "processe de novo" in "\n".join(estado["linhas"])


def test_integrados_tem_caixas_e_o_botao_reprocessar(cliente):
    with _Ligado(_patches(pedidos=_pedidos(3))):
        html = cliente.get("/pedidos-wbc?modo=integrados&buscar=1").text

    assert html.count('name="opp_ids"') == 3
    assert 'action="/pedidos-wbc/reprocessar/conferir"' in html
    assert re.search(r'id="btn-processar" disabled\s+data-verbo="Reprocessar"', html)
    assert 'id="forcar"' not in html                         # "forçar" is Processar-only
    assert "não recria" in _texto(html)


def _ops_para_replanejar():
    """Three Liberadas: 155744 nothing moved, 155743 material issued, 155742 product received."""
    base = {
        "Cód. Produto": "PAR000", "Produto": "CONJ PARAFUSO", "Qtde. Planejada": 20,
        "Qtde. Restante": 20, "Data Pedido": "2026-09-22 00:00:00",
        "Data inicio": "2026-09-22 00:00:00", "Data Vencimento": "2026-10-12 00:00:00",
        "Cod. Cliente": "C1", "Cliente": "GARRETT",
    }
    return [
        {"Número OP": 155747, "Status": "P", "Qtde. Apontada": 0, **base, "Status (descrição)": "Planejada"},
        {"Número OP": 155744, "Status": "R", "Qtde. Apontada": 0, **base, "Status (descrição)": "Liberada"},
        {"Número OP": 155743, "Status": "R", "Qtde. Apontada": 0, **base, "Status (descrição)": "Liberada"},
        {"Número OP": 155742, "Status": "R", "Qtde. Apontada": 5, **base, "Status (descrição)": "Liberada"},
    ]


def _busca_para_replanejar(cliente, baixadas):
    """`baixadas`: OP → issued quantity, put in the grid rows ("Baixada", since 30/09/2026);
    an OP left out gets no "Baixada" at all — the row without it reads as unknown."""
    linhas = []
    for op in _ops_para_replanejar():
        if op["Número OP"] in baixadas:
            op = {**op, "Baixada": baixadas[op["Número OP"]]}
        linhas.append(op)
    with _tela_ops(patch(f"{SVC_OP}.buscar_ops", return_value=linhas)):
        return cliente.post("/manutencao-op/buscar", data={"doc_num": "84376"}).text


def test_a_grade_diz_quais_ops_podem_voltar_e_por_que_nao(cliente):
    """D4 (29/09/2026): the reason sits next to the status, and each box carries whether the
    OP can go back — so the button knows before the POST (same rule as the action)."""
    html = _busca_para_replanejar(cliente, {155744: 0.0, 155743: 3.5, 155742: 0.0, 155747: 0.0})
    marcas = dict(re.findall(r'name="op_docnums" value="(\d+)"\s+data-replanejar="(\w+)"', html))
    assert marcas == {"155747": "na", "155744": "sim", "155743": "nao", "155742": "nao"}
    texto = _texto(html)
    assert "Liberada insumo baixado" in texto and "Liberada produto apontado" in texto
    assert 'id="btn-replanejar" disabled' in html
    assert "Replanejar selecionadas" in texto


def test_sem_o_insumo_baixado_nenhuma_liberada_e_oferecida(cliente):
    """Fail-closed: a row without the issued quantity offers no Replanejar (the action would
    refuse it too), and the grid never shows "Baixada" as a column of its own."""
    html = _busca_para_replanejar(cliente, {})
    assert ">Baixada<" not in html
    marcas = dict(re.findall(r'name="op_docnums" value="(\d+)"\s+data-replanejar="(\w+)"', html))
    assert marcas == {"155747": "na", "155744": "nao", "155743": "nao", "155742": "nao"}
    assert "não foi possível conferir o insumo" in _texto(html)


def test_replanejar_pela_tela_devolve_para_planejada(cliente):
    """D4 (29/09/2026): Replanejar runs from the screen like Liberar — one click, background."""
    ops = [{"doc_entry": 1, "doc_num": 155744, "status": "R", "item_code": "A",
            "planejada": 1.0, "apontada": 0.0, "pedido": 84376, "baixada": 0.0}]
    mudar = AsyncMock(return_value={"alteradas": ops, "com_erro": [], "ignoradas": []})
    with _tela_ops(
        patch(f"{SVC_OP}.levanta_ops", return_value=ops),
        patch(f"{SVC_OP}.muda_status", mudar),
        execucao=True,
    ):
        resposta = cliente.post(
            "/manutencao-op/status", data={"op_docnums": ["155744"], "acao": "p"},
            follow_redirects=False,
        )
        assert resposta.status_code == 303, resposta.text[:400]
        estado = _espera_terminar(cliente, resposta.headers["location"].rsplit("/", 1)[-1])

    assert estado["nome"] == "Replanejar OPs" and estado["desfecho"] == "ok"
    assert mudar.await_args.args[1:] == (ops, "p")


def test_replanejar_pela_tela_recusa_op_com_insumo_baixado(cliente):
    """The screen shows the API's refusal, with the OPs in a table — nothing is written."""
    ops = [
        {"doc_entry": 1, "doc_num": 155744, "status": "R", "item_code": "A",
         "planejada": 1.0, "apontada": 0.0, "pedido": 84376, "baixada": 0.0},
        {"doc_entry": 2, "doc_num": 155743, "status": "R", "item_code": "B",
         "planejada": 1.0, "apontada": 0.0, "pedido": 84376, "baixada": 3.5},
    ]
    mudar = AsyncMock()
    with _tela_ops(patch(f"{SVC_OP}.levanta_ops", return_value=ops), patch(f"{SVC_OP}.muda_status", mudar)):
        resposta = cliente.post(
            "/manutencao-op/status", data={"op_docnums": ["155744", "155743"], "acao": "p"},
        )

    assert resposta.status_code == 400
    texto = _texto(resposta.text)
    assert "já têm saída de insumo lançada" in texto and "Nenhuma OP foi alterada" in texto
    assert "155743" in texto and "Baixado" in texto
    mudar.assert_not_called()


def test_acao_desconhecida_pela_tela_e_recusada(cliente):
    levanta = MagicMock()
    with _tela_ops(patch(f"{SVC_OP}.levanta_ops", levanta)):
        resposta = cliente.post("/manutencao-op/status", data={"op_docnums": ["155747"], "acao": "c"})
    assert resposta.status_code == 400
    assert "use liberar ou replanejar" in resposta.text
    levanta.assert_not_called()


# ---------------------------------------------------------------------------
# 11. A linha do banco vira modelo (23/09/2026)
# ---------------------------------------------------------------------------
def test_busca_monta_o_modelo_a_partir_da_linha_do_hana():
    """Exercita a conversão linha→modelo, que TODOS os outros testes substituem por dublê.

    Foi exatamente esse buraco que deixou passar um erro para o Anderson em 23/09: um
    rename por expressão regular trocou `opp_id=` (argumento nomeado do Pydantic) por
    `orc_num=` na construção de `PedidoParaIntegrar`, e os 233 testes passaram porque
    `buscar_pedidos_para_integrar` está sempre mockada na camada web. A tela quebrou com
    `ValidationError: opp_id Field required` no primeiro clique.

    A lição do teste: quando uma função é sempre substituída por dublê, alguém precisa
    exercitar a de verdade — senão a fronteira entre o banco e o modelo não tem cobertura
    nenhuma, que é onde os nomes das colunas moram.
    """
    import asyncio

    from controleproducao.modules.pedidos_wbc import service as svc

    linha = {
        "Selecionar": "N",
        "Num Oportunidade": 15149,        # OPR1.OpprId — a chave da Oportunidade
        "Nº Pedido": 84397,
        "Cod.Cliente": "C011962",
        "Nome Cliente": "N.C. SERVICOS LTDA",
        "Total Pedido": 4035.33,
        "Data Lancamento": "2026-09-15 00:00:00",
        "Nº Oportunidade": "00125551",    # OOPR.U_ORCNUM_MASC — o nº do orçamento WBC
    }
    leitor = MagicMock()
    leitor.fetch_all = MagicMock(return_value=[linha])

    pedidos = asyncio.run(svc.buscar_pedidos_para_integrar(leitor))

    assert len(pedidos) == 1
    pedido = pedidos[0]
    # As duas numerações, cada uma no seu campo — trocá-las é o erro recorrente do projeto.
    assert pedido.opp_id == 15149
    assert pedido.orc_num_masc == "00125551"
    assert pedido.doc_num == 84397
    assert pedido.total_pedido == 4035.33



# ---------------------------------------------------------------------------
# Grupos que não geram OP precisam aparecer (23/09/2026 — pedido 84426)
# ---------------------------------------------------------------------------
def test_grupo_sem_op_avisa_em_vez_de_dizer_concluido(cliente):
    """O pedido 84426 gerou OPs de 2 dos 4 itens e a tela disse "concluído".

    As três saídas sem OP de `_processa_grupo_producao` gravavam só no `@INO_LOG`, sem
    `logger.*`: nada disso chegava à tela nem ao resultado. Este teste prende o contrato
    novo — o motivo volta em `sem_op` e a linha de acompanhamento avisa."""
    async def _falso(sl, wbc, hana, ids, force=False):
        return {
            "processados": list(ids),
            "com_erro": [],
            "sem_op": [{
                "orc_num": ids[0], "grp_code": 999, "orc_itm": 3,
                "motivo": "GrpCode 999 (Cobertura) não está cadastrado em @INO_GRP_PRODUTOS"
                          " — nenhuma OP foi criada para este item",
            }],
        }

    with _Ligado(_patches()):
        conferir = cliente.post("/pedidos-wbc/processar/conferir", data={"opp_ids": ["4321"]})
        token = _token(conferir.text)
        with patch("controleproducao.modules.pedidos_wbc.service.processar_pedidos_novos", _falso), \
             patch("controleproducao.modules.pedidos_wbc.router.ServiceLayerClient", MagicMock()), \
             patch("controleproducao.modules.pedidos_wbc.router.WbcSqlServerClient", MagicMock()):
            resposta = cliente.post(
                "/pedidos-wbc/processar/executar", data={"token": token}, follow_redirects=False
            )
            tarefa_id = resposta.headers["location"].rsplit("/", 1)[-1]
            estado = _espera_terminar(cliente, tarefa_id)

    linhas = "\n".join(estado["linhas"])
    assert "ATENÇÃO" in linhas
    assert "@INO_GRP_PRODUTOS" in linhas
    # O que mais atrapalhou no 84426: a execução se anunciar como concluída sem ressalva.
    assert "concluído" not in linhas
    assert estado["resultado"]["sem_op"][0]["doc_num"] == str(PEDIDO.doc_num)


# ---------------------------------------------------------------------------
# Vários grupos do WBC para o mesmo item SAP (23/09/2026 — causa real do 84426)
# ---------------------------------------------------------------------------
def test_grupos_do_mesmo_item_nao_se_bloqueiam():
    """No 84426, três itens do orçamento viraram `I000003`. O 1º grupo criou a OP e o
    2º/3º a encontraram e pularam como reprocessamento. A decisão agora conta: só é
    reprocessamento o N-ésimo grupo de um item que já tinha N OPs antes da execução."""
    from controleproducao.modules.pedidos_wbc.service import _op_previa_do_grupo

    # Pedido novo: nenhuma OP prévia -> os três grupos criam.
    assert [_op_previa_do_grupo([], n) for n in (1, 2, 3)] == [None, None, None]
    # Pedido já completo: três OPs prévias -> os três pulam, cada um citando a sua.
    assert [_op_previa_do_grupo([10, 11, 12], n) for n in (1, 2, 3)] == [10, 11, 12]
    # Execução anterior parou no meio: só o primeiro pula.
    assert [_op_previa_do_grupo([10], n) for n in (1, 2, 3)] == [10, None, None]


def test_grupos_do_mesmo_item_criam_uma_op_cada():
    """O mesmo cenário ponta a ponta em `_processa_grupo_producao`: a foto das OPs prévias
    é tirada uma vez por item, ANTES de a execução criar qualquer OP para ele."""
    import asyncio

    from controleproducao.modules.pedidos_wbc import service as svc

    consultas = []
    criadas = []

    async def _ops_existentes(hana, doc_num, item):
        consultas.append(item)
        # Se a foto fosse tirada depois da 1ª criação, veria a OP recém-criada.
        return list(criadas)

    async def _cria_op(*a, **k):
        criadas.append(900 + len(criadas))
        return 0, criadas[-1]

    grupo = [SimpleNamespace(orc_num="00125540", grp_code=7, prd_desc="PP", prd_code="X",
                             linha=1, quantidade=1, id_integracao_orc=1, linha_orc=1)]
    hana = MagicMock()
    hana.fetch_all.side_effect = (
        lambda sql, params=(): [{"U_INO_ItemSAP": "I000003"}] if "INO_GRP_PRODUTOS" in sql else []
    )
    hana.fetch_all_values.return_value = []
    wbc = MagicMock()
    wbc.fetch_all.return_value = []

    contexto: dict = {}
    with patch.object(svc, "_ops_existentes_para_item", _ops_existentes), \
         patch.object(svc, "cria_ordem_producao", _cria_op), \
         patch.object(svc, "_pega_doc_entry_ped", AsyncMock(return_value="1")), \
         patch.object(svc, "checa_semi_acabado", AsyncMock()), \
         patch.object(svc, "marca_op_nas_linhas", AsyncMock()), \
         patch.object(svc, "_atualiza_doc", AsyncMock()), \
         patch.object(svc, "preenche_log", AsyncMock()):
        motivos = [
            asyncio.run(svc._processa_grupo_producao(
                MagicMock(), wbc, hana, "00125540", 7, orc_itm, grupo, grupo, False, contexto
            ))
            for orc_itm in (1, 2, 3)
        ]

    assert motivos == [None, None, None]
    assert len(criadas) == 3
    assert consultas == ["I000003"]  # uma foto só, antes da primeira criação


def test_numero_do_pedido_sai_do_doc_entry_e_nao_da_oportunidade():
    """84425: a Oportunidade tinha dois pedidos, o 84424 (cancelado) e o 84425. A busca
    antiga do DocNum pela Oportunidade devolveu o cancelado, e a checagem de OP existente,
    a quantidade da OP e a base do rateio foram lidas do pedido errado. O DocNum agora sai
    do DocEntry já resolvido — nunca de uma segunda busca pela Oportunidade."""
    import asyncio

    from controleproducao.modules.pedidos_wbc import service as svc

    pedidos_consultados = []

    async def _ops_existentes(hana, doc_num, item):
        pedidos_consultados.append(doc_num)
        return []

    def _fetch_all(sql, params=()):
        if "INO_GRP_PRODUTOS" in sql:
            return [{"U_INO_ItemSAP": "I000003"}]
        # Since F7 (29/09/2026) the DocEntry travels as a bound parameter.
        if 'FROM ORDR WHERE "DocEntry" = ?' in sql and params == (20099,):
            return [{"DocNum": 84425}]
        assert "OPR1" not in sql, f"DocNum não pode vir da Oportunidade: {sql}"
        return []

    hana = MagicMock()
    hana.fetch_all.side_effect = _fetch_all
    hana.fetch_all_values.return_value = []
    wbc = MagicMock()
    wbc.fetch_all.return_value = []
    grupo = [SimpleNamespace(orc_num="00125539", grp_code=7, prd_desc="PP", prd_code="X",
                             linha=1, quantidade=1, id_integracao_orc=1, linha_orc=1)]

    with patch.object(svc, "_ops_existentes_para_item", _ops_existentes), \
         patch.object(svc, "cria_ordem_producao", AsyncMock(return_value=(0, 1))), \
         patch.object(svc, "_pega_doc_entry_ped", AsyncMock(return_value="20099")), \
         patch.object(svc, "checa_semi_acabado", AsyncMock()), \
         patch.object(svc, "marca_op_nas_linhas", AsyncMock()), \
         patch.object(svc, "_atualiza_doc", AsyncMock()), \
         patch.object(svc, "preenche_log", AsyncMock()):
        asyncio.run(svc._processa_grupo_producao(
            MagicMock(), wbc, hana, "00125539", 7, 1, grupo, grupo, False, {}
        ))

    assert pedidos_consultados == ["84425"]


def test_quantidade_da_op_vem_da_linha_do_grupo():
    """84274: três OPs saíram com 255, a quantidade da linha 6, para linhas de 8, 1 e 18.
    A busca do legado pegava a linha sem OP de MAIOR número do item, não a do grupo. Com a
    regra nova (quantidade = nº de módulos), isso dá a quantidade de outra linha."""
    import asyncio

    from controleproducao.modules.pedidos_wbc import service as svc

    # U_INO_ORCITM -> (LineNum, Quantity, LineTotal) das linhas do pedido
    linhas = {"1": (0, 8.0, 800.0), "2": (1, 1.0, 100.0), "7": (6, 255.0, 25500.0)}

    def _values(sql, params=()):
        assert 'T0."U_INO_ORCITM" IN (' in sql, "a busca precisa se restringir à linha do grupo"
        # Since F7 the group's ORCITM values are bound parameters (after DocEntry and item).
        pedidas = [str(v) for v in params[2:]]
        achadas = [linhas[v] for v in pedidas if v in linhas]
        return [list(l) for l in sorted(achadas, reverse=True)]

    hana = MagicMock()
    hana.fetch_all.side_effect = lambda sql, params=(): (
        [{"U_INO_ItemSAP": "I000003"}] if "INO_GRP_PRODUTOS" in sql
        else [{"DocNum": 84274}] if 'FROM ORDR WHERE "DocEntry"' in sql else []
    )
    hana.fetch_all_values.side_effect = _values
    wbc = MagicMock()
    wbc.fetch_all.return_value = []

    quantidades = []

    async def _cria_op(sl, hana_, grupo, item_pai, linha, qtd, *a, **k):
        quantidades.append(qtd)
        return 0, 900 + len(quantidades)

    def _grupo(orc_itm):
        return [SimpleNamespace(orc_num="00125000", grp_code=7, prd_desc="PP", prd_code="X",
                                linha=orc_itm, quantidade=1, id_integracao_orc=1, linha_orc=1)]

    with patch.object(svc, "_ops_existentes_para_item", AsyncMock(return_value=[])), \
         patch.object(svc, "cria_ordem_producao", _cria_op), \
         patch.object(svc, "_pega_doc_entry_ped", AsyncMock(return_value="20000")), \
         patch.object(svc, "checa_semi_acabado", AsyncMock()), \
         patch.object(svc, "marca_op_nas_linhas", AsyncMock()), \
         patch.object(svc, "_atualiza_doc", AsyncMock()), \
         patch.object(svc, "preenche_log", AsyncMock()):
        contexto: dict = {}
        for orc_itm in ("1", "2", "7"):
            g = _grupo(orc_itm)
            asyncio.run(svc._processa_grupo_producao(
                MagicMock(), wbc, hana, "00125000", 7, int(orc_itm), g, g, False, contexto
            ))

    assert quantidades == [8.0, 1.0, 255.0]


# ---------------------------------------------------------------------------
# Top line and buttons that need something to act on (29/09/2026)
# ---------------------------------------------------------------------------
_RAIZ_SIS = __import__("pathlib").Path(__file__).resolve().parents[2]


def _linha_topo(html: str) -> str:
    inicio = html.index('<div class="ov-linha-topo">')
    return html[inicio:html.index('<form method="post" id="form-pedidos"', inicio)]


def test_busca_e_numeros_na_mesma_linha_com_a_busca_primeiro(cliente):
    with _Ligado(_patches(pedidos=_pedidos(3))):
        antes = cliente.get("/pedidos-wbc?buscar=0").text
        depois = cliente.get("/pedidos-wbc?buscar=1").text

    linha = _linha_topo(depois)
    assert linha.index('class="ov-filtros"') < linha.index('class="ov-kpi"')
    assert linha.count('class="ov-kpi"') == 2 and "Novos" in linha and "Página" in linha
    # Before the first search the search card is alone: no empty number cards.
    assert 'class="ov-kpi"' not in _linha_topo(antes)


def test_processar_nasce_desabilitado_e_so_liga_com_pedido_marcado(cliente):
    with _Ligado(_patches(pedidos=_pedidos(3))):
        html = cliente.get("/pedidos-wbc?buscar=1").text

    assert re.search(r'<button type="submit" class="ov-btn ov-btn--perigo" id="btn-processar" disabled', html)
    assert re.search(r'name="force" value="1"\s+id="forcar" disabled', html)
    assert "Marque ao menos um pedido." in html
    # The script counts the marked boxes, drives the button and clears "forçar" with none.
    for trecho in ("input[name=opp_ids]", "botao.disabled = n === 0",
                   'verbo + " selecionados (" + n + ")…"', 'data-verbo="Processar"',
                   "forcar.checked = false",
                   'addEventListener("pageshow", pinta)'):
        assert trecho in html, trecho


def test_dica_do_processar_acompanha_a_situacao(cliente):
    with _Ligado(_patches(pedidos=[])):
        antes = _texto(cliente.get("/pedidos-wbc?buscar=0").text)
        vazio = _texto(cliente.get("/pedidos-wbc?buscar=1").text)
    assert "Busque os pedidos para selecionar." in antes
    assert "Nenhum pedido para processar." in vazio


def test_liberar_e_encerrar_so_com_op_marcada(cliente):
    with _tela_ops(patch(f"{SVC_OP}.buscar_ops", return_value=_ops_com_terminais())):
        html = cliente.post("/manutencao-op/buscar", data={"doc_num": "84376"}).text
        inicial = cliente.get("/manutencao-op").text

    acoes = re.findall(r"<button[^>]*data-exige-selecao[^>]*>", html)
    assert len(acoes) == 3 and all("disabled" in b for b in acoes)      # Liberar, Replanejar, Encerrar
    assert "Marque ao menos uma OP." in html
    assert "b.disabled = n === 0" in html
    assert "Busque um pedido para selecionar as OPs." in _texto(inicial)


def test_conferir_so_com_numero_digitado(cliente):
    with _Ligado(_patches()):
        pedidos = cliente.get("/pedidos-wbc").text
    ops = cliente.get("/manutencao-op").text

    for html, campos in ((pedidos, 2), (ops, 1)):
        form = re.search(r"<form[^>]*data-exige-numero>.*?</form>", html, re.S).group(0)
        assert form.count("data-numero") == campos
        assert re.search(r'<button type="submit" class="ov-btn ov-btn--perigo" disabled', form)
        assert "(só números)" in form
    # The shared rule in base.html: digits only, after trimming.
    assert r"/^\d+$/.test(c.value.trim())" in ops


def test_encerrar_pedido_com_letras_e_erro_de_tela_e_nao_500(cliente):
    """"84a" used to escape `levanta_ops` as a ValueError → HTTP 500."""
    with _tela_ops():
        resposta = cliente.post("/manutencao-op/encerrar/conferir", data={"pedido": "84a"})
    assert resposta.status_code == 400
    assert "esperado um número" in _texto(resposta.text)


def test_cancelar_ops_limpa_os_espacos_do_numero(cliente):
    levanta = AsyncMock(return_value={"pedido": None, "ops": [], "bloqueantes": [],
                                      "a_cancelar": [], "ja_canceladas": []})
    with _Ligado(_patches()), \
         patch("controleproducao.modules.pedidos_wbc.service.levanta_ops_para_cancelamento", levanta):
        so_espacos = cliente.post("/pedidos-wbc/cancelar-ops/conferir", data={"doc_num": "   "})
        colado = cliente.post("/pedidos-wbc/cancelar-ops/conferir", data={"doc_num": " 84439 "})

    assert "Informe o nº do pedido ou o nº do orçamento." in _texto(so_espacos.text)
    assert levanta.await_count == 1
    assert levanta.await_args.kwargs["doc_num"] == "84439"
    assert "Pedido não encontrado." in _texto(colado.text)


def test_css_da_linha_do_topo_e_da_barra_no_celular():
    css = (_RAIZ_SIS / "controleproducao/static/style.css").read_text(encoding="utf-8")
    assert "grid-template-columns: auto 1fr 1fr" in css
    assert re.search(r"@media \(max-width: 760px\) \{\s*\.ov-linha-topo \{ grid-template-columns: 1fr; \}", css)
    # The one-line nav forced every page to ~570px on a 375px phone; it wraps now.
    assert re.search(r"@media \(max-width: 760px\) \{\s*\.ov-nav \{\s*height: auto; flex-wrap: wrap;", css)
    assert ".ov-opcao:has(input:disabled)" in css
    base = (_RAIZ_SIS / "controleproducao/templates/base.html").read_text(encoding="utf-8")
    assert "style.css?v=10" in base
    # 29/09/2026: the mode choice of the search card is larger than the other options.
    assert re.search(r"\.ov-linha-topo \.ov-opcao input\[type=\"radio\"\] \{\s*width: 20px; height: 20px;", css)


def test_escrita_recusada_no_navegador_mostra_a_pagina_de_erro(cliente):
    """30/09/2026: the browser used to show FastAPI's raw JSON after "Confirmar"."""
    with _Ligado(_patches(producao=True)):
        conferir = cliente.post("/pedidos-wbc/processar/conferir", data={"opp_ids": ["4321"]})
        token = _token(conferir.text)
        resposta = cliente.post(
            "/pedidos-wbc/processar/executar", data={"token": token},
            headers={"Accept": "text/html,application/xhtml+xml", "Referer": "http://evil.example//x"},
        )
    assert resposta.status_code == 503
    assert "text/html" in resposta.headers["content-type"]
    assert "Escrita recusada" in resposta.text and "máquina de produção" in resposta.text
    assert 'href="//' not in resposta.text               # the Referer never becomes an off-site link


def test_dispara_com_modulo_ocupado_leva_a_execucao_em_andamento(cliente):
    import asyncio as _asyncio

    from controleproducao.modules.pedidos_wbc import router as r

    parar = _asyncio.Event()

    async def demorado(_t):
        await parar.wait()

    async def cenario():
        from starlette.requests import Request

        pedido = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b""})
        TAREFAS.criar(r.MODULO, "Processar pedidos novos", "a", demorado)
        resposta = r._dispara(pedido, "Processar pedidos novos", "b", demorado)
        rodando = TAREFAS.em_execucao(r.MODULO)
        parar.set()
        return resposta, rodando

    resposta, rodando = _asyncio.run(cenario())
    assert resposta.status_code == 400
    assert f'href="/tarefas/{rodando.id}"' in resposta.body.decode()


# ---------------------------------------------------------------------------
# Shared with the JSON API (29/09/2026, PLANO_API_MANUTENCAO_OP F1)
# ---------------------------------------------------------------------------
def test_tela_com_modulo_ocupado_nao_gasta_o_token_do_encerrar(cliente):
    """The token used to be spent before the busy check: the operator got "já existe execução
    em andamento" AND had to check the plan again. Now the plan survives the refusal."""
    ops = [{"doc_entry": 1, "doc_num": 101, "status": "P", "item_code": "A",
            "planejada": 1.0, "apontada": 0.0, "pedido": 84245}]
    with _tela_ops(
        patch(f"{SVC_OP}.levanta_ops", return_value=ops),
        patch(f"{SVC_OP}._componentes_por_op", return_value={}),
    ):
        token = _token(cliente.post("/manutencao-op/encerrar/conferir", data={"pedido": "84245"}).text)
        with patch("controleproducao.core.tarefas.RegistroDeTarefas.confere_livre",
                   side_effect=RuntimeError("O módulo 'manutencao_op' já tem uma execução em andamento")):
            recusada = cliente.post("/manutencao-op/encerrar/executar", data={"token": token})

    assert recusada.status_code == 400
    assert "já tem uma execução em andamento" in _texto(recusada.text)
    assert PLANOS.obter(token) is not None


def test_numero_de_op_com_letra_no_liberar_e_erro_de_tela_e_nao_500(cliente):
    """`levanta_ops` refuses "12a" with a ValueError; the screen let it escape as a 500."""
    with _tela_ops():
        resposta = cliente.post("/manutencao-op/status", data={"op_docnums": ["12a"], "acao": "l"})
    assert resposta.status_code == 400
    assert "Número da OP inválido" in _texto(resposta.text)


def test_tela_da_execucao_tem_voltar_para_a_origem_e_sem_faixa_de_producao():
    """29/09/2026 (owner): a "Voltar" to the screen the execution came from, and no red
    "Gravando em PRODUÇÃO" strip."""
    base = (_RAIZ_SIS / "controleproducao/templates/base.html").read_text(encoding="utf-8")
    tarefa = (_RAIZ_SIS / "controleproducao/templates/tarefa.html").read_text(encoding="utf-8")
    assert "ov-faixa--producao" not in base
    assert "'pedidos_wbc': ('/pedidos-wbc', 'Pedidos WBC')" in tarefa
    assert 'id="voltar"' in tarefa
