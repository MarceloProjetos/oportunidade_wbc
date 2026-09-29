"""Isolation of the Controle de Produção suite from the machine's `.env` and from the network.

Why each block exists:

- The root `tests/conftest.py` imports `config`, which runs `load_dotenv()`: the SIS `.env`
  lands in `os.environ` for the whole session. On top of that, `controleproducao.config`
  reads the SAME file by absolute path (`_ENV_FILE`), so a second door has to be closed:
  `env_file` is set to `None` on the model config for the duration of each test.
- Names are DELETED, but `OS_API_KEY` / `SIS_PAINEL_URL` are set EMPTY on purpose: an env
  var wins over the `.env` read by pydantic-settings (same rule as `tests/wbc/conftest.py`).
- `SL_BASE_URL` / `HANA_HOST` / `WBC_SQL_HOST` point at 127.0.0.1 (port 9 for the Service
  Layer) as a safety net: a test that forgets a stub fails at once instead of hanging httpx.
- `get_settings` is `lru_cache`d: cleared before AND after, so no test sees another's env.
- `SUPABASE_*` is deleted and `TAREFAS.historico` reset: the app attaches the Supabase history
  at startup (`with TestClient(app)` runs it), and a test playing the .11 must not leave a
  real history object on the process-wide registry for the next test. History tests inject
  a fake or set the env themselves.
- `cli.py` calls `logging.basicConfig(force=True)` and pins the `httpx` logger to WARNING on
  every CLI command (the tests drive it through `CliRunner`). Loggers are process globals:
  left as is, `tests/wbc/test_logs.py` (which runs later and expects httpx INFO in the
  file) failed only in the full suite. Root logger and `httpx` level are restored per test.
"""

from __future__ import annotations

import logging
import os

import pytest

from controleproducao.config import Settings, get_settings
from controleproducao.core.tarefas import TAREFAS
from wbcpython import safety

# Same list as tests/wbc/conftest.py plus `CP_` (this package's own knobs). The SIS prefixes
# (SAP_, SQL_, SQLSERVER_, OP_SL_) are in because the credentials fall back to them.
_PREFIXOS = (
    "SL_", "WBC_SQL_", "HANA_", "TRACKING_", "WORKER_", "PAINEL_", "MESES_DE_JANELA",
    "SAP_", "SQL_", "SQLSERVER_", "OP_SL_", "CP_", "SUPABASE_",
)
_NOMES = (
    "WBC_ENVIRONMENT", "WBC_PRODUCTION_COMPANY_DB", "LOG_LEVEL", "LOG_FILE", "WBC_PAINEL_URL",
)


@pytest.fixture(autouse=True)
def _ambiente_neutro(monkeypatch: pytest.MonkeyPatch, tmp_path):
    for nome in list(os.environ):
        if nome.startswith(_PREFIXOS) or nome in _NOMES:
            monkeypatch.delenv(nome, raising=False)
    monkeypatch.setenv("OS_API_KEY", "")
    monkeypatch.setenv("SIS_PAINEL_URL", "")
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("SL_BASE_URL", "http://127.0.0.1:9/b1s/v1")
    monkeypatch.setenv("HANA_HOST", "127.0.0.1")
    monkeypatch.setenv("WBC_SQL_HOST", "127.0.0.1")
    # CLI commands driven by CliRunner write their log file: never into the repo's logs/.
    monkeypatch.setenv("CP_CLI_LOG_FILE", str(tmp_path / "controleproducao_cli.log"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _sem_historico_guardado():
    TAREFAS.historico = None
    yield
    TAREFAS.historico = None


@pytest.fixture(autouse=True)
def _logging_restaurado():
    raiz = logging.getLogger()
    httpx_logger = logging.getLogger("httpx")
    nivel_raiz, handlers_raiz, nivel_httpx = raiz.level, list(raiz.handlers), httpx_logger.level
    yield
    raiz.setLevel(nivel_raiz)
    for handler in list(raiz.handlers):
        if handler not in handlers_raiz:
            raiz.removeHandler(handler)
            handler.close()   # the CLI's file handler holds a file in tmp_path
    for handler in handlers_raiz:
        if handler not in raiz.handlers:
            raiz.addHandler(handler)
    httpx_logger.setLevel(nivel_httpx)


@pytest.fixture
def como_a_11(monkeypatch: pytest.MonkeyPatch):
    """Plays the .11: the root conftest pins `PRODUCTION_MACHINE_IP` to 192.0.2.1 (never local)."""
    monkeypatch.setattr(safety, "PRODUCTION_MACHINE_IP", "127.0.0.1")
