"""`CheckResultsSource` sobre el historial guardado (ADR 0012)."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from ens_evidence.checks.catalog import ENDPOINT_CONNECTOR, KNOWN_CHECKS
from ens_evidence.checks.inputs import config_asset_id, evidence_after_run, load_engine_config
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
    """Las ejecuciones guardadas; los problemas del historial, del alcance y de la configuración
    bloquean el conector. Con `reference_now` (el reloj de quien consulta), una ejecución con fecha
    posterior bloquea también en consultas con `--at`."""

    reference_now: datetime | None = None

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
            by_key = {s.key: s.run for s in items}
            last = latest_run([to_check_run(s.key, s.run) for s in items], connector, at)
            if last is None:
                continue
            stored = by_key[last.run_id]
            if stored.scope_fingerprint != current:
                problems.setdefault(connector, []).append(f"check_scope_changed:{connector}")
            elif _config_changed(store, connector, stored):
                problems.setdefault(connector, []).append(f"check_config_changed:{connector}")
            elif _evidence_after(store, connector, stored, at):
                problems.setdefault(connector, []).append(f"check_run_missing:{connector}")
        if self.reference_now is not None:
            for connector, items in scan.runs.items():
                if any(s.run.finished_at > self.reference_now for s in items):
                    problems.setdefault(connector, []).append(f"check_run_in_future:{connector}")
        return problems


def _config_changed(store: WorkspaceStore, connector: str, run: StoredCheckRun) -> bool:
    """La configuración de hoy no es la del intento; una configuración que falta o no se lee
    también cuenta como cambio, para que el bloqueo sea visible."""
    config = load_engine_config(store, connector, ENGINE_CONNECTORS[connector].config_model)
    return config.sha256 != run.config_sha256


def _evidence_after(
    store: WorkspaceStore, connector: str, run: StoredCheckRun, at: datetime
) -> bool:
    """Evidencia recogida después de la última ejecución: sin ella en el historial, o se borró un
    registro o falta ejecutar `check --all`. Sin configuración válida no hay selección posible."""
    loaded = load_engine_config(store, connector, ENGINE_CONNECTORS[connector].config_model)
    if loaded.config is None:
        return False
    asset_id = None if connector == ENDPOINT_CONNECTOR else config_asset_id(loaded.config)
    return evidence_after_run(store, connector, run, at, asset_id)
