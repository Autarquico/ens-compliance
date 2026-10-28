"""Utilidades de los tests de `ens-evidence collect`: workspace de ejemplo y ejecución de la CLI."""

from pathlib import Path

import yaml
from typer.testing import CliRunner

from ens_evidence.cli import app
from ens_evidence.evidence import list_manifests
from ens_evidence.evidence.layout import sha256_hex
from ens_evidence.models.contracts import CollectionResult

from .fake_api import ACCOUNT

runner = CliRunner()
EXAMPLE = Path(__file__).resolve().parents[3] / "examples" / "workspace"
BUCKET = "evidencias"
CONFIG = {"asset_id": "cf-taller", "account_id": ACCOUNT}


def write_settings(tmp_path, workspace_settings) -> Path:
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump({"workspace": workspace_settings}))
    return path


def seed(store):
    for source in (EXAMPLE / "scope").iterdir():
        store.write_text(f"scope/{source.name}", source.read_text())
    store.write_text("connectors/cloudflare.yaml", yaml.safe_dump(CONFIG))
    return store


def run_collect(workspace, *args: str):
    settings, _ = workspace
    return runner.invoke(app, ["--settings", str(settings), "collect", "cloudflare", *args])


def stored_result(store) -> tuple[CollectionResult, bytes]:
    (manifest,) = list_manifests(store).manifests
    data = store.read_bytes(manifest.artifact.key)
    assert sha256_hex(data) == manifest.artifact.sha256
    return CollectionResult.model_validate_json(data), data
