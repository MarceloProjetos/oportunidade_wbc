"""Last executions of the Execuções screen, kept in Supabase (29/09/2026).

``core/tarefas.py`` keeps executions in process memory, so restarting the service (every
deploy) wiped the screen. Each finished execution is now also written to
``controle_producao_execucoes`` and the table is pruned to the ``MAX_GUARDADAS`` most
recent rows — insert then prune by ``id``, the same pattern as
``pipeline_core.SupabaseLoader.registrar_sync_log``. DDL: ``sql/controle_producao_execucoes.sql``.

Rules, each one a decision:

- **Only the .11 writes and reads** (``safety.is_production_machine``, by IP — no ``.env``
  flag). The table is production history; a notebook run must not land in it, and a
  notebook screen listing the .11's runs next to its own in-memory ones would be confusing.
- **Never in the way of the execution.** ``guardar`` runs in a worker thread after the task
  has finished and swallows every error (logged as WARNING): the SAP writes already happened
  and a Supabase outage must not turn them into a failure.
- **Reads fail loud, to the caller.** ``listar``/``obter`` raise; the screen catches it and
  says the stored history is unavailable instead of silently showing a shorter list.
- ``upsert`` on ``tarefa_id``: a retry after a timeout that actually reached the server
  cannot duplicate a row.
- Service role key only: the table has RLS on and no policy (like the other SIS log tables),
  so the anon key would read nothing.
- ``solicitante`` / ``origem`` (29/09/2026, PLANO_API_MANUTENCAO_OP F2): who asked and through
  what (``tela`` | ``api``). Written on every row; rows stored before the columns existed
  come back as ``tela`` with no requester (the column default).
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from controleproducao.config import Settings, get_settings
from controleproducao.core.tarefas import MAX_LINHAS_LOG, MAX_NA_TELA, ORIGEM_TELA, Tarefa, desfecho_de
from wbcpython import safety

logger = logging.getLogger(__name__)

TABELA = "controle_producao_execucoes"

# How many finished executions the table keeps (the 31st write prunes the oldest). Same
# number the screen lists.
MAX_GUARDADAS = MAX_NA_TELA

# REST timeout. The list page waits on this, so it is short: a slow Supabase must cost the
# user seconds, not the 120 s default of the ETL loader.
TIMEOUT_S = 8.0

# Pauses between write attempts (3 attempts). The write runs in a worker thread after the
# task ended, so waiting here delays nobody.
PAUSAS_ENTRE_TENTATIVAS_S = (2.0, 5.0)

# Columns of the list: everything but the log lines and the result, which can be large.
_COLUNAS_DA_LISTA = (
    "tarefa_id,modulo,nome,descricao,situacao,com_falhas,criada_em,duracao_segundos,"
    "solicitante,origem"
)

# Task ids come from `uuid4().hex[:12]`; anything else never reaches PostgREST.
_ID_VALIDO = re.compile(r"^[0-9a-f]{12}$")


@dataclass(frozen=True)
class ExecucaoGuardada:
    """One row of the list, with the attribute names `tarefas.html` reads from a `Tarefa`."""

    id: str
    modulo: str
    nome: str
    descricao: str
    situacao: str
    com_falhas: bool
    criada_em: datetime
    duracao_segundos: float | None
    solicitante: str | None = None
    origem: str = ORIGEM_TELA
    terminada: bool = True
    guardada: bool = True

    @property
    def desfecho(self) -> str:
        return desfecho_de(self.situacao, self.com_falhas)


class HistoricoSupabase:
    """Writes, prunes and reads the stored executions. Client created on first use."""

    def __init__(
        self,
        url: str,
        chave: str,
        *,
        maximo: int = MAX_GUARDADAS,
        timeout_s: float = TIMEOUT_S,
        pausas_s: tuple[float, ...] = PAUSAS_ENTRE_TENTATIVAS_S,
    ) -> None:
        self._url = url
        self._chave = chave
        self.maximo = maximo
        self._timeout_s = timeout_s
        self._pausas_s = pausas_s
        self._cliente: Any = None
        self._trava = threading.Lock()

    def _tabela(self) -> Any:
        with self._trava:
            if self._cliente is None:
                # Imported here: the package must import (and its tests run) without touching
                # the Supabase client, which the test suite blocks on purpose.
                from supabase import create_client
                from supabase.client import ClientOptions

                self._cliente = create_client(
                    self._url, self._chave, ClientOptions(postgrest_client_timeout=self._timeout_s)
                )
        return self._cliente.table(TABELA)

    # -- write -------------------------------------------------------------------------
    def guardar(self, tarefa: Tarefa) -> bool:
        """Store a finished execution and prune the table. Never raises.

        Returns whether the row was written (the prune is best effort on its own: a failed
        prune leaves 31 rows, and the next write prunes again).
        """
        if not tarefa.terminada:
            logger.warning("Execução %s ainda não terminou; não guardada.", tarefa.id)
            return False
        registro = registro_de(tarefa)
        tentativas = len(self._pausas_s) + 1
        for tentativa in range(1, tentativas + 1):
            try:
                self._tabela().upsert(registro, on_conflict="tarefa_id").execute()
                break
            except Exception as exc:  # noqa: BLE001 - a lost history row never fails the task
                if tentativa == tentativas:
                    logger.warning(
                        "Execução %s (%s) não foi guardada no Supabase após %d tentativa(s): %s",
                        tarefa.id, tarefa.nome, tentativas, exc,
                    )
                    return False
                time.sleep(self._pausas_s[tentativa - 1])
        try:
            self._podar()
        except Exception as exc:  # noqa: BLE001 - see the docstring
            logger.warning("Execução %s guardada, mas a poda de '%s' falhou: %s", tarefa.id, TABELA, exc)
        logger.info("Execução %s (%s, %s) guardada no Supabase.", tarefa.id, tarefa.nome, tarefa.situacao)
        return True

    def _podar(self) -> None:
        """Delete every row past the ``maximo`` most recent (by ``id``, insertion order)."""
        excedentes = (
            self._tabela().select("id").order("id", desc=True)
            .range(self.maximo, self.maximo + 999).execute()
        )
        ids = [linha["id"] for linha in (excedentes.data or [])]
        if ids:
            self._tabela().delete().in_("id", ids).execute()
            logger.info("Histórico de execuções: %d registro(s) antigo(s) apagado(s).", len(ids))

    # -- read --------------------------------------------------------------------------
    def listar(self, limite: int | None = None) -> list[ExecucaoGuardada]:
        """Most recent first. Raises on any Supabase failure (the caller shows a notice)."""
        resposta = (
            self._tabela().select(_COLUNAS_DA_LISTA).order("criada_em", desc=True)
            .limit(limite or self.maximo).execute()
        )
        return [_resumo(linha) for linha in (resposta.data or [])]

    def obter(self, tarefa_id: str) -> Tarefa | None:
        """The full stored execution, rebuilt as a finished `Tarefa`. Raises on failure."""
        if not _ID_VALIDO.match(tarefa_id or ""):
            return None
        resposta = self._tabela().select("*").eq("tarefa_id", tarefa_id).limit(1).execute()
        linhas = resposta.data or []
        return _tarefa(linhas[0]) if linhas else None


def da_maquina(settings: Settings | None = None) -> HistoricoSupabase | None:
    """The history this machine keeps: Supabase on the .11, none (memory only) elsewhere."""
    if not safety.is_production_machine():
        # DEBUG: asked again every few minutes while there is no history (tarefas.py).
        logger.debug("Histórico de execuções: só em memória (esta máquina não é a .11).")
        return None
    s = settings or get_settings()
    url = s.supabase_url.strip()
    chave = s.supabase_service_role_key.get_secret_value().strip()
    if not url or not chave:
        logger.warning(
            "Histórico de execuções: SUPABASE_URL ou SUPABASE_SERVICE_ROLE_KEY ausente no .env; "
            "a tela fica só com a memória do processo."
        )
        return None
    logger.info("Histórico de execuções: as últimas %d ficam no Supabase (%s).", MAX_GUARDADAS, TABELA)
    return HistoricoSupabase(url, chave)


# -- row <-> Tarefa ------------------------------------------------------------------------
def registro_de(tarefa: Tarefa) -> dict[str, Any]:
    """The table row of a finished execution."""
    return {
        "tarefa_id": tarefa.id,
        "modulo": tarefa.modulo,
        "nome": tarefa.nome,
        "descricao": tarefa.descricao,
        "situacao": tarefa.situacao,
        "com_falhas": tarefa.com_falhas,
        "criada_em": _para_iso(tarefa.criada_em),
        "iniciada_em": _para_iso(tarefa.iniciada_em),
        "terminada_em": _para_iso(tarefa.terminada_em),
        "duracao_segundos": tarefa.duracao_segundos,
        "passo": tarefa.passo,
        "passos_feitos": tarefa.passos_feitos,
        "passos_total": tarefa.passos_total,
        "linhas": list(tarefa.linhas),
        "resultado": _json_seguro(tarefa.resultado),
        "erro": tarefa.erro,
        "solicitante": tarefa.solicitante,
        "origem": tarefa.origem,
    }


def _para_iso(momento: datetime | None) -> str | None:
    # Task times are naive local time (datetime.now()); astimezone() stamps the machine's
    # offset so timestamptz stores the real instant.
    return momento.astimezone().isoformat(timespec="seconds") if momento else None


def _de_iso(texto: str | None) -> datetime | None:
    # Back to naive local time: the screen compares and formats these next to the
    # in-memory tasks, which are naive.
    if not texto:
        return None
    return datetime.fromisoformat(texto).astimezone().replace(tzinfo=None)


def _json_seguro(valor: Any) -> Any:
    """The result as plain JSON (Decimal/datetime become text; NaN is refused by Postgres)."""
    if valor is None:
        return None
    try:
        return json.loads(json.dumps(valor, default=str, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError):
        return {"texto": repr(valor)[:10_000]}


def _resumo(linha: dict[str, Any]) -> ExecucaoGuardada:
    return ExecucaoGuardada(
        id=linha["tarefa_id"],
        modulo=linha.get("modulo") or "",
        nome=linha.get("nome") or "",
        descricao=linha.get("descricao") or "",
        situacao=linha.get("situacao") or "",
        com_falhas=bool(linha.get("com_falhas")),
        criada_em=_de_iso(linha.get("criada_em")) or datetime.min,
        duracao_segundos=(
            float(linha["duracao_segundos"]) if linha.get("duracao_segundos") is not None else None
        ),
        solicitante=linha.get("solicitante"),
        origem=linha.get("origem") or ORIGEM_TELA,
    )


def _tarefa(linha: dict[str, Any]) -> Tarefa:
    return Tarefa(
        id=linha["tarefa_id"],
        nome=linha.get("nome") or "",
        descricao=linha.get("descricao") or "",
        criada_em=_de_iso(linha.get("criada_em")) or datetime.min,
        modulo=linha.get("modulo") or "",
        situacao=linha.get("situacao") or "",
        iniciada_em=_de_iso(linha.get("iniciada_em")),
        terminada_em=_de_iso(linha.get("terminada_em")),
        passo=linha.get("passo") or "",
        passos_feitos=int(linha.get("passos_feitos") or 0),
        passos_total=int(linha.get("passos_total") or 0),
        linhas=deque(linha.get("linhas") or [], maxlen=MAX_LINHAS_LOG),
        resultado=linha.get("resultado"),
        erro=linha.get("erro"),
        guardada=True,
        solicitante=linha.get("solicitante"),
        origem=linha.get("origem") or ORIGEM_TELA,
    )
