"""Edición mínima del alcance: añadir un activo y rellenar el sistema, sin tocar la aprobación."""

from datetime import datetime
from typing import Any, get_args, get_origin

import yaml
from pydantic import BaseModel, ValidationError

from ens_evidence.scope.approval import revoke_approval
from ens_evidence.scope.inventory import ASSETS_KEY, SYSTEM_KEY
from ens_evidence.scope.messages import describe_validation
from ens_evidence.scope.models import Asset, AssetsFile, SystemScope
from ens_evidence.scope.secrets import find_secrets
from ens_evidence.workspace import NotFound, WorkspaceStore
from ens_evidence.yaml_unique import safe_load_unique

_TRUE = {"true", "si", "sí", "yes"}
_FALSE = {"false", "no"}


class ScopeEditRejected(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


def _admits(annotation: Any, target: type) -> bool:
    """True si la anotación es `target`, `target[...]` o una unión que lo incluya."""
    if annotation is target or get_origin(annotation) is target:
        return True
    return any(_admits(arg, target) for arg in get_args(annotation))


def parse_field(name: str, value: str) -> Any:
    """Convierte el texto de la línea de órdenes al tipo del campo del activo."""
    field = Asset.model_fields.get(name)
    annotation = field.annotation if field else None
    if _admits(annotation, bool):
        lowered = value.strip().lower()
        if lowered not in _TRUE | _FALSE:
            raise ScopeEditRejected([f"{name}: indica true o false"])
        return lowered in _TRUE
    if _admits(annotation, list):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


def _read(store: WorkspaceStore, key: str) -> dict[str, Any]:
    try:
        raw = safe_load_unique(store.read_text(key))
    except NotFound:
        raise ScopeEditRejected(
            [f"{key}: no existe; crea el workspace con ens-evidence init"]
        ) from None
    except yaml.YAMLError:
        raise ScopeEditRejected([f"{key}: no es YAML válido; revisa la sintaxis"]) from None
    if not isinstance(raw, dict):
        raise ScopeEditRejected([f"{key}: no tiene la forma esperada"])
    return raw


def _checked(key: str, model: type[BaseModel], data: dict[str, Any]) -> None:
    try:
        model.model_validate(data)
    except ValidationError as exc:
        raise ScopeEditRejected(describe_validation(key, exc, data)) from None


def _write(store: WorkspaceStore, key: str, data: dict[str, Any]) -> None:
    store.write_text(
        key, yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)
    )


def _reject_secrets(key: str, fields: dict[str, Any]) -> None:
    """Rechaza antes de escribir; nombra el campo, nunca el valor."""
    if leaked := find_secrets(fields):
        raise ScopeEditRejected(
            [f"{key}: {path} parece contener un secreto; quítalo" for path in leaked]
        )


def add_asset(store: WorkspaceStore, fields: dict[str, Any], include: bool, now: datetime) -> bool:
    """Añade un activo (y lo incluye en el alcance si include). True si revocó la aprobación.

    Valida el activo con el modelo del inventario; la coherencia global la comprueban
    inventory validate y scope approve.
    """
    _reject_secrets(ASSETS_KEY, fields)
    assets = _read(store, ASSETS_KEY)
    current = assets.get("assets") or []
    if fields["id"] in {a.get("id") for a in current if isinstance(a, dict)}:
        raise ScopeEditRejected([f"{ASSETS_KEY}: el id {fields['id']} ya existe"])
    assets["assets"] = [*current, fields]
    _checked(ASSETS_KEY, AssetsFile, assets)
    system = _read(store, SYSTEM_KEY)
    if include:
        system["included_assets"] = [*(system.get("included_assets") or []), fields["id"]]
    _write(store, ASSETS_KEY, assets)
    _write(store, SYSTEM_KEY, system)
    return revoke_approval(store, "scope add-asset", now)


def set_system(store: WorkspaceStore, name: str, description: str, now: datetime) -> bool:
    """Rellena nombre y descripción del sistema. True si revocó la aprobación."""
    _reject_secrets(SYSTEM_KEY, {"name": name, "description": description})
    system = _read(store, SYSTEM_KEY)
    system.update(name=name, description=description)
    _checked(SYSTEM_KEY, SystemScope, system)
    _write(store, SYSTEM_KEY, system)
    return revoke_approval(store, "scope set-system", now)
