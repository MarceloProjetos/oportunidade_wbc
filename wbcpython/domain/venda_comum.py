"""What quotation and order really have in common.

This module holds **only** what is identical in both documents. Everything that differs lives in
`domain.cotacao` and `domain.pedido`, kept apart on purpose: in the legacy they are two distinct code
blocks with different field sets, and treating them as "one document with a type parameter" is exactly
what made the solution send quotation fields on the order and vice versa.

Three things live here:

1. **Formatting and field limits**: the value UDFs have tight sizes in the SAP and formats that are not
   all alike.
2. **`campos_comuns`**: the handful of fields both documents carry identically (partner, branch, link to
   the quote snapshot, salesperson, contact).
3. **Nothing else.** A field that applies to one document only does not belong here, even if it "almost"
   fits both.
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
    """Number without spare decimals: `4`, `4.5`, `3`.

    It is the format `U_INO_COM` is stored in on production (sample of real orders: `'3'`, `'4'`, `'4.5'`),
    not the four-decimal one used by the value fields. They are different UDFs with different formats;
    unifying them would make the field differ from what the sales team is used to seeing.
    """
    numero = Decimal(str(valor)).normalize()
    return f"{numero:f}"


def valor_texto(valor: Any, limite: int) -> str:
    """Value as text with four decimals, shortened until it fits the field.

    The base format is the same as `formatar_para_sap`, which is how the legacy quotations are stored
    (`'0.0000'`, `'113026.8800'`).

    The adjustment exists because `U_INO_ValorEmbalagem` has only 10 characters: a R$ 100,000.00 packaging
    would give `'100000.0000'` (11), and the SAP would refuse the whole document over an informational field.
    Decimals are then dropped (4, then 2, then 0): display precision is lost, never the document. If even the
    integer does not fit, the value is truncated, because the alternative is no document at all.
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
    """The fields quotation and order carry identically.

    `CardCode` is **mandatory**: without it the SAP refuses with `-2028 Customer record not found` (verified
    against the real environment).

    `BPL_IDAssignedToInvoice` (the branch) is also required: without it the SAP refuses with `-5002 Specify
    an active branch [OQUT.BPLId]`. The legacy hardcoded it as `1` (`ServiceProcess.cs:298` and `:1149`);
    here it is a parameter, whose default in the application is the same `1`, the only active branch in
    homologation.

    `U_INO_ORCAMENTO` receives the `DocEntry` of the OrcDetalhe snapshot written moments before: the link
    from the document back to the quote snapshot that originated it. It is a **numeric** field in the SAP
    (sending `''` returns `SAP 205 - the given value('') of property 'U_INO_ORCAMENTO' is not a NUMBER`), so
    it is sent only when there is a value: omitting differs from sending empty.

    Salesperson and contact come from the opportunity itself, as in the legacy (which read them with
    `oport.GetByKey`); here they already came in the query, with no extra SAP round trip. On a partner swap
    (`trocando_de_parceiro`) the contact is omitted, because it belongs to the old partner.
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
        vendedor = _codigo_ou_none(oportunidade.get("SalesPerson"))
        contato = _codigo_ou_none(oportunidade.get("ContactPerson"))
        if vendedor:
            payload["SalesPersonCode"] = vendedor
        # O contato pertence ao parceiro da oportunidade. Numa troca de PN ele
        # não existe no parceiro novo, e o SAP recusa o documento inteiro com
        # `-5002 Invalid contact person code [OQUT.CntctCode]`. Melhor
        # documento sem contato do que documento nenhum.
        if contato and not trocando_de_parceiro:
            payload["ContactPersonCode"] = contato

    return payload


def _codigo_ou_none(valor: Any) -> int | None:
    """SAP code or `None`: the SAP returns `0`, `''` and `None` for "has none".

    Not the `_inteiro` of the other modules (processar, cli, previsao, hana): here **0 becomes None**, there
    it does not. The different name is so nobody swaps one for the other.
    """
    try:
        numero = int(valor)
    except (TypeError, ValueError):
        return None
    return numero or None
