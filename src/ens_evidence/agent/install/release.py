"""Versión de osquery fijada y combinaciones de SO, paquete y arquitectura admitidas."""

import re
from dataclasses import dataclass
from importlib import resources

import yaml

HTTPS_PREFIX = "https://"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
OPERATING_SYSTEMS = ("macos", "linux", "windows")


class InstallError(ValueError):
    """Petición de instalación no válida o no soportada."""


@dataclass(frozen=True)
class OsqueryPackage:
    os: str
    package: str
    arch: str
    filename: str
    url: str
    sha256: str


def _entry(raw: dict) -> OsqueryPackage:
    package = OsqueryPackage(**raw)
    if not package.url.startswith(HTTPS_PREFIX) or not SHA256.match(package.sha256):
        raise InstallError(f"release.yaml no válido para {package.filename}.")
    return package


def load_release() -> tuple[str, tuple[OsqueryPackage, ...]]:
    text = resources.files(__package__).joinpath("release.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    return data["version"], tuple(_entry(raw) for raw in data["packages"])


def resolve_package(os_name: str, package: str | None, arch: str | None) -> OsqueryPackage:
    """El paquete exacto. Linux exige paquete y arquitectura: no se deducen de la caja."""
    if os_name not in OPERATING_SYSTEMS:
        raise InstallError(f"--os debe ser uno de {', '.join(OPERATING_SYSTEMS)}.")
    _, packages = load_release()
    candidates = [entry for entry in packages if entry.os == os_name]
    if os_name == "linux" and (package is None or arch is None):
        raise InstallError("En Linux indica --package (deb|rpm) y --arch (amd64|arm64).")
    for entry in candidates:
        if package in (None, entry.package) and arch in (None, entry.arch):
            return entry
    raise InstallError(
        f"Combinación no soportada: {os_name} {package or '-'} {arch or '-'}. "
        "Disponibles: " + ", ".join(f"{e.os}/{e.package}/{e.arch}" for e in candidates) + "."
    )
