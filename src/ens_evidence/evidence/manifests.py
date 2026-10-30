"""Lectura y escritura de manifiestos de evidencia."""

from dataclasses import dataclass, field

import yaml
from pydantic import ValidationError

from ens_evidence.evidence.layout import MANIFESTS_PREFIX, manifest_key
from ens_evidence.models.contracts import EvidenceManifest
from ens_evidence.scope.messages import describe_validation
from ens_evidence.workspace import NotFound, NotText, WorkspaceStore
from ens_evidence.yaml_unique import safe_load_unique


class ManifestError(Exception):
    """El manifiesto no existe o no es válido; `errors` está listo para mostrar."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass(frozen=True)
class ManifestListing:
    manifests: list[EvidenceManifest] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def render_manifest(manifest: EvidenceManifest) -> bytes:
    data = manifest.model_dump(mode="json", exclude_none=True)
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True).encode("utf-8")


def load_manifest(store: WorkspaceStore, key: str) -> EvidenceManifest:
    try:
        raw = safe_load_unique(store.read_text(key))
    except NotFound:
        raise ManifestError([f"{key}: el manifiesto no existe"]) from None
    except NotText:
        raise ManifestError([f"{key}: el manifiesto no es texto UTF-8"]) from None
    except yaml.YAMLError:
        raise ManifestError([f"{key}: YAML no válido; corrige la sintaxis"]) from None
    try:
        return EvidenceManifest.model_validate(raw)
    except ValidationError as exc:
        raise ManifestError(describe_validation(key, exc, raw)) from None


def load_manifest_by_id(store: WorkspaceStore, evidence_id: str) -> EvidenceManifest:
    return load_manifest(store, manifest_key(evidence_id))


def manifest_keys(store: WorkspaceStore) -> list[str]:
    return store.list(MANIFESTS_PREFIX)


def list_manifests(store: WorkspaceStore) -> ManifestListing:
    manifests: list[EvidenceManifest] = []
    errors: list[str] = []
    for key in manifest_keys(store):
        try:
            manifests.append(load_manifest(store, key))
        except ManifestError as exc:
            errors.extend(exc.errors)
    return ManifestListing(manifests, errors)
