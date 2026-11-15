"""`ens-evidence endpoint check`: evaluación HOST por activo con código de salida."""

from datetime import timedelta

from typer.testing import CliRunner

from ens_evidence.cli import app
from ens_evidence.endpoint.checks import HOST_CHECKS, WORKSTATION_ONLY_CHECKS

from .support import collect, import_package, utc_now

runner = CliRunner()


def check(settings, *args):
    return runner.invoke(app, ["--settings", str(settings), "endpoint", "check", *args])


def test_con_evidencia_vigente_imprime_un_resultado_por_activo_y_check_y_sale_con_0(
    tmp_path, settings, store, server_package
):
    import_package(store, collect(tmp_path / "run").data)
    import_package(store, server_package, asset="srv-app")

    result = check(settings)

    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert "lap-ana  HOST-001  PASS" in result.output
    laptop_lines = [line for line in lines if line.startswith("lap-ana  HOST-")]
    server_lines = [line for line in lines if line.startswith("srv-app  HOST-")]
    assert len(laptop_lines) == len(HOST_CHECKS)
    assert len(server_lines) == len(HOST_CHECKS) - len(WORKSTATION_ONLY_CHECKS)
    assert "caducada" not in result.output


def test_un_fail_sale_con_4(tmp_path, settings, store):
    # srv-app está inscrito pero sin evidencia: HOST-001 FAIL
    import_package(store, collect(tmp_path / "run").data)

    result = check(settings)

    assert "srv-app  HOST-001  FAIL" in result.output
    assert result.exit_code == 4


def test_con_evidencia_caducada_lo_indica_y_sale_con_4(tmp_path, settings, store):
    import_package(store, collect(tmp_path / "run").data)

    result = check(settings, "--at", (utc_now() + timedelta(days=31)).isoformat())

    assert result.exit_code == 4
    assert "lap-ana: evidencia caducada" in result.output
    assert "lap-ana  HOST-004  UNKNOWN" in result.output


def test_at_sin_zona_se_rechaza(settings, store):
    result = check(settings, "--at", "2026-01-01T00:00:00")

    assert result.exit_code == 2
