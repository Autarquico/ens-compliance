import re
import shutil
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from ens_evidence.cli import app
from ens_evidence.workspace import LocalStore

from .helpers import EXAMPLE_SCOPE, all_dimensions, service, write_categorization

runner = CliRunner()
FORBIDDEN = ("READY", "COMPLIANT", "DECLARED")


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
    return LocalStore(tmp_path / "ws")


@pytest.fixture
def approved_scope(settings, workspace) -> LocalStore:
    result = ens(settings, "scope", "approve", "--by", "Ana Ruiz", "--role", "system_owner")
    assert result.exit_code == 0, result.output
    return workspace


def approve_category(settings, workspace, level: str):
    write_categorization(workspace, [service("svc-facturacion", all_dimensions(level))])
    return ens(settings, "categorization", "approve", "--by", "Marta Gil")


def test_categoria_media_aprobada_dice_preparado_para_certificacion(settings, approved_scope):
    approved = approve_category(settings, approved_scope, "MEDIO")

    status = ens(settings, "categorization", "status")

    assert approved.exit_code == 0, approved.output
    assert status.exit_code == 0, status.output
    assert "CURRENT" in status.output
    assert "Categoría calculada: MEDIA" in status.output
    assert "Compromiso: preparado para certificación" in status.output
    assert "Requiere auditoría de certificación externa" in status.output
    assert not any(word in status.output for word in FORBIDDEN)


def test_las_fechas_de_la_categorizacion_se_muestran_sin_hora(settings, approved_scope):
    approved = approve_category(settings, approved_scope, "MEDIO")
    status = ens(settings, "categorization", "status")

    date = r"\d{4}-\d{2}-\d{2}"
    assert re.search(rf"vigente hasta {date}\.", approved.output)
    assert re.search(rf"Aprobada por Marta Gil el {date}; válida hasta {date}\.", status.output)


def test_categoria_basica_aprobada_dice_autoevaluacion(settings, approved_scope):
    approve_category(settings, approved_scope, "BAJO")

    status = ens(settings, "categorization", "status")

    assert status.exit_code == 0, status.output
    assert "Categoría calculada: BASICA" in status.output
    assert "Compromiso: autoevaluación" in status.output
    assert "la herramienta no declara" in status.output
    assert not any(word in status.output for word in FORBIDDEN)


def test_status_sale_con_1_si_la_categorizacion_no_esta_vigente(settings, approved_scope):
    write_categorization(approved_scope, [service("svc-facturacion", all_dimensions("MEDIO"))])

    status = ens(settings, "categorization", "status")

    assert status.exit_code == 1
    assert "NOT_APPROVED" in status.output
    assert "Compromiso: sin compromiso hasta aprobar la categorización" in status.output
    assert "preparado para certificación" not in status.output


def test_una_categoria_caducada_o_cambiada_deja_de_comprometer(settings, approved_scope):
    approve_category(settings, approved_scope, "MEDIO")
    write_categorization(approved_scope, [service("svc-facturacion", all_dimensions("ALTO"))])

    status = ens(settings, "categorization", "status")

    assert "STALE" in status.output
    assert "Compromiso: sin compromiso hasta aprobar la categorización" in status.output
    assert "preparado para certificación" not in status.output


def test_approve_sale_con_1_sin_alcance_aprobado(settings, workspace):
    write_categorization(workspace, [service("svc-facturacion", all_dimensions("MEDIO"))])

    result = ens(settings, "categorization", "approve", "--by", "Marta Gil")

    assert result.exit_code == 1
    assert "alcance no está aprobado" in result.output


def test_init_crea_la_plantilla_con_los_servicios_del_alcance_aprobado(settings, approved_scope):
    result = ens(settings, "categorization", "init")

    assert result.exit_code == 0, result.output
    document = yaml.safe_load(approved_scope.read_text("scope/categorization.yaml"))
    assert [item["id"] for item in document["services"]] == ["svc-facturacion"]
    assert document["information"] == []
    assert "#     - id: datos-clientes" in approved_scope.read_text("scope/categorization.yaml")
    assert ens(settings, "categorization", "status").exit_code == 1


def test_init_no_sobrescribe_un_fichero_existente(settings, approved_scope):
    approved_scope.write_text("scope/categorization.yaml", "# mío\ninformation: []\nservices: []\n")

    result = ens(settings, "categorization", "init")

    assert result.exit_code == 0, result.output
    assert approved_scope.read_text("scope/categorization.yaml").startswith("# mío")
    assert "Se conserva" in result.output


def test_init_exige_alcance_aprobado(settings, workspace):
    result = ens(settings, "categorization", "init")

    assert result.exit_code == 1
    assert not workspace.exists("scope/categorization.yaml")


def test_la_plantilla_de_init_incluye_los_criterios_del_anexo_i(settings, approved_scope):
    ens(settings, "categorization", "init")

    text = approved_scope.read_text("scope/categorization.yaml")

    assert "anexo I" in text
    for level in ("BAJO", "MEDIO", "ALTO", "not_affected"):
        assert level in text
