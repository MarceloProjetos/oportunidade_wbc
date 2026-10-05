"""Testes dos dois defeitos achados na primeira comparação real contra produção (21/09/2026).

Orçamento 00120634: 29 OPs contra 29, mas o item I000005 saiu sem a linha de rateio
(`GGF_00120634I0000050`). O `INO_LOG` mostrou o motivo:

    Erro ao preencher recurso: unsupported operand type(s) for /: 'float' and 'decimal.Decimal'

Duas causas independentes, ambas cobertas aqui:

1. `Decimal` do HANA entrando na aritmética do rateio — `TypeError` engolido pelo `except`,
   OP criada sem a linha. É a mesma classe do `Decimal is not JSON serializable` (7.8).
2. O código do Recurso era montado com `str(valor)`, que acrescenta `.0` em valores
   inteiros — nome diferente do que o legado gera, então o porte nunca reconhecia o
   recurso já existente.
"""
import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from controleproducao.modules.pedidos_wbc import service as svc


# ---------------------------------------------------------------------------
# _num — a conversão na fronteira
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "entrada,esperado",
    [
        (Decimal("3854.14"), 3854.14),
        (Decimal("0.00"), 0.0),
        (2, 2.0),
        (2.5, 2.5),
        ("1,50", 1.5),      # o WBC devolve alguns campos com vírgula decimal
        ("2.5", 2.5),
        (None, 0.0),
        ("", 0.0),
        ("abc", 0.0),       # não derruba o pedido por um campo sujo
    ],
)
def test_num_converte_o_que_os_drivers_devolvem(entrada, esperado):
    assert svc._num(entrada) == esperado


def test_num_respeita_o_padrao_informado():
    assert svc._num(None, padrao=-1.0) == -1.0


def test_decimal_dividido_por_float_era_o_erro_original():
    """Prova a premissa: sem a conversão, a conta do rateio levanta TypeError. Se algum dia
    o Python passar a permitir isso, este teste avisa que `_num` ficou desnecessário."""
    with pytest.raises(TypeError):
        _ = 2.0 / Decimal("10")

    assert (2.0 / svc._num(Decimal("10"))) == 0.2


# ---------------------------------------------------------------------------
# O nome do Recurso
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "valor,digitos",
    [
        (0, "0"),                  # legado: GGF_00120634I0000050
        (0.0, "0"),
        (Decimal("0.00"), "0"),
        (3854.14, "385414"),       # legado: GGF_00120634I000006385414
        (Decimal("3854.14"), "385414"),
        (1053.58, "105358"),
        (10, "10"),
    ],
)
def test_digitos_seguem_o_double_tostring_do_csharp(valor, digitos):
    """`double.ToString()` em pt-BR: inteiro sai sem parte decimal; o resto usa vírgula,
    que o legado remove. `str(0.0)` em Python daria "0.0" -> "00", nome errado."""
    assert svc._digitos_do_valor_como_no_legado(valor) == digitos


def test_nome_do_recurso_do_i000005_bate_com_o_do_legado():
    """O caso concreto: com valor_linha 0, o nome tem que fechar em um único zero, senão o
    porte não reencontra `GGF_00120634I0000050` e tenta criar um recurso paralelo."""
    nome = f"GGF_00120634I000005{svc._digitos_do_valor_como_no_legado(Decimal('0.00'))}"
    assert nome == "GGF_00120634I0000050"


# ---------------------------------------------------------------------------
# cria_recurso_rateio
# ---------------------------------------------------------------------------
def _leitor(existe: bool, code: str = "GGF_X"):
    leitor = MagicMock()
    respostas = [[{"c": 1 if existe else 0}]]
    if existe:
        respostas.append([{"ResCode": code}])
    leitor.fetch_all = MagicMock(side_effect=respostas)
    return leitor


def test_calcula_o_rateio_com_decimal_do_hana():
    """O teste de regressão do erro do INO_LOG: os três números chegando como Decimal."""
    sl = AsyncMock()
    sl.create_entity = AsyncMock(return_value={"Code": "GGF_NOVO", "VisCode": "GGF_NOVO"})

    codigo = asyncio.run(
        svc.cria_recurso_rateio(
            sl, _leitor(existe=False),
            custos_wbc=[Decimal("100"), Decimal("50"), Decimal("25")],
            nome_recurso="GGF_NOVO",
            quantidade_linha=Decimal("2"),
            valor_total=Decimal("10"),
            valor_linha=Decimal("5"),
            nome="00120634",
        )
    )

    assert codigo == "GGF_NOVO"
    corpo = sl.create_entity.await_args.args[1]
    assert corpo["Cost1"] == pytest.approx((2 * 100 / 10) * 5)
    assert corpo["Cost2"] == pytest.approx((2 * 50 / 10) * 5)
    assert corpo["Cost3"] == pytest.approx((2 * 25 / 10) * 5)


def test_reaproveita_recurso_existente_sem_criar_outro():
    """Com o nome agora igual ao do legado, este caminho passa a funcionar de verdade: o
    porte encontra o recurso que o addon já criou em vez de tentar um novo."""
    sl = AsyncMock()

    codigo = asyncio.run(
        svc.cria_recurso_rateio(
            sl, _leitor(existe=True, code="GGF_00120634I0000050"),
            custos_wbc=[1, 1, 1], nome_recurso="GGF_00120634I0000050",
            quantidade_linha=1, valor_total=1, valor_linha=1, nome="00120634",
        )
    )

    assert codigo == "GGF_00120634I0000050"
    sl.create_entity.assert_not_awaited()


def test_valor_total_zero_nao_estoura_divisao():
    """Antes daria ZeroDivisionError, que o `except` de quem chama engoliria igual ao
    TypeError — OP sem rateio, em silêncio."""
    sl = AsyncMock()

    codigo = asyncio.run(
        svc.cria_recurso_rateio(
            sl, _leitor(existe=False), custos_wbc=[1, 1, 1], nome_recurso="GGF_Z",
            quantidade_linha=1, valor_total=Decimal("0"), valor_linha=1, nome="00120634",
        )
    )

    assert codigo == ""
    sl.create_entity.assert_not_awaited()


def test_menos_um_continua_significando_sem_rateio():
    """Comportamento do C# preservado: `-1` nesses campos é "não há base", não um valor."""
    sl = AsyncMock()

    codigo = asyncio.run(
        svc.cria_recurso_rateio(
            sl, _leitor(existe=False), custos_wbc=[1, 1, 1], nome_recurso="GGF_Z",
            quantidade_linha=-1, valor_total=10, valor_linha=1, nome="00120634",
        )
    )

    assert codigo == ""
    sl.create_entity.assert_not_awaited()


def test_corpo_usa_os_nomes_reais_da_service_layer():
    """Nomes lidos de um recurso real em 23/09/2026 (`diag entidade Resources`). Os
    anteriores (`ResourceCode`, `Warehouses`, `DailyCapacities`) não existem na Service
    Layer e fizeram todo `POST /Resources` falhar — as OPs saíam sem a linha de rateio."""
    sl = AsyncMock()
    sl.create_entity = AsyncMock(return_value={"Code": "GGF_NOVO", "VisCode": "GGF_NOVO"})

    asyncio.run(
        svc.cria_recurso_rateio(
            sl, _leitor(existe=False), custos_wbc=[1, 1, 1], nome_recurso="GGF_NOVO",
            quantidade_linha=1, valor_total=10, valor_linha=5, nome="00125540",
        )
    )

    entidade, corpo = sl.create_entity.await_args.args[:2]
    assert entidade == "Resources"
    assert corpo["VisCode"] == "GGF_NOVO"
    assert "Code" not in corpo  # como no C#: o código interno fica com o B1
    assert corpo["ResourceWarehouses"] == [{"Warehouse": "01"}]
    assert corpo["ResourceDailyCapacities"] == [{"Weekday": "rdcwFirst", "Factor1": 1}]
    for nome_errado in ("ResourceCode", "ResourceName", "Warehouses", "DailyCapacities"):
        assert nome_errado not in corpo


def test_devolve_o_code_retornado_como_no_legado():
    sl = AsyncMock()
    sl.create_entity = AsyncMock(return_value={"Code": "12345", "VisCode": "GGF_NOVO"})
    codigo = asyncio.run(
        svc.cria_recurso_rateio(
            sl, _leitor(existe=False), custos_wbc=[1, 1, 1], nome_recurso="GGF_NOVO",
            quantidade_linha=1, valor_total=10, valor_linha=5, nome="00125540",
        )
    )
    assert codigo == "12345"
