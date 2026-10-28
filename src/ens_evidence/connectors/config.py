"""Configuración de un conector: `connectors/<name>.yaml` en el workspace (ADR 0006)."""

from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from ens_evidence.scope.secrets import find_secrets
from ens_evidence.workspace import NotFound, NotText, WorkspaceError, WorkspaceStore
from ens_evidence.workspace.settings import reject_credential_keys

# Claves de configuración cuyo nombre contiene un marcador de credencial pero no lo es.
_ALLOWED_KEYS = frozenset({"token_kind"})


class ConnectorConfigError(WorkspaceError):
    """La configuración del conector no es válida; el mensaje no incluye valores."""


def connector_config_key(name: str) -> str:
    return f"connectors/{name}.yaml"


def load_connector_config[M: BaseModel](store: WorkspaceStore, name: str, model: type[M]) -> M:
    key = connector_config_key(name)
    try:
        raw = yaml.safe_load(store.read_text(key))
    except NotFound:
        raise ConnectorConfigError(
            f"{key}: no existe; créalo (ver docs/connectors/{name}.md)"
        ) from None
    except (NotText, yaml.YAMLError):
        raise ConnectorConfigError(f"{key}: no es YAML UTF-8 válido") from None
    if not isinstance(raw, dict):
        raise ConnectorConfigError(f"{key}: debe ser un mapa YAML")
    _reject_credentials(key, raw)
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
            for item in exc.errors(include_input=False, include_url=False)
        )
        raise ConnectorConfigError(f"{key}: {details}") from None


def _reject_credentials(key: str, raw: dict[str, Any]) -> None:
    candidate = {name: value for name, value in raw.items() if name not in _ALLOWED_KEYS}
    try:
        reject_credential_keys(candidate)
    except WorkspaceError as exc:
        raise ConnectorConfigError(f"{key}: {exc}") from None
    if leaked := find_secrets(candidate):
        raise ConnectorConfigError(f"{key}: parece contener secretos en {', '.join(leaked)}")
