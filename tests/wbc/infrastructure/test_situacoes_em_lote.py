"""`situacoes_atuais` fatia os orçamentos em lotes de `TAMANHO_DO_LOTE` (SQL Server recusa
consulta com parâmetros demais). Desde 24/09/2026 o fatiamento é `itertools.batched`; aqui
se crava o tamanho e a ordem dos lotes sem banco nenhum."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from wbcpython.infrastructure.wbc_sql.repository import (
    TAMANHO_DO_LOTE,
    RepositorioOrcamentosWbcSql,
)


def _repo_espiao(lotes: list[list[str]]) -> RepositorioOrcamentosWbcSql:
    repo = RepositorioOrcamentosWbcSql(engine=None)  # type: ignore[arg-type]

    def _executar(sql: str, *, expandir: tuple[str, ...] = (), **parametros: Any):
        lotes.append(list(parametros["orcnums"]))
        return [
            SimpleNamespace(_mapping={"orcnum": o, "sitcode": 40, "revisao": "A"})
            for o in parametros["orcnums"]
        ]

    repo._executar = _executar  # type: ignore[method-assign]
    return repo


def test_lotes_do_tamanho_certo_e_nada_perdido():
    orcnums = [f"{n:08d}" for n in range(TAMANHO_DO_LOTE * 2 + 5)]
    lotes: list[list[str]] = []
    situacoes = _repo_espiao(lotes).situacoes_atuais(orcnums)
    assert [len(lote) for lote in lotes] == [TAMANHO_DO_LOTE, TAMANHO_DO_LOTE, 5]
    assert [o for lote in lotes for o in lote] == orcnums
    assert len(situacoes) == len(orcnums)


def test_lista_vazia_nao_vai_ao_banco():
    lotes: list[list[str]] = []
    assert _repo_espiao(lotes).situacoes_atuais([]) == {}
    assert lotes == []
