"""Desvio pelo `$batch` para chaves que o servidor web recusa na URL (22/09/2026).

Códigos de item do cliente contêm barra (`PPA3CPAB-1FF/D100220`). Percent-encodar é
obrigatório e está correto; quem recusa é o servidor web à frente da Service Layer, que
por padrão bloqueia `%2F` no caminho e devolve `404 Not Found` **em HTML** — a página de
erro é o que denuncia o culpado, porque a Service Layer erra em JSON.

No `$batch` a URI da operação é uma linha do corpo multipart: o servidor web vê só
`POST /$batch` e não tem o que inspecionar.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from controleproducao.core.exceptions import ServiceLayerError
from controleproducao.core.service_layer_client import (
    ServiceLayerClient,
    _formata_chave,
    _le_resposta_batch,
    _monta_corpo_batch,
    _precisa_de_batch,
)

COM_BARRA = "PPA3CPAB-1FF/D100220"
COM_CERQUILHA = "ESCESP00000SUP000000#1205#210#0"


def _settings():
    return SimpleNamespace(
        sl_base_url="https://sap:50000/b1s/v1",
        sl_verify_ssl=False,
        sl_company_db="SBOALTAMIRAHOMOLOG",
        sl_username="u",
        sl_password="p",
    )


def _cliente():
    with patch("httpx.AsyncClient", MagicMock()):
        sl = ServiceLayerClient(_settings())
    sl._logged_in = True
    return sl


# ---------------------------------------------------------------------------
# Quando desviar
# ---------------------------------------------------------------------------
def test_barra_codificada_desvia():
    assert _precisa_de_batch(f"Items({_formata_chave(COM_BARRA, texto=True)})")


def test_cerquilha_nao_desvia():
    """O `#` passa pelo servidor e já é resolvido pelo encoding desde 15/09.

    Desviar o que não precisa trocaria um caminho testado por um caminho novo sem motivo.
    """
    assert not _precisa_de_batch(f"Items({_formata_chave(COM_CERQUILHA, texto=True)})")


def test_chave_comum_nao_desvia():
    assert not _precisa_de_batch("Orders(19466)")
    assert not _precisa_de_batch("Items('PAR000PADRA000000000')")


# ---------------------------------------------------------------------------
# Formato do lote
# ---------------------------------------------------------------------------
def test_uri_da_operacao_vai_no_corpo_com_o_prefixo_e_ainda_codificada():
    corpo = _monta_corpo_batch(
        "B1", "PATCH", "/b1s/v1/Items('PPA3CPAB-1FF%2FD100220')", {"ItemsGroupCode": 332}, True
    )
    assert "PATCH /b1s/v1/Items('PPA3CPAB-1FF%2FD100220') HTTP/1.1" in corpo
    assert '{"ItemsGroupCode": 332}' in corpo
    # CRLF em TODA quebra: exigido pelo MIME, e a Service Layer é rigorosa — com `\n`
    # puro ela devolve erro de parsing sem dizer onde.
    assert "\r\n" in corpo
    assert corpo.replace("\r\n", "") .count("\n") == 0


def test_escrita_vai_em_changeset_e_leitura_nao():
    escrita = _monta_corpo_batch("B1", "PATCH", "/x", {"a": 1}, True)
    leitura = _monta_corpo_batch("B1", "GET", "/x", None, False)
    assert "changeset_" in escrita
    assert "changeset_" not in leitura
    assert "Content-Type: application/json" not in leitura


# ---------------------------------------------------------------------------
# Leitura da resposta
# ---------------------------------------------------------------------------
def test_le_sucesso_sem_corpo():
    resposta = (
        "--B1\r\nContent-Type: multipart/mixed;boundary=CS\r\n\r\n--CS\r\n"
        "Content-Type: application/http\r\n\r\nHTTP/1.1 204 No Content\r\n\r\n--CS--\r\n--B1--\r\n"
    )
    status, payload, _ = _le_resposta_batch(resposta)
    assert status == 204 and payload is None


def test_le_erro_de_negocio_com_a_mensagem_do_sap():
    resposta = (
        "--B1\r\nContent-Type: application/http\r\n\r\nHTTP/1.1 400 Bad Request\r\n"
        "Content-Type: application/json\r\n\r\n"
        '{"error":{"message":{"value":"Item nao existe"}}}\r\n--B1--\r\n'
    )
    status, payload, _ = _le_resposta_batch(resposta)
    assert status == 400
    assert payload["error"]["message"]["value"] == "Item nao existe"


def test_le_json_de_leitura():
    resposta = (
        "--B1\r\nContent-Type: application/http\r\n\r\nHTTP/1.1 200 OK\r\n\r\n"
        '{"ItemCode":"PPA3CPAB-1FF/D100220","ItemsGroupCode":332}\r\n--B1--\r\n'
    )
    status, payload, _ = _le_resposta_batch(resposta)
    assert status == 200
    # A Service Layer devolve o código DECODIFICADO — prova de que ela leu a URI inteira.
    assert payload["ItemCode"] == COM_BARRA


# ---------------------------------------------------------------------------
# Integração: o update_entity escolhe o caminho sozinho
# ---------------------------------------------------------------------------
def test_update_entity_com_barra_vai_pelo_batch_e_nao_pela_url():
    sl = _cliente()
    resposta = MagicMock(status_code=200, text=(
        "--B1\r\nContent-Type: application/http\r\n\r\nHTTP/1.1 204 No Content\r\n\r\n--B1--\r\n"
    ))
    sl._client.post = AsyncMock(return_value=resposta)
    sl._client.request = AsyncMock()

    asyncio.run(sl.update_entity("Items", COM_BARRA, {"ItemsGroupCode": 332}, chave_texto=True))

    # Nada foi pela URL: é isso que o servidor web recusava.
    sl._client.request.assert_not_awaited()
    caminho, = sl._client.post.await_args.args
    assert caminho == "/$batch"
    corpo = sl._client.post.await_args.kwargs["content"].decode()
    assert "PATCH /b1s/v1/Items('PPA3CPAB-1FF%2FD100220') HTTP/1.1" in corpo
    assert sl._client.post.await_args.kwargs["headers"]["Content-Type"].startswith("multipart/mixed")


def test_update_entity_sem_barra_continua_pela_url():
    """O desvio não pode virar o caminho padrão: o normal é testado, o desvio é exceção."""
    sl = _cliente()
    sl._client.request = AsyncMock(return_value=MagicMock(status_code=204, content=b""))
    sl._client.post = AsyncMock()

    asyncio.run(sl.update_entity("Orders", 19466, {"U_INO_ProcessWBC": "Y"}))

    sl._client.post.assert_not_awaited()
    metodo, caminho = sl._client.request.await_args.args
    assert (metodo, caminho) == ("PATCH", "/Orders(19466)")


def test_erro_de_negocio_dentro_do_lote_vira_excecao():
    """Status 200 no lote com 400 dentro não pode passar por sucesso."""
    sl = _cliente()
    resposta = MagicMock(status_code=200, text=(
        "--B1\r\nContent-Type: application/http\r\n\r\nHTTP/1.1 400 Bad Request\r\n\r\n"
        '{"error":{"message":{"value":"Item nao existe"}}}\r\n--B1--\r\n'
    ))
    sl._client.post = AsyncMock(return_value=resposta)

    with pytest.raises(ServiceLayerError) as erro:
        asyncio.run(sl.update_entity("Items", COM_BARRA, {"X": 1}, chave_texto=True))
    assert "Item nao existe" in str(erro.value)
    assert erro.value.status_code == 400
