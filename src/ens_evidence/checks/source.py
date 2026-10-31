"""`CheckResultsSource` sobre el historial guardado (ADR 0012)."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from ens_evidence.checks.catalog import ENDPOINT_CONNECTOR, KNOWN_CHECKS
from ens_evidence.checks.inputs import (
    MISSING_RUN,
    config_asset_id,
    evidence_after_run,
    load_engine_config,
)
from ens_evidence.checks.model import StoredCheckRun
from ens_evidence.checks.registry import ENGINE_CONNECTORS
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


@dataclass(frozen=True)
class StoredCheckResults:
    """Las ejecuciones guardadas; los problemas del historial, del alcance, de la configuración y
    de la evidencia bloquean el conector. `reference_now` es el reloj de quien consulta
    (obligatorio: sin él se perdería el aviso de reloj adelantado): una ejecución con fecha
    posterior bloquea también en consultas con `--at`. Las ejecuciones que sustituyó una
    corrección del reloj no cuentan."""

    reference_now: datetime

    def runs(self, store: WorkspaceStore, at: datetime) -> Sequence[CheckRun]:
        scan = scan_runs(store)
        return [to_check_run(s.key, s.run) for c in scan.runs for s in scan.live(c)]

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
        for connector in scan.runs:
            if connector not in ENGINE_CONNECTORS:
                problems.setdefault(connector, []).append(f"check_connector_unknown:{connector}")
                continue
            live = scan.live(connector)
            in_future = any(s.run.finished_at > self.reference_now for s in live)
            if in_future:
                future = max(s.run.finished_at for s in live)
                problems.setdefault(connector, []).append(
                    f"check_run_in_future:{connector}:{future.astimezone(UTC):%Y-%m-%d %Hh%M UTC}"
                )
            by_key = {s.key: s.run for s in live}
            last = latest_run([to_check_run(s.key, s.run) for s in live], connector, at)
            if last is None:
                continue
            problem = _last_run_problem(store, connector, by_key[last.run_id], current, at)
            if problem and not (
                in_future and problem == MISSING_RUN
            ):  # la fecha futura ya lo explica
                problems.setdefault(connector, []).append(f"{problem}:{connector}")
        return problems


def _last_run_problem(
    store: WorkspaceStore, connector: str, run: StoredCheckRun, scope: str | None, at: datetime
) -> str | None:
    """El primer motivo por el que el último intento ya no vale: alcance, configuración, evidencia.
    La configuración se lee una vez; un fichero que falta o no se lee también cuenta como cambio,
    para que el bloqueo sea visible."""
    if run.scope_fingerprint != scope:
        return "check_scope_changed"
    loaded = load_engine_config(store, connector, ENGINE_CONNECTORS[connector].config_model)
    if loaded.sha256 is None:
        return "check_config_missing"
    if loaded.sha256 != run.config_sha256:
        return "check_config_changed"
    if loaded.config is None:
        return None  # sin configuración válida no hay selección posible
    asset_id = None if connector == ENDPOINT_CONNECTOR else config_asset_id(loaded.config)
    return evidence_after_run(store, connector, run, at, asset_id)
