"""Restart ONE of the six NSSM services of the .11 — only after a person approved it (F4).

The same care as ``deploy_update.bat``, service by service:
- the Controle de Produção is never stopped with an execution running (``/health/ocupado``
  must answer a literal ``0``): stopping it mid-"processar" leaves OPs half-made in the SAP;
- the worker stops by FILE (``state/wbc_worker.stop``): it finishes the quote in hand and
  exits; a kill in the middle of a POST leaves a quotation without its link;
- the API restarting ITSELF: the restart is handed to a detached ``cmd`` that waits ~3 s
  (the answer goes out first) and calls ``nssm restart``. Detached through ``start``, so its
  parent is a ``cmd`` that already exited — NSSM, when it stops the API, kills the API's
  process tree, and a direct child would die before starting the service again.
"""
from __future__ import annotations

import shutil
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any

from config import get_settings
from operacao import servicos

RAIZ = Path(__file__).resolve().parents[1]
PARADA_DO_WORKER = RAIZ / "state" / "wbc_worker.stop"
ESPERA_WORKER_S = 90
ESPERA_SUBIR_S = 30
API = "OrcaView-OS-API"
WORKER = "OrcaView-WBC-Worker"
CP = "OrcaView-ControleProducao"
NOMES = tuple(nome for nome, _ in servicos.SERVICOS)

#: What a person should know before approving each one.
AVISOS = {
    API: "A API (8077) cai por ~10 s: telas da Sincronização, OrçaView e MCP ficam sem resposta nesse tempo.",
    "OrcaView-MCP": "O MCP (8078) cai por alguns segundos; quem estiver conversando com o agente recebe erro uma vez.",
    "OrcaView-Scheduler": "Se uma carga agendada estiver rodando, ela é interrompida e roda de novo na próxima janela.",
    "OrcaView-WBC-Painel": "O painel WBC (8079) fica fora por alguns segundos.",
    CP: "Recusado se houver execução em andamento no Controle de Produção (OPs pela metade no SAP).",
    WORKER: "O worker termina o orçamento em andamento antes de parar (até 90 s) — parada por arquivo, como no deploy.",
}


class ReinicioRecusado(RuntimeError):
    """The restart must not happen now (unknown service, CP busy, no nssm)."""


def _cp_ocupado() -> str | None:
    """``"0"`` free, ``"1"`` busy, ``None`` no answer (stopped or hung)."""
    s = get_settings()
    base = s.cp_url or f"http://127.0.0.1:{s.cp_porta}"
    try:
        with urllib.request.urlopen(f"{base.rstrip('/')}/health/ocupado", timeout=20) as r:
            return r.read().decode("utf-8", "replace").strip()
    except OSError:
        return None


def _estado(nome: str) -> str | None:
    try:
        import psutil
        return psutil.win_service_get(nome).status()
    except Exception:
        return None


def _nssm() -> str:
    caminho = shutil.which("nssm")
    if not caminho:
        raise ReinicioRecusado("nssm não encontrado no PATH do serviço da API.")
    return caminho


def validar(servico: str) -> str:
    nome = next((n for n in NOMES if n.lower() == (servico or "").strip().lower()), None)
    if nome is None:
        raise ReinicioRecusado(f"Serviço fora da lista: {servico!r}. Os 6: {', '.join(NOMES)}.")
    return nome


def previa(nome: str) -> dict[str, Any]:
    atual = next((s for s in servicos.estado_servicos().get("servicos", []) if s["nome"] == nome), {})
    texto = f"Reiniciar o serviço {nome} na .11. {AVISOS[nome]}"
    dados: dict[str, Any] = {"servico": nome, "estado_atual": atual.get("estado"),
                             "rodando_desde": atual.get("desde"), "texto": texto}
    if nome == CP:
        dados["cp_ocupado"] = _cp_ocupado()
    return dados


def _esperar(nome: str, alvo: str, segundos: int) -> bool:
    for _ in range(segundos):
        if _estado(nome) == alvo:
            return True
        time.sleep(1)
    return _estado(nome) == alvo


def _rodar(args: list[str], tempo: int) -> subprocess.CompletedProcess:
    # nssm writes UTF-16 to a pipe; the text is only for the record, so decode loosely.
    return subprocess.run(args, capture_output=True, timeout=tempo)


def _reiniciar_a_propria_api(nssm: str) -> None:
    comando = f'ping -n 4 127.0.0.1 >nul & "{nssm}" restart {API}'
    bandeiras = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                 | subprocess.CREATE_BREAKAWAY_FROM_JOB)
    argumentos = ["cmd", "/c", "start", '""', "/b", "cmd", "/c", comando]
    try:
        subprocess.Popen(argumentos, creationflags=bandeiras, close_fds=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:   # a job that forbids breakaway: still detached through `start`
        subprocess.Popen(argumentos, creationflags=bandeiras & ~subprocess.CREATE_BREAKAWAY_FROM_JOB,
                         close_fds=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)


def reiniciar(nome: str) -> dict[str, Any]:
    """Runs the restart. Returns ``{ok, texto, ...}``; raises ``ReinicioRecusado`` before acting."""
    nome = validar(nome)
    nssm = _nssm()
    if nome == CP:
        ocupado = _cp_ocupado()
        if ocupado != "0" and _estado(nome) == "running":
            raise ReinicioRecusado("O Controle de Produção tem execução em andamento (ou não respondeu); "
                                   "reiniciar agora deixaria OPs pela metade. Nada foi feito.")
    if nome == API:
        _reiniciar_a_propria_api(nssm)
        return {"ok": True, "servico": nome, "disparado": True,
                "texto": "Reinício da API disparado; ela volta em ~10 s. Confira com estado_servicos."}
    if nome == WORKER:
        if _estado(nome) != "running":
            # Same rule as the deploy: a stopped worker may be stopped on purpose (it writes in
            # the SAP); restarting must not become the back door that turns it on.
            raise ReinicioRecusado("O worker WBC não está rodando — pode estar parado de propósito. "
                                   "Ligá-lo é decisão de uma pessoa na .11 (nssm start), não um reinício.")
        PARADA_DO_WORKER.parent.mkdir(parents=True, exist_ok=True)
        PARADA_DO_WORKER.write_text("parada pedida por aprovação do agente (F4)\n", encoding="utf-8")
        _rodar([nssm, "stop", nome], 150)
        if not _esperar(nome, "stopped", ESPERA_WORKER_S):
            return {"ok": False, "servico": nome,
                    "texto": f"O worker não parou em {ESPERA_WORKER_S} s; NÃO foi religado à força. "
                             "Confira o ciclo em andamento antes de repetir."}
        _rodar([nssm, "start", nome], 60)
    else:
        _rodar([nssm, "restart", nome], 150)
    subiu = _esperar(nome, "running", ESPERA_SUBIR_S)
    return {"ok": subiu, "servico": nome, "estado": _estado(nome),
            "texto": f"{nome} reiniciado e rodando." if subiu
            else f"{nome} não voltou a 'running' em {ESPERA_SUBIR_S} s — confira (nssm status {nome})."}
