"""Confirmation of an irreversible operation on the web (22/09/2026).

Decision of Anderson: irreversible operations require confirmation on the web interface too.

**Why a browser `confirm()` is not enough.** On the CLI the user sees, before typing the
confirmation, the table of what is going to happen. A button on a page is much easier to press
without thinking — and a POST can be repeated by F5, by a double click or by a browser retry,
which simply does not happen on the CLI.

⚠️ This check **is not** the production gate removed on 22/09/2026: it applies equally in
homologation, and its reason is that the operation is irreversible, not that the environment
is risky.

The mechanism has two steps and solves both things at once:

1. The client asks for a **plan**: the server works out what will happen, returns it to be
   shown on screen and keeps a **token** tied to that exact plan.
2. The execution only accepts the token. A used token is not valid again, an expired token is
   not valid, and (01/10/2026) a token is only valid for the operation it was issued for.

So the user confirms what was **calculated and shown** — not what they imagine will happen —
and an F5 on the result page re-executes nothing.

Kept in memory, for the same reason as `tarefas.py`: on restart the pending tokens die and the
user redoes step 1. That is the intended effect — no token survives a restart to be executed
later against a state that may already have changed.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

# Tempo de vida do token. Curto de propósito: ele carrega um plano calculado sobre o estado
# do SAP naquele instante, e estado velho é o que faz alguém cancelar a OP errada.
VALIDADE = timedelta(minutes=10)


@dataclass
class Plano:
    """What an irreversible operation is going to do, calculated and awaiting confirmation."""

    token: str
    operacao: str
    resumo: dict
    itens: list[dict]
    criado_em: datetime
    #: Which execution route may spend this token ("processar", "encerrar"…). 01/10/2026
    #: review: the token was not tied to its operation — one checked for Processar was
    #: accepted by Reprocessar (same item shape), which cancels every planned OP of the order.
    tipo: str = ""

    @property
    def vencido(self) -> bool:
        return datetime.now() - self.criado_em > VALIDADE

    def para_json(self) -> dict:
        return {
            "token": self.token,
            "operacao": self.operacao,
            "resumo": self.resumo,
            "itens": self.itens,
            "valido_ate": (self.criado_em + VALIDADE).isoformat(timespec="seconds"),
        }


class ConfirmacaoInvalida(RuntimeError):
    """Token missing, expired, already used, or issued for another operation."""


class RegistroDePlanos:
    def __init__(self) -> None:
        self._planos: dict[str, Plano] = {}

    def criar(self, operacao: str, resumo: dict, itens: list[dict], *, tipo: str = "") -> Plano:
        plano = Plano(
            token=secrets.token_urlsafe(24),
            operacao=operacao,
            resumo=resumo,
            itens=itens,
            criado_em=datetime.now(),
            tipo=tipo,
        )
        self._planos[plano.token] = plano
        self._limpa_vencidos()
        return plano

    def consumir(self, token: str, tipo: str | None = None) -> Plano:
        """Validate and **invalidate** the token. Raises `ConfirmacaoInvalida` on any error.

        Invalidating before executing, not after, is deliberate: if the execution fails midway, the
        token must not be reusable to "try again" on a plan that no longer matches the SAP state.
        Redoing step 1 is cheap and recalculates everything.
        """
        plano = self._planos.get(token or "")
        if plano is None:
            raise ConfirmacaoInvalida(
                "Confirmação inválida ou já utilizada. Refaça a conferência antes de "
                "executar — o que seria feito precisa ser recalculado."
            )
        if plano.vencido:
            self._planos.pop(token, None)
            raise ConfirmacaoInvalida(
                "A confirmação venceu. O plano foi calculado sobre o estado do SAP de "
                "alguns minutos atrás e pode não valer mais; refaça a conferência."
            )
        if tipo is not None and plano.tipo != tipo:
            # Not spent: the right route can still use it. Reaching here takes a forged POST.
            raise ConfirmacaoInvalida(
                "Esta confirmação é de outra operação e não vale aqui. Refaça a conferência "
                "da operação que quer executar."
            )
        # Token invalidado aqui: decisão tomada, execução a seguir.
        self._planos.pop(token, None)
        return plano

    def obter(self, token: str) -> Plano | None:
        """Look up without consuming — so the screen can show the plan again."""
        plano = self._planos.get(token or "")
        return None if (plano and plano.vencido) else plano

    def _limpa_vencidos(self) -> None:
        for token in [t for t, p in self._planos.items() if p.vencido]:
            self._planos.pop(token, None)


PLANOS = RegistroDePlanos()
