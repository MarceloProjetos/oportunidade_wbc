"""Common engine for the lines (`DocumentLines`) of a sales document.

This module resolves **what every line has**: which SAP item, how many, for how much, in which warehouse.
The UDFs that differ between quotation and order are not here: each document has its own module
(`domain.cotacao`, `domain.pedido`) and passes its own fields through `udfs_da_linha`.

The split is deliberate. While both documents shared one function with a type parameter, it was easy to
send `U_INO_Composicao` on the quotation, where production never writes it and where it **appears on the
customer's printout**.

Pure domain: takes the quote and the already-loaded `de_para` and returns the lines and the warnings. It
knows nothing of Service Layer, HTTP or database, so each business rule is testable with no external
system.

The three decisions this rule embodies were taken by the business:

1. **Quantity 1** when `ORCPRDQTD` is empty, null, inconsistent or zero, except on a **porta-paletes**
   (pallet rack) line, where the quantity is read from the text (see `quantidade_no_texto`). `ORCPRDQTD`
   is null on all 20,997 WBC lines, and the PORTA-PALETES item used to be born in the SAP as 1 unit when
   the text said "14 Módulos" (report of 15/09/2026).
2. **Fallback to Porta-Paletes** (group `2`) when the group is not in the `de_para`, **with a log
   warning**, never silently.
3. **Warehouse `08`** on every line.

**The line value goes as `LineTotal`, not `Price`.** `ORCVAL` is the line total. With quantity 1,
sending `Price = ORCVAL` was the same; with 272 modules, `Price = ORCVAL / 272` has 4 decimals in the SAP
and the recomputed total diverges by 1 cent (`272 x 2600.0071 = 707,201.93` against `707,201.92`; 3 of
the 18 lines of the homologation rehearsal). Sending the total, the SAP derives the price and the total
matches by construction. `MeasureUnit` is **not** sent: the unit is the item master's; the "CJ" line was
implemented and undone at the business's request.

**The weight is sent on the order and not on the quotation**, and that is from the legacy, not an
oversight. `ServiceProcess.cs:640` writes `Weight1` on the order; on the quotation the same line exists
**commented out** (`:377`). The quotation keeps going out with the item master's weight
(`SalesUnitWeight`, which is `1.0` on the `I00000x` items).

So the weight enters through `pesos`, an optional parameter: `domain.pedido` passes it, `domain.cotacao`
does not. The decision is readable in the signature of the two functions instead of hidden behind an
`if tipo == PEDIDO`. `Weight1` is the WBC tree's level-1 net weight plus 10% (see `peso_da_linha`).

An earlier note in this module said the weight did not come from WBC and that the fix belonged in the
item master in the SAP. It was wrong: the weight comes from `INTEGRACAO_ORCPRDARV`, summing **level 1**
per `ORCITM` (see `queries.PESOS_NIVEL_1_POR_ITEM`). What the old note looked at was the `ORCPES` of
`INTEGRACAO_ORCPRD`, a different table that really exists for only a fraction of the quotes.

On point 2: the legacy treated an unknown group in two incompatible ways. At `ServiceProcess.cs:1223`
and `:1256` it fell back to group `2` without recording anything; at `:356` and `:626` it **dropped the
whole line**, also without recording. The second is the worse one: a quote reached the SAP with a lower
value than in WBC and nobody knew. Here there is a single path, and it is noisy.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

#: Tema das linhas de log desta regra. O painel colore a linha que começa com
#: `[porta-paletes]`, para que a quantidade lida (ou não lida) se destaque no
#: meio do ciclo — foi o pedido de quem acompanha a integração.
TEMA_PORTA_PALETES = "porta-paletes"

#: "porta-paletes" em qualquer grafia: hífen, espaço ou nada, singular ou
#: plural. Aplicado sobre o texto já sem acento e em minúsculas.
_PORTA_PALETES = re.compile(r"porta[\s\-]*paletes?")

#: "de porta-paletes", "para porta-paletes", "tipo porta-paletes": o texto
#: descreve um acessório (stop, coluna, guia, protetor), não a estrutura.
_ACESSORIO_DE_PORTA_PALETES = re.compile(
    r"\b(de|do|da|dos|das|para|p/|tipo|em|no|na|com|mini)\s+porta[\s\-]*paletes?$"
)

#: "N palavra" — o começo de uma descrição ("24 stops", "60 planos"). Se
#: aparece **antes** de porta-paletes, a linha é de outra coisa que só cita
#: porta-paletes ("PLANOS METÁLICOS 60 Planos ... tipo porta-paletes").
_NUMERO_E_PALAVRA = re.compile(r"\b\d+\s*([a-z]+)")

#: Palavras que, depois de um número, ainda são rótulo e não descrição:
#: "2000 kgf", "232 m", "2a fase". Duas letras ou menos passam sem lista.
_MEDIDAS = frozenset({"kgf", "kgs", "mm", "cm", "ton", "und", "conj"})

#: O inteiro imediatamente anterior a "modulo(s)".
_NUMERO_DE_MODULOS = re.compile(r"\b(\d+)\s*modulos?\b")

#: Grupo usado quando o grupo da linha não existe no de-para.
#: `2` é Porta-Paletes — decisão do negócio, herdada do legado.
GRUPO_FALLBACK = "2"

#: Depósito aplicado a toda linha de documento.
DEPOSITO_PADRAO = "08"

#: Marcador que o legado usa para cortar o texto ao montar `U_INO_Composicao`.
MARCADOR_COMPOSICAO = "Valor"


@dataclass(frozen=True, slots=True)
class Nota:
    """A log line about what the engine did with a quote line.

    Not an integration warning: it does not go to the tracking and does not flag the quote. It goes to the log,
    with a theme, so whoever follows the cycle sees what the SAP will receive. `atencao` raises the level to
    WARNING, for a porta-paletes without "N Módulos" in the text, where quantity 1 is a fallback and not a
    reading.
    """

    texto: str
    atencao: bool = False


@dataclass(frozen=True, slots=True)
class ResultadoLinhas:
    """Lines ready for the SAP, plus what had to be worked around.

    `avisos` is what someone must check and stays recorded in the quote's tracking (group missing from the
    `de_para`, including the case where the fallback group is missing too and the line is not sent). `notas`
    is what the engine decided on purpose and deserves to stay visible in the log (the quantity read from the
    text, or its absence), without becoming a quote error.
    """

    linhas: tuple[dict[str, Any], ...] = ()
    avisos: tuple[str, ...] = ()
    notas: tuple[Nota, ...] = ()
    grupos_desconhecidos: frozenset[int] = field(default_factory=frozenset)

    @property
    def vazio(self) -> bool:
        return not self.linhas


def _sem_acento(texto: str) -> str:
    decomposto = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in decomposto if not unicodedata.combining(c)).lower()


def _inicio_de_porta_paletes(texto_normalizado: str) -> int | None:
    """Position where the porta-paletes structure ends in the text, or `None`.

    Despite the name, it returns the end of the matched name (`encontrado.end()`), where the search for
    "N Módulos" starts. Three things say the line is **not** porta-paletes even though it cites the name:

    * the name follows a preposition such as "de/para/tipo" (see `_ACESSORIO_DE_PORTA_PALETES`): it is an
      accessory ("STOPS TRASEIROS PARA PORTA-PALETES", "COLUNAS DE PORTA-PALETES");
    * the description already started before it ("PLANOS METÁLICOS 60 Planos ... do tipo porta-paletes"): an
      "N word" before the name is the signal;
    * the name does not appear.

    What **may** come before is a label: "ÁREA: SECA PORTA-PALETES 226 Módulos", "ITEM 02 - PORTA-PALETES 04
    Módulos", "OPÇÃO 02 PORTA-PALETES". Measured on the whole base (15/09/2026): the label before the name
    exists in 355 of the 6,299 lines read; requiring the name at the start would leave them at 1, silently.
    """
    encontrado = _PORTA_PALETES.search(texto_normalizado)
    if encontrado is None:
        return None
    antes = texto_normalizado[: encontrado.start()]
    for par in _NUMERO_E_PALAVRA.finditer(antes):
        palavra = par.group(1)
        if len(palavra) > 2 and palavra not in _MEDIDAS:
            return None
    if _ACESSORIO_DE_PORTA_PALETES.search(texto_normalizado[: encontrado.end()]):
        return None
    return encontrado.end()


def eh_porta_paletes(texto: str) -> bool:
    """Whether the line text describes the porta-paletes structure, in any spelling.

    `PORTA-PALETES`, `Porta palete`, `porta paletes`, `PORTAPALETE`: hyphen, space or nothing, singular or
    plural, any case. This is the identification that matters for the quantity, **by text, not by group**:
    there are porta-paletes with `GRPCOD` 16, which go to another SAP item. An accessory that cites
    porta-paletes does not count (see `_inicio_de_porta_paletes`).
    """
    return _inicio_de_porta_paletes(_sem_acento(texto)) is not None


def quantidade_no_texto(texto: str) -> int | None:
    """Number of modules written in the text of a porta-paletes line.

    It is the **integer immediately before the first occurrence of "Módulo(s)"** after "porta-paletes",
    compared without accents and case. Returns `None` when the text is not porta-paletes, has no "Módulo", or
    the number is zero; the caller decides what to do (quantity 1, with a note).

    The rule arrived as "the first PORTA-PALETES word followed by a number", and would have read 1 in these
    two real texts:

        PORTA-PALETES ÁREA 1 10 Módulos...     -> 10, not 1 (the 1 is the area)
        PORTA-PALETES - OPÇÃO 1 14 Módulos...  -> 14, not 1 (the 1 is the option)

    Anchoring on "Módulo" solves both. It only applies to porta-paletes: a shelving unit with "12 Módulos"
    stays at quantity 1, with no note; that was the 15/09/2026 decision, so as not to change what was never
    asked.

    Measured on the whole WBC base (21,447 lines, 15/09/2026): 6,442 porta-paletes lines, **6,299 read
    (97.8%)**, 143 without a number (joints, columns, protectors and loose material, where 1 is right). Known
    limit: four lines "01 conjunto ... composto por N montantes ... para M módulos" read M. The line total
    does not depend on it (`LineTotal`).
    """
    normalizado = _sem_acento(texto)
    inicio = _inicio_de_porta_paletes(normalizado)
    if inicio is None:
        return None
    encontrado = _NUMERO_DE_MODULOS.search(normalizado, inicio)
    if encontrado is None:
        return None
    return int(encontrado.group(1)) or None


def composicao(texto: str) -> str:
    """Part of the line text that goes to `U_INO_Composicao`.

    The legacy cuts at the first "Valor": the composition description comes before it, the values after. If
    the marker does not appear, the whole text goes.

    It also reproduces a detail of the original: the cut only happens if the marker is **after** the start
    (`IndexOf(...) > 0`). A text that *begins* with "Valor" is not cut, because cutting would leave the
    composition empty.

    The trailing `rstrip` is what the legacy write does through the DI-API: the cut before "Valor" leaves
    spaces, and the production documents do not have them. Through the Service Layer they would pass straight
    to the customer's printout.
    """
    posicao = texto.find(MARCADOR_COMPOSICAO)
    return (texto[:posicao] if posicao > 0 else texto).rstrip()


def resolver_linhas(
    orcamento: Any,
    de_para: dict[str, Any],
    *,
    udfs_da_linha: Callable[[Any, Any], dict[str, Any]],
    deposito: str = DEPOSITO_PADRAO,
    pesos: Mapping[int, Decimal] | None = None,
) -> ResultadoLinhas:
    """Build `DocumentLines` from the quote's items.

    `de_para` maps group code (text) to something with `.item_sap`. A group missing from it falls back to
    `GRUPO_FALLBACK`, with a warning; if that is missing too, the line is skipped and a warning says so.

    `udfs_da_linha(item, orcamento)` returns the UDFs specific to the document being built. It is the point
    where quotation and order diverge, and the only one.

    `pesos` maps `ORCITM` to the item's **net** weight (tree level 1). Only the order passes it. An item
    missing from the dict **does not receive the field**: the SAP keeps the item master's weight, which is
    today's behavior. Sending zero would swap one wrong number for another, and a shipping report could not
    tell "unknown" from "weighs nothing". The computation is in `peso_da_linha`.
    """
    linhas: list[dict[str, Any]] = []
    avisos: list[str] = []
    notas: list[Nota] = []
    desconhecidos: set[int] = set()

    fallback = de_para.get(GRUPO_FALLBACK)

    for item in orcamento.itens:
        chave = str(item.grupo)
        grupo = de_para.get(chave)

        if grupo is None:
            desconhecidos.add(item.grupo)
            if fallback is None:
                # Sem grupo e sem fallback não há item nenhum a enviar. Pular a
                # linha seria repetir o defeito do legado, então isto vira aviso
                # e a ausência da linha fica explícita para quem ler o log.
                avisos.append(
                    f"Item {item.orcitm}: grupo {item.grupo} não está no de-para "
                    f"e o grupo de fallback {GRUPO_FALLBACK} também não — "
                    "linha não enviada."
                )
                continue
            grupo = fallback
            avisos.append(
                f"Item {item.orcitm}: grupo {item.grupo} não está no de-para "
                f"@INO_GRP_PRODUTOS; usado o item do grupo {GRUPO_FALLBACK} "
                f"({grupo.item_sap}). Confira o cadastro."
            )

        quantidade = item.quantidade_para_documento
        _anotar_quantidade(item, quantidade, notas)

        linha = {
            "ItemCode": grupo.item_sap,
            "Quantity": float(quantidade),
            # `LineTotal`, e não `Price`. `ORCVAL` é o total da linha, e é o
            # total que precisa bater com o WBC ao centavo — o preço unitário
            # é derivado, e quem deriva é o SAP. Mandar `Price = ORCVAL ÷ qtd`
            # foi tentado: o SAP guarda o preço com 4 casas e recalcula o total,
            # e 3 das 18 linhas do ensaio ficaram 1 centavo fora
            # (`272 × 2600,0071 = 707.201,93` contra `707.201,92`).
            #
            # Histórico: até 15/09/2026 a linha levava `Price` (o líquido, que
            # força o valor — diferente de `UnitPrice`, o bruto sobre o qual o
            # SAP aplica desconto de parceiro; o legado usa `Price`,
            # `ServiceProcess.cs:372`). Com quantidade sempre 1, `Price` e
            # `LineTotal` eram o mesmo número. Vai um campo só, de propósito:
            # com os dois, o Service Layer escolhe qual prevalece, e a versão
            # da .11 já mostrou comportamento irregular no PATCH.
            "LineTotal": float(item.valor),
            "WarehouseCode": deposito,
            "U_INO_ORCITM": str(item.orcitm),
            # `rstrip`: o texto do WBC vem com espaços à direita, e a produção os
            # grava sem. Não é suposição — o pedido 84316 (produção) e o 84315
            # (homologação) são o mesmo orçamento, criados no mesmo dia com 24
            # minutos de diferença: o de produção tem 0 espaços no fim, o nosso
            # tinha 1 e 4. Quem apara é a gravação do DI-API; pelo Service Layer
            # o espaço passa direto, e o campo aparece na impressão do cliente.
            "U_INO_D_Adicionais": item.texto.rstrip(),
        }

        peso = _peso_da_linha(item, pesos)
        if peso is not None:
            # `Weight1` é o peso **da linha inteira**, e o SAP o grava como vem —
            # não multiplica pela quantidade. Medido no pedido 84407 (22/09/2026):
            # 167 módulos, e a versão 1 do histórico (`ADO1`, gravada pelo
            # `orcaview`) tem `Weight1 = 137`, o peso da árvore dividido por 167.
            # O usuário corrigiu à mão para 22.879 no dia seguinte.
            #
            # Até então a linha levava `peso ÷ quantidade`, copiado do legado
            # (`ServiceProcess.cs:640`), onde a quantidade era sempre 1 e a
            # divisão nunca fazia efeito. Passou a fazer em 15/09/2026, quando o
            # porta-paletes começou a ler "N Módulos" do texto.
            linha["Weight1"] = float(peso)
        linha.update(udfs_da_linha(item, orcamento))
        linhas.append(linha)

    return ResultadoLinhas(
        linhas=tuple(linhas),
        avisos=tuple(avisos),
        notas=tuple(notas),
        grupos_desconhecidos=frozenset(desconhecidos),
    )


def _anotar_quantidade(item: Any, quantidade: Decimal, notas: list[Nota]) -> None:
    """Record where the quantity of a porta-paletes line came from.

    Three outcomes, only for porta-paletes (other lines stay silent, as they always did):

    * `ORCPRDQTD` filled in: nothing to say, the column won (precedence).
    * Number read from the text: a note, with the derived unit price, so whoever follows the log sees what
      the SAP will receive.
    * No number: a note **with attention** (WARNING in the log). Reading the 143 such lines in the base
      showed joints, columns, protectors and loose material; quantity 1 is right, but whoever checks needs to
      know it was the fallback and not a reading. It does not become a quote error.
    """
    if not eh_porta_paletes(item.texto):
        return
    if item.quantidade is not None and item.quantidade > 0:
        return
    lida = quantidade_no_texto(item.texto)
    if lida is None:
        notas.append(
            Nota(
                f"[{TEMA_PORTA_PALETES}] Item {item.orcitm}: texto de porta-paletes sem "
                f'"N Módulos" — quantidade 1, LineTotal R$ {_reais(item.valor)}. '
                f"Texto: {item.texto.strip()[:80]!r}",
                atencao=True,
            )
        )
        return
    notas.append(
        Nota(
            f"[{TEMA_PORTA_PALETES}] Item {item.orcitm}: {lida} módulos lidos do texto → "
            f"Quantity {quantidade:g}, LineTotal R$ {_reais(item.valor)} "
            f"(unitário R$ {_reais(item.preco_unitario, casas=4)})"
        )
    )


def _reais(valor: Decimal, *, casas: int = 2) -> str:
    """`707201.92` -> `707.201,92`, as a person reads it on the SAP screen."""
    return f"{valor:,.{casas}f}".replace(",", "\0").replace(".", ",").replace("\0", ".")


def _peso_da_linha(item: Any, pesos: Mapping[int, Decimal] | None) -> Decimal | None:
    """Weight of the item's line, or `None` when unknown."""
    if not pesos:
        return None
    return peso_da_linha(pesos.get(item.orcitm))


#: The order line weighs 10% more than the tree's net weight (packaging). A business rule
#: confirmed by Marcelo on 29/09/2026 — not configuration: no `.env` key changes it.
FATOR_DE_EMBALAGEM = Decimal("1.10")


def peso_da_linha(liquido: Decimal | None) -> Decimal | None:
    """`Weight1` of the whole line: tree level 1 × 1.10, or `None` when unknown.

    Rule confirmed by Marcelo on 29/09/2026: the WBC net weight plus 10%, rounded to
    2 decimals — no truncation (quote 00125817: 226.43 kg in the tree → 249.07 kg on the
    line). Until that day it was truncated to an integer (249).

    Used by order creation and by `wbcpython pesos`; the two cannot diverge.

    **Not divided by the quantity**: `Weight1` is the line total and the SAP does not
    multiply it — see the comment in `resolver_linhas`.

    `None` and zero differ: `None` means the field is not sent and the SAP keeps the item
    master's weight. Missing, zero or negative weight in the tree becomes `None`.
    """
    if liquido is None or liquido <= 0:
        return None
    arredondado = (Decimal(liquido) * FATOR_DE_EMBALAGEM).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return arredondado if arredondado > 0 else None
