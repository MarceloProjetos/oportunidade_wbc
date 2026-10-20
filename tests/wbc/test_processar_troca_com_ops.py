"""Execution of the partner swap with planned OPs (PLANO_TROCA_PN_COM_OP, F2).

The order is cancelled only after every planned OP is cancelled and a fresh read shows
none alive. Anything else stops the swap for this cycle and leaves the order alone.
"""

from __future__ import annotations

import inspect
import logging
from decimal import Decimal

from tests.wbc.test_processar import (
    DocumentosFalso,
    OportunidadesFalso,
    OrcDetalheFalso,
    WbcFalso,
    _oportunidade,
    _orcamento,
    _processador,
    tracking,  # noqa: F401 - pytest fixture
)
from wbcpython.domain.sitcode import Acao
from wbcpython.infrastructure.service_layer.documentos import TipoDocumento
from wbcpython.infrastructure.service_layer.ordens_producao import OpViva
from wbcpython.infrastructure.wbc_sql.models import ItemOrcamentoWbc
from wbcpython.tracking import StatusIntegracao

P, R = "boposPlanned", "boposReleased"


class OpsFalsas:
    """`vivas()` returns the next read in line (the last one repeats)."""

    def __init__(self, *leituras: list[OpViva], falha_em: int | None = None) -> None:
        self._leituras = list(leituras)
        self._falha_em = falha_em
        self.canceladas: list[int] = []
        self.leituras_feitas = 0

    def vivas(self, pedido_doc_entry: int) -> list[OpViva]:
        self.leituras_feitas += 1
        if len(self._leituras) > 1:
            return self._leituras.pop(0)
        return self._leituras[0]

    def cancelar(self, op_doc_entry: int) -> None:
        if op_doc_entry == self._falha_em:
            raise RuntimeError("HTTP 500 do Service Layer")
        self.canceladas.append(op_doc_entry)


class ParceiroInexistente:
    def existe(self, card_code: str) -> bool:
        return False


class PedidoQueOSapNaoCancela(DocumentosFalso):
    def cancelar_e_recriar(self, tipo, orcamento, dados):
        raise RuntimeError("SAP -5002: documento tem documento-destino")


def _ops(*status: str, inicio: int = 160867) -> list[OpViva]:
    return [OpViva(inicio + i, 159019 + i, s) for i, s in enumerate(status)]


def _docs(cls=DocumentosFalso, planejadas=2, em_producao=0):
    return cls(
        tem_cotacao=True,
        tem_pedido=True,
        parceiros={TipoDocumento.PEDIDO: "C011414"},
        ops=(planejadas, em_producao),
    )


def _rodar(tracking, ops, *, ocupado=False, docs=None, orcamento=None, **kw):  # noqa: F811
    docs = docs or _docs()
    resultado = _processador(
        tracking,
        WbcFalso(orcamento or _orcamento(sitcode=60)),
        docs,
        OportunidadesFalso(),
        OrcDetalheFalso(),
        ordens_producao=ops,
        controle_producao_ocupado=lambda: ocupado,
        **kw,
    ).processar(_oportunidade(U_INO_PN_Correc="C012060", CardCode="C011414", U_INO_Update="Y"))
    return resultado, docs


def _recriou(docs) -> bool:
    return any(c[0] == "cancelar_e_recriar" for c in docs.chamadas)


def _mensagens(tracking, regra: str | None = None) -> list[str]:  # noqa: F811
    return [
        e.mensagem for e in tracking.eventos("00123316") if regra is None or e.regra == regra
    ]


def _status(tracking):  # noqa: F811
    return StatusIntegracao(tracking.obter("00123316").status)


class TestCancelaAsOpsAntesDoPedido:
    def test_cancela_todas_e_depois_recria_o_pedido(self, tracking) -> None:  # noqa: F811
        ops = OpsFalsas(_ops(P, P), [])
        resultado, docs = _rodar(tracking, ops)

        assert ops.canceladas == [160867, 160868]
        assert _recriou(docs)
        assert Acao.CANCELAR_OPS_DO_PEDIDO in resultado.acoes_executadas
        assert "159019, 159020" in _mensagens(tracking, "troca_de_pn_cancelou_ops")[0]


class TestAdiaSemTocarNoPedido:
    def test_controle_de_producao_ocupado(self, tracking) -> None:  # noqa: F811
        ops = OpsFalsas(_ops(P, P), [])
        resultado, docs = _rodar(tracking, ops, ocupado=True)

        assert ops.canceladas == [] and ops.leituras_feitas == 0
        assert not _recriou(docs)
        assert _mensagens(tracking, "troca_de_pn_adiada")
        assert resultado.acoes_executadas == ()
        # Nothing written: no empty "Ações executadas" event every 3 minutes.
        assert not any(m.startswith("Ações executadas") for m in _mensagens(tracking))

    def test_nao_sabe_se_esta_ocupado_fica_em_vermelho(self, tracking) -> None:  # noqa: F811
        ops = OpsFalsas(_ops(P), [])
        _, docs = _rodar(tracking, ops, ocupado=None)

        assert ops.canceladas == []
        assert not _recriou(docs)
        assert _status(tracking) is StatusIntegracao.ERRO

    def test_op_liberada_na_releitura_nao_cancela_nada(self, tracking, caplog) -> None:  # noqa: F811
        """The decision counted only planned OPs; production released one meanwhile."""
        ops = OpsFalsas(_ops(P, R))
        with caplog.at_level(logging.ERROR):
            _, docs = _rodar(tracking, ops)

        assert ops.canceladas == []
        assert not _recriou(docs)
        assert _status(tracking) is StatusIntegracao.ERRO
        assert any("liberada(s) ou encerrada(s)" in r.getMessage() for r in caplog.records)

    def test_falha_numa_op_guarda_os_numeros_das_ja_canceladas(self, tracking) -> None:  # noqa: F811
        ops = OpsFalsas(_ops(P, P, P), falha_em=160869)
        _, docs = _rodar(tracking, ops)

        assert ops.canceladas == [160867, 160868]
        assert not _recriou(docs)
        mensagens = " ".join(_mensagens(tracking))
        assert "falha ao cancelar a OP 159021" in mensagens
        assert "159019, 159020" in mensagens

    def test_op_que_sobra_na_releitura_segura_o_pedido(self, tracking) -> None:  # noqa: F811
        """An OP created between the read and the cancellations must not be orphaned."""
        ops = OpsFalsas(_ops(P), _ops(P, inicio=160999))
        _, docs = _rodar(tracking, ops)

        assert ops.canceladas == [160867]
        assert not _recriou(docs)
        assert "159019" in " ".join(_mensagens(tracking, "troca_de_pn_cancelou_ops"))

    def test_sem_acesso_as_ops(self, tracking) -> None:  # noqa: F811
        _, docs = _rodar(tracking, None)

        assert not _recriou(docs)


class TestConferenciasDoPedidoVemAntesDasOps:
    """Review of the F2: guards found after the OPs left the order alive with none."""

    def test_parceiro_inexistente_nao_cancela_op(self, tracking) -> None:  # noqa: F811
        ops = OpsFalsas(_ops(P, P), [])
        _, docs = _rodar(tracking, ops, parceiros=ParceiroInexistente())

        assert ops.canceladas == []
        assert not _recriou(docs)

    def test_pedido_sem_valor_nao_cancela_op(self, tracking) -> None:  # noqa: F811
        ops = OpsFalsas(_ops(P, P), [])
        sem_valor = _orcamento(
            sitcode=60,
            itens=(ItemOrcamentoWbc(orcitm=1, produto="P", valor=Decimal(0)),),
        )
        _, docs = _rodar(tracking, ops, orcamento=sem_valor)

        assert ops.canceladas == []
        assert not _recriou(docs)

    def test_sap_recusa_cancelar_o_pedido_depois_das_ops(self, tracking, caplog) -> None:  # noqa: F811
        """No cheap pre-check for this one: the PCP must get the list of OPs cancelled."""
        ops = OpsFalsas(_ops(P, P), [])
        with caplog.at_level(logging.ERROR):
            resultado, _ = _rodar(tracking, ops, docs=_docs(PedidoQueOSapNaoCancela))

        assert ops.canceladas == [160867, 160868]
        assert not resultado.sucesso
        aviso = " ".join(_mensagens(tracking, "troca_de_pn_pedido_nao_cancelado"))
        assert "159019, 159020" in aviso and "avise o PCP" in aviso


class TestTrocaSemOpTambemConfere:
    def test_op_criada_depois_da_leitura_segura_o_pedido(self, tracking) -> None:  # noqa: F811
        """The decision saw zero OPs at the start of the cycle; the CP created one since."""
        ops = OpsFalsas(_ops(P))
        _, docs = _rodar(tracking, ops, docs=_docs(planejadas=0))

        assert ops.canceladas == []
        assert not _recriou(docs)

    def test_controle_de_producao_ocupado_segura_a_troca_sem_op(self, tracking) -> None:  # noqa: F811
        _, docs = _rodar(tracking, OpsFalsas([]), ocupado=True, docs=_docs(planejadas=0))

        assert not _recriou(docs)


class TestDecisaoQuePedeGente:
    def test_op_em_producao_vai_para_o_log_em_vermelho(self, tracking, caplog) -> None:  # noqa: F811
        ops = OpsFalsas(_ops(R))
        with caplog.at_level(logging.ERROR):
            _, docs = _rodar(tracking, ops, docs=_docs(planejadas=10, em_producao=3))

        assert not _recriou(docs)
        assert ops.leituras_feitas == 0  # decided without action: nothing read, nothing written
        erros = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert any("3 OP(s) liberada(s) ou encerrada(s)" in r.getMessage() for r in erros)


def test_worker_liga_as_ops_e_o_controle_de_producao() -> None:
    """Without them the swap of an order with OPs would wait forever (safe, but stuck)."""
    from wbcpython.host import worker

    fonte = inspect.getsource(worker)
    assert "ordens_producao=RepositorioOrdensProducaoServiceLayer(cliente)" in fonte
    assert "controle_producao_ocupado(" in fonte
