import pytest
from checks.acceptance_helpers import make_world

from .worlds import basic_store


@pytest.fixture
def ready(tmp_path, monkeypatch):
    return make_world(tmp_path, monkeypatch, "ready")


@pytest.fixture
def basic(tmp_path):
    return basic_store(tmp_path / "ws")
