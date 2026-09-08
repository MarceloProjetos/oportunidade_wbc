"""Fixtures compartilhadas pelos testes de cotação, pedido e motor de linhas."""

from __future__ import annotations

from decimal import Decimal

from wbcpython.infrastructure.service_layer.grupo_produtos import GrupoDeProduto
from wbcpython.infrastructure.wbc_sql.models import (
    DadosImpressaoWbc,
    ItemOrcamentoWbc,
    OrcamentoWbc,
)

DE_PARA = {
    "1": GrupoDeProduto("1", "Estantes", "I000002"),
    "2": GrupoDeProduto("2", "Porta-Paletes", "I000003"),
    "8": GrupoDeProduto("8", "Mezanino", "I000007"),
}


def item(**campos) -> ItemOrcamentoWbc:
    base = {
        "orcitm": 1,
        "grupo": 2,
        "texto": "PORTA-PALETES 16 Módulos",
        "valor": Decimal("41810.91"),
        "id_integracao": 126979,
    }
    base.update(campos)
    return ItemOrcamentoWbc(**base)


def orcamento(*itens: ItemOrcamentoWbc, **campos) -> OrcamentoWbc:
    base = {"orcnum": "00125535", "revisao": "A", "sitcode": 40, "itens": itens}
    base.update(campos)
    return OrcamentoWbc(**base)


def impressao(**campos) -> DadosImpressaoWbc:
    base = {
        "contato": "MARIA",
        "pagamento_codigo": "30 DIAS|60 DIAS;90 DIAS",
        "prazo_entrega": 42,
        "valor_transporte": Decimal("113026.88"),
        "valor_embalagem": Decimal(0),
        "valor_montagem": Decimal("2500.50"),
        "montagem_tipo": "CIF",
        "montagem": "Montagem inclusa.",
        "base3": Decimal("7000.25"),
        "percentual_comissao": Decimal("3.5"),
        "valor_comissao": Decimal(1200),
        "acabamento": "Cinza Padrão Altamira",
    }
    base.update(campos)
    return DadosImpressaoWbc(**base)
