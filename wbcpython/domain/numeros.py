"""Normalization of numbers coming from SAP text fields.

Several `OrcDetalhe` UDFs are **typed as text** and, in real data, the decimal separator is inconsistent
between records: `U_ORCVALEMB`, `U_ORCIMP_RETORNO` and `U_ORCIMP_INDICE_VENDAS` show up as `"0,0000"` or
`"0.0000"` within the same dataset (compare `orcamento_00125391.json` with `orcamento_00125537.json`).

Finding recorded in `ai_spec/02_data_model.md` (gone; `docs/wbc/ai_spec/00_index.md` says where its subjects
live now). In practice no convention can be assumed: a `Decimal("1,5")` blows up, and so does a
`float("1.234,56")`. Worse would be the silent case: reading `"1.234"` as 1234 (thousands) when it was
1.234 (decimal), or vice versa.

The strategy is to recognise the convention **per record**, by the relative position of the separators,
and to flag what cannot be interpreted instead of silently returning zero.
"""

from __future__ import annotations

import logging
import re
from decimal import Decimal, InvalidOperation

logger = logging.getLogger(__name__)

_SO_NUMERO = re.compile(r"^[+-]?[\d.,\s]+$")
_NAO_DIGITO = re.compile(r"[^\d]")


class NumeroInvalido(ValueError):
    """Text that could not be interpreted as a number."""


def normalizar_decimal(valor: object, *, padrao: Decimal | None = None) -> Decimal:
    """Convert a possibly textual value into `Decimal`.

    Handles both conventions (comma or dot as decimal separator) and thousands separators.

    Tie-break when both separators appear: **the last one is the decimal**. So `"1.234,56"` becomes
    `1234.56` and `"1,234.56"` also becomes `1234.56`, which is right for both conventions.

    When only one kind of separator appears, the decision depends on the digits after it:

    * comma: read as decimal (`"1,234"` -> `1.234`), unless it is repeated with exactly 3 digits after the
      last one (`"1,234,567"`), which is thousands;
    * dot: exactly 3 digits after the last dot is ambiguous (`"1.234"` may be one thousand two hundred thirty
      four, or 1.234); it is read as **thousands** when the dot is repeated or the integer part has at most 3
      digits (`"1.234"` -> `1234`, `"0.001"` -> `1`, but `"12345.678"` -> `12345.678`). Thousands is the
      commonest reading in Brazilian ERP data, and the decision is recorded here instead of hidden.

    Args:
        valor: number, text or `None`.
        padrao: value to return when the input is empty/absent. If `None` (the default), empty input returns
            `Decimal(0)`.

    Raises:
        NumeroInvalido: non-empty text that cannot be interpreted as a number.
    """
    if valor is None:
        return padrao if padrao is not None else Decimal(0)

    if isinstance(valor, Decimal):
        return valor
    if isinstance(valor, int):
        return Decimal(valor)
    if isinstance(valor, float):
        return Decimal(str(valor))

    texto = str(valor).strip()
    if not texto:
        return padrao if padrao is not None else Decimal(0)

    if not _SO_NUMERO.match(texto):
        raise NumeroInvalido(f"Valor não numérico vindo do SAP: {valor!r}")

    texto = texto.replace(" ", "")
    sinal = ""
    if texto[0] in "+-":
        sinal, texto = ("-" if texto[0] == "-" else ""), texto[1:]

    tem_ponto = "." in texto
    tem_virgula = "," in texto

    if tem_ponto and tem_virgula:
        # O separador decimal é o que aparece por último.
        decimal_e_virgula = texto.rfind(",") > texto.rfind(".")
        inteiro, _, fracao = texto.rpartition(",") if decimal_e_virgula else texto.rpartition(".")
        normalizado = f"{_NAO_DIGITO.sub('', inteiro)}.{_NAO_DIGITO.sub('', fracao)}"
    elif tem_virgula:
        inteiro, _, fracao = texto.rpartition(",")
        if len(fracao) == 3 and texto.count(",") > 1:
            # "1,234,567" — só faz sentido como milhar.
            normalizado = _NAO_DIGITO.sub("", texto)
        else:
            normalizado = f"{_NAO_DIGITO.sub('', inteiro)}.{fracao}"
    elif tem_ponto:
        inteiro, _, fracao = texto.rpartition(".")
        if len(fracao) == 3 and (texto.count(".") > 1 or len(inteiro) <= 3):
            # "1.234" ou "1.234.567": lido como separador de milhar.
            normalizado = _NAO_DIGITO.sub("", texto)
        else:
            normalizado = f"{_NAO_DIGITO.sub('', inteiro)}.{fracao}"
    else:
        normalizado = texto

    if not normalizado or normalizado == ".":
        raise NumeroInvalido(f"Valor não numérico vindo do SAP: {valor!r}")

    try:
        return Decimal(sinal + normalizado)
    except InvalidOperation as exc:
        raise NumeroInvalido(f"Valor não numérico vindo do SAP: {valor!r}") from exc


def normalizar_decimal_tolerante(valor: object, *, campo: str = "") -> Decimal:
    """Like `normalizar_decimal`, but logs and returns 0 instead of raising.

    For fields where one dirty value must not interrupt processing of the whole quote. The log warning keeps
    the failure from going unnoticed; silently returning zero would be the worst outcome.
    """
    try:
        return normalizar_decimal(valor)
    except NumeroInvalido:
        logger.warning(
            "Campo %s com valor não numérico (%r) — assumido 0. Verificar no SAP.",
            campo or "(sem nome)",
            valor,
        )
        return Decimal(0)


def formatar_para_sap(valor: Decimal | float) -> str:
    """Format a number for writing into SAP text UDFs.

    Uses a dot as decimal separator and four decimals, the format seen in most real records (`"0.0000"`).
    Always writing the same format is what keeps the new solution from adding to the existing mess.
    """
    return f"{Decimal(str(valor)):.4f}"
