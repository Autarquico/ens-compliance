import shutil
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from ens_evidence.cli import app
from ens_evidence.scope import ScopeState, scope_status
from ens_evidence.workspace import LocalStore

runner = CliRunner()
EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "workspace"


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
def approved(settings, tmp_path) -> LocalStore:
    workspace = LocalStore(tmp_path / "ws")
    shutil.copytree(EXAMPLE / "scope", workspace._root / "scope")
    assert ens(settings, "scope", "approve", "--by", "Ana Ruiz").exit_code == 0
    return workspace


@pytest.fixture
def draft(tmp_path) -> Path:
    path = tmp_path / "draft"
    shutil.copytree(EXAMPLE / "scope", path / "scope")
    return path


@pytest.mark.parametrize(
    ("key", "text"),
    [("controls/measures.yaml", "measures: []\n"), ("org/answers.yaml", 'organizacion: "x"\n')],
)
def test_push_de_claves_ajenas_al_alcance_conserva_la_aprobacion(
    settings, approved, draft, key, text
):
    (draft / key).parent.mkdir(parents=True, exist_ok=True)
    (draft / key).write_text(text)

    result = ens(settings, "workspace", "push", str(draft))

    assert result.exit_code == 0, result.output
    assert approved.read_text(key) == text
    assert scope_status(approved).state is ScopeState.APPROVED
    assert "revoca" not in result.output


def test_push_que_cambia_scope_assets_revoca_la_aprobacion(settings, approved, draft):
    path = draft / "scope" / "assets.yaml"
    path.write_text(path.read_text().replace("macOS 15", "macOS 16", 1))

    result = ens(settings, "workspace", "push", str(draft))

    assert result.exit_code == 0, result.output
    assert yaml.safe_load(approved.read_text("scope/approval.yaml"))["revoked"] is True
    assert scope_status(approved).state is ScopeState.NOT_APPROVED
