"""Painel tests: the test client behaves like the browser on the painel's own page."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _origem_da_propria_tela(monkeypatch: pytest.MonkeyPatch) -> None:
    """Browsers send ``Origin`` on every POST, and the painel refuses a write without a
    matching one (CSRF, 01/10/2026). A test that wants a foreign or missing origin passes its
    own ``headers=`` on the request."""
    original = TestClient.__init__

    def __init__(self, *args, headers=None, **kwargs):  # noqa: ANN001, ANN202
        original(self, *args, headers={"Origin": "http://testserver", **(headers or {})}, **kwargs)

    monkeypatch.setattr(TestClient, "__init__", __init__)
