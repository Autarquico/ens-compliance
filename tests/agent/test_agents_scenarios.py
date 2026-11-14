"""Escenarios de docs/specs/osquery-agents.md: Windows sin checks, token fuera de la vista,
paquete alterado y combinaciones no soportadas. Sin red, sin credenciales reales, sin docker."""
# ruff: noqa: F811

import pytest

from ens_evidence.agent.install import render_install_script
from ens_evidence.endpoint.checks import HOST_CHECKS
from ens_evidence.endpoint.evaluate import evaluate_workspace
from ens_evidence.models.enums import CheckStatus

from .protocol_support import LAPTOP, NOW, snapshot_event
from .test_cli_agent import ens, settings  # noqa: F401 -- fixture compartida
from .test_install import (
    FORBIDDEN_FLAGS,
    LINUX,
    assert_nothing_installed_or_started,
    payload_sha,
    sandbox,  # noqa: F401
)
from .test_install import SERVER as SERVER_HOST

SENTINEL = "sentinel-token-7f3a9c1e5b2d4086"
POSIX_TARGETS = [
    ("macos", None, None),
    ("linux", "deb", "amd64"),
    ("linux", "deb", "arm64"),
    ("linux", "rpm", "amd64"),
    ("linux", "rpm", "arm64"),
]
ALL_TARGETS = [*POSIX_TARGETS, ("windows", None, None)]
ASSETS = """\
assets:
  - id: lap-ana
    type: laptop
    deployment: deployed
    hostname: lap-ana.taller-norte.example
    os: Windows 11
    owner: Ana Ruiz
    encrypted: true
    usage: Desarrollo
"""
WINDOWS_ROWS = {
    "bitlocker_info": [{"drive_letter": "C:", "protection_status": "1", "conversion_status": "1"}],
    "programs": [{"name": "Firefox", "version": "130.0", "install_location": "C:\\Firefox"}],
}


# --- Windows sin checks ------------------------------------------------------------------------


def test_windows_host_that_delivers_results_gets_unknown_on_every_host_check(world):
    node_key = world.enroll(platform="windows")
    world.workspace.write_text("scope/assets.yaml", ASSETS)
    events = [snapshot_event(name, rows) for name, rows in WINDOWS_ROWS.items()]
    delivered = world.post("log", {"node_key": node_key, "log_type": "result", "data": events})
    assert delivered.status_code == 200
    assert world.state.host(LAPTOP).platform == "windows"

    results = evaluate_workspace(world.workspace, NOW).results

    mine = {r.check_id: r for r in results if r.asset_id == LAPTOP}
    assert set(mine) == set(HOST_CHECKS)
    assert CheckStatus.PASS not in {r.status for r in mine.values()}
    assert all(r.reason_code == "no_evidence" for r in mine.values())
    # HOST-008 y HOST-009 (laptop) también salen, UNKNOWN y sin evidencia.
    assert {"HOST-008", "HOST-009"} <= set(mine)
    # Sin evidencia (`_without_evidence`, endpoint/checks.py): HOST-001 sale FAIL por cobertura,
    # el resto UNKNOWN, nunca PASS.
    assert mine["HOST-001"].status is CheckStatus.FAIL
    assert {r.status for c, r in mine.items() if c != "HOST-001"} == {CheckStatus.UNKNOWN}


# --- El token no queda a la vista --------------------------------------------------------------


def token_file(tmp_path, content=SENTINEL):
    path = tmp_path / "enroll-token"
    path.write_text(content + "\n")
    return path


@pytest.mark.parametrize(("os_name", "package", "arch"), ALL_TARGETS)
def test_install_output_never_contains_the_token(world, settings, tmp_path, os_name, package, arch):
    world.workspace.write_text("scope/assets.yaml", ASSETS)
    path = token_file(tmp_path)
    args = ["install", "--asset", LAPTOP, "--os", os_name, "--server", SERVER_HOST]
    args += ["--token-file", str(path)]
    args += ["--package", package] if package else []
    args += ["--arch", arch] if arch else []

    result = ens(settings, *args)

    assert result.exit_code == 0, result.output
    assert SENTINEL not in result.stdout + result.output
    assert str(path) in result.stdout  # solo la ruta del fichero


@pytest.mark.parametrize(("os_name", "package", "arch"), ALL_TARGETS)
def test_rendered_flags_carry_no_token_and_no_forbidden_flag(os_name, package, arch):
    script = render_install_script(os_name, SERVER_HOST, "/root/token", package, arch)

    assert SENTINEL not in script
    assert not [flag for flag in FORBIDDEN_FLAGS if flag in script]
    assert "--enroll_secret_path=" in script


@pytest.mark.parametrize(("os_name", "package", "arch"), POSIX_TARGETS[1:])
def test_token_only_reaches_the_secret_file_never_flags_or_process_arguments(
    sandbox, os_name, package, arch
):
    sandbox.token.write_text(SENTINEL + "\n")
    sandbox._tool("chown", 'echo "chown $*" >> "$LOG"')
    result = sandbox.run(sandbox.render(os_name, package, arch), expected_sha=payload_sha(sandbox))

    assert result.returncode == 0, result.stderr
    etc = sandbox.root / "etc-osquery"
    assert (etc / "enroll_secret").read_text() == SENTINEL
    assert SENTINEL not in (etc / "osquery.flags").read_text()
    assert SENTINEL not in "\n".join(sandbox.calls)
    assert SENTINEL not in result.stdout + result.stderr


def test_powershell_reads_the_token_from_the_file_and_not_from_arguments():
    script = render_install_script("windows", SERVER_HOST, "C:\\t\\token.txt", None, None)

    assert script.count("$TokenFile") >= 2
    assert "ReadAllText($TokenFile)" in script
    assert "-Token" not in script and "enroll_secret_env" not in script


# --- Paquete alterado --------------------------------------------------------------------------
# test_install.py cubre deb/amd64; aquí las demás variantes de Linux.

OTHER_LINUX_TARGETS = [target for target in POSIX_TARGETS[1:] if target != LINUX]


@pytest.mark.parametrize(("os_name", "package", "arch"), OTHER_LINUX_TARGETS)
def test_altered_package_stops_before_the_installer_and_the_service(
    sandbox, os_name, package, arch
):
    result = sandbox.run(sandbox.render(os_name, package, arch), expected_sha="0" * 64)

    assert_nothing_installed_or_started(sandbox, result)
    assert "sha256" in result.stderr


@pytest.mark.parametrize(("os_name", "package", "arch"), OTHER_LINUX_TARGETS)
def test_truncated_package_stops_before_the_installer_and_the_service(
    sandbox, os_name, package, arch
):
    expected = payload_sha(sandbox)
    sandbox.payload.write_bytes(b"paquete-osq")

    result = sandbox.run(sandbox.render(os_name, package, arch), expected_sha=expected)

    assert_nothing_installed_or_started(sandbox, result)
