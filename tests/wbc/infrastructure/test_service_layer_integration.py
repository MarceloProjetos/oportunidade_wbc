"""Smoke test de integração contra o SAP Business One real.

Este arquivo é o **primeiro contato real com o SAP** previsto na Fase 1.
Ele NÃO roda por padrão. Para executar:

    python -m pytest --run-integration -k service_layer_integration -v

Requisitos:
  * `.env` preenchido com as credenciais de SBOALTAMIRAHOMOLOG;
  * máquina dentro da rede interna da Altamira.

Todos os testes aqui são de LEITURA. Nenhum deles cria, altera ou apaga
qualquer registro no SAP.
"""

from __future__ import annotations

import pytest

from wbcpython.config import get_settings
from wbcpython.infrastructure.service_layer import ServiceLayerClient

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def cliente() -> ServiceLayerClient:
    settings = get_settings()

    if settings.targets_production:
        pytest.fail(
            "Os testes de integração não devem rodar apontados para produção. "
            f"Ajuste SL_COMPANY_DB no .env (atual: {settings.service_layer.company_db})."
        )

    sl = ServiceLayerClient(
        settings.service_layer,
        production_company_db=settings.production_company_db,
        block_production_writes=settings.block_production_writes,
    )
    try:
        yield sl
    finally:
        sl.close()


def test_login_funciona(cliente: ServiceLayerClient) -> None:
    """Valida credenciais, endereço, porta e certificado de uma só vez."""
    cliente.login()
    assert cliente.autenticado is True


def test_le_o_udo_orcdetalhe(cliente: ServiceLayerClient) -> None:
    """Confirma que o UDO OrcDetalhe está exposto e acessível.

    Esta é a verificação que de-risca o projeto: se ela passar, o caminho
    Python → Service Layer → UDO está comprovado neste ambiente.
    """
    registros = cliente.listar("OrcDetalhe", ordenar_por="DocEntry desc", top=1)
    assert isinstance(registros, list)

    if not registros:
        pytest.skip("Nenhum registro em OrcDetalhe neste ambiente — nada a validar.")

    registro = registros[0]
    assert "DocEntry" in registro
    assert registro.get("Object") == "OrcDetalhe"


def test_traz_a_colecao_de_linhas_aninhada(cliente: ServiceLayerClient) -> None:
    """A tabela filha INO_ORC_LINHA deve vir aninhada no documento."""
    registros = cliente.listar("OrcDetalhe", ordenar_por="DocEntry desc", top=1)
    if not registros:
        pytest.skip("Nenhum registro em OrcDetalhe neste ambiente.")

    documento = cliente.get_json(f"OrcDetalhe({registros[0]['DocEntry']})")
    assert "INO_ORC_LINHACollection" in documento, (
        "A coleção de linhas não veio na resposta. Verifique se o UDO está "
        "registrado com a tabela filha INO_ORC_LINHA (ver ai_spec/02_data_model.md)."
    )


def test_filtro_por_codigo_de_orcamento(cliente: ServiceLayerClient) -> None:
    """Valida a sintaxe de `$filter` sobre um UDF — usada em toda a integração."""
    registros = cliente.listar("OrcDetalhe", ordenar_por="DocEntry desc", top=1)
    if not registros:
        pytest.skip("Nenhum registro em OrcDetalhe neste ambiente.")

    codigo = registros[0].get("U_INO_COD")
    if not codigo:
        pytest.skip("Registro mais recente sem U_INO_COD preenchido.")

    encontrados = cliente.listar("OrcDetalhe", filtro=f"U_INO_COD eq '{codigo}'")
    assert encontrados, f"O filtro por U_INO_COD='{codigo}' não retornou nada."
    assert all(r.get("U_INO_COD") == codigo for r in encontrados)


def test_comportamento_de_historico_do_orcdetalhe(cliente: ServiceLayerClient) -> None:
    """Confirma o achado de que OrcDetalhe é um histórico (append-only).

    Espera-se encontrar o mesmo U_INO_COD em mais de um DocEntry. Se isso
    NÃO ocorrer neste ambiente, o teste não falha — apenas avisa, porque
    homologação pode simplesmente ter poucos dados.
    """
    registros = cliente.listar("OrcDetalhe", ordenar_por="DocEntry desc", top=20)
    if len(registros) < 2:
        pytest.skip("Poucos registros em OrcDetalhe para avaliar o comportamento.")

    codigos = [r.get("U_INO_COD") for r in registros if r.get("U_INO_COD")]
    if len(codigos) == len(set(codigos)):
        pytest.skip(
            "Nenhum U_INO_COD repetido entre os 20 registros mais recentes. "
            "Não contradiz o achado de histórico — apenas não o confirma aqui."
        )

    assert len(codigos) > len(set(codigos))
