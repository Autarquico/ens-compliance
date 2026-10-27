from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from ens_evidence.cli import app
from ens_evidence.evidence.layout import artifact_key, sha256_hex
from ens_evidence.workspace import LocalStore

runner = CliRunner()
DATA = b"politica de acceso v1"


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
def document(tmp_path) -> Path:
    path = tmp_path / "politica.pdf"
    path.write_bytes(DATA)
    return path


def import_args(document: Path, *extra: str) -> tuple[str, ...]:
    return (
        "evidence",
        "import",
        str(document),
        "--source",
        "manual",
        "--asset",
        "a1",
        "--by",
        "ana",
        *extra,
    )


def imported_id(result) -> str:
    return next(
        line.split(": ")[1] for line in result.output.splitlines() if line.startswith("Evidencia")
    )


def test_import_registra_la_evidencia_y_muestra_id_y_huella(settings, workspace, document):
    result = ens(settings, *import_args(document))

    assert result.exit_code == 0, result.output
    assert f"Huella sha256: {sha256_hex(DATA)}" in result.output
    assert "no se autentica" in result.output
    assert workspace.read_bytes(artifact_key(sha256_hex(DATA))) == DATA


def test_import_de_un_fichero_inexistente_sale_con_2_y_mensaje_en_espanol(settings, tmp_path):
    result = ens(settings, *import_args(tmp_path / "nada.pdf"))

    assert result.exit_code == 2
    assert "No se puede leer el fichero indicado." in result.output


def test_import_con_origen_distinto_de_manual_se_rechaza(settings, document):
    result = ens(
        settings, "evidence", "import", str(document),
        "--source", "github", "--asset", "a1", "--by", "ana",
    )  # fmt: skip

    assert result.exit_code == 2
    assert "solo se admite manual" in result.output


def test_import_con_fecha_sin_zona_horaria_se_rechaza(settings, document):
    result = ens(settings, *import_args(document, "--collected-at", "2026-01-31T10:00:00"))

    assert result.exit_code == 2
    assert "--collected-at: fecha y hora no válidas" in result.output


def test_import_con_valid_until_anterior_a_collected_at_sale_con_1(settings, workspace, document):
    result = ens(
        settings,
        *import_args(
            document,
            "--collected-at", "2026-03-01T10:00:00+01:00",
            "--valid-until", "2026-02-01T10:00:00+01:00",
        ),
    )  # fmt: skip

    assert result.exit_code == 1
    assert "No se importa la evidencia" in result.output
    assert "valid_until" in result.output
    assert workspace.list("evidence") == []


def test_list_muestra_las_evidencias_y_el_total(settings, document):
    ens(settings, *import_args(document, "--valid-until", "2999-01-01T00:00:00+00:00"))
    ens(settings, *import_args(document))

    result = ens(settings, "evidence", "list")

    assert result.exit_code == 0
    assert result.output.count("manual  a1") == 2
    assert "sin caducidad" in result.output
    assert "2999-01-01T00:00:00+00:00" in result.output
    assert "2 evidencias." in result.output


def test_list_vacio_informa_cero_evidencias(settings):
    result = ens(settings, "evidence", "list")

    assert result.exit_code == 0
    assert "0 evidencias." in result.output


def test_list_con_manifiesto_invalido_lo_informa_y_sale_con_1(settings, workspace, document):
    ens(settings, *import_args(document))
    workspace.write_text("evidence/manifests/EV-20260101-deadbeef.yaml", "id: [roto")

    result = ens(settings, "evidence", "list")

    assert result.exit_code == 1
    assert "1 evidencias." in result.output
    assert "Manifiestos no válidos" in result.output
    assert "YAML no válido" in result.output


def test_verify_de_una_evidencia_intacta_sale_con_0(settings, document):
    evidence_id = imported_id(ens(settings, *import_args(document)))

    result = ens(settings, "evidence", "verify", evidence_id)

    assert result.exit_code == 0
    assert "1 evidencias verificadas: íntegras." in result.output


def test_verify_de_una_evidencia_alterada_sale_con_1_mostrando_ambos_hashes(
    settings, workspace, document
):
    evidence_id = imported_id(ens(settings, *import_args(document)))
    altered = b"X" + DATA[1:]
    workspace.write_bytes(artifact_key(sha256_hex(DATA)), altered)

    result = ens(settings, "evidence", "verify", evidence_id)

    assert result.exit_code == 1
    assert "La verificación ha fallado" in result.output
    assert f"hash esperado {sha256_hex(DATA)}" in result.output
    assert f"obtenido {sha256_hex(altered)}" in result.output


def test_verify_all_informa_huerfanos_y_sale_con_1(settings, workspace, document):
    ens(settings, *import_args(document))
    workspace.create_bytes(artifact_key(sha256_hex(b"x")), b"x")

    result = ens(settings, "evidence", "verify", "--all")

    assert result.exit_code == 1
    assert "artefacto huérfano" in result.output


def test_verify_all_de_un_workspace_integro_sale_con_0(settings, document):
    ens(settings, *import_args(document))

    result = ens(settings, "evidence", "verify", "--all")

    assert result.exit_code == 0
    assert "1 evidencias verificadas: íntegras." in result.output


@pytest.mark.parametrize("extra", [[], ["EV-20260101-deadbeef", "--all"]])
def test_verify_exige_un_id_o_all_pero_no_ambos(settings, extra):
    result = ens(settings, "evidence", "verify", *extra)

    assert result.exit_code == 2
    assert "Indica un id de evidencia o --all, no ambos." in result.output


def test_la_importacion_usa_el_momento_actual_por_defecto(settings, workspace, document):
    before = datetime.now(UTC)
    evidence_id = imported_id(ens(settings, *import_args(document)))

    manifest = yaml.safe_load(workspace.read_text(f"evidence/manifests/{evidence_id}.yaml"))

    assert datetime.fromisoformat(str(manifest["collected_at"])) >= before
