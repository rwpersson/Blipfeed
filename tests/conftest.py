import pytest

from blipfeed import store


@pytest.fixture
def conn():
    """A fresh in-memory store per test. Never touches the real DB."""
    c = store.connect(":memory:")
    yield c
    c.close()
