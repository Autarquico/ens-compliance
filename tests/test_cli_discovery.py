import shutil
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from ens_evidence.cli import app
from ens_evidence.workspace import LocalStore

runner = CliRunner()
REPO = Path(__file__).resolve().parent.parent
EXAMPLE = REPO / "examples" / "workspace"
SCOPE_KEYS = {
    "scope/assets.yaml",
    "scope/services.yaml",
    "scope/dependencies.yaml",
    "scope/system.yaml",
    "scope/questionnaire.md",
}
TOKEN = "ghp_" + "A1b2C3d4E5" * 4


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
    return LocalStore(tmp_path / "ws")


@pytest.fixture
def example_workspace(settings, tmp_path) -> LocalStore:
    shutil.copytree(EXAMPLE / "scope", tmp_path / "ws" / "scope")
    return LocalStore(tmp_path / "ws")


def test_init_crea_ajustes_y_esqueleto(tmp_path):
    settings = tmp_path / "cfg" / "settings.yaml"

    result = ens(settings, "init", "--path", str(tmp_path / "ws"))

    assert result.exit_code == 0, result.output
    assert yaml.safe_load(settings.read_text())["workspace"]["backend"] == "local"
    assert set(LocalStore(tmp_path / "ws").list()) == SCOPE_KEYS


def test_init_es_idempotente_y_no_sobrescribe_lo_editado(tmp_path):
    settings = tmp_path / "settings.yaml"
    ens(settings, "init", "--path", str(tmp_path / "ws"))
    store = LocalStore(tmp_path / "ws")
    store.write_text("scope/assets.yaml", "assets: []  # editado\n")
    before_settings = settings.read_text()

    result = ens(settings, "init", "--path", str(tmp_path / "otro"))

    assert result.exit_code == 0, result.output
    assert store.read_text("scope/assets.yaml") == "assets: []  # editado\n"
    assert settings.read_text() == before_settings
    assert not (tmp_path / "otro").exists()


def test_init_con_path_dentro_del_repo_del_producto_sale_con_1(tmp_path):
    settings = tmp_path / "settings.yaml"
    inside = REPO / "tmp-ens-workspace-no-crear"

    result = ens(settings, "init", "--path", str(inside))

    assert result.exit_code == 1
    assert "repo del producto" in result.output
    assert not inside.exists()
    assert not settings.exists()


def test_init_exige_un_destino(tmp_path):
    result = ens(tmp_path / "settings.yaml", "init")

    assert result.exit_code == 2
    assert not (tmp_path / "settings.yaml").exists()


def test_workspace_pull_dentro_del_repo_del_producto_sale_con_1(settings, example_workspace):
    inside = REPO / "tmp-ens-pull-no-crear"

    result = ens(settings, "workspace", "pull", str(inside))

    assert result.exit_code == 1
    assert not inside.exists()


def test_workspace_pull_copia_el_workspace_a_una_carpeta_local(
    settings, example_workspace, tmp_path
):
    result = ens(settings, "workspace", "pull", str(tmp_path / "edit"))

    assert result.exit_code == 0, result.output
    assert set(LocalStore(tmp_path / "edit").list()) == set(example_workspace.list())


def test_inventory_validate_sale_con_0_en_un_inventario_valido(settings, example_workspace):
    result = ens(settings, "inventory", "validate")

    assert result.exit_code == 0, result.output
    assert "bk-externo" in result.output


def test_inventory_validate_sale_con_1_en_el_esqueleto_recien_creado(tmp_path):
    settings = tmp_path / "settings.yaml"
    ens(settings, "init", "--path", str(tmp_path / "ws"))

    result = ens(settings, "inventory", "validate")

    assert result.exit_code == 1
    assert "El inventario no es válido" in result.output


def test_scope_approve_y_status_con_codigo_0_cuando_el_alcance_esta_aprobado(
    settings, example_workspace
):
    assert ens(settings, "scope", "status").exit_code == 1

    approved = ens(settings, "scope", "approve", "--by", "Ana Ruiz")
    status = ens(settings, "scope", "status")

    assert approved.exit_code == 0, approved.output
    assert status.exit_code == 0, status.output
    assert "APPROVED" in status.output


def test_scope_status_sale_con_1_si_el_alcance_cambia_tras_aprobarse(settings, example_workspace):
    ens(settings, "scope", "approve", "--by", "Ana Ruiz")
    services = example_workspace.read_text("scope/services.yaml")
    example_workspace.write_text("scope/services.yaml", services.replace("online", "web"))

    result = ens(settings, "scope", "status")

    assert result.exit_code == 1
    assert "STALE" in result.output


@pytest.mark.parametrize(
    "extra",
    [["--by", ""], ["--by", "Ana Ruiz", "--role", "security_officer"]],
)
def test_scope_approve_rechazado_sale_con_1_y_no_registra_nada(settings, example_workspace, extra):
    result = ens(settings, "scope", "approve", *extra)

    assert result.exit_code == 1
    assert not example_workspace.exists("scope/approval.yaml")


def test_scope_approve_con_inventario_vacio_sale_con_1(tmp_path):
    settings = tmp_path / "settings.yaml"
    ens(settings, "init", "--path", str(tmp_path / "ws"))

    result = ens(settings, "scope", "approve", "--by", "Ana Ruiz")

    assert result.exit_code == 1
    assert not LocalStore(tmp_path / "ws").exists("scope/approval.yaml")


def test_secreto_en_el_inventario_se_rechaza_y_no_aparece_en_ninguna_salida(
    settings, example_workspace
):
    text = example_workspace.read_text("scope/assets.yaml")
    example_workspace.write_text("scope/assets.yaml", text.replace("Linux", TOKEN, 1))

    outputs = [
        ens(settings, "inventory", "validate"),
        ens(settings, "scope", "approve", "--by", "Ana Ruiz"),
        ens(settings, "scope", "status"),
    ]

    for result in outputs:
        assert result.exit_code == 1
        assert TOKEN not in result.output
    assert "parece un secreto" in outputs[0].output
    assert not example_workspace.exists("scope/approval.yaml")


def test_push_con_inventario_invalido_no_escribe_nada(settings, workspace, tmp_path):
    draft = tmp_path / "draft"
    shutil.copytree(EXAMPLE / "scope", draft / "scope")
    (draft / "scope" / "assets.yaml").write_text("assets: [\n")

    result = ens(settings, "workspace", "push", str(draft))

    assert result.exit_code == 1
    assert workspace.list() == []


def test_push_valido_publica_los_ficheros_en_el_workspace(settings, workspace, tmp_path):
    draft = tmp_path / "draft"
    shutil.copytree(EXAMPLE / "scope", draft / "scope")

    result = ens(settings, "workspace", "push", str(draft))

    assert result.exit_code == 0, result.output
    assert set(workspace.list()) == {f"scope/{p.name}" for p in (EXAMPLE / "scope").iterdir()}


def test_examples_workspace_valida_y_es_aprobable(settings, example_workspace):
    validated = ens(settings, "inventory", "validate")
    approved = ens(settings, "scope", "approve", "--by", "Ana Ruiz", "--role", "system_owner")

    assert validated.exit_code == 0, validated.output
    assert approved.exit_code == 0, approved.output


def test_inventory_reconcile_lista_el_host_no_inventariado_sin_bloquear(
    settings, example_workspace
):
    result = ens(
        settings, "inventory", "reconcile", "--observed", str(EXAMPLE / "observed-hosts.json")
    )

    assert result.exit_code == 0, result.output
    assert "srv-extra.taller-norte.example" in result.output
    assert ens(settings, "scope", "approve", "--by", "Ana Ruiz").exit_code == 0


def test_inventory_reconcile_con_entrada_invalida_sale_con_1(settings, example_workspace, tmp_path):
    observed = tmp_path / "observed.json"
    observed.write_text('{"hosts": []}')

    result = ens(settings, "inventory", "reconcile", "--observed", str(observed))

    assert result.exit_code == 1
