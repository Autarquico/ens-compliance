"""Revisiones humanas de la matriz y su estado.

Una revisión es de una persona (anexo III, 2.1.a): la herramienta solo la registra, atribuye a cada
cláusula las fuentes que cuentan en ese momento y se niega a registrar `COMPLIANT` si algo queda
sin cubrir. El nombre de quien revisa es declarado; no se autentica (regla 12).
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from ens_evidence.matrix.documents import OrgDocumentSource, documents_snapshot
from ens_evidence.matrix.evaluate import covered_units, link_supports, uncovered_units
from ens_evidence.matrix.evidence import LinkSupport
from ens_evidence.matrix.fingerprint import links_sha256
from ens_evidence.matrix.links import (
    MatrixRejected,
    entry_of,
    link_label,
    load_matrix,
    parse_role,
    require_current,
    row_of,
)
from ens_evidence.matrix.model import (
    ApplicabilityReview,
    CheckLink,
    MatrixEntry,
    MeasureReviewRecord,
    ReviewedClause,
    ReviewedDocument,
    ReviewedEvidence,
    ReviewedObligation,
)
from ens_evidence.matrix.obligations import MatrixRowSpec, Obligation, RowKind
from ens_evidence.matrix.store import add_review
from ens_evidence.matrix.technical import CheckResultsSource, load_checks
from ens_evidence.models.contracts import Review
from ens_evidence.models.dossier import SELF_ASSESSORS
from ens_evidence.models.enums import MeasureAssessment
from ens_evidence.workspace import WorkspaceStore

ASSESSMENTS = (
    MeasureAssessment.COMPLIANT,
    MeasureAssessment.NON_COMPLIANT,
    MeasureAssessment.REQUIRES_REVIEW,
)


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


def _covering(
    row: MatrixRowSpec, supports: Iterable[LinkSupport]
) -> list[tuple[Obligation, dict[str, list[LinkSupport]]]]:
    """Por obligación y unidad, las fuentes que cuentan y la cubren: el único cálculo de qué
    fuente respalda qué, del que salen la atribución que se muestra y la que se registra."""
    counting = [s for s in supports if s.counts]
    return [
        (o, {unit: [s for s in counting if unit in s.clauses] for unit in o.units})
        for o in row.obligations
    ]


def _attribution(
    covering: list[tuple[Obligation, dict[str, list[LinkSupport]]]],
) -> tuple[ObligationAttribution, ...]:
    return tuple(
        ObligationAttribution(
            o.id,
            tuple(
                ClauseAttribution(unit, tuple(link_label(s.link) for s in found))
                for unit, found in by_unit.items()
            ),
        )
        for o, by_unit in covering
    )


def _reviewed_obligations(
    covering: list[tuple[Obligation, dict[str, list[LinkSupport]]]],
) -> list[ReviewedObligation]:
    reviewed: list[ReviewedObligation] = []
    for obligation, by_unit in covering:
        used = [s for found in by_unit.values() for s in found]
        evidence: dict[str, ReviewedEvidence] = {}
        documents: dict[str, ReviewedDocument] = {}
        for support in used:
            evidence.update((e.evidence_id, e) for e in support.reviewed_evidence)
            if support.reviewed_document is not None:
                documents[support.reviewed_document.name] = support.reviewed_document
        reviewed.append(
            ReviewedObligation(
                obligation=obligation.id,
                coverage=[
                    ReviewedClause(
                        clause=unit,
                        sources=[
                            f"{kind}:{target}" for kind, target in (s.link.target for s in found)
                        ],
                    )
                    for unit, found in sorted(by_unit.items())
                    if found
                ],
                evidence=list(evidence.values()),
                checks=sorted({s.link.check_id for s in used if isinstance(s.link, CheckLink)}),
                documents=list(documents.values()),
            )
        )
    return reviewed


def _missing(row: MatrixRowSpec, supports: Iterable[LinkSupport]) -> list[str]:
    return [
        f"{'cláusula' if u.is_clause else 'obligación'} sin cubrir: {u.unit} ({u.obligation})"
        for u in uncovered_units(row, covered_units(supports))
    ]


def _before_sources(supports: Iterable[LinkSupport], reviewed_at: datetime | None) -> list[str]:
    """Una revisión no puede datarse antes de las fuentes que se le atribuyen."""
    if reviewed_at is None:
        return []
    return [
        f"--at {reviewed_at.isoformat()} es anterior a {link_label(s.link)}, disponible desde "
        f"{s.available_at.isoformat()}; no se puede revisar una fuente que aún no existía."
        for s in supports
        if s.counts and s.available_at is not None and s.available_at > reviewed_at
    ]


def _problems(
    entry: MatrixEntry,
    assessment: MeasureAssessment | None,
    missing: list[str],
    late_sources: list[str],
) -> list[str]:
    problems: list[str] = []
    if entry.review_every_days is None:
        problems.append("falta la periodicidad (matrix set --every DÍAS): fija la vigencia.")
    if entry.owner is None:
        problems.append("falta el responsable (matrix set --owner NOMBRE --owner-role ROL).")
    if assessment is MeasureAssessment.COMPLIANT:
        problems += [*missing, *late_sources]
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
    runs, problems = load_checks(checks, store, now)
    supports = link_supports(store, row, entry, runs, snapshot, now, problems) if selected else []
    problems = _problems(
        entry, verdict, _missing(row, supports) if selected else [], _before_sources(supports, at)
    )
    notes = tuple(
        f"{link_label(s.link)}: {b.kind.value} {b.detail}" for s in supports for b in s.blockers
    )
    covering = _covering(row, supports)
    attribution = _attribution(covering)
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
            obligations=_reviewed_obligations(covering),
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
