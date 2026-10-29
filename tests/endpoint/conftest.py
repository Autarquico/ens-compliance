from pathlib import Path

import pytest
import yaml

from ens_evidence.workspace import LocalStore

from .support import ASSETS, CONFIG, SERVER_HOST, SERVER_UUID, collect


@pytest.fixture
def settings(tmp_path) -> Path:
    path = tmp_path / "settings.yaml"
    path.write_text(
        yaml.safe_dump({"workspace": {"backend": "local", "local": {"path": str(tmp_path / "ws")}}})
    )
    return path


@pytest.fixture
def store(tmp_path) -> LocalStore:
    workspace = LocalStore(tmp_path / "ws")
    workspace.write_text("scope/assets.yaml", ASSETS)
    workspace.write_text("connectors/endpoint.yaml", yaml.safe_dump(CONFIG))
    return workspace


@pytest.fixture(scope="session")
def laptop_package(tmp_path_factory) -> bytes:
    return collect(tmp_path_factory.mktemp("laptop")).data


@pytest.fixture(scope="session")
def server_package(tmp_path_factory) -> bytes:
    run = collect(
        tmp_path_factory.mktemp("server"),
        platform="linux",
        uuid=SERVER_UUID,
        hostname=SERVER_HOST,
    )
    return run.data
