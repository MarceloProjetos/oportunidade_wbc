"""Comparação de revisões ("versão") de orçamento.

O WBC identifica a revisão de um orçamento por um caractere: `"0"` para a
versão inicial e depois `"A"`, `"B"`, `"C"`… A integração precisa comparar a
revisão que está no WBC com a que já foi gravada no documento do SAP
(`U_INO_VERSAOWBC`) para decidir se há algo novo a aplicar.

O sistema legado faz essa comparação com a fórmula::

    ((int)char.ToUpper(char.Parse(v))) - 64

Ou seja: o código ASCII do caractere menos 64. Isso dá `A`=1, `B`=2, `C`=3 —
e, para dígitos, valores negativos (`0`=-16, `9`=-7). À primeira vista parece
um acidente, mas o resultado é uma ordem total coerente com a semântica real:

    "0" < "1" < … < "9" < "A" < "B" < "C" …

isto é, versão inicial antes das revisões. Por isso a fórmula é **preservada**
aqui, em vez de "corrigida" — mudá-la alteraria silenciosamente o comportamento
de comparação de milhares de registros históricos.

O que foi corrigido em relação ao legado: `char.Parse` lança exceção se a
string tiver mais de um caractere, o que derrubaria a execução inteira. Aqui,
um valor inesperado é tratado de forma explícita e previsível.
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
    """Revisão com formato inesperado (mais de um caractere)."""


def ordem_revisao(revisao: str | None, *, estrito: bool = False) -> int:
    """Converte a revisão num inteiro comparável.

    Args:
        revisao: caractere da revisão (`""`, `"0"`, `"A"`, `"b"`…).
        estrito: se True, levanta `RevisaoInvalida` diante de um valor com mais
            de um caractere. Se False (padrão), usa apenas o primeiro caractere,
            evitando derrubar o processamento por causa de um dado sujo.

    Returns:
        Inteiro que preserva a ordem "versão inicial < revisões posteriores".
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
    """Diz se o WBC tem uma revisão mais nova do que a já gravada no SAP.

    É a condição que o legado usa para decidir entre "há trabalho a fazer" e
    "documento congelado, não mexer". Empate significa **não** reprocessar.
    """
    return ordem_revisao(revisao_wbc) > ordem_revisao(revisao_sap)
