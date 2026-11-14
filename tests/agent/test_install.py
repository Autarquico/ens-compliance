"""`agent install`: validación, recursos, flags y contrato del script generado."""
# ruff: noqa: F811

import hashlib
import os
import shutil
import subprocess
import sys
import zipfile
from importlib import resources
from pathlib import Path

import pytest

from ens_evidence.agent.install import InstallError, render_install_script
from ens_evidence.agent.install.release import load_release

from .protocol_support import LAPTOP
from .protocol_support import SERVER as SERVER_ASSET
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


ASSETS = """\
assets:
  - id: lap-ana
    type: laptop
    deployment: deployed
    os: macOS 15
    owner: Ana Ruiz
    encrypted: true
    usage: Desarrollo
  - id: srv-app
    type: server
    deployment: deployed
    hostname: srv-app.taller-norte.example
    function: Aplicación
    os: Debian 12
    environment: production
    location: VPS
"""
ASSETS_KEY = "scope/assets.yaml"


@pytest.fixture(autouse=True)
def inventory(world):
    world.workspace.write_text(ASSETS_KEY, ASSETS)


def install_args(os_name="linux", package="deb", arch="amd64", server=SERVER, asset=LAPTOP):
    args = ["install", "--asset", asset, "--os", os_name, "--server", server]
    args += ["--token-file", "/root/token"]
    if package:
        args += ["--package", package]
    if arch:
        args += ["--arch", arch]
    return args


def render(
    os_name="linux",
    package="deb",
    arch="amd64",
    server=SERVER,
    token_file="/root/token",
    with_antimalware=False,
):
    return render_install_script(
        os_name, server, token_file, package, arch, with_antimalware=with_antimalware
    )


# --- CLI ---------------------------------------------------------------------------------------


def test_prints_script_for_an_enrolled_asset_and_nothing_else(settings):
    result = ens(settings, *install_args())
    assert result.exit_code == 0, result.output
    assert result.stdout.startswith("#!/bin/sh\n")
    assert "osquery 5.24.1" in result.stdout


def test_server_script_installs_clamav_and_laptop_script_does_not(settings):
    server = ens(settings, *install_args(asset=SERVER_ASSET)).stdout
    laptop = ens(settings, *install_args(asset=LAPTOP)).stdout
    assert "WITH_ANTIMALWARE=1" in server
    assert "WITH_ANTIMALWARE=0" in laptop


@pytest.mark.parametrize("os_name", ["macos", "windows"])
def test_server_on_macos_or_windows_gets_no_clamav(settings, os_name):
    out = ens(settings, *install_args(os_name, None, None, asset=SERVER_ASSET)).stdout
    assert "clamav" not in out.lower()


def test_asset_enrolled_but_missing_from_the_inventory_is_rejected(settings, world):
    world.workspace.write_text(ASSETS_KEY, ASSETS.split("  - id: srv-app")[0])
    result = ens(settings, *install_args(asset=SERVER_ASSET))
    assert result.exit_code == 2
    assert "scope/assets.yaml" in result.output
    assert not result.stdout.startswith("#!")


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
        self._tool("apt-get", 'echo "apt-get $*" >> "$LOG"')

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

    def render(
        self,
        *target: str | None,
        token_file: Path | None = None,
        with_antimalware: bool = False,
    ) -> str:
        return render(
            *(target or LINUX),
            token_file=str(token_file or self.token),
            with_antimalware=with_antimalware,
        )

    @property
    def calls(self) -> list[str]:
        return self.log.read_text().splitlines()


@pytest.fixture
def sandbox(tmp_path) -> Sandbox:
    return Sandbox(tmp_path)


def payload_sha(box: Sandbox) -> str:
    return hashlib.sha256(box.payload.read_bytes()).hexdigest()


def isolated_path(box: Sandbox, tmp_path: Path) -> str:
    """PATH solo con las utilidades básicas y las herramientas simuladas de la raíz falsa."""
    shell = tmp_path / "shell"
    shell.mkdir()
    for tool in (
        "sh",
        "printf",
        "cut",
        "mktemp",
        "rm",
        "tr",
        "cat",
        "chmod",
        "mkdir",
        "dirname",
        "cp",
    ):
        found = shutil.which(tool)
        if found:
            (shell / tool).symlink_to(found)
    return f"{box.bin}:{shell}"


def assert_nothing_installed_or_started(box: Sandbox, result) -> None:
    assert result.returncode != 0
    assert not [c for c in box.calls if c.startswith(("dpkg", "rpm", "systemctl", "apt-get"))]
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


def test_missing_tool_aborts_before_downloading(sandbox, tmp_path):
    # PATH aislado: el runner de CI tiene systemctl en /usr/bin y lo encontraría.
    (sandbox.bin / "systemctl").unlink()
    for tool in ("cut", "mktemp", "rm", "tr", "cat", "chmod", "mkdir", "dirname", "cp"):
        sandbox._tool(tool, "exit 0")
    shell = tmp_path / "shell"
    shell.mkdir()
    (shell / "sh").symlink_to(shutil.which("sh"))
    result = sandbox.run(
        render(*LINUX),
        expected_sha=payload_sha(sandbox),
        env={"PATH": f"{sandbox.bin}:{shell}"},
    )
    assert_nothing_installed_or_started(sandbox, result)
    assert "curl" not in sandbox.calls
    assert "systemctl" in result.stderr


def test_server_deb_installs_clamav_before_osquery(sandbox):
    result = sandbox.run(sandbox.render(with_antimalware=True), expected_sha=payload_sha(sandbox))
    assert result.returncode == 0, result.stderr
    calls = sandbox.calls
    index = calls.index("apt-get update")
    clamav = calls.index("apt-get install -y clamav clamav-freshclam")
    assert index < clamav
    freshclam = calls.index("systemctl enable --now clamav-freshclam")
    osquery = next(i for i, c in enumerate(calls) if c.startswith("dpkg -i "))
    assert clamav < freshclam < osquery
    assert osquery < calls.index("systemctl disable --now osqueryd")


def test_laptop_deb_never_touches_clamav(sandbox):
    result = sandbox.run(sandbox.render(), expected_sha=payload_sha(sandbox))
    assert result.returncode == 0, result.stderr
    assert not [c for c in sandbox.calls if "clamav" in c or c.startswith("apt-get")]


def test_server_rpm_aborts_with_instructions_before_mutating(sandbox, tmp_path):
    script = sandbox.render("linux", "rpm", "amd64", with_antimalware=True)
    result = sandbox.run(
        script,
        expected_sha=payload_sha(sandbox),
        env={"PATH": isolated_path(sandbox, tmp_path)},
    )
    assert_nothing_installed_or_started(sandbox, result)
    assert "curl" not in sandbox.calls
    assert "EPEL" in result.stderr
    assert "clamav-update" in result.stderr


def test_server_rpm_continues_when_clamav_was_installed_by_hand(sandbox, tmp_path):
    script = sandbox.render("linux", "rpm", "amd64", with_antimalware=True)
    env = {"PATH": isolated_path(sandbox, tmp_path)}
    first = sandbox.run(script, expected_sha=payload_sha(sandbox), env=env)
    assert first.returncode != 0
    sandbox._tool("clamscan", "exit 0")
    sandbox._tool("freshclam", "exit 0")
    retry = sandbox.run(script, expected_sha=payload_sha(sandbox), env=env)
    assert retry.returncode == 0, retry.stderr
    assert any(c.startswith("rpm -U ") for c in sandbox.calls)
    assert not [c for c in sandbox.calls if "clamav" in c or c.startswith("apt-get")]


def test_server_deb_without_dpkg_aborts_before_touching_clamav(sandbox, tmp_path):
    (sandbox.bin / "dpkg").unlink()
    result = sandbox.run(
        sandbox.render(with_antimalware=True),
        expected_sha=payload_sha(sandbox),
        env={"PATH": isolated_path(sandbox, tmp_path)},
    )
    assert_nothing_installed_or_started(sandbox, result)
    assert "dpkg" in result.stderr


def test_failure_after_clamav_install_reports_the_partial_state(sandbox):
    sandbox._tool("dpkg", 'echo "dpkg $*" >> "$LOG"; exit 1')
    result = sandbox.run(sandbox.render(with_antimalware=True), expected_sha=payload_sha(sandbox))
    assert result.returncode != 0
    assert "ClamAV ya está instalado y osquery no" in result.stderr


def test_apt_update_failure_aborts_before_osquery(sandbox):
    sandbox._tool("apt-get", 'echo "apt-get $*" >> "$LOG"; exit 100')
    result = sandbox.run(sandbox.render(with_antimalware=True), expected_sha=payload_sha(sandbox))
    assert result.returncode != 0
    assert not [c for c in sandbox.calls if c.startswith(("dpkg", "systemctl", "apt-get install"))]


def test_clamav_failure_aborts_before_osquery(sandbox):
    sandbox._tool("apt-get", 'echo "apt-get $*" >> "$LOG"; [ "$1" = install ] && exit 100; exit 0')
    result = sandbox.run(sandbox.render(with_antimalware=True), expected_sha=payload_sha(sandbox))
    assert result.returncode != 0
    assert "ClamAV" in result.stderr
    assert not [c for c in sandbox.calls if c.startswith(("dpkg", "systemctl"))]
    assert not (sandbox.root / "etc-osquery").exists()


def test_server_deb_without_apt_get_aborts_before_downloading(sandbox, tmp_path):
    (sandbox.bin / "apt-get").unlink()
    result = sandbox.run(
        sandbox.render(with_antimalware=True),
        expected_sha=payload_sha(sandbox),
        env={"PATH": isolated_path(sandbox, tmp_path)},
    )
    assert_nothing_installed_or_started(sandbox, result)
    assert "curl" not in sandbox.calls
    assert "apt-get" in result.stderr


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


def test_rpm_install_is_repeatable_with_the_same_version(sandbox):
    sandbox._tool(
        "rpm",
        'echo "rpm $*" >> "$LOG"\n'
        'if [ -e "$LOG.rpm" ]; then case "$*" in *--replacepkgs*) ;; *) exit 1;; esac; fi\n'
        'touch "$LOG.rpm"',
    )
    script = sandbox.render("linux", "rpm", "arm64")
    for _ in range(2):
        result = sandbox.run(script, expected_sha=payload_sha(sandbox))
        assert result.returncode == 0, result.stderr
    assert len([c for c in sandbox.calls if c.startswith("rpm -U --replacepkgs ")]) == 2


def test_token_file_equal_to_the_destination_ends_with_the_token_never_empty(sandbox):
    etc = sandbox.root / "etc-osquery"
    etc.mkdir()
    destination = etc / "enroll_secret"
    destination.write_text(TOKEN + "\n")
    hard_link = sandbox.work / "alias"
    os.link(destination, hard_link)
    for token_file in (destination, hard_link):
        result = sandbox.run(
            sandbox.render(token_file=token_file), expected_sha=payload_sha(sandbox)
        )
        assert result.returncode == 0, result.stderr
        assert destination.read_text() == TOKEN
        assert TOKEN not in result.stdout + result.stderr


def test_token_file_with_only_line_breaks_aborts_before_touching_the_destination(sandbox):
    etc = sandbox.root / "etc-osquery"
    etc.mkdir()
    destination = etc / "enroll_secret"
    destination.write_text("previo")
    sandbox.token.write_text("\r\n\n")
    result = sandbox.run(sandbox.render(), expected_sha=payload_sha(sandbox))
    assert result.returncode != 0
    assert destination.read_text() == "previo"
    assert not [c for c in sandbox.calls if c.startswith(("curl", "dpkg", "rpm", "systemctl"))]


def test_service_that_cannot_be_stopped_aborts_before_flags_and_token(sandbox):
    sandbox._tool(
        "systemctl", 'echo "systemctl $*" >> "$LOG"; case "$*" in disable*) exit 1;; esac'
    )
    result = sandbox.run(sandbox.render(), expected_sha=payload_sha(sandbox))
    assert result.returncode != 0
    assert not (sandbox.root / "etc-osquery" / "enroll_secret").exists()
    assert not (sandbox.root / "etc-osquery" / "osquery.flags").exists()
    assert "systemctl enable --now osqueryd" not in sandbox.calls


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
    assert "$installer.ExitCode -ne 0" in powershell
    assert "ReparsePoint" in powershell
    assert "finally" in powershell and "Remove-Item" in powershell
    assert "C:\\Program Files\\osquery\\enroll_secret" in powershell


def test_powershell_orders_check_install_stop_token_start(powershell):
    order = [
        "[IO.File]::ReadAllText($TokenFile)",
        "$actual -ne $ExpectedSha256",
        "msiexec.exe",
        "Disable-Osqueryd\n",
        "New-Item -ItemType File -Path $EnrollSecret",
        "Set-ExclusiveAcl $EnrollSecret",
        "ReadAllText($TokenCopy)",
        "Start-Service",
    ]
    positions = [powershell.index(fragment) for fragment in order]
    assert positions == sorted(positions)


def test_powershell_acl_replaces_the_whole_dacl_with_well_known_sids(powershell):
    assert "S-1-5-18" in powershell and "S-1-5-32-544" in powershell
    assert "SetAccessRuleProtection($true, $false)" in powershell
    assert "SetOwner($AdministratorsSid)" in powershell
    assert "New-Object Security.AccessControl.FileSecurity" in powershell
    assert "Set-Acl -LiteralPath $Path -AclObject $acl" in powershell
    assert "icacls" not in powershell
    assert "Set-ExclusiveAcl $WorkDir $true" in powershell
    assert "Set-ExclusiveAcl $EnrollSecret $false" in powershell


def test_powershell_acl_is_applied_before_the_token_is_written(powershell):
    assert powershell.index("Set-ExclusiveAcl $EnrollSecret") < powershell.index(
        "[IO.File]::WriteAllText($EnrollSecret"
    )


def test_powershell_reads_the_token_before_creating_the_destination(powershell):
    read = powershell.index("[IO.File]::ReadAllText($TokenFile)")
    assert read < powershell.index("Invoke-WebRequest")
    assert read < powershell.index("New-Item -ItemType File -Path $EnrollSecret")
    assert "$secret.Length -eq 0" in powershell
    assert "Test-SameFile" not in powershell and "fsutil" not in powershell


def test_powershell_stop_is_never_silenced_and_is_verified(powershell):
    assert "SilentlyContinue" not in powershell.split("finally")[0]
    assert "Stop-Service -Name osqueryd -Force -ErrorAction Stop" in powershell
    assert "Set-Service -Name osqueryd -StartupType Disabled -ErrorAction Stop" in powershell
    assert "Get-Service -Name osqueryd -ErrorAction Stop" in powershell
    assert "WaitForStatus(" in powershell and "FromSeconds(30)" in powershell
    assert "ServiceControllerStatus]::Stopped" in powershell
    assert "Write-Error" in powershell


def test_powershell_failure_after_the_msi_leaves_the_service_stopped_and_disabled(powershell):
    assert "Stop-Service -Name osqueryd -Force" in powershell
    assert "Set-Service -Name osqueryd -StartupType Disabled" in powershell
    body = powershell[powershell.index("msiexec.exe") :]
    cleanup = body.index("try { Disable-Osqueryd }")
    assert body.index("catch {") < cleanup < body.index("throw $failure")
    assert powershell.rindex("try {", 0, powershell.index("msiexec.exe")) > powershell.index(
        "Invoke-WebRequest"
    )


def test_temp_dir_that_cannot_be_removed_is_reported_and_fails(sandbox):
    sandbox._tool("rm", "exit 1")
    result = sandbox.run(sandbox.render(), expected_sha=payload_sha(sandbox))
    assert result.returncode != 0
    assert "copia del token" in result.stderr
    assert TOKEN not in result.stdout + result.stderr


def test_powershell_temp_cleanup_is_never_silenced(powershell):
    cleanup = powershell.split("finally")[1]
    assert "SilentlyContinue" not in cleanup
    assert "Remove-Item -LiteralPath $WorkDir -Recurse -Force -ErrorAction Stop" in cleanup
    assert "copia del token" in cleanup
    assert "if ($ResidueLeft) { exit 1 }" in powershell
