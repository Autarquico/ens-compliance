"""Genera el script de instalación. No lee secretos ni escribe: solo devuelve texto."""

import re
import shlex
from importlib import resources

from ens_evidence.agent.install.release import InstallError, load_release, resolve_package

HOST_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
SCRIPTS = {"macos": "macos.sh", "linux": "linux.sh", "windows": "windows.ps1"}
PLACEHOLDER = re.compile(r"@@[A-Z0-9_]+@@")


def validate_server(server: str) -> str:
    """Un nombre de host sin esquema, puerto, ruta ni credenciales."""
    host = server.lower()
    labels = host.split(".")
    if len(host) > 253 or not all(HOST_LABEL.match(label) for label in labels):
        raise InstallError("--server debe ser un nombre de host, sin esquema, puerto ni ruta.")
    if labels[-1].isdigit():
        raise InstallError("--server debe ser un nombre de host, no una dirección IP.")
    return host


def validate_token_file(path: str) -> str:
    if not path or any(ord(char) < 32 or ord(char) == 127 for char in path):
        raise InstallError("--token-file debe ser una ruta sin caracteres de control.")
    return path


def _sh(value: str) -> str:
    return shlex.quote(value)


POWERSHELL_QUOTES = "'\u2018\u2019\u201a\u201b"


def _ps(value: str) -> str:
    """Literal de comilla simple: PowerShell cierra con U+0027 y con las comillas tipográficas."""
    escaped = "".join(char * 2 if char in POWERSHELL_QUOTES else char for char in value)
    return "'" + escaped + "'"


def _resource(*parts: str) -> str:
    node = resources.files("ens_evidence.agent.install")
    for part in parts:
        node = node.joinpath(part)
    return node.read_text(encoding="utf-8")


def _fill(template: str, values: dict[str, str]) -> str:
    return PLACEHOLDER.sub(lambda match: values[match.group(0)[2:-2]], template)


def render_install_script(
    os_name: str,
    server: str,
    token_file: str,
    package: str | None,
    arch: str | None,
    *,
    with_antimalware: bool = False,
) -> str:
    version, packages = load_release()
    entry = resolve_package(os_name, package, arch, packages)
    host = validate_server(server)
    token = validate_token_file(token_file)
    flags = _fill(_resource("flags", f"{os_name}.flags"), {"SERVER": host})
    quote = _ps if os_name == "windows" else _sh
    values = {
        "VERSION": version,
        "PACKAGE": entry.package,
        "FILENAME": quote(entry.filename),
        "URL": quote(entry.url),
        "SHA256": quote(entry.sha256),
        "TOKEN_FILE": quote(token),
        "FLAGS": flags.rstrip("\n"),
    }
    if os_name == "linux":
        values["WITH_ANTIMALWARE"] = "1" if with_antimalware else "0"
    if os_name != "windows":
        values["COMMON"] = _fill(_resource("scripts", "common.sh"), values)
    return _fill(_resource("scripts", SCRIPTS[os_name]), values)
