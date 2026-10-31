"""Evidencia de la matriz (manual o historial de incidente) y contrato común de lo que sustenta.

Cada fuente (manual, historial de incidente, técnica, documental) devuelve por enlace un
`LinkSupport`: las unidades de cobertura que sustenta de forma válida en el instante `at`, o los
bloqueos que lo impiden. Solo cuentan los manifiestos íntegros (`verify_one`); la ausencia de
evidencia nunca es cumplimiento.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta

import yaml

from ens_evidence.approvals import safe_load_unique
from ens_evidence.evidence.importer import INCIDENT_HISTORY_SOURCE, MANUAL_SOURCE
from ens_evidence.evidence.layout import manifest_key
from ens_evidence.evidence.manifests import ManifestError, load_manifest_by_id
from ens_evidence.evidence.verify import verify_one
from ens_evidence.incidents import check_history
from ens_evidence.matrix.model import (
    Link,
    ManualEvidenceLink,
    ReviewedDocument,
    ReviewedEvidence,
)
from ens_evidence.matrix.obligations import MatrixRowSpec, expand_covers
from ens_evidence.matrix.policy import SourcePolicy
from ens_evidence.models.contracts import EvidenceManifest, ProvenanceKind
from ens_evidence.models.readiness import Blocker, BlockerKind, EvidenceRef
from ens_evidence.workspace import NotFound, NotText, WorkspaceStore


@dataclass(frozen=True)
class LinkSupport:
    """Lo que un enlace sustenta en `at`. `clauses` está vacío si hay algún bloqueo.

    `available_at` es cuándo existe la fuente (alta de la evidencia, fin de la ejecución o
    aprobación del documento): la evaluación la compara con el registro de la revisión.
    `simulated`: el historial de un incidente es de un simulacro.
    """

    link: Link
    clauses: frozenset[str] = frozenset()
    blockers: tuple[Blocker, ...] = ()
    evidence: tuple[EvidenceRef, ...] = ()
    reviewed_evidence: tuple[ReviewedEvidence, ...] = ()
    reviewed_document: ReviewedDocument | None = None
    available_at: datetime | None = None
    simulated: bool = False

    @property
    def counts(self) -> bool:
        return bool(self.clauses) and not self.blockers


def blocker(kind: BlockerKind, row: MatrixRowSpec, detail: str) -> Blocker:
    return Blocker(kind=kind, measure_id=row.measure_id, detail=detail)


def link_units(row: MatrixRowSpec, link: Link) -> tuple[frozenset[str], tuple[Blocker, ...]]:
    """Unidades de cobertura del enlace y un bloqueo por cada `covers` ajeno a la medida."""
    expansion = expand_covers(link.covers, row)
    blockers = tuple(blocker(BlockerKind.MISSING_EVIDENCE, row, e) for e in expansion.errors)
    return expansion.units, blockers


def manifest_sha256(manifest: EvidenceManifest) -> str:
    """Huella del manifiesto completo (procedencia, vigencia y artefacto): si algo cambia, la
    revisión que lo vio ya no describe lo que hay."""
    canonical = json.dumps(
        manifest.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _repeats_a_key(store: WorkspaceStore, evidence_id: str) -> bool:
    """Un manifiesto con una clave repetida (p. ej. dos `collected_at`) es ambiguo: el cargador
    de evidencias se queda con la última."""
    try:
        safe_load_unique(store.read_text(manifest_key(evidence_id)))
    except yaml.YAMLError:
        return True
    except (NotFound, NotText):
        return False
    return False


def verified_manifest(store: WorkspaceStore, evidence_id: str) -> EvidenceManifest | None:
    """El manifiesto si él y su artefacto están íntegros y su alta coincide con su id; None si
    algo no cuadra."""
    if not verify_one(store, evidence_id).ok or _repeats_a_key(store, evidence_id):
        return None
    try:
        manifest = load_manifest_by_id(store, evidence_id)
    except ManifestError:
        return None
    # El id codifica el día del alta (`EV-AAAAMMDD-…`): retrofechar `submitted_at` para que una
    # evidencia nueva parezca anterior a una revisión no coincide con él.
    if evidence_id[3:11] != manifest.provenance.submitted_at.strftime("%Y%m%d"):
        return None
    return manifest


def evidence_source(store: WorkspaceStore, link: Link) -> str | None:
    """El `source` del manifiesto que cita el enlace; None si no es una evidencia o no se lee."""
    if not isinstance(link, ManualEvidenceLink):
        return None
    try:
        return load_manifest_by_id(store, link.evidence_id).source
    except ManifestError:
        return None


def policy_rejection(row: MatrixRowSpec, link: Link, policy: SourcePolicy) -> LinkSupport:
    """El enlace no es una fuente que la política de la medida admita."""
    _, cover_blockers = link_units(row, link)
    detail = blocker(BlockerKind.MISSING_EVIDENCE, row, policy.detail)
    return LinkSupport(link, blockers=(*cover_blockers, detail))


LINKABLE_SOURCES = frozenset({MANUAL_SOURCE, INCIDENT_HISTORY_SOURCE})


def is_linkable(manifest: EvidenceManifest) -> bool:
    """Puede enlazarse y contar: evidencia manual o historial de incidente, ambos de procedencia
    manual. Una medida con política de fuentes admite además solo la suya."""
    return manifest.provenance.kind is ProvenanceKind.MANUAL and manifest.source in LINKABLE_SOURCES


def manual_support(
    store: WorkspaceStore,
    row: MatrixRowSpec,
    link: ManualEvidenceLink,
    at: datetime,
    review_every_days: int | None,
) -> LinkSupport:
    """Evidencia manual o historial de incidente (`incident-history`, verificado en vivo con los
    registros): ambas con las mismas reglas temporales. Un `incident-record` nunca cuenta."""
    units, cover_blockers = link_units(row, link)
    simulated = False

    def rejected(kind: BlockerKind, detail: str) -> LinkSupport:
        return LinkSupport(
            link, blockers=(*cover_blockers, blocker(kind, row, detail)), simulated=simulated
        )

    evidence_id = link.evidence_id
    manifest = verified_manifest(store, evidence_id)
    if manifest is None or not is_linkable(manifest):
        return rejected(BlockerKind.MISSING_EVIDENCE, f"evidence_unverified:{evidence_id}")
    if manifest.source == INCIDENT_HISTORY_SOURCE:
        history = check_history(store, manifest)
        simulated = history.simulated
        if history.problems:
            return rejected(BlockerKind.MISSING_EVIDENCE, f"incident_history_broken:{evidence_id}")
    if manifest.provenance.submitted_at > at or manifest.collected_at > at:
        return rejected(BlockerKind.MISSING_EVIDENCE, f"evidence_in_future:{evidence_id}")
    if manifest.valid_until is not None:
        if manifest.valid_until <= at:
            return rejected(BlockerKind.EXPIRED_EVIDENCE, f"evidence_expired:{evidence_id}")
    elif review_every_days is None:
        return rejected(BlockerKind.MISSING_EVIDENCE, f"evidence_no_validity:{evidence_id}")
    elif manifest.collected_at < at - timedelta(days=review_every_days):
        return rejected(BlockerKind.EXPIRED_EVIDENCE, f"evidence_expired:{evidence_id}")
    return LinkSupport(
        link,
        clauses=units if not cover_blockers else frozenset(),
        blockers=cover_blockers,
        evidence=(
            EvidenceRef(
                id=evidence_id,
                valid_until=manifest.valid_until,
                collected_at=manifest.collected_at,
            ),
        ),
        reviewed_evidence=(
            ReviewedEvidence(
                evidence_id=evidence_id,
                artifact_sha256=manifest.artifact.sha256,
                manifest_sha256=manifest_sha256(manifest),
            ),
        ),
        available_at=manifest.provenance.submitted_at,
        simulated=simulated,
    )
