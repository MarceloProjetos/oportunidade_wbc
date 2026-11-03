"""Environment notice and write gate (21/09/2026 · revised 22/09 and 28/09/2026).

Until 22/09/2026 this module was a two-key lock (`WBC_BLOCK_PRODUCTION_WRITES` in `.env` +
`--producao` per run, and on the web typing the company DB name), removed at Anderson's
request. What stayed: the red banner on every page when the target is production
(`core/templates.py` sets the environment as a Jinja global), the two-step confirmation with
a single-use token (`core/confirmacao.py` — never the production gate, it applies equally in
homologação) and the log notice on every production write.

Since 2026-09-28 (package inside the SIS) a gate is back — keyed on the MACHINE, not on a
`.env` switch. That is the SIS rule for production features: a switch vanishes in a `.env`
rewrite and the feature dies silently; the machine's IP does not. Production writes are
allowed only on the host that owns `wbcpython.safety.PRODUCTION_MACHINE_IP` (the .11);
homologação is never blocked, so the retest runs from any dev box. The hard stop lives in
`ServiceLayerClient` (every write leaves through it); this module is the fail-fast in
front of it, so the web answers 503 and the CLI exits before a task is even created —
instead of a task that ends "concluída com falhas" because `audit_log` swallowed the error.
"""
from __future__ import annotations

from controleproducao.config import Settings
from wbcpython import safety
from wbcpython.safety import ProductionWriteBlocked

__all__ = ["ProductionWriteBlocked", "ambiente_descrito", "aviso_de_escrita"]


def ambiente_descrito(settings: Settings) -> str:
    """Short label of the write target for banners and logs. Never includes credentials."""
    destino = "PRODUÇÃO" if settings.is_production else "homologação"
    return f"{destino} — company DB '{settings.sl_company_db}'"


def aviso_de_escrita(settings: Settings, operacao: str) -> str | None:
    """Notice text for a write operation, or `None` outside production.

    Homologação gets no noise: a notice that always shows stops being read exactly when it
    starts to matter.

    Raises:
        ProductionWriteBlocked: target is production and this is not the production
            machine — before any network call, before any task is created.
    """
    if not settings.is_production:
        return None
    if not safety.is_production_machine():
        raise ProductionWriteBlocked(
            f"Escrita em {ambiente_descrito(settings)} recusada: só a máquina de produção "
            f"({safety.PRODUCTION_MACHINE_IP}) grava em produção — operação: {operacao}. "
            "Homologação continua liberada em qualquer máquina."
        )
    return f"ATENÇÃO: gravando em {ambiente_descrito(settings)} · operação: {operacao}."
