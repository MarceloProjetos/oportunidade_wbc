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
#: Categories that are "out of the process" when a PERSON changes them. Payment terms count
#: since decision D10 (owner, 15/10/2026). "endereco" and "fechamento" only inform.
QUEBRA = frozenset({"cliente", "preco", "quantidade", "entrega", "liberacao", "cancelamento",
                    "condicao_pagamento"})
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


def _sql_versoes(schema: str, *, desde: bool = False) -> str:
    """``desde=True`` adds a second parameter: only versions >= it (the archiver's cursor)."""
    filtro = ' AND T1."LogInstanc" >= ?' if desde else ""
    return (
        f'SELECT T1.*, T2."U_NAME" AS "_U_NAME", T2."USER_CODE" AS "_USER_CODE" '
        f'FROM "{schema}"."ADOC" T1 '
        f'LEFT JOIN "{schema}"."OUSR" T2 ON T2."USERID" = COALESCE(T1."UserSign2", T1."UserSign") '
        f'WHERE T1."ObjType" = \'17\' AND T1."DocEntry" = ?{filtro} ORDER BY T1."LogInstanc"'
    )


def _sql_linhas(schema: str, *, desde: bool = False) -> str:
    colunas = ", ".join(f'"{c}"' for c in CAMPOS_LINHA)
    filtro = ' AND "LogInstanc" >= ?' if desde else ""
    return (
        f'SELECT "LogInstanc", "LineNum", {colunas} FROM "{schema}"."ADO1" '
        f'WHERE "ObjType" = \'17\' AND "DocEntry" = ?{filtro} ORDER BY "LogInstanc", "LineNum"'
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


MAIS_ANTIGA = "versão mais antiga que o SAP ainda guarda (as anteriores foram apagadas)"


def entradas(versoes: list[dict], linhas: list[dict]) -> list[dict]:
    """ADOC/ADO1 rows → classified and folded entries, OLDEST first.

    The first version has nothing to diff against: it is the creation (LogInstanc 1), the
    oldest one the SAP still keeps, or -- for the archiver -- the cursor version it already
    stored (the caller drops that one).
    """
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
        # Not the creation: its "criado com" would lie.
        itens[0]["resumo"] = MAIS_ANTIGA
    saida = _dobrar(itens)
    for e in saida:
        e.pop("_primeira", None)
        e.pop("_so_peso", None)
    return saida


# --- The Pedidos screen (.90) writes to SAP as the integration user (plan T8) ----------
#: How far a SAP version may be from the screen row written before its PATCH. The PATCH and
#: the reread take seconds; a version's time has minute precision.
TELA_ANTES_S = 300
TELA_DEPOIS_S = 120


def _instante(valor: Any) -> datetime | None:
    if isinstance(valor, datetime):
        return valor
    try:
        return datetime.fromisoformat(str(valor))
    except (TypeError, ValueError):
        return None


def casar_tela(entrada: dict, telas: list[dict]) -> dict:
    """An "integracao" entry saved while someone wrote from the Pedidos screen becomes that
    person's (``pela_tela``). ``telas``: rows ``fonte='tela'`` of the same order. A worker save
    in the same minutes would be taken for the person: accepted (rare), and the entry says
    "pela tela" so it can be checked."""
    if entrada.get("origem") != "integracao" or not telas:
        return entrada
    quando = _instante(entrada.get("momento"))
    if quando is None:
        return entrada
    melhor, distancia = None, None
    for t in telas:
        if t.get("resultado") == "falhou":
            continue
        t_quando = _instante(t.get("momento"))
        if t_quando is None:
            continue
        # The version is stamped to the minute (seconds dropped): up to 60 s "early".
        delta = (quando - t_quando).total_seconds()
        if -(TELA_DEPOIS_S + 60) <= delta <= TELA_ANTES_S and (distancia is None or abs(delta) < distancia):
            melhor, distancia = t, abs(delta)
    if melhor is None:
        return entrada
    # The screen lets only PEDIDO_LIBERACAO_EDITORES release: a release from it is the process.
    fora = sorted((set(entrada.get("categorias") or []) & QUEBRA) - {"liberacao"})
    return entrada | {"origem": "pessoa", "pela_tela": True, "pessoa": melhor.get("pessoa"),
                      "tela_id": melhor.get("id"), "tela_acao": melhor.get("acao"),
                      "quebra": bool(fora), "quebra_em": fora}


def de_arquivo(r: dict) -> dict:
    """An archived row (fonte sap_adoc) in the shape of a live entry."""
    e: dict[str, Any] = {
        "versao": r.get("log_instanc_ate") or r.get("log_instanc"),
        "momento": r.get("momento"), "usuario": r.get("usuario_nome") or r.get("usuario_codigo"),
        "usuario_codigo": r.get("usuario_codigo"), "origem": r.get("origem"),
        "categorias": r.get("categorias") or [], "quebra": bool(r.get("quebra")),
        "quebra_em": r.get("quebra_em") or [], "resumo": r.get("resumo"),
        "mudancas": r.get("mudancas") or [], "arquivado": True,
    }
    if (r.get("log_instanc_ate") or 0) > (r.get("log_instanc") or 0):
        e |= {"versao_de": r["log_instanc"], "momento_de": r.get("momento_de"),
              "repeticoes": r.get("repeticoes") or 1, "sem_mudanca": r.get("sem_mudanca") or 0}
    for chave in ("detalhe", "pessoa", "tela_id"):
        if r.get(chave):
            e[chave] = r[chave]
    if r.get("pessoa"):
        e["pela_tela"] = True
    if r.get("lacuna"):
        e["lacuna"] = True
    return e


def _lacuna(primeira: int, perdidas: list[dict]) -> dict[str, Any] | None:
    """What the SAP dropped before anyone read it: the start, and gaps the archiver logged.

    A gap row starting at version 1 is the START (the first read of an order already past 99
    versions), not a hole in the middle -- 84080 showed "1947 no meio" on 15/10/2026."""
    def tamanho(r: dict) -> int:
        return int(r["log_instanc_ate"]) - int(r["log_instanc"]) + 1

    antes = max(0, primeira - 1) + sum(tamanho(r) for r in perdidas if int(r["log_instanc"]) == 1)
    meio = sum(tamanho(r) for r in perdidas if int(r["log_instanc"]) != 1)
    if not antes and not meio:
        return None
    partes = ([f"as {antes} primeiras"] if antes else []) + ([f"{meio} no meio"] if meio else [])
    return {"versoes_apagadas": antes + meio,
            "texto": (f"O SAP guarda só as 99 últimas versões: {' e '.join(partes)} deste pedido foram "
                      f"apagadas antes de serem lidas e não podem ser recuperadas.")}


def montar(doc_num: int, doc_entry: int, versoes: list[dict], linhas: list[dict],
           limite: int = VERSOES_PADRAO, arquivo: list[dict] | None = None) -> dict[str, Any]:
    """Pure part: the rows of ADOC/ADO1 (+ the archived rows, if any) → the answer.

    ``arquivo``: rows of ``pedidos_sap_alteracoes`` for this order. The archived SAP rows are
    the truth up to the highest version they hold; the live ADOC only adds what came after.
    """
    arquivo = arquivo or []
    guardadas = sorted((r for r in arquivo if r.get("fonte") == "sap_adoc"),
                       key=lambda r: int(r.get("log_instanc") or 0))
    telas = [r for r in arquivo if r.get("fonte") == "tela"]
    worker = [r for r in arquivo if r.get("fonte") == "worker"]
    ate = max((int(r.get("log_instanc_ate") or 0) for r in guardadas), default=0)
    vivas = [v for v in versoes if int(v["LogInstanc"]) >= ate]
    novas = entradas(vivas, [ln for ln in linhas if int(ln["LogInstanc"]) >= ate])
    if ate and novas and novas[0]["versao"] == ate and "versao_de" not in novas[0]:
        novas = novas[1:]  # the base: already archived
    novas = [casar_tela(e, telas) for e in novas]
    todas = [de_arquivo(r) for r in guardadas] + novas
    limite = max(1, min(int(limite), VERSOES_MAX))
    resposta: dict[str, Any] = {
        "pedido": doc_num, "doc_entry": doc_entry,
        "total_versoes": max([int(v["LogInstanc"]) for v in versoes] + [ate], default=0),
        "total_entradas": len(todas),
        "versoes_omitidas": max(0, len(todas) - limite),
        "quebras": sum(1 for e in todas if e.get("quebra")),
        "campos_acompanhados": {"cabecalho": "todos (menos os internos que mudam a cada gravação)",
                                "linha": list(CAMPOS_LINHA.values())},
        "versoes": todas[-limite:][::-1],  # newest first
    }
    if guardadas:
        resposta["arquivo"] = {"linhas": len(guardadas), "ate_versao": ate}
    primeira = (int(guardadas[0]["log_instanc"]) if guardadas
                else int(versoes[0]["LogInstanc"]) if versoes else 1)
    lacuna = _lacuna(primeira, [r for r in guardadas if r.get("lacuna")])
    if lacuna:
        resposta["lacuna"] = lacuna
    if worker:
        resposta["eventos_worker"] = [
            {"momento": r.get("momento"), "regra": r.get("acao"), "orcamento": r.get("orcamento"),
             "texto": r.get("resumo")}
            for r in sorted(worker, key=lambda r: str(r.get("momento")), reverse=True)]
    return resposta


def historico(numero: int, *, por_docentry: bool = False, limite: int = VERSOES_PADRAO,
              ler_arquivo: Any = None) -> dict[str, Any]:
    """The change log of ONE sales order. Raises ``PedidoNaoEncontrado`` / ``SAPIndisponivel``.

    ``ler_arquivo(doc_entry) -> list[dict]`` reads the archived rows; a failure there only adds
    ``aviso_arquivo`` (the live SAP answer still goes out)."""
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
    arquivo: list[dict] = []
    aviso_arquivo = None
    if ler_arquivo is not None:
        try:
            arquivo = list(ler_arquivo(doc_entry) or [])
        except Exception as exc:  # the archive is a bonus: never take the live answer down
            aviso_arquivo = f"arquivo do histórico indisponível agora ({type(exc).__name__})"
    resposta = montar(doc_num, doc_entry, versoes, linhas, limite, arquivo=arquivo)
    if aviso_arquivo:
        resposta["aviso_arquivo"] = aviso_arquivo
    if not versoes and not arquivo:
        resposta["aviso"] = ("O SAP não tem histórico deste pedido (log de alterações desligado "
                             "ou pedido nunca salvo depois de criado).")
    return resposta
