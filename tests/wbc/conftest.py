"""Isolamento dos testes do WBC em relação ao `.env` da máquina.

O `conftest.py` da raiz importa `config` do ServidorIntegracaoSAP, que faz
`load_dotenv()`: o `.env` do repositório entra no `os.environ` da sessão inteira.
Para o WBC isso importaria `OS_API_KEY` (o painel passaria a exigir chave em
todo teste) e, num `.env` com o bloco WBC preenchido, as credenciais reais.
Aqui cada teste começa com esses valores neutros; quem precisa de um deles
define o seu com `monkeypatch.setenv` depois (a fixture do teste vence esta).
"""

from __future__ import annotations

import os

import pytest

# Os prefixos do SIS (SAP_, SQL_, SQLSERVER_, OP_SL_) entram porque, desde 09/09/2026, as
# credenciais do WBC caem neles quando os nomes do WBC faltam — sem apagá-los, o `.env` da
# máquina preencheria HANA/SQL/SL nos testes que esperam configuração vazia.
_PREFIXOS = (
    "SL_", "WBC_SQL_", "HANA_", "TRACKING_", "WORKER_", "PAINEL_", "MESES_DE_JANELA",
    "SAP_", "SQL_", "SQLSERVER_", "OP_SL_",
)
_NOMES = (
    "WBC_ENVIRONMENT", "WBC_BLOCK_PRODUCTION_WRITES", "WBC_PRODUCTION_COMPANY_DB",
    "LOG_LEVEL", "LOG_FILE", "LIMITE_DE_ESCRITA_POR_CICLO", "FATOR_PESO_EMBARQUE",
    "MESES_DE_JANELA_DIRIGIDA",
)


@pytest.fixture(autouse=True)
def _ambiente_neutro(monkeypatch: pytest.MonkeyPatch) -> None:
    for nome in list(os.environ):
        if nome.startswith(_PREFIXOS) or nome in _NOMES:
            monkeypatch.delenv(nome, raising=False)
    # Vazio (e não ausente) de propósito: variável de ambiente vence o `.env`
    # lido pelo pydantic-settings, e o `.env` do repositório tem OS_API_KEY.
    monkeypatch.setenv("OS_API_KEY", "")
    monkeypatch.setenv("SIS_PAINEL_URL", "")
