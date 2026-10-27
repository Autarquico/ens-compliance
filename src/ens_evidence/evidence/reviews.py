"""Revisiones añadidas a una evidencia; el manifiesto nunca se modifica."""

from datetime import UTC

import yaml

from ens_evidence.evidence.layout import is_evidence_id, manifest_key, reviews_prefix
from ens_evidence.models.contracts import Review
from ens_evidence.workspace import WorkspaceError, WorkspaceStore


def add_review(store: WorkspaceStore, evidence_id: str, review: Review) -> str:
    """Escribe la revisión como fichero nuevo y devuelve su clave."""
    if not is_evidence_id(evidence_id) or not store.exists(manifest_key(evidence_id)):
        raise WorkspaceError("la evidencia no existe; no se puede añadir la revisión")
    stamp = review.reviewed_at.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    key = f"{reviews_prefix(evidence_id)}/{stamp}.yaml"
    text = yaml.safe_dump(review.model_dump(mode="json"), sort_keys=False, allow_unicode=True)
    store.create_bytes(key, text.encode("utf-8"))
    return key
