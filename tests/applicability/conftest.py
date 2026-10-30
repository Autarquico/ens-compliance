import pytest
import yaml
from typer.testing import CliRunner

from ens_evidence.cli import app

from .helpers import LATER, prepared_workspace, uniform


@pytest.fixture
def workspace(tmp_path):
    """Alcance aprobado, categoría MEDIA aprobada y `controls init` ejecutado."""
    return prepared_workspace(tmp_path / "ws", uniform("MEDIO"))


@pytest.fixture
def ens_cli(tmp_path, workspace, monkeypatch):
    """CLI sobre `workspace`, con el reloj fijado un día después de las aprobaciones."""
    monkeypatch.setattr("ens_evidence.cli.clock", lambda: LATER)
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        yaml.safe_dump({"workspace": {"backend": "local", "local": {"path": str(tmp_path / "ws")}}})
    )
    runner = CliRunner()

    def run(*args: str):
        return runner.invoke(app, ["--settings", str(settings), *args])

    return run
