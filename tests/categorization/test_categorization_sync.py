import shutil
from pathlib import Path

import yaml
from typer.testing import CliRunner

from ens_evidence.cli import app
from ens_evidence.workspace import LocalStore

from .helpers import EXAMPLE_SCOPE, all_dimensions, service, write_categorization

runner = CliRunner()


def ens(settings: Path, *args: str):
    return runner.invoke(app, ["--settings", str(settings), *args])


def make_settings(tmp_path: Path) -> Path:
    path = tmp_path / "settings.yaml"
    path.write_text(
        yaml.safe_dump({"workspace": {"backend": "local", "local": {"path": str(tmp_path / "ws")}}})
    )
    return path


def approved_workspace(tmp_path: Path) -> tuple[Path, LocalStore]:
    settings = make_settings(tmp_path)
    shutil.copytree(EXAMPLE_SCOPE, tmp_path / "ws" / "scope")
    workspace = LocalStore(tmp_path / "ws")
    ens(settings, "scope", "approve", "--by", "Ana Ruiz", "--role", "system_owner")
    write_categorization(workspace, [service("svc-facturacion", all_dimensions("MEDIO"))])
    ens(settings, "categorization", "approve", "--by", "Marta Gil")
    return settings, workspace


def test_pull_copia_la_categorizacion_pero_no_su_aprobacion(tmp_path):
    settings, workspace = approved_workspace(tmp_path)
    assert workspace.exists("scope/categorization-approval.yaml")

    result = ens(settings, "workspace", "pull", str(tmp_path / "edit"))

    pulled = set(LocalStore(tmp_path / "edit").list())
    assert result.exit_code == 0, result.output
    assert "scope/categorization.yaml" in pulled
    assert "scope/categorization-approval.yaml" not in pulled
    assert "scope/approval.yaml" not in pulled


def test_push_publica_la_categorizacion_pero_no_su_aprobacion(tmp_path):
    settings = make_settings(tmp_path)
    draft = tmp_path / "draft"
    shutil.copytree(EXAMPLE_SCOPE, draft / "scope")
    write_categorization(LocalStore(draft), [service("svc-facturacion", all_dimensions("MEDIO"))])
    (draft / "scope" / "categorization-approval.yaml").write_text("category: ALTA\n")

    result = ens(settings, "workspace", "push", str(draft))

    published = set(LocalStore(tmp_path / "ws").list())
    assert result.exit_code == 0, result.output
    assert "scope/categorization.yaml" in published
    assert "scope/categorization-approval.yaml" not in published


def test_push_deja_la_aprobacion_de_categoria_no_vigente(tmp_path):
    settings, workspace = approved_workspace(tmp_path)
    draft = tmp_path / "edit"
    ens(settings, "workspace", "pull", str(draft))
    write_categorization(LocalStore(draft), [service("svc-facturacion", all_dimensions("BAJO"))])

    pushed = ens(settings, "workspace", "push", str(draft))
    status = ens(settings, "categorization", "status")

    assert pushed.exit_code == 0, pushed.output
    assert status.exit_code == 1
    assert "CURRENT" not in status.output.split()
