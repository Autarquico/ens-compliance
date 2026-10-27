import shutil
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from ens_evidence.cli import app
from ens_evidence.workspace import LocalStore

runner = CliRunner()
EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "workspace"
TOKEN = "ghp_" + "A1b2C3d4E5" * 4
PUBLISHED = {
    "scope/assets.yaml",
    "scope/services.yaml",
    "scope/dependencies.yaml",
    "scope/system.yaml",
    "scope/questionnaire.md",
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


def draft_settings(draft: Path) -> Path:
    path = draft.parent / "draft-settings.yaml"
    path.write_text(
        yaml.safe_dump({"workspace": {"backend": "local", "local": {"path": str(draft)}}})
    )
    return path


@pytest.fixture
def workspace(tmp_path) -> LocalStore:
    return LocalStore(tmp_path / "ws")


@pytest.fixture
def draft(tmp_path) -> Path:
    path = tmp_path / "draft"
    shutil.copytree(EXAMPLE / "scope", path / "scope")
    return path


@pytest.fixture
def example_workspace(settings, workspace) -> LocalStore:
    shutil.copytree(EXAMPLE / "scope", workspace._root / "scope")
    return workspace


def test_push_no_publica_ficheros_fuera_de_la_lista_blanca(settings, workspace, draft):
    (draft / ".git").mkdir()
    (draft / ".git" / "config").write_text("[core]\n")
    (draft / "scope" / "desconocido.txt").write_text("no debe viajar\n")
    (draft / "notas.md").write_text("tampoco\n")

    result = ens(settings, "workspace", "push", str(draft))

    assert result.exit_code == 0, result.output
    assert set(workspace.list()) <= PUBLISHED
    assert ".git/config" not in workspace.list()


def test_push_con_secreto_en_un_fichero_publicable_aborta_sin_imprimir_el_valor(
    settings, workspace, draft
):
    (draft / "scope" / "questionnaire.md").write_text(f"# Cuestionario\n\nclave: {TOKEN}\n")

    result = ens(settings, "workspace", "push", str(draft))

    assert result.exit_code == 1
    assert TOKEN not in result.output
    assert "scope/questionnaire.md" in result.output
    assert workspace.list() == []


def test_push_ignora_un_secreto_en_un_fichero_fuera_de_la_lista_blanca(settings, workspace, draft):
    (draft / "scope" / "desconocido.txt").write_text(f"{TOKEN}\n")

    result = ens(settings, "workspace", "push", str(draft))

    assert result.exit_code == 0, result.output
    assert TOKEN not in result.output
    assert "scope/desconocido.txt" not in workspace.list()


def test_push_nunca_copia_la_aprobacion(settings, workspace, draft):
    ens(draft_settings(draft), "scope", "approve", "--by", "Ana Ruiz")
    assert (draft / "scope" / "approval.yaml").exists()

    result = ens(settings, "workspace", "push", str(draft))

    assert result.exit_code == 0, result.output
    assert not workspace.exists("scope/approval.yaml")


def test_push_con_aprobacion_local_malformada_publica_los_ficheros_validos(
    settings, workspace, draft
):
    (draft / "scope" / "approval.yaml").write_text("approved_by: [\n")

    result = ens(settings, "workspace", "push", str(draft))

    assert result.exit_code == 0, result.output
    assert {"scope/assets.yaml", "scope/services.yaml"} <= set(workspace.list())
    assert not workspace.exists("scope/approval.yaml")


def test_push_no_revierte_una_aprobacion_mas_reciente_en_el_destino(
    settings, example_workspace, draft
):
    assert ens(settings, "scope", "approve", "--by", "Ana Ruiz").exit_code == 0
    approval = example_workspace.read_text("scope/approval.yaml")
    ens(draft_settings(draft), "scope", "approve", "--by", "Borrador Antiguo")

    result = ens(settings, "workspace", "push", str(draft))

    assert result.exit_code == 0, result.output
    assert example_workspace.read_text("scope/approval.yaml") == approval


def test_push_muestra_el_estado_del_alcance_en_el_destino(settings, example_workspace, draft):
    ens(settings, "scope", "approve", "--by", "Ana Ruiz")
    path = draft / "scope" / "services.yaml"
    path.write_text(path.read_text().replace("online", "web"))

    result = ens(settings, "workspace", "push", str(draft))

    assert result.exit_code == 0, result.output
    assert "STALE" in result.output


def test_pull_nunca_copia_la_aprobacion(settings, example_workspace, tmp_path):
    ens(settings, "scope", "approve", "--by", "Ana Ruiz")

    result = ens(settings, "workspace", "pull", str(tmp_path / "edit"))

    assert result.exit_code == 0, result.output
    assert not LocalStore(tmp_path / "edit").exists("scope/approval.yaml")


def test_pull_con_fichero_local_distinto_sale_con_1_y_lista_los_ficheros(
    settings, example_workspace, tmp_path
):
    local = LocalStore(tmp_path / "edit")
    local.write_text("scope/assets.yaml", "assets: []  # edición local\n")

    result = ens(settings, "workspace", "pull", str(tmp_path / "edit"))

    assert result.exit_code == 1
    assert "scope/assets.yaml" in result.output
    assert "scope/services.yaml" not in result.output
    assert local.read_text("scope/assets.yaml") == "assets: []  # edición local\n"


def test_pull_con_force_sobrescribe_los_ficheros_locales(settings, example_workspace, tmp_path):
    local = LocalStore(tmp_path / "edit")
    local.write_text("scope/assets.yaml", "assets: []  # edición local\n")

    result = ens(settings, "workspace", "pull", str(tmp_path / "edit"), "--force")

    assert result.exit_code == 0, result.output
    assert local.read_text("scope/assets.yaml") == example_workspace.read_text("scope/assets.yaml")


def test_pull_con_ficheros_identicos_no_cuenta_como_conflicto(
    settings, example_workspace, tmp_path
):
    target = str(tmp_path / "edit")
    assert ens(settings, "workspace", "pull", target).exit_code == 0

    again = ens(settings, "workspace", "pull", target)

    assert again.exit_code == 0, again.output


def test_scope_status_con_inventario_no_utf8_sale_con_1_sin_traza(settings, example_workspace):
    (example_workspace._root / "scope" / "assets.yaml").write_bytes(b"assets: \xff\xfe\n")

    result = ens(settings, "scope", "status")

    assert result.exit_code == 1
    assert "INVALID" in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)


def test_reconcile_con_assets_valido_y_system_invalido_funciona(
    settings, example_workspace, tmp_path
):
    example_workspace.write_text("scope/system.yaml", "system: [\n")

    result = ens(
        settings, "inventory", "reconcile", "--observed", str(EXAMPLE / "observed-hosts.json")
    )

    assert result.exit_code == 0, result.output
    assert "srv-extra.taller-norte.example" in result.output


def test_reconcile_con_assets_vacio_marca_todo_como_no_inventariado(
    settings, example_workspace, tmp_path
):
    example_workspace.write_text("scope/assets.yaml", "assets: []\n")
    observed = tmp_path / "observed.json"
    observed.write_text(
        '{"source": "edr", "observed_at": "2026-10-27T08:00:00+00:00",'
        ' "hosts": [{"hostname": "a.x"}, {"hostname": "b.x"}]}'
    )

    result = ens(settings, "inventory", "reconcile", "--observed", str(observed))

    assert result.exit_code == 0, result.output
    assert result.output.count("Observado pero no está en el inventario") == 2
    assert "En el inventario (desplegado) pero no observado" not in result.output


def test_inventario_con_campo_obligatorio_ausente_se_informa_en_espanol_con_fichero_e_id(
    settings, example_workspace
):
    text = example_workspace.read_text("scope/services.yaml")
    example_workspace.write_text(
        "scope/services.yaml", text.replace("    name: Facturación online\n", "", 1)
    )

    result = ens(settings, "inventory", "validate")

    assert result.exit_code == 1
    assert "scope/services.yaml › svc-facturacion › " in result.output
    assert "name: falta este campo" in result.output
    assert "Field required" not in result.output


def test_activo_sin_campos_propios_de_su_tipo_se_informa_en_espanol(settings, example_workspace):
    text = example_workspace.read_text("scope/assets.yaml")
    example_workspace.write_text(
        "scope/assets.yaml", text.replace("    function: Aplicaciones de clientes\n", "", 1)
    )

    result = ens(settings, "inventory", "validate")

    assert "requires" not in result.output
    assert "Value error" not in result.output


def test_aprobar_con_rol_distinto_de_system_owner_se_rechaza_en_espanol(
    settings, example_workspace
):
    result = ens(settings, "scope", "approve", "--by", "Ana Ruiz", "--role", "security_officer")

    assert result.exit_code == 1
    assert "solo el responsable del sistema (system_owner) puede aprobar" in result.output
    assert not example_workspace.exists("scope/approval.yaml")


def test_rechazo_por_rol_no_contiene_texto_en_ingles(settings, example_workspace):
    result = ens(settings, "scope", "approve", "--by", "Ana Ruiz", "--role", "security_officer")

    assert "Value error" not in result.output
