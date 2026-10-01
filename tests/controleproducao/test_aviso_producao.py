"""Aviso de ambiente (22/09/2026) — substitui `test_trava_producao.py`.

A trava de escrita em produção (duas chaves: `WBC_BLOCK_PRODUCTION_WRITES` no `.env` +
`--producao` por execução, e na web a digitação da company DB) foi **removida a pedido do
Anderson**. O que estes testes garantem agora é o que sobrou:

- produção é **anunciada**, em log e na tela, por toda operação de escrita;
- homologação **não** ganha ruído — aviso que aparece sempre deixa de ser lido;
- since 28/09/2026 (package inside the SIS) production writes are refused anywhere but the
  production machine (`wbcpython.safety`, IP rule — the conftest pins the IP to a
  never-local address, and the `como_a_11` fixture plays the .11); no `.env` switch;
- a conferência de operação irreversível continua valendo, porque ela nunca foi a trava.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from controleproducao.cli import app
from controleproducao.core.guardas import ambiente_descrito, aviso_de_escrita


def _settings(producao: bool):
    """`SimpleNamespace` de propósito: um MagicMock daria `is_production` verdadeiro por
    acidente, que é exatamente o erro que estes testes precisam pegar."""
    return SimpleNamespace(
        is_production=producao,
        sl_company_db="SBOALTAMIRAPROD" if producao else "SBOALTAMIRAHOMOLOG",
    )


# ---------------------------------------------------------------------------
# A regra
# ---------------------------------------------------------------------------
def test_homologacao_nao_ganha_aviso():
    """É onde se trabalha o dia inteiro; ruído constante vira ruído ignorado."""
    assert aviso_de_escrita(_settings(producao=False), "teste") is None


def test_producao_e_anunciada_com_o_alvo(como_a_11):
    aviso = aviso_de_escrita(_settings(producao=True), "manutencao-op encerrar")
    assert "PRODUÇÃO" in aviso
    assert "SBOALTAMIRAPROD" in aviso          # o alvo, para não restar dúvida de onde grava
    assert "manutencao-op encerrar" in aviso   # e a operação, para o log servir de rastro


def test_o_aviso_nunca_leva_credencial():
    """Ele vai para log e para tela; senha ali seria um vazamento permanente."""
    descrito = ambiente_descrito(_settings(producao=True))
    assert "SBOALTAMIRAPROD" in descrito
    for proibido in ("password", "senha", "sl_password", "user"):
        assert proibido not in descrito.lower()


def test_homologacao_nunca_e_recusada():
    """The IP gate is about PRODUCTION: homologação writes from any machine (the retest
    runs from a dev box)."""
    assert aviso_de_escrita(_settings(producao=False), "teste") is None


def test_producao_fora_da_11_e_recusada_antes_de_qualquer_rede():
    """28/09/2026: the `.env` switch of 21/09 came back as the machine's IP. The conftest
    pins PRODUCTION_MACHINE_IP to a never-local address, so here we are "another machine"."""
    from controleproducao.core.guardas import ProductionWriteBlocked

    with pytest.raises(ProductionWriteBlocked) as recusa:
        aviso_de_escrita(_settings(producao=True), "manutencao-op encerrar")
    assert "manutencao-op encerrar" in str(recusa.value)
    assert "192.0.2.1" in str(recusa.value)  # names the machine that may write


def test_na_11_producao_e_so_anunciada(como_a_11):
    aviso = aviso_de_escrita(_settings(producao=True), "teste")
    assert aviso and "PRODUÇÃO" in aviso


# ---------------------------------------------------------------------------
# A CLI não pede mais flag nenhuma
# ---------------------------------------------------------------------------
def test_nenhum_comando_expoe_mais_producao():
    """`--producao` saiu de todos os comandos; deixá-lo em um só seria pior que nada."""
    comandos = [
        ["pedidos-wbc", "processar-novos"], ["pedidos-wbc", "reprocessar-integrados"],
        ["pedidos-wbc", "cancelar-ops"], ["manutencao-op", "liberar"],
        ["manutencao-op", "replanejar"], ["manutencao-op", "encerrar"],
        ["diag", "patch-parcial"],
    ]
    for comando in comandos:
        saida = CliRunner().invoke(app, comando + ["--help"]).output
        assert "--producao" not in saida, f"{' '.join(comando)} ainda expõe --producao"


def test_sim_pula_a_confirmacao_tambem_em_producao():
    """Antes, em produção, `--sim` exigia digitar a company DB. Saiu junto com a trava —
    e a consequência (um script com `--sim` não encontra mais barreira) é conhecida."""
    from controleproducao import cli

    with patch.object(cli, "typer") as typer_falso:
        cli._confirma("Confirma?", confirmar=True, em_producao=True)
    typer_falso.confirm.assert_not_called()


def test_em_producao_a_pergunta_vem_destacada():
    from controleproducao import cli

    with patch.object(cli.console, "print") as imprime, \
         patch.object(cli.typer, "confirm"):
        cli._confirma("Vai gravar 68 OPs.", confirmar=False, em_producao=True)
    assert any("bold red" in str(c) for c in imprime.call_args_list)


# ---------------------------------------------------------------------------
# A web avisa, e não barra
# ---------------------------------------------------------------------------
def test_rota_de_escrita_em_producao_registra_o_aviso_e_prossegue(monkeypatch, como_a_11):
    """On the .11, with a key configured: the write goes on and the log keeps the trace."""
    import controleproducao.core.web as web
    from controleproducao.config import get_settings

    monkeypatch.setenv("OS_API_KEY", "chave")
    get_settings.cache_clear()
    with patch.object(web, "get_settings", return_value=_settings(producao=True)), \
         patch.object(web.logger, "warning") as registrou:
        aviso = web.avisa_escrita("teste")

    assert "PRODUÇÃO" in aviso
    registrou.assert_called_once()   # o log é o que sobra para auditoria depois


def test_rota_de_escrita_sem_chave_e_503(monkeypatch):
    """Fail-closed: no OS_API_KEY → the screen is read-only, whatever the environment."""
    from fastapi import HTTPException

    import controleproducao.core.web as web

    with patch.object(web, "get_settings", return_value=_settings(producao=False)), \
         pytest.raises(HTTPException) as recusa:
        web.avisa_escrita("teste")
    assert recusa.value.status_code == 503 and "OS_API_KEY" in recusa.value.detail


def test_rota_de_escrita_em_producao_fora_da_11_e_503(monkeypatch):
    from fastapi import HTTPException

    import controleproducao.core.web as web
    from controleproducao.config import get_settings

    monkeypatch.setenv("OS_API_KEY", "chave")
    get_settings.cache_clear()
    with patch.object(web, "get_settings", return_value=_settings(producao=True)), \
         pytest.raises(HTTPException) as recusa:
        web.avisa_escrita("teste")
    assert recusa.value.status_code == 503 and "máquina de produção" in recusa.value.detail


def test_conferencia_de_operacao_irreversivel_continua_valendo_em_homologacao(monkeypatch):
    """Ela nunca foi a trava de produção: o motivo é a operação não ter volta.

    Sem token, nada chega ao serviço — em qualquer ambiente. The key is configured: since
    01/10/2026 the write gate runs before the token (a refused write must not spend it).
    """
    from fastapi.testclient import TestClient

    import controleproducao.core.web as web
    from controleproducao.config import get_settings
    from controleproducao.main import app as aplicacao

    monkeypatch.setenv("OS_API_KEY", "chave")
    get_settings.cache_clear()
    with patch.object(web, "get_settings", return_value=_settings(producao=False)), \
         patch("controleproducao.modules.pedidos_wbc.service.processar_pedidos_novos",
               AsyncMock()) as servico:
        resposta = TestClient(aplicacao).post("/pedidos-wbc/processar/executar",
                                              data={"token": ""}, headers={"X-API-Key": "chave"})

    assert resposta.status_code == 400
    servico.assert_not_called()
