"""Normalização de números vindos de campos de texto do SAP.

Vários UDFs do `OrcDetalhe` são **tipados como texto** e, em dados reais, o
separador decimal é inconsistente entre registros: `U_ORCVALEMB`,
`U_ORCIMP_RETORNO` e `U_ORCIMP_INDICE_VENDAS` aparecem ora como `"0,0000"`, ora
como `"0.0000"`, dentro do mesmo conjunto de dados (comparar
`orcamento_00125391.json` com `orcamento_00125537.json`).

Achado registrado em `ai_spec/02_data_model.md`. A consequência prática é que
não dá para assumir uma convenção: um `Decimal("1,5")` estoura, e um
`float("1.234,56")` também. Pior seria o silencioso — interpretar `"1.234"` como
1234 (milhar) quando era 1,234 (decimal), ou vice-versa.

A estratégia aqui é reconhecer a convenção **por registro**, pela posição
relativa dos separadores, e sinalizar o que não der para interpretar em vez de
devolver zero calado.
"""

from __future__ import annotations

import logging
import re
from decimal import Decimal, InvalidOperation

logger = logging.getLogger(__name__)

_SO_NUMERO = re.compile(r"^[+-]?[\d.,\s]+$")
_NAO_DIGITO = re.compile(r"[^\d]")


class NumeroInvalido(ValueError):
    """Texto que não pôde ser interpretado como número."""


def normalizar_decimal(valor: object, *, padrao: Decimal | None = None) -> Decimal:
    """Converte um valor possivelmente textual em `Decimal`.

    Trata as duas convenções (vírgula e ponto como separador decimal) e também
    separadores de milhar.

    Regra de desempate, quando os dois separadores aparecem: **o último a
    ocorrer é o decimal**. Assim `"1.234,56"` vira `1234.56` e `"1,234.56"`
    também vira `1234.56` — que é o comportamento correto para ambas as
    convenções.

    Quando aparece só um tipo de separador, a decisão é pelo número de casas
    depois dele: exatamente 3 dígitos e nenhum outro separador é ambíguo
    (`"1.234"` pode ser mil duzentos e trinta e quatro, ou 1,234). Nesse caso
    trata-se como **separador de milhar**, que é a leitura mais comum em dados
    de ERP brasileiro — e a decisão fica registrada aqui, não escondida.

    Args:
        valor: número, texto ou `None`.
        padrao: valor a devolver quando a entrada é vazia/ausente. Se `None`
            (o padrão), entrada vazia devolve `Decimal(0)`.

    Raises:
        NumeroInvalido: texto não vazio que não é interpretável como número.
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
    """Como `normalizar_decimal`, mas registra e devolve 0 em vez de estourar.

    Para uso em campos onde um dado sujo isolado não deve interromper o
    processamento do orçamento inteiro. O aviso no log é o que impede a falha de
    passar despercebida — devolver zero calado seria o pior dos mundos.
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
    """Formata um número para gravação nos UDFs de texto do SAP.

    Usa ponto como separador decimal e quatro casas, que é o formato observado
    na maioria dos registros reais (`"0.0000"`). Escrever sempre no mesmo
    formato é o que impede a nova solução de aumentar a bagunça que já existe.
    """
    return f"{Decimal(str(valor)):.4f}"
