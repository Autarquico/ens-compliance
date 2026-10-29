"""Evidencia de endpoints lista para evaluar: resultado normalizado más su vigencia."""

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from pydantic import AwareDatetime

from ens_evidence.endpoint.normalize import SOURCE
from ens_evidence.evidence import list_manifests
from ens_evidence.evidence.layout import sha256_hex
from ens_evidence.models.contracts import CollectionResult, EvidenceManifest
from ens_evidence.models.readiness import EvidenceRef
from ens_evidence.workspace import WorkspaceError, WorkspaceStore


def expires_at(
    collected_at: datetime, valid_until: datetime, max_evidence_age_days: int
) -> datetime:
    """Caduca con lo primero entre el manifiesto y el umbral vigente de la configuración."""
    return min(valid_until, collected_at + timedelta(days=max_evidence_age_days))


@dataclass(frozen=True)
class HostEvidence:
    evidence_id: str
    valid_until: AwareDatetime
    result: CollectionResult

    @property
    def asset_id(self) -> str:
        return self.result.asset_id

    @property
    def collected_at(self) -> AwareDatetime:
        return self.result.collected_at

    def expires_at(self, max_evidence_age_days: int) -> datetime:
        return expires_at(self.collected_at, self.valid_until, max_evidence_age_days)


def latest_by_host(evidence: Iterable[HostEvidence]) -> dict[str, HostEvidence]:
    """Última ejecución de cada host: la de mayor `collected_at`, sin volver a una anterior."""
    latest: dict[str, HostEvidence] = {}
    for item in sorted(
        evidence, key=lambda candidate: (candidate.collected_at, candidate.evidence_id)
    ):
        latest[item.asset_id] = item
    return latest


@dataclass(frozen=True)
class LoadedEvidence:
    """Evidencia legible más los motivos por los que algo no se pudo leer.

    Con `unreadable` no vacío no se sabe cuál es la última ejecución de cada host.
    """

    items: list[HostEvidence] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)


def load_host_evidence(store: WorkspaceStore) -> LoadedEvidence:
    """Los resultados normalizados `endpoint-script` del workspace, listos para los checks."""
    listing = list_manifests(store)
    items: list[HostEvidence] = []
    unreadable = list(listing.errors)
    for manifest in listing.manifests:
        if manifest.source != SOURCE:
            continue
        try:
            items.append(_read(store, manifest))
        except (WorkspaceError, ValueError) as exc:
            unreadable.append(f"{manifest.id}: artefacto ilegible ({type(exc).__name__})")
    return LoadedEvidence(items, unreadable)


def _read(store: WorkspaceStore, manifest: EvidenceManifest) -> HostEvidence:
    raw = store.read_bytes(manifest.artifact.key)
    if sha256_hex(raw) != manifest.artifact.sha256:
        raise ValueError("el artefacto no coincide con su sha256")
    result = CollectionResult.model_validate(json.loads(raw))
    return HostEvidence(manifest.id, manifest.valid_until, result)


def evidence_refs(
    manifests: Iterable[EvidenceManifest], max_evidence_age_days: int
) -> dict[str, list[EvidenceRef]]:
    """`EvidenceRef` de la última ejecución de cada activo, con el umbral vigente de los checks.

    Las ejecuciones anteriores siguen en el workspace para auditoría, pero no bloquean la
    preparación una vez sustituidas por otra más reciente.
    """
    latest: dict[str, EvidenceManifest] = {}
    for manifest in sorted(
        (m for m in manifests if m.source == SOURCE), key=lambda m: (m.collected_at, m.id)
    ):
        latest[manifest.asset_id] = manifest
    return {
        asset_id: [
            EvidenceRef(
                id=manifest.id,
                valid_until=expires_at(
                    manifest.collected_at, manifest.valid_until, max_evidence_age_days
                ),
                collected_at=manifest.collected_at,
            )
        ]
        for asset_id, manifest in latest.items()
    }
