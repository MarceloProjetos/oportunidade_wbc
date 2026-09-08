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

1. **Quantidade 1** quando `ORCPRDQTD` for vazia, nula, inconsistente ou zero.
2. **Fallback para Porta-Paletes** (grupo `2`) quando o grupo não estiver no
   de-para — **com aviso em log**, nunca em silêncio.
3. **Depósito `08`** em toda linha.

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

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from decimal import ROUND_FLOOR, Decimal
from typing import Any, Protocol

#: Grupo usado quando o grupo da linha não existe no de-para.
#: `2` é Porta-Paletes — decisão do negócio, herdada do legado.
GRUPO_FALLBACK = "2"

#: Depósito aplicado a toda linha de documento.
DEPOSITO_PADRAO = "08"

#: Marcador que o legado usa para cortar o texto ao montar `U_INO_Composicao`.
MARCADOR_COMPOSICAO = "Valor"

#: Folga sobre o peso líquido da árvore, para chegar ao peso de **embarque**.
#: Ver `Settings.fator_de_peso_de_embarque` — quem manda é a configuração; este
#: valor existe para que o domínio possa ser exercitado sozinho.
FATOR_DE_EMBARQUE = Decimal("1.10")


class ItemComGrupo(Protocol):
    """O mínimo que uma linha de orçamento precisa oferecer."""

    orcitm: int
    grupo: int
    texto: str
    valor: Decimal
    id_integracao: int

    @property
    def quantidade_para_documento(self) -> Decimal: ...

    @property
    def preco_unitario(self) -> Decimal: ...


@dataclass(frozen=True, slots=True)
class ResultadoLinhas:
    """Linhas prontas para o SAP, mais o que precisou ser contornado."""

    linhas: tuple[dict[str, Any], ...] = ()
    avisos: tuple[str, ...] = ()
    grupos_desconhecidos: frozenset[int] = field(default_factory=frozenset)

    @property
    def vazio(self) -> bool:
        return not self.linhas


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
    fator_de_embarque: Decimal = FATOR_DE_EMBARQUE,
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
    como distinguir "não sei" de "não pesa nada".

    `fator_de_embarque` é a folga de embalagem aplicada sobre o líquido — ver
    `_peso_unitario`.
    """
    linhas: list[dict[str, Any]] = []
    avisos: list[str] = []
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

        linha = {
            "ItemCode": grupo.item_sap,
            "Quantity": float(item.quantidade_para_documento),
            # `Price`, não `UnitPrice`. São campos diferentes no SAP: `UnitPrice`
            # é o preço **bruto**, sobre o qual o SAP ainda aplica o desconto do
            # parceiro ou da lista de preços; `Price` é o **líquido**, e força o
            # valor. O legado usa `Price` (`DocCot.Lines.Price = item.OrcVal`,
            # `ServiceProcess.cs:372`), e a diferença aparece no marcador
            # `SpecPrice`: 'R' nos documentos do legado, 'N' nos nossos.
            #
            # Hoje dá no mesmo — nenhum parceiro da integração tem desconto
            # cadastrado, e as 27 linhas geradas em homologação saíram com
            # `Price = PriceBefDi`, impostos e totais idênticos aos da produção.
            # A troca é proteção: no dia em que alguém cadastrar um desconto, o
            # pedido sairia abaixo do valor do orçamento, em silêncio.
            "Price": float(item.preco_unitario),
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

        peso = _peso_unitario(item, pesos, fator_de_embarque)
        if peso is not None:
            # `Weight1` no SAP é o peso **de uma unidade**: o total da linha é
            # ele vezes a quantidade. Por isso a divisão — é o que o legado faz
            # (`Weight1 = soma / item.OrcProdQuantidade`, `ServiceProcess.cs:640`).
            #
            # Hoje a quantidade é sempre 1 (`ORCPRDQTD` é nula em toda a tabela),
            # então dividir ou não dá no mesmo. A diferença aparece no dia em que
            # o WBC preencher a coluna: sem a divisão o SAP multiplicaria de
            # novo, e o peso sairia quantidade vezes maior do que deveria.
            linha["Weight1"] = float(peso)
        linha.update(udfs_da_linha(item, orcamento))
        linhas.append(linha)

    return ResultadoLinhas(
        linhas=tuple(linhas),
        avisos=tuple(avisos),
        grupos_desconhecidos=frozenset(desconhecidos),
    )


def _peso_unitario(
    item: Any, pesos: Mapping[int, Decimal] | None, fator: Decimal
) -> Decimal | None:
    """Peso de **embarque** de uma unidade do item, ou `None` quando não se sabe.

    Três transformações sobre o líquido da árvore, e cada uma tem prova:

    1. **Divide pela quantidade** — `Weight1` é o peso de uma unidade, e o SAP
       multiplica de volta (`ServiceProcess.cs:640`).
    2. **Aplica a folga de embalagem** (`fator`, 1,10 por padrão). Medido em
       1.060 linhas de pedido de 2026 da produção: a razão entre o `Weight1`
       gravado e o líquido da árvore tem mediana **1,099**, com 622 delas entre
       1,09 e 1,11. Varrendo fatores de milésimo em milésimo, o que mais acerta
       é exatamente 1,100.
    3. **Trunca para inteiro.** 1.056 dos 1.061 pesos da produção são inteiros
       redondos — o que a fórmula pura quase nunca produziria. Entre truncar e
       arredondar, truncar acerta mais (36,8% contra 25,1%).

    **A reprodução não é exata, e não tem como ser**: 63% dos casos não seguem
    fórmula nenhuma a partir do retrato ligado ao pedido. O campo é preenchido à
    mão em produção, com uma folga *típica* de 10% — não por uma regra. O que
    esta função garante é a ordem de grandeza certa e um critério único, em vez
    de 1 kg (o padrão do cadastro, em 127 linhas) ou 0 (em 64).

    `None` e zero são coisas diferentes aqui: `None` faz o campo não ser
    enviado. Peso ausente, zero ou negativo na árvore vira `None` — e o
    truncamento que resulta em zero também, porque gravar 0 kg substituiria o
    peso do cadastro por um número pior.
    """
    if not pesos:
        return None
    liquido = pesos.get(item.orcitm)
    if liquido is None or liquido <= 0:
        return None
    embarque = (liquido / item.quantidade_para_documento) * fator
    truncado = embarque.to_integral_value(rounding=ROUND_FLOOR)
    return truncado if truncado > 0 else None
