"""Routes to follow the background tasks (22/09/2026).

It lives in `core/` and not in a module because it is shared infrastructure: modules 2 and 3
create tasks, this is the single screen where any of them is followed. One follow-up page per
module would be three copies of the same thing drifting apart.

Following is by *polling* a JSON, not by WebSocket. The task state lives in memory in the same
process (see `tarefas.py`); a GET every 2 s costs nothing in an internal application and adds
neither a persistent connection nor a reconnection to maintain.

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
NAO_ENCONTRADA = _NAO_ENCONTRADA.format(n=MAX_NA_TELA)


async def procura(tarefa_id: str) -> tuple[Tarefa | None, int, str]:
    """(task, HTTP status, message). "Not found" (404) and "could not look" (503) differ.

    Public since 29/09/2026: the JSON API of the Manutenção de OP answers its execution
    status with the same lookup and the same messages as this screen.
    """
    try:
        tarefa = await TAREFAS.obter_ou_guardada(tarefa_id)
    except Exception as exc:  # noqa: BLE001 - the history being down is an answer, not a crash
        logger.warning("Execução %s: histórico guardado indisponível: %s", tarefa_id, exc)
        return None, 503, _HISTORICO_FORA
    if tarefa is None:
        return None, 404, NAO_ENCONTRADA
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
    tarefa, status, mensagem = await procura(tarefa_id)
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
    """Task state as JSON — what the page polls while the task runs."""
    tarefa, status, mensagem = await procura(tarefa_id)
    if tarefa is None:
        return JSONResponse({"detail": mensagem}, status_code=status)
    return JSONResponse(tarefa.para_json())


@router.post("/{tarefa_id}/cancelar")
async def cancelar(request: Request, tarefa_id: str):
    """Stops the task between steps.

    ⚠️ Does not undo what was already written in the SAP — documents created so far stay there.
    The screen warns about this before the user clicks.
    """
    cancelou = await TAREFAS.cancelar(tarefa_id)
    tarefa = TAREFAS.obter(tarefa_id)
    # `entre_etapas`: the task stops before its next step instead of being cut (the closing
    # of OPs, D5 of 29/09/2026) — the page tells the operator the OP in progress will finish.
    entre_etapas = bool(tarefa is not None and tarefa.parada_combinada)
    if cancelou:
        # Audit line next to the one written when the execution started (29/09/2026).
        logger.warning(
            "Execução %s: interrupção pedida pela tela · ip %s%s",
            tarefa_id, request.client.host if request.client else "—",
            " · para depois da etapa em curso" if entre_etapas else "",
        )
    return JSONResponse({"cancelada": cancelou, "entre_etapas": entre_etapas})
