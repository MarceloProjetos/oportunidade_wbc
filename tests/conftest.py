"""Fixtures compartilhadas para pytest."""

import pytest

from config import reset_settings


@pytest.fixture(autouse=True)
def _reset_config_cache():
    """Garante que cada teste relê o ambiente do zero."""
    reset_settings()
    yield
    reset_settings()
