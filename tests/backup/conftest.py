import os
import shutil
import stat
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from ens_evidence.cli import app
from ens_evidence.workspace import LocalStore

PASSWORD = "contrasena-de-la-copia-9f3k"
BACKUP_KEY = "AKIA-COPIA-0001"
BACKUP_SECRET = "secreto-de-la-copia-0002"
WORKSPACE_KEY = "AKIA-WORKSPACE-0003"
WORKSPACE_SECRET = "secreto-del-workspace-0004"

runner = CliRunner()


def ens(settings: Path, *args: str):
    return runner.invoke(app, ["--settings", str(settings), *args])


def restic_available() -> bool:
    return shutil.which("restic") is not None


@pytest.fixture
def real_restic():
    """restic real; ausente = skip en local y fallo en CI."""
    if restic_available():
        return
    message = "restic no está instalado (brew install restic / apt-get install restic)"
    if os.environ.get("CI") == "true":
        pytest.fail(message)
    pytest.skip(message)


@pytest.fixture
def repository(tmp_path) -> Path:
    return tmp_path / "repo"


@pytest.fixture
def workspace(tmp_path) -> LocalStore:
    return LocalStore(tmp_path / "ws")


@pytest.fixture
def settings(tmp_path, repository, workspace, monkeypatch) -> Path:
    monkeypatch.setenv("ENS_BACKUP_RESTIC_PASSWORD", PASSWORD)
    path = tmp_path / "settings.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "workspace": {"backend": "local", "local": {"path": str(tmp_path / "ws")}},
                "backup": {"restic_repository": str(repository)},
            }
        )
    )
    return path


@pytest.fixture
def documents(tmp_path) -> list[Path]:
    paths = [tmp_path / "politica.pdf", tmp_path / "captura.bin"]
    paths[0].write_bytes(b"politica de acceso v1")
    paths[1].write_bytes(bytes(range(256)) * 4)
    return paths


def import_evidences(settings: Path, documents: list[Path]) -> None:
    for document in documents:
        result = ens(
            settings,
            "evidence",
            "import",
            str(document),
            "--source",
            "manual",
            "--asset",
            "a1",
            "--by",
            "ana",
        )
        assert result.exit_code == 0, result.output


@pytest.fixture
def fake_restic(tmp_path, monkeypatch):
    """Un `restic` falso que vuelca su entorno y argumentos; solo frontera de proceso."""
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    dump = tmp_path / "invocation.txt"
    script = bin_dir / "restic"
    script.write_text(f'#!/bin/sh\n{{ env; echo "ARGS:$*"; }} > "{dump}"\nexit 0\n')
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return dump
