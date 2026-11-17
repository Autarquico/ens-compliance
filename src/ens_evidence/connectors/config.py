"""Configuración de un conector: `connectors/<name>.yaml` en el workspace (ADR 0006)."""

from collections.abc import Mapping
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from ens_evidence.scope.secrets import find_secrets
from ens_evidence.workspace import NotFound, NotText, WorkspaceError, WorkspaceStore
from ens_evidence.workspace.settings import reject_credential_keys
from ens_evidence.yaml_unique import safe_load_unique

# Claves de configuración cuyo nombre contiene un marcador de credencial pero no lo es.
_ALLOWED_KEYS = frozenset({"token_kind"})


class ConnectorConfigError(WorkspaceError):
    """La configuración del conector no es válida; el mensaje no incluye valores."""


def connector_config_key(name: str) -> str:
    return f"connectors/{name}.yaml"


def load_connector_config[M: BaseModel](store: WorkspaceStore, name: str, model: type[M]) -> M:
    key = connector_config_key(name)
    try:
        raw = safe_load_unique(store.read_text(key))
    except NotFound:
        raise ConnectorConfigError(
            f"{key}: no existe; créalo (ver docs/connectors/{name}.md)"
        ) from None
    except (NotText, yaml.YAMLError):
        raise ConnectorConfigError(f"{key}: no es YAML UTF-8 válido") from None
    if not isinstance(raw, dict):
        raise ConnectorConfigError(f"{key}: debe ser un mapa YAML")
    try:
        reject_config_credentials(raw)
    except ConnectorCredentialsRejected as exc:
        raise ConnectorConfigError(f"{key}: {exc}") from None
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
            for item in exc.errors(include_input=False, include_url=False)
        )
        raise ConnectorConfigError(f"{key}: {details}") from None


class ConnectorCredentialsRejected(ConnectorConfigError):
    """Credencial o secreto en la configuración; `fields`: claves de primer nivel afectadas."""

    def __init__(self, message: str, fields: tuple[str, ...]) -> None:
        super().__init__(message)
        self.fields = fields


def _credential_scan(raw: Mapping[str, Any]) -> tuple[str | None, tuple[str, ...]]:
    """Mensaje (con rutas, sin valores) y claves de primer nivel que incumplen la política."""
    flagged: list[str] = []
    message: str | None = None
    for name, value in raw.items():
        entry = {name: value}
        if name in _ALLOWED_KEYS:
            # El nombre no es una credencial, pero el valor sí puede serlo.
            leaked = find_secrets(entry, by_key_name=False)
        else:
            try:
                reject_credential_keys(entry)
            except WorkspaceError as exc:
                message = message or str(exc)
                flagged.append(str(name))
                continue
            leaked = find_secrets(entry)
        if leaked:
            flagged.append(str(name))
    if message is None and flagged:
        paths = [
            path
            for name, value in raw.items()
            for path in find_secrets({name: value}, by_key_name=name not in _ALLOWED_KEYS)
        ]
        message = f"parece contener secretos en {', '.join(paths)}"
    return message, tuple(flagged)


def credential_fields(raw: Mapping[str, Any]) -> tuple[str, ...]:
    """Claves de primer nivel que la política del cargador rechazaría; nunca valores."""
    return _credential_scan(raw)[1]


def reject_config_credentials(raw: Mapping[str, Any]) -> None:
    """Política del cargador: ni claves de credencial ni valores con forma de secreto."""
    message, fields = _credential_scan(raw)
    if message is not None:
        raise ConnectorCredentialsRejected(message, fields)
