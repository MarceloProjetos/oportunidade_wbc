"""Processar and Reprocessar sync the OS mirror by themselves (04/11/2026).

Pedidos 84530 and 84550: the mirror stayed on the old OPs until someone clicked Sincronização.
The copy goes through the API 8077 and never fails the execution: the SAP part is already done.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from pydantic import SecretStr

from controleproducao.core.tarefas import MARCA_PROBLEMA, Tarefa
from controleproducao.modules.pedidos_wbc import acoes

REAL = acoes.sincroniza_espelho
SETTINGS = SimpleNamespace(os_api_port=8077, os_api_key=SecretStr("chave"))


def _tarefa() -> Tarefa:
    return Tarefa(id="t", nome="Processar", descricao="d", criada_em=datetime.now(), modulo="pedidos_wbc",
                  parada_combinada=True)


def _roda(handler, doc_nums, tarefa=None):
    """The real function over a fake API."""
    tarefa = tarefa or _tarefa()
    original = httpx.AsyncClient

    def cliente(**kwargs):
        return original(transport=httpx.MockTransport(handler), **kwargs)

    with patch.object(acoes.httpx, "AsyncClient", cliente):
        return asyncio.run(REAL(tarefa, SETTINGS, doc_nums)), tarefa


def _resposta(*resultados, status=200):
    return httpx.Response(status, json={"results": list(resultados)})


def _ok(nped):
    return {"nped": nped, "ok": True, "tipo": None, "motivo": None}


def test_chama_a_api_local_com_a_chave_e_os_numeros() -> None:
    pedidos = []

    def api(request):
        pedidos.append(request)
        return _resposta(_ok(84530))

    resultado, tarefa = _roda(api, ["84530"])

    assert str(pedidos[0].url) == "http://127.0.0.1:8077/sync/ordens-servico"
    assert pedidos[0].headers["X-API-Key"] == "chave"
    assert json.loads(pedidos[0].read()) == {"npeds": [84530]}
    assert resultado == [{"doc_num": "84530", "ok": True, "tipo": None, "motivo": None}]
    assert any("Pedido 84530: sincronizado com a base de dados." in linha for linha in tarefa.linhas)
    assert not any(MARCA_PROBLEMA in linha for linha in tarefa.linhas)


def test_lote_grande_e_dividido_no_teto_da_api() -> None:
    tamanhos = []

    def api(request):
        npeds = json.loads(request.read())["npeds"]
        tamanhos.append(len(npeds))
        return _resposta(*[_ok(n) for n in npeds])

    resultado, _ = _roda(api, [str(n) for n in range(84000, 84051)])

    assert tamanhos == [acoes.LOTE_SINCRONIZACAO, 1]
    assert len(resultado) == 51 and all(r["ok"] for r in resultado)


def test_sem_op_viva_nao_e_problema() -> None:
    """After a Reprocessar every OP may be cancelled: the API copies nothing, and that is right."""
    resultado, tarefa = _roda(
        lambda _r: _resposta({"nped": 84530, "ok": False, "tipo": "cancelada",
                              "motivo": "A OS deste pedido esta cancelada."}, status=207),
        ["84530"],
    )

    assert resultado[0]["tipo"] == "cancelada"
    assert not any(MARCA_PROBLEMA in linha for linha in tarefa.linhas)


def test_falha_de_um_pedido_avisa_so_nele() -> None:
    resultado, tarefa = _roda(
        lambda _r: _resposta(_ok(84530), {"nped": 84531, "ok": False, "tipo": "erro",
                                          "motivo": "Nao foi possivel sincronizar."}, status=207),
        ["84530", "84531"],
    )

    assert [r["ok"] for r in resultado] == [True, False]
    (problema,) = [linha for linha in tarefa.linhas if MARCA_PROBLEMA in linha]
    assert "Pedido 84531: NÃO sincronizado" in problema and "tela Sincronização" in problema


def test_api_fora_nao_levanta_e_avisa_cada_pedido() -> None:
    def fora(_request):
        raise httpx.ConnectError("recusada")

    resultado, tarefa = _roda(fora, ["84530", "84531"])

    assert [r["ok"] for r in resultado] == [False, False]
    assert resultado[0]["motivo"] == "A API 8077 não respondeu."
    assert sum(MARCA_PROBLEMA in linha for linha in tarefa.linhas) == 2


def test_recusa_da_api_nao_levanta() -> None:
    resultado, _ = _roda(lambda _r: httpx.Response(401, json={"ok": False}), ["84530"])
    assert resultado[0]["ok"] is False


class _Sl:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return None


def _executa(tarefa, alvos, modo="processar", sincroniza=None, **servico):
    sincroniza = sincroniza or AsyncMock(return_value=[])
    nome = "reprocessar_pedidos_integrados" if modo == "reprocessar" else "processar_pedidos_novos"
    with patch(f"{acoes.__name__}.ServiceLayerClient", return_value=_Sl()), \
         patch(f"{acoes.service.__name__}.{nome}", AsyncMock(**servico)), \
         patch.object(acoes, "sincroniza_espelho", sincroniza):
        resultado = asyncio.run(acoes._roda_pedidos_com(
            tarefa, alvos, modo, False, SETTINGS, MagicMock(), MagicMock(),
        ))
    return resultado, sincroniza


def test_processar_sincroniza_ao_final_e_guarda_no_resultado() -> None:
    feitos = [{"doc_num": "84530", "ok": True, "tipo": None, "motivo": None}]
    resultado, sincroniza = _executa(
        _tarefa(), [("84530", "00125273")], sincroniza=AsyncMock(return_value=feitos),
        return_value={"processados": ["00125273"]},
    )

    assert sincroniza.await_args.args[2] == ["84530"]
    assert resultado["sincronizacao"] == feitos


def test_reprocessar_tambem_sincroniza() -> None:
    _, sincroniza = _executa(
        _tarefa(), [("84530", "00125273")], modo="reprocessar",
        return_value={"atualizados": ["00125273"]},
    )
    assert sincroniza.await_args.args[2] == ["84530"]


def test_pedido_com_erro_sincroniza_porque_pode_ter_deixado_ops() -> None:
    _, sincroniza = _executa(
        _tarefa(), [("84530", "00125273")],
        return_value={"com_erro": [{"orc_num": "00125273", "motivo": "falhou"}]},
    )
    assert sincroniza.await_args.args[2] == ["84530"]


def test_pedido_nao_iniciado_nao_sincroniza() -> None:
    tarefa = _tarefa()
    tarefa.parada_pedida = True
    resultado, sincroniza = _executa(tarefa, [("84530", "00125273")], return_value={})

    assert [p["doc_num"] for p in resultado["nao_iniciados"]] == ["84530"]
    sincroniza.assert_not_awaited()


def test_falha_da_sincronizacao_nao_vira_falha_da_execucao() -> None:
    tarefa = _tarefa()
    falhou = [{"doc_num": "84530", "ok": False, "tipo": "erro", "motivo": "x"}]
    resultado, _ = _executa(
        tarefa, [("84530", "00125273")], sincroniza=AsyncMock(return_value=falhou),
        return_value={"processados": ["00125273"]},
    )
    tarefa.resultado = resultado
    assert tarefa.com_falhas is False
