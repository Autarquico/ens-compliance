import shutil
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from ens_evidence.cli import app
from ens_evidence.connectors import ConnectorRegistry
from ens_evidence.connectors.google_workspace import GoogleWorkspaceConfig, GoogleWorkspaceConnector
from ens_evidence.scope.sync import SYNCED_KEYS
from ens_evidence.workspace import LocalStore

from .cli_support import CONFIG, EXAMPLE, KEY

runner = CliRunner()
CONFIG_TEXT = yaml.safe_dump(CONFIG)
PRIVATE_KEY = "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkq" + "A1b2C3d4E5" * 4


def ens(settings: Path, *args: str):
    return runner.invoke(app, ["--settings", str(settings), *args])


@pytest.fixture
def settings(tmp_path) -> Path:
    path = tmp_path / "settings.yaml"
    path.write_text(
        yaml.safe_dump({"workspace": {"backend": "local", "local": {"path": str(tmp_path / "ws")}}})
    )
    return path


@pytest.fixture
def workspace(tmp_path) -> LocalStore:
    shutil.copytree(EXAMPLE / "scope", tmp_path / "ws" / "scope")
    return LocalStore(tmp_path / "ws")


@pytest.fixture
def draft(tmp_path) -> Path:
    path = tmp_path / "draft"
    shutil.copytree(EXAMPLE / "scope", path / "scope")
    return path


def test_la_configuracion_del_conector_esta_entre_las_claves_sincronizadas():
    assert KEY in SYNCED_KEYS


def test_pull_trae_la_configuracion_del_conector(settings, workspace, tmp_path):
    workspace.write_text(KEY, CONFIG_TEXT)
    target = tmp_path / "local"

    result = ens(settings, "workspace", "pull", str(target))

    assert result.exit_code == 0, result.output
    assert (target / KEY).read_text() == CONFIG_TEXT


def test_push_publica_la_configuracion_del_conector(settings, workspace, draft):
    (draft / "connectors").mkdir()
    (draft / KEY).write_text(CONFIG_TEXT)

    result = ens(settings, "workspace", "push", str(draft))

    assert result.exit_code == 0, result.output
    assert workspace.read_text(KEY) == CONFIG_TEXT


def test_push_con_un_secreto_en_la_configuracion_aborta_sin_imprimir_el_valor(
    settings, workspace, draft
):
    (draft / "connectors").mkdir()
    (draft / KEY).write_text(CONFIG_TEXT + f"private_key: {PRIVATE_KEY}\n")

    result = ens(settings, "workspace", "push", str(draft))

    assert result.exit_code == 1
    assert PRIVATE_KEY not in result.output
    assert KEY in result.output
    assert not workspace.exists(KEY)


def test_el_entry_point_google_workspace_se_descubre_en_el_registro():
    registry = ConnectorRegistry()

    registry.load_entry_points()

    assert "google-workspace" in registry.names()
    connector = registry.get("google-workspace")
    assert connector is GoogleWorkspaceConnector
    assert connector.config_model is GoogleWorkspaceConfig
