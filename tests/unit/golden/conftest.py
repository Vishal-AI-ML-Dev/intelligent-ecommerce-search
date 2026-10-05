import pytest
from evals.contracts.catalog import load_catalog_index


@pytest.fixture(scope="session")
def catalog():
    """The committed seed catalog, read offline (no database)."""
    return load_catalog_index()
