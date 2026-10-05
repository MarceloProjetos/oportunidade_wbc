"""Testes do cancelamento em lote das OPs de um pedido (`pedidos-wbc cancelar-ops`).

Feature pedida pelo Anderson em 16/09/2026, para limpar um pedido de vendas. A regra é
lista branca: só começa se **todas** as OPs do pedido estiverem Planejadas (`P`) ou
Canceladas (`C`). Qualquer uma Liberada (`R`) ou Encerrada (`L`) aborta o processo inteiro.

O teste que mais importa aqui é o `test_nada_e_cancelado_quando_ha_op_liberada`: a garantia
de que a barreira funciona precisa valer sempre, não só no dia em que foi escrita. Um
cancelamento indevido de OP não tem desfazer trivial.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from controleproducao.cli import app
from controleproducao.modules.pedidos_wbc import service as svc

PEDIDO = {
    "DocEntry": 19124, "DocNum": 84245, "CardCode": "C1", "CardName": "DURATEX S/A",
    "ProcessWBC": "Y", "CotWBC": "00125192", "DocStatus": "O", "CANCELED": "N",
}


def _op(doc_entry, doc_num, item, status):
    return {
        "DocEntry": doc_entry, "DocNum": doc_num, "ItemCode": item, "PlannedQty": 1,
        "CmpltQty": 0, "Status": status, "Type": "S", "U_INO_LinhaRef": "1",
        "Warehouse": "01", "CreateDate": "2026-09-16", "OriginAbs": 19124, "OriginNum": 84245,
    }


def _executa(ops, argumentos=None, linhas_com_op=()):
    """Roda o comando com HANA e Service Layer simulados.

    Devolve (resultado_cli, escritas_na_service_layer). A lista de escritas é a prova real
    de que nada foi alterado nos cenários que devem barrar.

    `argumentos=None` usa `--sim` (pula a confirmação); `argumentos=[]` roda SEM flag
    nenhuma, para exercitar a confirmação. O sentinela precisa ser `None` e não um teste de
    verdade: `argumentos or ["--sim"]` trataria a lista vazia como ausência e injetaria o
    `--sim` justamente no teste que quer verificar a confirmação — foi o que aconteceu na
    primeira versão, e o teste "passou a cancelar sem confirmar" por culpa do próprio teste.

    `linhas_com_op`: LineNums do pedido que têm `U_INO_OP` preenchido, para exercitar a
    limpeza dos vínculos.
    """
    linha_de_comando = ["--sim"] if argumentos is None else argumentos
    escritas = []

    def hana_fetch(sql, *_a, **_k):
        if "ORDR" in sql and "DocNum" in sql:
            return [PEDIDO]
        if "OWOR" in sql:
            return ops
        if "RDR1" in sql and "U_INO_OP" in sql:
            return [{"LineNum": n} for n in linhas_com_op]
        return []

    with patch("controleproducao.cli.get_settings") as cfg, patch("controleproducao.cli.HanaDirectReader") as leitor, \
            patch("controleproducao.cli.ServiceLayerClient") as cliente_sl:
        # Sem isto, `settings.is_production` seria um MagicMock (verdadeiro) e a trava de
        # escrita em produção recusaria o comando antes de ele fazer qualquer coisa.
        cfg.return_value.is_production = False
        leitor.return_value.fetch_all = MagicMock(side_effect=hana_fetch)
        leitor.return_value.close = MagicMock()

        async def update_entity(entity, key, fields, **_kw):
            escritas.append((entity, key, fields))

        cliente_sl.return_value.__aenter__.return_value.update_entity = AsyncMock(
            side_effect=update_entity
        )
        resultado = CliRunner().invoke(
            app, ["pedidos-wbc", "cancelar-ops", "84245"] + linha_de_comando
        )
    return resultado, escritas


# ---------------------------------------------------------------------------
# A barreira
# ---------------------------------------------------------------------------
def test_nada_e_cancelado_quando_ha_op_liberada():
    """O requisito central: uma OP liberada impede o processo inteiro — inclusive o
    cancelamento das que estariam aptas."""
    resultado, escritas = _executa([
        _op(101, 9001, "I000002", "P"),
        _op(102, 9002, "PAR000PADRA", "R"),
        _op(103, 9003, "ESTSAP", "P"),
    ])

    assert escritas == [], "nenhuma OP pode ser alterada quando existe uma liberada"
    assert resultado.exit_code == 1
    assert "Nenhuma OP foi alterada" in resultado.output
    assert "9002" in resultado.output, "a OP que bloqueou precisa ser identificada"


def test_op_encerrada_tambem_bloqueia():
    """A regra é lista branca (P/C), então `L` bloqueia junto com `R` — e é o caso mais
    sensível: uma OP encerrada já movimentou estoque."""
    resultado, escritas = _executa([
        _op(101, 9001, "I000002", "P"),
        _op(104, 9004, "ESTPRT", "L"),
    ])

    assert escritas == []
    assert resultado.exit_code == 1


def test_status_desconhecido_bloqueia():
    """Status fora do previsto não pode ser tratado como liberado para cancelar."""
    _, escritas = _executa([_op(101, 9001, "I000002", "X")])
    assert escritas == []


# ---------------------------------------------------------------------------
# O caminho feliz
# ---------------------------------------------------------------------------
def _cancelamentos(escritas):
    return [(chave, campos) for entidade, chave, campos in escritas if entidade == "ProductionOrders"]


def _limpezas(escritas):
    return [(chave, campos) for entidade, chave, campos in escritas if entidade == "Orders"]


def test_cancela_apenas_as_planejadas():
    resultado, escritas = _executa([
        _op(101, 9001, "I000002", "P"),
        _op(102, 9002, "PAR000PADRA", "C"),  # já cancelada: não deve ser tocada
        _op(103, 9003, "ESTSAP", "P"),
    ])

    assert resultado.exit_code == 0
    cancelamentos = _cancelamentos(escritas)
    assert sorted(chave for chave, _campos in cancelamentos) == [101, 103]
    assert all(campos == {"ProductionOrderStatus": "boposCancelled"} for _c, campos in cancelamentos)


def test_cancela_e_ainda_assim_limpa_quando_todas_ja_estao_canceladas():
    """Sem OP a cancelar a limpeza continua valendo: o pedido pode estar com
    `U_INO_ProcessWBC='Y'` de uma execução anterior, e é isso que trava o reprocessamento."""
    resultado, escritas = _executa([_op(102, 9002, "X", "C")])

    assert resultado.exit_code == 0
    assert _cancelamentos(escritas) == []
    assert len(_limpezas(escritas)) == 1


def test_nada_acontece_se_todas_canceladas_e_manter_vinculos():
    _resultado, escritas = _executa([_op(102, 9002, "X", "C")], argumentos=["--sim", "--manter-vinculos"])
    assert escritas == []


# ---------------------------------------------------------------------------
# Limpeza dos vínculos do pedido
# ---------------------------------------------------------------------------
def test_limpa_cabecalho_e_linhas_numa_unica_chamada():
    _resultado, escritas = _executa(
        [_op(101, 9001, "I000002", "P")], linhas_com_op=[0, 3, 7]
    )

    limpezas = _limpezas(escritas)
    assert len(limpezas) == 1, "cabeçalho e linhas devem ir no mesmo PATCH"
    _chave, campos = limpezas[0]
    assert campos == {
        "U_INO_ProcessWBC": "N",
        "DocumentLines": [
            {"LineNum": 0, "U_INO_OP": 0},
            {"LineNum": 3, "U_INO_OP": 0},
            {"LineNum": 7, "U_INO_OP": 0},
        ],
    }


def test_limpeza_sem_linhas_vinculadas_so_mexe_no_cabecalho():
    _resultado, escritas = _executa([_op(101, 9001, "I000002", "P")], linhas_com_op=[])
    _chave, campos = _limpezas(escritas)[0]
    assert campos == {"U_INO_ProcessWBC": "N"}
    assert "DocumentLines" not in campos


def test_manter_vinculos_cancela_mas_nao_limpa():
    resultado, escritas = _executa(
        [_op(101, 9001, "I000002", "P")], argumentos=["--sim", "--manter-vinculos"], linhas_com_op=[0]
    )

    assert resultado.exit_code == 0
    assert len(_cancelamentos(escritas)) == 1
    assert _limpezas(escritas) == [], "com --manter-vinculos o pedido não pode ser alterado"


def test_limpeza_e_pulada_se_algum_cancelamento_falhar():
    """O ponto mais delicado: marcar o pedido como 'não processado' com OP viva apontando
    para ele é pior que o estado anterior, porque some da vista sem ter sumido do banco."""
    escritas = []

    def hana_fetch(sql, *_a, **_k):
        if "ORDR" in sql and "DocNum" in sql:
            return [PEDIDO]
        if "OWOR" in sql:
            return [_op(101, 9001, "A", "P"), _op(102, 9002, "B", "P")]
        if "RDR1" in sql and "U_INO_OP" in sql:
            return [{"LineNum": 0}]
        return []

    with patch("controleproducao.cli.get_settings") as cfg, patch("controleproducao.cli.HanaDirectReader") as leitor, \
            patch("controleproducao.cli.ServiceLayerClient") as cliente_sl:
        # Sem isto, `settings.is_production` seria um MagicMock (verdadeiro) e a trava de
        # escrita em produção recusaria o comando antes de ele fazer qualquer coisa.
        cfg.return_value.is_production = False
        leitor.return_value.fetch_all = MagicMock(side_effect=hana_fetch)
        leitor.return_value.close = MagicMock()

        async def update_entity(entity, key, fields, **_kw):
            if entity == "ProductionOrders" and key == 102:
                raise RuntimeError("OP travada")
            escritas.append((entity, key, fields))

        cliente_sl.return_value.__aenter__.return_value.update_entity = AsyncMock(
            side_effect=update_entity
        )
        resultado = CliRunner().invoke(app, ["pedidos-wbc", "cancelar-ops", "84245", "--sim"])

    assert resultado.exit_code == 1
    assert _limpezas(escritas) == [], "não pode limpar o pedido com OP ainda planejada"
    assert "limpeza dos vínculos do pedido NÃO foi feita" in resultado.output


def test_exige_confirmacao_sem_a_flag_sim():
    """Sem `--sim`, a confirmação é obrigatória: responder não (entrada vazia) não escreve."""
    _resultado, escritas = _executa([_op(101, 9001, "I000002", "P")], argumentos=[])
    assert escritas == []


def test_pedido_sem_ops_sai_sem_erro():
    resultado, escritas = _executa([])
    assert escritas == []
    assert resultado.exit_code == 0
    assert "não tem nenhuma Ordem de Produção" in resultado.output


# ---------------------------------------------------------------------------
# A camada de serviço, direto
# ---------------------------------------------------------------------------
def test_levantamento_classifica_por_status():
    hana = MagicMock()
    hana.fetch_all = MagicMock(side_effect=lambda sql, *a, **k: (
        [PEDIDO] if "ORDR" in sql and "DocNum" in sql
        else [_op(101, 9001, "A", "P"), _op(102, 9002, "B", "C"), _op(103, 9003, "C", "R")]
        if "OWOR" in sql else []
    ))

    levantamento = asyncio.run(svc.levanta_ops_para_cancelamento(hana, doc_num="84245"))

    assert [op["doc_entry"] for op in levantamento["a_cancelar"]] == [101]
    assert [op["doc_entry"] for op in levantamento["ja_canceladas"]] == [102]
    assert [op["doc_entry"] for op in levantamento["bloqueantes"]] == [103]


def test_levantamento_nao_escreve_nada():
    """A verificação é deliberadamente separada da execução — ela só lê."""
    hana = MagicMock()
    hana.fetch_all = MagicMock(return_value=[])
    asyncio.run(svc.levanta_ops_para_cancelamento(hana, doc_num="84245"))
    # `HanaDirectReader` não tem método de escrita; o que se garante aqui é que o
    # levantamento não chama a Service Layer (ela nem é parâmetro da função).
    assert "sl" not in svc.levanta_ops_para_cancelamento.__code__.co_varnames


def test_levantamento_exige_pedido_ou_orcamento():
    hana = MagicMock()
    with pytest.raises(ValueError):
        asyncio.run(svc.levanta_ops_para_cancelamento(hana))


def test_erro_numa_op_nao_impede_as_demais():
    """Cancelar 2 de 3 e dizer qual falhou é melhor do que parar no meio e deixar o pedido
    num estado que ninguém sabe qual é."""
    sl = AsyncMock()

    async def update_entity(_entity, key, _fields, **_kw):
        if key == 102:
            raise RuntimeError("OP travada por outro usuário")

    sl.update_entity = AsyncMock(side_effect=update_entity)
    ops = [
        {"doc_entry": 101, "doc_num": 9001, "item_code": "A"},
        {"doc_entry": 102, "doc_num": 9002, "item_code": "B"},
        {"doc_entry": 103, "doc_num": 9003, "item_code": "C"},
    ]

    resultado = asyncio.run(svc.cancela_ops_do_pedido(sl, ops))

    assert [op["doc_entry"] for op in resultado["canceladas"]] == [101, 103]
    assert len(resultado["com_erro"]) == 1
    assert resultado["com_erro"][0]["doc_entry"] == 102
    assert "travada" in resultado["com_erro"][0]["motivo"]


# ---------------------------------------------------------------------------
# The screen's path: re-read at execution, then clean like the CLI (01/10/2026 review)
# ---------------------------------------------------------------------------
def _conferidas(*ops):
    """The plan's items, as `levanta_ops_para_cancelamento` produced them at checking time."""
    return [svc._resumo_op(op) for op in ops]


def _tela(ops_agora, conferidas, *, deve_parar=None, falha_em=(), linhas_com_op=(7,)):
    escritas = []
    hana = MagicMock()
    hana.fetch_all = MagicMock(side_effect=lambda sql, *a, **k: (
        [PEDIDO] if "ORDR" in sql and "DocNum" in sql
        else ops_agora if "OWOR" in sql
        else [{"LineNum": n} for n in linhas_com_op] if "RDR1" in sql and "U_INO_OP" in sql
        else []
    ))
    sl = AsyncMock()

    async def update_entity(entidade, chave, campos, **_kw):
        if entidade == "ProductionOrders" and chave in falha_em:
            raise RuntimeError("recusada pela Service Layer")
        escritas.append((entidade, chave, campos))

    sl.update_entity = AsyncMock(side_effect=update_entity)
    resultado = asyncio.run(
        svc.cancela_ops_conferidas(sl, hana, "84245", conferidas, deve_parar=deve_parar)
    )
    return resultado, escritas


def test_tela_cancela_e_devolve_o_pedido_como_a_cli():
    """The screen never cleaned the links: the order stayed ProcessWBC='Y' and the documented
    recovery (cancelar-ops → processar-novos) did not work from the screen."""
    ops = [_op(101, 9001, "A", "P"), _op(102, 9002, "B", "P")]
    resultado, escritas = _tela(ops, _conferidas(*ops))

    assert [chave for chave, _ in _cancelamentos(escritas)] == [101, 102]
    assert _limpezas(escritas) == [
        (19124, {"U_INO_ProcessWBC": "N", "DocumentLines": [{"LineNum": 7, "U_INO_OP": 0}]})
    ]
    assert resultado["limpeza"] == {"linhas_limpas": 1}


def test_tela_op_liberada_depois_da_conferencia_barra_tudo():
    """Checked at 10:00 (all planned); someone released 9002 at 10:04; confirmed at 10:05.
    It used to be cancelled anyway, against the white list."""
    conferidas = _conferidas(_op(101, 9001, "A", "P"), _op(102, 9002, "B", "P"))
    agora = [_op(101, 9001, "A", "P"), _op(102, 9002, "B", "R")]
    resultado, escritas = _tela(agora, conferidas)

    assert escritas == []
    assert resultado["limpeza"] == "pulada"
    assert resultado["com_erro"][0]["doc_entry"] == 102
    assert "mudou" in resultado["com_erro"][0]["motivo"]


def test_tela_op_cancelada_por_outro_nao_e_tocada_e_o_pedido_e_limpo():
    conferidas = _conferidas(_op(101, 9001, "A", "P"), _op(102, 9002, "B", "P"))
    agora = [_op(101, 9001, "A", "P"), _op(102, 9002, "B", "C")]
    resultado, escritas = _tela(agora, conferidas)

    assert [chave for chave, _ in _cancelamentos(escritas)] == [101]
    assert [op["doc_entry"] for op in resultado["status_mudou"]] == [102]
    assert _limpezas(escritas), "every OP ended cancelled: the order goes back"


def test_tela_op_planejada_fora_da_conferencia_nao_e_tocada_e_nao_limpa():
    conferidas = _conferidas(_op(101, 9001, "A", "P"))
    agora = [_op(101, 9001, "A", "P"), _op(103, 9003, "C", "P")]
    resultado, escritas = _tela(agora, conferidas)

    assert [chave for chave, _ in _cancelamentos(escritas)] == [101]
    assert [op["doc_entry"] for op in resultado["nao_conferidas"]] == [103]
    assert not _limpezas(escritas) and resultado["limpeza"] == "pulada"


def test_tela_interromper_para_entre_ops_e_nao_limpa():
    ops = [_op(101, 9001, "A", "P"), _op(102, 9002, "B", "P"), _op(103, 9003, "C", "P")]
    chamadas = iter([False, True])
    resultado, escritas = _tela(ops, _conferidas(*ops), deve_parar=lambda: next(chamadas))

    assert [chave for chave, _ in _cancelamentos(escritas)] == [101]
    assert [op["doc_entry"] for op in resultado["nao_iniciadas"]] == [102, 103]
    assert not _limpezas(escritas) and resultado["limpeza"] == "pulada"


def test_tela_falha_de_uma_op_nao_limpa():
    ops = [_op(101, 9001, "A", "P"), _op(102, 9002, "B", "P")]
    resultado, escritas = _tela(ops, _conferidas(*ops), falha_em=(101,))

    assert [chave for chave, _ in _cancelamentos(escritas)] == [102]
    assert resultado["com_erro"][0]["doc_entry"] == 101
    assert not _limpezas(escritas) and resultado["limpeza"] == "pulada"
