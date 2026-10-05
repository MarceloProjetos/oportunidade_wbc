"""What changed in a sales order, version by version, and who saved each version.

The SAP keeps every saved version of a document in its change log (ADOC = header, ADO1 =
lines; ObjType 17 = sales order). Until now the only reader was the weight "CAUSA" of the
Controle de Produção (29/09/2026, the 00125348 case: a person changed a line quantity and
the SAP rescaled the weight). This generalises it to the whole order, so the agent can
answer "what changed in order N, and who?" — and tell a person's edit from the
integration's.

Read-only, two SELECTs with bound parameters, one short HANA connection.
"""
from __future__ import annotations

import os
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import situacao_pedidos_hana as hana

#: The WHOLE header is compared (ADOC has ~670 columns, UDFs included): on the 84453 the
#: person's saves changed the contact and the payment terms, none of a hand-picked list
#: (measured 02/10/2026). These change on every save, or are derived from other fields.
CABECALHO_IGNORADO = frozenset({
    "LogInstanc", "UpdateDate", "UpdateTS", "DataVers", "UserSign", "UserSign2", "VATFirst",
    "GrosProfit", "GrosProfSy", "GrosProfFC", "DocEntry", "ObjType",
})
#: How the header columns people ask about are named to them; any other column keeps its name.
ROTULOS_CABECALHO: dict[str, str] = {
    "CardCode": "cliente", "CardName": "nome do cliente", "DocDueDate": "data de entrega",
    "DocTotal": "valor total", "DocStatus": "situação (O aberto, C fechado)",
    "CANCELED": "cancelado", "SlpCode": "vendedor (código)", "Confirmed": "aprovado",
    "CntctCode": "contato (código)", "Comments": "observações", "Address": "endereço de cobrança",
    "Address2": "endereço de entrega", "PayToCode": "endereço de cobrança (código)",
    "ShipToCode": "endereço de entrega (código)", "GroupNum": "condição de pagamento (código)",
    "AtcEntry": "anexos", "Weight": "peso total", "Printed": "impresso",
    "U_INO_PedLib": "liberação financeira", "U_INO_DT_PED_LIB": "data da liberação financeira",
    "U_INO_ProcessWBC": "processado no Controle de Produção", "U_INO_MONTADOR": "montador",
    "U_INO_VL_MT": "valor da montagem", "U_INO_TPO_MONTAGEM": "tipo de montagem",
}
#: Lines: a fixed set (ADO1 has ~370 columns x lines x versions — too much to bring whole);
#: the ones a person or the integration sets, not the derived open/stock quantities.
CAMPOS_LINHA: dict[str, str] = {
    "ItemCode": "item", "Quantity": "quantidade", "Weight1": "peso", "Price": "preço",
    "DiscPrcnt": "desconto (%)", "LineStatus": "situação da linha", "ShipDate": "data de entrega da linha",
    "WhsCode": "depósito", "U_INO_OP": "OPs da linha",
}
#: Caps on the answer (rule 6): a big order has hundreds of lines and dozens of versions.
VERSOES_PADRAO = 20
VERSOES_MAX = 60
MUDANCAS_POR_VERSAO = 30


class PedidoNaoEncontrado(LookupError):
    """No sales order with that number."""


def _valor(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime):
        return v.date().isoformat() if not (v.hour or v.minute or v.second) else v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, str):
        return v.rstrip()
    return v


#: Long texts (observations, addresses) are compared whole but shown cut.
TEXTO_MAX = 200


def _curto(v: Any) -> Any:
    return v[:TEXTO_MAX] + "…" if isinstance(v, str) and len(v) > TEXTO_MAX else v


def _usuarios_da_integracao() -> set[str]:
    """The Service Layer users the integration writes with (worker and Controle de Produção)."""
    nomes = (os.getenv("SL_USERNAME"), os.getenv("OP_SL_USERNAME"))
    return {n.strip().lower() for n in nomes if n and n.strip()}


def _sql_versoes(schema: str) -> str:
    return (
        f'SELECT T1.*, T2."U_NAME" AS "_U_NAME", T2."USER_CODE" AS "_USER_CODE" '
        f'FROM "{schema}"."ADOC" T1 '
        f'LEFT JOIN "{schema}"."OUSR" T2 ON T2."USERID" = COALESCE(T1."UserSign2", T1."UserSign") '
        f'WHERE T1."ObjType" = \'17\' AND T1."DocEntry" = ? ORDER BY T1."LogInstanc"'
    )


def _sql_linhas(schema: str) -> str:
    colunas = ", ".join(f'"{c}"' for c in CAMPOS_LINHA)
    return (
        f'SELECT "LogInstanc", "LineNum", {colunas} FROM "{schema}"."ADO1" '
        f'WHERE "ObjType" = \'17\' AND "DocEntry" = ? ORDER BY "LogInstanc", "LineNum"'
    )


def _mudancas(antes: dict, depois: dict, linhas_antes: dict, linhas_depois: dict) -> list[dict]:
    """Header and line differences between two consecutive versions."""
    mudancas: list[dict] = []
    for campo in depois:
        if campo in CABECALHO_IGNORADO or campo.startswith("_"):
            continue
        a, d = _valor(antes.get(campo)), _valor(depois.get(campo))
        if a != d:
            mudancas.append({"onde": "cabeçalho", "campo": ROTULOS_CABECALHO.get(campo, campo),
                             "antes": _curto(a), "depois": _curto(d)})
    for num in sorted(set(linhas_antes) | set(linhas_depois)):
        la, ld = linhas_antes.get(num), linhas_depois.get(num)
        if la is None:
            mudancas.append({"onde": f"linha {num}", "campo": "linha incluída",
                             "antes": None, "depois": _valor(ld.get("ItemCode"))})
            continue
        if ld is None:
            mudancas.append({"onde": f"linha {num}", "campo": "linha removida",
                             "antes": _valor(la.get("ItemCode")), "depois": None})
            continue
        for campo, rotulo in CAMPOS_LINHA.items():
            a, d = _valor(la.get(campo)), _valor(ld.get(campo))
            if a != d:
                mudancas.append({"onde": f"linha {num} ({_valor(ld.get('ItemCode'))})",
                                 "campo": rotulo, "antes": _curto(a), "depois": _curto(d)})
    return mudancas


def montar(doc_num: int, doc_entry: int, versoes: list[dict], linhas: list[dict],
           limite: int = VERSOES_PADRAO) -> dict[str, Any]:
    """Pure part: the rows of ADOC/ADO1 → the answer (tested without a HANA)."""
    por_versao: dict[Any, dict[int, dict]] = {}
    for linha in linhas:
        por_versao.setdefault(linha["LogInstanc"], {})[int(linha["LineNum"])] = linha
    integracao = _usuarios_da_integracao()
    saida: list[dict] = []
    anterior: dict | None = None
    for v in versoes:
        atual_linhas = por_versao.get(v["LogInstanc"], {})
        codigo = (v.get("_USER_CODE") or "").strip()
        quando = hana.momento(v.get("UpdateDate"), v.get("UpdateTS"))
        item: dict[str, Any] = {
            "versao": v["LogInstanc"],
            "momento": quando.isoformat(timespec="minutes") if quando else _valor(v.get("UpdateDate")),
            "usuario": (v.get("_U_NAME") or "").strip() or codigo or None,
            "usuario_codigo": codigo or None,
            # None = cannot tell (no Service Layer user in this machine's .env, or no user).
            "pela_integracao": codigo.lower() in integracao if codigo and integracao else None,
        }
        if anterior is None:
            item["mudancas"] = []
            item["resumo"] = f"criado com {len(atual_linhas)} linha(s)"
        else:
            mudancas = _mudancas(anterior[0], v, anterior[1], atual_linhas)
            item["resumo"] = (f"{len(mudancas)} mudança(s)" if mudancas
                              else "salvo sem mudança de conteúdo (abriu e salvou, ou só campo interno)")
            item["mudancas"] = mudancas[:MUDANCAS_POR_VERSAO]
            if len(mudancas) > MUDANCAS_POR_VERSAO:
                item["mudancas_omitidas"] = len(mudancas) - MUDANCAS_POR_VERSAO
        saida.append(item)
        anterior = (v, atual_linhas)
    limite = max(1, min(int(limite), VERSOES_MAX))
    return {
        "pedido": doc_num, "doc_entry": doc_entry,
        "total_versoes": len(saida),
        "versoes_omitidas": max(0, len(saida) - limite),
        "campos_acompanhados": {"cabecalho": "todos (menos os internos que mudam a cada gravação)",
                                "linha": list(CAMPOS_LINHA.values())},
        "versoes": saida[-limite:][::-1],  # newest first
    }


def historico(numero: int, *, por_docentry: bool = False, limite: int = VERSOES_PADRAO) -> dict[str, Any]:
    """The change log of ONE sales order. Raises ``PedidoNaoEncontrado`` / ``SAPIndisponivel``."""
    schema = hana._schema()
    conn = hana._conectar()
    try:
        chave = "DocEntry" if por_docentry else "DocNum"
        achados = hana._linhas(
            conn, f'SELECT "DocEntry", "DocNum" FROM "{schema}"."ORDR" WHERE "{chave}" = ?',
            (int(numero),))
        if not achados:
            raise PedidoNaoEncontrado(f"Nenhum pedido de venda com {chave} {numero}.")
        doc_entry, doc_num = int(achados[0]["DocEntry"]), int(achados[0]["DocNum"])
        versoes = hana._linhas(conn, _sql_versoes(schema), (doc_entry,))
        linhas = hana._linhas(conn, _sql_linhas(schema), (doc_entry,))
    finally:
        try:
            conn.close()
        except Exception:  # connection already gone with the failure being reported
            pass
    resposta = montar(doc_num, doc_entry, versoes, linhas, limite)
    if not versoes:
        resposta["aviso"] = ("O SAP não tem histórico deste pedido (log de alterações desligado "
                             "ou pedido nunca salvo depois de criado).")
    return resposta
