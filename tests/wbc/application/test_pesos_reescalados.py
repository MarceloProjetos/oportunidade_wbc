"""Applying the rescaled-weight rule: simulation, one guarded PATCH per order, never twice."""

from __future__ import annotations

import logging
from contextlib import nullcontext
from datetime import datetime, timedelta

import pytest

from wbcpython.application.pesos_reescalados import Memoria, conferir
from wbcpython.domain.peso_reescalado import LinhaAtual, Versao
from wbcpython.infrastructure.hana.pesos_reescalados import LinhaDoPedido
from wbcpython.safety import ProductionWriteBlocked
from wbcpython.tracking import TipoEvento

AGORA = datetime(2026, 10, 5, 10, 0)


def _linha(line_num, antes, depois, *, qtd=(1, 30), total=4127.4, quem="Pessoa de Vendas", minutos=60):
    versoes = (
        Versao(1, qtd[0], antes, total, True, "orcaview", AGORA - timedelta(days=1)),
        Versao(2, qtd[1], depois, total, quem == "orcaview", quem, AGORA - timedelta(minutes=minutos)),
    )
    return LinhaDoPedido(20317, 84457, "00125299", line_num, "I000003",
                         LinhaAtual(qtd[1], depois, total), versoes)


REESCALADAS = [_linha(0, 148.94, 4468.2), _linha(1, 398.11, 19905.5, qtd=(1, 50), total=6906.5)]
NO_SAP = {0: (30.0, 4468.2), 1: (50.0, 19905.5)}


class DocumentosFalso:
    def __init__(self, *, erro: Exception | None = None, no_sap=None) -> None:
        self.gravados: list[tuple[int, dict, str | None]] = []
        self.erro = erro
        self.no_sap = NO_SAP if no_sap is None else no_sap

    def estado_das_linhas(self, tipo, doc_entry):
        return 'W/"v1"', self.no_sap

    def atualizar_pesos(self, tipo, doc_entry, pesos, *, etag=None):
        if self.erro:
            raise self.erro
        self.gravados.append((doc_entry, pesos, etag))


class TrackingFalso:
    def __init__(self) -> None:
        self.eventos: list[tuple[str, TipoEvento, str]] = []

    def registrar_evento(self, orcnum, *, tipo, regra, mensagem):
        self.eventos.append((orcnum, tipo, mensagem))


def test_grava_as_linhas_do_pedido_num_patch_so_com_if_match(caplog):
    docs, tracking = DocumentosFalso(), TrackingFalso()
    with caplog.at_level(logging.INFO):
        resumo = conferir(REESCALADAS, agora=AGORA, gravar=lambda: nullcontext(docs), tracking=tracking)
    assert docs.gravados == [(20317, {0: 148.94, 1: 398.11}, 'W/"v1"')]
    assert resumo.corrigidas == 2
    assert "Pedido 84457 linha 0: peso restaurado para 148,94 kg. Pessoa de Vendas mudou" in caplog.text
    assert [(o, t) for o, t, _ in tracking.eventos] == [("00125299", TipoEvento.ACAO)] * 2


def test_linha_que_mudou_desde_a_leitura_nao_e_gravada():
    """A weight typed between the HANA read and the PATCH is never overwritten."""
    docs = DocumentosFalso(no_sap={0: (30.0, 148.0), 1: (50.0, 19905.5)})
    resumo = conferir(REESCALADAS, agora=AGORA, gravar=lambda: nullcontext(docs))
    assert docs.gravados == [(20317, {1: 398.11}, 'W/"v1"')] and resumo.corrigidas == 1


def test_nunca_grava_a_mesma_reescala_duas_vezes(caplog):
    """If the SAP did not keep the weight, the next cycle warns instead of PATCHing forever."""
    docs, memoria = DocumentosFalso(), Memoria()
    conferir(REESCALADAS, agora=AGORA, gravar=lambda: nullcontext(docs), memoria=memoria)
    resumo = conferir(REESCALADAS, agora=AGORA, gravar=lambda: nullcontext(docs), memoria=memoria)
    assert len(docs.gravados) == 1 and resumo.avisos == 2
    assert "peso restaurado antes e o SAP não guardou" in caplog.text


def test_simulacao_nao_grava_e_relata_cada_linha_uma_vez(caplog):
    memoria = Memoria()
    with caplog.at_level(logging.INFO):
        for _ in range(2):
            resumo = conferir(REESCALADAS, agora=AGORA, memoria=memoria)
    assert resumo.simuladas == 2
    assert caplog.text.count("SIMULAÇÃO: voltaria a 148,94 kg") == 1


def test_falha_tenta_de_novo_e_avisa_uma_vez(caplog):
    memoria = Memoria()
    docs = DocumentosFalso(erro=RuntimeError("412 Precondition Failed"))
    for _ in range(2):
        resumo = conferir(REESCALADAS, agora=AGORA, gravar=lambda: nullcontext(docs), memoria=memoria)
    assert resumo.falhas == 1 and resumo.corrigidas == 0 and memoria.gravadas == set()
    assert caplog.text.count("não consegui restaurar o peso (412 Precondition Failed)") == 1


def test_trava_de_producao_nunca_e_engolida():
    bloqueado = DocumentosFalso(erro=ProductionWriteBlocked("x"))
    with pytest.raises(ProductionWriteBlocked):
        conferir(REESCALADAS, agora=AGORA, gravar=lambda: nullcontext(bloqueado))


def test_parada_solicitada_nao_comeca_o_patch():
    docs = DocumentosFalso()
    conferir(REESCALADAS, agora=AGORA, gravar=lambda: nullcontext(docs), parar=lambda: True)
    assert docs.gravados == []


def test_aviso_sai_uma_vez_e_nada_e_gravado(caplog):
    venda_mudou = LinhaDoPedido(
        1, 84438, "00125000", 0, "I000003", LinhaAtual(1, 57.5, 3389.83),
        (Versao(1, 10, 575, 7476.0, True, "orcaview", AGORA - timedelta(days=9)),
         Versao(2, 1, 57.5, 3389.83, False, "Pessoa de Vendas", AGORA - timedelta(days=9))),
    )
    docs, memoria = DocumentosFalso(), Memoria()
    for _ in range(2):
        resumo = conferir([venda_mudou], agora=AGORA, gravar=lambda: nullcontext(docs), memoria=memoria)
    assert docs.gravados == [] and resumo.avisos == 1
    assert [r.levelno for r in caplog.records].count(logging.WARNING) == 1
    assert "peso NÃO restaurado: o total da linha mudou -54,7%" in caplog.text


def test_troca_anterior_ao_inicio_nao_e_tocada():
    docs = DocumentosFalso()
    resumo = conferir(
        REESCALADAS, agora=AGORA, gravar=lambda: nullcontext(docs), a_partir_de=AGORA - timedelta(minutes=5)
    )
    assert docs.gravados == [] and resumo.corrigidas == 0


def test_sem_linha_a_gravar_nem_abre_a_escrita():
    """Lock + Service Layer session only when some line is to be written (review, 05/10/2026)."""
    abertas: list[bool] = []

    def gravar():
        abertas.append(True)
        return nullcontext(DocumentosFalso())

    conferir([], agora=AGORA, gravar=gravar)
    conferir(REESCALADAS, agora=AGORA, gravar=gravar, a_partir_de=AGORA)
    assert abertas == []
