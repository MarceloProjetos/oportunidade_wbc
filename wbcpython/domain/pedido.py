"""Everything about the **sales order** (`pedido`): creation and maintenance.

Mirror module of `domain.cotacao`. What is here holds for the order and only for the order, and the list
of differences is not small.

**Header** (`ServiceProcess.cs:294-344`):

| Field | WBC source |
|---|---|
| `DocDueDate` | today + `PRZENT` |
| `Comments` | raw `PGTCOD`, cut at 150 |
| `U_INO_TIPO_MT` | `TIPMONCOD` |
| `U_INO_VL_MT` | **`ORCBAS3`** |
| `U_INO_COM` | `ORCPERCOM` |
| `U_INO_VL_COM` | `ORCVALCOM` |
| `TaxExtension.MainUsage` + line `Usage` | fixed `9`, **on creation only** (`com_uso_principal`) |

**Lines**: besides `U_INO_ORCITM` and `U_INO_D_Adicionais`, the order carries `U_INO_Composicao` and
`U_INO_Id_IntWBC` (`:378-395`, `:653-667`) and does **not** carry `U_INO_ACAB`. Of the 16,242 WBC order
lines in production, only 6 have `U_INO_ACAB` and 16,088 have `U_INO_Composicao`.

**What the order lacks** and the quotation has: `U_INO_PrazoEntrega`, `U_INO_ValorTransp`,
`U_INO_ValorEmbalagem`, `U_INO_PessoaContato`, `U_INO_CondPag` and `U_INO_Montagem`. Checked: in 4,431 of
the 4,432 WBC orders in production these fields are empty.

**Two traps** that the split makes visible:

1. `U_INO_VL_MT` comes from `ORCBAS3` here and from `ORCVALMON` on the quotation (`:281` against
   `:1180`). Different columns of the same record. There is no way to know whether it is intentional; both
   behaviors were preserved and the question is in `RETOMADA.md`.
2. The payment condition goes **raw** into `Comments`, without the swap of `|` and `;` for line breaks
   that the quotation applies to `U_INO_CondPag`.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from wbcpython.domain.linhas import (
    DEPOSITO_PADRAO,
    ResultadoLinhas,
    composicao,
    resolver_linhas,
)
from wbcpython.domain.venda_comum import (
    LIMITE_COM,
    LIMITE_COMENTARIO,
    campos_comuns,
    numero_curto,
)


def comentario(texto: str) -> str:
    """`Comments` of the order: the raw `PGTCOD`, cut at 150 characters.

    The source code we have does more: after cutting, it splits the text into words and applies `Distinct()`
    (`ServiceProcess.cs:332-334`), which would delete repeated words mid-sentence. **Production does not do
    that.**

    Order 19500 (quote `00125401`) has `Comments` identical, character by character, to the WBC `PGTCOD`,
    including the repeated "da" in "da mercadoria" and "da emissão" that `Distinct()` would have eaten. The
    same holds for the other orders checked.

    So the binary running in production **is not** this source. Between reproducing the source and reproducing
    the observed behavior, the observed behavior wins: it is what the sales team reads on the printout today.
    The divergence is recorded in `RETOMADA.md`.
    """
    return texto[:LIMITE_COMENTARIO]


def cabecalho(impressao: Any, *, hoje: date | None = None) -> dict[str, Any]:
    """Order header: the `ServiceProcess.cs:294-344` set.

    Two of the fields are not UDFs, which is why they escaped the sweeps that searched for `U_INO_`:

    * `DocDueDate` = today + `PRZENT` (`:294`). Checked in production: on the 4,432 WBC orders,
      `DocDueDate - DocDate` is exactly the delivery term, always.
    * `Comments` = the payment condition (`:327-331`), filled in 4,328 of them.
    """
    vencimento = (hoje or date.today()) + timedelta(  # noqa: DTZ011
        days=impressao.prazo_entrega
    )
    return {
        "DocDueDate": vencimento.isoformat(),
        "Comments": comentario(impressao.pagamento_codigo),
        "U_INO_TIPO_MT": impressao.montagem_tipo,
        # `VL_MT` e `VL_COM` são `db_Float`; `COM` é texto de 10 caracteres.
        "U_INO_VL_MT": float(impressao.base3),
        "U_INO_COM": numero_curto(impressao.percentual_comissao)[:LIMITE_COM],
        "U_INO_VL_COM": float(impressao.valor_comissao),
    }


def _udfs_da_linha(item: Any, orcamento: Any) -> dict[str, Any]:
    """`U_INO_Composicao` and `U_INO_Id_IntWBC`: the order's two line UDFs."""
    del orcamento  # tudo vem da própria linha
    return {
        "U_INO_Composicao": composicao(item.texto),
        "U_INO_Id_IntWBC": str(item.id_integracao),
    }


def linhas(
    orcamento: Any,
    de_para: dict[str, Any],
    *,
    deposito: str = DEPOSITO_PADRAO,
    pesos: Mapping[int, Decimal] | None = None,
) -> ResultadoLinhas:
    """`DocumentLines` of the order.

    `pesos` (`ORCITM` -> level-1 net weight of the tree) becomes `Weight1` on the line, already through
    `domain.linhas.peso_da_linha` (net weight + 10%, rounded to 2 decimals). This is what distinguishes this
    function from its `domain.cotacao` counterpart, which has no such parameter: the legacy writes the weight
    on the order and leaves the quotation's line commented out (`ServiceProcess.cs:640` against `:377`).

    Without `pesos`, the order goes out as before, with the item master's weight.
    """
    return resolver_linhas(
        orcamento,
        de_para,
        udfs_da_linha=_udfs_da_linha,
        deposito=deposito,
        pesos=pesos,
    )


#: SAP B1 Brazil "Uso principal" for every order the integration creates: ``OUSG.ID`` 9,
#: "Venda Prod. Própria" (Marcelo, 13/10/2026). Not left to the partner default
#: (``OCRD.MainUsage``): 195 customers have none, and order 84451 (30/09/2026) was
#: born with an empty usage on the header and on its line, and so with no tax code.
#: Homolog test 13/10/2026 (order 84398, same customer, cancelled): 9 on both levels,
#: CFOP 5101 and tax code 5101#013, the code 410 of 463 production 5101 lines carry.
USO_PRINCIPAL = 9


def com_uso_principal(dados: dict[str, Any]) -> dict[str, Any]:
    """Copy of an order body with the main usage set, for **creation only**.

    Two places, because SAP keeps two: the header (``RDR12.MainUsage``, Service Layer
    ``TaxExtension.MainUsage``) and each line (``RDR1.Usage``, the one that drives CFOP
    and tax code). Setting only the header through the Service Layer does not reach
    the lines. An update never gets this: a usage changed by hand on an existing order
    (e.g. 85 for a remittance) stays as the person left it.
    """
    novo = dict(dados)
    novo["TaxExtension"] = {**(dados.get("TaxExtension") or {}), "MainUsage": USO_PRINCIPAL}
    if dados.get("DocumentLines"):
        novo["DocumentLines"] = [
            {**linha, "Usage": USO_PRINCIPAL} for linha in dados["DocumentLines"]
        ]
    return novo


def montar_payload(
    orcamento: Any,
    *,
    parceiro: str | None,
    filial: int,
    snapshot_id: int | None = None,
    oportunidade: dict[str, Any] | None = None,
    trocando_de_parceiro: bool = False,
    hoje: date | None = None,
) -> dict[str, Any]:
    """Order body, without the lines. See the note in `domain.cotacao.montar_payload`."""
    payload = campos_comuns(
        orcamento,
        parceiro=parceiro,
        filial=filial,
        snapshot_id=snapshot_id,
        oportunidade=oportunidade,
        trocando_de_parceiro=trocando_de_parceiro,
    )
    payload.update(cabecalho(orcamento.impressao, hoje=hoje))
    return payload
