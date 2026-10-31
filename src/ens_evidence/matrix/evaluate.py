"""Evaluación de la matriz: único punto de integración con la regla de preparación.

`assess` construye, por fila de la Declaración vigente, la `Measure` de la regla compartida
(decisiones de `measures.yaml`, valoración y revisión de la última revisión registrada y la
evidencia que cuenta) y le añade los huecos propios de la matriz como `coverage_gaps`. Nada de
aquí declara conformidad: `COMPLIANT` solo sale de una revisión humana registrada (regla 12).
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import NamedTuple, Protocol

from ens_evidence.applicability import Declaration, DeclarationState, declaration_history
from ens_evidence.catalog.controls import MeasuresFileError, load_measures
from ens_evidence.matrix.documents import (
    DocumentsSnapshot,
    OrgDocumentSource,
    document_support,
    documents_snapshot,
)
from ens_evidence.matrix.evidence import (
    LinkSupport,
    manifest_sha256,
    manual_support,
    verified_manifest,
)
from ens_evidence.matrix.fingerprint import links_sha256, measure_context_sha256
from ens_evidence.matrix.history import ChangeScan, scan_history
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
    ConnectorProblems,
    load_checks,
    problem_errors,
    technical_support,
)
from ens_evidence.models.contracts import Review
from ens_evidence.models.enums import Applicability, MeasureAssessment, Readiness
from ens_evidence.models.readiness import (
    Blocker,
    BlockerKind,
    EvidenceRef,
    Measure,
    OpenFinding,
    ReadinessInput,
    ReadinessResult,
    evaluate_readiness,
    measure_blockers,
    unique_blockers,
)
from ens_evidence.workspace import WorkspaceStore


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


class FindingsSource(Protocol):
    def open_findings(self, store: WorkspaceStore, at: datetime) -> tuple[list[OpenFinding], bool]:
        """Hallazgos abiertos en `at` y si el juicio (pre-auditoría) se ha ejecutado."""
        ...


class StaticFindings:
    def __init__(self, findings: Iterable[OpenFinding] = (), *, executed: bool = True) -> None:
        self._findings = list(findings)
        self._executed = executed

    def open_findings(self, store: WorkspaceStore, at: datetime) -> tuple[list[OpenFinding], bool]:
        return list(self._findings), self._executed


class NoFindings(StaticFindings):
    """Hasta `pre-audit` no hay fuente real: sin hallazgos y sin juicio ejecutado."""

    def __init__(self) -> None:
        super().__init__((), executed=False)


def _gap(kind: BlockerKind, measure_id: str, detail: str) -> Blocker:
    return Blocker(kind=kind, measure_id=measure_id, detail=detail)


def effective_review(review: Review, review_every_days: int | None) -> Review:
    """Vigencia efectiva: `min(valid_until, reviewed_at + review_every_days)`."""
    if review_every_days is None:
        return review
    periodic = review.reviewed_at + timedelta(days=review_every_days)
    return review.model_copy(update={"valid_until": min(review.valid_until, periodic)})


def _not_current(store: WorkspaceStore, plan, now: datetime) -> AssessmentResult:
    # No pasa por `evaluate_readiness`: con la Declaración sin vigencia puede no haber filas y
    # `ReadinessInput.measures` no admite una lista vacía. Nada abre aquí un camino a READY.
    # Las filas conservan la entrada y la última revisión para mostrarlas tal como están: lo que
    # la Declaración deja sin efecto es la medida, no lo que la persona registró.
    detail = f"declaration_{plan.state.value.lower()}"
    glob = Blocker(kind=BlockerKind.UNDETERMINED_APPLICABILITY, detail=detail)
    matrix, errors = read_matrix(store)
    entries = {e.id: e for e in matrix.entries} if matrix else {}
    rows: list[AssessmentRow] = []
    for row in plan.rows:
        reviews = effective_reviews(store, row.measure_id, now)
        errors += reviews.errors
        rows.append(
            AssessmentRow(
                row,
                Measure(id=row.measure_id, applicability=Applicability.UNDETERMINED),
                (_gap(BlockerKind.UNDETERMINED_APPLICABILITY, row.measure_id, detail),),
                entries.get(row.measure_id) or MatrixEntry(id=row.measure_id),
                _latest(reviews.implementation, reviews.applicability),
            )
        )
    readiness = ReadinessResult(
        readiness=Readiness.NOT_READY, blockers=[glob], pending_human_review=True
    )
    return AssessmentResult(
        now, plan.state, tuple(rows), (glob,), readiness, (*plan.errors, *errors)
    )


def _decisions(store: WorkspaceStore) -> tuple[Mapping[str, Measure], list[str]]:
    try:
        document = load_measures(store)
    except MeasuresFileError as exc:
        return {}, exc.errors
    return {m.id: m for m in document.measures} if document else {}, []


def link_supports(
    store: WorkspaceStore,
    row: MatrixRowSpec,
    entry: MatrixEntry,
    runs: tuple[CheckRun, ...],
    snapshot: DocumentsSnapshot,
    at: datetime,
    problems: ConnectorProblems = MappingProxyType({}),
) -> list[LinkSupport]:
    """Lo que cada enlace sustenta en `at`: la única agregación de fuentes (`assess` y
    `prepare_review`). No compara con revisiones anteriores: una revisión nueva se registra en
    `at`, así que toda fuente existente es anterior a su registro y no bloquea re-revisar tras
    importar evidencia nueva. `assess` añade `_sustains_review` sobre la revisión vigente."""
    found: list[LinkSupport] = []
    for link in entry.links:
        match link:
            case ManualEvidenceLink():
                found.append(manual_support(store, row, link, at, entry.review_every_days))
            case CheckLink():
                found.append(technical_support(store, row, link, runs, at, problems))
            case DocumentLink():
                found.append(document_support(snapshot, row, link, at))
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
        if manifest is None or (
            manifest.artifact.sha256,
            manifest_sha256(manifest),
        ) != (reviewed.artifact_sha256, reviewed.manifest_sha256):
            detail = f"evidence_changed:{reviewed.evidence_id}"
            changed.append(_gap(BlockerKind.EXPIRED_REVIEW, measure_id, detail))
    for reviewed_doc in {d.name: d for o in record.obligations for d in o.documents}.values():
        current = snapshot.documents.get(reviewed_doc.name)
        if current is None or current.document_sha256 != reviewed_doc.document_sha256:
            detail = f"document_changed:{reviewed_doc.name}"
            changed.append(_gap(BlockerKind.EXPIRED_REVIEW, measure_id, detail))
    return changed


@dataclass(frozen=True)
class Signings:
    """Todas las Declaraciones firmadas (las archivadas y la vigente) y las copias ilegibles."""

    declarations: tuple[Declaration, ...] = ()
    unreadable: tuple[str, ...] = ()

    def contexts_since(self, measure_id: str, since: datetime) -> set[str | None]:
        """Contexto de la medida en cada firma posterior a `since`; None si ya no figuraba."""
        contexts: set[str | None] = set()
        for declaration in self.declarations:
            if declaration.approved_at <= since:
                continue
            try:
                contexts.add(measure_context_sha256(declaration, measure_id))
            except LookupError:
                contexts.add(None)
        return contexts


def read_signings(store: WorkspaceStore, current: Declaration | None) -> Signings:
    """El historial archivado al firmar más la vigente (que puede ser anterior al archivo)."""
    archived, unreadable = declaration_history(store)
    declarations = [*archived, *([current] if current and current not in archived else [])]
    return Signings(tuple(sorted(declarations, key=lambda d: d.approved_at)), tuple(unreadable))


def _review_gaps(
    store: WorkspaceStore,
    row: MatrixRowSpec,
    entry: MatrixEntry,
    stored: StoredReview | None,
    snapshot: DocumentsSnapshot,
    signings: Signings,
    history: ChangeScan,
) -> list[Blocker]:
    selected = row.kind is RowKind.SELECTED
    if stored is None:
        # La regla compartida ya señala la falta de revisión en las no aplicables.
        missing = _gap(BlockerKind.MISSING_REVIEW, row.measure_id, "review_missing")
        return [missing] if selected else []
    record = stored.record
    gaps: list[Blocker] = []
    axis_matches = isinstance(record, MeasureReviewRecord) is selected
    # Una revisión solo vale si, entre su registro y ahora, la medida no tuvo otro contexto en
    # ninguna firma de la Declaración: volver al contexto original no la recupera.
    contexts = signings.contexts_since(row.measure_id, record.recorded_at)
    if (
        not axis_matches
        or record.measure_context_sha256 != row.measure_context_sha256
        or contexts - {record.measure_context_sha256}
    ):
        gaps.append(_gap(BlockerKind.EXPIRED_REVIEW, row.measure_id, "context_changed"))
    gaps += [
        _gap(BlockerKind.MISSING_REVIEW, row.measure_id, f"declaration_history_unreadable:{key}")
        for key in signings.unreadable
    ]
    if selected and isinstance(record, MeasureReviewRecord):
        # Quitar y volver a poner el mismo enlace deja el mismo fichero pero no la misma historia.
        if record.links_sha256 != links_sha256(entry) or history.links_changed_since(
            record.recorded_at, record.links_sha256
        ):
            gaps.append(_gap(BlockerKind.EXPIRED_REVIEW, row.measure_id, "links_changed"))
        gaps += _changed_sources(store, record, snapshot, row.measure_id)
    return gaps


class Uncovered(NamedTuple):
    obligation: str
    unit: str
    is_clause: bool


def covered_units(supports: Iterable[LinkSupport]) -> frozenset[str]:
    """Unidades que cubren las fuentes que cuentan: la única definición de cobertura."""
    return frozenset().union(*(s.clauses for s in supports if s.counts))


def uncovered_units(row: MatrixRowSpec, covered: frozenset[str]) -> list[Uncovered]:
    """Unidades de la medida (cláusulas con id o la obligación entera) que ninguna fuente cubre."""
    return [
        Uncovered(o.id, unit, bool(o.clause_ids))
        for o in row.obligations
        for unit in o.units
        if unit not in covered
    ]


def _coverage_gaps(row: MatrixRowSpec, covered: frozenset[str]) -> list[Blocker]:
    return [
        _gap(
            BlockerKind.MISSING_EVIDENCE,
            row.measure_id,
            f"{'clause' if u.is_clause else 'obligation'}:{u.unit}",
        )
        for u in uncovered_units(row, covered)
    ]


def _latest(*stored: StoredReview | None) -> StoredReview | None:
    """La revisión más reciente de cualquier eje: una afirmación humana posterior sustituye a la
    anterior aunque sea de otro eje (ADR 0002)."""
    return max((s for s in stored if s), key=lambda s: (s.record.recorded_at, s.key), default=None)


def _assess_row(
    store: WorkspaceStore,
    row: MatrixRowSpec,
    entry: MatrixEntry | None,
    decision: Measure | None,
    runs: tuple[CheckRun, ...],
    problems: ConnectorProblems,
    snapshot: DocumentsSnapshot,
    signings: Signings,
    now: datetime,
) -> tuple[AssessmentRow, list[Blocker], list[str]]:
    selected = row.kind is RowKind.SELECTED
    entry = entry or MatrixEntry(id=row.measure_id)
    reviews = effective_reviews(store, row.measure_id, now)
    stored = _latest(reviews.implementation, reviews.applicability)
    history = scan_history(store, row.measure_id)
    # Un registro ilegible podría revocar lo anterior: lo legible se muestra pero no satisface.
    unreadable = [
        _gap(BlockerKind.MISSING_REVIEW, row.measure_id, f"review_unreadable:{key}")
        for key in reviews.unreadable
    ] + [
        _gap(BlockerKind.MISSING_REVIEW, row.measure_id, f"matrix_history_unreadable:{key}")
        for key in history.unreadable
    ]
    edited = history.edited(entry)
    record = stored.record if stored and not unreadable else None
    implementation = record if selected and isinstance(record, MeasureReviewRecord) else None

    supports = (
        [
            _sustains_review(s, row, implementation)
            for s in link_supports(store, row, entry, runs, snapshot, now, problems)
        ]
        if selected
        else []
    )
    covered = covered_units(supports)

    gaps: list[Blocker] = [*unreadable]
    if edited:
        gaps.append(
            _gap(BlockerKind.MISSING_REVIEW, row.measure_id, f"matrix_edited:{row.measure_id}")
        )
    if entry.owner is None:
        gaps.append(_gap(BlockerKind.MISSING_REVIEW, row.measure_id, "owner_missing"))
    if entry.review_every_days is None:
        gaps.append(_gap(BlockerKind.MISSING_REVIEW, row.measure_id, "review_every_days_missing"))
    gaps += _review_gaps(store, row, entry, stored, snapshot, signings, history)
    gaps += _coverage_gaps(row, covered)
    gaps += [b for s in supports for b in s.blockers]

    base = decision or Measure(id=row.measure_id, applicability=Applicability.UNDETERMINED)
    measure = Measure.model_validate(
        {
            **base.model_dump(),
            "applicability": Applicability.APPLICABLE if selected else Applicability.NOT_APPLICABLE,
            "assessment": implementation.assessment
            if implementation
            else MeasureAssessment.NOT_ASSESSED,
            "review": effective_review(record.review, entry.review_every_days) if record else None,
            "evidence": _evidence_refs(supports),
        }
    )
    unique_gaps = unique_blockers(gaps)
    blockers = tuple(unique_blockers([*measure_blockers(measure, now), *unique_gaps]))
    assessed = AssessmentRow(row, measure, blockers, entry, stored, tuple(supports), covered)
    edits = (
        [
            f"controls/matrix.yaml: la entrada de {row.measure_id} no coincide con el último "
            "estado registrado; edita con la CLI (matrix set, link, unlink)"
        ]
        if edited
        else []
    )
    return assessed, unique_gaps, [*reviews.errors, *history.errors, *edits]


def assess(
    store: WorkspaceStore,
    now: datetime,
    *,
    checks: CheckResultsSource | None = None,
    documents: OrgDocumentSource | None = None,
    findings: FindingsSource | None = None,
) -> AssessmentResult:
    """Filas, bloqueos de la matriz y `ReadinessResult` en `now`; único punto de integración."""
    plan = plan_obligations(store, now)
    if not plan.current:
        return _not_current(store, plan, now)

    matrix, errors = read_matrix(store)
    entries = {e.id: e for e in matrix.entries} if matrix else {}
    decisions, decision_errors = _decisions(store)
    errors = [*plan.errors, *errors, *decision_errors]
    runs, problems = load_checks(checks, store, now)
    errors += problem_errors(problems)
    snapshot = documents_snapshot(store, now, documents)
    errors += [
        f"{key}: registro de aprobación ilegible (documento {name})"
        for name, document in sorted(snapshot.documents.items())
        for key in document.unreadable
    ]
    signings = read_signings(store, plan.declaration)
    errors += [
        f"{key}: copia de una firma de la Declaración ilegible" for key in signings.unreadable
    ]

    rows: list[AssessmentRow] = []
    gaps: list[Blocker] = []
    for spec in plan.rows:
        assessed, row_gaps, review_errors = _assess_row(
            store,
            spec,
            entries.get(spec.measure_id),
            decisions.get(spec.measure_id),
            runs,
            problems,
            snapshot,
            signings,
            now,
        )
        rows.append(assessed)
        gaps += row_gaps
        errors += review_errors

    open_findings, judgment_executed = (findings or NoFindings()).open_findings(store, now)
    readiness = evaluate_readiness(
        ReadinessInput(
            measures=[r.measure for r in rows],
            open_findings=open_findings,
            judgment_executed=judgment_executed,
            evaluated_at=now,
            coverage_gaps=gaps,
        )
    )
    return AssessmentResult(now, plan.state, tuple(rows), tuple(gaps), readiness, tuple(errors))
