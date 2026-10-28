"""Pedidos fechados com OPs abertas — consultar, marcar e encerrar (28/10/2026).

``docs/PLANO_ENCERRAR_OPS_PEDIDOS_FECHADOS.md``. A sales order already ``Fechado`` in
``VW_STATUS_PEDIDO`` still had thousands of Planejada/Liberada OPs; the first clean-up ran
by hand from ``maintenance/`` scripts. This module is that run as a function of the
Manutenção de OP, for the screen card, the JSON API and (through an approval) the Mira.

Nothing that already exists in the module changes: the closing of each order is the same
``service.levanta_ops`` → ``ordena_por_dependencia`` → ``finalizar_ops`` the "Encerrar todas
as OPs de um pedido" form uses, the confirmation is the same single-use ``PLANOS`` token and
the run is the same one-per-module execution. What is new is around it, per order:

- ``10001287 Item cost not found``: raw materials with zero average cost get ``custo``
  (default 0.01) by a price-change revaluation, and the order is tried once more.
  Semi-finished items an open child OP of the same order produces are left out — they get
  their real cost when that child closes (60 orders would have been revalued instead of 5
  in the 28/10 run).
- ``10001069 ... inactive``: only with ``ativar_inativos``, the inactive items of the
  order's open OPs are activated for the retry and restored to their exact previous state
  afterwards, even when the retry fails.
- The Service Layer dropping the connection: the order is tried once more.

One retry per order, at most: a second failure is reported, never looped on.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from controleproducao.config import get_settings
from controleproducao.core.confirmacao import PLANOS, ConfirmacaoInvalida, Plano
from controleproducao.core.hana_reader import HanaDirectReader
from controleproducao.core.recusa import Recusa
from controleproducao.core.service_layer_client import ServiceLayerClient
from controleproducao.core.tarefas import TAREFAS, Tarefa, acompanha_log
from controleproducao.modules.manutencao_op import acoes, service

logger = logging.getLogger(__name__)

NOME = "Encerrar OPs de pedidos fechados"
TIPO = "encerrar_fechados"
CUSTO_PADRAO = Decimal("0.01")

ERRO_SEM_CUSTO = "10001287"
ERRO_INATIVO = "10001069"
# httpx's texts when the Service Layer drops the connection ("Server disconnected…" seen on
# 28/10/2026); matched in the failure reason, which is ``str(exc)``.
ERROS_DE_CONEXAO = ("Server disconnected", "Connection reset", "timed out")

# The SQL the owner runs in the SAP (28/10/2026), joined with the OPs that are really still
# open in OWOR: the view's Total_OS counts are kept for reference, the action uses OWOR.
PEDIDOS_FECHADOS_COM_OP = """
SELECT V."DocNum" "Pedido", V."CardName" "Cliente", V."Data_Entrega" "Entrega",
       V."Atrasado" "Atrasado", V."Total_OS" "Total_OS", V."Total_OS_Fechadas" "Total_OS_Fechadas",
       COUNT(T."DocEntry") "Abertas",
       SUM(CASE WHEN T."Status" = 'P' THEN 1 ELSE 0 END) "Planejadas"
FROM "VW_STATUS_PEDIDO" V
  INNER JOIN OWOR T ON T."OriginNum" = V."DocNum" AND T."Status" IN ('P', 'R')
WHERE V."StatusPedido" = 'Fechado' AND (V."Atrasado" = 'N' OR ? = 1)
GROUP BY V."DocNum", V."CardName", V."Data_Entrega", V."Atrasado", V."Total_OS", V."Total_OS_Fechadas"
ORDER BY V."DocNum"
""".strip()

# Raw materials still to issue whose average cost is zero in the line's warehouse, minus the
# items an open OP of the same order produces (semi-finished: see the module docstring).
INSUMOS_SEM_CUSTO = """
SELECT DISTINCT W."ItemCode", W."wareHouse"
FROM OWOR T
  INNER JOIN WOR1 W ON W."DocEntry" = T."DocEntry"
  INNER JOIN OITM I ON I."ItemCode" = W."ItemCode"
  LEFT JOIN OITW S ON S."ItemCode" = W."ItemCode" AND S."WhsCode" = W."wareHouse"
WHERE T."OriginNum" = ? AND T."Status" IN ('P', 'R') AND I."InvntItem" = 'Y'
  AND W."PlannedQty" > W."IssuedQty" AND IFNULL(S."AvgPrice", 0) = 0
  AND W."ItemCode" NOT IN (
    SELECT O."ItemCode" FROM OWOR O WHERE O."OriginNum" = T."OriginNum" AND O."Status" IN ('P', 'R'))
ORDER BY W."ItemCode"
""".strip()

# Inactive items among what the order's open OPs consume or produce.
ITENS_INATIVOS = """
SELECT DISTINCT X."ItemCode"
FROM (
  SELECT W."ItemCode" FROM OWOR T INNER JOIN WOR1 W ON W."DocEntry" = T."DocEntry"
  WHERE T."OriginNum" = ? AND T."Status" IN ('P', 'R')
  UNION
  SELECT T."ItemCode" FROM OWOR T WHERE T."OriginNum" = ? AND T."Status" IN ('P', 'R')
) X
  INNER JOIN OITM I ON I."ItemCode" = X."ItemCode"
WHERE I."frozenFor" = 'Y' OR I."validFor" = 'N'
ORDER BY X."ItemCode"
""".strip()


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
def _num(valor: Any) -> int:
    return int(valor or 0)


def pedidos_fechados_com_op(leitor: HanaDirectReader, incluir_atrasados: bool = False) -> list[dict]:
    """The closed orders that still have Planejada/Liberada OPs (the screen list and the API)."""
    linhas = leitor.fetch_all(PEDIDOS_FECHADOS_COM_OP, (1 if incluir_atrasados else 0,))
    return [
        {
            "pedido": _num(linha["Pedido"]),
            "cliente": str(linha.get("Cliente") or ""),
            "entrega": str(linha.get("Entrega") or "")[:10] or None,
            "atrasado": str(linha.get("Atrasado") or "") == "S",
            "ops_abertas": _num(linha["Abertas"]),
            "planejadas": _num(linha["Planejadas"]),
            "total_os": _num(linha.get("Total_OS")),
            "total_os_fechadas": _num(linha.get("Total_OS_Fechadas")),
        }
        for linha in linhas
    ]


def insumos_sem_custo(leitor: HanaDirectReader, pedido: int) -> list[tuple[str, str]]:
    return [(str(r["ItemCode"]), str(r["wareHouse"])) for r in leitor.fetch_all(INSUMOS_SEM_CUSTO, (pedido,))]


def itens_inativos(leitor: HanaDirectReader, pedido: int) -> list[str]:
    return [str(r["ItemCode"]) for r in leitor.fetch_all(ITENS_INATIVOS, (pedido, pedido))]


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------
def le_custo(valor: Any) -> Decimal:
    """Optional cost: empty → 0.01. Accepts "0,01" and "0.01" (no shell in between here)."""
    texto = str(valor if valor is not None else "").strip().replace(",", ".")
    if not texto:
        return CUSTO_PADRAO
    try:
        custo = Decimal(texto)
    except InvalidOperation as exc:
        raise Recusa("invalido", f"Custo inválido: {valor!r}. Use um número, ex.: 0,01.") from exc
    if not custo.is_finite() or custo <= 0:
        raise Recusa("invalido", "O custo precisa ser maior que zero (vazio = 0,01).")
    return custo


def le_pedidos(valores: Any) -> list[int]:
    if not isinstance(valores, (list, tuple)) or not valores:
        raise Recusa("invalido", "Marque ao menos um pedido.")
    pedidos: list[int] = []
    for valor in valores:
        texto = str(valor).strip()
        if isinstance(valor, bool) or not texto.isdigit() or int(texto) <= 0:
            raise Recusa("invalido", f"Número de pedido inválido: {valor!r}.")
        if int(texto) not in pedidos:
            pedidos.append(int(texto))
    return pedidos


# ---------------------------------------------------------------------------
# Plan (read-only) and its token
# ---------------------------------------------------------------------------
def monta_plano(
    leitor: HanaDirectReader, pedidos: list[int], custo: Decimal, ativar_inativos: bool,
    incluir_atrasados: bool = False,
) -> Plano:
    """The checked plan: only the orders the person marked, each still in the list right now.

    An order that left the list since it was shown (closed meanwhile, or no longer
    ``Fechado``) refuses the whole plan: closing something the person did not see on the
    list is what the token exists to prevent.
    """
    lista = {p["pedido"]: p for p in pedidos_fechados_com_op(leitor, incluir_atrasados)}
    fora = [n for n in pedidos if n not in lista]
    if fora:
        raise Recusa(
            "fora_da_lista",
            f"{len(fora)} pedido(s) marcado(s) já não estão na lista de pedidos fechados com OP "
            "aberta: " + ", ".join(map(str, fora)) + ". Consulte de novo e marque outra vez.",
            titulo="A lista mudou",
        )
    itens = []
    for indice, numero in enumerate(pedidos, start=1):
        p = lista[numero]
        itens.append({
            "#": indice, "Pedido": numero, "Cliente": p["cliente"], "Entrega": p["entrega"] or "—",
            "OPs abertas": p["ops_abertas"], "Planejadas (serão liberadas)": p["planejadas"],
            # Underscored keys: read by the run, not shown (the same convention as acoes.py).
            "_pedido": numero, "_custo": str(custo), "_ativar_inativos": bool(ativar_inativos),
        })
    return PLANOS.criar(
        operacao=NOME,
        resumo={
            "pedido(s) marcados": len(pedidos),
            "OP(s) abertas nesses pedidos": sum(lista[n]["ops_abertas"] for n in pedidos),
            "custo para insumo sem custo": f"R$ {str(custo).replace('.', ',')}",
            "itens inativos": "ativados só durante o encerramento" if ativar_inativos
                              else "não são ativados (o pedido falha)",
        },
        itens=itens,
        tipo=TIPO,
    )


def consome_plano(token: str) -> Plano:
    """Spend the token after checking the module is free (the order `acoes.consome_plano` uses)."""
    try:
        TAREFAS.confere_livre(acoes.MODULO)
    except RuntimeError as exc:
        raise acoes._ocupado(exc) from exc
    try:
        return PLANOS.consumir(token, TIPO)
    except ConfirmacaoInvalida as exc:
        raise Recusa("confirmacao_invalida", str(exc), titulo="Confirmação não aceita") from exc


def operacao_do_plano(token: str) -> str:
    plano = PLANOS.obter(token)
    return plano.operacao if plano else NOME


def descricao_do_plano(plano: Plano) -> str:
    return f"{len(plano.itens)} pedido(s)"


# ---------------------------------------------------------------------------
# Writes around the closing (Service Layer)
# ---------------------------------------------------------------------------
async def reavalia_custo(sl: ServiceLayerClient, insumos: list[tuple[str, str]], custo: Decimal,
                         pedido: int) -> Any:
    """Price-change revaluation (``RevalType=P``): a journal entry, undone only by another one."""
    doc = await sl.post("MaterialRevaluation", {
        "DocDate": date.today().isoformat(),
        "RevalType": "P",
        "Comments": f"Custo para encerrar OPs do pedido fechado {pedido} (Manutenção de OP)",
        "MaterialRevaluationLines": [
            {"ItemCode": item, "WarehouseCode": deposito, "Price": float(custo)}
            for item, deposito in insumos
        ],
    })
    return doc.get("DocNum")


async def ativa_itens(sl: ServiceLayerClient, itens: list[str], originais: dict[str, dict]) -> None:
    """Activate items, recording each one's previous ``Valid``/``Frozen`` in ``originais``.

    The caller owns ``originais`` and restores from it in a ``finally``: an item is recorded
    BEFORE its PATCH, so a failure halfway still leaves every touched item restorable.
    """
    for item in itens:
        atual = await sl.get_by_key("Items", item, chave_texto=True)
        originais[item] = {"Valid": atual.get("Valid"), "Frozen": atual.get("Frozen")}
        await sl.update_entity("Items", item, {"Valid": "tYES", "Frozen": "tNO"}, chave_texto=True)


async def restaura_itens(sl: ServiceLayerClient, originais: dict[str, dict]) -> list[str]:
    """Put each item back; returns the ones that could NOT be restored (to be reported)."""
    falhas = []
    for item, estado in originais.items():
        try:
            await sl.update_entity("Items", item, estado, chave_texto=True)
        except Exception as exc:  # noqa: BLE001 - keep restoring the others, report this one
            logger.error("Item %s NÃO voltou ao estado original %s: %s", item, estado, exc)
            falhas.append(item)
    return falhas


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------
async def _encerra_pedido(sl, leitor, pedido: int, filial: int, deve_parar) -> dict:
    """One closing attempt of one order — the same chain as "Encerrar todas as OPs de um pedido"."""
    ops = service.levanta_ops(leitor, doc_num_pedido=str(pedido))
    ops = [o for o in ops if service.classifica_encerramento(o["status"], o["planejada"], o["apontada"])[1]]
    if not ops:
        return {"finalizadas": [], "com_erro": [], "puladas": [], "interrompidas": [], "ignoradas": []}
    componentes = service._componentes_por_op(leitor, [int(o["doc_entry"]) for o in ops])
    ordenadas, em_ciclo = service.ordena_por_dependencia(ops, componentes)
    if em_ciclo:
        return {"finalizadas": [], "puladas": [], "interrompidas": [], "ignoradas": [],
                "com_erro": [{**o, "etapa": "ordenar", "motivo": "ciclo de dependência entre OPs"}
                             for o in em_ciclo]}
    dependentes = service.dependentes_transitivos(ordenadas, componentes)
    return await service.finalizar_ops(sl, leitor, ordenadas, filial, dependentes=dependentes,
                                       deve_parar=deve_parar)


def _motivos(resultado: dict) -> str:
    return " | ".join(str(e.get("motivo") or "") for e in resultado.get("com_erro", []))


async def encerra_pedido_com_ajustes(
    sl, leitor, pedido: int, filial: int, custo: Decimal, ativar_inativos: bool,
    deve_parar: Callable[[], bool] | None = None,
) -> dict:
    """Close one order; on a known failure apply its fix and try ONCE more.

    Returns the last attempt's result plus ``ajustes`` (what was done between attempts) and
    ``tentativas``. Inactive items activated here are always restored, retry or not.
    """
    resultado = await _encerra_pedido(sl, leitor, pedido, filial, deve_parar)
    ajustes: list[str] = []
    motivos = _motivos(resultado)
    if not resultado["com_erro"] or resultado.get("interrompidas"):
        return {**resultado, "ajustes": ajustes, "tentativas": 1}

    tentar_de_novo = any(e in motivos for e in ERROS_DE_CONEXAO)
    if tentar_de_novo:
        ajustes.append("Service Layer caiu: pedido tentado de novo")
    if ERRO_SEM_CUSTO in motivos:
        insumos = insumos_sem_custo(leitor, pedido)
        if insumos:
            doc = await reavalia_custo(sl, insumos, custo, pedido)
            ajustes.append(f"custo {str(custo).replace('.', ',')} gravado em {len(insumos)} insumo(s) "
                           f"(reavaliação {doc}: {', '.join(i for i, _ in insumos)})")
            tentar_de_novo = True
    originais: dict[str, dict] = {}
    if ERRO_INATIVO in motivos:
        if ativar_inativos:
            inativos = itens_inativos(leitor, pedido)
            if inativos:
                ajustes.append(f"itens ativados durante o encerramento: {', '.join(inativos)}")
                tentar_de_novo = True
        else:
            ajustes.append("item inativo no cadastro: marque 'ativar itens inativos' para encerrar")
    if not tentar_de_novo:
        return {**resultado, "ajustes": ajustes, "tentativas": 1}

    try:
        if ERRO_INATIVO in motivos and ativar_inativos:
            await ativa_itens(sl, itens_inativos(leitor, pedido), originais)
        resultado2 = await _encerra_pedido(sl, leitor, pedido, filial, deve_parar)
    finally:
        if originais:
            presos = await restaura_itens(sl, originais)
            ajustes.append("itens devolvidos a inativo" if not presos
                           else f"ATENÇÃO: inative à mão no SAP: {', '.join(presos)}")
    # The first attempt's closed OPs stay closed: report both attempts' successes.
    resultado2["finalizadas"] = resultado["finalizadas"] + resultado2["finalizadas"]
    return {**resultado2, "ajustes": ajustes, "tentativas": 2}


def corrotina(plano: Plano) -> Callable[[Tarefa], Awaitable[dict]]:
    """The background body: the marked orders, in the order shown, one at a time."""
    pedidos = [int(i["_pedido"]) for i in plano.itens]
    custo = Decimal(str(plano.itens[0]["_custo"])) if plano.itens else CUSTO_PADRAO
    ativar = bool(plano.itens[0].get("_ativar_inativos")) if plano.itens else False

    async def executa(tarefa: Tarefa) -> dict:
        settings = get_settings()
        leitor = HanaDirectReader(settings)
        por_pedido: dict[int, dict] = {}
        parar = lambda: tarefa.parada_pedida  # noqa: E731 - checked between OPs, never inside one
        try:
            with acompanha_log(tarefa, service.__name__):
                async with ServiceLayerClient(settings) as sl:
                    for indice, pedido in enumerate(pedidos):
                        if tarefa.parada_pedida:
                            tarefa.anota(f"Pedido {pedido}: NÃO INICIADO — a execução foi interrompida.")
                            continue
                        tarefa.avanca(f"Pedido {pedido} ({indice + 1} de {len(pedidos)})…", indice, len(pedidos))
                        r = await encerra_pedido_com_ajustes(sl, leitor, pedido, settings.sl_business_place_id,
                                                              custo, ativar, parar)
                        por_pedido[pedido] = r
                        _anota_pedido(tarefa, pedido, r)
        finally:
            leitor.close()

        ok = [p for p, r in por_pedido.items() if not r["com_erro"] and not r.get("interrompidas")]
        tarefa.avanca(
            f"{len(ok)} pedido(s) sem erro, {len(por_pedido) - len(ok)} com problema"
            + (f", {len(pedidos) - len(por_pedido)} não iniciado(s)" if len(por_pedido) < len(pedidos) else "")
            + ".",
            len(por_pedido),
        )
        return {"pedidos": {str(p): _resumo(r) for p, r in por_pedido.items()},
                "nao_iniciados": [p for p in pedidos if p not in por_pedido]}

    return executa


def _resumo(r: dict) -> dict:
    return {
        "encerradas": len(r["finalizadas"]),
        "com_erro": [{"op": e.get("doc_num"), "etapa": e.get("etapa"), "motivo": e.get("motivo")}
                     for e in r["com_erro"]],
        "puladas": [o.get("doc_num") for o in r.get("puladas", [])],
        "nao_iniciadas": [o.get("doc_num") for o in r.get("interrompidas", [])],
        "ajustes": r.get("ajustes", []),
        "tentativas": r.get("tentativas", 1),
    }


def _anota_pedido(tarefa: Tarefa, pedido: int, r: dict) -> None:
    for ajuste in r.get("ajustes", []):
        tarefa.anota(f"Pedido {pedido}: {ajuste}", problema=ajuste.startswith("ATENÇÃO"))
    linha = (f"Pedido {pedido}: {len(r['finalizadas'])} OP(s) encerrada(s)"
             + (f", {len(r['com_erro'])} com erro" if r["com_erro"] else "")
             + (f", {len(r.get('puladas', []))} pulada(s)" if r.get("puladas") else "")
             + (f", {len(r.get('interrompidas', []))} não iniciada(s)" if r.get("interrompidas") else ""))
    tarefa.anota(linha, problema=bool(r["com_erro"] or r.get("puladas")))
    for erro in r["com_erro"]:
        tarefa.anota(f"  OP {erro.get('doc_num')}: ERRO em '{erro.get('etapa')}' — {erro.get('motivo')}",
                     problema=True)
