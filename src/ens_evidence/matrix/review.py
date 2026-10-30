"""Revisiones humanas de la matriz y su estado.

Una revisión es de una persona (anexo III, 2.1.a): la herramienta solo la registra, atribuye a cada
cláusula las fuentes que cuentan en ese momento y se niega a registrar `COMPLIANT` si algo queda
sin cubrir. El nombre de quien revisa es declarado; no se autentica (regla 12).
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from ens_evidence.matrix.documents import OrgDocumentSource, documents_snapshot
from ens_evidence.matrix.evaluate import AssessmentResult, AssessmentRow, assess
from ens_evidence.matrix.evidence import LinkSupport
from ens_evidence.matrix.fingerprint import links_sha256
from ens_evidence.matrix.links import (
    MatrixRejected,
    current_supports,
    entry_of,
    link_label,
    load_matrix,
    parse_role,
    require_current,
    row_of,
)
from ens_evidence.matrix.model import (
    ApplicabilityReview,
    MatrixEntry,
    MeasureReviewRecord,
    ReviewedDocument,
    ReviewedEvidence,
    ReviewedObligation,
)
from ens_evidence.matrix.obligations import MatrixRowSpec, RowKind
from ens_evidence.matrix.store import add_review
from ens_evidence.matrix.technical import CheckResultsSource, NoCheckResults
from ens_evidence.models.contracts import Review
from ens_evidence.models.dossier import SELF_ASSESSORS
from ens_evidence.models.enums import MeasureAssessment
from ens_evidence.models.readiness import Blocker, BlockerKind
from ens_evidence.workspace import WorkspaceStore

ASSESSMENTS = (
    MeasureAssessment.COMPLIANT,
    MeasureAssessment.NON_COMPLIANT,
    MeasureAssessment.REQUIRES_REVIEW,
)
CHECKS_PENDING = "evidencia técnica: pendiente del motor de checks"
REVIEW_BLOCKERS = {BlockerKind.MISSING_REVIEW, BlockerKind.EXPIRED_REVIEW}


@dataclass(frozen=True)
class ClauseAttribution:
    unit: str
    sources: tuple[str, ...]


@dataclass(frozen=True)
class ObligationAttribution:
    obligation: str
    clauses: tuple[ClauseAttribution, ...]


@dataclass(frozen=True)
class ReviewPlan:
    """Lo que se registraría. `problems` vacío ⇔ `record` existe."""

    row: MatrixRowSpec
    attribution: tuple[ObligationAttribution, ...]
    notes: tuple[str, ...]
    problems: tuple[str, ...]
    record: MeasureReviewRecord | ApplicabilityReview | None


def parse_assessment(text: str | None) -> MeasureAssessment | None:
    if text is None:
        return None
    try:
        assessment = MeasureAssessment(text)
    except ValueError:
        assessment = None
    if assessment not in ASSESSMENTS:
        allowed = ", ".join(a.value for a in ASSESSMENTS)
        raise MatrixRejected(f"--assessment «{text}» no válido; valores: {allowed}.")
    return assessment


def _check_inputs(
    by: str, role: str, conclusion: str, at: datetime | None, now: datetime
) -> list[str]:
    errors: list[str] = []
    if not by.strip():
        errors.append("--by no puede estar vacío.")
    if not conclusion.strip():
        errors.append("--conclusion no puede estar vacía.")
    try:
        if parse_role(role) not in SELF_ASSESSORS:
            allowed = ", ".join(sorted(r.value for r in SELF_ASSESSORS))
            errors.append(
                f"Rol «{role}» no permitido: registran revisiones {allowed}. "
                "El responsable de la seguridad analiza los informes (2.1.b), no los revisa."
            )
    except MatrixRejected as exc:
        errors += exc.errors
    if at is not None and at > now:
        errors.append("--at no puede ser futuro: se declara cuándo se revisó, no cuándo se hará.")
    return errors


def _attribution(
    row: MatrixRowSpec, supports: Iterable[LinkSupport]
) -> tuple[ObligationAttribution, ...]:
    counting = [s for s in supports if s.counts]
    return tuple(
        ObligationAttribution(
            o.id,
            tuple(
                ClauseAttribution(
                    unit, tuple(link_label(s.link) for s in counting if unit in s.clauses)
                )
                for unit in o.units
            ),
        )
        for o in row.obligations
    )


def _reviewed_obligations(
    row: MatrixRowSpec, supports: Iterable[LinkSupport]
) -> list[ReviewedObligation]:
    counting = [s for s in supports if s.counts]
    reviewed: list[ReviewedObligation] = []
    for obligation in row.obligations:
        units = set(obligation.units)
        used = [s for s in counting if s.clauses & units]
        evidence: dict[str, ReviewedEvidence] = {}
        documents: dict[str, ReviewedDocument] = {}
        for support in used:
            evidence.update((e.evidence_id, e) for e in support.reviewed_evidence)
            if support.reviewed_document is not None:
                documents[support.reviewed_document.name] = support.reviewed_document
        reviewed.append(
            ReviewedObligation(
                obligation=obligation.id,
                clauses=sorted(u for u in units if any(u in s.clauses for s in used)),
                evidence=list(evidence.values()),
                checks=sorted({s.link.check_id for s in used if s.link.kind == "check"}),
                documents=list(documents.values()),
            )
        )
    return reviewed


def _missing(row: MatrixRowSpec, supports: Iterable[LinkSupport]) -> list[str]:
    covered = frozenset().union(*(s.clauses for s in supports if s.counts))
    return [
        f"{'cláusula' if o.clause_ids else 'obligación'} sin cubrir: {unit} ({o.id})"
        for o in row.obligations
        for unit in o.units
        if unit not in covered
    ]


def _problems(
    entry: MatrixEntry, assessment: MeasureAssessment | None, missing: list[str]
) -> list[str]:
    problems: list[str] = []
    if entry.review_every_days is None:
        problems.append("falta la periodicidad (matrix set --every DÍAS): fija la vigencia.")
    if assessment is MeasureAssessment.COMPLIANT:
        if entry.owner is None:
            problems.append("falta el responsable (matrix set --owner NOMBRE --owner-role ROL).")
        problems += missing
    return problems


def prepare_review(
    store: WorkspaceStore,
    now: datetime,
    measure_id: str,
    *,
    by: str,
    role: str,
    conclusion: str,
    assessment: str | None = None,
    at: datetime | None = None,
    checks: CheckResultsSource | None = None,
    documents: OrgDocumentSource | None = None,
) -> ReviewPlan:
    """Decide qué se registraría; no escribe. `MatrixRejected` si la orden no es válida."""
    verdict = parse_assessment(assessment)
    errors = _check_inputs(by, role, conclusion, at, now)
    plan = require_current(store, now)
    row = row_of(plan, measure_id)
    selected = row.kind is RowKind.SELECTED
    if selected and verdict is None:
        errors.append(
            "--assessment es obligatorio en una medida aplicable (COMPLIANT, NON_COMPLIANT o "
            "REQUIRES_REVIEW)."
        )
    if not selected and verdict is not None:
        errors.append(
            f"{measure_id} no es aplicable: --assessment está prohibido; la revisión solo "
            "confirma la decisión de no aplicabilidad."
        )
    if errors:
        raise MatrixRejected(*errors)

    entry = entry_of(load_matrix(store), measure_id)
    snapshot = documents_snapshot(store, now, documents)
    runs = tuple((checks or NoCheckResults()).runs(store, now))
    supports = current_supports(store, row, entry, now, snapshot, runs) if selected else []
    problems = _problems(entry, verdict, _missing(row, supports) if selected else [])
    notes = tuple(
        f"{link_label(s.link)}: {b.kind.value} {b.detail}" for s in supports for b in s.blockers
    )
    attribution = _attribution(row, supports)
    if problems:
        return ReviewPlan(row, attribution, notes, tuple(problems), None)

    reviewed_at = at or now
    assert entry.review_every_days is not None
    review = Review(
        reviewer=by.strip(),
        role=parse_role(role),
        reviewed_at=reviewed_at,
        valid_until=reviewed_at + timedelta(days=entry.review_every_days),
        conclusion=conclusion.strip(),
    )
    record: MeasureReviewRecord | ApplicabilityReview
    if selected and verdict is not None:
        record = MeasureReviewRecord(
            measure_id=measure_id,
            review=review,
            recorded_at=now,
            assessment=verdict,
            obligations=_reviewed_obligations(row, supports),
            links_sha256=links_sha256(entry),
            measure_context_sha256=row.measure_context_sha256,
        )
    else:
        record = ApplicabilityReview(
            measure_id=measure_id,
            review=review,
            recorded_at=now,
            measure_context_sha256=row.measure_context_sha256,
        )
    return ReviewPlan(row, attribution, notes, (), record)


def record_review(store: WorkspaceStore, plan: ReviewPlan) -> str:
    """Escribe la revisión aceptada y devuelve su clave."""
    if plan.record is None:
        raise MatrixRejected(*plan.problems)
    return add_review(store, plan.record)


@dataclass(frozen=True)
class RowStatus:
    row: AssessmentRow
    implemented: bool
    regularly_reviewed: bool
    sources: tuple[str, ...]
    has_check_links: bool

    @property
    def blockers(self) -> tuple[Blocker, ...]:
        return self.row.blockers


def row_status(row: AssessmentRow) -> RowStatus:
    """Implantada: revisión COMPLIANT sin ningún bloqueo. Revisión regular: responsable,
    periodicidad y revisión vigente (sin `MISSING_REVIEW` ni `EXPIRED_REVIEW`)."""
    selected = row.spec.kind is RowKind.SELECTED
    implemented = (
        selected and row.measure.assessment is MeasureAssessment.COMPLIANT and not row.blockers
    )
    regular = row.review is not None and not any(b.kind in REVIEW_BLOCKERS for b in row.blockers)
    links = row.entry.links if row.entry else []
    return RowStatus(
        row,
        implemented,
        regular,
        tuple(link_label(s.link) for s in row.supports if s.counts),
        any(link.kind == "check" for link in links),
    )


def matrix_status(
    store: WorkspaceStore,
    at: datetime,
    *,
    checks: CheckResultsSource | None = None,
    documents: OrgDocumentSource | None = None,
) -> tuple[AssessmentResult, tuple[RowStatus, ...]]:
    kwargs = {}
    if checks is not None:
        kwargs["checks"] = checks
    if documents is not None:
        kwargs["documents"] = documents
    result = assess(store, at, **kwargs)
    return result, tuple(row_status(r) for r in result.rows)
