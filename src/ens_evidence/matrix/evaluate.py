"""Evaluación de la matriz: único punto de integración con la regla de preparación.

`assess` construye, por fila de la Declaración vigente, la `Measure` de la regla compartida
(decisiones de `measures.yaml`, valoración y revisión de la última revisión registrada y la
evidencia que cuenta) y le añade los huecos propios de la matriz como `coverage_gaps`. Nada de
aquí declara conformidad: `COMPLIANT` solo sale de una revisión humana registrada (regla 12).
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ens_evidence.applicability import DeclarationState
from ens_evidence.catalog.controls import MeasuresFileError, load_measures
from ens_evidence.matrix.documents import (
    DocumentsSnapshot,
    OrgDocumentSource,
    default_document_source,
    document_support,
    documents_snapshot,
)
from ens_evidence.matrix.evidence import LinkSupport, manual_support, verified_manifest
from ens_evidence.matrix.fingerprint import links_sha256
from ens_evidence.matrix.model import (
    CheckLink,
    DocumentLink,
    ManualEvidenceLink,
    MatrixEntry,
    MeasureReviewRecord,
)
from ens_evidence.matrix.obligations import MatrixRowSpec, RowKind, plan_obligations
from ens_evidence.matrix.store import StoredReview, effective_reviews, read_matrix
from ens_evidence.matrix.technical import (
    CheckResultsSource,
    CheckRun,
    NoCheckResults,
    technical_support,
)
from ens_evidence.models.contracts import Review
from ens_evidence.models.enums import Applicability, MeasureAssessment, Readiness
from ens_evidence.models.readiness import (
    Blocker,
    BlockerKind,
    EvidenceRef,
    Measure,
    ReadinessInput,
    ReadinessResult,
    evaluate_readiness,
    measure_blockers,
)
from ens_evidence.workspace import WorkspaceStore

_NO_CHECKS = NoCheckResults()
_ORG_DOCUMENTS = default_document_source()


@dataclass(frozen=True)
class AssessmentRow:
    """Una fila evaluada; `blockers` reúne la regla compartida y los huecos de la matriz."""

    spec: MatrixRowSpec
    measure: Measure
    blockers: tuple[Blocker, ...]
    entry: MatrixEntry | None = None
    review: StoredReview | None = None
    supports: tuple[LinkSupport, ...] = ()
    covered: frozenset[str] = frozenset()


@dataclass(frozen=True)
class AssessmentResult:
    """`blockers` son los que la matriz añade a la regla compartida (huecos y bloqueo global);
    `readiness.blockers` lo reúne todo. `errors`: ficheros ilegibles que se han ignorado."""

    evaluated_at: datetime
    declaration_state: DeclarationState
    rows: tuple[AssessmentRow, ...]
    blockers: tuple[Blocker, ...]
    readiness: ReadinessResult
    errors: tuple[str, ...] = field(default=())


def _unique(blockers: list[Blocker]) -> tuple[Blocker, ...]:
    return tuple({(b.kind, b.measure_id, b.detail): b for b in blockers}.values())


def _gap(kind: BlockerKind, measure_id: str, detail: str) -> Blocker:
    return Blocker(kind=kind, measure_id=measure_id, detail=detail)


def effective_review(review: Review, review_every_days: int | None) -> Review:
    """Vigencia efectiva: `min(valid_until, reviewed_at + review_every_days)`."""
    if review_every_days is None:
        return review
    periodic = review.reviewed_at + timedelta(days=review_every_days)
    return review.model_copy(update={"valid_until": min(review.valid_until, periodic)})


def _not_current(plan, now: datetime) -> AssessmentResult:
    detail = f"declaration_{plan.state.value.lower()}"
    glob = Blocker(kind=BlockerKind.UNDETERMINED_APPLICABILITY, detail=detail)
    rows = tuple(
        AssessmentRow(
            row,
            Measure(id=row.measure_id, applicability=Applicability.UNDETERMINED),
            (_gap(BlockerKind.UNDETERMINED_APPLICABILITY, row.measure_id, detail),),
        )
        for row in plan.rows
    )
    readiness = ReadinessResult(
        readiness=Readiness.NOT_READY, blockers=[glob], pending_human_review=True
    )
    return AssessmentResult(now, plan.state, rows, (glob,), readiness, tuple(plan.errors))


def _decisions(store: WorkspaceStore) -> tuple[Mapping[str, Measure], list[str]]:
    try:
        document = load_measures(store)
    except MeasuresFileError as exc:
        return {}, exc.errors
    return {m.id: m for m in document.measures} if document else {}, []


def _supports(
    store: WorkspaceStore,
    row: MatrixRowSpec,
    entry: MatrixEntry,
    runs: tuple[CheckRun, ...],
    snapshot: DocumentsSnapshot,
    now: datetime,
) -> list[LinkSupport]:
    found: list[LinkSupport] = []
    for link in entry.links:
        match link:
            case ManualEvidenceLink():
                found.append(manual_support(store, row, link, now, entry.review_every_days))
            case CheckLink():
                found.append(technical_support(store, row, link, runs, now))
            case DocumentLink():
                found.append(document_support(snapshot, row, link, now))
    return found


def _sustains_review(
    support: LinkSupport, row: MatrixRowSpec, record: MeasureReviewRecord | None
) -> LinkSupport:
    """Una fuente posterior al registro no sustenta esa revisión; la ejecución nueva de un check
    que el revisor aceptó sí (fuente continua)."""
    if record is None or not support.counts or support.available_at is None:
        return support
    if support.available_at <= record.recorded_at:
        return support
    link = support.link
    if isinstance(link, CheckLink) and any(link.check_id in o.checks for o in record.obligations):
        return support
    kind, target = link.target
    detail = f"source_after_review:{kind}:{target}"
    return LinkSupport(link, blockers=(_gap(BlockerKind.MISSING_EVIDENCE, row.measure_id, detail),))


def _evidence_refs(supports: list[LinkSupport]) -> list[EvidenceRef]:
    refs: dict[str, EvidenceRef] = {}
    for support in supports:
        if not support.counts:
            continue
        refs.update((ref.id, ref) for ref in support.evidence)
        if support.reviewed_document is not None:
            name = support.reviewed_document.name
            refs[f"document:{name}"] = EvidenceRef(
                id=f"document:{name}", collected_at=support.available_at
            )
    return list(refs.values())


def _changed_sources(
    store: WorkspaceStore, record: MeasureReviewRecord, snapshot: DocumentsSnapshot, measure_id: str
) -> list[Blocker]:
    changed: list[Blocker] = []
    for reviewed in {e.evidence_id: e for o in record.obligations for e in o.evidence}.values():
        manifest = verified_manifest(store, reviewed.evidence_id)
        if manifest is None or manifest.artifact.sha256 != reviewed.artifact_sha256:
            detail = f"evidence_changed:{reviewed.evidence_id}"
            changed.append(_gap(BlockerKind.EXPIRED_REVIEW, measure_id, detail))
    for reviewed_doc in {d.name: d for o in record.obligations for d in o.documents}.values():
        current = snapshot.documents.get(reviewed_doc.name)
        if current is None or current.document_sha256 != reviewed_doc.document_sha256:
            detail = f"document_changed:{reviewed_doc.name}"
            changed.append(_gap(BlockerKind.EXPIRED_REVIEW, measure_id, detail))
    return changed


def _review_gaps(
    store: WorkspaceStore,
    row: MatrixRowSpec,
    entry: MatrixEntry,
    stored: StoredReview | None,
    snapshot: DocumentsSnapshot,
) -> list[Blocker]:
    selected = row.kind is RowKind.SELECTED
    if stored is None:
        # La regla compartida ya señala la falta de revisión en las no aplicables.
        missing = _gap(BlockerKind.MISSING_REVIEW, row.measure_id, "review_missing")
        return [missing] if selected else []
    record = stored.record
    gaps: list[Blocker] = []
    axis_matches = isinstance(record, MeasureReviewRecord) is selected
    if not axis_matches or record.measure_context_sha256 != row.measure_context_sha256:
        gaps.append(_gap(BlockerKind.EXPIRED_REVIEW, row.measure_id, "context_changed"))
    if selected and isinstance(record, MeasureReviewRecord):
        if record.links_sha256 != links_sha256(entry):
            gaps.append(_gap(BlockerKind.EXPIRED_REVIEW, row.measure_id, "links_changed"))
        gaps += _changed_sources(store, record, snapshot, row.measure_id)
    return gaps


def _coverage_gaps(row: MatrixRowSpec, covered: frozenset[str]) -> list[Blocker]:
    gaps: list[Blocker] = []
    for obligation in row.obligations:
        label = "clause" if obligation.clause_ids else "obligation"
        gaps += [
            _gap(BlockerKind.MISSING_EVIDENCE, row.measure_id, f"{label}:{unit}")
            for unit in obligation.units
            if unit not in covered
        ]
    return gaps


def _assess_row(
    store: WorkspaceStore,
    row: MatrixRowSpec,
    entry: MatrixEntry | None,
    decision: Measure | None,
    runs: tuple[CheckRun, ...],
    snapshot: DocumentsSnapshot,
    now: datetime,
) -> tuple[AssessmentRow, list[Blocker], list[str]]:
    selected = row.kind is RowKind.SELECTED
    entry = entry or MatrixEntry(id=row.measure_id)
    reviews = effective_reviews(store, row.measure_id, now)
    primary, other = (
        (reviews.implementation, reviews.applicability)
        if selected
        else (reviews.applicability, reviews.implementation)
    )
    stored = primary or other
    record = stored.record if stored else None
    implementation = record if isinstance(record, MeasureReviewRecord) else None

    supports = (
        [
            _sustains_review(s, row, implementation)
            for s in _supports(store, row, entry, runs, snapshot, now)
        ]
        if selected
        else []
    )
    covered = frozenset().union(*(s.clauses for s in supports if s.counts))

    gaps: list[Blocker] = []
    if entry.owner is None:
        gaps.append(_gap(BlockerKind.MISSING_REVIEW, row.measure_id, "owner_missing"))
    if entry.review_every_days is None:
        gaps.append(_gap(BlockerKind.MISSING_REVIEW, row.measure_id, "review_every_days_missing"))
    gaps += _review_gaps(store, row, entry, stored, snapshot)
    gaps += _coverage_gaps(row, covered)
    gaps += [b for s in supports for b in s.blockers]

    base = decision or Measure(id=row.measure_id, applicability=Applicability.UNDETERMINED)
    measure = base.model_copy(
        update={
            "applicability": Applicability.APPLICABLE if selected else Applicability.NOT_APPLICABLE,
            "assessment": implementation.assessment
            if implementation
            else MeasureAssessment.NOT_ASSESSED,
            "review": effective_review(record.review, entry.review_every_days) if record else None,
            "evidence": _evidence_refs(supports),
        }
    )
    unique_gaps = list(_unique(gaps))
    blockers = _unique([*measure_blockers(measure, now), *unique_gaps])
    assessed = AssessmentRow(row, measure, blockers, entry, stored, tuple(supports), covered)
    return assessed, unique_gaps, reviews.errors


def assess(
    store: WorkspaceStore,
    now: datetime,
    *,
    checks: CheckResultsSource = _NO_CHECKS,
    documents: OrgDocumentSource = _ORG_DOCUMENTS,
) -> AssessmentResult:
    """Filas, bloqueos de la matriz y `ReadinessResult` en `now`; único punto de integración."""
    plan = plan_obligations(store, now)
    if not plan.current:
        return _not_current(plan, now)

    matrix, errors = read_matrix(store)
    entries = {e.id: e for e in matrix.entries} if matrix else {}
    decisions, decision_errors = _decisions(store)
    errors = [*plan.errors, *errors, *decision_errors]
    runs = tuple(checks.runs(store, now))
    snapshot = documents_snapshot(store, now, documents)

    rows: list[AssessmentRow] = []
    gaps: list[Blocker] = []
    for spec in plan.rows:
        assessed, row_gaps, review_errors = _assess_row(
            store,
            spec,
            entries.get(spec.measure_id),
            decisions.get(spec.measure_id),
            runs,
            snapshot,
            now,
        )
        rows.append(assessed)
        gaps += row_gaps
        errors += review_errors

    readiness = evaluate_readiness(
        ReadinessInput(
            measures=[r.measure for r in rows],
            judgment_executed=False,
            evaluated_at=now,
            coverage_gaps=gaps,
        )
    )
    return AssessmentResult(now, plan.state, tuple(rows), tuple(gaps), readiness, tuple(errors))
