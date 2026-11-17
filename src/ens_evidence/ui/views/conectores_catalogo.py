"""Catálogo de conectores: una ficha estática por conector, contrastada con sus modelos.

Sin E/S salvo `configs_citing`, que solo lee. Nada de aquí inventa campos: los tests exigen que
`fields ∪ preserved` coincida con los campos del modelo y que los nombres coincidan con los entry
points. Los errores de formulario están en español y nunca repiten el valor recibido.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

import yaml
from pydantic import BaseModel

from ens_evidence.backup.environment import (
    ACCESS_KEY_VAR,
    DEFAULT_PREFIX,
    PASSWORD_VAR,
    SECRET_KEY_VAR,
)
from ens_evidence.connectors.backups.config import BackupsConfig
from ens_evidence.connectors.cloudflare.config import CloudflareConfig
from ens_evidence.connectors.cloudflare.connector import TOKEN_ENV as CLOUDFLARE_TOKEN
from ens_evidence.connectors.config import connector_config_key
from ens_evidence.connectors.github.config import GitHubConfig
from ens_evidence.connectors.github.connector import TOKEN_ENV as GITHUB_TOKEN
from ens_evidence.connectors.google_workspace.config import GoogleWorkspaceConfig
from ens_evidence.connectors.google_workspace.connector import KEY_ENV as GOOGLE_KEY
from ens_evidence.connectors.keystore.config import KeystoreConfig
from ens_evidence.connectors.keystore.connector import TOKEN_ENV as KEYSTORE_TOKEN
from ens_evidence.connectors.passwords.config import PasswordsConfig
from ens_evidence.connectors.restic.config import ResticConfig
from ens_evidence.scope.models import AssetType
from ens_evidence.workspace import WorkspaceError, WorkspaceStore
from ens_evidence.yaml_unique import safe_load_unique

Kind = Literal["text", "int", "choice", "lines", "asset"]

REQUIRED = "Obligatorio."
NOT_A_NUMBER = "Debe ser un número entero positivo."
NOT_ALLOWED = "Valor no permitido."
ENV_WHERE = "deploy/.env de la caja"
SETTINGS_WHERE = "ajustes backup.* y deploy/.env"
_MAX_DIGITS = 9


@dataclass(frozen=True)
class Choice:
    value: str
    label: str


@dataclass(frozen=True)
class FieldSpec:
    name: str
    label: str
    kind: Kind
    required: bool
    hint: str | None = None
    choices: tuple[Choice, ...] = ()


@dataclass(frozen=True)
class CredentialSpec:
    kind: Literal["env", "env_prefix", "settings", "none"]
    names: tuple[str, ...]
    where: str


@dataclass(frozen=True)
class ConnectorSpec:
    """Ficha de un conector. Sin `model` es un conector registrado sin ficha (solo lectura)."""

    name: str
    label: str
    model: type[BaseModel] | None
    asset_type: AssetType | None
    fields: tuple[FieldSpec, ...]
    preserved: frozenset[str]
    credential: CredentialSpec | None
    doc_path: str | None
    kit_href: str | None
    status_href: str | None
    editable: bool

    @property
    def doc_href(self) -> str | None:
        return f"/ayuda/conector-{self.name}" if self.doc_path else None


def _asset(hint: str) -> FieldSpec:
    return FieldSpec("asset_id", "Activo del inventario", "asset", True, hint)


def _days(name: str, label: str, required: bool = True, hint: str | None = None) -> FieldSpec:
    return FieldSpec(name, label, "int", required, hint)


def _spec(
    name: str,
    label: str,
    model: type[BaseModel],
    asset_type: AssetType | None,
    fields: tuple[FieldSpec, ...],
    credential: CredentialSpec,
    *,
    preserved: Iterable[str] = (),
    kit_href: str | None = None,
    status_href: str | None = None,
    editable: bool = True,
) -> ConnectorSpec:
    return ConnectorSpec(
        name=name,
        label=label,
        model=model,
        asset_type=asset_type,
        fields=fields,
        preserved=frozenset(preserved),
        credential=credential,
        doc_path=f"docs/connectors/{name}.md",
        kit_href=kit_href,
        status_href=status_href,
        editable=editable,
    )


def _env(*names: str) -> CredentialSpec:
    return CredentialSpec("env", names, ENV_WHERE)


_BACKUPS_SUFFIXES = tuple(
    v.removeprefix(DEFAULT_PREFIX) for v in (PASSWORD_VAR, ACCESS_KEY_VAR, SECRET_KEY_VAR)
)

_SPECS: tuple[ConnectorSpec, ...] = (
    _spec(
        "backups",
        "Copias (varios repositorios)",
        BackupsConfig,
        None,
        (),
        CredentialSpec(
            "env_prefix",
            tuple(f"ENS_BACKUPS_<ID>_{suffix}" for suffix in _BACKUPS_SUFFIXES),
            ENV_WHERE,
        ),
        preserved={"repositories"},
        kit_href="/herramientas/copias",
        status_href="/copias",
        editable=False,
    ),
    _spec(
        "cloudflare",
        "Cloudflare",
        CloudflareConfig,
        AssetType.EDGE_NETWORK,
        (
            _asset("Activo de tipo red de borde."),
            FieldSpec(
                "account_id",
                "Identificador de la cuenta",
                "text",
                True,
                "32 caracteres hexadecimales en minúscula (0-9, a-f).",
            ),
            FieldSpec(
                "token_kind",
                "Tipo de token",
                "choice",
                False,
                "Si lo dejas vacío, se usa un token de cuenta.",
                (Choice("account", "De cuenta"), Choice("user", "De usuario")),
            ),
            FieldSpec(
                "ui_hostname",
                "Nombre de host de la interfaz",
                "text",
                False,
                "Como «panel.ejemplo.org»: sin https://, puerto, ruta ni comodín.",
            ),
        ),
        _env(CLOUDFLARE_TOKEN),
        preserved={"public_routes"},
    ),
    _spec(
        "github",
        "GitHub",
        GitHubConfig,
        AssetType.CODE_HOSTING,
        (
            _asset("Activo de tipo alojamiento de código."),
            FieldSpec(
                "organization", "Organización", "text", True, "Nombre de la organización en GitHub."
            ),
        ),
        _env(GITHUB_TOKEN),
    ),
    _spec(
        "google-workspace",
        "Google Workspace",
        GoogleWorkspaceConfig,
        AssetType.IDENTITY,
        (
            _asset("Activo de tipo identidad."),
            FieldSpec(
                "customer",
                "Cliente",
                "text",
                False,
                "my_customer o un identificador que empieza por C.",
            ),
            FieldSpec(
                "admin_subject", "Administrador delegado", "text", True, "Correo del administrador."
            ),
            FieldSpec(
                "domains",
                "Dominios",
                "lines",
                True,
                "Uno por línea, como «ejemplo.org»; incluye el del correo del administrador.",
            ),
        ),
        _env(GOOGLE_KEY),
    ),
    _spec(
        "keystore",
        "Almacén de claves",
        KeystoreConfig,
        AssetType.APPLICATION,
        (
            _asset("Activo de tipo aplicación."),
            FieldSpec(
                "address", "Dirección", "text", False, "Si lo dejas vacío, se usa la de la caja."
            ),
            _days(
                "max_key_age_days",
                "Edad máxima de una clave (días)",
                hint="La decide la organización.",
            ),
            _days("max_token_period_hours", "Periodo máximo del token (horas)", False),
        ),
        _env(KEYSTORE_TOKEN),
        kit_href="/herramientas/claves",
    ),
    _spec(
        "passwords",
        "Contraseñas",
        PasswordsConfig,
        AssetType.APPLICATION,
        (
            _asset("Activo de tipo aplicación."),
            FieldSpec(
                "export_path",
                "Ruta del volcado",
                "text",
                False,
                "Si lo dejas vacío, se usa la del kit.",
            ),
            _days("inactive_days", "Días de inactividad"),
            _days("max_export_age_minutes", "Antigüedad máxima del volcado (minutos)"),
        ),
        CredentialSpec("none", (), ""),
        kit_href="/herramientas/contrasenas",
    ),
    _spec(
        "restic",
        "Restic",
        ResticConfig,
        AssetType.BACKUP,
        (
            _asset("Activo de tipo copia de seguridad."),
            _days("max_backup_age_hours", "Antigüedad máxima de la copia (horas)"),
            _days("max_check_age_days", "Antigüedad máxima de la comprobación (días)"),
            _days("restore_test_validity_days", "Vigencia de la prueba de restauración (días)"),
            FieldSpec(
                "min_check_read_data_percent",
                "Porcentaje mínimo de datos leídos",
                "int",
                True,
                "De 0 a 100.",
            ),
        ),
        CredentialSpec("settings", (PASSWORD_VAR, ACCESS_KEY_VAR, SECRET_KEY_VAR), SETTINGS_WHERE),
        preserved={"offsite"},
        kit_href="/herramientas/copias",
    ),
)

CATALOGUE: dict[str, ConnectorSpec] = {spec.name: spec for spec in _SPECS}
EDITABLE: tuple[str, ...] = tuple(name for name, spec in CATALOGUE.items() if spec.editable)


def _without_sheet(name: str) -> ConnectorSpec:
    return ConnectorSpec(name, name, None, None, (), frozenset(), None, None, None, None, False)


def catalogue_for(registered_names: Iterable[str]) -> tuple[ConnectorSpec, ...]:
    """Una ficha por conector registrado: la del catálogo o, si no hay, una genérica."""
    return tuple(CATALOGUE.get(name) or _without_sheet(name) for name in sorted(registered_names))


def _parse_int(text: str) -> int | None:
    if text.isascii() and text.isdigit() and len(text) <= _MAX_DIGITS:
        return int(text)
    return None


def _convert(field: FieldSpec, raw: str) -> tuple[Any, str | None]:
    """Valor convertido y, si no se puede, el mensaje de error."""
    text = raw.strip()
    if field.kind == "lines":
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        return (lines, None) if lines else (None, None)
    if not text:
        return None, None
    if field.kind == "int":
        number = _parse_int(text)
        return (number, None) if number is not None else (None, NOT_A_NUMBER)
    if field.kind == "choice" and text not in {c.value for c in field.choices}:
        return None, NOT_ALLOWED
    return text, None


def _nullable(spec: ConnectorSpec, field: FieldSpec) -> bool:
    assert spec.model is not None
    return spec.model.model_fields[field.name].default is None


def form_fields_to_document(
    spec: ConnectorSpec, form: Mapping[str, str]
) -> tuple[dict[str, Any], dict[str, list[str]]]:
    """Documento para validar con el modelo y errores por campo.

    Un campo opcional vacío es `None` si el modelo lo admite (su valor por defecto es `None`) y
    se omite si no, para que actúe el valor por defecto del modelo.
    """
    document: dict[str, Any] = {}
    errors: dict[str, list[str]] = {}
    for field in spec.fields:
        value, problem = _convert(field, form.get(field.name) or "")
        if problem:
            errors[field.name] = [problem]
        elif value is not None:
            document[field.name] = value
        elif field.required:
            errors[field.name] = [REQUIRED]
        elif _nullable(spec, field):
            document[field.name] = None
    return document, errors


def document_to_values(spec: ConnectorSpec, raw: Mapping[str, Any]) -> dict[str, str]:
    """Valores de formulario a partir de un documento; lo que no es escalar ni lista, vacío."""
    values: dict[str, str] = {}
    for field in spec.fields:
        item = raw.get(field.name)
        if isinstance(item, list):
            values[field.name] = "\n".join(str(i) for i in item if isinstance(i, str | int))
        elif isinstance(item, str | int) and not isinstance(item, bool):
            values[field.name] = str(item)
        else:
            values[field.name] = ""
    return values


def _cited_assets(name: str, raw: Any) -> set[str]:
    if not isinstance(raw, dict):
        return set()
    cited = {raw.get("asset_id")}
    if name == "backups" and isinstance(raw.get("repositories"), list):
        cited |= {r.get("asset_id") for r in raw["repositories"] if isinstance(r, dict)}
    return {c for c in cited if isinstance(c, str)}


def configs_citing(store: WorkspaceStore, asset_id: str) -> tuple[str, ...]:
    """Nombres de los conectores del catálogo cuyo `connectors/<name>.yaml` cita el activo.

    Ilegible o ausente cuenta como que no lo cita: no se informa de ficheros que no se leen.
    """
    citing: list[str] = []
    for name in CATALOGUE:
        try:
            raw = safe_load_unique(store.read_text(connector_config_key(name)))
        except (WorkspaceError, yaml.YAMLError):
            continue
        if asset_id in _cited_assets(name, raw):
            citing.append(name)
    return tuple(citing)
