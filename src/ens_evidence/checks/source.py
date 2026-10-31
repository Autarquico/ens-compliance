"""`CheckResultsSource` sobre el historial guardado (ADR 0012)."""

from collections.abc import Sequence
from datetime import datetime

from ens_evidence.checks.catalog import KNOWN_CHECKS
from ens_evidence.checks.model import StoredCheckRun
from ens_evidence.checks.store import UNATTRIBUTED, scan_runs
from ens_evidence.matrix.technical import CheckRun, ConnectorProblems, latest_run
from ens_evidence.scope import ScopeState, scope_status
from ens_evidence.workspace import WorkspaceStore


def to_check_run(key: str, run: StoredCheckRun) -> CheckRun:
    return CheckRun(
        run_id=key,
        connector=run.connector,
        started_at=run.started_at,
        finished_at=run.finished_at,
        complete=run.complete,
        results=tuple(run.results),
        errors=tuple(run.errors),
        expected_by_check={check: frozenset(s) for check, s in run.expected_subjects.items()},
        evidence_sha256={i.evidence_id: i.manifest_sha256 for i in run.inputs},
    )


class StoredCheckResults:
    """Las ejecuciones guardadas; los problemas del historial y del alcance bloquean el conector."""

    def runs(self, store: WorkspaceStore, at: datetime) -> Sequence[CheckRun]:
        scan = scan_runs(store)
        return [to_check_run(s.key, s.run) for items in scan.runs.values() for s in items]

    def connector_problems(self, store: WorkspaceStore, at: datetime) -> ConnectorProblems:
        scan = scan_runs(store)
        problems: dict[str, list[str]] = {}
        everywhere = scan.unreadable.get(UNATTRIBUTED, ())
        for connector in {*scan.runs, *scan.unreadable, *KNOWN_CHECKS.values()} - {UNATTRIBUTED}:
            keys = (*scan.unreadable.get(connector, ()), *everywhere)
            if keys:
                problems[connector] = [f"check_history_unreadable:{k}" for k in keys]
        status = scope_status(store)
        current = status.fingerprint if status.state is ScopeState.APPROVED else None
        for connector, items in scan.runs.items():
            fingerprints = {s.key: s.run.scope_fingerprint for s in items}
            last = latest_run([to_check_run(s.key, s.run) for s in items], connector, at)
            if last is not None and fingerprints[last.run_id] != current:
                problems.setdefault(connector, []).append(f"check_scope_changed:{connector}")
        return problems
