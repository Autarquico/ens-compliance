"""Fixtures compartidas de la suite del ciclo: un workspace con alcance aprobado y GitHub
configurado, los ajustes que lo apuntan, el conector falso y el restic falso de la copia."""

import os

import pytest
import yaml
from backup.conftest import PASSWORD, scripted_restic  # noqa: F401 (fixture reexportada)

from ens_evidence.backup import PASSWORD_VAR
from ens_evidence.connectors.registry import ConnectorRegistry
from ens_evidence.scheduling.collect import Plugins
from ens_evidence.workspace.cycle_lock import CycleContext, CycleLock
from ens_evidence.workspace.settings import load_settings
from scheduling.collect_fakes import FakeConnector

SNAPSHOT = '{"message_type": "summary", "snapshot_id": "abcd1234"}'


@pytest.fixture
def settings_path(store, tmp_path):
    path = tmp_path / "settings.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "workspace": {"backend": "local", "local": {"path": str(store._root)}},
                "backup": {"restic_repository": str(tmp_path / "repo")},
            }
        )
    )
    return path


@pytest.fixture
def settings(settings_path):
    return load_settings(settings_path, allow_inside_product=True)


@pytest.fixture
def fake(monkeypatch, tmp_path):
    state = tmp_path / "fake-state"
    state.mkdir()
    monkeypatch.setenv("ENS_FAKE_STATE", str(state))
    monkeypatch.setenv("ENS_STATE_DIR", str(tmp_path / "state"))

    def mode(name: str, fail_first: int | None = None) -> None:
        monkeypatch.setenv("ENS_FAKE_MODE", name)
        if fail_first is not None:
            monkeypatch.setenv("ENS_FAKE_FAIL_FIRST", str(fail_first))

    mode.state = state
    return mode


@pytest.fixture
def plugins():
    registry = ConnectorRegistry()
    registry.register(FakeConnector)
    return Plugins(registry)


def cycle_env() -> dict[str, str]:
    return {PASSWORD_VAR: PASSWORD, "PATH": os.environ["PATH"]}


@pytest.fixture
def env(scripted_restic):  # noqa: F811
    return cycle_env()


@pytest.fixture
def base_env(scripted_restic):  # noqa: F811
    return cycle_env()


@pytest.fixture
def context(tmp_path):
    lock = CycleLock("identidad-de-prueba", tmp_path / "state").acquire()
    yield CycleContext(lock)
    lock.release()


@pytest.fixture
def restic(scripted_restic):  # noqa: F811
    scripted_restic.reply("backup", out=SNAPSHOT)
    return scripted_restic
