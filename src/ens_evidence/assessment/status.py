"""`assessment status`: la preparación que calcula `assess` y qué ejecuciones técnicas la alimentan.

Solo lee. No decide nada: la regla de preparación y los bloqueos son de la matriz (regla 12).
"""

from dataclasses import dataclass
from datetime import datetime

from ens_evidence.checks.catalog import KNOWN_CHECKS
from ens_evidence.checks.source import StoredCheckResults
from ens_evidence.matrix import AssessmentResult, assess
from ens_evidence.matrix.technical import latest_run
from ens_evidence.models.enums import CheckStatus, Readiness
from ens_evidence.workspace import WorkspaceStore

STATUS_ORDER = (CheckStatus.PASS, CheckStatus.FAIL, CheckStatus.UNKNOWN, CheckStatus.ERROR)


@dataclass(frozen=True)
class ConnectorSummary:
    """La última ejecución del conector en o antes de `at`; `finished_at` None si no hay."""

    connector: str
    finished_at: datetime | None
    complete: bool
    counts: dict[CheckStatus, int]


@dataclass(frozen=True)
class AssessmentStatus:
    result: AssessmentResult
    connectors: tuple[ConnectorSummary, ...]

    @property
    def pending_human_review(self) -> bool:
        return self.result.readiness.pending_human_review

    @property
    def is_ready(self) -> bool:
        return self.result.readiness.readiness is Readiness.READY and not self.result.errors


def assessment_status(store: WorkspaceStore, at: datetime) -> AssessmentStatus:
    checks = StoredCheckResults()
    result = assess(store, at, checks=checks)
    runs = checks.runs(store, at)
    summaries = []
    for connector in sorted(set(KNOWN_CHECKS.values())):
        run = latest_run(runs, connector, at)
        counts = dict.fromkeys(STATUS_ORDER, 0)
        for item in run.results if run else ():
            counts[item.status] += 1
        summaries.append(
            ConnectorSummary(
                connector,
                run.finished_at if run else None,
                run.complete if run else False,
                counts,
            )
        )
    return AssessmentStatus(result, tuple(summaries))
