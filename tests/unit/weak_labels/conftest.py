import sys
from pathlib import Path

import pytest

# Reuse the Golden Dataset test builders (synthetic pool fixtures) without duplicating them.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "golden"))

from evals.contracts.catalog import load_catalog_index  # noqa: E402


@pytest.fixture(scope="session")
def catalog():
    """The committed seed catalog, read offline (no database)."""
    return load_catalog_index()
