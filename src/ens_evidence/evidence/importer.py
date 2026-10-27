"""Importación de evidencias: artefacto direccionado por contenido y después manifiesto."""

from datetime import datetime

from ens_evidence.evidence.layout import artifact_key, manifest_key, new_evidence_id, sha256_hex
from ens_evidence.evidence.manifests import render_manifest
from ens_evidence.models.contracts import Artifact, EvidenceManifest, Provenance, ProvenanceKind
from ens_evidence.workspace import AlreadyExists, WorkspaceError, WorkspaceStore

MANUAL_SOURCE = "manual"
ID_ATTEMPTS = 5


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
) -> EvidenceManifest:
    """Registra un fichero aportado por una persona; `ValidationError` si los datos no valen."""
    sha256 = sha256_hex(data)
    manifest = EvidenceManifest(
        id=new_evidence_id(now),
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
    return _store_manifest(store, manifest, now)


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
    store: WorkspaceStore, manifest: EvidenceManifest, now: datetime
) -> EvidenceManifest:
    for _ in range(ID_ATTEMPTS):
        try:
            store.create_bytes(manifest_key(manifest.id), render_manifest(manifest))
            return manifest
        except AlreadyExists:
            manifest = manifest.model_copy(update={"id": new_evidence_id(now)})
    raise EvidenceImportError(
        "no se pudo reservar un identificador de evidencia libre; "
        "el artefacto queda sin manifiesto (huérfano). Reintenta."
    )
