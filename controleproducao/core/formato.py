"""Formatação numérica pt-BR para as telas (22/09/2026).

`457578.12` na tela não é um número que alguém confere — é uma sequência de dígitos. Com
separador de milhar (`457.578,12`) a ordem de grandeza se lê de relance, que é o ponto de
uma coluna de valor num painel operacional.

**Formatação mora na apresentação, nunca no dado.** O serviço continua devolvendo `float`;
quem converte para texto é o template (ou o router, ao montar a tabela do plano). Formatar
na origem transformaria um número em string e a primeira conta feita sobre ele quebraria —
é a mesma lição do `Decimal` que entrou na aritmética em 21/09, na direção contrária.

Não usamos `locale`: ele é estado global do processo, depende de o locale pt_BR estar
gerado no sistema (num contêiner enxuto, não está) e falha silenciosamente voltando ao C.
A troca de separadores é de duas linhas e não tem nenhuma dessas armadilhas.
"""
from __future__ import annotations

from decimal import Decimal


def numero_br(valor: object, casas: int = 2) -> str:
    """`457578.12` → `457.578,12`. Devolve o valor original se não for número."""
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
    """Quantidade sem casas decimais inúteis: `640`, `10,09`, `9.106,08`.

    Quantidade de OP costuma ser inteira; exibir `640,00` acrescenta ruído a uma coluna
    que o operador percorre de cima a baixo. Mas `10,09` precisa das duas casas — é uma
    quantidade real de insumo, e arredondar na tela esconderia justamente a divergência
    de um centavo que já nos custou uma investigação.
    """
    if valor is None or valor == "":
        return ""
    try:
        numero = Decimal(str(valor))
    except Exception:  # noqa: BLE001 - texto que não é número passa direto
        return str(valor)
    return numero_br(numero, 0 if numero == numero.to_integral_value() else 2)
