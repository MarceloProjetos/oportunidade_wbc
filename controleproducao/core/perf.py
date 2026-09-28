"""Medição de onde o tempo é gasto durante uma execução.

Criado em 16/09/2026 para diagnosticar a lentidão na criação das OPs (relatada pelo
Anderson no orçamento 00125192). O objetivo é responder com número, e não com suposição,
a pergunta "o que está demorando": abrir conexões, executar as queries, ou as chamadas à
Service Layer.

A distinção que mais importa é entre **abrir conexão** e **executar query**. Hoje
`HanaDirectReader.fetch_all` e `WbcSqlServerClient.fetch_all` abrem uma conexão nova a cada
chamada (autenticação completa; no SQL Server, com o Driver 18, também um handshake TLS) e
fecham em seguida. Se o tempo estiver concentrado em "abrir conexão", a correção é reusar a
conexão ao longo da execução; se estiver em "executar query", o problema é o peso das
queries em si (várias herdadas do addon tinham `CommandTimeout` de 300-500s — ver item 9 da
seção 8 do migration_guide.md). São correções bem diferentes, daí valer medir antes.

Usa um registro global por processo. Não é elegante, mas aqui é adequado: a CLI é um
processo por execução, e a alternativa (passar um objeto de perfil por toda a cadeia de
funções de negócio) poluiria assinaturas que replicam o C# original. Fica DESLIGADO por
padrão — `ativar()` é chamado só pela CLI, com `--perfil`.
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
    def total_medido(self) -> float:
        return sum(a.segundos for a in self.por_categoria.values())

    @property
    def decorrido(self) -> float:
        return time.perf_counter() - self.inicio

    def resumo_categorias(self) -> list[tuple[str, int, float]]:
        """(categoria, chamadas, segundos), da mais cara para a mais barata."""
        return sorted(
            ((cat, a.chamadas, a.segundos) for cat, a in self.por_categoria.items()),
            key=lambda item: item[2],
            reverse=True,
        )

    def resumo_operacoes(self, limite: int = 15) -> list[tuple[str, str, int, float]]:
        """(categoria, rótulo, chamadas, segundos) das operações mais caras no total."""
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
    """Reduz uma query à sua "forma", para agrupar execuções da mesma query.

    As queries deste projeto trazem os valores embutidos no texto (herança do
    `String.Format` do C#), então agrupar pelo texto cru geraria uma entrada por chamada.
    Trocando literais e números por `?` e colapsando espaços, todas as execuções da mesma
    query caem na mesma linha do relatório — que é o que revela o "esta query rodou 340
    vezes" que interessa aqui.
    """
    forma = _LITERAL_TEXTO.sub("?", sql)
    forma = _NUMERO.sub("?", forma)
    forma = _ESPACOS.sub(" ", forma).strip()
    return forma[:tamanho] + ("…" if len(forma) > tamanho else "")
