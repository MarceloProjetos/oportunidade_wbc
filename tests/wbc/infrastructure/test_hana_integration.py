"""Smoke test do HANA contra o ambiente real.

Não roda por padrão. Para executar, na rede interna da Altamira:

    python -m pytest --run-integration -k hana_integration -v -s

Requisitos: `.env` preenchido e `pip install -r requirements.txt` (driver `hdbcli`).

**Objetivo principal:** resolver a dúvida deixada em aberto na especificação
(`ai_spec/02_data_model.md`, seção 3) — as views `VW_EVOL_OPORTUNIDADE_ALT` e
`VW_CLIENTE_MUNICIPIO_ALTA` existem sob o schema de homologação, ou apenas sob o
de produção? O protótipo original consultava o schema de produção com o nome
fixo no código, e nunca se confirmou se havia alternativa.

Todos os testes aqui são de LEITURA.
"""

from __future__ import annotations

import pytest

from wbcpython.config import get_settings
from wbcpython.infrastructure.hana import (
    VIEW_EVOLUCAO,
    VIEW_MUNICIPIO,
    RepositorioViewsHanaSql,
)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def repositorio() -> RepositorioViewsHanaSql:
    pytest.importorskip("hdbcli", reason="driver ausente: pip install -r requirements.txt")
    settings = get_settings()

    if not settings.hana.host or not settings.hana.username:
        pytest.skip("HANA não configurado no .env (HANA_HOST / HANA_USERNAME).")

    repo = RepositorioViewsHanaSql(
        settings.hana,
        production_company_db=settings.production_company_db,
        block_production_writes=settings.block_production_writes,
    )
    try:
        yield repo
    finally:
        repo.close()


def test_conexao_funciona(repositorio: RepositorioViewsHanaSql) -> None:
    """Valida host, porta 30015, usuário e senha de uma só vez."""
    resultado = repositorio.views_existem()
    assert isinstance(resultado, dict)


def test_em_qual_schema_as_views_existem(repositorio: RepositorioViewsHanaSql) -> None:
    """Responde à pergunta em aberto — e imprime o resultado para registro.

    Não falha se as views não estiverem no schema configurado: o objetivo é
    *descobrir* onde elas estão, e essa informação precisa chegar ao PROGRESS.md
    mesmo quando a resposta é "não estão aqui".
    """
    resultado = repositorio.views_existem()

    print(f"\n{'=' * 62}")
    print(f"Schema consultado: {repositorio.schema}")
    for view, existe in sorted(resultado.items()):
        print(f"  {'ENCONTRADA' if existe else 'ausente   '}  {view}")
    print(f"{'=' * 62}")

    if not any(resultado.values()):
        pytest.skip(
            f"Nenhuma das views existe no schema '{repositorio.schema}'. "
            f"Registre isso no PROGRESS.md: o acesso de leitura ao schema de "
            f"produção passa a ser o padrão confirmado do projeto."
        )


def test_le_a_evolucao_de_um_orcamento(repositorio: RepositorioViewsHanaSql) -> None:
    """Lê um orçamento real, se a view estiver acessível."""
    if not repositorio.views_existem().get(VIEW_EVOLUCAO):
        pytest.skip(f"{VIEW_EVOLUCAO} não existe no schema '{repositorio.schema}'.")

    # Orçamento observado em dados reais durante a análise da especificação.
    evolucao = repositorio.evolucao_do_orcamento("00123316")
    if evolucao is None:
        pytest.skip("Orçamento 00123316 não existe neste ambiente.")

    print(
        f"\n  N_WBC={evolucao.n_wbc} Status={evolucao.status_wbc!r} "
        f"PctComissao={evolucao.pct_comissao} Retorno={evolucao.retorno}"
    )
    assert evolucao.n_wbc == "00123316"


def test_le_um_municipio(repositorio: RepositorioViewsHanaSql) -> None:
    if not repositorio.views_existem().get(VIEW_MUNICIPIO):
        pytest.skip(f"{VIEW_MUNICIPIO} não existe no schema '{repositorio.schema}'.")

    municipio = repositorio.municipio("COTIA", "SP")
    if municipio is None:
        pytest.skip("Município COTIA/SP não encontrado neste ambiente.")

    print(f"\n  Municipio={municipio.municipio} UF={municipio.uf} AbsId={municipio.abs_id}")
    assert municipio.abs_id is not None
