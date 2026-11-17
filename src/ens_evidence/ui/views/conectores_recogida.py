"""Última recogida importada de cada conector, proyectada a categorías fijas.

Solo lee: un `list_manifests` por petición (índice por nombre de conector) y, por conector, su
artefacto con las defensas de `/copias`: clave canónica, lectura acotada, sha256, validación
estricta de `CollectionResult` e identidad. Los DTO llevan solo etiquetas fijas, nombres de sección
validados y estados del enum: nunca `data.error`, `Section.reason` ni texto derivado de un fichero.
"""

import hashlib
import json
import re
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ValidationError

from ens_evidence.connectors.passwords.checks import _NOT_COLLECTED as PASSWORD_LABELS
from ens_evidence.connectors.sections import worst
from ens_evidence.evidence import list_manifests
from ens_evidence.evidence.layout import artifact_key
from ens_evidence.models.contracts import CollectionResult, EvidenceManifest, ProvenanceKind
from ens_evidence.models.enums import CollectionStatus
from ens_evidence.scheduling.collect import GENERATED_BY, Phase
from ens_evidence.scope.secrets import find_secrets
from ens_evidence.ui.store import UiStore
from ens_evidence.ui.views import format_instant
from ens_evidence.workspace import WorkspaceError

# Los artefactos de github (repositorios) son mayores que los de copias (4 MiB).
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
MAX_SECTIONS = 100
SECTION_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
PASSWORDS = "passwords"
UNREADABLE_LABEL = "estado no legible"
NO_EXPORT_DETAIL = "no se recogió el export de usuarios"

Category = Literal[
    "credential_missing",
    "credential_rejected",
    "permission_denied",
    "not_supported",
    "connector_error",
]

CATEGORY_LABELS: dict[Category, str] = {
    "credential_missing": "credencial ausente",
    "credential_rejected": "credencial rechazada",
    "permission_denied": "sin permiso",
    "not_supported": "no soportado",
    "connector_error": "error del conector",
}
STATUS_LABELS = {
    CollectionStatus.SUCCESS: "correcta",
    CollectionStatus.ERROR: "con error",
    CollectionStatus.PERMISSION_DENIED: "sin permiso",
    CollectionStatus.NOT_SUPPORTED: "no soportado",
}
_STATUS_CATEGORY: dict[CollectionStatus, Category] = {
    CollectionStatus.PERMISSION_DENIED: "permission_denied",
    CollectionStatus.NOT_SUPPORTED: "not_supported",
    CollectionStatus.ERROR: "connector_error",
}
PHASE_LABELS = {
    Phase.PLUGIN_LOAD.value: "no se pudo cargar el conector",
    Phase.CONFIG.value: "la configuración no es válida",
    Phase.PREPARE.value: "falló la preparación de la recogida",
    Phase.COLLECT.value: "falló la recogida",
    Phase.TIMEOUT.value: "la recogida superó el tiempo máximo",
    Phase.RESULT_REJECTED.value: "se descartó el resultado de la recogida",
}
CRASHED_DETAIL = "el conector falló de forma inesperada"
SECRET_DETAIL = "la recogida se descartó porque el resultado contenía un secreto"
IDENTITY_DETAIL = "la recogida se descartó porque no correspondía al conector"
GENERIC_SCHEDULER_DETAIL = "la recogida no llegó a ejecutarse"

_MISSING_CREDENTIAL = (
    re.compile(r"^ENS_[A-Z0-9_]+ no está definida$"),
    re.compile(r"^falta la variable de entorno [A-Z][A-Z0-9_]*$"),
)
_REJECTED_CREDENTIAL = (
    re.compile(r"^ENS_[A-Z0-9_]+ contiene caracteres no válidos$"),
    re.compile(r"^HTTP 401"),
    re.compile(r"^el token no"),
    re.compile(r"^la clave de la cuenta de servicio"),
    re.compile(r"^contraseña del repositorio incorrecta"),
)
_PERMISSION = (re.compile(r"^HTTP 403"), re.compile(r"^HTTP 404"))
_CRASHED = re.compile(r"^[a-z_]+_crashed:")
_SECTION_MISSING_CREDENTIAL = re.compile(r"no está definida")


# --- DTO -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SectionRow:
    name: str
    status: str  # valor de `CollectionStatus`


@dataclass(frozen=True)
class Failure:
    category: Category
    label: str
    detail: str | None  # etiqueta fija de fase o de código; nunca el texto en bruto


@dataclass(frozen=True)
class LastCollection:
    evidence_id: str
    collected_at: str
    status: str | None  # `None` si el artefacto no se pudo verificar
    status_label: str
    unreadable: bool
    partial: bool
    failure: Failure | None
    sections: tuple[SectionRow, ...]
    sections_unreadable: bool = False


@dataclass(frozen=True)
class RepositoryState:
    """Estado de un repositorio configurado de `backups` en la última recogida."""

    id: str
    status: str | None
    status_label: str
    failure: Failure | None


@dataclass(frozen=True)
class CollectionIndex:
    latest: Mapping[str, EvidenceManifest]
    unreadable_manifests: int


# --- índice y lectura verificada -----------------------------------------------------------------


def index_collections(store: UiStore) -> CollectionIndex:
    """El último manifiesto de recogida por conector; un solo `list_manifests` por petición."""
    listing = list_manifests(store)
    latest: dict[str, EvidenceManifest] = {}
    for manifest in listing.manifests:
        collector = manifest.collector
        if (
            manifest.provenance.kind is not ProvenanceKind.COLLECTOR
            or collector is None
            or collector.name != manifest.source
        ):
            continue
        current = latest.get(collector.name)
        if current is None or manifest.collected_at > current.collected_at:
            latest[collector.name] = manifest
    return CollectionIndex(latest, len(listing.errors))


def read_collection(
    store: UiStore, manifest: EvidenceManifest, name: str
) -> CollectionResult | None:
    """El artefacto verificado del manifiesto, o `None` ante cualquier duda."""
    artifact = manifest.artifact
    if artifact.storage != "workspace" or artifact.key != artifact_key(artifact.sha256):
        return None
    try:
        raw = store.read_bytes(artifact.key, MAX_ARTIFACT_BYTES + 1)
        if len(raw) > MAX_ARTIFACT_BYTES or hashlib.sha256(raw).hexdigest() != artifact.sha256:
            return None
        result = CollectionResult.model_validate(json.loads(raw))
    except (WorkspaceError, ValidationError, ValueError, RecursionError):
        return None
    if result.source != name or result.collector != name or result.asset_id != manifest.asset_id:
        return None
    return result


# --- secciones (D8) ------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Sections:
    rows: tuple[SectionRow, ...]
    unreadable: bool
    credential_missing: bool


def _parse_sections(data: Mapping[str, Any]) -> _Sections:
    raw = data.get("sections")
    if raw is None:
        return _Sections((), False, False)
    if not isinstance(raw, dict):
        return _Sections((), True, False)
    rows: list[SectionRow] = []
    unreadable = len(raw) > MAX_SECTIONS
    missing = False
    for name, section in sorted(raw.items(), key=lambda item: str(item[0]))[:MAX_SECTIONS]:
        status = _status_of(section)
        if not _valid_name(name) or status is None:
            unreadable = True
            continue
        rows.append(SectionRow(name, status.value))
        reason = section.get("reason")
        if (
            status is not CollectionStatus.SUCCESS
            and isinstance(reason, str)
            and _SECTION_MISSING_CREDENTIAL.search(reason)
        ):
            missing = True
    return _Sections(tuple(rows), unreadable, missing)


def _valid_name(name: Any) -> bool:
    """Con la forma de un nombre de sección y sin parecer un secreto (el patrón solo no basta)."""
    return (
        isinstance(name, str)
        and SECTION_NAME.fullmatch(name) is not None
        and not find_secrets({"name": name})
    )


def _status_of(value: Any) -> CollectionStatus | None:
    status = value.get("status") if isinstance(value, dict) else None
    try:
        return CollectionStatus(status) if isinstance(status, str) else None
    except ValueError:
        return None


# --- categorías (D4) -----------------------------------------------------------------------------


def _failure(category: Category, detail: str | None = None) -> Failure:
    return Failure(category, CATEGORY_LABELS[category], detail)


def _scheduler_failure(data: Mapping[str, Any]) -> Failure:
    error = data.get("error")
    phase = data.get("phase")
    error = error if isinstance(error, str) else ""
    if error.startswith("secret_detected:"):
        return _failure("connector_error", SECRET_DETAIL)
    if error.startswith("identity_mismatch:"):
        return _failure("connector_error", IDENTITY_DETAIL)
    if _CRASHED.match(error):
        return _failure("connector_error", CRASHED_DETAIL)
    detail = PHASE_LABELS.get(phase) if isinstance(phase, str) else None
    return _failure("connector_error", detail or GENERIC_SCHEDULER_DETAIL)


def _known_error(error: Any) -> Category | None:
    """La categoría de un `data.error` que es un literal conocido de los conectores."""
    if not isinstance(error, str):
        return None
    if any(p.match(error) for p in _MISSING_CREDENTIAL):
        return "credential_missing"
    if any(p.match(error) for p in _REJECTED_CREDENTIAL):
        return "credential_rejected"
    if any(p.match(error) for p in _PERMISSION):
        return "permission_denied"
    return None


def _error_failure(error: Any, status: CollectionStatus) -> Failure:
    """Literal conocido, o la categoría del estado, o «error del conector»."""
    return _failure(_known_error(error) or _STATUS_CATEGORY.get(status, "connector_error"))


def _failure_of(name: str, result: CollectionResult, sections: _Sections) -> Failure | None:
    data = result.data
    statuses = [CollectionStatus(row.status) for row in sections.rows]
    if result.collection_status is CollectionStatus.SUCCESS and worst(statuses) is (
        CollectionStatus.SUCCESS
    ):
        return None
    if data.get("generated_by") == GENERATED_BY:
        return _scheduler_failure(data)
    error = data.get("error")
    if name == PASSWORDS and isinstance(error, str) and error in PASSWORD_LABELS:
        return _failure("connector_error", PASSWORD_LABELS[error])
    if (known := _known_error(error)) is not None:
        return _failure(known)
    if sections.credential_missing:
        return _failure("credential_missing")
    status = worst(statuses) if statuses else result.collection_status
    if status is CollectionStatus.SUCCESS:
        status = result.collection_status
    if name == PASSWORDS and isinstance(error, str):
        return _failure("connector_error", NO_EXPORT_DETAIL)
    return _failure(_STATUS_CATEGORY.get(status, "connector_error"))


def categorize(name: str, result: CollectionResult) -> Failure | None:
    """El motivo del fallo en una categoría fija, o `None` si la recogida fue completa."""
    return _failure_of(name, result, _parse_sections(result.data))


# --- proyecciones --------------------------------------------------------------------------------


def last_collection(store: UiStore, manifest: EvidenceManifest, name: str) -> LastCollection:
    result = read_collection(store, manifest, name)
    when = format_instant(manifest.collected_at)
    if result is None:
        return LastCollection(manifest.id, when, None, UNREADABLE_LABEL, True, False, None, ())
    sections = _parse_sections(result.data)
    status = result.collection_status
    partial = status is CollectionStatus.SUCCESS and any(
        row.status != CollectionStatus.SUCCESS.value for row in sections.rows
    )
    return LastCollection(
        evidence_id=manifest.id,
        collected_at=when,
        status=status.value,
        status_label=STATUS_LABELS[status],
        unreadable=False,
        partial=partial,
        failure=_failure_of(name, result, sections),
        sections=sections.rows,
        sections_unreadable=sections.unreadable,
    )


def repository_states(
    store: UiStore, manifest: EvidenceManifest, configured_ids: Iterable[str]
) -> tuple[RepositoryState, ...]:
    """El estado por repositorio de `backups`, solo para los ids de la configuración.

    Un repositorio configurado sin entrada válida en la recogida sale sin estado; una entrada con
    un id que la configuración no tiene se ignora.
    """
    result = read_collection(store, manifest, "backups")
    entries = _entries_by_id(result, set(configured_ids)) if result else {}
    return tuple(_repository_state(rid, entries.get(rid)) for rid in configured_ids)


def _entries_by_id(result: CollectionResult, wanted: Collection[str]) -> dict[str, dict]:
    entries = result.data.get("repositories")
    found: dict[str, dict] = {}
    for entry in entries if isinstance(entries, list) else ():
        rid = entry.get("id") if isinstance(entry, dict) else None
        if isinstance(rid, str) and rid in wanted and rid not in found:
            found[rid] = entry
    return found


def _repository_state(rid: str, entry: dict | None) -> RepositoryState:
    status = _status_of({"status": entry.get("collection_status")}) if entry else None
    if entry is None or status is None:
        return RepositoryState(rid, None, UNREADABLE_LABEL, None)
    failure = None
    if status is not CollectionStatus.SUCCESS:
        failure = _error_failure(entry.get("error"), status)
    return RepositoryState(rid, status.value, STATUS_LABELS[status], failure)
