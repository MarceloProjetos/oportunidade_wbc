"""Rotas de acompanhamento das tarefas em segundo plano (22/09/2026).

Fica em `core/` e não num módulo porque é infraestrutura compartilhada: os módulos 2 e 3
criam tarefas, esta é a tela única onde se acompanha qualquer uma delas. Uma página de
acompanhamento por módulo seria três cópias da mesma coisa divergindo com o tempo.

O acompanhamento é por *polling* de um JSON, não por WebSocket. O estado da tarefa vive em
memória no mesmo processo (ver `tarefas.py`); um GET a cada 2s custa nada numa aplicação
interna e não acrescenta nem conexão persistente nem reconexão para manter.

Since 29/09/2026 the list and the detail also read the Supabase history on the .11
(`core/historico.py`): a finished execution survives a restart, read-only.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from controleproducao.core.tarefas import MAX_NA_TELA, TAREFAS, Tarefa
from controleproducao.core.templates import templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tarefas", tags=["tarefas"])

_NAO_ENCONTRADA = (
    "Execução não encontrada. Ela pode ter saído do histórico (ficam as {n} mais recentes) "
    "ou o serviço foi reiniciado antes de ela ser guardada."
)
_HISTORICO_FORA = (
    "O histórico guardado no Supabase não respondeu agora — tente de novo em instantes. "
    "As execuções em andamento continuam visíveis na lista."
)


async def _procura(tarefa_id: str) -> tuple[Tarefa | None, int, str]:
    """(task, HTTP status, message). "Not found" (404) and "could not look" (503) differ."""
    try:
        tarefa = await TAREFAS.obter_ou_guardada(tarefa_id)
    except Exception as exc:  # noqa: BLE001 - the history being down is an answer, not a crash
        logger.warning("Execução %s: histórico guardado indisponível: %s", tarefa_id, exc)
        return None, 503, _HISTORICO_FORA
    if tarefa is None:
        return None, 404, _NAO_ENCONTRADA.format(n=MAX_NA_TELA)
    return tarefa, 200, ""


@router.get("", response_class=HTMLResponse)
async def listar(request: Request):
    tarefas, aviso = await TAREFAS.recentes()
    return templates.TemplateResponse(
        request,
        "tarefas.html",
        {
            "tarefas": tarefas,
            "aviso": aviso,
            "guardado": TAREFAS.historico is not None,
            "maximo": MAX_NA_TELA,
        },
    )


@router.get("/{tarefa_id}", response_class=HTMLResponse)
async def acompanhar(request: Request, tarefa_id: str):
    tarefa, status, mensagem = await _procura(tarefa_id)
    if tarefa is None:
        # The error page, not FastAPI's raw JSON: this route is opened in the browser.
        return templates.TemplateResponse(
            request, "erro.html",
            {
                "titulo": "Execução não encontrada" if status == 404 else "Histórico indisponível",
                "mensagem": mensagem,
                "voltar": "/tarefas",
                "ativo": "tarefas",
            },
            status_code=status,
        )
    return templates.TemplateResponse(request, "tarefa.html", {"tarefa": tarefa})


@router.get("/{tarefa_id}/estado")
async def estado(tarefa_id: str):
    """Estado da tarefa em JSON — é o que a página consulta enquanto ela roda."""
    tarefa, status, mensagem = await _procura(tarefa_id)
    if tarefa is None:
        return JSONResponse({"detail": mensagem}, status_code=status)
    return JSONResponse(tarefa.para_json())


@router.post("/{tarefa_id}/cancelar")
async def cancelar(tarefa_id: str):
    """Interrompe a tarefa entre passos.

    ⚠️ Não desfaz o que já foi gravado no SAP — documentos criados até aqui continuam lá.
    A tela avisa isso antes de o usuário clicar.
    """
    cancelou = await TAREFAS.cancelar(tarefa_id)
    return JSONResponse({"cancelada": cancelou})
