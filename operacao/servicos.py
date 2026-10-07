"""State of the six NSSM services of the .11.

``/status`` already says whether the worker cycles and the Controle de Produção answers,
but not whether the Windows services themselves are running, nor since when — the first
question after "something is off". Read through psutil (already a dependency of the
``system`` block of ``/status``); never starts or stops anything.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from operacao import versao

#: The services ``deploy_update.bat`` manages, in its order, with what each one is.
SERVICOS: tuple[tuple[str, str], ...] = (
    ("OrcaView-OS-API", "API 8077 e tela de Sincronização"),
    ("OrcaView-MCP", "MCP 8078"),
    ("OrcaView-Scheduler", "agendador (oportunidades, Vendas BI, espelho de orçamentos)"),
    ("OrcaView-WBC-Painel", "painel WBC 8079"),
    ("OrcaView-ControleProducao", "Controle de Produção 8080"),
    ("OrcaView-WBC-Worker", "worker WBC → SAP"),
)


#: A deploy stops and starts the six services within ~1 min of its start; slack for a slow pip.
JANELA_DEPLOY = timedelta(minutes=10)
#: NSSM starts the automatic services a few minutes after the boot (the .11 reboots ~06:12).
JANELA_BOOT = timedelta(minutes=15)
#: An approved restart starts the service before ``concluido_em`` is written.
FOLGA_REINICIO = timedelta(minutes=2)


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
    # The account matters for F4: restarting the others needs a service-control right
    # (LocalSystem has it; a plain user does not).
    item.update(instalado=True, estado=info.get("status"),
                inicio_automatico=info.get("start_type") == "automatic", conta=info.get("username"))
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


def _iso(texto: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(texto) if texto else None
    except ValueError:
        return None


def _boot(psutil) -> datetime | None:
    try:
        return datetime.fromtimestamp(psutil.boot_time()).replace(microsecond=0)
    except Exception:  # the test double and odd platforms have no boot_time
        return None


def _reinicios_aprovados() -> list[dict[str, Any]]:
    """Executed restarts from the approvals DB; never creates the DB (read only if it exists)."""
    try:
        from seguranca import aprovacoes

        if not aprovacoes._arquivo(None).exists():
            return []
        feitos = aprovacoes.listar("executado", limite=50) + aprovacoes.listar("falhou", limite=50)
    except Exception:
        return []
    return [a for a in feitos if a.get("acao") == "reiniciar_servico"]


def causa_do_inicio(nome: str, desde: datetime | None, *, deploys: list[dict], boot: datetime | None,
                    reinicios: list[dict]) -> dict[str, Any] | None:
    """Why a service started when it did: ``reinicio_aprovado``, ``deploy``, ``boot`` or ``desconhecido``.

    ``desconhecido`` covers NSSM bringing it back after a crash and a manual restart — neither
    leaves a record here. Without this the Mira saw six identical start times and guessed
    "a reboot of the machine or of the services" while it was the 10:42 deploy.
    """
    if desde is None:
        return None
    for r in reinicios:
        decidido, concluido = _iso(r.get("decidido_em")), _iso(r.get("concluido_em"))
        servico = (r.get("parametros") or {}).get("servico")
        if servico == nome and decidido and decidido <= desde <= (concluido or decidido) + FOLGA_REINICIO:
            return {"tipo": "reinicio_aprovado", "quando": r.get("decidido_em"), "codigo": r.get("codigo"),
                    "aprovado_por": r.get("decidido_por")}
    for d in deploys:
        inicio = _iso(d.get("inicio_iso"))
        # An aborted run that never stopped anything says so ("nada parado").
        if inicio and inicio <= desde <= inicio + JANELA_DEPLOY and "nada parado" not in (d.get("resultado") or ""):
            return {"tipo": "deploy", "quando": d.get("inicio_iso"), "versao": d.get("para"),
                    "resultado": d.get("resultado")}
    if boot and boot <= desde <= boot + JANELA_BOOT:
        return {"tipo": "boot", "quando": boot.isoformat()}
    return {"tipo": "desconhecido"}


def estado_servicos(agora: datetime | None = None, *, deploys: list[dict] | None = None,
                    reinicios: list[dict] | None = None) -> dict[str, Any]:
    """The six services: installed, state, automatic start, since when (process start) and why."""
    psutil = _psutil()
    if psutil is None:
        return {"disponivel": False, "motivo": "leitura de serviços só existe no Windows com psutil",
                "servicos": []}
    agora = agora or datetime.now()
    servicos = [_um(psutil, nome, papel, agora) for nome, papel in SERVICOS]
    boot = _boot(psutil)
    deploys = versao.deploys_recentes() if deploys is None else deploys
    reinicios = _reinicios_aprovados() if reinicios is None else reinicios
    for item in servicos:
        item["causa_do_inicio"] = causa_do_inicio(item["nome"], _iso(item["desde"]), deploys=deploys,
                                                  boot=boot, reinicios=reinicios)
    fora = [s["nome"] for s in servicos if s["estado"] != "running"]
    return {
        "disponivel": True,
        "rodando": len(servicos) - len(fora),
        "total": len(servicos),
        "fora_do_ar": fora,
        "boot": boot.isoformat() if boot else None,
        "servicos": servicos,
    }
