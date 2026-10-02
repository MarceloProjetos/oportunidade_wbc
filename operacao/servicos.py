"""State of the six NSSM services of the .11.

``/status`` already says whether the worker cycles and the Controle de Produção answers,
but not whether the Windows services themselves are running, nor since when — the first
question after "something is off". Read through psutil (already a dependency of the
``system`` block of ``/status``); never starts or stops anything.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

#: The services ``deploy_update.bat`` manages, in its order, with what each one is.
SERVICOS: tuple[tuple[str, str], ...] = (
    ("OrcaView-OS-API", "API 8077 e tela de Sincronização"),
    ("OrcaView-MCP", "MCP 8078"),
    ("OrcaView-Scheduler", "agendador (oportunidades, Vendas BI, espelho de orçamentos)"),
    ("OrcaView-WBC-Painel", "painel WBC 8079"),
    ("OrcaView-ControleProducao", "Controle de Produção 8080"),
    ("OrcaView-WBC-Worker", "worker WBC → SAP"),
)


def _psutil():
    try:
        import psutil
    except ImportError:
        return None
    return psutil if hasattr(psutil, "win_service_get") else None


def _um(psutil, nome: str, papel: str, agora: datetime) -> dict[str, Any]:
    item: dict[str, Any] = {
        "nome": nome, "papel": papel, "instalado": False, "estado": None,
        "inicio_automatico": None, "desde": None, "ha_minutos": None,
    }
    try:
        info = psutil.win_service_get(nome).as_dict()
    except psutil.NoSuchProcess:
        item["motivo"] = "serviço não instalado nesta máquina"
        return item
    except Exception as exc:  # access denied, SCM hiccup: say so instead of guessing
        item["motivo"] = f"não foi possível ler o serviço: {exc}"[:200]
        return item
    item.update(instalado=True, estado=info.get("status"),
                inicio_automatico=info.get("start_type") == "automatic")
    pid = info.get("pid")
    if pid:
        try:
            desde = datetime.fromtimestamp(psutil.Process(pid).create_time())
        except Exception:
            desde = None
        if desde is not None:
            item["desde"] = desde.isoformat(timespec="seconds")
            item["ha_minutos"] = max(0, int((agora - desde).total_seconds() // 60))
    return item


def estado_servicos(agora: datetime | None = None) -> dict[str, Any]:
    """The six services: installed, state, automatic start and since when (process start)."""
    psutil = _psutil()
    if psutil is None:
        return {"disponivel": False, "motivo": "leitura de serviços só existe no Windows com psutil",
                "servicos": []}
    agora = agora or datetime.now()
    servicos = [_um(psutil, nome, papel, agora) for nome, papel in SERVICOS]
    fora = [s["nome"] for s in servicos if s["estado"] != "running"]
    return {
        "disponivel": True,
        "rodando": len(servicos) - len(fora),
        "total": len(servicos),
        "fora_do_ar": fora,
        "servicos": servicos,
    }
