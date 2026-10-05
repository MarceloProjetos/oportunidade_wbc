"""`Recusa` — an action refused before anything was written, shared by the screens and the
JSON APIs of the Controle de Produção (29/09/2026 for Manutenção de OP, 01/10/2026 for
Pedidos WBC — docs/PLANO_API_PEDIDOS_WBC.md).

A refusal carries the screen's own message. The screens render it with ``erro.html`` (400,
as they always did); the APIs answer JSON with the HTTP status of its ``tipo``. One text,
written once — the screen and the API cannot drift apart.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from controleproducao.core.tarefas import Tarefa

# HTTP status of each refusal in the JSON APIs: "fix the request" (400), "no such thing"
# (404), "the state of the SAP or of the module says no" (409).
HTTP_DA_RECUSA = {
    "invalido": 400,
    "nao_encontrada": 404,
    "status_terminal": 409,
    "saida_lancada": 409,
    "entrada_lancada": 409,
    "ciclo": 409,
    "nada_a_encerrar": 409,
    "confirmacao_invalida": 409,
    "ocupado": 409,
    # Pedidos WBC: a selected pedido is no longer in the eligible list (processed meanwhile).
    "fora_da_lista": 409,
}


class Recusa(Exception):
    """An action refused before anything was written, with the screen's own message.

    ``detalhes``/``colunas`` are the table the screen shows under the message; ``dados`` is
    extra JSON only the API returns; ``execucao`` is the task that keeps the module busy (the
    screen links to it, the API returns its id).
    """

    def __init__(
        self,
        tipo: str,
        mensagem: str,
        *,
        titulo: str | None = None,
        detalhes: list[dict] | None = None,
        colunas: list[str] | None = None,
        dados: dict | None = None,
        execucao: Tarefa | None = None,
    ) -> None:
        if tipo not in HTTP_DA_RECUSA:
            raise ValueError(f"tipo de recusa desconhecido: {tipo!r}")
        super().__init__(mensagem)
        self.tipo = tipo
        self.mensagem = mensagem
        self.titulo = titulo
        self.detalhes = detalhes or []
        self.colunas = colunas or []
        self.dados = dados or {}
        self.execucao = execucao

    @property
    def http(self) -> int:
        return HTTP_DA_RECUSA[self.tipo]
