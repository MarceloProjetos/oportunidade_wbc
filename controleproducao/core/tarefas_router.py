"""Rotas de acompanhamento das tarefas em segundo plano (22/09/2026).

Fica em `core/` e não num módulo porque é infraestrutura compartilhada: os módulos 2 e 3
criam tarefas, esta é a tela única onde se acompanha qualquer uma delas. Uma página de
acompanhamento por módulo seria três cópias da mesma coisa divergindo com o tempo.

O acompanhamento é por *polling* de um JSON, não por WebSocket. O estado da tarefa vive em
memória no mesmo processo (ver `tarefas.py`); um GET a cada 2s custa nada numa aplicação
interna e não acrescenta nem conexão persistente nem reconexão para manter.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from controleproducao.core.tarefas import TAREFAS
from controleproducao.core.templates import templates

router = APIRouter(prefix="/tarefas", tags=["tarefas"])


@router.get("", response_class=HTMLResponse)
async def listar(request: Request):
    return templates.TemplateResponse(
        request, "tarefas.html", {"tarefas": TAREFAS.listar()}
    )


@router.get("/{tarefa_id}", response_class=HTMLResponse)
async def acompanhar(request: Request, tarefa_id: str):
    tarefa = TAREFAS.obter(tarefa_id)
    if tarefa is None:
        # 404 e não uma página vazia: uma tarefa some do histórico quando passa de
        # MAX_TAREFAS, e dizer isso é melhor do que mostrar uma tela em branco.
        raise HTTPException(
            status_code=404,
            detail="Tarefa não encontrada. Ela pode ter saído do histórico "
                   "(as 200 mais recentes) ou o servidor foi reiniciado.",
        )
    return templates.TemplateResponse(request, "tarefa.html", {"tarefa": tarefa})


@router.get("/{tarefa_id}/estado")
async def estado(tarefa_id: str):
    """Estado da tarefa em JSON — é o que a página consulta enquanto ela roda."""
    tarefa = TAREFAS.obter(tarefa_id)
    if tarefa is None:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada.")
    return JSONResponse(tarefa.para_json())


@router.post("/{tarefa_id}/cancelar")
async def cancelar(tarefa_id: str):
    """Interrompe a tarefa entre passos.

    ⚠️ Não desfaz o que já foi gravado no SAP — documentos criados até aqui continuam lá.
    A tela avisa isso antes de o usuário clicar.
    """
    cancelou = await TAREFAS.cancelar(tarefa_id)
    return JSONResponse({"cancelada": cancelou})
