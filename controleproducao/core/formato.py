"""pt-BR number formatting for the screens (22/09/2026).

`457578.12` on screen is not a number anyone can check — it is a string of digits. With a
thousands separator (`457.578,12`) the order of magnitude reads at a glance, which is the point
of a value column in an operational panel.

**Formatting lives in presentation, never in the data.** The service keeps returning `float`;
the template (or the router, when building the plan table) converts to text. Formatting at the
source would turn a number into a string and the first calculation done on it would break —
the same lesson as the `Decimal` that entered the arithmetic on 21/09, in the opposite direction.

We do not use `locale`: it is global process state, depends on the pt_BR locale being generated
on the system (in a lean container it is not) and fails silently by falling back to C. Swapping
the separators takes two lines and has none of those traps.
"""
from __future__ import annotations

from decimal import Decimal


def numero_br(valor: object, casas: int = 2) -> str:
    """`457578.12` → `457.578,12`. Empty for `None`/`""`; the value as text if it is not a number."""
    if valor is None or valor == "":
        return ""
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return str(valor)
    # `f"{n:,.2f}"` dá o padrão en-US (`457,578.12`); a troca em três passos evita
    # sobrescrever o separador que acabou de ser trocado.
    return f"{numero:,.{casas}f}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def quantidade_br(valor: object) -> str:
    """Quantity without useless decimals: `640`, `10,09`, `9.106,08`.

    OP quantities are usually whole; showing `640,00` adds noise to a column the operator scans top
    to bottom. But `10,09` needs both places — it is a real input quantity, and rounding on screen
    would hide exactly the one-cent divergence that already cost us an investigation.
    """
    if valor is None or valor == "":
        return ""
    try:
        numero = Decimal(str(valor))
    except Exception:  # noqa: BLE001 - texto que não é número passa direto
        return str(valor)
    return numero_br(numero, 0 if numero == numero.to_integral_value() else 2)
