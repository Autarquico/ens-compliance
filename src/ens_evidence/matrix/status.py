"""Estado de cada fila de la matriz tal como lo presentan `status`, la exportación y la vista de la
Declaración. Solo lee una `AssessmentResult`; no decide nada (regla 12)."""

from dataclasses import dataclass
from datetime import datetime

from ens_evidence.matrix.documents import OrgDocumentSource
from ens_evidence.matrix.evaluate import AssessmentResult, AssessmentRow, assess
from ens_evidence.matrix.links import link_label
from ens_evidence.matrix.model import CheckLink
from ens_evidence.matrix.obligations import RowKind
from ens_evidence.matrix.technical import CheckResultsSource
from ens_evidence.models.enums import MeasureAssessment
from ens_evidence.models.readiness import Blocker, BlockerKind
from ens_evidence.workspace import WorkspaceStore

CHECKS_PENDING = "evidencia técnica: pendiente del motor de checks"
IRREGULAR_REVIEW_BLOCKERS = {
    BlockerKind.MISSING_REVIEW,
    BlockerKind.EXPIRED_REVIEW,
    BlockerKind.UNDETERMINED_APPLICABILITY,
}


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
    periodicidad y revisión vigente: sin bloqueos de revisión, y con la Declaración vigente."""
    selected = row.spec.kind is RowKind.SELECTED
    implemented = (
        selected and row.measure.assessment is MeasureAssessment.COMPLIANT and not row.blockers
    )
    regular = row.review is not None and not any(
        b.kind in IRREGULAR_REVIEW_BLOCKERS for b in row.blockers
    )
    links = row.entry.links if row.entry else []
    return RowStatus(
        row,
        implemented,
        regular,
        tuple(link_label(s.link) for s in row.supports if s.counts),
        any(isinstance(link, CheckLink) for link in links),
    )


def matrix_status(
    store: WorkspaceStore,
    at: datetime,
    *,
    checks: CheckResultsSource | None = None,
    documents: OrgDocumentSource | None = None,
) -> tuple[AssessmentResult, tuple[RowStatus, ...]]:
    result = assess(store, at, checks=checks, documents=documents)
    return result, tuple(row_status(r) for r in result.rows)
