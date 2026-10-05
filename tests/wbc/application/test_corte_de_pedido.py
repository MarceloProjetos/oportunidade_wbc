"""`montar_estado` marca a oportunidade que está fora da janela padrão.

A regra em si é do domínio (`tests/wbc/domain/test_sem_pedido_fora_da_janela`).
O que se testa aqui é o que a liga: quem lê o `OpenDate` da oportunidade e o
compara com o corte. Errar este lado deixa a regra escrita e nunca aplicada.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from wbcpython.application.processar import montar_estado
from wbcpython.infrastructure.service_layer.documentos import TipoDocumento

CORTE = date(2026, 3, 1)


class _SemDocumentos:
    """O mínimo que `montar_estado` pede — nada aqui depende de documento."""

    def existe(self, _tipo: TipoDocumento, _orcamento: str) -> bool:
        return False

    def esta_fechado(self, _tipo: TipoDocumento, _orcamento: str) -> bool:
        return False

    def revisao_aplicada(self, _tipo: TipoDocumento, _orcamento: str) -> str:
        return ""

    def parceiro_aplicado(self, _tipo: TipoDocumento, _orcamento: str) -> str:
        return ""


class _Orcamento:
    orcnum = "00122598"
    sitcode = 60
    revisao = "A"


def _estado(abertura: Any, corte: date | None = CORTE):
    return montar_estado(
        _Orcamento(),
        {"OpenDate": abertura},
        _SemDocumentos(),
        corte_de_pedido=corte,
    )


def test_aberta_antes_do_corte_fica_fora() -> None:
    assert _estado(datetime(2025, 11, 20)).fora_da_janela_padrao is True


def test_aberta_no_dia_do_corte_fica_dentro() -> None:
    """O corte é o primeiro dia da janela, e é inclusivo — é o mesmo `>=` que
    a consulta do HANA usa para ler a janela."""
    assert _estado(datetime(2026, 3, 1)).fora_da_janela_padrao is False


def test_aberta_depois_do_corte_fica_dentro() -> None:
    assert _estado(datetime(2026, 8, 10)).fora_da_janela_padrao is False


def test_sem_corte_a_regra_nao_existe() -> None:
    """`None` é o ciclo que não pediu a regra — um teste, ou um caso pontual."""
    assert _estado(datetime(2020, 1, 1), corte=None).fora_da_janela_padrao is False


def test_sem_data_de_abertura_nao_bloqueia() -> None:
    """Na dúvida, o comportamento é o de sempre.

    Uma data ausente virando bloqueio silencioso de pedido faria um ciclo
    NORMAL deixar de criar pedidos sem ninguém entender por quê.
    """
    assert _estado(None).fora_da_janela_padrao is False
