"""Aviso de ambiente e trava de escrita (21/09/2026 · revisto em 22/09 · 28/09/2026).

Até 22/09/2026 este módulo era uma trava de duas chaves (`WBC_BLOCK_PRODUCTION_WRITES` no
`.env` + `--producao` por execução, e na web a digitação do nome da company DB), removida
a pedido do Anderson. O que ficou: a faixa vermelha no topo de toda página quando o alvo é
produção (`core/templates.py` põe o ambiente como global do Jinja), a conferência em duas
etapas com token de uso único (`core/confirmacao.py` — nunca foi a trava de produção, vale
igual em homologação) e o aviso em log a cada escrita em produção.

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

__all__ = ["ProductionWriteBlocked", "ambiente_descrito", "aviso_de_escrita", "escrita_permitida"]


def ambiente_descrito(settings: Settings) -> str:
    """Rótulo curto do alvo da escrita, para banners e logs. Nunca inclui credenciais."""
    destino = "PRODUÇÃO" if settings.is_production else "homologação"
    return f"{destino} — company DB '{settings.sl_company_db}'"


def escrita_permitida(settings: Settings) -> bool:
    """Production writes only from the production machine; anything else, always."""
    return not settings.is_production or safety.is_production_machine()


def aviso_de_escrita(settings: Settings, operacao: str) -> str | None:
    """Texto do aviso para uma operação de escrita, ou `None` fora de produção.

    Homologação não ganha ruído — é onde se trabalha o dia inteiro, e um aviso que aparece
    sempre deixa de ser lido justamente quando passa a importar.

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
