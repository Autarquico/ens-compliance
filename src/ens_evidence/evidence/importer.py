"""Importación de evidencias: artefacto direccionado por contenido y después manifiesto."""

import json
from collections.abc import Callable
from datetime import datetime

from ens_evidence.evidence.layout import artifact_key, manifest_key, new_evidence_id, sha256_hex
from ens_evidence.evidence.manifests import list_manifests, render_manifest
from ens_evidence.models.contracts import (
    Artifact,
    CollectionResult,
    CollectorInfo,
    EvidenceManifest,
    Provenance,
    ProvenanceKind,
)
from ens_evidence.workspace import AlreadyExists, WorkspaceError, WorkspaceStore

MANUAL_SOURCE = "manual"
INCIDENT_RECORD_SOURCE = "incident-record"
INCIDENT_HISTORY_SOURCE = "incident-history"
# Las genera la herramienta a partir de sus propios registros: `evidence import` no las admite.
GENERATED_SOURCES = frozenset({INCIDENT_RECORD_SOURCE, INCIDENT_HISTORY_SOURCE})
ID_ATTEMPTS = 5
# Da el id de cada manifiesto nuevo, también en los reintentos por colisión.
IdFactory = Callable[[datetime], str]


class EvidenceImportError(WorkspaceError):
    """No se pudo importar la evidencia; el mensaje es apto para el usuario."""


def import_manual(
    store: WorkspaceStore,
    data: bytes,
    *,
    asset_id: str,
    submitted_by: str,
    now: datetime,
    collected_at: datetime | None = None,
    valid_until: datetime | None = None,
    id_factory: IdFactory = new_evidence_id,
) -> EvidenceManifest:
    """Registra un fichero aportado por una persona; `ValidationError` si los datos no valen."""
    sha256 = sha256_hex(data)
    manifest = EvidenceManifest(
        id=id_factory(now),
        source=MANUAL_SOURCE,
        asset_id=asset_id,
        collected_at=collected_at or now,
        valid_until=valid_until,
        artifact=Artifact(storage="workspace", key=artifact_key(sha256), sha256=sha256),
        provenance=Provenance(
            kind=ProvenanceKind.MANUAL, submitted_by=submitted_by, submitted_at=now
        ),
    )
    _store_artifact(store, manifest.artifact, data)
    return _store_manifest(store, manifest, now, id_factory)


def import_generated(
    store: WorkspaceStore,
    data: bytes,
    *,
    source: str,
    asset_id: str,
    submitted_by: str,
    now: datetime,
    collected_at: datetime,
    id_factory: IdFactory = new_evidence_id,
) -> EvidenceManifest:
    """Registra bytes que la propia herramienta generó (`GENERATED_SOURCES`), con procedencia manual
    y sin caducidad; `ValidationError` si los datos no valen."""
    if source not in GENERATED_SOURCES:
        raise ValueError(f"{source} no es una fuente generada por la herramienta")
    sha256 = sha256_hex(data)
    manifest = EvidenceManifest(
        id=id_factory(now),
        source=source,
        asset_id=asset_id,
        collected_at=collected_at,
        artifact=Artifact(storage="workspace", key=artifact_key(sha256), sha256=sha256),
        provenance=Provenance(
            kind=ProvenanceKind.MANUAL, submitted_by=submitted_by, submitted_at=now
        ),
    )
    _store_artifact(store, manifest.artifact, data)
    return _store_manifest(store, manifest, now, id_factory)


def import_collected(
    store: WorkspaceStore,
    result: CollectionResult,
    *,
    collector_version: str,
    now: datetime,
    id_factory: IdFactory = new_evidence_id,
) -> EvidenceManifest:
    """Guarda una ejecución de collector (también `ERROR`) como JSON canónico y su manifiesto."""
    data = canonical_json(result)
    sha256 = sha256_hex(data)
    manifest = EvidenceManifest(
        id=id_factory(now),
        source=result.source,
        asset_id=result.asset_id,
        collected_at=result.collected_at,
        artifact=Artifact(storage="workspace", key=artifact_key(sha256), sha256=sha256),
        provenance=Provenance(
            kind=ProvenanceKind.COLLECTOR,
            submitted_by=f"collector:{result.collector}",
            submitted_at=now,
        ),
        collector=CollectorInfo(name=result.collector, version=collector_version),
    )
    _store_artifact(store, manifest.artifact, data)
    return _store_manifest(store, manifest, now, id_factory)


def import_attested_package(
    store: WorkspaceStore,
    package: bytes,
    result: CollectionResult,
    *,
    package_source: str,
    collector_version: str,
    submitted_by: str,
    valid_until: datetime,
    now: datetime,
    id_factory: IdFactory = new_evidence_id,
) -> tuple[EvidenceManifest, EvidenceManifest]:
    """Guarda un paquete aportado a mano y su `CollectionResult` normalizado.

    Dos manifiestos con `provenance: manual`, mismo `collected_at` y `valid_until`:
    el paquete original (para que `evidence verify` no lo vea huérfano) y el
    resultado normalizado, que es el que leen los checks. Reimportar el mismo
    paquete para el mismo activo se rechaza y nombra la evidencia existente.
    """
    _reject_reimport(store, package, package_source, result.asset_id)
    kinds = ((package_source, package), (result.source, canonical_json(result)))
    manifests = []
    for source, data in kinds:
        sha256 = sha256_hex(data)
        manifest = EvidenceManifest(
            id=id_factory(now),
            source=source,
            asset_id=result.asset_id,
            collected_at=result.collected_at,
            valid_until=valid_until,
            artifact=Artifact(storage="workspace", key=artifact_key(sha256), sha256=sha256),
            provenance=Provenance(
                kind=ProvenanceKind.MANUAL, submitted_by=submitted_by, submitted_at=now
            ),
            collector=CollectorInfo(name=result.collector, version=collector_version),
        )
        manifests.append((manifest, data))
    for manifest, data in manifests:
        _store_artifact(store, manifest.artifact, data)
    return (
        _store_manifest(store, manifests[0][0], now, id_factory),
        _store_manifest(store, manifests[1][0], now, id_factory),
    )


def _reject_reimport(store: WorkspaceStore, package: bytes, source: str, asset_id: str) -> None:
    sha256 = sha256_hex(package)
    for existing in list_manifests(store).manifests:
        if (existing.source, existing.asset_id, existing.artifact.sha256) == (
            source,
            asset_id,
            sha256,
        ):
            raise EvidenceImportError(
                f"este paquete ya estaba importado como {existing.id}; no se registra otra vez."
            )


def canonical_json(result: CollectionResult) -> bytes:
    document = result.model_dump(mode="json")
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _store_artifact(store: WorkspaceStore, artifact: Artifact, data: bytes) -> None:
    try:
        store.create_bytes(artifact.key, data)
    except AlreadyExists:
        if sha256_hex(store.read_bytes(artifact.key)) != artifact.sha256:
            raise EvidenceImportError(
                f"{artifact.key} existe pero su contenido no coincide con su hash; "
                "no se importa nada. Ejecuta `evidence verify --all`."
            ) from None


def _store_manifest(
    store: WorkspaceStore, manifest: EvidenceManifest, now: datetime, id_factory: IdFactory
) -> EvidenceManifest:
    for _ in range(ID_ATTEMPTS):
        try:
            store.create_bytes(manifest_key(manifest.id), render_manifest(manifest))
            return manifest
        except AlreadyExists:
            manifest = manifest.model_copy(update={"id": id_factory(now)})
    raise EvidenceImportError(
        "no se pudo reservar un identificador de evidencia libre; "
        "el artefacto queda sin manifiesto (huérfano). Reintenta."
    )
