"""Fixtures compartilhadas para pytest (suíte do ServidorIntegracaoSAP + suíte do WBC).

Os testes do pacote ``wbcpython`` vivem em ``tests/wbc/``. Os marcados com
``@pytest.mark.integration`` acessam sistemas reais (Service Layer, SQL Server do WBC,
HANA) e por isso exigem ``.env`` preenchido e rede interna da Altamira — só rodam com
``--run-integration``. A opção precisa ser registrada AQUI (conftest da raiz de
``tests/``): pytest ignora ``pytest_addoption`` em conftest aninhado.
"""

import importlib

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


#: Destinos reais que a suíte NUNCA pode alcançar. O ``config`` faz ``load_dotenv()`` no
#: import, e o ``.env`` desta máquina aponta para o Supabase e o SAP de PRODUÇÃO: um teste
#: que esquecesse um stub (``DELETE /historico`` faz ``.delete().neq('id', 0)``) falaria
#: com eles. Valor FALSO, não apagado: ``*_ready()`` continua verdadeiro e o código segue o
#: caminho normal até a rede — onde a porta 9 (discard) recusa na hora.
_DESTINO_FALSO = "127.0.0.1"
_AMBIENTE_FALSO = {
    "SUPABASE_URL": f"http://{_DESTINO_FALSO}:9",
    "SUPABASE_KEY": "pytest-falso",
    "SUPABASE_SERVICE_ROLE_KEY": "pytest-falso",
    "SAP_HOST": _DESTINO_FALSO,
    "SAP_PASSWORD": "pytest-falso",
    "SQL_HOST": _DESTINO_FALSO,
    "SQLSERVER_HOST": _DESTINO_FALSO,
    "SQL_PASSWORD": "pytest-falso",
    "SQLSERVER_PASSWORD": "pytest-falso",
    "OP_SL_SERVER": _DESTINO_FALSO,
    "OP_SL_PASSWORD": "pytest-falso",
    "OP_SL_ENABLED": "false",
}


#: Pontos onde uma conexão real nasce: ``(módulo, atributo)``. Só o endereço falso não
#: basta — o ``hdbcli`` 2.29.25 no Python 3.14 derruba o processo inteiro com access
#: violation quando a conexão falha (medido em 24/09/2026), e o pytest morria sem dizer
#: qual teste vazou. A trava falha o TESTE, com o nome do destino.
_CONEXOES_REAIS = (
    ("hdbcli.dbapi", "connect", "HANA"),
    ("pyodbc", "connect", "SQL Server (pyodbc)"),
    ("pymssql", "connect", "SQL Server do WBC (pymssql)"),
    ("supabase", "create_client", "Supabase"),
    ("pipeline_core", "create_client", "Supabase"),
)


def _trava(destino: str):
    def conectar(*_args, **_kwargs):
        # pytest.fail levanta BaseException: atravessa os `except Exception` do código.
        pytest.fail(f"teste tentou abrir conexão REAL com {destino}; faça o stub", pytrace=False)

    return conectar


@pytest.fixture(autouse=True)
def _ambiente_sem_producao(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    """Troca os destinos reais do ``.env`` por falsos e trava os drivers — exceto nos testes de integração."""
    if request.node.get_closest_marker("integration") is None:
        for nome, valor in _AMBIENTE_FALSO.items():
            monkeypatch.setenv(nome, valor)
        for modulo, atributo, destino in _CONEXOES_REAIS:
            try:
                alvo = importlib.import_module(modulo)
            except ImportError:
                continue
            monkeypatch.setattr(alvo, atributo, _trava(destino), raising=False)
    yield


@pytest.fixture(autouse=True)
def _reset_config_cache(_ambiente_sem_producao):
    """Garante que cada teste relê o ambiente do zero."""
    reset_settings()
    yield
    reset_settings()
