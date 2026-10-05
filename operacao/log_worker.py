"""What the WBC worker logged about ONE quote — the painel's log search, for the agent.

``estado_orcamento_wbc`` gives the tracking state (status, rule, last error); the reason
behind an error is often only in the log text ("[porta-paletes] 00125442: …"). Same file
and same reader as the painel's "Log" tab (``wbcpython.logs.ler``), plus the previous
rotated file, so a quote from yesterday is still found.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from wbcpython import logs

RAIZ = Path(__file__).resolve().parents[1]
LINHAS_PADRAO = 40
LINHAS_MAX = 200
#: A traceback line can be long; the agent needs the gist, not a megabyte.
TEXTO_MAX = 600


def caminho_do_log() -> Path | None:
    """``LOG_FILE`` of the worker (same `.env`), relative to the project root like the worker's cwd."""
    nome = os.getenv("LOG_FILE", "wbcpython.log").strip()
    if not nome:
        return None
    caminho = Path(nome)
    return caminho if caminho.is_absolute() else RAIZ / caminho


def log_do_orcamento(orcnum: str, limite: int = LINHAS_PADRAO) -> dict[str, Any]:
    """The newest lines that mention the quote (8-digit number), newest first."""
    numero = orcnum.zfill(8)
    limite = max(1, min(int(limite), LINHAS_MAX))
    atual = caminho_do_log()
    if atual is None:
        return {"orcamento": numero, "disponivel": False,
                "motivo": "log do worker em arquivo desligado (LOG_FILE vazio)", "linhas": []}
    encontradas: list[logs.LinhaDeLog] = []
    for arquivo in (atual, atual.with_name(atual.name + ".1")):  # current, then the rotated one
        if len(encontradas) >= limite:
            break
        encontradas += logs.ler(arquivo, limite=limite - len(encontradas), busca=numero)
    return {
        "orcamento": numero,
        "disponivel": atual.exists(),
        "arquivo": str(atual.relative_to(RAIZ)) if atual.is_relative_to(RAIZ) else str(atual),
        "graves": sum(1 for linha in encontradas if linha.grave),
        "linhas": [
            {"momento": linha.momento, "nivel": linha.nivel,
             "mensagem": linha.mensagem[:TEXTO_MAX]}
            for linha in encontradas
        ],
    }
