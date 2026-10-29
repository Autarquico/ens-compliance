"""`collect restic` usa los ajustes con los que se abrió el workspace, no `ENS_SETTINGS`."""

import json

import yaml
from typer.testing import CliRunner

from ens_evidence.cli import app
from ens_evidence.connectors import ConnectorRegistry
from ens_evidence.connectors.restic import ResticConnector
from ens_evidence.connectors.restic.process import CommandResult
from ens_evidence.workspace import LocalStore

from ..github.cli_support import EXAMPLE

runner = CliRunner()
CONFIG = {
    "asset_id": "bk-diario",
    "max_backup_age_hours": 26,
    "max_check_age_days": 35,
    "restore_test_validity_days": 90,
    "min_check_read_data_percent": 5,
}


def fake_restic(args: tuple[str, ...]) -> CommandResult:
    if "cat" in args:
        return CommandResult(0, json.dumps({"id": "a" * 64}))
    return CommandResult(0, "[]")


def write_settings(path, workspace, repository) -> str:
    path.write_text(
        yaml.safe_dump(
            {
                "workspace": {"backend": "local", "local": {"path": str(workspace)}},
                "backup": {"restic_repository": repository},
            }
        )
    )
    return str(path)


def test_collect_uses_repository_from_settings_option_not_env(tmp_path, monkeypatch):
    workspace = tmp_path / "ws"
    store = LocalStore(workspace)
    for source in (EXAMPLE / "scope").iterdir():
        store.write_text(f"scope/{source.name}", source.read_text())
    store.write_text("connectors/restic.yaml", yaml.safe_dump(CONFIG))
    chosen = write_settings(tmp_path / "chosen.yaml", workspace, "s3:s3.example.com/copias")
    other = write_settings(tmp_path / "other.yaml", workspace, "/repos/otro")
    monkeypatch.setenv("ENS_SETTINGS", other)

    class Injected(ResticConnector):
        def __init__(self, backup=None) -> None:
            super().__init__(backup=backup, executor=fake_restic)

    registry = ConnectorRegistry()
    registry.register(Injected)
    monkeypatch.setattr("ens_evidence.cli.connector_registry", lambda: registry)

    result = runner.invoke(app, ["--settings", chosen, "collect", "restic"])

    assert "Evidencia registrada" in result.output, result.output
    artifacts = "".join(store.read_bytes(key).decode() for key in store.list("evidence"))
    assert '"backend":"remote"' in artifacts
    assert '"backend":"local"' not in artifacts
