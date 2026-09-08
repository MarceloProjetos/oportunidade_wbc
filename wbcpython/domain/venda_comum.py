"""O que cotação e pedido têm de fato em comum.

Este módulo existe para conter **só** o que é igual nos dois documentos. Tudo o
que difere mora em `domain.cotacao` e `domain.pedido`, separados de propósito:
no legado são dois blocos de código distintos, com conjuntos de campos
diferentes, e tratá-los como "um documento com um parâmetro de tipo" foi
justamente o que fez a solução mandar campos de cotação no pedido e vice-versa.

Aqui ficam três coisas:

1. **Formatação e limites de campo** — os UDFs de valor têm tamanhos apertados
   no SAP e formatos que não são todos iguais entre si.
2. **`campos_comuns`** — o punhado de campos que os dois documentos levam
   idênticos (parceiro, filial, vínculo com o orçamento, vendedor, contato).
3. **Nada além disso.** Se um campo vale para um documento só, ele não entra
   aqui, mesmo que "quase" sirva para os dois.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

#: Corte do `U_INO_CondPag`, igual ao do legado — e igual ao tamanho do campo.
LIMITE_CONDPAG = 254

#: Onde o texto da montagem é partido entre `U_INO_Montagem` e `...2`.
#: Não é número escolhido: é o tamanho de `U_INO_Montagem` no SAP.
LIMITE_MONTAGEM = 200

#: `U_INO_PessoaContato` tem 20 caracteres no SAP. `CLICON` no WBC é maior, e
#: o legado manda o texto inteiro — o que funciona porque o SAP corta. Cortar
#: aqui deixa explícito o que já acontece.
LIMITE_CONTATO = 20

#: `U_INO_COM` tem 10 caracteres.
LIMITE_COM = 10

#: Corte do `Comments` do pedido, como no legado.
LIMITE_COMENTARIO = 150

#: Tamanhos dos dois UDFs de texto que guardam valor. `U_INO_ValorEmbalagem`
#: tem só 10 caracteres — quatro casas decimais produzem 11 a partir de
#: 100.000, então o valor precisa ser encurtado antes de sair daqui.
LIMITE_VALOR_TRANSP = 20
LIMITE_VALOR_EMBALAGEM = 10


def numero_curto(valor: Any) -> str:
    """Número sem casas decimais sobrando — `4`, `4.5`, `3`.

    É o formato em que `U_INO_COM` está gravado em produção (amostra de pedidos
    reais: `'3'`, `'4'`, `'4.5'`), e não o de quatro casas usado nos campos de
    valor. São UDFs diferentes, com formatos diferentes; unificá-los deixaria o
    campo diferente do que o comercial está acostumado a ver.
    """
    numero = Decimal(str(valor)).normalize()
    return f"{numero:f}"


def valor_texto(valor: Any, limite: int) -> str:
    """Valor como texto, com quatro casas, encurtado até caber no campo.

    O padrão é o mesmo `formatar_para_sap` do resto da solução, que é o formato
    em que as cotações do legado estão gravadas (`'0.0000'`, `'113026.8800'`).

    O ajuste existe porque `U_INO_ValorEmbalagem` tem apenas 10 caracteres: uma
    embalagem de R$ 100.000,00 daria `'100000.0000'`, com 11, e o SAP recusaria
    o documento inteiro por causa de um campo informativo. Nesse caso as casas
    decimais vão sendo cortadas — perde-se precisão de exibição, nunca o
    documento. Se nem o inteiro couber, aí sim o valor vai truncado, porque a
    alternativa é não gravar documento nenhum.
    """
    for casas in (4, 2, 0):
        texto = f"{Decimal(str(valor)):.{casas}f}"
        if len(texto) <= limite:
            return texto
    return texto[:limite]


def campos_comuns(
    orcamento: Any,
    *,
    parceiro: str | None,
    filial: int,
    snapshot_id: int | None = None,
    oportunidade: dict[str, Any] | None = None,
    trocando_de_parceiro: bool = False,
) -> dict[str, Any]:
    """Os campos que cotação e pedido levam iguais.

    `CardCode` é **obrigatório** — sem ele o SAP recusa com
    `-2028 Customer record not found` (verificado contra o ambiente real).

    `BPL_IDAssignedToInvoice` (a filial) também é exigido: sem ele o SAP recusa
    com `-5002 Specify an active branch [OQUT.BPLId]`. O legado o fixava em `1`
    no código (`ServiceProcess.cs:298` e `:1149`); aqui é configurável, mas o
    padrão é o mesmo `1`, a única filial ativa em homologação.

    `U_INO_ORCAMENTO` recebe o `DocEntry` do snapshot do OrcDetalhe gravado
    momentos antes — é o vínculo do documento de volta para o retrato do
    orçamento que o originou. É campo **numérico** no SAP (mandar `''` devolve
    `SAP 205 — the given value('') of property 'U_INO_ORCAMENTO' is not a
    NUMBER`), então só é enviado quando há valor: omitir é diferente de mandar
    vazio.

    Vendedor e contato vêm da própria oportunidade, como no legado — que os lia
    com `oport.GetByKey`. Aqui já vieram na consulta, sem ida extra ao SAP.
    """
    payload: dict[str, Any] = {
        "U_INO_COTWBC": orcamento.orcnum,
        "U_INO_VERSAOWBC": orcamento.revisao,
        "BPL_IDAssignedToInvoice": filial,
    }
    if parceiro:
        payload["CardCode"] = parceiro
    if snapshot_id:
        payload["U_INO_ORCAMENTO"] = snapshot_id

    if oportunidade:
        vendedor = _inteiro(oportunidade.get("SalesPerson"))
        contato = _inteiro(oportunidade.get("ContactPerson"))
        if vendedor:
            payload["SalesPersonCode"] = vendedor
        # O contato pertence ao parceiro da oportunidade. Numa troca de PN ele
        # não existe no parceiro novo, e o SAP recusa o documento inteiro com
        # `-5002 Invalid contact person code [OQUT.CntctCode]`. Melhor
        # documento sem contato do que documento nenhum.
        if contato and not trocando_de_parceiro:
            payload["ContactPersonCode"] = contato

    return payload


def _inteiro(valor: Any) -> int | None:
    """Inteiro tolerante: o SAP devolve `0`, `''` e `None` para "não tem"."""
    try:
        numero = int(valor)
    except (TypeError, ValueError):
        return None
    return numero or None
