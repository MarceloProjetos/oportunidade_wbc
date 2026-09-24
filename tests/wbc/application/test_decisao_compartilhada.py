"""Prévia (`wbcpython pendentes`) e ciclo decidem pelo MESMO caminho.

Até 24/09/2026 a CLI remontava a decisão de dois passos por conta própria (importando o
`_OrcamentoResumido` privado do processador) e usava outra porta para carregar o orçamento
inteiro: `ACOES_QUE_ESCREVEM` em vez de `tem_acao`. Davam no mesmo só porque os dois
conjuntos coincidiam. Estes testes cravam as duas pontas.
"""

from __future__ import annotations

import inspect

from wbcpython import cli
from wbcpython.application import processar
from wbcpython.application.previsao import ACOES_DE_DOCUMENTO, ACOES_QUE_ESCREVEM
from wbcpython.domain.sitcode import Acao, Decisao


def test_toda_acao_do_dominio_conta_como_escrita_na_previa():
    """Ação nova fora de `ACOES_QUE_ESCREVEM` = a prévia diria "sem escrita" sobre ela."""
    faltando = {a.value for a in Acao} - ACOES_QUE_ESCREVEM
    assert not faltando, f"acrescente em previsao.ACOES_QUE_ESCREVEM: {sorted(faltando)}"


def test_acoes_de_documento_sao_acoes_do_dominio():
    assert ACOES_DE_DOCUMENTO <= {a.value for a in Acao}


def test_a_porta_e_ter_acao():
    assert processar.precisa_do_orcamento(Decisao(regra="x", acoes=(Acao.CRIAR_COTACAO,)))
    assert not processar.precisa_do_orcamento(Decisao(regra="x"))


def test_a_previa_usa_as_funcoes_do_processador():
    fonte = inspect.getsource(cli._cmd_pendentes)
    for nome in ("decidir_pela_situacao", "precisa_do_orcamento", "decidir_pelo_orcamento"):
        assert nome in fonte
    assert "_OrcamentoResumido" not in fonte
    assert "montar_estado(" not in fonte
