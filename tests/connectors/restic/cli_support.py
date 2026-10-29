"""Utilidades de los tests de `ens-evidence collect restic`: workspace de ejemplo y CLI."""

import hashlib
from pathlib import Path

import yaml
from typer.testing import CliRunner

from ens_evidence.cli import app
from ens_evidence.connectors import ConnectorRegistry
from ens_evidence.connectors.restic import ResticConnector
from ens_evidence.evidence import list_manifests
from ens_evidence.evidence.layout import sha256_hex
from ens_evidence.models.contracts import CollectionResult
from ens_evidence.workspace import LocalStore

from ..github.cli_support import EXAMPLE
from .fake_restic import NOW
from .record_data import CONFIG

__all__ = [
    "EXAMPLE",
    "ens",
    "install_executor",
    "run_collect",
    "snapshot_tree",
    "stored_result",
    "write_workspace",
]

runner = CliRunner()


def write_workspace(tmp_path: Path, repository: str, config: dict | None = None):
    """(settings, store): workspace local con el alcance de ejemplo y la configuración."""
    tmp_path.mkdir(exist_ok=True)
    store = LocalStore(tmp_path / "ws")
    for source in (EXAMPLE / "scope").iterdir():
        store.write_text(f"scope/{source.name}", source.read_text())
    store.write_text("connectors/restic.yaml", yaml.safe_dump(config or CONFIG))
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        yaml.safe_dump(
            {
                "workspace": {"backend": "local", "local": {"path": str(tmp_path / "ws")}},
                "backup": {"restic_repository": repository},
            }
        )
    )
    return settings, store


def ens(settings: Path, *args: str):
    return runner.invoke(app, ["--settings", str(settings), *args])


def run_collect(settings: Path, *args: str):
    return ens(settings, "collect", "restic", *args)


def install_executor(monkeypatch, executor) -> None:
    """Registra en la CLI un conector de Restic con el ejecutor falso."""

    class Injected(ResticConnector):
        def __init__(self, backup=None) -> None:
            super().__init__(backup=backup, executor=executor, clock=lambda: NOW)

    registry = ConnectorRegistry()
    registry.register(Injected)
    monkeypatch.setattr("ens_evidence.cli.connector_registry", lambda: registry)


def snapshot_tree(root: Path) -> dict[str, str]:
    """Ficheros bajo `root` (ruta relativa -> sha256); las carpetas vacías también cuentan."""
    tree: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = str(path.relative_to(root))
        tree[relative] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "dir"
    return tree


def stored_result(store) -> tuple[CollectionResult, bytes]:
    """La última recogida de restic guardada (puede haber otras evidencias en el workspace)."""
    manifests = [m for m in list_manifests(store).manifests if m.source == "restic"]
    manifest = max(manifests, key=lambda m: m.collected_at)
    data = store.read_bytes(manifest.artifact.key)
    assert sha256_hex(data) == manifest.artifact.sha256
    return CollectionResult.model_validate_json(data), data
