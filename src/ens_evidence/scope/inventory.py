"""Carga del inventario desde el workspace, validación agregada y huella canónica."""

import hashlib
import json
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from ens_evidence.scope.messages import SEPARATOR, describe_validation
from ens_evidence.scope.models import (
    AssetsFile,
    DependenciesFile,
    ExclusionKind,
    ServicesFile,
    SystemScope,
)
from ens_evidence.scope.secrets import find_secrets
from ens_evidence.workspace import NotFound, NotText, WorkspaceStore

ASSETS_KEY = "scope/assets.yaml"
SERVICES_KEY = "scope/services.yaml"
DEPENDENCIES_KEY = "scope/dependencies.yaml"
SYSTEM_KEY = "scope/system.yaml"

_FILES: tuple[tuple[str, type[BaseModel]], ...] = (
    (ASSETS_KEY, AssetsFile),
    (SERVICES_KEY, ServicesFile),
    (DEPENDENCIES_KEY, DependenciesFile),
    (SYSTEM_KEY, SystemScope),
)


@dataclass(frozen=True)
class Inventory:
    assets: AssetsFile
    services: ServicesFile
    dependencies: DependenciesFile
    system: SystemScope


@dataclass(frozen=True)
class LoadResult:
    inventory: Inventory | None
    errors: list[str]


def load_inventory(store: WorkspaceStore) -> LoadResult:
    """Lee y valida los cuatro ficheros; agrega todos los errores sin interrumpirse."""
    errors: list[str] = []
    models: list[BaseModel] = []
    for key, model in _FILES:
        parsed = _load_file(store, key, model, errors)
        if parsed is not None:
            models.append(parsed)
    if len(models) < len(_FILES):
        return LoadResult(None, errors)
    assets, services, dependencies, system = models
    inventory = Inventory(assets, services, dependencies, system)  # type: ignore[arg-type]
    errors.extend(check_integrity(inventory))
    return LoadResult(None if errors else inventory, errors)


def load_assets(store: WorkspaceStore) -> tuple[AssetsFile | None, list[str]]:
    """Solo el inventario de activos: basta para reconciliar sin alcance completo."""
    errors: list[str] = []
    assets = _load_file(store, ASSETS_KEY, AssetsFile, errors)
    assert assets is None or isinstance(assets, AssetsFile)
    return assets, errors


def _load_file(
    store: WorkspaceStore, key: str, model: type[BaseModel], errors: list[str]
) -> BaseModel | None:
    try:
        text = store.read_text(key)
    except NotFound:
        errors.append(f"{key}: no existe; créalo (ens-evidence init lo crea vacío)")
        return None
    except NotText:
        errors.append(f"{key}: no es texto UTF-8; guárdalo como UTF-8")
        return None
    try:
        raw: Any = yaml.safe_load(text)
    except yaml.YAMLError:
        errors.append(f"{key}: no es YAML válido; revisa la sintaxis")
        return None
    leaked = find_secrets(raw)
    if leaked:
        errors.extend(
            f"{key}{SEPARATOR}{path}: parece un secreto; quítalo del fichero" for path in leaked
        )
        return None
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        errors.extend(describe_validation(key, exc, raw))
        return None


_KINDS = {"asset": "el activo", "service": "el servicio"}


def _unknown(kind: str, ref: str) -> str:
    return f"{kind} {ref} no existe; añádelo o corrige la referencia"


def _duplicates(ids: Iterable[str]) -> list[str]:
    return sorted(id_ for id_, count in Counter(ids).items() if count > 1)


def check_integrity(inventory: Inventory) -> list[str]:
    """IDs únicos, referencias resolubles, inventario no vacío y alcance completo."""
    errors: list[str] = []
    assets = inventory.assets.assets
    services = inventory.services.services
    asset_ids = {asset.id for asset in assets}
    service_ids = {service.id for service in services}

    if not assets:
        errors.append(f"{ASSETS_KEY}: el inventario está vacío; añade al menos un activo")
    for key, ids in (
        (ASSETS_KEY, [a.id for a in assets]),
        (SERVICES_KEY, [s.id for s in services]),
        (DEPENDENCIES_KEY, [d.id for d in inventory.dependencies.dependencies]),
    ):
        errors.extend(
            f"{key}: id repetido {id_}; cada id debe ser único" for id_ in _duplicates(ids)
        )
    hostnames = [a.hostname.strip().lower().rstrip(".") for a in assets if a.hostname]
    errors.extend(
        f"{ASSETS_KEY}: hostname repetido {name}; cada activo necesita el suyo"
        for name in _duplicates(hostnames)
    )

    for service in services:
        errors.extend(
            f"{SERVICES_KEY}{SEPARATOR}{service.id}{SEPARATOR}assets: {_unknown('el activo', ref)}"
            for ref in service.assets
            if ref not in asset_ids
        )
    for dependency in inventory.dependencies.dependencies:
        errors.extend(
            f"{DEPENDENCIES_KEY}{SEPARATOR}{dependency.id}: {_unknown('el activo', ref)}"
            for ref in (dependency.asset, dependency.depends_on)
            if ref not in asset_ids
        )
    errors.extend(_check_scope(inventory.system, asset_ids, service_ids))
    return errors


def _check_scope(system: SystemScope, asset_ids: set[str], service_ids: set[str]) -> list[str]:
    errors: list[str] = []
    excluded_assets = {e.ref for e in system.exclusions if e.kind is ExclusionKind.ASSET}
    excluded_services = {e.ref for e in system.exclusions if e.kind is ExclusionKind.SERVICE}
    for kind, known, included, excluded in (
        ("asset", asset_ids, system.included_assets, excluded_assets),
        ("service", service_ids, system.included_services, excluded_services),
    ):
        for ref in sorted((set(included) | excluded) - known):
            errors.append(f"{SYSTEM_KEY}: {_unknown(_KINDS[kind], ref)}")
        for ref in sorted(known - set(included) - excluded):
            errors.append(
                f"{SYSTEM_KEY}: {_KINDS[kind]} {ref} no está incluido ni excluido; decide uno"
            )
        for ref in sorted(set(included) & excluded):
            errors.append(
                f"{SYSTEM_KEY}: {_KINDS[kind]} {ref} está incluido y excluido; deja solo uno"
            )
    return errors


def fingerprint(inventory: Inventory) -> str:
    """sha256 del JSON canónico de assets, services, dependencies y system, en ese orden."""
    payload = [
        model.model_dump(mode="json")
        for model in (
            inventory.assets,
            inventory.services,
            inventory.dependencies,
            inventory.system,
        )
    ]
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
