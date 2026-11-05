"""Redo an order's OPs when the WBC tree moved past them (owner's decision, 05/11/2026).

Order 84544: the OPs were created at 09:32 with a 300 mm spacer, the WBC went to 400 mm at 10:28,
and syncing kept showing 300 mm because the sync only copies the OPs. The owner's rule: syncing
means "bring what is new", so an order whose OPs have not started is redone (Reprocessar, then
Processar) and an order with any OP started gets a notice instead.

The trigger compares trees, not revision letters: the worker stamps the new letter on the header
of a frozen order (`U_INO_VERSAOWBC`), and at 09:40 revision B still carried the old tree. Measured
on the 91 open processed orders of 05/11/2026: 85 equal, 6 different, all 6 on real revisions.

A refusal that only Processar would give is checked BEFORE anything is cancelled: Reprocessar has
no way back, so failing after it would leave the order with no OPs.

`motivo` is accent-free like every other notice of the sync route (`api._sync_one`).
"""
from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from controleproducao.config import get_settings
from controleproducao.core.hana_reader import HanaDirectReader
from controleproducao.core.sql_ligado import ligar
from controleproducao.core.sqlserver_client import WbcSqlServerClient
from controleproducao.core.tarefas import Tarefa
from controleproducao.modules.pedidos_wbc import acoes, service
from controleproducao.modules.pedidos_wbc import queries as q

NOME_REFAZER = "Refazer OPs do pedido"

EM_DIA = "em_dia"
REFAZER = "refazer"
REFAZENDO = "refazendo"
JA_PRODUZIDO = "ja_produzido"
NAO_APLICADA = "revisao_nao_aplicada"

#: The WBC situation of a closed order. A quote being edited (30 at 10:28 on the 84544) is not
#: what the factory should build yet.
SITCOD_PEDIDO = 60
#: Quantities come through different float and decimal columns on each side.
TOLERANCIA_QTD = 0.01

_SO_LETRAS_E_NUMEROS = re.compile(r"[^A-Z0-9]")


@dataclass(frozen=True)
class Avaliacao:
    """What a sync should do about the OPs of one order."""

    situacao: str
    motivo: str | None = None
    orc_num: str = ""
    revisao: str = ""
    ops: list[int] = field(default_factory=list)


def _chave(linha: dict) -> tuple[str, str, int]:
    """A tree line's identity. Letters and digits only: the two databases hand back accents
    differently, and a false "changed" here would cancel OPs. The product code stays out because
    the two sides format it differently (`PPLCOL...#1440#0#0` x `PPLCOL...`) on the same part."""
    descricao_limpa = _SO_LETRAS_E_NUMEROS.sub("", str(linha.get("Descricao") or "").upper())
    cor = _SO_LETRAS_E_NUMEROS.sub("", str(linha.get("Cor") or "").upper())
    return descricao_limpa, cor, int(linha.get("Nivel") or 0)


def _quantidades(linhas: Iterable[dict]) -> dict[tuple[str, str, int], list[float]]:
    grupos: dict[tuple[str, str, int], set[float]] = {}
    for linha in linhas:
        grupos.setdefault(_chave(linha), set()).add(round(float(linha.get("Quantidade") or 0), 3))
    return {k: sorted(v) for k, v in grupos.items()}


def arvore_mudou(detalhe: Iterable[dict], wbc: Iterable[dict]) -> bool:
    """True when the tree the OPs were built from differs from the WBC's current one."""
    a, b = _quantidades(detalhe), _quantidades(wbc)
    if a.keys() != b.keys():
        return True
    return any(
        len(a[k]) != len(b[k])
        or any(abs(x - y) > TOLERANCIA_QTD for x, y in zip(a[k], b[k], strict=True))
        for k in a
    )


def op_iniciada(op: dict) -> bool:
    """Released, closed or with anything issued or finished. Reprocessar only cancels planned OPs,
    so redoing with one of these would leave two generations of OPs on the same order."""
    return (str(op.get("Status")) != "P"
            or float(op.get("Baixada") or 0) > 0 or float(op.get("Concluida") or 0) > 0)


def _lista(numeros: Iterable[int], limite: int = 5) -> str:
    itens = [str(n) for n in numeros]
    return ", ".join(itens[:limite]) + (f" e mais {len(itens) - limite}" if len(itens) > limite else "")


def avalia(doc_num: int, hana: HanaDirectReader, wbc: WbcSqlServerClient) -> Avaliacao:
    """Decide what to do with one order. Reads only."""
    linhas = hana.fetch_all(*ligar(q.SITUACAO_PARA_REFAZER, doc_num=int(doc_num)))
    if not linhas:
        return Avaliacao(EM_DIA)
    pedido = linhas[0]
    orc_num = str(pedido.get("Orcamento") or "").strip()
    detalhe = str(pedido.get("Detalhe") or "").strip()
    if (not orc_num or not detalhe.isdigit() or str(pedido.get("ProcessWBC")) != "Y"
            or str(pedido.get("DocStatus")) != "O" or str(pedido.get("CANCELED")) != "N"):
        return Avaliacao(EM_DIA)

    situacao = wbc.fetch_all(*ligar(q.SITUACAO_NO_WBC, orc_num=orc_num))
    if not situacao or int(situacao[0].get("SITCOD") or 0) != SITCOD_PEDIDO:
        return Avaliacao(EM_DIA)
    revisao = str(situacao[0].get("REVISAO") or "").strip()

    # An empty side means "could not compare", never "everything changed".
    arvore_wbc = wbc.fetch_all(*ligar(q.ARVORE_NO_WBC, orc_num=orc_num))
    arvore_ops = hana.fetch_all(*ligar(q.ARVORE_DO_DETALHE, detalhe=int(detalhe)))
    if not arvore_wbc or not arvore_ops or not arvore_mudou(arvore_ops, arvore_wbc):
        return Avaliacao(EM_DIA)

    ops = hana.fetch_all(*ligar(q.OPS_VIVAS_DO_PEDIDO, doc_num=int(doc_num)))
    if not ops:
        return Avaliacao(EM_DIA)
    numeros = [int(op["DocNum"]) for op in ops]
    iniciadas = [int(op["DocNum"]) for op in ops if op_iniciada(op)]
    rotulo = f"O orcamento {orc_num} mudou (revisao {revisao})"
    if iniciadas:
        return Avaliacao(
            JA_PRODUZIDO,
            f"{rotulo}, mas o pedido {doc_num} ja tem OP em producao ({_lista(iniciadas)}). "
            "As OPs nao foram refeitas e continuam na versao anterior.",
            orc_num, revisao, iniciadas,
        )
    if str(pedido.get("NotaEspelho")).strip().upper() == "Y":
        return Avaliacao(
            NAO_APLICADA,
            f"{rotulo}, mas o pedido {doc_num} esta com Nota Espelho e ainda nao pode ir para a "
            "producao. As OPs nao foram refeitas.",
            orc_num, revisao, numeros,
        )
    orfas = asyncio.run(service.ops_vivas_de_pedidos_cancelados(hana, [orc_num])).get(orc_num)
    if orfas:
        return Avaliacao(
            NAO_APLICADA,
            f"{rotulo}, mas ha OP viva em pedido cancelado do mesmo orcamento "
            f"({_lista(o['op'] for o in orfas)}). As OPs nao foram refeitas.",
            orc_num, revisao, numeros,
        )
    return Avaliacao(REFAZER, None, orc_num, revisao, numeros)


def avalia_agora(doc_num: int) -> Avaliacao:
    """`avalia` with its own connections. Blocking: call it in a thread."""
    settings = get_settings()
    with HanaDirectReader(settings) as hana, WbcSqlServerClient(settings) as wbc:
        return avalia(doc_num, hana, wbc)


def descricao(doc_num: int) -> str:
    return f"Pedido {doc_num}"


def motivo_refazendo(doc_num: int, revisao: str = "") -> str:
    versao = f" para a revisao {revisao}" if revisao else ""
    return (f"O orcamento mudou: as OPs do pedido {doc_num} estao sendo refeitas{versao}. "
            "Sincronize de novo daqui a alguns minutos para ver o resultado.")


def corrotina(doc_num: int, orc_num: str) -> Callable[[Tarefa], Awaitable[dict[str, Any]]]:
    """Reprocessar, then Processar, as one execution. Each step syncs the mirror at its end."""
    pares = [(str(doc_num), orc_num)]

    async def executa(tarefa: Tarefa) -> dict[str, Any]:
        cancelou = await acoes._roda_pedidos(tarefa, pares, modo="reprocessar", force=False)
        if cancelou.get("com_erro") or tarefa.parada_pedida:
            tarefa.anota(
                f"Pedido {doc_num}: as OPs NAO foram recriadas. Confira o pedido em Pedidos WBC.",
                problema=True,
            )
            return {"reprocessar": cancelou}
        return {"reprocessar": cancelou,
                "processar": await acoes._roda_pedidos(tarefa, pares, modo="processar", force=False)}

    return executa
