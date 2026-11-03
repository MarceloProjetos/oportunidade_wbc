"""Measures where the time goes during an execution.

Created 16/09/2026 to diagnose slow OP creation (reported by Anderson on orçamento
00125192): answer "what is taking long" with numbers — opening connections, running
queries, or Service Layer calls.

The distinction that matters is **opening a connection** vs **running a query**. When this
was written, `HanaDirectReader.fetch_all` and `WbcSqlServerClient.fetch_all` opened a new
connection per call (full authentication, plus a TLS handshake on SQL Server); both readers
now keep and reuse one connection (`_conexao_ativa`), so "abrir conexão" should show a few
calls per run. If time concentrates there, reuse is not working; if it concentrates in
"executar query", the queries themselves are heavy (several inherited from the addon had a
`CommandTimeout` of 300-500 s — migration_guide.md section 8, item 9). Different fixes,
hence measure first.

Uses a process-global registry: the CLI is one process per run, and passing a profile object
through every business function would pollute signatures that mirror the original C#. OFF by
default — only the CLI calls `ativar()`, with `--perfil`.
"""
from __future__ import annotations

import re
import time
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field


@dataclass
class _Acumulado:
    chamadas: int = 0
    segundos: float = 0.0


@dataclass
class Perfil:
    ativo: bool = False
    inicio: float = field(default_factory=time.perf_counter)
    por_categoria: dict[str, _Acumulado] = field(default_factory=lambda: defaultdict(_Acumulado))
    por_operacao: dict[tuple[str, str], _Acumulado] = field(default_factory=lambda: defaultdict(_Acumulado))

    def ativar(self) -> None:
        self.ativo = True
        self.inicio = time.perf_counter()

    @contextmanager
    def medir(self, categoria: str, rotulo: str) -> Iterator[None]:
        if not self.ativo:
            yield
            return
        comeco = time.perf_counter()
        try:
            yield
        finally:
            gasto = time.perf_counter() - comeco
            for acumulado in (self.por_categoria[categoria], self.por_operacao[(categoria, rotulo)]):
                acumulado.chamadas += 1
                acumulado.segundos += gasto

    @property
    def decorrido(self) -> float:
        return time.perf_counter() - self.inicio

    def resumo_categorias(self) -> list[tuple[str, int, float]]:
        """(category, calls, seconds), most expensive first."""
        return sorted(
            ((cat, a.chamadas, a.segundos) for cat, a in self.por_categoria.items()),
            key=lambda item: item[2],
            reverse=True,
        )

    def resumo_operacoes(self, limite: int = 15) -> list[tuple[str, str, int, float]]:
        """(category, label, calls, seconds) of the `limite` most expensive operations overall."""
        itens = (
            (cat, rotulo, a.chamadas, a.segundos)
            for (cat, rotulo), a in self.por_operacao.items()
        )
        return sorted(itens, key=lambda item: item[3], reverse=True)[:limite]


PERFIL = Perfil()


_LITERAL_TEXTO = re.compile(r"'[^']*'")
_NUMERO = re.compile(r"\b\d+\b")
_ESPACOS = re.compile(r"\s+")


def forma_sql(sql: str, tamanho: int = 90) -> str:
    """Reduce a query to its "shape" so executions of the same query group together.

    Queries here carry values embedded in the text (legacy of the C# `String.Format`), so
    grouping by raw text would give one entry per call. Replacing literals and numbers with
    `?` and collapsing whitespace puts every run of a query on one report line — which is
    what reveals "this query ran 340 times".
    """
    forma = _LITERAL_TEXTO.sub("?", sql)
    forma = _NUMERO.sub("?", forma)
    forma = _ESPACOS.sub(" ", forma).strip()
    return forma[:tamanho] + ("…" if len(forma) > tamanho else "")
