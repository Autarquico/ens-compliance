"""`assessment status`: la preparación que calcula `assess` y qué ejecuciones técnicas la alimentan.

Solo lee. No decide nada: la regla de preparación y los bloqueos son de la matriz (regla 12).
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from ens_evidence.checks.catalog import KNOWN_CHECKS
from ens_evidence.connectors.config import connector_config_key
from ens_evidence.matrix import AssessmentResult, assess
from ens_evidence.matrix.technical import (
    CheckResultsSource,
    StaticCheckResults,
    latest_run,
    load_checks,
)
from ens_evidence.models.contracts import CheckResult
from ens_evidence.models.enums import CheckStatus, Readiness
from ens_evidence.models.preaudit import PreAuditRecord
from ens_evidence.preaudit.findings import last_run
from ens_evidence.workspace import WorkspaceStore

STATUS_ORDER = (CheckStatus.PASS, CheckStatus.FAIL, CheckStatus.UNKNOWN, CheckStatus.ERROR)


def count_statuses(results: Iterable[CheckResult]) -> dict[CheckStatus, int]:
    counts = dict.fromkeys(STATUS_ORDER, 0)
    for result in results:
        counts[result.status] += 1
    return counts


@dataclass(frozen=True)
class ConnectorSummary:
    """La última ejecución del conector en o antes de `at`; `finished_at` None si no hay.
    `configured`: el workspace tiene configuración de ese conector."""

    connector: str
    configured: bool
    finished_at: datetime | None
    complete: bool
    counts: dict[CheckStatus, int]


@dataclass(frozen=True)
class AssessmentStatus:
    result: AssessmentResult
    connectors: tuple[ConnectorSummary, ...]
    pre_audit: PreAuditRecord | None = None
    """La última pre-auditoría sobre el expediente vigente (None: ninguna)."""

    @property
    def pending_human_review(self) -> bool:
        return self.result.readiness.pending_human_review

    @property
    def is_ready(self) -> bool:
        return self.result.readiness.readiness is Readiness.READY and not self.result.errors


def assessment_status(
    store: WorkspaceStore, at: datetime, checks: CheckResultsSource
) -> AssessmentStatus:
    """`checks`: la misma fuente que usan el resto de comandos (la construye la CLI)."""
    runs, problems = load_checks(checks, store, at)
    result = assess(store, at, checks=StaticCheckResults(runs, problems=problems))
    summaries = []
    for connector in sorted(set(KNOWN_CHECKS.values())):
        run = latest_run(runs, connector, at)
        summaries.append(
            ConnectorSummary(
                connector,
                store.exists(connector_config_key(connector)),
                run.finished_at if run else None,
                run.complete if run else False,
                count_statuses(run.results if run else ()),
            )
        )
    return AssessmentStatus(result, tuple(summaries), last_run(store, at))
