"""Contrato de los documentos organizativos como fuente de la matriz.

Un documento aprobado y vigente sustenta las cláusulas que cubre; aprobarlo nunca cambia la
valoración de la medida (ADR 0010). La fuente se evalúa una sola vez por ejecución
(`documents_snapshot`) y el adaptador real está en `matrix/orgdocs_source.py`.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from ens_evidence.matrix.evidence import LinkSupport, blocker, link_units
from ens_evidence.matrix.model import DocumentLink, ReviewedDocument
from ens_evidence.matrix.obligations import MatrixRowSpec
from ens_evidence.models.readiness import BlockerKind
from ens_evidence.workspace import WorkspaceStore

APPROVED = "APPROVED"
OBSOLETE = "OBSOLETE"


@dataclass(frozen=True)
class MeasureCoverage:
    covered_clauses: frozenset[str]
    missing_clauses: frozenset[str] = frozenset()


@dataclass(frozen=True)
class OrgDocumentEvidence:
    """`state` es el del documento (`APPROVED`, `OBSOLETE`…); solo `APPROVED` cuenta.
    `document_sha256` es el de la versión aprobada, si la hay."""

    name: str
    state: str
    approved_at: datetime | None = None
    document_sha256: str | None = None
    reasons: tuple[str, ...] = ()
    measures: Mapping[str, MeasureCoverage] = field(default_factory=dict)


@dataclass(frozen=True)
class DocumentsSnapshot:
    documents: Mapping[str, OrgDocumentEvidence] = field(default_factory=dict)

    def document(
        self, name: str, measure_id: str
    ) -> tuple[OrgDocumentEvidence, MeasureCoverage | None] | None:
        found = self.documents.get(name)
        return None if found is None else (found, found.measures.get(measure_id))


class OrgDocumentSource(Protocol):
    def snapshot(self, store: WorkspaceStore, at: datetime) -> DocumentsSnapshot: ...


def default_document_source() -> OrgDocumentSource:
    from ens_evidence.matrix.orgdocs_source import OrgdocsSource

    return OrgdocsSource()


def documents_snapshot(
    store: WorkspaceStore, at: datetime, source: OrgDocumentSource | None = None
) -> DocumentsSnapshot:
    """Estado de los documentos en `at`; se calcula una vez por ejecución de la matriz."""
    return (source or default_document_source()).snapshot(store, at)


def document_support(
    snapshot: DocumentsSnapshot, row: MatrixRowSpec, link: DocumentLink, at: datetime
) -> LinkSupport:
    units, cover_blockers = link_units(row, link)
    name = link.document

    def rejected(kind: BlockerKind, detail: str) -> LinkSupport:
        return LinkSupport(link, blockers=(*cover_blockers, blocker(kind, row, detail)))

    entry = snapshot.document(name, row.measure_id)
    if entry is None:
        return rejected(BlockerKind.MISSING_EVIDENCE, f"document_unknown:{name}")
    document, coverage = entry
    if document.state == OBSOLETE:
        return rejected(BlockerKind.EXPIRED_EVIDENCE, f"document_not_approved:{name}:OBSOLETE")
    if document.state != APPROVED:
        return rejected(
            BlockerKind.MISSING_EVIDENCE, f"document_not_approved:{name}:{document.state}"
        )
    if document.approved_at is None or document.approved_at > at:
        return rejected(
            BlockerKind.MISSING_EVIDENCE, f"document_not_approved:{name}:approved_after_query"
        )
    covered = units & coverage.covered_clauses if coverage else frozenset()
    if not covered:
        return rejected(BlockerKind.MISSING_EVIDENCE, f"document_not_covering:{name}")
    return LinkSupport(
        link,
        clauses=covered if not cover_blockers else frozenset(),
        blockers=cover_blockers,
        reviewed_document=ReviewedDocument(
            name=name, document_sha256=document.document_sha256 or ""
        ),
        available_at=document.approved_at,
    )
