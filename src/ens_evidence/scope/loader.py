"""Carga tipada de los cuatro documentos del alcance, cada uno con sus propios errores."""

from dataclasses import dataclass, field

from ens_evidence.scope.inventory import (
    ASSETS_KEY,
    DEPENDENCIES_KEY,
    SERVICES_KEY,
    SYSTEM_KEY,
    Inventory,
    _load_file,
    check_integrity,
)
from ens_evidence.scope.models import AssetsFile, DependenciesFile, ServicesFile, SystemScope
from ens_evidence.workspace import WorkspaceStore


@dataclass(frozen=True)
class Document[M]:
    """Un fichero: su modelo si es válido, o los errores que lo impiden (y si falta)."""

    model: M | None
    errors: list[str] = field(default_factory=list)
    absent: bool = False


@dataclass(frozen=True)
class ScopeDocuments:
    assets: Document[AssetsFile]
    system: Document[SystemScope]
    services: Document[ServicesFile]
    dependencies: Document[DependenciesFile]
    integrity_errors: list[str]

    @property
    def document_errors(self) -> list[str]:
        return [
            *self.assets.errors,
            *self.system.errors,
            *self.services.errors,
            *self.dependencies.errors,
        ]


def _document[M](store: WorkspaceStore, key: str, model: type[M]) -> Document[M]:
    errors: list[str] = []
    parsed = _load_file(store, key, model, errors)  # type: ignore[arg-type]
    return Document(parsed, errors, absent=parsed is None and not store.exists(key))  # type: ignore[arg-type]


def load_documents(store: WorkspaceStore) -> ScopeDocuments:
    """Lee cada documento por separado; la integridad solo se calcula si los cuatro son válidos."""
    assets = _document(store, ASSETS_KEY, AssetsFile)
    services = _document(store, SERVICES_KEY, ServicesFile)
    dependencies = _document(store, DEPENDENCIES_KEY, DependenciesFile)
    system = _document(store, SYSTEM_KEY, SystemScope)
    integrity: list[str] = []
    if assets.model and services.model and dependencies.model and system.model:
        integrity = check_integrity(
            Inventory(assets.model, services.model, dependencies.model, system.model)
        )
    return ScopeDocuments(assets, system, services, dependencies, integrity)
