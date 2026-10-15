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

# --- Classification (web docs/PLANO_PEDIDOS_QUEM_MEXEU.md, F1) -------------------------
# Change keys: header columns by name, line columns as "linha.<Column>", plus
# "linha.incluida" / "linha.removida".

#: Header columns the SAP derives from others (payment, totals, copied dates, weight sum):
#: kept in ``detalhe`` so ``mudancas`` shows what someone set. A delivery or invoice closing
#: the order writes no version (196 of 228 closed orders, 15/10/2026), so the next manual save
#: carries these along with whatever the person did.
CABECALHO_DERIVADO = frozenset({
    "PaidToDate", "PaidSys", "PaidFC", "VatPaid", "VatPaidSys", "VatPaidFC",
    "VatSum", "VatSumSy", "VatSumFC", "DocTotal", "DocTotalSy", "DocTotalFC",
    "DiscSum", "DiscSumSy", "DiscSumFC", "ReqDate", "CancelDate", "Weight", "Printed",
    "FinncPriod", "JrnlMemo",
})
#: Change key -> category of plan §5.
CATEGORIAS: dict[str, str] = {
    "CardCode": "cliente", "CardName": "cliente",
    "ShipToCode": "endereco", "Address2": "endereco",
    "linha.Price": "preco", "linha.DiscPrcnt": "preco", "DiscPrcnt": "preco", "U_INO_VL_MT": "preco",
    "linha.Quantity": "quantidade", "linha.ItemCode": "quantidade",
    "linha.incluida": "quantidade", "linha.removida": "quantidade",
    "DocDueDate": "entrega", "linha.ShipDate": "entrega",
    "U_INO_PedLib": "liberacao", "U_INO_DT_PED_LIB": "liberacao", "Confirmed": "liberacao",
    "CANCELED": "cancelamento",
    # Status closes by itself on delivery/invoice (see CABECALHO_DERIVADO): informational only.
    "DocStatus": "fechamento", "linha.LineStatus": "fechamento",
    "GroupNum": "condicao_pagamento",
}
#: Categories that are "out of the process" when a PERSON changes them. "condicao_pagamento"
#: waits on decision D10; "endereco" and "fechamento" only inform.
QUEBRA = frozenset({"cliente", "preco", "quantidade", "entrega", "liberacao", "cancelamento"})
#: SAP users allowed to release an order: the web's PEDIDO_LIBERACAO_EDITORES (owner's
#: decision 14/10/2026, Adriano Fonseca and Roberto Zelli) in SAP user codes.
LIBERACAO_PERMITIDA = frozenset({"vendas01", "projeto06"})
#: OP processing (Controle de Produção, or the legacy add-on run from a person's SAP client)
#: writes only these: a version limited to them is "criacao_op", whoever saved it. It must touch
#: one of MARCA_OP -- the worker alone also bumps U_INO_VERSAOWBC.
CAMPOS_OP = frozenset({"linha.U_INO_OP", "U_INO_ProcessWBC", "U_INO_ORCAMENTO", "U_INO_VERSAOWBC",
                       "Weight", "linha.Weight1"})
MARCA_OP = frozenset({"linha.U_INO_OP", "U_INO_ProcessWBC", "U_INO_ORCAMENTO"})
#: Weight-only saves: filling the tree weights around OP processing (84179: 6 in a row). Not
#: relabelled (a person may type a weight), only folded with the noise around them.
CAMPOS_PESO = frozenset({"Weight", "linha.Weight1"})


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
    """Header and line differences between two consecutive versions.

    Each change carries its key in ``_chave`` (see CATEGORIAS) for the classification; it is
    removed before the answer goes out.
    """
    mudancas: list[dict] = []
    for campo in depois:
        if campo in CABECALHO_IGNORADO or campo.startswith("_"):
            continue
        a, d = _valor(antes.get(campo)), _valor(depois.get(campo))
        if a != d:
            mudancas.append({"onde": "cabeçalho", "campo": ROTULOS_CABECALHO.get(campo, campo),
                             "antes": _curto(a), "depois": _curto(d), "_chave": campo})
    for num in sorted(set(linhas_antes) | set(linhas_depois)):
        la, ld = linhas_antes.get(num), linhas_depois.get(num)
        if la is None:
            mudancas.append({"onde": f"linha {num}", "campo": "linha incluída",
                             "antes": None, "depois": _valor(ld.get("ItemCode")),
                             "_chave": "linha.incluida"})
            continue
        if ld is None:
            mudancas.append({"onde": f"linha {num}", "campo": "linha removida",
                             "antes": _valor(la.get("ItemCode")), "depois": None,
                             "_chave": "linha.removida"})
            continue
        for campo, rotulo in CAMPOS_LINHA.items():
            a, d = _valor(la.get(campo)), _valor(ld.get(campo))
            if a != d:
                mudancas.append({"onde": f"linha {num} ({_valor(ld.get('ItemCode'))})",
                                 "campo": rotulo, "antes": _curto(a), "depois": _curto(d),
                                 "_chave": f"linha.{campo}"})
    return mudancas


def _sem_chave(mudancas: list[dict]) -> list[dict]:
    return [{k: v for k, v in m.items() if k != "_chave"} for m in mudancas]


def classificar(mudancas: list[dict], codigo: str, pela_integracao: bool | None) -> dict[str, Any]:
    """Who kind of save this was and what it touched (plan §5).

    ``origem``: "criacao_op" (only OP-processing fields, whoever saved), "integracao",
    "pessoa", or None when the integration users are unknown here. ``quebra`` = a person
    changed a field of QUEBRA (a release by LIBERACAO_PERMITIDA is the process).
    """
    chaves = {m["_chave"] for m in mudancas}
    if chaves and chaves <= CAMPOS_OP and chaves & MARCA_OP:
        origem: str | None = "criacao_op"
    elif pela_integracao is None:
        origem = None
    else:
        origem = "integracao" if pela_integracao else "pessoa"
    categorias = sorted({CATEGORIAS[c] for c in chaves if c in CATEGORIAS})
    fora = set(categorias) & QUEBRA
    if (codigo or "").lower() in LIBERACAO_PERMITIDA:
        fora.discard("liberacao")
    return {"origem": origem, "categorias": categorias,
            "quebra": origem == "pessoa" and bool(fora),
            "quebra_em": sorted(fora) if origem == "pessoa" else []}


def _entrada(v: dict, linhas_v: dict, anterior: tuple[dict, dict] | None,
             integracao: set[str]) -> dict[str, Any]:
    """One version → one answer entry (before folding)."""
    codigo = (v.get("_USER_CODE") or "").strip()
    quando = hana.momento(v.get("UpdateDate"), v.get("UpdateTS"))
    # None = cannot tell (no Service Layer user in this machine's .env, or no user).
    pela_integracao = codigo.lower() in integracao if codigo and integracao else None
    item: dict[str, Any] = {
        "versao": v["LogInstanc"],
        "momento": quando.isoformat(timespec="minutes") if quando else _valor(v.get("UpdateDate")),
        "usuario": (v.get("_U_NAME") or "").strip() or codigo or None,
        "usuario_codigo": codigo or None,
        "pela_integracao": pela_integracao,
    }
    if anterior is None:
        item |= classificar([], codigo, pela_integracao)
        item["mudancas"] = []
        item["resumo"] = f"criado com {len(linhas_v)} linha(s)"
        item["_primeira"] = True  # no base to diff against: never folded as an empty save
        return item
    todas = _mudancas(anterior[0], v, anterior[1], linhas_v)
    item |= classificar(todas, codigo, pela_integracao)
    chaves = {m["_chave"] for m in todas}
    item["_so_peso"] = bool(chaves) and chaves <= CAMPOS_PESO
    principais = [m for m in todas if m["_chave"] not in CABECALHO_DERIVADO]
    derivadas = [m for m in todas if m["_chave"] in CABECALHO_DERIVADO]
    if not todas:
        item["resumo"] = "salvo sem mudança de conteúdo (abriu e salvou, ou só campo interno)"
    elif item["origem"] == "criacao_op":
        item["resumo"] = "processamento de OP (vínculo da OP / Detalhe do Orçamento)"
    else:
        item["resumo"] = f"{len(principais)} mudança(s)" + (
            f" + {len(derivadas)} calculada(s) pelo SAP" if derivadas else "")
    item["mudancas"] = _sem_chave(principais[:MUDANCAS_POR_VERSAO])
    if len(principais) > MUDANCAS_POR_VERSAO:
        item["mudancas_omitidas"] = len(principais) - MUDANCAS_POR_VERSAO
    if derivadas:
        item["detalhe"] = _sem_chave(derivadas[:MUDANCAS_POR_VERSAO])
    return item


def _dobravel(item: dict) -> bool:
    """Empty saves, OP processing and weight-only saves: the noise that fills the SAP's
    99-version window."""
    return not item.get("_primeira") and (
        item["origem"] == "criacao_op" or item.get("_so_peso")
        or (not item["mudancas"] and not item.get("detalhe")))


def _dobrar(itens: list[dict]) -> list[dict]:
    """Fold consecutive noise saves by the same user into one entry (oldest → newest in,
    same order out). 84080 on 27/08: 60 versions in 5 min became one line."""
    saida: list[dict] = []
    for item in itens:
        ult = saida[-1] if saida else None
        if (ult is not None and _dobravel(item) and _dobravel(ult)
                and ult["usuario_codigo"] == item["usuario_codigo"]):
            grupo = ult.setdefault("_grupo", [ult.copy()])
            grupo.append(item)
            continue
        saida.append(item)
    for i, item in enumerate(saida):
        grupo = item.pop("_grupo", None)
        if not grupo:
            continue
        ops = [g for g in grupo if g["origem"] == "criacao_op"]
        pesos = [g for g in grupo if g["origem"] != "criacao_op" and g.get("_so_peso")]
        vazias = len(grupo) - len(ops) - len(pesos)
        mudancas = [m for g in grupo for m in g["mudancas"]]
        partes = ([f"{len(ops)} com OP"] if ops else []) + (
            [f"{len(pesos)} só de peso"] if pesos else []) + (
            [f"{vazias} sem mudança"] if vazias else [])
        if ops:
            resumo = "processamento de OP: " + ", ".join(partes)
        elif pesos:
            resumo = f"{len(grupo)} gravações seguidas: " + ", ".join(partes)
        else:
            resumo = f"abriu e salvou {vazias}× sem mudança de conteúdo"
        detalhe = [m for g in grupo for m in g.get("detalhe", [])]
        novo = grupo[-1] | {
            "versao_de": grupo[0]["versao"], "momento_de": grupo[0]["momento"],
            "repeticoes": len(grupo), "sem_mudanca": vazias,
            "origem": "criacao_op" if ops else grupo[-1]["origem"],
            "mudancas": mudancas[:MUDANCAS_POR_VERSAO], "resumo": resumo,
        }
        novo.pop("detalhe", None)
        if detalhe:
            novo["detalhe"] = detalhe[:MUDANCAS_POR_VERSAO]
        novo.pop("mudancas_omitidas", None)
        if len(mudancas) > MUDANCAS_POR_VERSAO:
            novo["mudancas_omitidas"] = len(mudancas) - MUDANCAS_POR_VERSAO
        saida[i] = novo
    return saida


def montar(doc_num: int, doc_entry: int, versoes: list[dict], linhas: list[dict],
           limite: int = VERSOES_PADRAO) -> dict[str, Any]:
    """Pure part: the rows of ADOC/ADO1 → the answer (tested without a HANA)."""
    por_versao: dict[Any, dict[int, dict]] = {}
    for linha in linhas:
        por_versao.setdefault(linha["LogInstanc"], {})[int(linha["LineNum"])] = linha
    integracao = _usuarios_da_integracao()
    itens: list[dict] = []
    anterior: tuple[dict, dict] | None = None
    for v in versoes:
        atual_linhas = por_versao.get(v["LogInstanc"], {})
        itens.append(_entrada(v, atual_linhas, anterior, integracao))
        anterior = (v, atual_linhas)
    if versoes and int(versoes[0]["LogInstanc"]) > 1:
        # The oldest kept version is not the creation: its "criado com" would lie.
        primeira = itens[0]
        primeira["resumo"] = "versão mais antiga que o SAP ainda guarda (as anteriores foram apagadas)"
    entradas = _dobrar(itens)
    for e in entradas:
        e.pop("_primeira", None)
        e.pop("_so_peso", None)
    limite = max(1, min(int(limite), VERSOES_MAX))
    resposta: dict[str, Any] = {
        "pedido": doc_num, "doc_entry": doc_entry,
        "total_versoes": len(itens),
        "total_entradas": len(entradas),
        "versoes_omitidas": max(0, len(entradas) - limite),
        "quebras": sum(1 for e in entradas if e.get("quebra")),
        "campos_acompanhados": {"cabecalho": "todos (menos os internos que mudam a cada gravação)",
                                "linha": list(CAMPOS_LINHA.values())},
        "versoes": entradas[-limite:][::-1],  # newest first
    }
    if versoes and int(versoes[0]["LogInstanc"]) > 1:
        apagadas = int(versoes[0]["LogInstanc"]) - 1
        resposta["lacuna"] = {
            "versoes_apagadas": apagadas,
            "texto": (f"O SAP guarda só as 99 últimas versões: as {apagadas} primeiras deste pedido "
                      f"(antes da versão {apagadas + 1}) foram apagadas e não podem ser lidas."),
        }
    return resposta


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
