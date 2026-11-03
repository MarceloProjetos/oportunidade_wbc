"""HANA SQL built from a t-string (PEP 750, Python 3.14): values become parameters, never text.

Before, each query taking an outside number (the NPED from the URL) ran
``coerce_positive_int`` and pasted the integer with an f-string: safe by discipline, one
forgotten ``int()`` away from injection. With ``sql(t"...")`` the rule lives in the type:

- ``{value}``           -> ``?`` + parameter (the driver sends data apart from the SQL);
- ``{name:ident}``      -> identifier (``VIEW``, ``"SCHEMA"."VIEW"``) checked by regex and
  pasted; an object name cannot be a SQL parameter;
- ``{n:int}``           -> pasted integer literal, where HANA rejects ``?`` (``LIMIT``).
  Real ``int`` only; ``bool`` and strings are refused;
- ``{items:ints}``      -> NON-empty list of ``int`` pasted as ``1,2,3`` (for ``IN (...)``),
  same rule as ``:int`` per element.

Any other format (or ``!r``/``!s`` conversion) is an error: there is no "paste as is".

    consulta, params = sql(t'SELECT * FROM {base:ident} WHERE "N_PED" = {nped}')
    df = extrator.execute_query(consulta, params)
"""

from __future__ import annotations

import re
from string.templatelib import Interpolation, Template
from typing import Any

_PARTE = r'(?:"[A-Za-z_][A-Za-z0-9_]*"|[A-Za-z_][A-Za-z0-9_]*)'
_IDENTIFICADOR = re.compile(rf"^{_PARTE}(?:\.{_PARTE})*$")


_NOME_SIMPLES = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def nome_simples(nome: str, *, what: str = "identificador") -> str:
    """Validate a bare name (no quotes, no dot; e.g. ``SAP_SCHEMA``) for callers that quote it in SQL."""
    if not isinstance(nome, str) or not _NOME_SIMPLES.match(nome):
        raise ValueError(f"{what} inválido (esperado letras, dígitos e _): {nome!r}")
    return nome


def sql(consulta: Template) -> tuple[str, list[Any]]:
    """Return ``(text_with_?, params)`` for ``execute_query``/``cursor.execute``."""
    if not isinstance(consulta, Template):
        raise TypeError("sql() recebe uma t-string: sql(t'...'), não str nem f-string.")
    partes: list[str] = []
    parametros: list[Any] = []
    for item in consulta:
        if isinstance(item, str):
            partes.append(item)
            continue
        partes.append(_trecho(item, parametros))
    return "".join(partes), parametros


def _trecho(item: Interpolation, parametros: list[Any]) -> str:
    if item.conversion is not None:
        raise ValueError(f"conversão !{item.conversion} não é aceita em sql() ({item.expression}).")
    valor, formato = item.value, item.format_spec
    if formato == "":
        parametros.append(valor)
        return "?"
    if formato == "ident":
        if not isinstance(valor, str) or not _IDENTIFICADOR.match(valor):
            raise ValueError(f"identificador SQL inválido em {item.expression}: {valor!r}")
        return valor
    if formato == "int":
        if isinstance(valor, bool) or not isinstance(valor, int):
            raise ValueError(f"{item.expression} tem de ser int para :int, veio {type(valor).__name__}")
        return str(valor)
    if formato == "ints":
        # IN-list of keys read from SAP itself (DocEntry): pasted as literals because one `?`
        # per element would make the statement text vary with the list size anyway.
        if isinstance(valor, (str, bytes)):
            raise ValueError(f"{item.expression} tem de ser lista de int para :ints, veio texto")
        itens = list(valor)
        if not itens or any(isinstance(v, bool) or not isinstance(v, int) for v in itens):
            raise ValueError(f"{item.expression} tem de ser lista NÃO vazia de int para :ints")
        return ",".join(str(v) for v in itens)
    raise ValueError(f"formato {formato!r} desconhecido em sql() — use '', ':ident', ':int' ou ':ints'.")
