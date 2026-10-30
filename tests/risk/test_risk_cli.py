import shutil
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from ens_evidence.categorization import approve_categorization
from ens_evidence.cli import app
from ens_evidence.models.enums import Role
from ens_evidence.scope import approve_scope
from ens_evidence.workspace import LocalStore

from .helpers import (
    EXAMPLE_SCOPE,
    METHODOLOGY,
    METHODOLOGY_MD,
    NOW,
    REGISTER,
    action,
    adopted_methodology,
    dump,
    risk,
    write_category,
    write_risks,
)

runner = CliRunner()
RISK_PUBLISHED = {
    "risk/methodology.md",
    "risk/methodology.yaml",
    "risk/register.yaml",
    "risk/treatment-plan.yaml",
}


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
def workspace(settings, tmp_path) -> LocalStore:
    shutil.copytree(EXAMPLE_SCOPE, tmp_path / "ws" / "scope")
    store = LocalStore(tmp_path / "ws")
    approve_scope(store, "Ana Ruiz", Role.SYSTEM_OWNER, NOW)
    write_category(store, "MEDIO")
    approve_categorization(store, "Marta Gil", NOW)
    return store


@pytest.fixture
def valid_workspace(settings, workspace) -> LocalStore:
    ens(settings, "risk", "init")
    workspace.write_text(METHODOLOGY_MD, "# Metodología\n\nLa que usa la organización.\n")
    dump(workspace, METHODOLOGY, adopted_methodology())
    write_risks(workspace, [risk()], [action()])
    return workspace


def test_init_crea_las_plantillas_y_no_sobrescribe(settings, workspace):
    first = ens(settings, "risk", "init")
    workspace.write_text(REGISTER, "risks: []  # mío\n")
    second = ens(settings, "risk", "init")

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert "Creado risk/register.yaml" in first.output
    assert "Se conserva risk/register.yaml" in second.output
    assert workspace.read_text(REGISTER) == "risks: []  # mío\n"


def test_validate_de_la_plantilla_recien_creada_sale_con_1(settings, workspace):
    ens(settings, "risk", "init")

    result = ens(settings, "risk", "validate")

    assert result.exit_code == 1
    assert "risk/methodology.yaml" in result.output


def test_validate_valido_sale_con_0_y_avisa_de_que_no_evalua_los_valores(settings, valid_workspace):
    result = ens(settings, "risk", "validate")

    assert result.exit_code == 0, result.output
    assert "1 riesgos, 1 acciones" in result.output
    assert "no evalúa" in result.output


def test_approve_aprueba_y_status_lo_muestra_vigente(settings, valid_workspace):
    approved = ens(settings, "risk", "approve", "--by", "Marta Gil")
    status = ens(settings, "risk", "status")

    assert approved.exit_code == 0, approved.output
    assert status.exit_code == 0, status.output
    assert "APPROVED" in status.output


def test_approve_ya_no_ofrece_elegir_el_rol(settings, valid_workspace):
    result = ens(settings, "risk", "approve", "--by", "Ana Ruiz", "--role", "system_owner")

    assert result.exit_code == 2
    assert not valid_workspace.exists("risk/approval.yaml")


def test_validate_y_approve_exigen_el_alcance_aprobado(settings, valid_workspace):
    valid_workspace.write_text("scope/assets.yaml", "assets: []\n")

    validated = ens(settings, "risk", "validate")
    approved = ens(settings, "risk", "approve", "--by", "Marta Gil")

    assert (validated.exit_code, approved.exit_code) == (1, 1)
    assert "alcance no está aprobado" in validated.output
    assert "alcance no está aprobado" in approved.output
    assert not valid_workspace.exists("risk/approval.yaml")


def test_status_sale_con_1_sin_aprobacion(settings, valid_workspace):
    result = ens(settings, "risk", "status")

    assert result.exit_code == 1
    assert "NOT_APPROVED" in result.output


def test_pull_copia_los_ficheros_de_risk_pero_no_su_aprobacion(settings, valid_workspace, tmp_path):
    ens(settings, "risk", "approve", "--by", "Marta Gil")

    result = ens(settings, "workspace", "pull", str(tmp_path / "edit"))

    pulled = set(LocalStore(tmp_path / "edit").list())
    assert result.exit_code == 0, result.output
    assert RISK_PUBLISHED <= pulled
    assert "risk/approval.yaml" not in pulled


def test_push_publica_los_ficheros_de_risk_pero_no_su_aprobacion(settings, workspace, tmp_path):
    draft = tmp_path / "draft"
    shutil.copytree(EXAMPLE_SCOPE, draft / "scope")
    for key in RISK_PUBLISHED:
        (draft / key).parent.mkdir(parents=True, exist_ok=True)
        (draft / key).write_text("risks: []\n" if key.endswith("register.yaml") else "x: 1\n")
    (draft / "risk" / "approval.yaml").write_text("approved_by: Mallory\n")

    result = ens(settings, "workspace", "push", str(draft))

    published = set(workspace.list())
    assert result.exit_code == 0, result.output
    assert RISK_PUBLISHED <= published
    assert "risk/approval.yaml" not in published
