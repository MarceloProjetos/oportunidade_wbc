"""entrega_combinada — Production's side of the agreed delivery date (PLANO_DATA_ENTREGA_COMBINADA, F4).

Production (the PCP team's app) asks for a new delivery date of a sales order through this
API/MCP; Comercial decides on the OrçaView screen (.90). Both write the same Supabase table,
``pedidos_entrega_alteracoes``, with service_role; every invariant lives there (CHECKs
``ck_pea_*`` + the partial unique index ``pedidos_entrega_um_pendente``). Nothing here writes
to SAP. Design: ``docs/DESENHO_DATA_ENTREGA_COMBINADA.md``.

This module holds what is pure (body validation, the reply block) and the three table calls,
which receive the Supabase client as an argument; ``api.py`` only adapts HTTP. The date
arithmetic is ``situacao_pedidos.entrega_combinada`` — ported byte for byte from the web, so
the screen and this API never disagree.
"""
from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from typing import Any

from situacao_pedidos import MOTIVOS_ENTREGA

logger = logging.getLogger(__name__)

__all__ = [
    "COLUNAS",
    "INDICE_UM_PENDENTE",
    "Recusa",
    "TABELA",
    "inserir",
    "linhas_do_pedido",
    "listar",
    "validar_corpo",
    "validar_datas",
    "violou_um_pendente",
]

TABELA = "pedidos_entrega_alteracoes"
#: Name of the partial unique index "one pending per order" (D6) — a 23505 on it is the
#: race the request flow handles, not an error.
INDICE_UM_PENDENTE = "pedidos_entrega_um_pendente"
COLUNAS = ("id,doc_num,data_nova,data_anterior,data_sap,motivo,gera_atraso,observacao,origem,"
           "situacao,solicitado_por,solicitado_cliente,solicitado_em,decidido_por,decidido_em,"
           "motivo_recusa")
OBSERVACAO_MAX = 1000
PESSOA_MAX = 80
#: How far ahead a new date may go (owner, Q4): beyond it is a typo.
HORIZONTE_DIAS = 365
#: Rows of one order. An order has a handful; the cap only guards a runaway.
_LINHAS_MAX = 500
#: Strictly AAAA-MM-DD: ``date.fromisoformat`` alone also takes ISO week dates ("2026-W47-5").
_DATA_RE = re.compile(r"\d{4}-\d{2}-\d{2}", re.ASCII)


class Recusa(Exception):
    """A request this API refuses with a stable ``motivo`` key (the contract) and a text."""

    def __init__(self, status: int, motivo: str, texto: str, **extra: Any) -> None:
        super().__init__(texto)
        self.status = status
        self.motivo = motivo
        self.texto = texto
        self.extra = extra


# ── Body ─────────────────────────────────────────────────────────────────────

def _texto_livre(valor: Any, *, limite: int, motivo: str, campo: str,
                 quebra_ok: bool, motivo_controle: str | None = None) -> str | None:
    if valor is None:
        return None
    if not isinstance(valor, str):
        raise Recusa(400, motivo_controle or motivo, f"{campo} deve ser texto")
    # A Windows textarea / PowerShell here-string sends CRLF: it is a line break, not junk.
    texto = valor.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not texto:
        return None
    if len(texto) > limite:
        raise Recusa(400, motivo, f"{campo} com mais de {limite} caracteres")
    permitido = (lambda ch: ch.isprintable() or ch == "\n") if quebra_ok else str.isprintable
    if not all(permitido(ch) for ch in texto):
        raise Recusa(400, motivo_controle or motivo, f"{campo} com caractere de controle")
    return texto


def validar_corpo(corpo: Any, *, solicitante_declarado: str | None) -> dict[str, Any]:
    """The POST body → ``{data_nova, motivo, observacao, solicitado_por}``, or ``Recusa(400)``.

    ``solicitante_declarado`` is the person a trusted backend vouches for (``X-SIS-Usuario``);
    when present it wins over the body's ``solicitante``.
    """
    if not isinstance(corpo, dict):
        raise Recusa(400, "corpo_invalido", "corpo JSON ausente ou nao e um objeto")

    bruto = corpo.get("data_nova")
    if not isinstance(bruto, str) or not _DATA_RE.fullmatch(bruto):
        raise Recusa(400, "data_invalida", "data_nova deve ser AAAA-MM-DD")
    try:
        data_nova = date.fromisoformat(bruto)
    except ValueError:
        raise Recusa(400, "data_invalida", "data_nova deve ser uma data valida AAAA-MM-DD") from None

    motivo = corpo.get("motivo")
    if not isinstance(motivo, str) or motivo not in MOTIVOS_ENTREGA:
        raise Recusa(400, "motivo_invalido", f"motivo deve ser um de: {', '.join(MOTIVOS_ENTREGA)}",
                     motivos=list(MOTIVOS_ENTREGA))

    observacao = _texto_livre(corpo.get("observacao"), limite=OBSERVACAO_MAX,
                              motivo="observacao_longa", campo="observacao", quebra_ok=True,
                              motivo_controle="observacao_invalida")

    pessoa = solicitante_declarado or _texto_livre(
        corpo.get("solicitante"), limite=PESSOA_MAX, motivo="solicitante_invalido",
        campo="solicitante", quebra_ok=False)
    if not pessoa:
        raise Recusa(400, "solicitante_invalido",
                     "informe 'solicitante': o nome de quem pede (ate 80 caracteres)")
    return {"data_nova": data_nova, "motivo": motivo, "observacao": observacao,
            "solicitado_por": pessoa}


def validar_datas(data_nova: date, *, hoje: date, data_vigente: str | None) -> None:
    """Date rules of a request (Q4): today is fine, yesterday is not; up to a year ahead;
    never the date already agreed. Bringing a date forward is accepted."""
    if data_nova < hoje:
        raise Recusa(422, "data_no_passado", f"a nova data ({data_nova.isoformat()}) ja passou")
    if data_nova > hoje + timedelta(days=HORIZONTE_DIAS):
        raise Recusa(422, "data_muito_distante",
                     f"a nova data passa de {HORIZONTE_DIAS} dias a partir de hoje - confira o ano")
    if data_vigente and data_nova.isoformat() == data_vigente:
        raise Recusa(422, "data_igual_a_vigente", "essa ja e a data combinada do pedido")


# ── Table ────────────────────────────────────────────────────────────────────

def linhas_do_pedido(cliente: Any, doc_num: int) -> list[dict[str, Any]]:
    """Every row of one order, oldest first. Raises on any base failure (the caller → 503)."""
    res = (cliente.table(TABELA).select(COLUNAS).eq("doc_num", int(doc_num))
           .order("id").limit(_LINHAS_MAX).execute())
    return list(res.data or [])


def violou_um_pendente(exc: Exception) -> bool:
    """A postgrest ``APIError`` 23505 on the one-pending index (D6) — the race, not a bug."""
    codigo = str(getattr(exc, "code", "") or "")
    texto = f"{getattr(exc, 'message', '')} {getattr(exc, 'details', '')} {exc}"
    return codigo == "23505" and INDICE_UM_PENDENTE in texto


def inserir(cliente: Any, linha: dict[str, Any]) -> dict[str, Any]:
    """INSERT one pending request; returns the stored row. Raises the client's error as is."""
    res = cliente.table(TABELA).insert(linha).execute()
    dados = res.data or []
    if not dados or "id" not in dados[0]:
        # Written or not, we cannot show it: the caller answers "check with the GET".
        raise RuntimeError("insert sem a linha gravada na resposta")
    return dados[0]


def listar(cliente: Any, *, situacao: str, desde: str, limite: int) -> list[dict[str, Any]]:
    """Requests across orders, newest first.

    ``situacao``: ``pendente`` (all pending, whatever their age), ``decididas`` (decided since
    ``desde``, Comercial's direct changes included) or ``todas`` (asked or decided since then).
    """
    q = cliente.table(TABELA).select(COLUNAS)
    if situacao == "pendente":
        q = q.eq("situacao", "pendente").order("solicitado_em", desc=True)
    elif situacao == "decididas":
        q = q.neq("situacao", "pendente").gte("decidido_em", desde).order("decidido_em", desc=True)
    else:
        q = q.or_(f"solicitado_em.gte.{desde},decidido_em.gte.{desde}").order("id", desc=True)
    return list(q.limit(limite).execute().data or [])
