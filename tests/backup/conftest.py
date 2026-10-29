import os
import shutil
import stat
from contextlib import contextmanager
from pathlib import Path

import boto3
import pytest
import yaml
from moto import mock_aws
from typer.testing import CliRunner

from ens_evidence.cli import app
from ens_evidence.workspace import LocalStore, S3Store

PASSWORD = "contrasena-de-la-copia-9f3k"
BACKUP_KEY = "AKIA-COPIA-0001"
BACKUP_SECRET = "secreto-de-la-copia-0002"
WORKSPACE_KEY = "AKIA-WORKSPACE-0003"
WORKSPACE_SECRET = "secreto-del-workspace-0004"
BUCKET = "evidencias"

runner = CliRunner()


def ens(settings: Path, *args: str):
    return runner.invoke(app, ["--settings", str(settings), *args])


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


@pytest.fixture
def s3(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        boto3.client("s3").create_bucket(Bucket=BUCKET)
        yield S3Store(BUCKET, "org")


REPOSITORY_ID = "ab" * 32
CANARY = "CANARIO-salida-restic-7c1d"
_SCRIPTED_RESTIC = """#!/bin/sh
dir="{directory}"
shift 2
printf '%s\\t' "$@" >> "$dir/calls.log"
echo >> "$dir/calls.log"
for arg in "$@"; do
  case "$arg" in cat|snapshots|check|backup|restore|init) command=$arg; break;; esac
done
if [ -f "$dir/$command.out" ]; then cat "$dir/$command.out"; fi
if [ -f "$dir/$command.err" ]; then cat "$dir/$command.err" >&2; fi
code=0
if [ -f "$dir/$command.code" ]; then code=$(cat "$dir/$command.code"); fi
exit "$code"
"""


class ScriptedRestic:
    """Un `restic` falso con respuestas por subcomando; solo frontera de proceso."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def reply(self, command: str, *, out: str = "", err: str = "", code: int = 0) -> None:
        (self._directory / f"{command}.out").write_text(out)
        (self._directory / f"{command}.err").write_text(err)
        (self._directory / f"{command}.code").write_text(str(code))

    def calls(self) -> list[list[str]]:
        log = self._directory / "calls.log"
        if not log.exists():
            return []
        return [line.rstrip("\t").split("\t") for line in log.read_text().splitlines()]


@pytest.fixture
def scripted_restic(tmp_path, monkeypatch) -> ScriptedRestic:
    directory = tmp_path / "scripted"
    directory.mkdir()
    script = directory / "restic"
    script.write_text(_SCRIPTED_RESTIC.format(directory=directory))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{directory}{os.pathsep}{os.environ['PATH']}")
    scripted = ScriptedRestic(directory)
    scripted.reply("cat", out=f'{{"version": 2, "id": "{REPOSITORY_ID}"}}')
    return scripted


@contextmanager
def stdin_closed():
    saved = os.dup(0)
    with open(os.devnull) as devnull:
        os.dup2(devnull.fileno(), 0)
    try:
        yield
    finally:
        os.dup2(saved, 0)
        os.close(saved)


@pytest.fixture
def closed_stdin():
    """El proceso hijo hereda un stdin vacío: restic nunca espera el teclado."""
    with stdin_closed():
        yield


def build_backed_up(root: Path, *, assets: list[dict] | None = None) -> None:
    """Deja en `root` un workspace (`ws`) y un repositorio restic real (`repo`) con una copia.

    `init` y `run` con restic real cuestan segundos: se hacen una vez por módulo y cada test
    trabaja sobre `restore_backed_up`, una copia en su propio `tmp_path`.
    """
    documents = [root / "politica.pdf", root / "captura.bin"]
    documents[0].write_bytes(b"politica de acceso v1")
    documents[1].write_bytes(bytes(range(256)) * 4)
    settings = root / "settings.yaml"
    settings.write_text(
        yaml.safe_dump(
            {
                "workspace": {"backend": "local", "local": {"path": str(root / "ws")}},
                "backup": {"restic_repository": str(root / "repo")},
            }
        )
    )
    with pytest.MonkeyPatch.context() as patch, stdin_closed():
        patch.setenv("ENS_BACKUP_RESTIC_PASSWORD", PASSWORD)
        if assets:
            LocalStore(root / "ws").write_text(
                "scope/assets.yaml", yaml.safe_dump({"assets": assets})
            )
        import_evidences(settings, documents)
        assert ens(settings, "backup", "init").exit_code == 0
        assert ens(settings, "backup", "run").exit_code == 0


def restore_backed_up(template: Path, tmp_path: Path) -> None:
    """Copia el workspace y el repositorio de `build_backed_up` al `tmp_path` del test."""
    for name in ("ws", "repo"):
        shutil.copytree(template / name, tmp_path / name, dirs_exist_ok=True)
