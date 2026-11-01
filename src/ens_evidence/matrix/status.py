"""Estado de cada fila de la matriz tal como lo presentan `status`, la exportación y la vista de la
Declaración. Solo lee una `AssessmentResult`; no decide nada (regla 12)."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from ens_evidence.checks.catalog import KNOWN_CHECKS
from ens_evidence.connectors.config import connector_config_key
from ens_evidence.matrix.documents import OrgDocumentSource
from ens_evidence.matrix.evaluate import AssessmentResult, AssessmentRow, assess
from ens_evidence.matrix.links import link_label
from ens_evidence.matrix.messages import with_simulated_mark
from ens_evidence.matrix.model import CheckLink
from ens_evidence.matrix.obligations import RowKind
from ens_evidence.matrix.technical import (
    CheckResultsSource,
    CheckRun,
    StaticCheckResults,
    latest_run,
    load_checks,
)
from ens_evidence.models.enums import MeasureAssessment
from ens_evidence.models.readiness import Blocker, BlockerKind
from ens_evidence.workspace import WorkspaceStore

NO_CHECK_RUN = "sin ejecución; ejecuta `ens-evidence check --all`"
CONNECTOR_NOT_CONFIGURED = "conector no configurado; configúralo si lo usas o desvincula el check"
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
    incident_history: bool
    check_lines: tuple[str, ...]

    @property
    def blockers(self) -> tuple[Blocker, ...]:
        return self.row.blockers


def check_line(
    link: CheckLink, runs: Sequence[CheckRun], at: datetime, configured: bool = True
) -> str:
    """Qué ejecución del motor respalda (o no) un enlace a un check."""
    connector = KNOWN_CHECKS.get(link.check_id)
    run = latest_run(runs, connector, at) if connector else None
    if run is not None:
        detail = f"ejecución de {run.finished_at.isoformat()}"
    else:
        detail = NO_CHECK_RUN if configured else CONNECTOR_NOT_CONFIGURED
    return f"{link_label(link)}: {detail}"


def _is_configured(store: WorkspaceStore, link: CheckLink) -> bool:
    connector = KNOWN_CHECKS.get(link.check_id)
    return connector is None or store.exists(connector_config_key(connector))


def row_status(row: AssessmentRow, check_lines: tuple[str, ...] = ()) -> RowStatus:
    """Implantada: revisión COMPLIANT sin ningún bloqueo. Revisión regular: responsable,
    periodicidad y revisión vigente: sin bloqueos de revisión, y con la Declaración vigente."""
    selected = row.spec.kind is RowKind.SELECTED
    implemented = (
        selected and row.measure.assessment is MeasureAssessment.COMPLIANT and not row.blockers
    )
    regular = row.review is not None and not any(
        b.kind in IRREGULAR_REVIEW_BLOCKERS for b in row.blockers
    )
    return RowStatus(
        row,
        implemented,
        regular,
        tuple(
            with_simulated_mark(link_label(s.link), s.simulated) for s in row.supports if s.counts
        ),
        any(s.incident_history for s in row.supports if s.counts),
        check_lines,
    )


def matrix_status(
    store: WorkspaceStore,
    at: datetime,
    *,
    checks: CheckResultsSource | None = None,
    documents: OrgDocumentSource | None = None,
) -> tuple[AssessmentResult, tuple[RowStatus, ...]]:
    runs, problems = load_checks(checks, store, at)
    result = assess(
        store, at, checks=StaticCheckResults(runs, problems=problems), documents=documents
    )

    def lines(row: AssessmentRow) -> tuple[str, ...]:
        links = row.entry.links if row.entry else []
        return tuple(
            check_line(link, runs, at, _is_configured(store, link))
            for link in links
            if isinstance(link, CheckLink)
        )

    return result, tuple(row_status(r, lines(r)) for r in result.rows)
