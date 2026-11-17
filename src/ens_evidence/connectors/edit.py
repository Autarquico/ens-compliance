"""Escritura de `connectors/<name>.yaml` desde campos sin secretos.

Valida con el mismo modelo y la misma política de secretos que el cargador. Los mensajes de error
son fijos: nunca incluyen valores, claves del fichero ni texto de excepciones. El lock es de proceso
y solo protege a los escritores de este módulo; la escritura es una sola operación atómica del
almacén y no hay deshacer (si falla, el fichero anterior sigue intacto).
"""

import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ValidationError

from ens_evidence.connectors.config import (
    ConnectorCredentialsRejected,
    connector_config_key,
    credential_fields,
    reject_config_credentials,
)
from ens_evidence.serialized import serializer
from ens_evidence.workspace import NotFound, NotText, WorkspaceStore
from ens_evidence.yaml_unique import safe_load_unique

SECRET_IN_FIELD = (
    "Este campo parece contener un secreto: no se guarda; las credenciales van en el entorno."
)
CONFIRM_REQUIRED = (
    "El fichero actual no es válido: confirma que quieres sustituirlo "
    "(se conservan solo las claves conocidas que sean válidas)."
)
GENERAL_INVALID = "La combinación de valores no es válida; revisa los campos."
FIELD_INVALID = "El valor no es válido."
_FIELD_MESSAGES = {
    "missing": "Es obligatorio.",
    "string_too_short": "No puede estar vacío.",
    "string_too_long": "Es demasiado largo.",
    "too_short": "Falta al menos un valor.",
    "int_type": "Debe ser un número entero.",
    "int_parsing": "Debe ser un número entero.",
    "greater_than": "Está fuera del rango permitido.",
    "greater_than_equal": "Está fuera del rango permitido.",
    "less_than": "Está fuera del rango permitido.",
    "less_than_equal": "Está fuera del rango permitido.",
    "literal_error": "Elige una de las opciones.",
    "string_pattern_mismatch": "No tiene el formato esperado.",
    "string_type": "Debe ser texto.",
    "list_type": "Debe ser una lista.",
}


def fix_by_hand(field: str) -> str:
    return f"Hay que corregir «{field}» en el fichero a mano."


serialized = serializer(threading.Lock())  # una escritura de conector a la vez en este proceso


class ConnectorEditRejected(Exception):
    """`errors` por nombre de campo recibido; `general` para todo lo demás. Textos fijos."""

    def __init__(self, errors: dict[str, list[str]], general: list[str]) -> None:
        super().__init__("configuración de conector rechazada")
        self.errors = errors
        self.general = general


@dataclass(frozen=True)
class ExistingConfig:
    state: Literal["absent", "unparseable", "invalid", "valid"]
    raw: dict[str, Any] | None = None  # contenido del fichero: nunca se muestra


def inspect(store: WorkspaceStore, name: str, model: type[BaseModel]) -> ExistingConfig:
    try:
        raw = safe_load_unique(store.read_text(connector_config_key(name)))
    except NotFound:
        return ExistingConfig("absent")
    except (NotText, yaml.YAMLError):
        return ExistingConfig("unparseable")
    if not isinstance(raw, dict):
        return ExistingConfig("unparseable")
    try:
        reject_config_credentials(raw)
        model.model_validate(raw)
    except (ConnectorCredentialsRejected, ValidationError):
        return ExistingConfig("invalid", raw)
    return ExistingConfig("valid", raw)


class _Findings:
    """Reparte los hallazgos entre campos recibidos y mensajes generales fijos."""

    def __init__(self, model: type[BaseModel], fields: Mapping[str, Any]) -> None:
        self._model = model
        self._fields = fields
        self.errors: dict[str, list[str]] = {}
        self.general: list[str] = []

    def add(self, name: object, message: str) -> None:
        if name in self._fields:
            bucket = self.errors.setdefault(str(name), [])
        elif name in self._model.model_fields:
            bucket = self.general
            message = fix_by_hand(str(name))
        else:
            bucket = self.general
            message = GENERAL_INVALID
        if message not in bucket:
            bucket.append(message)

    def raise_if_any(self) -> None:
        if self.errors or self.general:
            raise ConnectorEditRejected(self.errors, self.general)


def _merge(
    existing: ExistingConfig, model: type[BaseModel], fields: Mapping[str, Any]
) -> dict[str, Any]:
    raw = existing.raw or {}
    if existing.state == "valid":
        base = raw
    elif existing.state == "invalid":
        # Reparación: solo las claves del modelo; las desconocidas y las de credencial se descartan.
        flagged = set(credential_fields(raw))
        base = {k: v for k, v in raw.items() if k in model.model_fields and k not in flagged}
    else:
        base = {}
    document = {**base, **fields}
    return {k: v for k, v in document.items() if not (k in fields and v is None)}


@serialized
def save_connector_config(
    store: WorkspaceStore,
    name: str,
    model: type[BaseModel],
    fields: Mapping[str, Any],
    *,
    replace_invalid: bool,
) -> bool:
    """Guarda `fields` sobre `connectors/<name>.yaml`; False si no hay nada que cambiar.

    Un valor `None` borra la clave. Un fichero no válido solo se sustituye con `replace_invalid`."""
    findings = _Findings(model, fields)
    for field in credential_fields(fields):
        findings.add(field, SECRET_IN_FIELD)
    findings.raise_if_any()

    existing = inspect(store, name, model)  # bajo el lock: el estado puede haber cambiado
    if existing.state in ("unparseable", "invalid") and not replace_invalid:
        raise ConnectorEditRejected({}, [CONFIRM_REQUIRED])

    document = _merge(existing, model, fields)
    for field in credential_fields(document):
        findings.add(field, SECRET_IN_FIELD)
    findings.raise_if_any()
    try:
        model.model_validate(document)
    except ValidationError as exc:
        for item in exc.errors(include_input=False, include_url=False, include_context=False):
            loc = item["loc"]
            findings.add(loc[0] if loc else None, _FIELD_MESSAGES.get(item["type"], FIELD_INVALID))
    findings.raise_if_any()

    if document == existing.raw:
        return False
    text = yaml.safe_dump(document, sort_keys=False, allow_unicode=True, default_flow_style=False)
    store.write_text(connector_config_key(name), text)
    return True
