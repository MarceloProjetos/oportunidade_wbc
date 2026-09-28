"""``pedidos-wbc comparar-ops`` refuses to compare a schema with itself (28/09/2026).

Since the package reads its own schema from ``SL_COMPANY_DB``, on the .11 both sides default
to production and the report would say "ok" about nothing. The refusal happens before any
connection — the root conftest traps the drivers, so reaching one would fail loudly.
"""

from __future__ import annotations

from typer.testing import CliRunner

from controleproducao.cli import app
from controleproducao.config import get_settings


def _invoca(monkeypatch, *args: str, **env: str):
    for nome, valor in env.items():
        monkeypatch.setenv(nome, valor)
    get_settings.cache_clear()
    return CliRunner().invoke(app, ["pedidos-wbc", "comparar-ops", "00120634", *args])


def test_mesmo_schema_dos_dois_lados_e_recusado_antes_da_rede(monkeypatch) -> None:
    resultado = _invoca(monkeypatch, SL_COMPANY_DB="SBOALTAMIRAPROD", HANA_SCHEMA_LEGADO="SBOALTAMIRAPROD")
    assert resultado.exit_code == 2
    assert "mesmo schema" in resultado.output


def test_a_comparacao_e_case_insensitive(monkeypatch) -> None:
    resultado = _invoca(monkeypatch, "--schema-novo", "sboaltamiraprod", HANA_SCHEMA_LEGADO="SBOALTAMIRAPROD")
    assert resultado.exit_code == 2


def test_a_ajuda_diz_de_onde_vem_o_padrao(monkeypatch) -> None:
    saida = CliRunner().invoke(app, ["pedidos-wbc", "comparar-ops", "--help"]).output
    assert "SL_COMPANY_DB" in saida and "HANA_SCHEMA do .env" not in saida
