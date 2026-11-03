"""Comparison of quotation revisions ("version").

WBC identifies a quotation's revision by one character: `"0"` for the initial version, then `"A"`, `"B"`,
`"C"`... The integration must compare the revision in WBC with the one already stored on the SAP document
(`U_INO_VERSAOWBC`) to decide whether there is anything new to apply.

The legacy system compares with the formula::

    ((int)char.ToUpper(char.Parse(v))) - 64

That is, the ASCII code of the character minus 64. It gives `A`=1, `B`=2, `C`=3 and, for digits, negative
values (`0`=-16, `9`=-7). It looks like an accident, but the result is a total order consistent with the
real semantics:

    "0" < "1" < ... < "9" < "A" < "B" < "C" ...

that is, initial version before the revisions. So the formula is **preserved** here instead of being
"fixed": changing it would silently change the comparison outcome for thousands of historical records.

What was fixed relative to the legacy: `char.Parse` throws if the string has more than one character,
which would bring down the whole run. Here an unexpected value is handled explicitly and predictably.
"""

from __future__ import annotations

# Ausência de revisão — ordenada **abaixo de qualquer revisão real**.
#
# Divergência deliberada do legado, e vale explicar por quê. No legado, revisão
# vazia deixava a variável no valor inicial 0. Como a fórmula ASCII-64 dá -16
# para "0" e -7 para "9", esse zero caía *entre* os dígitos e as letras — de
# modo que uma revisão ausente no WBC era considerada MAIS NOVA que a revisão
# "0" gravada no SAP, e disparava o ramo de cancelar-e-recriar a cotação.
#
# Ou seja: dado faltando no WBC provocava uma ação destrutiva no SAP. Manter
# essa fidelidade ao legado seria preservar um bug com consequência real, então
# aqui a ausência de revisão ordena abaixo de tudo: sem informação, o documento
# é tratado como congelado, nunca como desatualizado.
SEM_REVISAO = -1000


class RevisaoInvalida(ValueError):
    """Revision with an unexpected format (more than one character)."""


def ordem_revisao(revisao: str | None, *, estrito: bool = False) -> int:
    """Convert the revision into a comparable integer.

    Args:
        revisao: revision character (`""`, `"0"`, `"A"`, `"b"`...).
        estrito: if True, raise `RevisaoInvalida` for a value with more than one character. If False
            (default), use only the first character, so dirty data does not bring down processing.

    Returns:
        Integer preserving the order "initial version < later revisions". `None` or blank gives
        `SEM_REVISAO`.
    """
    if revisao is None:
        return SEM_REVISAO

    texto = revisao.strip()
    if not texto:
        return SEM_REVISAO

    if len(texto) > 1:
        if estrito:
            raise RevisaoInvalida(
                f"Revisão inesperada: {revisao!r}. Esperava um único caractere "
                f'(ex.: "0", "A", "B").'
            )
        texto = texto[0]

    return ord(texto.upper()) - 64


def revisao_wbc_e_mais_nova(revisao_wbc: str | None, revisao_sap: str | None) -> bool:
    """Whether WBC has a newer revision than the one already stored in the SAP.

    It is the condition the legacy uses to choose between "there is work to do" and "frozen document, do not
    touch". A tie means **do not** reprocess.
    """
    return ordem_revisao(revisao_wbc) > ordem_revisao(revisao_sap)
