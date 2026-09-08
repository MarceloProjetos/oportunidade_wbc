"""Validação e citação de identificadores SQL (nomes de schema).

Por que este módulo existe: o nome do schema **não pode** ser passado como
parâmetro de bind. Em SQL, um parâmetro é um *valor*; o schema é um
*identificador*, e precisa aparecer literalmente no texto da consulta::

    SELECT ... FROM "SBOALTAMIRAHOMOLOG"."VW_EVOL_OPORTUNIDADE_ALT"
                     ^^^^^^^^^^^^^^^^^^ não dá para bindar

Ou seja, este é o único ponto de todo o projeto em que algo vindo de
configuração é interpolado no texto de uma consulta. Como a regra do projeto é
nunca concatenar SQL com dado variável, a exceção precisa ser tratada com
rigor: o nome é validado contra uma lista branca de caracteres e depois citado.

O risco não é hipotético: `HANA_SCHEMA` vem do `.env`, e um valor como
``X"."Y`` ou ``X; DROP`` mudaria a consulta se fosse interpolado sem cuidado.
"""

from __future__ import annotations

import re

# Identificadores SAP/HANA usam letras, dígitos, `_` e (raramente) `$` e `#`.
# Deliberadamente restritivo: é melhor recusar um nome exótico e legítimo — que
# alguém então corrige na configuração — do que aceitar um nome construído.
_IDENTIFICADOR_VALIDO = re.compile(r"^[A-Za-z_][A-Za-z0-9_$#]{0,127}$")


class IdentificadorInvalido(ValueError):
    """Nome de schema/objeto que não passou na validação."""


def validar_identificador(nome: str, *, descricao: str = "identificador") -> str:
    """Valida um identificador SQL e devolve a forma normalizada (sem aspas).

    Raises:
        IdentificadorInvalido: se o nome não puder ser usado com segurança.
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
    """Valida e devolve o identificador entre aspas duplas, pronto para o SQL."""
    return f'"{validar_identificador(nome, descricao=descricao)}"'
