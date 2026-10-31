"""Exportación de la matriz de cumplimiento (regla 11: fuentes y timestamps).

`build_export` congela una `AssessmentResult` en un `MatrixExport` con todo lo que permite
reconstruir de dónde sale cada fila: fuentes por evidencia, check, documento y revisión. La
exportación falla si falta una fila de la Declaración (nunca sale una matriz con huecos). Con
las mismas entradas y el mismo `generated_at`, los bytes son idénticos. Nada de aquí declara
conformidad (regla 12): la valoración es de la persona que revisa.
"""

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Literal

from pydantic import AwareDatetime, Field

from ens_evidence import __version__
from ens_evidence.applicability import DeclarationState, declaration_status
from ens_evidence.catalog import load_catalog
from ens_evidence.catalog.controls import CatalogVersion, current_catalog_version
from ens_evidence.evidence.layout import manifest_key
from ens_evidence.evidence.manifests import ManifestError, load_manifest_by_id
from ens_evidence.matrix.evaluate import AssessmentResult, AssessmentRow
from ens_evidence.matrix.links import CHECK, DOCUMENT, EVIDENCE, source_label
from ens_evidence.matrix.messages import (
    ASSESSMENT_LABELS,
    CATEGORY_LABELS,
    DECLARATION_LABELS,
    DECLARATION_REASONS,
    READINESS_LABELS,
    ROW_KIND_LABELS,
    SIMULATED_LABEL,
    blocker_lines,
    blocker_text,
    obligation_label,
    role_label,
)
from ens_evidence.matrix.model import (
    CheckLink,
    ManualEvidenceLink,
    MeasureReviewRecord,
    Owner,
    ReviewedObligation,
)
from ens_evidence.matrix.obligations import COMPENSATION, RowKind
from ens_evidence.matrix.status import RowStatus, row_status
from ens_evidence.matrix.store import MATRIX_KEY
from ens_evidence.matrix.technical import KNOWN_CHECKS
from ens_evidence.models.contracts import Provenance, Strict
from ens_evidence.models.enums import Category, MeasureAssessment, Readiness, Role
from ens_evidence.models.readiness import Blocker
from ens_evidence.workspace import WorkspaceStore

MATRIX_REPORTS_PREFIX = "evidence/reports/matrix"
DECLARATION_REPORTS_PREFIX = "evidence/reports/declaration"
DISCLAIMER = "La herramienta no declara conformidad; la valoración es de la persona que revisa."

_MARKDOWN_SPECIALS = re.compile(r"([\\`*_\[\]<>|~&#])")


class ExportRejected(Exception):
    """No se puede exportar; `errors` dice por qué, una razón por línea."""

    def __init__(self, *errors: str) -> None:
        super().__init__("; ".join(errors))
        self.errors = list(errors)


class ExportedDeclaration(Strict):
    state: DeclarationState
    approved_by: str
    role: Role
    approved_at: AwareDatetime
    category: Category
    catalog_version: CatalogVersion
    measures_sha256: str
    categorization_sha256: str
    scope_fingerprint: str


class ExportedLink(Strict):
    kind: Literal["evidence", "check", "org_document"]
    target: str
    covers: list[str]
    counts: bool
    simulated: bool = Field(
        default=False, description="Historial de un incidente de simulacro: no es un incidente."
    )


class ExportedObligation(Strict):
    id: str
    clauses: list[str]
    replaces: list[str]
    compensating_measures: list[str]


class ExportedReview(Strict):
    key: str
    axis: Literal["implementation", "applicability"]
    assessment: MeasureAssessment | None
    reviewer: str
    role: Role
    conclusion: str
    reviewed_at: AwareDatetime
    valid_until: AwareDatetime
    recorded_at: AwareDatetime
    record_sha256: str | None
    obligations: list[ReviewedObligation]


class ExportedRow(Strict):
    measure_id: str
    name: str
    kind: RowKind
    obligations: list[ExportedObligation]
    compensated_clauses: list[str]
    technical: list[ExportedLink]
    manual: list[ExportedLink]
    owner: Owner | None
    review_every_days: int | None
    last_review: ExportedReview | None
    implemented: bool
    review_current: bool
    blockers: list[Blocker]


class EvidenceSource(Strict):
    id: str
    manifest_key: str
    source: str
    artifact_sha256: str
    provenance: Provenance
    collected_at: AwareDatetime
    valid_until: AwareDatetime | None


class CheckSource(Strict):
    check_id: str
    connector: str | None
    measures: list[str]
    last_run_at: AwareDatetime | None
    evidence_ids: list[str]


class DocumentSource(Strict):
    name: str
    document_sha256: str
    approved_at: AwareDatetime | None
    measures: list[str]


class ReviewSource(Strict):
    measure_id: str
    key: str


class ExportSources(Strict):
    evidence: list[EvidenceSource]
    checks: list[CheckSource]
    documents: list[DocumentSource]
    reviews: list[ReviewSource]


class MatrixExport(Strict):
    generated_at: AwareDatetime
    evaluated_at: AwareDatetime
    product_version: str
    catalog_version: CatalogVersion
    declaration: ExportedDeclaration
    matrix_sha256: str | None
    readiness: Readiness
    blockers: list[Blocker]
    rows: list[ExportedRow]
    sources: ExportSources
    errors: list[str] = Field(
        default_factory=list,
        description="Ficheros ilegibles al evaluar; cada uno bloquea lo que afecta.",
    )
    disclaimer: str = DISCLAIMER


# --- construcción -----------------------------------------------------------------------------


def _blocker_key(blocker: Blocker) -> tuple[str, str, str]:
    return (blocker.measure_id or "", blocker.kind.value, blocker.detail)


def _links(row: AssessmentRow, kinds: set[str]) -> list[ExportedLink]:
    supports = {s.link.target: s for s in row.supports}
    links = row.entry.links if row.entry else []
    return [
        ExportedLink(
            kind=link.kind,
            target=link.target[1],
            covers=list(link.covers),
            counts=link.target in supports and supports[link.target].counts,
            simulated=link.target in supports and supports[link.target].simulated,
        )
        for link in links
        if link.kind in kinds
    ]


def _exported_review(row: AssessmentRow, store: WorkspaceStore | None) -> ExportedReview | None:
    stored = row.review
    if stored is None:
        return None
    record = stored.record
    effective = row.measure.review or record.review
    is_implementation = isinstance(record, MeasureReviewRecord)
    return ExportedReview(
        key=stored.key,
        axis=record.axis,
        assessment=record.assessment if is_implementation else None,
        reviewer=record.review.reviewer,
        role=record.review.role,
        conclusion=record.review.conclusion,
        reviewed_at=record.review.reviewed_at,
        valid_until=effective.valid_until,
        recorded_at=record.recorded_at,
        record_sha256=hashlib.sha256(store.read_bytes(stored.key)).hexdigest() if store else None,
        obligations=list(record.obligations) if is_implementation else [],
    )


def export_row(status: RowStatus, name: str, store: WorkspaceStore | None = None) -> ExportedRow:
    row = status.row
    return ExportedRow(
        measure_id=row.spec.measure_id,
        name=name,
        kind=row.spec.kind,
        obligations=[
            ExportedObligation(
                id=o.id,
                clauses=list(o.clause_ids),
                replaces=list(o.replaces),
                compensating_measures=list(o.compensating_measures),
            )
            for o in row.spec.obligations
        ],
        compensated_clauses=sorted(row.spec.compensated_clauses),
        technical=_links(row, {CHECK}),
        manual=_links(row, {EVIDENCE, DOCUMENT}),
        owner=row.entry.owner if row.entry else None,
        review_every_days=row.entry.review_every_days if row.entry else None,
        last_review=_exported_review(row, store),
        implemented=status.implemented,
        review_current=status.regularly_reviewed,
        blockers=sorted(status.blockers, key=_blocker_key),
    )


def _declaration_rows(store: WorkspaceStore, at: datetime):
    status = declaration_status(store, at)
    declaration = status.declaration
    if declaration is None:
        raise ExportRejected(
            f"No hay Declaración de Aplicabilidad legible ({status.state.value}): "
            "no hay filas que exportar.",
            *status.errors,
        )
    expected = [
        *(m.id for m in declaration.selected),
        *(m.id for m in declaration.not_selected),
        *(m.id for m in declaration.required_but_absent_assets),
    ]
    return status.state, declaration, expected


def _check_complete(expected: list[str], rows: tuple[AssessmentRow, ...]) -> None:
    found = [r.spec.measure_id for r in rows]
    missing = sorted(set(expected) - set(found))
    repeated = sorted({i for i in found if found.count(i) > 1})
    extra = sorted(set(found) - set(expected))
    problems = [
        *(f"{i}: falta la fila de la Declaración." for i in missing),
        *(f"{i}: fila duplicada." for i in repeated),
        *(f"{i}: fila que no está en la Declaración." for i in extra),
    ]
    if problems:
        raise ExportRejected(*problems)


def _evidence_source(store: WorkspaceStore, evidence_id: str) -> EvidenceSource | None:
    try:
        manifest = load_manifest_by_id(store, evidence_id)
    except ManifestError:
        return None
    return EvidenceSource(
        id=manifest.id,
        manifest_key=manifest_key(manifest.id),
        source=manifest.source,
        artifact_sha256=manifest.artifact.sha256,
        provenance=manifest.provenance,
        collected_at=manifest.collected_at,
        valid_until=manifest.valid_until,
    )


def _build_sources(store: WorkspaceStore, rows: tuple[AssessmentRow, ...]) -> ExportSources:
    evidence_ids: set[str] = set()
    checks: dict[str, dict] = {}
    documents: dict[tuple[str, str], dict] = {}
    reviews: list[ReviewSource] = []
    for row in rows:
        measure = row.spec.measure_id
        for support in row.supports:
            link = support.link
            if isinstance(link, ManualEvidenceLink):
                evidence_ids.add(link.evidence_id)
            elif isinstance(link, CheckLink):
                found = checks.setdefault(
                    link.check_id, {"measures": set(), "last_run_at": None, "evidence": set()}
                )
                found["measures"].add(measure)
                if support.counts:
                    found["evidence"].update(ref.id for ref in support.evidence)
                    evidence_ids.update(ref.id for ref in support.evidence)
                    runs = [t for t in (found["last_run_at"], support.available_at) if t]
                    found["last_run_at"] = max(runs, default=None)
            elif support.counts and support.reviewed_document is not None:
                found = documents.setdefault(
                    (link.document, support.reviewed_document.document_sha256),
                    {"approved_at": support.available_at, "measures": set()},
                )
                found["measures"].add(measure)
        if row.review is not None:
            reviews.append(ReviewSource(measure_id=measure, key=row.review.key))
            record = row.review.record
            if isinstance(record, MeasureReviewRecord):
                evidence_ids.update(e.evidence_id for o in record.obligations for e in o.evidence)
                for reviewed in (d for o in record.obligations for d in o.documents):
                    found = documents.setdefault(
                        (reviewed.name, reviewed.document_sha256),
                        {"approved_at": None, "measures": set()},
                    )
                    found["measures"].add(measure)
    sources = (_evidence_source(store, i) for i in sorted(evidence_ids))
    return ExportSources(
        evidence=[s for s in sources if s is not None],
        checks=[
            CheckSource(
                check_id=check_id,
                connector=KNOWN_CHECKS.get(check_id),
                measures=sorted(found["measures"]),
                last_run_at=found["last_run_at"],
                evidence_ids=sorted(found["evidence"]),
            )
            for check_id, found in sorted(checks.items())
        ],
        documents=[
            DocumentSource(
                name=name,
                document_sha256=sha,
                approved_at=found["approved_at"],
                measures=sorted(found["measures"]),
            )
            for (name, sha), found in sorted(documents.items())
        ],
        reviews=sorted(reviews, key=lambda r: (r.measure_id, r.key)),
    )


def _matrix_sha256(store: WorkspaceStore) -> str | None:
    if not store.exists(MATRIX_KEY):
        return None
    return hashlib.sha256(store.read_bytes(MATRIX_KEY)).hexdigest()


def build_export(
    store: WorkspaceStore, evaluation: AssessmentResult, *, generated_at: datetime
) -> MatrixExport:
    """Congela la evaluación; `ExportRejected` si falta o sobra alguna fila de la Declaración."""
    state, declaration, expected = _declaration_rows(store, evaluation.evaluated_at)
    _check_complete(expected, evaluation.rows)
    catalog = load_catalog()
    names = {m.id: m.name for m in catalog.measures}
    order = {m.id: position for position, m in enumerate(catalog.measures)}
    rows = sorted(
        (
            export_row(row_status(r), names.get(r.spec.measure_id, ""), store)
            for r in evaluation.rows
        ),
        key=lambda r: (order.get(r.measure_id, len(order)), r.measure_id),
    )
    return MatrixExport(
        generated_at=generated_at,
        evaluated_at=evaluation.evaluated_at,
        product_version=__version__,
        catalog_version=current_catalog_version(catalog),
        declaration=ExportedDeclaration(
            state=state,
            approved_by=declaration.approved_by,
            role=declaration.role,
            approved_at=declaration.approved_at,
            category=declaration.category,
            catalog_version=declaration.catalog_version,
            measures_sha256=declaration.measures_sha256,
            categorization_sha256=declaration.categorization_sha256,
            scope_fingerprint=declaration.scope_fingerprint,
        ),
        matrix_sha256=_matrix_sha256(store),
        readiness=evaluation.readiness.readiness,
        blockers=sorted(evaluation.readiness.blockers, key=_blocker_key),
        rows=rows,
        sources=_build_sources(store, evaluation.rows),
        errors=list(evaluation.errors),
    )


# --- render -----------------------------------------------------------------------------------


def md_escape(text: str) -> str:
    """Texto libre en una sola línea y sin sintaxis Markdown activa."""
    return _MARKDOWN_SPECIALS.sub(r"\\\1", " ".join(text.split()))


def render_json(export: MatrixExport) -> str:
    document = export.model_dump(mode="json")
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def _day(moment: datetime) -> str:
    return moment.astimezone(UTC).date().isoformat()


def _instant(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def _owner_text(owner: Owner | None) -> str:
    return "—" if owner is None else f"{md_escape(owner.name)} ({role_label(owner.role)})"


def _links_text(links: list[ExportedLink], *, evaluated: bool) -> str:
    """Sin Declaración vigente las fuentes no se evalúan: «no evaluada», no «no cuenta»."""
    if not links:
        return "—"
    unused = " (no cuenta)" if evaluated else " (no evaluada)"
    return "; ".join(
        md_escape(link.target)
        + _simulated_mark(link.simulated)
        + f" (cubre {md_escape(', '.join(obligation_label(c) for c in link.covers))})"
        + ("" if link.counts else unused)
        for link in links
    )


def review_text(review: ExportedReview | None) -> str:
    if review is None:
        return "sin revisar"
    value = (
        f" · {ASSESSMENT_LABELS[review.assessment]}"
        if review.assessment
        else " · decisión de no aplicabilidad"
    )
    who = f"{md_escape(review.reviewer)} ({role_label(review.role)})"
    return f"{who}, {_day(review.reviewed_at)}{value}"


def requirements_text(row: ExportedRow) -> str:
    if not row.obligations:
        return "—"
    parts = []
    for obligation in row.obligations:
        clauses = ", ".join(obligation.clauses) or "sin cláusulas con id"
        compensated = (
            f" (medidas compensatorias: {'; '.join(obligation.compensating_measures)})"
            if obligation.id == COMPENSATION and obligation.compensating_measures
            else ""
        )
        parts.append(f"{obligation_label(obligation.id)}: {clauses}{compensated}")
    return md_escape("; ".join(parts))


def blockers_text(row: ExportedRow) -> str:
    if not row.blockers:
        return "—"
    return md_escape("; ".join(blocker_lines(row.blockers, frozenset(row.compensated_clauses))))


def _yes_no(row: ExportedRow, value: bool, *, applies_to_selected_only: bool = False) -> str:
    if applies_to_selected_only and row.kind is not RowKind.SELECTED:
        return "n/a"
    return "sí" if value else "no"


def _row_line(row: ExportedRow, *, evaluated: bool) -> str:
    suffix = "" if row.kind is RowKind.SELECTED else f" ({ROW_KIND_LABELS[row.kind]})"
    cells = [
        f"{md_escape(row.measure_id)} {md_escape(row.name)}{suffix}",
        requirements_text(row),
        _links_text(row.technical, evaluated=evaluated),
        _links_text(row.manual, evaluated=evaluated),
        _owner_text(row.owner),
        "—" if row.review_every_days is None else f"{row.review_every_days} días",
        review_text(row.last_review),
        _yes_no(row, row.implemented, applies_to_selected_only=True),
        _yes_no(row, row.review_current),
        blockers_text(row),
    ]
    return "| " + " | ".join(cells) + " |"


TABLE_HEADER = (
    "| Medida | Requisitos (cláusulas) | Evidencias técnicas | Evidencias manuales y documentos "
    "| Responsable | Periodicidad | Última revisión | ¿Implantada? | ¿Revisión vigente? "
    "| Bloqueos |\n" + "|" + "---|" * 10
)


def _source_lines(sources: ExportSources, rows: list[ExportedRow]) -> list[str]:
    simulated = {link.target for row in rows for link in row.manual if link.simulated}
    lines = ["## Fuentes", "", "### Evidencias", ""]
    for e in sources.evidence:
        valid = (
            f"válida hasta {_instant(e.valid_until)}"
            if e.valid_until
            else "sin fecha de caducidad (cuenta dentro de la periodicidad de la medida)"
        )
        lines.append(
            f"- {e.id}{_simulated_mark(e.id in simulated)} · manifiesto `{e.manifest_key}` · "
            f"origen {md_escape(e.source)} · "
            f"sha256 `{e.artifact_sha256}` · procedencia {e.provenance.kind.value} "
            f"({md_escape(e.provenance.submitted_by)}, {_instant(e.provenance.submitted_at)}) · "
            f"recogida {_instant(e.collected_at)} · {valid}"
        )
    lines += ["", "### Checks", ""]
    for c in sources.checks:
        run = _instant(c.last_run_at) if c.last_run_at else "sin ejecución que cuente"
        lines.append(
            f"- {md_escape(c.check_id)} · conector {md_escape(c.connector or 'desconocido')} · "
            f"última ejecución {run} · medidas {', '.join(c.measures)}"
            + (f" · evidencias {', '.join(c.evidence_ids)}" if c.evidence_ids else "")
        )
    lines += ["", "### Documentos", ""]
    for d in sources.documents:
        approved = _instant(d.approved_at) if d.approved_at else "sin aprobar"
        lines.append(
            f"- {md_escape(d.name)} · sha256 `{d.document_sha256}` · aprobado {approved} · "
            f"medidas {', '.join(d.measures)}"
        )
    lines += ["", "### Revisiones", ""]
    for review_row in rows:
        review = review_row.last_review
        if review is not None:
            marked = {link.target for link in review_row.manual if link.simulated}
            lines += _review_lines(review_row.measure_id, review, marked)
    return lines


def _simulated_mark(simulated: bool) -> str:
    return f" ({SIMULATED_LABEL})" if simulated else ""


def _reviewed_source(code: str, simulated: set[str]) -> str:
    kind, _, target = code.partition(":")
    return source_label(kind, target) + _simulated_mark(target in simulated)


def _review_lines(measure_id: str, review: ExportedReview, simulated: set[str]) -> list[str]:
    sha = f"`{review.record_sha256}`" if review.record_sha256 else "sin huella"
    lines = [
        f"- {measure_id} · registro `{review.key}` · sha256 {sha} · {review_text(review)} · "
        f"conclusión: {md_escape(review.conclusion)}"
    ]
    for obligation in review.obligations:
        lines.append(f"  - {md_escape(obligation_label(obligation.obligation))}: fuentes revisadas")
        for clause in obligation.coverage:
            sources = (
                ", ".join(_reviewed_source(code, simulated) for code in clause.sources)
                or "sin fuente"
            )
            lines.append(f"    - cláusula {md_escape(clause.clause)} ← {md_escape(sources)}")
    return lines


def _pending(export: MatrixExport) -> list[tuple[str | None, str]]:
    """Bloqueos para personas: sin repetir el texto de cada medida y en el orden del catálogo."""
    order = {row.measure_id: i for i, row in enumerate(export.rows)}
    texts = {(b.measure_id, blocker_text(b)) for b in export.blockers}
    return sorted(texts, key=lambda t: (order.get(t[0], -1), t[0] or "", t[1]))


def _readiness_line(export: MatrixExport) -> str:
    if export.declaration.state is not DeclarationState.CURRENT:
        return (
            f"- Preparación: {READINESS_LABELS[export.readiness]}; todas las medidas quedan "
            "bloqueadas: la Declaración de Aplicabilidad no está vigente: "
            f"{DECLARATION_REASONS[export.declaration.state]}"
        )
    count = len(_pending(export))
    noun = "bloqueo" if count == 1 else "bloqueos"
    return f"- Preparación: {READINESS_LABELS[export.readiness]}, {count} {noun}"


def render_markdown(export: MatrixExport) -> str:
    d = export.declaration
    matrix_sha = f"`{export.matrix_sha256}`" if export.matrix_sha256 else "sin matriz"
    lines = [
        "# Matriz de cumplimiento por medida",
        "",
        f"- Generada: {_instant(export.generated_at)}",
        f"- Evaluada: {_instant(export.evaluated_at)}",
        f"- Versión del producto: {export.product_version}",
        f"- Catálogo actual: consolidado el {export.catalog_version.consolidation_date}, "
        f"sha256 `{export.catalog_version.snapshot_sha256}`",
        f"- Declaración de Aplicabilidad: {DECLARATION_LABELS[d.state]}, firmada por "
        f"{md_escape(d.approved_by)} ({role_label(d.role)}) el {_instant(d.approved_at)}; "
        f"categoría {CATEGORY_LABELS[d.category]}",
        f"- Huellas de la Declaración: medidas `{d.measures_sha256}`, categorización "
        f"`{d.categorization_sha256}`, alcance `{d.scope_fingerprint}`",
        f"- Matriz (`controls/matrix.yaml`) sha256: {matrix_sha}",
        _readiness_line(export),
        "",
        f"> {export.disclaimer}",
        "",
        "## Medidas",
        "",
        TABLE_HEADER,
        *(
            _row_line(row, evaluated=export.declaration.state is DeclarationState.CURRENT)
            for row in export.rows
        ),
    ]
    if export.blockers:
        lines += ["", "## Bloqueos", ""]
        lines += [
            f"- {md_escape((measure_id or 'global') + ': ' + text)}"
            for measure_id, text in _pending(export)
        ]
    if export.errors:
        lines += ["", "## Errores de lectura", ""]
        lines += [f"- {md_escape(error)}" for error in export.errors]
    lines += ["", *_source_lines(export.sources, export.rows), "", export.disclaimer, ""]
    return "\n".join(lines)


# --- almacenamiento ---------------------------------------------------------------------------


def report_key(prefix: str, at: datetime, extension: str) -> str:
    stamp = at.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{prefix}/{stamp}.{extension}"


def save_report(store: WorkspaceStore, prefix: str, at: datetime, extension: str, text: str) -> str:
    """Copia nueva e independiente (ADR 0008): nunca sobrescribe. Devuelve la clave."""
    key = report_key(prefix, at, extension)
    store.create_bytes(key, text.encode("utf-8"))
    return key
