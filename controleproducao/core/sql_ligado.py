"""Bind the package's `{name}` SQL templates as parameters (F7, 29/09/2026).

The module-2 queries (`pedidos_wbc/queries.py`) are transcriptions of the C# addon's
`Querys.resx`, kept textually close to the original on purpose. They used to be filled with
`str.format`, pasting values into the SQL. `ligar` keeps the SAME templates and turns every
value into a bound parameter (`?`), so nothing reaches the server as SQL text:

- `'{x}'` (quoted in the original)  → `?` with `str(x)` — the same text comparison;
- `{x}` (unquoted)                  → `?` with an integer, refused unless it is one;
- `{x}` holding a list/tuple        → `?, ?, ...` with the items as given (IN lists);
- `{schema}`                        → pasted, but only `""` or `"NAME".` (an identifier);
- `{filtro}`                        → only `""` is accepted (no dynamic SQL fragments).

Returns `(sql, params)` for `HanaDirectReader.fetch_all(*ligar(...))` and for the WBC
client, which translates `?` to what its driver expects.

`exige_leitura` is the read-only guard both readers call before executing (30/09/2026): this
package never writes by SQL — SAP data changes only through the Service Layer, and the WBC
database must never be written at all.
"""
from __future__ import annotations

import re
from typing import Any

_MARCA = re.compile(r"'\{(\w+)\}'|\{(\w+)\}")
_PREFIXO_SCHEMA = re.compile(r'^(?:"[A-Za-z0-9_]+"\.)?$')
_NOME_DE_SCHEMA = re.compile(r"^[A-Za-z0-9_]+$")

# Literals, quoted/bracketed identifiers and comments: blanked before the guard looks for
# keywords, so a column named "DELETE" or a text with ';' does not trip it.
_NAO_CODIGO = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|\[[^\]]*\]|--[^\n]*|/\*.*?\*/", re.S)
_INICIO_DE_LEITURA = re.compile(r"^\s*\(?\s*(?:SELECT|WITH)\b", re.I)
# Statements that change data or schema. REPLACE is absent on purpose: here it is the string
# function (NOVA_TABELA_QUOT), and the wbcpython guard trips on it.
_ESCRITA = re.compile(
    r"\b(?:INSERT|UPDATE|DELETE|MERGE|UPSERT|INTO|DROP|ALTER|CREATE|TRUNCATE|EXEC|EXECUTE|CALL|GRANT|REVOKE)\b",
    re.I,
)


def exige_leitura(sql: str) -> None:
    """Refuse anything but ONE read statement (SELECT / WITH). Raises `ValueError`."""
    codigo = _NAO_CODIGO.sub(" ", sql)
    escrita = _ESCRITA.search(codigo)
    if not _INICIO_DE_LEITURA.match(codigo) or escrita or ";" in codigo.rstrip().rstrip(";"):
        motivo = f"'{escrita.group(0)}'" if escrita else "não é um único SELECT"
        raise ValueError(f"SQL recusado: este pacote só lê ({motivo}): {sql.strip()[:120]!r}")


def nome_de_schema(valor: str) -> str:
    """A bare schema/company name, validated before it is quoted into `SET SCHEMA "..."`."""
    if not isinstance(valor, str) or not _NOME_DE_SCHEMA.match(valor):
        raise ValueError(f"nome de schema inválido: {valor!r}")
    return valor


def _inteiro(valor: Any, nome: str) -> int:
    if isinstance(valor, bool):
        raise ValueError(f"{nome}: esperado um número, veio {valor!r}")
    if isinstance(valor, int):
        return valor
    if isinstance(valor, float) and valor.is_integer():
        return int(valor)
    texto = str(valor if valor is not None else "").strip()
    if texto.isdigit():
        return int(texto)
    raise ValueError(f"{nome}: esperado um número, veio {valor!r}")


def ligar(modelo: str, **valores: Any) -> tuple[str, tuple[Any, ...]]:
    """`(sql_com_?, params)` from a `{name}` template — see the module docstring."""
    params: list[Any] = []
    usados: set[str] = set()

    def troca(marca: re.Match[str]) -> str:
        nome = marca.group(1) or marca.group(2)
        if nome not in valores:
            raise KeyError(f"SQL sem valor para {{{nome}}}")
        usados.add(nome)
        valor = valores[nome]
        if marca.group(1):
            params.append(str(valor))
            return "?"
        if nome == "schema":
            if not isinstance(valor, str) or not _PREFIXO_SCHEMA.match(valor):
                raise ValueError(f"schema inválido para o SQL: {valor!r}")
            return valor
        if nome == "filtro":
            if valor:
                raise ValueError("filtro dinâmico não é aceito no SQL (só vazio).")
            return ""
        if isinstance(valor, (list, tuple)):
            if not valor:
                raise ValueError(f"lista vazia em {{{nome}}}: o IN () seria SQL inválido.")
            params.extend(valor)
            return ", ".join("?" * len(valor))
        params.append(_inteiro(valor, nome))
        return "?"

    sql = _MARCA.sub(troca, modelo)
    sobra = set(valores) - usados
    if sobra:
        raise TypeError(f"valor(es) sem lugar no SQL: {sorted(sobra)}")
    return sql, tuple(params)
