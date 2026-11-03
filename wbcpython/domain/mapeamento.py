"""Mapping of the WBC quote to the payload of the `OrcDetalhe` UDO.

Pure transformation: an `OrcamentoWbc` (read from SQL Server) goes in, the dict the Service Layer expects
in a `POST /b1s/v1/OrcDetalhe` comes out.

`OrcDetalhe` is the **historical snapshot** of the quote at the moment the integration acted: append-only,
never updated, and the target of the quotation's `U_INO_ORCAMENTO` UDF. A snapshot with half its fields
empty defeats its purpose, hence the care below.

--------------------------------------------------------------------------
The lines have two paths, and the legacy chooses between them
--------------------------------------------------------------------------
1. **With a product tree** (`INTEGRACAO_ORCPRDARV` has rows): each snapshot line is a component: code,
   colour, level, description, quantity, weight, unit price and total. It is the engineering breakdown,
   and can have dozens of lines for a two-item quote.
2. **Without a tree**: what is left is the `INTEGRACAO_ORCIMP` rows, and the legacy writes only
   **sequence and text** (`U_INO_ORCITM`, `U_INO_ORCTXT`).

**When each path applies, and why.** The tree is only filled in WBC when the quote becomes an **order**:
measured over the quotes changed in 2026, the 600 at `SitCode 60` all have a tree (**100%**), against
**1 in 1,255** of those at `SitCode 40` (quotation issued). While it is only a quotation there is no
engineering breakdown to record.

Note that the choice here **does not look at the SitCode**: it looks at whether the tree exists. That is
on purpose. The business rule ("the tree appears on the order") belongs to WBC, not to us; encoding it
here would create a second source of truth that would break the day WBC changed. The legacy does the same
(`if ret.Count > 0`).

Reproducing both paths matters: writing price and quantity on path 2, as this implementation once did,
invents `Qtde = 1` and a "price" that is the line total, looking *like* path 1 without being it. Better to
write less and true.

--------------------------------------------------------------------------
Field that still has no source
--------------------------------------------------------------------------
`U_INO_LINHA` holds the number of the **order** line in the matching SAP document, and the legacy fills it
by querying `RDR1` (`GetLinha`). That only makes sense once an order exists, and it is null in the
reference records. It stays out until a real case requires it.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from wbcpython.domain.numeros import formatar_para_sap
from wbcpython.infrastructure.wbc_sql.models import (
    ItemArvoreWbc,
    ItemOrcamentoWbc,
    OrcamentoWbc,
)

COLECAO_LINHAS = "INO_ORC_LINHACollection"

#: Limite dos campos de texto do UDO. Textos maiores são cortados, e os que têm
#: campo de continuação (`_2`) transbordam para ele em vez de perder o resto.
LIMITE_TEXTO = 254

def _data_iso(valor: date | None) -> str | None:
    return valor.isoformat() if valor else None


def _texto(valor: str) -> str:
    return (valor or "").strip()


def _com_continuacao(payload: dict[str, Any], campo: str, valor: str, *, continuacao: str) -> None:
    """Write a long text, spilling the excess into the `_2` continuation field.

    The legacy does this with `PGTCOD` and simply **cuts** `ORCPGT` and `ORCIMP_MONTAGEM`. Here every field
    that has a continuation pair in the UDO uses the pair: the field exists exactly for that, and a finish
    text cut mid-sentence is a wrong historical snapshot.
    """
    valor = _texto(valor)
    if not valor:
        return
    payload[campo] = valor[:LIMITE_TEXTO]
    if len(valor) > LIMITE_TEXTO:
        payload[continuacao] = valor[LIMITE_TEXTO : LIMITE_TEXTO * 2]


def montar_payload_orcdetalhe(
    orcamento: OrcamentoWbc, *, capturado_em: date | None = None
) -> dict[str, Any]:
    """Build the body of `POST /b1s/v1/OrcDetalhe` from the WBC quote.

    Fields with no source in WBC are **omitted**, not zeroed: omitting lets the SAP apply its default, while
    zeroing would assert a value we do not have.

    `capturado_em` exists so the test can pin the date; in production it is today.
    """
    imp = orcamento.impressao

    payload: dict[str, Any] = {
        "U_INO_COD": orcamento.orcnum,
        # A revisão do OrcDetalhe é a **da impressão** (`ORCIMP_REVISAO`), não a
        # do cabeçalho que dirige a máquina de estados. São colunas distintas no
        # WBC e o legado usa cada uma no seu lugar:
        # `SetProperty("U_ORCIMP_REVISAO", item.ORCIMP_REVISAO)`, sem alternativa.
        #
        # Havia aqui um `or orcamento.revisao` que contradizia o próprio
        # comentário acima. O efeito é silencioso e real: no orçamento
        # `00125528`, a impressão não tem revisão e o cabeçalho tem `'A'` — a
        # produção grava vazio, e nós gravávamos `'A'`. O campo passava a
        # significar uma coisa ou outra conforme o dado, que é o pior dos casos
        # para quem lê o relatório.
        "U_ORCIMP_REVISAO": imp.revisao,
        "U_CLINOM": orcamento.cliente_nome,
        "U_REPCOD": orcamento.representante,
        # --- valores ---------------------------------------------------------
        "U_ORCVALVND": float(imp.valor_venda),
        "U_ORCVALLST": float(imp.valor_lista),
        "U_ORCVALINV": float(imp.valor_investimento),
        "U_ORCVALLUC": float(imp.valor_lucro),
        "U_ORCVALEXP": float(imp.valor_expedicao),
        "U_ORCVALCOM": float(imp.valor_comissao),
        "U_ORCPERCOM": float(imp.percentual_comissao),
        "U_ORCVALTRP": float(imp.valor_transporte),
        "U_ORCVALMON": float(imp.valor_montagem),
        "U_ORCBAS1": float(imp.base1),
        "U_ORCBAS2": float(imp.base2),
        "U_ORCBAS3": float(imp.base3),
        # --- cliente e contato ----------------------------------------------
        "U_CLICOD": imp.cliente_codigo,
        "U_CLICONCOD": imp.contato_codigo,
        "U_CLICON": imp.contato,
        # --- condições comerciais -------------------------------------------
        "U_TIPMONCOD": imp.montagem_tipo,
        "U_PRZENT": imp.prazo_entrega,
        "U_TABELA_PRECO": imp.tabela_preco,
        # --- dados de impressão ---------------------------------------------
        "U_ORCIMP_EMAIL": imp.email,
        "U_ORCIMP_FONE": imp.fone,
        "U_ORCIMP_CIDADE": imp.cidade or orcamento.municipio,
        "U_ORCIMP_UF": imp.uf or orcamento.uf,
        "U_ORCIMP_TIPO_VENDA": imp.tipo_venda,
        "U_ORCIMP_TRANSPORTE": imp.transporte,
        # Campos tipados como texto no SAP — gravados sempre no mesmo formato,
        # para não perpetuar a inconsistência de separador decimal existente.
        "U_ORCVALEMB": formatar_para_sap(imp.valor_embalagem),
        "U_ORCIMP_RETORNO": formatar_para_sap(orcamento.retorno),
        "U_ORCIMP_INDICE_VENDAS": formatar_para_sap(orcamento.indice_vendas),
        "U_ORCIMP_NEGOCIACAO": formatar_para_sap(orcamento.negociacao),
        COLECAO_LINHAS: montar_linhas_snapshot(orcamento),
    }

    _com_continuacao(payload, "U_PGTCOD", imp.pagamento_codigo, continuacao="U_PGTCOD2")
    _com_continuacao(
        payload, "U_ORCIMP_ACABAMENTO", imp.acabamento, continuacao="U_ORCIMP_ACABAMENTO2"
    )
    if imp.pagamento_texto:
        payload["U_ORCPGT"] = imp.pagamento_texto[:LIMITE_TEXTO]
    if imp.montagem:
        payload["U_ORCIMP_MONTAGEM"] = imp.montagem[:LIMITE_TEXTO]

    # `U_INO_DATA` é **quando o retrato foi tirado**, não a data do orçamento.
    # O legado grava `DateTime.Now` aqui, e faz sentido: o OrcDetalhe é
    # append-only, então o mesmo orçamento gera vários registros ao longo do
    # tempo e a data é o que os distingue. Gravar a data do orçamento — como
    # esta implementação fazia — deixava todos os retratos com a mesma data,
    # justamente perdendo a informação que o campo carrega.
    payload["U_INO_DATA"] = _data_iso(capturado_em or date.today())  # noqa: DTZ011

    return payload


def montar_linhas_snapshot(orcamento: OrcamentoWbc) -> list[dict[str, Any]]:
    """Choose between the two line paths and build the collection."""
    if orcamento.arvore:
        return [_linha_da_arvore(item) for item in orcamento.arvore]
    return [_linha_de_texto(item) for item in orcamento.itens]


def _linha_da_arvore(item: ItemArvoreWbc) -> dict[str, Any]:
    """Line from the product tree: the engineering breakdown.

    `U_INO_CODIGO` goes out in upper case, as in the legacy: it is a code, and a code with variable case
    creates duplicates where there are none.
    """
    return {
        "U_INO_ORCITM": str(item.orcitm),
        "U_INO_CODIGO": item.produto.upper() or None,
        "U_INO_PROD": item.descricao or None,
        "U_INO_COR": item.cor or None,
        "U_INO_NIVEL": str(item.nivel),
        "U_INO_Qtde": float(item.quantidade),
        "U_INO_PESO": float(item.peso),
        "U_INO_PRECO": float(item.preco_unitario),
        "U_INO_TOTAL": float(item.total),
    }


def _linha_de_texto(item: ItemOrcamentoWbc) -> dict[str, Any]:
    """Line of the no-tree path: only sequence and text, as in the legacy.

    No quantity or price here. `ORCPRDQTD` is null on every WBC line and `ORCVAL` is the total, not the unit
    price; writing them would produce a snapshot that looks detailed and is not.
    """
    return {
        "U_INO_ORCITM": str(item.orcitm),
        "U_INO_ORCTXT": item.texto,
    }
