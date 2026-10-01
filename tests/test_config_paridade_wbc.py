"""The shared defaults of the .11 live in ONE place: ``wbcpython/padroes.py``.

The root ``config.py`` (the ``/status`` checks), ``wbcpython/config.py`` (worker and painel)
and ``controleproducao/config.py`` used to carry their own copies of the worker's schedule,
the tracking DB and the screens' ports, and this file compared the copies one by one. Since
01/10/2026 the three read the same constants; what is left to guard is that nobody types a
value back into one of them. Defaults are read from the field DECLARATION, not from an
instance: the machine's `.env` does not enter the comparison.
"""

from datetime import time

import config
from controleproducao.config import Settings as SettingsCP
from wbcpython import padroes
from wbcpython.config import Settings, TrackingSettings


def _default(classe, campo: str):
    return classe.model_fields[campo].default


def test_os_tres_configs_leem_os_mesmos_padroes():
    esperado = {
        "acompanhamento": padroes.TRACKING_DB_URL,
        "intervalo": padroes.WORKER_INTERVAL_S,
        "inicio": padroes.WORKER_HORARIO_INICIO,
        "fim": padroes.WORKER_HORARIO_FIM,
        "dias": padroes.WORKER_DIAS,
        "painel": padroes.PAINEL_PORTA,
        "api": padroes.OS_API_PORT,
        "cp": padroes.CP_PORTA,
        "cp_log": padroes.CP_LOG_FILE,
        "orcaview": padroes.ORCAVIEW_URL,
    }
    wbc = {
        "acompanhamento": _default(TrackingSettings, "db_url").get_secret_value(),
        "intervalo": _default(Settings, "worker_interval_seconds"),
        "inicio": _default(Settings, "worker_horario_inicio"),
        "fim": _default(Settings, "worker_horario_fim"),
        "dias": _default(Settings, "worker_dias_de_trabalho"),
        "painel": _default(Settings, "painel_porta"),
        "api": _default(Settings, "os_api_port"),
        "cp": _default(Settings, "cp_porta"),
        "orcaview": _default(Settings, "orcaview_url"),
    }
    raiz = {
        "acompanhamento": config.WBC_TRACKING_DB_URL_DEFAULT,
        "intervalo": config.WBC_WORKER_INTERVAL_S_DEFAULT,
        "inicio": time.fromisoformat(config.WBC_WORKER_HORARIO_INICIO_DEFAULT),
        "fim": time.fromisoformat(config.WBC_WORKER_HORARIO_FIM_DEFAULT),
        "dias": config.WBC_WORKER_DIAS_DEFAULT,
        "painel": config.WBC_PAINEL_PORTA_DEFAULT,
        "api": config.OS_API_PORT_DEFAULT,
        "cp": config.CP_PORTA_DEFAULT,
        "cp_log": config.CP_LOG_FILE_DEFAULT,
        "orcaview": config.ORCAVIEW_URL_DEFAULT,
    }
    cp = {
        "painel": _default(SettingsCP, "painel_porta"),
        "api": _default(SettingsCP, "os_api_port"),
        "cp": _default(SettingsCP, "cp_porta"),
        "cp_log": _default(SettingsCP, "cp_log_file"),
        "orcaview": _default(SettingsCP, "orcaview_url"),
    }
    for nome, lido in (("raiz", raiz), ("wbcpython", wbc), ("controleproducao", cp)):
        for chave, valor in lido.items():
            assert valor == esperado[chave], f"{nome}.{chave}: {valor!r} != padroes {esperado[chave]!r}"


def test_ninguem_volta_a_fixar_um_valor_no_config():
    """The guard that replaces the copy-by-copy comparison: the literal values only appear in
    padroes.py. A number typed back into a config.py would pass the test above today and
    drift tomorrow."""
    from pathlib import Path

    raiz = Path(__file__).resolve().parents[1]
    literais = ("8077", "8079", "8080", "wbc_tracking.db", "192.168.0.90:8000", "controleproducao.log")
    for arquivo in ("config.py", "wbcpython/config.py", "controleproducao/config.py"):
        texto = (raiz / arquivo).read_text(encoding="utf-8")
        codigo = "\n".join(
            linha.split("#", 1)[0] for linha in texto.splitlines()
            if not linha.lstrip().startswith(("#", '"', "'", "`"))
        )
        for literal in literais:
            assert literal not in codigo, f"{arquivo} fixa {literal!r} — use wbcpython/padroes.py"
