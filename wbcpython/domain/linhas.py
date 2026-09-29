"""Motor comum das linhas de documento de venda (`DocumentLines`).

Este módulo resolve **o que toda linha tem**: qual item do SAP, quanto, por
quanto, em que depósito. Os UDFs que variam entre cotação e pedido não estão
aqui — cada documento tem o seu módulo (`domain.cotacao`, `domain.pedido`) e
passa os próprios campos por `udfs_da_linha`.

A separação é deliberada. Enquanto os dois documentos dividiam uma função com
um parâmetro de tipo, foi fácil mandar `U_INO_Composicao` na cotação (onde
produção nunca o grava, e onde ele **aparece na impressão do cliente**).

Domínio puro: recebe o orçamento e o de-para já carregado, devolve as linhas e
os avisos. Não conhece Service Layer, HTTP nem banco — o que permite testar
cada regra de negócio sem nenhum sistema externo.

As três decisões que esta regra materializa foram tomadas pelo negócio:

1. **Quantidade 1** quando `ORCPRDQTD` for vazia, nula, inconsistente ou zero —
   exceto na linha de **porta-paletes**, onde a quantidade é lida do texto
   (ver `quantidade_no_texto`). `ORCPRDQTD` é nula nas 20.997 linhas do WBC, e
   o item PORTA-PALETES nascia no SAP como 1 unidade quando o texto dizia
   "14 Módulos" (relato de 15/09/2026).
2. **Fallback para Porta-Paletes** (grupo `2`) quando o grupo não estiver no
   de-para — **com aviso em log**, nunca em silêncio.
3. **Depósito `08`** em toda linha.

**O valor da linha vai como `LineTotal`, não como `Price`.** `ORCVAL` é o total
da linha. Com quantidade 1, mandar `Price = ORCVAL` dava no mesmo; com 272
módulos, `Price = ORCVAL ÷ 272` tem 4 casas no SAP e o total recalculado
diverge 1 centavo (`272 × 2600,0071 = 707.201,93` contra `707.201,92` — 3 das
18 linhas do ensaio de homologação). Enviando o total, o SAP deriva o preço e o
total bate por construção. `MeasureUnit` **não** é enviado: a unidade é a do
cadastro do item — a linha com "CJ" foi implementada e desfeita a pedido do
negócio.

**O peso é enviado no pedido e não na cotação** — e isso é do legado, não
descuido. `ServiceProcess.cs:640` grava `Weight1` no pedido; na cotação a
mesma linha existe **comentada** (`:377`). A cotação continua saindo com o peso
do cadastro do item (`SalesUnitWeight`, que vale `1.0` nos itens `I00000x`).

Por isso o peso entra por `pesos`, um parâmetro opcional: `domain.pedido` o
passa, `domain.cotacao` não. A decisão fica legível na assinatura das duas
funções, em vez de escondida atrás de um `if tipo == PEDIDO`.

Uma nota anterior deste módulo dizia que o peso não vinha do WBC e que o lugar
de corrigir seria o cadastro do item no SAP. Estava errada: vem de
`INTEGRACAO_ORCPRDARV`, somando o **nível 1** por `ORCITM` — ver
`queries.PESOS_NIVEL_1_POR_ITEM`. O que a nota antiga olhou foi o `ORCPES` de
`INTEGRACAO_ORCPRD`, que é outra tabela e de fato só existe para uma fração dos
orçamentos.

O ponto 2 merece um comentário. O legado tratava o grupo desconhecido de duas
formas incompatíveis: em `ServiceProcess.cs:1223` e `:1256` caía no grupo `2`
sem registrar nada; em `:356` e `:626` **descartava a linha inteira**, também
sem registrar. A segunda é a pior das duas — um orçamento chegava ao SAP com
valor menor que o do WBC e ninguém ficava sabendo. Aqui há um caminho só, e ele
é ruidoso.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Protocol

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

class ItemComGrupo(Protocol):
    """O mínimo que uma linha de orçamento precisa oferecer."""

    orcitm: int
    grupo: int
    texto: str
    valor: Decimal
    quantidade: Decimal | None
    id_integracao: int

    @property
    def quantidade_para_documento(self) -> Decimal: ...

    @property
    def preco_unitario(self) -> Decimal: ...


@dataclass(frozen=True, slots=True)
class Nota:
    """Uma linha de log sobre o que o motor fez com uma linha do orçamento.

    Não é aviso de integração: não vai para o acompanhamento e não marca o
    orçamento. Vai para o log, com tema, para quem acompanha o ciclo ver o que
    o SAP vai receber. `atencao` sobe o nível para WARNING — é o caso da
    porta-paletes sem "N Módulos" no texto, onde a quantidade 1 é o fallback e
    não uma leitura.
    """

    texto: str
    atencao: bool = False


@dataclass(frozen=True, slots=True)
class ResultadoLinhas:
    """Linhas prontas para o SAP, mais o que precisou ser contornado.

    `avisos` é o que alguém precisa conferir e fica registrado no
    acompanhamento do orçamento (grupo fora do de-para). `notas` é o que o
    motor decidiu de propósito e merece ficar visível no log — a quantidade
    lida do texto, ou a falta dela — sem virar erro do orçamento.
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
    """Posição em que a estrutura porta-paletes começa no texto, ou `None`.

    Três coisas dizem que a linha **não** é de porta-paletes, mesmo citando o
    nome:

    * a palavra vem depois de "de/para/tipo" — é acessório ("STOPS TRASEIROS
      PARA PORTA-PALETES", "COLUNAS DE PORTA-PALETES");
    * a descrição já começou antes dela ("PLANOS METÁLICOS 60 Planos ... do
      tipo porta-paletes") — um "N palavra" antes do nome é o sinal;
    * o nome não aparece.

    O que **pode** vir antes é um rótulo: "ÁREA: SECA PORTA-PALETES 226
    Módulos", "ITEM 02 - PORTA-PALETES 04 Módulos", "OPÇÃO 02 PORTA-PALETES".
    Medido na base inteira (15/09/2026): o rótulo antes do nome existe em 355
    das 6.299 linhas lidas; exigir o nome no início as deixaria em 1, caladas.
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
    """O texto da linha descreve a estrutura porta-paletes, em qualquer grafia.

    `PORTA-PALETES`, `Porta palete`, `porta paletes`, `PORTAPALETE`: hífen,
    espaço ou nada, singular ou plural, com ou sem caixa. É a identificação
    que importa para a quantidade — **pelo texto, não pelo grupo**: há
    porta-paletes com `GRPCOD` 16, que vão para outro item do SAP. Acessório
    que cita porta-paletes não conta (ver `_inicio_de_porta_paletes`).
    """
    return _inicio_de_porta_paletes(_sem_acento(texto)) is not None


def quantidade_no_texto(texto: str) -> int | None:
    """Quantidade de módulos escrita no texto de uma linha de porta-paletes.

    É o **inteiro imediatamente anterior à primeira ocorrência de "Módulo(s)"**
    depois de "porta-paletes", comparando sem acento e sem caixa. Devolve
    `None` quando o texto não é de porta-paletes, não traz "Módulo" ou o
    número é zero — o chamador decide o que fazer (quantidade 1, com nota).

    A regra chegou como "a primeira palavra PORTA-PALETES seguida de um
    número", e teria lido 1 nestes dois textos reais:

        PORTA-PALETES ÁREA 1 10 Módulos...     → 10, e não 1 (o 1 é da área)
        PORTA-PALETES - OPÇÃO 1 14 Módulos...  → 14, e não 1 (o 1 é da opção)

    Ancorar no "Módulo" resolve os dois. Só vale para porta-paletes: uma
    estante com "12 Módulos" continua em quantidade 1, sem nota — foi a
    decisão de 15/09/2026, para não mudar o que nunca foi pedido.

    Medido na base inteira do WBC (21.447 linhas, 15/09/2026): 6.442 linhas de
    porta-paletes, **6.299 lidas (97,8%)**, 143 sem número — junções, colunas,
    protetores e material avulso, onde 1 está certo. Limite conhecido: quatro
    linhas "01 conjunto ... composto por N montantes ... para M módulos" leem
    M. O total da linha não depende disso (`LineTotal`).
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
    """Trecho do texto da linha que vai para `U_INO_Composicao`.

    O legado corta no primeiro "Valor" — a descrição da composição vem antes,
    os valores vêm depois. Se o marcador não aparecer, o texto vai inteiro.

    Reproduz também um detalhe do original: o corte só acontece se o marcador
    estiver **depois** do início (`IndexOf(...) > 0`). Um texto que *comece*
    com "Valor" não é cortado, porque cortar deixaria a composição vazia.

    O `rstrip` no fim é o que a gravação do legado faz por conta do DI-API — o
    corte antes de "Valor" deixa espaços, e os documentos de produção não os
    têm. Pelo Service Layer eles passariam direto para a impressão do cliente.
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
    """Monta `DocumentLines` a partir dos itens do orçamento.

    `de_para` mapeia código de grupo (texto) para algo com `.item_sap`.

    `udfs_da_linha(item, orcamento)` devolve os UDFs específicos do documento
    que está sendo montado. É o ponto onde cotação e pedido divergem — e o
    único.

    `pesos` mapeia `ORCITM` → peso **líquido** do item (nível 1 da árvore). Só o
    pedido o passa. Item ausente do dicionário **não recebe o campo**: o SAP
    mantém o peso do cadastro, que é o comportamento de hoje. Enviar zero
    trocaria um número errado por outro, e um relatório de expedição não teria
    como distinguir "não sei" de "não pesa nada". A conta está em `peso_da_linha`.
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
    """Registra de onde veio a quantidade de uma linha de porta-paletes.

    Três saídas, e só para porta-paletes — as outras linhas seguem caladas,
    como sempre seguiram:

    * `ORCPRDQTD` preenchida: nada a dizer, a coluna venceu (precedência).
    * Número lido do texto: nota, com o unitário derivado, para que quem
      acompanha o log veja o que o SAP vai receber.
    * Sem número: nota **com atenção** (WARNING no log). A leitura das 143
      linhas assim na base mostrou junções, colunas, protetores e material
      avulso — quantidade 1 está certa, mas quem confere precisa saber que
      foi o fallback, e não uma leitura. Não vira erro do orçamento.
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
    """`707201.92` → `707.201,92`, como a pessoa lê na tela do SAP."""
    return f"{valor:,.{casas}f}".replace(",", "\0").replace(".", ",").replace("\0", ".")


def _peso_da_linha(item: Any, pesos: Mapping[int, Decimal] | None) -> Decimal | None:
    """Peso da linha do item, ou `None` quando não se sabe."""
    if not pesos:
        return None
    return peso_da_linha(pesos.get(item.orcitm))


def peso_da_linha(liquido: Decimal | None) -> Decimal | None:
    """`Weight1` of the whole line: the net weight of tree level 1, or `None` when unknown.

    Rule decided by Marcelo on 29/09/2026: the net weight as the WBC gives it, rounded to
    2 decimals (the tree's own precision; the SUM comes back as a float, 226.42999…) —
    no packaging factor and no truncation. Until then the line took net × 1.10, truncated
    (a factor measured on hand-typed weights), and quote 00125817 went out with 249 kg
    for a tree of 226.43 kg.

    Used by order creation and by `wbcpython pesos`; the two cannot diverge.

    **Not divided by the quantity**: `Weight1` is the line total and the SAP does not
    multiply it — see the comment in `resolver_linhas`.

    `None` and zero differ: `None` means the field is not sent and the SAP keeps the item
    master's weight. Missing, zero or negative weight in the tree becomes `None`.
    """
    if liquido is None or liquido <= 0:
        return None
    arredondado = Decimal(liquido).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return arredondado if arredondado > 0 else None
