"""Adaptador real de la matriz sobre `orgdocs` (`document_status` y `approved_coverage`)."""

from datetime import datetime

from ens_evidence.matrix.documents import DocumentsSnapshot, MeasureCoverage, OrgDocumentEvidence
from ens_evidence.orgdocs import all_statuses, approved_coverage
from ens_evidence.workspace import WorkspaceStore


class OrgdocsSource:
    """Lee el estado actual de org-docs. `at` solo descarta lo aprobado después (en
    `document_support`): org-docs no guarda el historial de su vigencia."""

    def snapshot(self, store: WorkspaceStore, at: datetime) -> DocumentsSnapshot:
        measures = approved_coverage(store).measures
        documents = {}
        for status in all_statuses(store):
            approval = status.approval
            documents[status.name] = OrgDocumentEvidence(
                name=status.name,
                state=status.state.value,
                approved_at=approval.approved_at if approval else None,
                document_sha256=approval.document_sha256 if approval else None,
                reasons=tuple(status.reasons),
                measures={
                    measure_id: MeasureCoverage(
                        frozenset(by_document[status.name].covered_clauses),
                        frozenset(by_document[status.name].missing_clauses),
                    )
                    for measure_id, by_document in measures.items()
                    if status.name in by_document
                },
            )
        return DocumentsSnapshot(documents)
