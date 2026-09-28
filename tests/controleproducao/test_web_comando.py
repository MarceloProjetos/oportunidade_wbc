"""``python -m controleproducao web`` — what the NSSM service runs (28/09/2026)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from controleproducao.cli import app
from controleproducao.config import get_settings


def _roda(monkeypatch: pytest.MonkeyPatch, tmp_path, *args: str, **env: str) -> dict:
    for nome, valor in env.items():
        monkeypatch.setenv(nome, valor)
    monkeypatch.setenv("CP_LOG_FILE", str(tmp_path / "cp.log"))
    get_settings.cache_clear()
    chamada: dict = {}

    def uvicorn_falso(aplicacao, **kwargs):
        chamada["app"] = aplicacao
        chamada.update(kwargs)

    with patch("uvicorn.run", uvicorn_falso):
        resultado = CliRunner().invoke(app, ["web", *args])
    assert resultado.exit_code == 0, resultado.output
    return chamada


def test_usa_host_e_porta_do_env_e_nao_deixa_o_uvicorn_configurar_o_log(monkeypatch, tmp_path) -> None:
    chamada = _roda(monkeypatch, tmp_path, CP_HOST="0.0.0.0", CP_PORTA="8181")
    assert chamada["host"] == "0.0.0.0" and chamada["port"] == 8181
    assert chamada["log_config"] is None  # otherwise uvicorn.* stops propagating to the file
    # INFO so "Application startup complete" reaches the file (the proof the bind worked);
    # no access log (that was the only noise "warning" used to hide).
    assert chamada["log_level"] == "info" and chamada["access_log"] is False
    from controleproducao.main import app as web_app

    assert chamada["app"] is web_app


def test_opcoes_da_linha_vencem_o_env(monkeypatch, tmp_path) -> None:
    chamada = _roda(monkeypatch, tmp_path, "--host", "127.0.0.1", "--porta", "9000", CP_PORTA="8181")
    assert chamada["host"] == "127.0.0.1" and chamada["port"] == 9000


def test_escreve_no_log_proprio(monkeypatch, tmp_path) -> None:
    _roda(monkeypatch, tmp_path)
    assert "Controle de Produção em http://127.0.0.1:8080" in (tmp_path / "cp.log").read_text(encoding="utf-8")
