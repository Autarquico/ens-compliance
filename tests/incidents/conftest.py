import pytest

from .world import World


@pytest.fixture
def world(tmp_path) -> World:
    return World(tmp_path)
