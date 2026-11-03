"""Validation and quoting of SQL identifiers (schema names).

A schema name cannot be a bind parameter: a parameter is a *value*, a schema is an
*identifier* and must appear literally in the query text::

    SELECT ... FROM "SBOALTAMIRAHOMOLOG"."VW_EVOL_OPORTUNIDADE_ALT"
                     ^^^^^^^^^^^^^^^^^^ cannot be bound

So this is the only place in the project where configuration is interpolated into query
text. The project rule is never to concatenate variable data into SQL, hence the name is
checked against a character allow-list and then quoted.

The risk is real: `HANA_SCHEMA` and `SL_COMPANY_DB` come from the `.env`, and a value like
``X"."Y`` or ``X; DROP`` would change the query if interpolated unchecked.
"""

from __future__ import annotations

import re

# Identificadores SAP/HANA usam letras, dígitos, `_` e (raramente) `$` e `#`.
# Deliberadamente restritivo: é melhor recusar um nome exótico e legítimo — que
# alguém então corrige na configuração — do que aceitar um nome construído.
_IDENTIFICADOR_VALIDO = re.compile(r"^[A-Za-z_][A-Za-z0-9_$#]{0,127}$")


class IdentificadorInvalido(ValueError):
    """Schema/object name that failed validation."""


def validar_identificador(nome: str, *, descricao: str = "identificador") -> str:
    """Validate a SQL identifier and return it stripped and unquoted.

    Raises:
        IdentificadorInvalido: the name cannot be used safely.
    """
    if not isinstance(nome, str) or not nome.strip():
        raise IdentificadorInvalido(f"{descricao} vazio ou ausente.")

    limpo = nome.strip()
    if not _IDENTIFICADOR_VALIDO.match(limpo):
        raise IdentificadorInvalido(
            f"{descricao} inválido: {nome!r}. São aceitos apenas letras, dígitos, "
            f"'_', '$' e '#', começando por letra ou '_'. Como o schema é interpolado "
            f"no texto da consulta (não pode ser parâmetro de bind), qualquer outro "
            f"caractere é recusado por segurança."
        )
    return limpo


def citar_identificador(nome: str, *, descricao: str = "identificador") -> str:
    """Validate the identifier and return it in double quotes, ready for SQL."""
    return f'"{validar_identificador(nome, descricao=descricao)}"'
