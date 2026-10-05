"""SQL do HANA montado com t-string (PEP 750, Python 3.14): valor vira parâmetro, nunca texto.

Antes, cada consulta que recebia um número de fora (o NPED que chega pela URL) fazia
``coerce_positive_int`` e colava o inteiro no texto com f-string — seguro por disciplina,
um ``int()`` esquecido de virar injeção. Com ``sql(t"...")`` a regra mora no tipo:

- ``{valor}``           → ``?`` + parâmetro (o driver manda o dado separado do SQL);
- ``{nome:ident}``      → identificador (``VIEW``, ``"SCHEMA"."VIEW"``) conferido por regex e
  colado — nome de objeto não pode ser parâmetro no SQL;
- ``{n:int}``           → literal inteiro colado, para onde o HANA não aceita ``?``
  (``LIMIT``). Só ``int`` de verdade; ``bool`` e string são recusados.

Qualquer outro formato (ou conversão ``!r``/``!s``) é erro: não existe "cole como veio".

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
    """Nome SEM aspas nem ponto (ex.: o ``SAP_SCHEMA``), para quem o põe entre aspas no SQL."""
    if not isinstance(nome, str) or not _NOME_SIMPLES.match(nome):
        raise ValueError(f"{what} inválido (esperado letras, dígitos e _): {nome!r}")
    return nome


def sql(consulta: Template) -> tuple[str, list[Any]]:
    """Devolve ``(texto_com_?, parametros)`` para ``execute_query``/``cursor.execute``."""
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
    raise ValueError(f"formato {formato!r} desconhecido em sql() — use '', ':ident' ou ':int'.")
