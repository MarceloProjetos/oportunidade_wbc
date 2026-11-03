"""Environment safety guards shared by the three SAP writers (worker, CP, OP status).

Two rules, originally from the WBC spec (``docs/wbc/ai_spec/00_index.md``):

Rule 1 — production writes only from the .11:
    A write (POST/PATCH/PUT/DELETE/MERGE, or write SQL) aimed at the production company DB
    (SBOALTAMIRAPROD) is blocked unless this process runs on :data:`PRODUCTION_MACHINE_IP`.
    The original "production is immutable" rule was revoked at go-live (2026-09-02); since
    2026-09-28 the switch is machine identity, not a ``.env`` key — callers pass
    ``block_production_writes=not is_production_machine()``.

Rule 5 — the WBC SQL Server is read-only:
    No write ever runs on the WBC SQL Server, in any environment; only the WBC itself writes
    there. Unlike Rule 1 this guard has **no off switch**.

The checks live here, once, so the infrastructure adapters (Service Layer, SQL Server, HANA)
do not each reimplement them and so they can be tested in isolation.
"""

from __future__ import annotations

import re
import socket

# The only host allowed to write to the production company DB (decision 2026-09-28). Machine
# identity instead of a `.env` switch: a switch can vanish in a `.env` rewrite, and the worker
# would keep cycling with every write blocked. Not overridable from the environment on purpose.
PRODUCTION_MACHINE_IP = "192.168.7.11"

# Métodos HTTP considerados de escrita no Service Layer.
WRITE_METHODS: frozenset[str] = frozenset({"POST", "PATCH", "PUT", "DELETE", "MERGE"})

# Palavras-chave que caracterizam SQL de escrita.
_SQL_WRITE_KEYWORDS: frozenset[str] = frozenset(
    {
        "insert",
        "update",
        "delete",
        "merge",
        "upsert",
        "truncate",
        "drop",
        "alter",
        "create",
        "replace",
        "grant",
        "revoke",
        "call",
        "exec",
        "execute",
        "into",  # 'SELECT ... INTO nova_tabela' cria tabela: é escrita.
    }
)


class SafetyViolation(RuntimeError):
    """Base of the project's safety-rule violations.

    Never catch and swallow these in application code: they signal a broken project rule,
    not a transient failure worth retrying.
    """


class ProductionWriteBlocked(SafetyViolation):
    """A write would reach production from a machine that is not the .11."""


class ReadOnlyViolation(SafetyViolation):
    """A write was attempted on a source declared read-only.

    Applies to the WBC SQL Server (Rule 5) and, as a precaution, to direct HANA access, which
    only reads views.
    """


def owns_ipv4(ip: str) -> bool:
    """True if a local interface holds ``ip``.

    Binding a UDP socket only succeeds on an owned address — reliable on multi-NIC hosts where
    hostname resolution is incomplete. No packet is sent. Same check as the web's
    ``config._owns_ipv4``.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.bind((ip, 0))
        return True
    except OSError:
        return False


def is_production_machine() -> bool:
    """True only on the .11 — the machine whose writes to production are intended."""
    return owns_ipv4(PRODUCTION_MACHINE_IP)


def is_production(target: str | None, production_company_db: str) -> bool:
    """True if ``target`` (company DB or schema) is production.

    Case-insensitive and whitespace-trimmed: the same name is spelled differently across
    config, URL and SQL.
    """
    if target is None:
        return False
    return target.strip().casefold() == production_company_db.strip().casefold()


def assert_write_allowed(
    target_company_db: str | None,
    *,
    production_company_db: str,
    block_production_writes: bool = True,
    operation: str = "escrita",
) -> None:
    """Refuse a write aimed at production while the guard is on.

    Fails **closed**: when the target, or the name that counts as production, is unknown, the
    write is refused. This code used to let it pass on incomplete config, which was the quietest
    bypass of all — an empty or mistyped ``WBC_PRODUCTION_COMPANY_DB`` made no target look like
    production.

    Args:
        target_company_db: Company DB (or HANA schema) the write would hit.
        production_company_db: Name treated as production.
        block_production_writes: Guard on (default). Callers derive it from the machine
            (``not is_production_machine()``), never from the environment.
        operation: Short label for the error message.

    Raises:
        ProductionWriteBlocked: The write would hit production, or the config cannot prove
            it does **not**.
    """
    if not block_production_writes:
        return

    if not production_company_db or not production_company_db.strip():
        raise ProductionWriteBlocked(
            f"Operação de {operation} bloqueada: o nome da company DB de produção não está "
            f"configurado (WBC_PRODUCTION_COMPANY_DB vazio). Sem ele, a trava não tem como "
            f"reconhecer a produção — e deixar passar seria pior. Configure a variável antes "
            f"de qualquer escrita."
        )

    if target_company_db is None or not target_company_db.strip():
        raise ProductionWriteBlocked(
            f"Operação de {operation} bloqueada: o destino não foi informado. A trava recusa "
            f"escritas de destino desconhecido, porque não teria como garantir que não são "
            f"em produção."
        )

    if is_production(target_company_db, production_company_db):
        raise ProductionWriteBlocked(
            f"Operação de {operation} bloqueada: o destino '{target_company_db}' é a "
            f"company DB de produção. Regra do projeto (ai_spec/04_environment_constraints.md): "
            f"toda escrita deve usar o ambiente de homologação. "
            f"Se esta operação for realmente necessária, ela exige autorização humana "
            f"explícita — não contorne esta trava em código."
        )


def assert_http_call_allowed(
    method: str,
    target_company_db: str | None,
    *,
    production_company_db: str,
    block_production_writes: bool = True,
) -> None:
    """Apply :func:`assert_write_allowed` to a Service Layer HTTP call.

    Reads (GET) against production always pass; only :data:`WRITE_METHODS` are checked.
    """
    if method.strip().upper() not in WRITE_METHODS:
        return
    assert_write_allowed(
        target_company_db,
        production_company_db=production_company_db,
        block_production_writes=block_production_writes,
        operation=f"escrita ({method.strip().upper()})",
    )


def _strip_noise(sql: str) -> str:
    """Drop comments, literals and quoted identifiers, keeping only the statement structure.

    Avoids the classic false positive ``SELECT * FROM t WHERE obs = 'favor update'``.

    Must be a **state scanner**, not a chain of regexes: an earlier version stripped comments
    before literals, so a ``--`` inside a literal erased the rest of the statement and let
    ``SELECT 'a--b' FROM t; DROP TABLE Alvo`` through with the ``DROP`` invisible.

    Double-quoted identifiers (common in HANA, e.g. ``"VW_ALGO"``) become a neutral marker so
    an identifier named ``insert`` is not mistaken for the verb. Returns casefolded text.
    """
    NORMAL, LITERAL, COMENTARIO_LINHA, COMENTARIO_BLOCO, IDENTIFICADOR = range(5)

    saida: list[str] = []
    estado = NORMAL
    i = 0
    total = len(sql)

    while i < total:
        atual = sql[i]
        proximo = sql[i + 1] if i + 1 < total else ""

        if estado == NORMAL:
            if atual == "'":
                estado = LITERAL
                saida.append(" '' ")
                i += 1
            elif atual == '"':
                estado = IDENTIFICADOR
                saida.append(" ident ")
                i += 1
            elif atual == "-" and proximo == "-":
                estado = COMENTARIO_LINHA
                i += 2
            elif atual == "/" and proximo == "*":
                estado = COMENTARIO_BLOCO
                i += 2
            else:
                saida.append(atual)
                i += 1

        elif estado == LITERAL:
            if atual == "'":
                # '' dentro de um literal é uma aspas escapada, não o fim dele.
                if proximo == "'":
                    i += 2
                else:
                    estado = NORMAL
                    i += 1
            else:
                i += 1

        elif estado == IDENTIFICADOR:
            if atual == '"':
                if proximo == '"':
                    i += 2
                else:
                    estado = NORMAL
                    i += 1
            else:
                i += 1

        elif estado == COMENTARIO_LINHA:
            if atual == "\n":
                estado = NORMAL
                saida.append(" ")
            i += 1

        else:  # COMENTARIO_BLOCO
            if atual == "*" and proximo == "/":
                estado = NORMAL
                saida.append(" ")
                i += 2
            else:
                i += 1

    return " ".join("".join(saida).split()).casefold()


def _tokens(texto: str) -> list[str]:
    return re.findall(r"[a-z_]+", texto)


def looks_like_write_sql(sql: str) -> bool:
    """Heuristic: True if any statement in ``sql`` writes.

    Deliberately conservative — when unsure, it says write: a refused SELECT costs far less
    than an unwanted write. Each ``;``-separated statement is judged on its own, so
    ``SELECT 1; DROP TABLE T`` is a write. A statement starting with an unknown verb is a write.
    """
    limpo = _strip_noise(sql)
    if not limpo:
        return False

    for comando in limpo.split(";"):
        comando = comando.strip()
        if not comando:
            continue

        tokens = _tokens(comando)
        if not tokens:
            continue

        primeiro = tokens[0]
        if primeiro in _SQL_WRITE_KEYWORDS:
            return True

        if primeiro in ("select", "with"):
            # Um SELECT/CTE ainda pode desembocar em INSERT/UPDATE/... ou em
            # 'SELECT ... INTO nova_tabela'. Verifica o corpo inteiro.
            if any(token in _SQL_WRITE_KEYWORDS for token in tokens[1:]):
                return True
            continue

        # Comando que não começa com verbo de leitura conhecido: por precaução,
        # trata como escrita.
        if primeiro not in ("explain", "describe", "show", "values", "table"):
            return True

    return False


def assert_read_only_sql(sql: str, *, fonte: str = "SQL Server do WBC") -> None:
    """Rule 5: refuse any write SQL on a read-only source. Has no off switch.

    Raises:
        ReadOnlyViolation: ``sql`` contains any write.
    """
    if looks_like_write_sql(sql):
        raise ReadOnlyViolation(
            f"Operação de escrita bloqueada: {fonte} é acesso SOMENTE LEITURA "
            f"por diretiva do projeto (ai_spec/04_environment_constraints.md, Regra 5). "
            f"A integração apenas consome dados do WBC — quem escreve no WBC é o próprio "
            f"sistema WBC. Esta trava não possui chave de desligamento; se houver "
            f"necessidade real de gravar, isso exige decisão humana e mudança de escopo."
        )


def assert_sql_allowed(
    sql: str,
    target_schema: str | None,
    *,
    production_company_db: str,
    block_production_writes: bool = True,
) -> None:
    """Apply :func:`assert_write_allowed` to write SQL (SQL Server or HANA); reads always pass.

    The HANA read views run against the production schema, so SELECT must never be blocked.
    """
    if not looks_like_write_sql(sql):
        return
    assert_write_allowed(
        target_schema,
        production_company_db=production_company_db,
        block_production_writes=block_production_writes,
        operation="escrita SQL",
    )
