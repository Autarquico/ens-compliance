"""Ajustes del workspace: carga, validación y resolución de rutas."""

import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from ens_evidence.workspace.errors import SettingsError

PRODUCT_NAME = "ens-evidence"
DEFAULT_SETTINGS_PATH = Path("~/.config/ens-evidence/settings.yaml")
SETTINGS_ENV = "ENS_SETTINGS"
_CREDENTIAL_MARKERS = ("accesskey", "secret", "token", "password", "credential")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LocalSettings(_Strict):
    path: Path


class S3Settings(_Strict):
    bucket: str
    prefix: str = ""
    endpoint_url: str | None = None
    region: str | None = None
    allow_insecure_http: bool = False

    @field_validator("bucket")
    @classmethod
    def _bucket_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("no puede estar vacío")
        return value

    @model_validator(mode="after")
    def _endpoint_is_safe(self) -> "S3Settings":
        if self.endpoint_url is None:
            return self
        try:
            parts = urlsplit(self.endpoint_url)
            embedded = parts.username is not None or parts.password is not None
            hostname = parts.hostname
        except ValueError as exc:
            raise ValueError("endpoint_url no es una URL válida") from exc
        if embedded:
            raise ValueError("endpoint_url no puede llevar credenciales")
        if not hostname or parts.scheme not in ("http", "https"):
            raise ValueError("endpoint_url no es una URL http(s) válida")
        if parts.scheme == "http" and not self.allow_insecure_http:
            raise ValueError("endpoint_url exige https (allow_insecure_http solo en desarrollo)")
        return self


class WorkspaceSettings(_Strict):
    backend: Literal["local", "s3"]
    local: LocalSettings | None = None
    s3: S3Settings | None = None

    @model_validator(mode="after")
    def _selected_backend_configured(self) -> "WorkspaceSettings":
        if getattr(self, self.backend) is None:
            raise ValueError(f"falta la sección '{self.backend}'")
        return self


class Settings(_Strict):
    workspace: WorkspaceSettings


def resolve_settings_path(
    explicit: Path | None = None, env: Mapping[str, str] | None = None
) -> Path:
    """`--settings`, luego `ENS_SETTINGS`, luego el fichero de usuario; nunca el cwd."""
    env = os.environ if env is None else env
    if explicit is not None:
        return Path(explicit).expanduser()
    if env.get(SETTINGS_ENV):
        return Path(env[SETTINGS_ENV]).expanduser()
    return DEFAULT_SETTINGS_PATH.expanduser()


def load_settings(path: Path, *, allow_inside_product: bool = False) -> Settings:
    """Carga y valida los ajustes. `allow_inside_product` es solo para fixtures y tests."""
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SettingsError("no existe el fichero de ajustes") from exc
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise SettingsError("no se puede leer el fichero de ajustes") from exc
    if not isinstance(raw, dict):
        raise SettingsError("el fichero de ajustes debe ser un mapa YAML")
    _reject_credential_keys(raw)
    try:
        settings = Settings.model_validate(raw)
    except ValidationError as exc:
        raise SettingsError(_describe(exc)) from exc
    return _resolve_local(settings, path, allow_inside_product)


def _reject_credential_keys(node: Any, trail: str = "") -> None:
    if not isinstance(node, dict):
        return
    for key, value in node.items():
        name = str(key)
        folded = name.lower().replace("_", "").replace("-", "")
        where = f"{trail}.{name}" if trail else name
        if any(marker in folded for marker in _CREDENTIAL_MARKERS):
            raise SettingsError(
                f"'{where}': las credenciales no van en el YAML; usa variables de entorno"
            )
        _reject_credential_keys(value, where)


def _describe(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
        for item in error.errors(include_input=False, include_url=False)
    )


def _resolve_local(settings: Settings, settings_file: Path, allow_inside_product: bool) -> Settings:
    local = settings.workspace.local
    if settings.workspace.backend != "local" or local is None:
        return settings
    target = local.path.expanduser()
    if not target.is_absolute():
        target = settings_file.resolve().parent / target
    target = target.resolve()
    if not allow_inside_product and is_inside_product(target):
        raise SettingsError("el workspace local no puede estar dentro del repo del producto")
    workspace = settings.workspace.model_copy(update={"local": LocalSettings(path=target)})
    return settings.model_copy(update={"workspace": workspace})


def is_inside_product(target: Path) -> bool:
    """True si `target`, tras resolver enlaces simbólicos, cuelga de un checkout del producto."""
    target = Path(target).expanduser().resolve()
    return any(_is_product_root(candidate) for candidate in (target, *target.parents))


def _is_product_root(directory: Path) -> bool:
    try:
        with (directory / "pyproject.toml").open("rb") as handle:
            return tomllib.load(handle).get("project", {}).get("name") == PRODUCT_NAME
    except (OSError, tomllib.TOMLDecodeError):
        return False
