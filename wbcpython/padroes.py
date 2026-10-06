"""Defaults shared by the three configs of the .11 — one definition per value.

The root ``config.py`` (API, scheduler, the ``/status`` checks), ``wbcpython/config.py``
(worker and painel) and ``controleproducao/config.py`` each need the worker's schedule,
the tracking DB and the ports of the three screens. They used to keep their own copies,
with a parity test to catch drift — a `.env` without one of these lines would otherwise
make the monitor read another DB, another schedule or another port than the worker's,
with no error at all (01/10/2026 review). Now every config reads them from here.

Constants only, stdlib only, no I/O: the Flask API imports this without pulling pydantic.
``deploy_update.bat`` keeps its own fallbacks for the ports (a .bat cannot import this);
it reads the `.env` first.
"""
from __future__ import annotations

from datetime import time

# Integração WBC → SAP: the worker and its tracking DB
TRACKING_DB_URL = "sqlite:///./state/wbc_tracking.db"
WORKER_INTERVAL_S = 300
WORKER_HORARIO_INICIO = time(6, 30)
WORKER_HORARIO_FIM = time(19, 0)
WORKER_DIAS = "1,2,3,4,5"

# The three screens of the "Central Integração SAP" and the way back to the OrçaView
OS_API_PORT = 8077
PAINEL_PORTA = 8079
CP_PORTA = 8080
CP_LOG_FILE = "logs/controleproducao.log"
# https since PLANO_HTTPS_90 F4 (web_orcaview_V118/docs); :8000 http still answers for old links.
ORCAVIEW_URL = "https://192.168.0.90/"
