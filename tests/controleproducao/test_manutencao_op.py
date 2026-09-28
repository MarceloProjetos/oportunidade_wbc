"""Testes de `manutencao-op liberar` / `replanejar` (módulo 3, implementado em 16/09/2026).

Porte de `ManutencaoOp.mudaStatus` + `updateOP` (ManutencaoOp.b1f.cs, linhas ~241 e ~278).
No legado o usuário via os status na grade e clicava em Liberar/Planejar; na CLI a tabela
impressa antes da confirmação faz esse papel.

O `replanejar` tem um uso prático além da paridade com o legado: é o caminho para destravar
um pedido cujo `pedidos-wbc cancelar-ops` foi barrado por OP liberada.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from controleproducao.cli import app
from controleproducao.modules.manutencao_op import service as svc


def _op(doc_entry, doc_num, item, status, planejada=2, apontada=0):
    return {
        "DocEntry": doc_entry, "DocNum": doc_num, "Status": status, "ItemCode": item,
        "PlannedQty": planejada, "CmpltQty": apontada, "OriginNum": 84245,
    }


def _executa(ops, argumentos):
    """Roda o comando com HANA e Service Layer simulados; devolve (resultado, escritas)."""
    escritas = []

    with patch("controleproducao.cli.get_settings") as cfg, patch("controleproducao.cli.HanaDirectReader") as leitor, \
            patch("controleproducao.cli.ServiceLayerClient") as cliente_sl:
        # Sem isto, `settings.is_production` seria um MagicMock (verdadeiro) e a trava de
        # escrita em produção recusaria o comando antes de ele fazer qualquer coisa.
        cfg.return_value.is_production = False
        leitor.return_value.fetch_all = MagicMock(return_value=ops)
        leitor.return_value.close = MagicMock()

        async def update_entity(entity, key, fields, **_kw):
            escritas.append((entity, key, fields))

        cliente_sl.return_value.__aenter__.return_value.update_entity = AsyncMock(
            side_effect=update_entity
        )
        resultado = CliRunner().invoke(app, ["manutencao-op"] + argumentos)
    return resultado, escritas


# ---------------------------------------------------------------------------
# Liberar
# ---------------------------------------------------------------------------
def test_liberar_altera_so_as_planejadas():
    """Pular as que já estão no destino evita chamadas inúteis à Service Layer — que é o
    recurso mais caro da execução (52% do tempo, seção 7.14 do guia)."""
    resultado, escritas = _executa(
        [_op(101, 9001, "I000002", "P"), _op(102, 9002, "ESTPRT", "R")],
        ["liberar", "9001", "9002", "--sim"],
    )

    assert resultado.exit_code == 0
    assert [chave for _e, chave, _f in escritas] == [101]
    assert escritas[0][2] == {"ProductionOrderStatus": "boposReleased"}


def test_liberar_por_pedido():
    _resultado, escritas = _executa(
        [_op(101, 9001, "A", "P"), _op(102, 9002, "B", "P")],
        ["liberar", "--pedido", "84245", "--sim"],
    )
    assert sorted(chave for _e, chave, _f in escritas) == [101, 102]


# ---------------------------------------------------------------------------
# Replanejar
# ---------------------------------------------------------------------------
def test_replanejar_devolve_liberada_para_planejada():
    _resultado, escritas = _executa(
        [_op(101, 9001, "I000002", "P"), _op(102, 9002, "ESTPRT", "R")],
        ["replanejar", "--pedido", "84245", "--sim"],
    )

    assert [chave for _e, chave, _f in escritas] == [102]
    assert escritas[0][2] == {"ProductionOrderStatus": "boposPlanned"}


# ---------------------------------------------------------------------------
# Guardas
# ---------------------------------------------------------------------------
def test_nao_chama_nada_se_todas_ja_estao_no_destino():
    resultado, escritas = _executa([_op(101, 9001, "X", "R")], ["liberar", "9001", "--sim"])
    assert escritas == []
    assert resultado.exit_code == 0


def test_exige_confirmacao():
    _resultado, escritas = _executa([_op(101, 9001, "X", "P")], ["liberar", "9001"])
    assert escritas == []


def test_recusa_ops_e_pedido_ao_mesmo_tempo():
    resultado, escritas = _executa([], ["liberar", "9001", "--pedido", "84245", "--sim"])
    assert escritas == []
    assert resultado.exit_code == 1


def test_recusa_sem_ops_e_sem_pedido():
    resultado, escritas = _executa([], ["liberar", "--sim"])
    assert escritas == []
    assert resultado.exit_code == 1


def test_sem_ops_encontradas_sai_sem_erro():
    resultado, escritas = _executa([], ["liberar", "9999", "--sim"])
    assert escritas == []
    assert resultado.exit_code == 0


# ---------------------------------------------------------------------------
# A camada de serviço
# ---------------------------------------------------------------------------
def test_transicoes_correspondem_ao_updateop_original():
    """`updateOP` (linha ~278): p=Planned, c=Cancelled, l=Released, f=Closed — e o "f"
    também grava ClosingDate."""
    assert svc.TRANSICOES["p"]["sl"] == "boposPlanned"
    assert svc.TRANSICOES["c"]["sl"] == "boposCancelled"
    assert svc.TRANSICOES["l"]["sl"] == "boposReleased"
    assert svc.TRANSICOES["f"]["sl"] == "boposClosed"
    assert svc.TRANSICOES["f"].get("fecha") is True


def test_encerrar_grava_data_de_fechamento():
    """Fiel ao `OrdemPrducao.ClosingDate = DateTime.Now` do original. O "f" não é exposto
    na CLI (precisa da movimentação de estoque antes), mas a transição está correta para
    quando `finalizar_ops` for implementado."""
    sl = AsyncMock()
    asyncio.run(svc.muda_status(sl, [{"doc_entry": 1, "doc_num": 9001, "item_code": "X", "status": "R"}], "f"))

    campos = sl.update_entity.await_args.args[2]
    assert campos["ProductionOrderStatus"] == "boposClosed"
    assert "ClosingDate" in campos


def test_status_invalido_e_recusado():
    sl = AsyncMock()
    with pytest.raises(ValueError):
        asyncio.run(svc.muda_status(sl, [], "z"))


def test_erro_numa_op_nao_impede_as_demais():
    sl = AsyncMock()

    async def update_entity(_entity, key, _fields, **_kw):
        if key == 102:
            raise RuntimeError("status não permite a transição")

    sl.update_entity = AsyncMock(side_effect=update_entity)
    ops = [
        {"doc_entry": 101, "doc_num": 9001, "item_code": "A", "status": "P"},
        # Status "R", não "L": desde 22/09 a OP terminal é recusada ANTES da Service
        # Layer (`STATUS_TERMINAIS`), e este teste é sobre a falha VINDA DO SAP não
        # interromper o lote — não sobre a recusa local.
        {"doc_entry": 102, "doc_num": 9002, "item_code": "B", "status": "R"},
        {"doc_entry": 103, "doc_num": 9003, "item_code": "C", "status": "P"},
    ]

    resultado = asyncio.run(svc.muda_status(sl, ops, "l"))

    assert [op["doc_entry"] for op in resultado["alteradas"]] == [101, 103]
    assert len(resultado["com_erro"]) == 1
    assert resultado["com_erro"][0]["doc_entry"] == 102


def test_levantamento_exige_ops_ou_pedido():
    with pytest.raises(ValueError):
        svc.levanta_ops(MagicMock())


# ---------------------------------------------------------------------------
# OP cancelada é estado final (21/09/2026)
# ---------------------------------------------------------------------------
def test_cancelada_nao_e_liberada():
    """O SAP não libera nem replaneja OP cancelada. A grade do legado nunca mostrava
    canceladas (`OPS_MANUTENCAO` filtra `Status != 'C'`), mas o `OPS_POR_DOCNUM` — usado
    quando o usuário informa os números — não filtra: a OP chegava até a Service Layer e
    falhava com mensagem obscura."""
    resultado, escritas = _executa(
        [_op(101, 9001, "X", "C")], ["liberar", "9001", "--sim"]
    )
    assert escritas == []
    assert resultado.exit_code == 0
    assert "cancelada" in resultado.output


def test_cancelada_aparece_na_tabela_em_vez_de_desaparecer():
    """Sumir com um número que o usuário digitou é pior que explicar por que ele não entra."""
    resultado, _escritas = _executa(
        [_op(101, 9001, "X", "C")], ["liberar", "9001", "--sim"]
    )
    assert "9001" in resultado.output


def test_cancelada_no_meio_do_lote_nao_impede_as_demais():
    _resultado, escritas = _executa(
        [_op(101, 9001, "A", "C"), _op(102, 9002, "B", "P")],
        ["liberar", "--pedido", "84245", "--sim"],
    )
    assert [chave for _e, chave, _f in escritas] == [102]


# ---------------------------------------------------------------------------
# Status terminal (22/09/2026)
# ---------------------------------------------------------------------------
def test_op_encerrada_ou_cancelada_nao_muda_de_status():
    """Encerrada já teve a movimentação lançada; cancelada foi descartada.

    Quarta vez no projeto em que a proteção que morava na grade precisa virar regra em
    código. A tela desabilita a caixa dessas linhas, mas a tela é sugestão — o número
    chega por POST e pode ser qualquer um. Vai para `ignoradas`, não `com_erro`: não é
    falha, é recusa.
    """
    sl = AsyncMock()
    sl.update_entity = AsyncMock()
    ops = [
        {"doc_entry": 101, "doc_num": 9001, "item_code": "A", "status": "P"},
        {"doc_entry": 102, "doc_num": 9002, "item_code": "B", "status": "L"},
        {"doc_entry": 103, "doc_num": 9003, "item_code": "C", "status": "C"},
    ]

    resultado = asyncio.run(svc.muda_status(sl, ops, "l"))

    assert [op["doc_entry"] for op in resultado["alteradas"]] == [101]
    assert resultado["com_erro"] == []
    assert [op["doc_entry"] for op in resultado["ignoradas"]] == [102, 103]
    # E a Service Layer nem foi chamada para elas — a recusa é local.
    assert [c.args[1] for c in sl.update_entity.await_args_list] == [101]
    assert "Encerrada" in resultado["ignoradas"][0]["motivo"]
