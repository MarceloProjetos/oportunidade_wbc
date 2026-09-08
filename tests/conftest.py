"""Fixtures compartilhadas para pytest (suíte do ServidorIntegracaoSAP + suíte do WBC).

Os testes do pacote ``wbcpython`` vivem em ``tests/wbc/``. Os marcados com
``@pytest.mark.integration`` acessam sistemas reais (Service Layer, SQL Server do WBC,
HANA) e por isso exigem ``.env`` preenchido e rede interna da Altamira — só rodam com
``--run-integration``. A opção precisa ser registrada AQUI (conftest da raiz de
``tests/``): pytest ignora ``pytest_addoption`` em conftest aninhado.
"""

import pytest

from config import reset_settings


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="Executa também os testes de integração do WBC (exigem .env e rede interna).",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--run-integration"):
        return
    skip = pytest.mark.skip(reason="teste de integração: use --run-integration para executar")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _reset_config_cache():
    """Garante que cada teste relê o ambiente do zero."""
    reset_settings()
    yield
    reset_settings()
