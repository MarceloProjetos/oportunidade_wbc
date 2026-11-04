"""Flows of the Pedidos WBC shared by the screen and the JSON API (01/10/2026).

F1 of PLANO_API_PEDIDOS_WBC.md (removed 2026-10-06), same move as the Manutenção de OP on 29/09: until then
these lived inside the screen's routes, mixed with the HTML — paging the list, building the
checked plan of Processar/Reprocessar, spending the token, running the pedidos one by one.
The JSON API needs the same decisions with the same words, so both routers became thin
adapters over this file. A refusal is raised as `Recusa` (core/recusa.py).

What stays in the adapters: reading the request, opening the HANA reader for the LIST (the
tests swap it at the router) and the write gate `core.web.avisa_escrita`, called by every
write endpoint where the coverage test in `test_web_modulos.py` sees it.

"forçar" exists on the screen only (owner, 01/10/2026): `monta_plano` takes ``force`` and the
API never passes it.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

from controleproducao.config import get_settings
from controleproducao.core import service_layer_client
from controleproducao.core.confirmacao import PLANOS, ConfirmacaoInvalida, Plano
from controleproducao.core.formato import numero_br
from controleproducao.core.hana_reader import HanaDirectReader
from controleproducao.core.recusa import Recusa
from controleproducao.core.service_layer_client import ServiceLayerClient
from controleproducao.core.sqlserver_client import WbcSqlServerClient
from controleproducao.core.tarefas import ORIGEM_TELA, TAREFAS, Tarefa, acompanha_log
from controleproducao.modules.pedidos_wbc import service

logger = logging.getLogger(__name__)

MODULO = "pedidos_wbc"

#: Which execution may spend a plan's token (`Plano.tipo`, 01/10/2026 review).
TIPO_PROCESSAR = "processar"
TIPO_REPROCESSAR = "reprocessar"
TIPO_CANCELAR_OPS = "cancelar-ops"

# Pedidos per page. "integrados" passes 300 rows — scrolling everything to find one number is
# worse than paging.
POR_PAGINA = 15

NOME_PROCESSAR = "Processar pedidos novos"
NOME_REPROCESSAR = "Reprocessar pedidos integrados"

# The red notice of the confirmation: `destaque` is the bold start of the sentence.
DESTAQUE_PROCESSAR = "Cria Ordens de Produção"
AVISO_PROCESSAR = (
    ", itens e recursos no SAP, e marca o pedido como processado. Não há desfazer automático."
)
# What Reprocessar really does, read from `service.reprocessar_pedidos_integrados` on
# 30/09/2026. The pre-D8 screen said it cancelled the OPs "before recreating them" — it
# recreates nothing: the order goes back to "Pedidos novos" and needs Processar again.
AVISO_REPROCESSAR = (
    "Para cada pedido: grava uma tabela nova do orçamento (OrcDetalhe), marca o pedido como "
    "NÃO processado e zera o U_INO_OP das linhas, revincula a Oportunidade e CANCELA todas as "
    "OPs PLANEJADAS do pedido — de qualquer origem, inclusive as do addon. NÃO recria as OPs: "
    "o pedido volta para \"Pedidos novos\" e precisa ser processado de novo. OP liberada ou "
    "encerrada não é tocada; OP cancelada não volta."
)
# The warning under the list in "Pedidos integrados" (the screen shows it before any click).
AVISO_LISTA_REPROCESSAR = (
    "Reprocessar cancela todas as OPs planejadas do pedido — de qualquer origem, inclusive as "
    "do addon — e não recria: o pedido volta para \"Pedidos novos\" e precisa ser processado "
    "de novo."
)
RESUMO_SELECIONADOS = "pedido(s) selecionado(s)"
RESUMO_FORCADO = "forçado (ignora as checagens de reprocessamento)"
#: Written after the pedido number in the execution description (kept in the Supabase history),
#: and what the Execuções screens look for to show the "Nota espelho" pill.
MARCA_ESPELHO = "nota espelho"

#: The default `SYNC_LOTE_MAX` of the API 8077: a bigger batch gets a 413.
LOTE_SINCRONIZACAO = 50
#: Not a failure: the API copies nothing while the pedido has no live OP.
SEM_OP_PARA_SINCRONIZAR = frozenset({"sem_os", "cancelada"})

MSG_NENHUM_SELECIONADO = "Nenhum pedido selecionado."
MSG_LISTA_VAZIA = "Nenhum pedido neste modo."
MSG_BUSCA_AUTOMATICA_FALHOU = (
    "Não foi possível carregar os pedidos novos agora — recarregue a página (F5) para tentar de novo."
)


@dataclass(frozen=True)
class Operacao:
    """What a mode of the list does to the selection: the screen's button and its plan."""

    tipo: str
    nome: str
    verbo: str
    destaque: str
    aviso: str


PROCESSAR = Operacao(TIPO_PROCESSAR, NOME_PROCESSAR, "Processar", DESTAQUE_PROCESSAR, AVISO_PROCESSAR)
REPROCESSAR = Operacao(TIPO_REPROCESSAR, NOME_REPROCESSAR, "Reprocessar", "", AVISO_REPROCESSAR)


def operacao_do_modo(integrados: bool) -> Operacao:
    return REPROCESSAR if integrados else PROCESSAR


# ---------------------------------------------------------------------------
# The list (read only)
# ---------------------------------------------------------------------------
async def le_pedidos(leitor: HanaDirectReader, integrados: bool) -> list:
    """The eligible pedidos of a mode, most recent first (the screen's and the API's list)."""
    return await service.buscar_pedidos_para_integrar(leitor, integrados=integrados)


@dataclass(frozen=True)
class Pagina:
    itens: list
    total: int
    pagina: int
    paginas: int
    primeiro: int
    ultimo: int


def pagina(pedidos: list, numero: int) -> Pagina:
    """One page of the list. A page out of range becomes the nearest valid one instead of an
    empty table: the number comes from a URL and may be stale (the list shrinks as pedidos are
    processed)."""
    total = len(pedidos)
    paginas = max(1, -(-total // POR_PAGINA))  # ceiling division
    numero = min(max(1, numero), paginas)
    inicio = (numero - 1) * POR_PAGINA
    return Pagina(
        itens=pedidos[inicio:inicio + POR_PAGINA],
        total=total,
        pagina=numero,
        paginas=paginas,
        primeiro=inicio + 1 if total else 0,
        ultimo=min(inicio + POR_PAGINA, total),
    )


# ---------------------------------------------------------------------------
# Step 1 — the checked plan (reads, never writes)
# ---------------------------------------------------------------------------
def monta_plano(
    elegiveis: list,
    opp_ids: list[str],
    integrados: bool,
    force: bool = False,
) -> Plano:
    """The checked plan of Processar (``integrados=False``) or Reprocessar.

    ``elegiveis`` is the list read again from the SAP right now, not what the page showed:
    what arrives from a browser is a number anyone can type, and the legacy grid never let the
    user act on a pedido outside its filter. A selected pedido missing from it refuses the
    whole plan (`fora_da_lista`); it is never skipped silently.

    Processar also refuses `nota_espelho` and `ops_orfas`, ``force`` included; Reprocessar
    skips both checks. ``force`` is ignored for Reprocessar and travels in the plan (`_force`).

    Raises:
        Recusa: `invalido` (empty selection), `fora_da_lista`, `nota_espelho`, `ops_orfas`.
    """
    if not opp_ids:
        raise Recusa("invalido", MSG_NENHUM_SELECIONADO)
    escolhidos = {str(i) for i in opp_ids}
    selecionados = [p for p in elegiveis if str(p.opp_id) in escolhidos]
    fora = sorted(escolhidos - {str(p.opp_id) for p in selecionados})
    if fora:
        raise Recusa(
            "fora_da_lista",
            "Estes pedidos não estão mais na lista elegível e foram recusados: "
            + ", ".join(fora)
            + ". Refaça a busca — o estado no SAP mudou desde que a tela foi carregada.",
            dados={"fora": fora},
        )

    if not integrados:
        _recusa_nota_espelho(selecionados)
        _recusa_ops_orfas(selecionados)

    operacao = operacao_do_modo(integrados)
    force = bool(force) and not integrados
    plano = PLANOS.criar(
        operacao=operacao.nome,
        resumo={RESUMO_SELECIONADOS: len(selecionados),
                **({"modo": RESUMO_FORCADO} if force else {})},
        itens=[
            {"Pedido": p.doc_num, "Oportunidade": p.opp_id, "WBC": p.orc_num_masc,
             "Cliente": f"{p.cod_cliente} — {p.nome_cliente}",
             "Total": numero_br(p.total_pedido),
             # Underscored keys are not shown by the screen; the API returns the raw values.
             "_cliente_codigo": p.cod_cliente, "_cliente_nome": p.nome_cliente,
             "_total": p.total_pedido, "_nota_espelho": p.nota_espelho}
            for p in selecionados
        ],
        tipo=operacao.tipo,
    )
    # `force` travels in the plan, not in the confirmation form: the user confirms exactly
    # the mode that was checked.
    plano.resumo["_force"] = force
    return plano


def _recusa_nota_espelho(selecionados: list) -> None:
    """Processar refused for a Nota Espelho order, "forçar" included (Marcelo, 26/10/2026).

    The mirror order is not confirmed yet: creating its OPs would send it to the factory.
    Reprocessar stays open — it only cancels planned OPs, it never creates one.
    """
    espelhos = [p for p in selecionados if p.nota_espelho]
    if not espelhos:
        return
    raise Recusa(
        "nota_espelho",
        " ".join(service.mensagem_nota_espelho(p.doc_num, p.orc_num_masc) for p in espelhos),
        titulo="Pedido com Nota Espelho não vai para a produção",
        detalhes=[{"Pedido": p.doc_num, "WBC": p.orc_num_masc,
                   "Cliente": f"{p.cod_cliente} — {p.nome_cliente}"} for p in espelhos],
        colunas=["Pedido", "WBC", "Cliente"],
        dados={"nota_espelho": [p.doc_num for p in espelhos]},
    )


def _recusa_ops_orfas(selecionados: list) -> None:
    """Processar refused while a cancelled order of the same quote has live OPs.

    Not bypassed by "forçar": that one skips the reprocessing checks, and this is not one —
    it is the same items planned twice (84505/84507, 84506/84510, 20/10/2026).
    """
    com_orfas = [p for p in selecionados if p.ops_orfas]
    if not com_orfas:
        return
    raise Recusa(
        "ops_orfas",
        " ".join(service.mensagem_ops_orfas(p.doc_num, p.orc_num_masc, p.ops_orfas)
                 for p in com_orfas),
        titulo="OP viva em pedido cancelado do mesmo orçamento",
        detalhes=[
            {"Pedido": p.doc_num, "WBC": p.orc_num_masc, "Pedido cancelado": o["pedido"],
             "OP": o["op"], "Status": "Planejada" if o["status"] == "P" else "Liberada"}
            for p in com_orfas for o in p.ops_orfas
        ],
        colunas=["Pedido", "WBC", "Pedido cancelado", "OP", "Status"],
        dados={"ops_orfas": {str(p.doc_num): p.ops_orfas for p in com_orfas}},
    )


def plano_visivel(plano: Plano) -> dict:
    """The plan's JSON without the internal keys (prefixed with `_`)."""
    dados = plano.para_json()
    dados["resumo"] = {k: v for k, v in dados["resumo"].items() if not k.startswith("_")}
    dados["itens"] = [{k: v for k, v in i.items() if not k.startswith("_")} for i in dados["itens"]]
    return dados


# ---------------------------------------------------------------------------
# Step 2 — spending the token and running
# ---------------------------------------------------------------------------
def ocupado(exc: RuntimeError) -> Recusa:
    return Recusa(
        "ocupado", str(exc), titulo="Já existe execução em andamento",
        execucao=TAREFAS.em_execucao(MODULO),
    )


def operacao_do_token(token: str) -> str:
    """The operation a token stands for, without spending it — the label of the write gate."""
    plano = PLANOS.obter(token)
    return plano.operacao if plano else "Pedidos WBC"


def consome_plano(token: str, tipo: str) -> Plano:
    """Spend the token — only after checking the module is free.

    The write gate (in the adapter) and the busy check come BEFORE the token is spent: a
    refused write or a busy module used to burn it and send the operator back to check the
    plan again. No `await` between the check and the spend.
    """
    try:
        TAREFAS.confere_livre(MODULO)
    except RuntimeError as exc:
        raise ocupado(exc) from exc
    try:
        return PLANOS.consumir(token, tipo)
    except ConfirmacaoInvalida as exc:
        raise Recusa("confirmacao_invalida", str(exc), titulo="Confirmação não aceita") from exc


def alvos(plano: Plano) -> list[tuple[str, str]]:
    """Pairs ``(pedido SAP, orçamento WBC)`` of the checked plan.

    Both travel together: the service works by the WBC quote (``00125720``), but whoever reads
    the screen thinks in the SAP pedido (``84371``) — the number opened in the B1.
    """
    return [(str(i["Pedido"]), str(i["WBC"])) for i in plano.itens]


def descricao(plano: Plano) -> str:
    nomes = [
        f"{i['Pedido']} ({MARCA_ESPELHO})" if i.get("_nota_espelho") else str(i["Pedido"])
        for i in plano.itens
    ]
    texto = f"{len(nomes)} pedido(s): " + ", ".join(nomes)
    return texto + (" (forçado)" if plano.resumo.get("_force") else "")


def corrotina_pedidos(plano: Plano) -> Callable[[Tarefa], Awaitable[dict]]:
    """The background body of a checked Processar/Reprocessar plan."""
    pares = alvos(plano)
    modo = "reprocessar" if plano.tipo == TIPO_REPROCESSAR else "processar"
    force = bool(plano.resumo.get("_force")) and modo == "processar"

    async def executa(tarefa: Tarefa) -> dict:
        return await _roda_pedidos(tarefa, pares, modo=modo, force=force)

    return executa


async def _roda_pedidos(tarefa: Tarefa, alvos: list[tuple[str, str]], modo: str, force: bool) -> dict:
    """``alvos`` are pairs ``(pedido SAP, orçamento WBC)``.

    The service gets the QUOTE — what `GetOrcsWBC` and `GetIdOrcamentosPedido` look up, not
    the Oportunidade key (``15149``); swapping them gives "sem pedido vinculado" on every row.
    The screen gets the PEDIDO.
    """
    settings = get_settings()
    tarefa.avanca(f"Conectando à Service Layer ({settings.sl_company_db})…", 0, len(alvos))
    # Both readers keep ONE connection for the whole execution and close it here.
    with HanaDirectReader(settings) as hana_reader, WbcSqlServerClient(settings) as wbc:
        return await _roda_pedidos_com(tarefa, alvos, modo, force, settings, hana_reader, wbc)


async def _roda_pedidos_com(
    tarefa: Tarefa,
    alvos: list[tuple[str, str]],
    modo: str,
    force: bool,
    settings: Any,
    hana_reader: HanaDirectReader,
    wbc: WbcSqlServerClient,
) -> dict:
    """Body of `_roda_pedidos`, with both readers opened (and closed) by the caller."""
    iniciados: list[str] = []
    async with ServiceLayerClient(settings) as sl:
        # One pedido at a time, not the whole list at once, so the follow-up says where it
        # stopped: the service's aggregate result does not say which pedido was running when
        # someone interrupted.
        agregado: dict[str, list] = {}
        for i, (doc_num, orc_num) in enumerate(alvos, start=1):
            if tarefa.parada_pedida:
                faltaram = alvos[i - 1:]
                agregado["nao_iniciados"] = [{"doc_num": d, "orc_num": o} for d, o in faltaram]
                tarefa.anota(
                    "Interrompida: " + ", ".join(d for d, _ in faltaram)
                    + " NÃO foram iniciados. Os anteriores terminaram inteiros."
                )
                break
            rotulo = f"Pedido {doc_num} (WBC {orc_num})"
            tarefa.avanca(f"{rotulo} ({i}/{len(alvos)})…", i - 1)
            iniciados.append(doc_num)
            # Every OP created, item registered and allocation resource already goes to the
            # service's `logger.info`; the bridge puts it on the screen while it runs.
            with acompanha_log(tarefa, service.__name__, service_layer_client.__name__):
                if modo == "reprocessar":
                    parcial = await service.reprocessar_pedidos_integrados(
                        sl, wbc, hana_reader, [orc_num]
                    )
                else:
                    parcial = await service.processar_pedidos_novos(
                        sl, wbc, hana_reader, [orc_num], force=force
                    )
            # The service names everything by the quote; the screen is the pedido's. Add the
            # pedido number to each record so the result JSON needs no translating back.
            for chave, valores in parcial.items():
                agregado.setdefault(chave, []).extend(
                    [{**v, "doc_num": doc_num} if isinstance(v, dict)
                     else {"doc_num": doc_num, "orc_num": v}
                     for v in valores]
                )
            erros = parcial.get("com_erro", [])
            # Groups that produced no OP: the pedido "passed", but part of it produced nothing
            # (pedido 84426, 23/09/2026 — the screen said "concluído").
            sem_op = parcial.get("sem_op", [])
            # OP created without the allocation line (GGF_ resource refused by the SL).
            sem_rateio = parcial.get("sem_rateio", [])
            # SAP weight off the WBC tree — usually a quantity changed by hand in the SAP; the
            # cause (who, when) is in `resultado.pesos_diferentes` and in the line above.
            pesos = parcial.get("pesos_diferentes", [])
            # A failed pedido is not "concluído": saying both in a row made the log look like
            # the failure had been worked around.
            if erros:
                tarefa.avanca(f"{rotulo}: ERRO — {erros[0].get('motivo')}", i, problema=True)
            elif sem_op or sem_rateio or pesos:
                ressalvas = []
                if pesos:
                    ressalvas.append(
                        f"{len(pesos)} linha(s) com peso diferente da árvore do WBC"
                        + (" (veja a CAUSA acima)" if any(p.get("causa") for p in pesos) else "")
                    )
                if sem_op:
                    ressalvas.append(
                        f"{len(sem_op)} grupo(s) sem OP: "
                        + "; ".join(str(g.get("motivo")) for g in sem_op)
                    )
                if sem_rateio:
                    ressalvas.append(
                        f"{len(sem_rateio)} OP(s) sem a linha de rateio: "
                        + "; ".join(f"{g.get('item')} — {g.get('motivo')}" for g in sem_rateio)
                    )
                tarefa.avanca(f"{rotulo}: ATENÇÃO — " + " | ".join(ressalvas), i, problema=True)
            elif modo == "reprocessar":
                tarefa.avanca(
                    f"{rotulo}: reprocessado — OPs planejadas canceladas; processe de novo "
                    "em \"Pedidos novos\" para criar as OPs.", i,
                )
            else:
                tarefa.avanca(f"{rotulo}: concluído.", i)
    if tarefa.parada_pedida and not agregado.get("nao_iniciados"):
        # Asked while the last pedido was already running: nothing was left out.
        tarefa.parada_pedida = False
        tarefa.anota("Interrupção pedida com o último pedido já em andamento — nenhum ficou de fora.")
    # Every started pedido, failed ones included: there is no rollback, so a failed Processar may
    # have left OPs, and the mirror only ever copies what the SAP has.
    if iniciados:
        agregado["sincronizacao"] = await sincroniza_espelho(tarefa, settings, iniciados)
    return agregado


async def sincroniza_espelho(tarefa: Tarefa, settings: Any, doc_nums: list[str]) -> list[dict]:
    """Copy the pedidos that just ran to the OS mirror, through the API 8077 of this machine.

    Without it the mirror stayed on the OPs of before until someone clicked Sincronização, and
    the other team's tree read the old rows. Through the API, not `extract_ordens_servico_engenharia`
    directly, so it shares the API's lock, diagnosis and sync log.

    Never raises: the pedidos are already done in the SAP, so a failed copy is a warning for the
    log and the result, not a failed execution.
    """
    url = f"http://127.0.0.1:{settings.os_api_port}/sync/ordens-servico"
    cabecalho = {"X-API-Key": settings.os_api_key.get_secret_value()}
    resultados: list[dict] = []
    for inicio in range(0, len(doc_nums), LOTE_SINCRONIZACAO):
        lote = doc_nums[inicio:inicio + LOTE_SINCRONIZACAO]
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0 + 15.0 * len(lote), connect=5.0)) as cliente:
                resposta = await cliente.post(url, headers=cabecalho, json={"npeds": [int(d) for d in lote]})
            resposta.raise_for_status()
            por_pedido = {str(r["nped"]): r for r in resposta.json()["results"]}
        except Exception as exc:  # noqa: BLE001
            logger.warning("Sincronização automática dos pedidos %s falhou: %s", lote, exc)
            por_pedido = {}
        for doc_num in lote:
            r = por_pedido.get(str(doc_num), {})
            resultados.append({"doc_num": doc_num, "ok": bool(r.get("ok")), "tipo": r.get("tipo"),
                               "motivo": r.get("motivo") or (None if r else "A API 8077 não respondeu.")})
            _anota_sincronizacao(tarefa, resultados[-1])
    return resultados


def _anota_sincronizacao(tarefa: Tarefa, r: dict) -> None:
    if r["ok"]:
        tarefa.anota(f"Pedido {r['doc_num']}: sincronizado com a base de dados.")
    elif r["tipo"] in SEM_OP_PARA_SINCRONIZAR:
        tarefa.anota(f"Pedido {r['doc_num']}: nada a sincronizar agora ({r['motivo']}).")
    else:
        tarefa.anota(
            f"Pedido {r['doc_num']}: NÃO sincronizado com a base de dados ({r['motivo']}). "
            "Sincronize pela tela Sincronização.", problema=True,
        )


def dispara(
    nome: str,
    descricao: str,
    corrotina: Callable[[Tarefa], Awaitable[Any]],
    *,
    solicitante: str | None = None,
    origem: str = ORIGEM_TELA,
    ip: str | None = None,
) -> Tarefa:
    """Create the background execution of this module, or refuse because it is busy.

    `parada_combinada` (01/10/2026 review): "Interromper" used to cancel the coroutine in the
    middle of a Service Layer write — an OP without U_INO_OP, a pedido marked processed without
    its semi-finished OPs. Now the body stops between pedidos / OPs.
    """
    try:
        tarefa = TAREFAS.criar(
            MODULO, nome, descricao, corrotina,
            solicitante=solicitante, origem=origem, parada_combinada=True,
        )
    except RuntimeError as exc:
        raise ocupado(exc) from exc
    logger.warning(
        "Execução %s iniciada: %s (%s) · origem %s · solicitante %s · ip %s",
        tarefa.id, nome, descricao, origem, solicitante or "—", ip or "—",
    )
    return tarefa
