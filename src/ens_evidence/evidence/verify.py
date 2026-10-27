"""Verificación de integridad de las evidencias."""

from dataclasses import dataclass

from ens_evidence.evidence.layout import (
    RAW_PREFIX,
    is_evidence_id,
    manifest_key,
    sha256_hex,
)
from ens_evidence.evidence.manifests import ManifestError, load_manifest, manifest_keys
from ens_evidence.workspace import NotFound, WorkspaceStore


@dataclass(frozen=True)
class VerifyReport:
    checked: int
    problems: list[str]

    @property
    def ok(self) -> bool:
        return not self.problems


def verify_one(store: WorkspaceStore, evidence_id: str) -> VerifyReport:
    if not is_evidence_id(evidence_id):
        return VerifyReport(
            0, [f"{evidence_id}: no es un identificador de evidencia (EV-AAAAMMDD-xxxxxxxx)"]
        )
    return VerifyReport(1, _check_manifest(store, manifest_key(evidence_id))[0])


def verify_all(store: WorkspaceStore) -> VerifyReport:
    problems: list[str] = []
    referenced: set[str] = set()
    keys = manifest_keys(store)
    for key in keys:
        found, artifact_key = _check_manifest(store, key)
        problems += found
        if artifact_key is not None:
            referenced.add(artifact_key)
    problems += [
        f"{key}: artefacto huérfano, ningún manifiesto lo referencia"
        for key in store.list(RAW_PREFIX)
        if key not in referenced
    ]
    return VerifyReport(len(keys), problems)


def _check_manifest(store: WorkspaceStore, key: str) -> tuple[list[str], str | None]:
    """Problemas del manifiesto en `key` y la clave del artefacto que referencia, si es legible."""
    try:
        manifest = load_manifest(store, key)
    except ManifestError as exc:
        return exc.errors, None
    artifact = manifest.artifact
    if manifest_key(manifest.id) != key:
        return [
            f"{key}: el id {manifest.id} no coincide con el nombre del manifiesto"
        ], artifact.key
    try:
        data = store.read_bytes(artifact.key)
    except NotFound:
        return [f"{manifest.id}: falta el artefacto {artifact.key}"], artifact.key
    actual = sha256_hex(data)
    if actual != artifact.sha256:
        return [
            f"{manifest.id}: el artefacto ha cambiado; "
            f"hash esperado {artifact.sha256}, obtenido {actual}"
        ], artifact.key
    return [], artifact.key
