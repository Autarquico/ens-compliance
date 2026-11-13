"""`agent install`: validación, recursos, flags y contrato del script generado."""
# ruff: noqa: F811

import hashlib
import os
import subprocess
import sys
import zipfile
from importlib import resources
from pathlib import Path

import pytest

from ens_evidence.agent.install import InstallError, render_install_script
from ens_evidence.agent.install.release import load_release

from .protocol_support import LAPTOP
from .test_cli_agent import ens, settings  # noqa: F401 -- fixture compartida

SERVER = "agentes.example.org"
TOKEN = "enroll-secret-token-value"
FORBIDDEN_FLAGS = ("--logger_tls_compress", "--tls_secret_always", "--enroll_secret_env")
REQUIRED_FLAGS = (
    "--tls_hostname=agentes.example.org",
    "--host_identifier=uuid",
    "--enroll_secret_path=",
    "--enroll_tls_endpoint=/agent/enroll",
    "--config_tls_endpoint=/agent/config",
    "--logger_tls_endpoint=/agent/log",
    "--distributed_tls_read_endpoint=/agent/distributed/read",
    "--distributed_tls_write_endpoint=/agent/distributed/write",
    "--config_refresh=3600",
    "--buffered_log_max=10000",
    "--logger_tls_period=60",
    "--logger_tls_backoff_max=3600",
)
LINUX = ("linux", "deb", "amd64")


def install_args(os_name="linux", package="deb", arch="amd64", server=SERVER, asset=LAPTOP):
    args = ["install", "--asset", asset, "--os", os_name, "--server", server]
    args += ["--token-file", "/root/token"]
    if package:
        args += ["--package", package]
    if arch:
        args += ["--arch", arch]
    return args


def render(os_name="linux", package="deb", arch="amd64", server=SERVER, token_file="/root/token"):
    return render_install_script(os_name, server, token_file, package, arch)


# --- CLI ---------------------------------------------------------------------------------------


def test_prints_script_for_an_enrolled_asset_and_nothing_else(settings):
    result = ens(settings, *install_args())
    assert result.exit_code == 0, result.output
    assert result.stdout.startswith("#!/bin/sh\n")
    assert "osquery 5.24.1" in result.stdout


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"asset": "fantasma"}, "no está inscrito"),
        ({"server": "https://agentes.example.org"}, "nombre de host"),
        ({"server": "agentes.example.org:8443"}, "nombre de host"),
        ({"server": "agentes.example.org/ruta"}, "nombre de host"),
        ({"server": "a b"}, "nombre de host"),
        ({"server": "127.0.0.1"}, "no una dirección IP"),
        ({"package": None}, "indica --package"),
        ({"arch": None}, "indica --package"),
        ({"package": "msi"}, "no soportada"),
        ({"arch": "riscv"}, "no soportada"),
        ({"os_name": "windows", "package": None, "arch": "arm64"}, "no soportada"),
        ({"os_name": "macos", "package": "deb", "arch": None}, "no soportada"),
        ({"os_name": "freebsd", "package": None, "arch": None}, "--os debe ser"),
    ],
)
def test_rejects_invalid_requests(settings, kwargs, message):
    result = ens(settings, *install_args(**kwargs))
    assert result.exit_code == 2
    assert message in result.output
    assert not result.stdout.startswith("#!")


@pytest.mark.parametrize(
    ("os_name", "package", "arch"),
    [
        ("macos", None, None),
        ("windows", None, None),
        ("windows", "msi", "amd64"),
        ("linux", "deb", "arm64"),
        ("linux", "rpm", "amd64"),
        ("linux", "rpm", "arm64"),
    ],
)
def test_supported_combinations(settings, os_name, package, arch):
    result = ens(settings, *install_args(os_name, package, arch))
    assert result.exit_code == 0, result.output


def test_install_never_reads_the_secrets(settings, monkeypatch):
    for name in ("ENS_AGENT_ENROLL_KEY", "ENS_AGENT_NODE_KEY_SECRET"):
        monkeypatch.delenv(name)
    assert ens(settings, *install_args()).exit_code == 0


def test_server_is_validated_before_it_reaches_any_script():
    for hostile in ("a;rm -rf /", "$(id).example.org", "a.example.org\nX", "-a.example.org", ""):
        with pytest.raises(InstallError):
            render(server=hostile)


@pytest.mark.parametrize("os_name", ["macos", "linux", "windows"])
def test_token_file_with_control_characters_is_rejected(os_name):
    with pytest.raises(InstallError):
        render_install_script(os_name, SERVER, "/root/t\nx", "deb", "amd64")


# --- Recursos y release ------------------------------------------------------------------------


def test_release_pins_5_24_1_with_https_and_sha256():
    version, packages = load_release()
    assert version == "5.24.1"
    assert len(packages) == 6
    for entry in packages:
        assert entry.url.startswith("https://github.com/osquery/osquery/releases/download/5.24.1/")
        assert entry.url.endswith(entry.filename)
        assert len(entry.sha256) == 64


@pytest.mark.parametrize("os_name", ["macos", "linux", "windows"])
def test_flags_have_required_and_none_of_the_forbidden(os_name):
    text = resources.files("ens_evidence.agent.install").joinpath(f"flags/{os_name}.flags")
    flags = text.read_text(encoding="utf-8")
    for flag in REQUIRED_FLAGS[1:]:
        assert flag in flags
    assert "--tls_hostname=@@SERVER@@" in flags
    assert not [flag for flag in FORBIDDEN_FLAGS if flag in flags]
    rendered = render(*(("linux", "deb", "amd64") if os_name == "linux" else (os_name, None, None)))
    for flag in REQUIRED_FLAGS:
        assert flag in rendered
    assert not [flag for flag in FORBIDDEN_FLAGS if flag in rendered]
    assert "@@" not in rendered


def test_wheel_contains_the_install_resources(tmp_path):
    root = Path(__file__).resolve().parents[2]
    build = subprocess.run(
        ["uv", "build", "--wheel", "--offline", "--out-dir", str(tmp_path), str(root)],
        capture_output=True,
        text=True,
    )
    assert build.returncode == 0, build.stderr
    (wheel,) = tmp_path.glob("*.whl")
    names = set(zipfile.ZipFile(wheel).namelist())
    base = "ens_evidence/agent/install/"
    expected = {
        "release.yaml",
        "flags/macos.flags",
        "flags/linux.flags",
        "flags/windows.flags",
        "scripts/common.sh",
        "scripts/macos.sh",
        "scripts/linux.sh",
        "scripts/windows.ps1",
    }
    assert {base + name for name in expected} <= names


# --- Script POSIX: ejecución real en un sandbox ------------------------------------------------


def test_posix_scripts_parse():
    for args in (LINUX, ("linux", "rpm", "arm64"), ("macos", None, None)):
        result = subprocess.run(
            ["sh", "-n"], input=render(*args), capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("os_name", ["macos", "linux"])
def test_script_has_no_tracing_and_never_embeds_a_token(os_name):
    script = render(
        os_name, None if os_name == "macos" else "deb", None if os_name == "macos" else "amd64"
    )
    assert "set -eu" in script
    assert "set -x" not in script and "set -o xtrace" not in script
    assert "--enroll_secret_env" not in script
    assert TOKEN not in script


class Sandbox:
    """Raíz falsa: rutas del sistema reescritas a un tmp y herramientas simuladas en PATH."""

    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "root"
        self.bin = tmp_path / "bin"
        self.log = tmp_path / "calls.log"
        self.payload = tmp_path / "payload"
        self.token = tmp_path / "token"
        self.work = tmp_path / "work"
        for path in (self.root, self.bin, self.work):
            path.mkdir()
        self.log.touch()
        self.token.write_text(TOKEN + "\n")
        self.payload.write_bytes(b"paquete-osquery-falso")
        self._tool("id", "echo 0")
        self._tool("chown", "exit 0")
        self._tool("curl", self._curl())
        self._tool("sha256sum", f'exec {sys.executable} -I {self._hasher()} "$@"')
        self._tool("dpkg", 'echo "dpkg $*" >> "$LOG"')
        self._tool("rpm", 'echo "rpm $*" >> "$LOG"')
        self._tool("systemctl", 'echo "systemctl $*" >> "$LOG"')

    def _tool(self, name: str, body: str) -> None:
        path = self.bin / name
        path.write_text(f"#!/bin/sh\n{body}\n")
        path.chmod(0o755)

    def _hasher(self) -> Path:
        path = self.bin / "hasher.py"
        path.write_text(
            "import hashlib, sys\n"
            "print(hashlib.sha256(open(sys.argv[1], 'rb').read()).hexdigest(), sys.argv[1])\n"
        )
        return path

    def _curl(self) -> str:
        return (
            'while [ $# -gt 0 ]; do [ "$1" = --output ] && out=$2; shift; done\n'
            f'echo "curl" >> "$LOG"\ncp "{self.payload}" "$out"\n[ -z "${{CURL_FAIL:-}}" ]'
        )

    def run(self, script: str, *, expected_sha: str | None, env: dict | None = None):
        release_sha = next(e.sha256 for e in load_release()[1] if e.filename in script)
        script = script.replace(release_sha, expected_sha or release_sha)
        script = script.replace("/etc/osquery", str(self.root / "etc-osquery"))
        path = self.work / "install.sh"
        path.write_text(script)
        environ = {**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}", "LOG": str(self.log)}
        environ.update(env or {})
        return subprocess.run(
            ["sh", str(path)], capture_output=True, text=True, env=environ, check=False
        )

    def render(self, *target: str | None, token_file: Path | None = None) -> str:
        return render(*(target or LINUX), token_file=str(token_file or self.token))

    @property
    def calls(self) -> list[str]:
        return self.log.read_text().splitlines()


@pytest.fixture
def sandbox(tmp_path) -> Sandbox:
    return Sandbox(tmp_path)


def payload_sha(box: Sandbox) -> str:
    return hashlib.sha256(box.payload.read_bytes()).hexdigest()


def assert_nothing_installed_or_started(box: Sandbox, result) -> None:
    assert result.returncode != 0
    assert not [c for c in box.calls if c.startswith(("dpkg", "rpm", "systemctl"))]
    assert not (box.root / "etc-osquery").exists()
    assert TOKEN not in result.stdout + result.stderr


def test_happy_path_installs_with_private_token_and_prints_no_token(sandbox):
    result = sandbox.run(sandbox.render(), expected_sha=payload_sha(sandbox))
    assert result.returncode == 0, result.stderr
    secret = sandbox.root / "etc-osquery" / "enroll_secret"
    assert secret.read_text() == TOKEN
    assert oct(secret.stat().st_mode & 0o777) == "0o600"
    assert TOKEN not in result.stdout + result.stderr
    flags = (sandbox.root / "etc-osquery" / "osquery.flags").read_text()
    assert f"--enroll_secret_path={secret}" in flags
    calls = sandbox.calls
    assert calls.index("systemctl disable --now osqueryd") < calls.index(
        "systemctl enable --now osqueryd"
    )
    assert any(c.startswith("dpkg -i ") for c in calls)


def test_sha_mismatch_aborts_before_the_installer(sandbox):
    result = sandbox.run(sandbox.render(), expected_sha="0" * 64)
    assert_nothing_installed_or_started(sandbox, result)
    assert "sha256" in result.stderr


def test_truncated_download_aborts_before_the_installer(sandbox):
    expected = payload_sha(sandbox)
    sandbox.payload.write_bytes(b"paquete-osq")
    result = sandbox.run(sandbox.render(), expected_sha=expected)
    assert_nothing_installed_or_started(sandbox, result)


def test_failed_download_aborts_before_the_installer(sandbox):
    result = sandbox.run(
        sandbox.render(), expected_sha=payload_sha(sandbox), env={"CURL_FAIL": "1"}
    )
    assert_nothing_installed_or_started(sandbox, result)


def test_missing_tool_aborts_before_downloading(sandbox):
    (sandbox.bin / "systemctl").unlink()
    result = sandbox.run(
        render(*LINUX),
        expected_sha=payload_sha(sandbox),
        env={"PATH": f"{sandbox.bin}:/usr/bin:/bin"},
    )
    assert_nothing_installed_or_started(sandbox, result)
    assert "curl" not in sandbox.calls
    assert "systemctl" in result.stderr


def test_installer_failure_never_starts_the_service(sandbox):
    sandbox._tool("dpkg", 'echo "dpkg $*" >> "$LOG"; exit 1')
    result = sandbox.run(sandbox.render(), expected_sha=payload_sha(sandbox))
    assert result.returncode != 0
    assert not [c for c in sandbox.calls if c.startswith("systemctl")]
    assert not (sandbox.root / "etc-osquery" / "enroll_secret").exists()
    assert TOKEN not in result.stdout + result.stderr


def test_token_file_that_is_a_symlink_or_not_regular_is_rejected(sandbox, tmp_path):
    link = tmp_path / "link"
    link.symlink_to(sandbox.token)
    for bad in (link, tmp_path):
        result = sandbox.run(sandbox.render(token_file=bad), expected_sha=payload_sha(sandbox))
        assert_nothing_installed_or_started(sandbox, result)
        assert "curl" not in sandbox.calls


def test_rpm_installs_with_rpm(sandbox):
    script = sandbox.render("linux", "rpm", "arm64")
    result = sandbox.run(script, expected_sha=payload_sha(sandbox))
    assert result.returncode == 0, result.stderr
    assert any(c.startswith("rpm -U ") for c in sandbox.calls)


def test_server_with_shell_metacharacters_never_reaches_the_script():
    with pytest.raises(InstallError):
        render(server="x.example.org'; touch /tmp/pwned; '")


# --- PowerShell: solo texto (pwsh no está disponible aquí) -------------------------------------


@pytest.fixture
def powershell() -> str:
    return render("windows", None, None, token_file="C:\\Users\\o'brien\\token.txt")


@pytest.mark.parametrize("quote", ["'", "\u2018", "\u2019", "\u201a", "\u201b"])
def test_powershell_doubles_every_quote_that_closes_a_literal(quote):
    script = render("windows", None, None, token_file=f"C:\\x{quote}; calc{quote}\\t.txt")
    assert f"$TokenFile = 'C:\\x{quote}{quote}; calc{quote}{quote}\\t.txt'" in script


def test_powershell_resolves_token_file_once_to_an_absolute_path(powershell):
    assert powershell.count("GetUnresolvedProviderPathFromPSPath($TokenFile)") == 1
    assert "[IO.File]::ReadAllText($TokenFile)" in powershell


def test_powershell_contract_at_text_level(powershell):
    assert "$ErrorActionPreference = 'Stop'" in powershell
    assert "Set-PSDebug" not in powershell
    assert "-Trace" not in powershell
    assert "$TokenFile = 'C:\\Users\\o''brien\\token.txt'" in powershell
    assert "-Algorithm SHA256" in powershell
    assert "Tls12" in powershell
    assert "https://github.com/osquery/osquery/releases/download/5.24.1/osquery-5.24.1.msi" in (
        powershell
    )
    assert "*S-1-5-18" in powershell and "*S-1-5-32-544" in powershell
    assert "$LASTEXITCODE -ne 0" in powershell
    assert "$installer.ExitCode -ne 0" in powershell
    assert "ReparsePoint" in powershell
    assert "finally" in powershell and "Remove-Item" in powershell
    assert "C:\\Program Files\\osquery\\enroll_secret" in powershell


def test_powershell_orders_check_install_stop_token_start(powershell):
    order = [
        "$actual -ne $ExpectedSha256",
        "msiexec.exe",
        "Stop-Service",
        "-StartupType Disabled",
        "New-Item -ItemType File -Path $EnrollSecret",
        "Restrict-ToSystemAndAdministrators $EnrollSecret",
        "ReadAllText($TokenFile)",
        "Start-Service",
    ]
    positions = [powershell.index(fragment) for fragment in order]
    assert positions == sorted(positions)
