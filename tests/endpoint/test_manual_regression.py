"""`evidence import --source manual` no cambia con la fuente endpoint."""

from typer.testing import CliRunner

from ens_evidence.cli import app
from ens_evidence.evidence import list_manifests

runner = CliRunner()


def ens(settings, *args):
    return runner.invoke(app, ["--settings", str(settings), *args])


def test_la_importacion_manual_sigue_registrando_una_evidencia(tmp_path, settings, store):
    document = tmp_path / "politica.pdf"
    document.write_bytes(b"politica de acceso v1")

    result = ens(
        settings,
        *("evidence", "import", str(document), "--source", "manual"),
        *("--asset", "lap-ana", "--by", "ana"),
    )

    assert result.exit_code == 0, result.output
    [manifest] = list_manifests(store).manifests
    assert manifest.source == "manual"
    assert manifest.provenance.submitted_by == "ana"


def test_una_fuente_desconocida_se_sigue_rechazando(tmp_path, settings, store):
    document = tmp_path / "x.bin"
    document.write_bytes(b"x")

    result = ens(
        settings,
        *("evidence", "import", str(document), "--source", "osquery"),
        *("--asset", "lap-ana", "--by", "ana"),
    )

    assert result.exit_code == 2
    assert store.list("evidence") == []
