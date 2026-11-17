"""Edición mínima del alcance: añadir un activo y rellenar el sistema, sin tocar la aprobación."""

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from functools import wraps
from typing import Any, get_args, get_origin

import yaml
from pydantic import BaseModel, ValidationError

from ens_evidence.scope.approval import APPROVAL_KEY, revoke_approval
from ens_evidence.scope.inventory import (
    ASSETS_KEY,
    DEPENDENCIES_KEY,
    SERVICES_KEY,
    SYSTEM_KEY,
    Inventory,
    check_integrity,
)
from ens_evidence.scope.loader import load_documents
from ens_evidence.scope.messages import describe_validation
from ens_evidence.scope.models import (
    Asset,
    AssetsFile,
    DependenciesFile,
    ServicesFile,
    SystemScope,
)
from ens_evidence.scope.secrets import find_secrets
from ens_evidence.workspace import NotFound, NotText, WorkspaceStore
from ens_evidence.yaml_unique import safe_load_unique

_TRUE = {"true", "si", "sí", "yes"}
_FALSE = {"false", "no"}


_WRITES = threading.Lock()  # una escritura del alcance a la vez dentro de este proceso


def serialized[**P, R](operation: Callable[P, R]) -> Callable[P, R]:
    """Lectura, validación, escritura y revocación de una operación, sin intercalarse con otra."""

    @wraps(operation)
    def run(*args: P.args, **kwargs: P.kwargs) -> R:
        with _WRITES:
            return operation(*args, **kwargs)

    return run


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
    except NotText:
        raise ScopeEditRejected([f"{key}: no es texto UTF-8; guárdalo como UTF-8"]) from None
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


def _appended(
    store: WorkspaceStore, key: str, list_name: str, model: type[BaseModel], fields: dict[str, Any]
) -> dict[str, Any]:
    """Fichero `key` con `fields` añadido a `list_name`, validado y sin escribir."""
    _reject_secrets(key, fields)
    document = _read(store, key)
    current = document.get(list_name) or []
    if fields["id"] in {item.get("id") for item in current if isinstance(item, dict)}:
        raise ScopeEditRejected([f"{key}: el id {fields['id']} ya existe"])
    document[list_name] = [*current, fields]
    _checked(key, model, document)
    return document


def _with_included(system: dict[str, Any], list_name: str, ref: str) -> dict[str, Any]:
    return {**system, list_name: [*(system.get(list_name) or []), ref]}


@serialized
def add_asset(store: WorkspaceStore, fields: dict[str, Any], include: bool, now: datetime) -> bool:
    """Añade un activo (y lo incluye en el alcance si include). True si revocó la aprobación.

    Valida el activo con el modelo del inventario; la coherencia global la comprueban
    inventory validate y scope approve.
    """
    assets = _appended(store, ASSETS_KEY, "assets", AssetsFile, fields)
    system = _read(store, SYSTEM_KEY)
    if include:
        system = _with_included(system, "included_assets", fields["id"])
    _write(store, ASSETS_KEY, assets)
    _write(store, SYSTEM_KEY, system)
    return revoke_approval(store, "scope add-asset", now)


@serialized
def add_service(
    store: WorkspaceStore,
    service_id: str,
    name: str,
    assets: list[str],
    include: bool,
    now: datetime,
) -> bool:
    """Añade un servicio (y lo incluye en el alcance si include). True si revocó la aprobación."""
    fields = {"id": service_id, "name": name, "assets": assets}
    services = _appended(store, SERVICES_KEY, "services", ServicesFile, fields)
    system = _read(store, SYSTEM_KEY)
    if include:
        system = _with_included(system, "included_services", service_id)
    _write(store, SERVICES_KEY, services)
    _write(store, SYSTEM_KEY, system)
    return revoke_approval(store, "scope add-service", now)


@serialized
def include_asset(store: WorkspaceStore, asset_id: str, now: datetime) -> bool:
    """Incluye en el alcance un activo existente. True si revocó la aprobación.

    Si ya estaba incluido no cambia nada y no revoca.
    """
    assets = _read(store, ASSETS_KEY).get("assets") or []
    if asset_id not in {a.get("id") for a in assets if isinstance(a, dict)}:
        raise ScopeEditRejected([f"{ASSETS_KEY}: el activo {asset_id} no existe"])
    system = _read(store, SYSTEM_KEY)
    if asset_id in (system.get("included_assets") or []):
        return False
    system = _with_included(system, "included_assets", asset_id)
    _checked(SYSTEM_KEY, SystemScope, system)
    _write(store, SYSTEM_KEY, system)
    return revoke_approval(store, "scope include-asset", now)


@serialized
def include_service(store: WorkspaceStore, service_id: str, now: datetime) -> bool:
    """Incluye en el alcance un servicio existente. True si revocó la aprobación.

    Si ya estaba incluido no cambia nada y no revoca.
    """
    services = _read(store, SERVICES_KEY).get("services") or []
    if service_id not in {s.get("id") for s in services if isinstance(s, dict)}:
        raise ScopeEditRejected([f"{SERVICES_KEY}: el servicio {service_id} no existe"])
    system = _read(store, SYSTEM_KEY)
    if service_id in (system.get("included_services") or []):
        return False
    system = _with_included(system, "included_services", service_id)
    _checked(SYSTEM_KEY, SystemScope, system)
    _write(store, SYSTEM_KEY, system)
    return revoke_approval(store, "scope include-service", now)


@serialized
def add_dependency(
    store: WorkspaceStore,
    dependency_id: str,
    asset: str,
    depends_on: str,
    purpose: str | None,
    now: datetime,
) -> bool:
    """Añade una dependencia entre activos. True si revocó la aprobación."""
    fields = {"id": dependency_id, "asset": asset, "depends_on": depends_on}
    if purpose:
        fields["purpose"] = purpose
    dependencies = _appended(store, DEPENDENCIES_KEY, "dependencies", DependenciesFile, fields)
    _write(store, DEPENDENCIES_KEY, dependencies)
    return revoke_approval(store, "scope add-dependency", now)


@serialized
def set_system(store: WorkspaceStore, name: str, description: str, now: datetime) -> bool:
    """Rellena nombre y descripción del sistema. True si revocó la aprobación."""
    _reject_secrets(SYSTEM_KEY, {"name": name, "description": description})
    system = _read(store, SYSTEM_KEY)
    system.update(name=name, description=description)
    _checked(SYSTEM_KEY, SystemScope, system)
    _write(store, SYSTEM_KEY, system)
    return revoke_approval(store, "scope set-system", now)


# --- alta, edición y baja de un activo: una operación, todo o nada ------------------------------


@dataclass(frozen=True)
class ScopeChoice:
    """Dónde queda el activo: incluido, o excluido con su justificación."""

    included: bool
    justification: str | None = None


def _entries(document: dict[str, Any], list_name: str) -> list[dict[str, Any]]:
    return [item for item in document.get(list_name) or [] if isinstance(item, dict)]


def _without_asset(system: dict[str, Any], asset_id: str) -> dict[str, Any]:
    exclusions = [
        e
        for e in system.get("exclusions") or []
        if not (isinstance(e, dict) and e.get("kind") == "asset" and e.get("ref") == asset_id)
    ]
    included = [ref for ref in system.get("included_assets") or [] if ref != asset_id]
    return {**system, "included_assets": included, "exclusions": exclusions}


def _placed(system: dict[str, Any], asset_id: str, choice: ScopeChoice) -> dict[str, Any]:
    stripped = _without_asset(system, asset_id)
    if choice.included:
        placed = {**stripped, "included_assets": [*stripped["included_assets"], asset_id]}
    else:
        exclusion = {"kind": "asset", "ref": asset_id, "justification": choice.justification or ""}
        placed = {**stripped, "exclusions": [*stripped["exclusions"], exclusion]}
    return system if _same_placement(system, placed, asset_id) else placed


def _same_placement(a: dict[str, Any], b: dict[str, Any], asset_id: str) -> bool:
    def place(system: dict[str, Any]) -> tuple[bool, list[Any]]:
        mine = [
            e
            for e in system.get("exclusions") or []
            if isinstance(e, dict) and e.get("ref") == asset_id and e.get("kind") == "asset"
        ]
        return asset_id in (system.get("included_assets") or []), mine

    return place(a) == place(b)


def _merged(existing: dict[str, Any] | None, asset_id: str, changes: Mapping[str, Any]) -> dict:
    merged = {**(existing or {}), **changes, "id": asset_id}
    return {name: value for name, value in merged.items() if value is not None}


def _inventory_for(
    assets: dict[str, Any], system: dict[str, Any], store: WorkspaceStore
) -> Inventory | None:
    """Inventario resultante con los servicios y dependencias actuales (vacíos si no valen)."""
    documents = load_documents(store)
    try:
        return Inventory(
            AssetsFile.model_validate(assets),
            documents.services.model or ServicesFile(services=[]),
            documents.dependencies.model or DependenciesFile(dependencies=[]),
            SystemScope.model_validate(system),
        )
    except ValidationError:
        return None


def _new_integrity_errors(
    store: WorkspaceStore,
    before: tuple[dict[str, Any], dict[str, Any]],
    after: tuple[dict[str, Any], dict[str, Any]],
) -> list[str]:
    """Errores de integridad que introduce el cambio; los que ya había no lo bloquean."""
    after_inventory = _inventory_for(*after, store)
    if after_inventory is None:
        return []
    before_inventory = _inventory_for(*before, store)
    known = set(check_integrity(before_inventory)) if before_inventory else set()
    return [e for e in check_integrity(after_inventory) if e not in known]


def _commit(
    store: WorkspaceStore,
    changed: list[tuple[str, str, dict[str, Any]]],
    reason: str,
    now: datetime,
) -> bool:
    """Escribe (clave, texto previo, documento nuevo); si una falla, restaura las anteriores.

    Revoca la aprobación solo tras escribir todo; si la revocación falla, restaura también
    todo lo escrito. False si no había nada que escribir.
    """
    written: list[tuple[str, str]] = []
    try:
        for key, previous, document in changed:
            _write(store, key, document)
            written.append((key, previous))
        if not changed:
            return False
        if store.exists(APPROVAL_KEY):
            written.append((APPROVAL_KEY, store.read_text(APPROVAL_KEY)))
        return revoke_approval(store, reason, now)
    except Exception:
        for key, previous in reversed(written):
            store.write_text(key, previous)
        raise


def _asset_documents(
    store: WorkspaceStore,
) -> tuple[dict[str, Any], dict[str, Any], str, str]:
    assets, system = _read(store, ASSETS_KEY), _read(store, SYSTEM_KEY)
    return assets, system, store.read_text(ASSETS_KEY), store.read_text(SYSTEM_KEY)


@serialized
def save_asset(
    store: WorkspaceStore,
    asset_id: str,
    changes: Mapping[str, Any],
    scope_choice: ScopeChoice | None,
    now: datetime,
    *,
    create: bool,
) -> bool:
    """Crea o edita un activo y, si se pide, lo coloca en el alcance. True si revocó la aprobación.

    En la edición `changes` se aplica sobre el activo existente: lo que no menciona se
    conserva y un valor None borra el campo. Valida todo antes de escribir; si la segunda
    escritura falla, restaura la primera. Sin cambios efectivos no escribe ni revoca.
    """
    _reject_secrets(ASSETS_KEY, dict(changes))
    if scope_choice and scope_choice.justification:
        _reject_secrets(SYSTEM_KEY, {"justification": scope_choice.justification})
    assets, system, assets_text, system_text = _asset_documents(store)
    current = _entries(assets, "assets")
    existing = next((a for a in current if a.get("id") == asset_id), None)
    if create and existing is not None:
        raise ScopeEditRejected([f"{ASSETS_KEY}: el id {asset_id} ya existe"])
    if not create and existing is None:
        raise ScopeEditRejected([f"{ASSETS_KEY}: el activo {asset_id} no existe"])
    merged = _merged(existing, asset_id, changes)
    new_assets = {
        **assets,
        "assets": [merged if a is existing else a for a in assets.get("assets") or []]
        if existing is not None
        else [*(assets.get("assets") or []), merged],
    }
    new_system = _placed(system, asset_id, scope_choice) if scope_choice else system
    _checked(ASSETS_KEY, AssetsFile, new_assets)
    _checked(SYSTEM_KEY, SystemScope, new_system)
    if errors := _new_integrity_errors(store, (assets, system), (new_assets, new_system)):
        raise ScopeEditRejected(errors)
    changed = [
        (key, text, document)
        for key, text, old, document in (
            (ASSETS_KEY, assets_text, assets, new_assets),
            (SYSTEM_KEY, system_text, system, new_system),
        )
        if document != old
    ]
    return _commit(store, changed, "scope add-asset" if create else "scope edit-asset", now)


def asset_references(
    services: ServicesFile | None, dependencies: DependenciesFile | None, asset_id: str
) -> list[str]:
    """Quién referencia el activo: un mensaje por servicio y por dependencia."""
    found = [
        f"{SERVICES_KEY}: el servicio {s.id} referencia el activo {asset_id}"
        for s in (services.services if services else [])
        if asset_id in s.assets
    ]
    found.extend(
        f"{DEPENDENCIES_KEY}: la dependencia {d.id} referencia el activo {asset_id}"
        for d in (dependencies.dependencies if dependencies else [])
        if asset_id in (d.asset, d.depends_on)
    )
    return found


@serialized
def remove_asset(store: WorkspaceStore, asset_id: str, now: datetime) -> bool:
    """Da de baja un activo del inventario y del alcance. True si revocó la aprobación.

    Exige servicios y dependencias legibles (ausentes valen como vacíos) y que nadie
    referencie el activo; si no, rechaza sin escribir nada.
    """
    documents = load_documents(store)
    unusable = [
        error
        for document in (documents.services, documents.dependencies)
        if not document.absent
        for error in document.errors
    ]
    if unusable:
        raise ScopeEditRejected(unusable)
    if references := asset_references(
        documents.services.model, documents.dependencies.model, asset_id
    ):
        raise ScopeEditRejected(references)
    assets, system, assets_text, system_text = _asset_documents(store)
    if asset_id not in {a.get("id") for a in _entries(assets, "assets")}:
        raise ScopeEditRejected([f"{ASSETS_KEY}: el activo {asset_id} no existe"])
    new_assets = {
        **assets,
        "assets": [
            a
            for a in assets.get("assets") or []
            if not (isinstance(a, dict) and a.get("id") == asset_id)
        ],
    }
    new_system = _without_asset(system, asset_id)
    _checked(ASSETS_KEY, AssetsFile, new_assets)
    _checked(SYSTEM_KEY, SystemScope, new_system)
    changed = [
        (ASSETS_KEY, assets_text, new_assets),
        (SYSTEM_KEY, system_text, new_system),
    ]
    return _commit(store, changed, "scope remove-asset", now)
