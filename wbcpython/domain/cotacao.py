"""Everything about the **quotation** (`cotação`): creation and maintenance.

Header, lines and the full payload live here and only here. The order has its mirror module
(`domain.pedido`). The two share nothing beyond `domain.venda_comum`, because in the legacy code they
are two distinct blocks written at different times, and the differences between them are real and
verifiable in production.

**Header** (`ServiceProcess.cs:1173-1189` on create, `:1374-1388` on update):

| UDF | WBC column |
|---|---|
| `U_INO_PessoaContato` | `CLICON` |
| `U_INO_CondPag` | `PGTCOD`, with `\\|` and `;` turned into line breaks, cut at 254 |
| `U_INO_PrazoEntrega` | `PRZENT` |
| `U_INO_ValorTransp` | `ORCVALTRP` |
| `U_INO_ValorEmbalagem` | `ORCVALEMB` |
| `U_INO_VL_MT` | `ORCVALMON` |
| `U_INO_TIPO_MT` | `TIPMONCOD` |
| `U_INO_Montagem` / `U_INO_Montagem2` | `ORCIMP_MONTAGEM`, split at 200 |

**Lines**: besides `U_INO_ORCITM` and `U_INO_D_Adicionais`, the quotation carries `U_INO_ACAB`
(`:1217`, `:1231`, `:1435`, `:1449`) and does **not** carry `U_INO_Composicao` or `U_INO_Id_IntWBC`.
None of the 579,088 WBC quotation lines in production has `U_INO_Composicao`. It matters: that field
shows up in the print view `VW_ORCAMENTO_IMPRESSAO`, so filling it would change what the customer
receives.

**What the quotation lacks** and the order has: `DocDueDate`, `Comments`, `U_INO_COM` and
`U_INO_VL_COM`.
"""

from __future__ import annotations

from typing import Any

from wbcpython.domain.linhas import DEPOSITO_PADRAO, ResultadoLinhas, resolver_linhas
from wbcpython.domain.venda_comum import (
    LIMITE_CONDPAG,
    LIMITE_CONTATO,
    LIMITE_MONTAGEM,
    LIMITE_VALOR_EMBALAGEM,
    LIMITE_VALOR_TRANSP,
    campos_comuns,
    valor_texto,
)


def condicao_de_pagamento(texto: str) -> str:
    """`PGTCOD` in the format the legacy stores in `U_INO_CondPag`.

    WBC joins the instalments with `|` or `;`; the legacy turns both into a line break and cuts at 254
    characters.

    The **order** does it differently: there `PGTCOD` goes raw into `Comments`, with no separator swap. That
    is not our oversight, it is what production shows in both documents.
    """
    return texto.replace("|", "\n").replace(";", "\n")[:LIMITE_CONDPAG]


def partir_montagem(texto: str) -> dict[str, str]:
    """Split the assembly text between `U_INO_Montagem` and `U_INO_Montagem2`.

    The second field is sent only when the text exceeds 200 characters. Sending it always would write an
    empty value over existing content when an existing quotation is updated.
    """
    if len(texto) <= LIMITE_MONTAGEM:
        return {"U_INO_Montagem": texto}
    return {
        "U_INO_Montagem": texto[:LIMITE_MONTAGEM],
        "U_INO_Montagem2": texto[LIMITE_MONTAGEM:],
    }


def cabecalho(impressao: Any) -> dict[str, Any]:
    """Header UDFs of the quotation: the `ServiceProcess.cs:1173-1189` set."""
    udfs: dict[str, Any] = {
        "U_INO_PessoaContato": impressao.contato[:LIMITE_CONTATO],
        "U_INO_CondPag": condicao_de_pagamento(impressao.pagamento_codigo),
        "U_INO_PrazoEntrega": str(impressao.prazo_entrega),
        "U_INO_ValorTransp": valor_texto(impressao.valor_transporte, LIMITE_VALOR_TRANSP),
        "U_INO_ValorEmbalagem": valor_texto(impressao.valor_embalagem, LIMITE_VALOR_EMBALAGEM),
        # `U_INO_VL_MT` é `db_Float` no SAP, não texto — daí o número puro.
        # Na cotação ele vem de `ORCVALMON`; no pedido, de `ORCBAS3`.
        "U_INO_VL_MT": float(impressao.valor_montagem),
        "U_INO_TIPO_MT": impressao.montagem_tipo,
    }
    udfs.update(partir_montagem(impressao.montagem))
    return udfs


def _udfs_da_linha(item: Any, orcamento: Any) -> dict[str, Any]:
    """`U_INO_ACAB` is a header field at the source and a line field at the destination.

    The legacy repeats the same `ORCIMP_ACABAMENTO` on every quotation line (`ServiceProcess.cs:1217`,
    `:1231`, `:1435`, `:1449`), and real quotations confirm it. Reproduced as is.
    """
    del item  # o acabamento é do orçamento, não da linha
    impressao = getattr(orcamento, "impressao", None)
    return {"U_INO_ACAB": getattr(impressao, "acabamento", "") if impressao else ""}


def linhas(
    orcamento: Any, de_para: dict[str, Any], *, deposito: str = DEPOSITO_PADRAO
) -> ResultadoLinhas:
    """`DocumentLines` of the quotation."""
    return resolver_linhas(orcamento, de_para, udfs_da_linha=_udfs_da_linha, deposito=deposito)


def montar_payload(
    orcamento: Any,
    *,
    parceiro: str | None,
    filial: int,
    snapshot_id: int | None = None,
    oportunidade: dict[str, Any] | None = None,
    trocando_de_parceiro: bool = False,
) -> dict[str, Any]:
    """Quotation body, without the lines.

    The lines are left out on purpose: resolving them depends on the group mapping (`de_para`), which is
    infrastructure (it hits the SAP) and produces warnings that must be recorded in the tracking. The
    application builds the full payload because it has both.
    """
    payload = campos_comuns(
        orcamento,
        parceiro=parceiro,
        filial=filial,
        snapshot_id=snapshot_id,
        oportunidade=oportunidade,
        trocando_de_parceiro=trocando_de_parceiro,
    )
    payload.update(cabecalho(orcamento.impressao))
    return payload
