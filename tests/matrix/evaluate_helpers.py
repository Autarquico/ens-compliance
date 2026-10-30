from datetime import timedelta

from applicability.helpers import decide

from ens_evidence.applicability import approve_declaration
from ens_evidence.catalog import load_catalog
from ens_evidence.catalog.controls import current_catalog_version
from ens_evidence.evidence.manifests import load_manifest_by_id
from ens_evidence.matrix import (
    DocumentsSnapshot,
    MatrixEntry,
    MatrixFile,
    MeasureCoverage,
    MeasureReviewRecord,
    OrgDocumentEvidence,
    Owner,
    ReviewedDocument,
    ReviewedEvidence,
    ReviewedObligation,
    add_review,
    assess,
    links_sha256,
    plan_obligations,
    save_matrix,
)
from ens_evidence.models import MeasureAssessment, Review, Role

from .helpers import SHA
from .source_helpers import AT as NOW

ONE_CLAUSE = "op.exp.1"
TWO_CLAUSES = "op.ext.4"
NOT_SELECTED = "op.cont.3"
ADDITIONAL = "op.cont.2"
RECORDED = NOW - timedelta(hours=12)
FAR = NOW + timedelta(days=365)


class FakeDocuments:
    def __init__(self, *documents: OrgDocumentEvidence) -> None:
        self._documents = {d.name: d for d in documents}

    def snapshot(self, store, at) -> DocumentsSnapshot:
        return DocumentsSnapshot(self._documents)


def approved_document(
    name="politica", measure_id=TWO_CLAUSES, clauses=("op.ext.4.2",), *, sha=SHA, state="APPROVED"
):
    return OrgDocumentEvidence(
        name=name,
        state=state,
        approved_at=NOW - timedelta(days=2),
        document_sha256=sha,
        measures={measure_id: MeasureCoverage(frozenset(clauses))},
    )


def entry(measure_id, *links, every: int | None = 30, owner: bool = True) -> MatrixEntry:
    return MatrixEntry(
        id=measure_id,
        owner=Owner(name="Ana", role=Role.SYSTEM_ADMINISTRATOR) if owner else None,
        review_every_days=every,
        links=list(links),
    )


def configure(store, *entries: MatrixEntry) -> None:
    version = current_catalog_version(load_catalog())
    save_matrix(store, MatrixFile(catalog_version=version, entries=list(entries)))


def row_spec(store, measure_id, at=NOW):
    spec = plan_obligations(store, at).row(measure_id)
    assert spec is not None
    return spec


def review_record(
    store,
    matrix_entry: MatrixEntry,
    *,
    recorded_at=RECORDED,
    assessment=MeasureAssessment.COMPLIANT,
    valid_days=90,
    evidence_ids=(),
    checks=(),
    documents=(),
    **overrides,
) -> MeasureReviewRecord:
    """Revisión de la medida tal como la vería quien revisa ahora (huellas actuales)."""
    spec = row_spec(store, matrix_entry.id, recorded_at)
    reviewed_at = recorded_at - timedelta(hours=1)
    obligation = ReviewedObligation(
        obligation="base",
        evidence=[
            ReviewedEvidence(
                evidence_id=i, artifact_sha256=load_manifest_by_id(store, i).artifact.sha256
            )
            for i in evidence_ids
        ],
        checks=list(checks),
        documents=[ReviewedDocument(name=n, document_sha256=SHA) for n in documents],
    )
    fields = {
        "measure_id": matrix_entry.id,
        "review": Review(
            reviewer="Ana",
            role=Role.SYSTEM_ADMINISTRATOR,
            reviewed_at=reviewed_at,
            valid_until=reviewed_at + timedelta(days=valid_days),
            conclusion="Revisada",
        ),
        "recorded_at": recorded_at,
        "assessment": assessment,
        "obligations": [obligation],
        "links_sha256": links_sha256(matrix_entry),
        "measure_context_sha256": spec.measure_context_sha256,
    }
    return MeasureReviewRecord(**{**fields, **overrides})


def record(store, matrix_entry: MatrixEntry, **kwargs) -> MeasureReviewRecord:
    review = review_record(store, matrix_entry, **kwargs)
    add_review(store, review)
    return review


def assess_now(store, at=NOW, **kwargs):
    kwargs.setdefault("documents", FakeDocuments())
    return assess(store, at, **kwargs)


def row_of(result, measure_id):
    return next(r for r in result.rows if r.spec.measure_id == measure_id)


def details(row) -> set[tuple[str, str]]:
    return {(b.kind.value, b.detail) for b in row.blockers}


def re_decide(store, measure_id, applicability, at, by="Luis Pardo"):
    decide(store, measure_id, applicability, "Decisión revisada de la organización.")
    return approve_declaration(store, by, at)
