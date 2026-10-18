"""Fixtures for the M12 query-decision diagnostic tests (synthetic test fixtures only)."""

from pathlib import Path

import pytest
from query_decision_support import install_network_guard, make_sealed_dir


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    install_network_guard(monkeypatch)


@pytest.fixture
def sealed_dir(tmp_path: Path) -> Path:
    return make_sealed_dir(tmp_path)
