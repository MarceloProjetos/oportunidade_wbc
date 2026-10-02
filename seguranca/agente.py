"""Rules that apply only to credentials marked as an agent (rules 7 and 8 of
docs/PLANO_MIRA_AGENTE_11.md).

- **Off switch** (rule 8): ``state/agente.desligado`` cuts the agent in one step, without
  restarting anything — the same idea as the worker's stop file. Content ``escrita`` stops
  only the writes; anything else (or empty) stops everything. ``python -m seguranca
  desligar-agente`` writes it; ``religar-agente`` removes it.
- **Business hours** (rule 7, owner 02/10/2026): an agent writes only Mon–Fri 07:00–19:00, not
  on national holidays — nobody is around to check an OP created at 2 a.m. Reading is always
  allowed (unless switched off).

People (the screens, the master key, the other team) are not affected by either rule.
"""
from __future__ import annotations

import os
from datetime import datetime, time
from pathlib import Path

from feriados_br import is_business_day
from seguranca.credenciais import RAIZ, Cliente

ARQUIVO_PADRAO = RAIZ / "state" / "agente.desligado"
INICIO = time(7, 0)
FIM = time(19, 0)

LIGADO = "ligado"
SO_LEITURA = "so_leitura"
DESLIGADO = "desligado"


def _arquivo() -> Path:
    return Path(os.environ.get("SIS_AGENTE_INTERRUPTOR") or ARQUIVO_PADRAO)


def estado() -> str:
    arquivo = _arquivo()
    if not arquivo.exists():
        return LIGADO
    try:
        conteudo = arquivo.read_text(encoding="utf-8").strip().lower()
    except OSError:
        return DESLIGADO    # cannot read the switch → fail closed
    return SO_LEITURA if conteudo.startswith("escrita") else DESLIGADO


def desligar(so_escrita: bool = False, motivo: str = "") -> None:
    arquivo = _arquivo()
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    arquivo.write_text(("escrita" if so_escrita else "tudo") + (f"\n{motivo}" if motivo else "") + "\n",
                       encoding="utf-8")


def religar() -> bool:
    try:
        _arquivo().unlink()
        return True
    except FileNotFoundError:
        return False


def no_expediente(agora: datetime | None = None) -> bool:
    agora = agora or datetime.now()
    return is_business_day(agora.date()) and INICIO <= agora.time() < FIM


def recusa(cliente: Cliente, *, escrita: bool, agora: datetime | None = None) -> str | None:
    """Why this agent call is refused, in words for the person — or ``None`` if allowed."""
    if not cliente.agente:
        return None
    situacao = estado()
    if situacao == DESLIGADO:
        return "O agente está desligado nesta máquina (interruptor da .11). Fale com o TI."
    if escrita and situacao == SO_LEITURA:
        return "As escritas do agente estão desligadas nesta máquina (interruptor da .11). Ele só consulta."
    if escrita and not no_expediente(agora):
        return "O agente só grava em dia útil, das 7h às 19h. Fora disso ele apenas consulta."
    return None
