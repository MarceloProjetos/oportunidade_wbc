"""Confirmação de operação irreversível na web (22/09/2026).

Decisão do Anderson: operações irreversíveis exigem confirmação também na interface web.

**Por que não basta um `confirm()` no navegador.** Na CLI o usuário vê, antes de digitar
a confirmação, a tabela do que vai acontecer. Um botão numa página é bem mais fácil de
apertar sem pensar — e um POST pode ser repetido por F5, por duplo clique ou por um retry
do navegador, o que na CLI simplesmente não acontece.

⚠️ Esta conferência **não é** a trava de produção removida em 22/09/2026: ela vale igual
em homologação, e o motivo dela é a operação ser irreversível, não o ambiente ser
arriscado.

O mecanismo é de duas etapas, e resolve as duas coisas de uma vez:

1. O cliente pede um **plano**: o servidor levanta o que vai acontecer, devolve isso para
   ser mostrado na tela e guarda um **token** ligado àquele plano exato.
2. A execução só aceita o token. Token usado não vale de novo, token vencido não vale.

Com isso, o usuário confirma sobre o que foi **calculado e mostrado** — não sobre o que ele
imagina que vai acontecer — e um F5 na página de resultado não reexecuta nada.

Guardado em memória, pelo mesmo motivo de `tarefas.py`: reiniciou, os tokens pendentes
morrem, e o usuário refaz o passo 1. É o efeito desejado — nenhum token sobrevive a um
reinício para ser executado depois, contra um estado que já pode ter mudado.
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
    """O que uma operação irreversível vai fazer, calculado e aguardando confirmação."""

    token: str
    operacao: str
    resumo: dict
    itens: list[dict]
    criado_em: datetime

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
    """Token ausente, vencido, já usado ou com o texto de confirmação errado."""


class RegistroDePlanos:
    def __init__(self) -> None:
        self._planos: dict[str, Plano] = {}

    def criar(self, operacao: str, resumo: dict, itens: list[dict]) -> Plano:
        plano = Plano(
            token=secrets.token_urlsafe(24),
            operacao=operacao,
            resumo=resumo,
            itens=itens,
            criado_em=datetime.now(),
        )
        self._planos[plano.token] = plano
        self._limpa_vencidos()
        return plano

    def consumir(self, token: str) -> Plano:
        """Valida e **invalida** o token. Levanta `ConfirmacaoInvalida` em qualquer erro.

        Invalidar antes de executar, e não depois, é deliberado: se a execução falhar no
        meio, o token não pode ser reaproveitado para "tentar de novo" sobre um plano que
        já não corresponde ao estado do SAP. Refazer o passo 1 é barato e recalcula tudo.
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
        # Token invalidado aqui: decisão tomada, execução a seguir.
        self._planos.pop(token, None)
        return plano

    def obter(self, token: str) -> Plano | None:
        """Consulta sem consumir — para a tela reexibir o plano."""
        plano = self._planos.get(token or "")
        return None if (plano and plano.vencido) else plano

    def _limpa_vencidos(self) -> None:
        for token in [t for t, p in self._planos.items() if p.vencido]:
            self._planos.pop(token, None)


PLANOS = RegistroDePlanos()
