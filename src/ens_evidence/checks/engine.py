"""Motor de checks: evalúa lo ya recogido y registra el intento (ADR 0012).

Sin red: lee la configuración y la evidencia del workspace y llama a funciones de check puras. No
instancia conectores, no recoge ni prepara datos y no carga entry points. Cierre seguro: si una
entrada falla, cada check da UNKNOWN con el código del fallo y nunca se retrocede a una evidencia
anterior. Un check que lanza una excepción da UNKNOWN `check_crashed` (solo el nombre de la
excepción, nunca su mensaje) y el intento queda incompleto.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel

from ens_evidence import __version__
from ens_evidence.checks.expected import ExpectedError, expected_subjects
from ens_evidence.checks.inputs import (
    config_asset_id,
    load_endpoint_inputs,
    load_engine_config,
    select_collector_input,
)
from ens_evidence.checks.model import CheckInput, StoredCheckRun
from ens_evidence.checks.registry import ENDPOINT, ENGINE_CONNECTORS, EngineConnector
from ens_evidence.checks.store import CheckHistoryUnreadable, record_run
from ens_evidence.connectors.config import connector_config_key
from ens_evidence.endpoint.checks import evaluate_host_checks
from ens_evidence.endpoint.config import EndpointConfig
from ens_evidence.endpoint.evidence import latest_by_host
from ens_evidence.evidence.layout import sha256_hex
from ens_evidence.models.contracts import CheckResult
from ens_evidence.models.enums import CheckStatus
from ens_evidence.scope import Inventory, fingerprint, load_inventory, require_approved_scope
from ens_evidence.workspace import WorkspaceStore

CHECK_CRASHED = "check_crashed"
CONFIG_INVALID = "config_invalid"
_MAX_PROBLEMS_SHOWN = 3


class OutcomeKind(StrEnum):
    NOT_CONFIGURED = "not_configured"
    RECORDED = "recorded"
    UNCHANGED = "unchanged"
    HISTORY_UNREADABLE = "history_unreadable"
    REJECTED = "rejected"


@dataclass(frozen=True)
class ConnectorOutcome:
    """Qué pasó con un conector en `run_all`. `run` es el intento evaluado (None si no está
    configurado); `key` la clave escrita (solo en RECORDED); `detail` explica los rechazos."""

    connector: str
    kind: OutcomeKind
    run: StoredCheckRun | None = None
    key: str | None = None
    detail: tuple[str, ...] = ()


@dataclass
class _Evaluation:
    expected: dict[str, list[str]] = field(default_factory=dict)
    results: list[CheckResult] = field(default_factory=list)
    inputs: list[CheckInput] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def run_all(
    store: WorkspaceStore, now: datetime, *, accept_clock_correction: bool = False
) -> list[ConnectorOutcome]:
    """Evalúa y registra cada conector configurado. Exige el alcance aprobado
    (`ScopeNotApproved` si no). `accept_clock_correction`: ver `record_run`."""
    require_approved_scope(store)
    inventory = load_inventory(store).inventory
    assert inventory is not None  # aprobado implica inventario válido
    outcomes: list[ConnectorOutcome] = []
    for name in ENGINE_CONNECTORS:
        if not store.exists(connector_config_key(name)):
            outcomes.append(ConnectorOutcome(name, OutcomeKind.NOT_CONFIGURED))
            continue
        run = evaluate_connector(store, name, inventory, now)
        outcomes.append(_record(store, run, now, accept_clock_correction))
    return outcomes


def _record(
    store: WorkspaceStore, run: StoredCheckRun, now: datetime, accept_clock_correction: bool
) -> ConnectorOutcome:
    try:
        recorded = record_run(store, run, now, accept_clock_correction=accept_clock_correction)
    except CheckHistoryUnreadable as exc:
        return ConnectorOutcome(run.connector, OutcomeKind.HISTORY_UNREADABLE, run, None, exc.keys)
    except ValueError as exc:
        return ConnectorOutcome(run.connector, OutcomeKind.REJECTED, run, None, (str(exc),))
    kind = OutcomeKind.RECORDED if recorded else OutcomeKind.UNCHANGED
    return ConnectorOutcome(run.connector, kind, run, recorded)


def evaluate_connector(
    store: WorkspaceStore, connector: str, inventory: Inventory, now: datetime
) -> StoredCheckRun:
    """El intento de `connector` sobre lo que hay en el workspace, con `seq=1` (lo renumera el
    almacén al registrarlo)."""
    spec = ENGINE_CONNECTORS[connector]
    loaded = load_engine_config(store, connector, spec.config_model)
    config_sha256 = loaded.sha256 or sha256_hex(b"")  # fichero ilegible: huella de cero bytes
    if loaded.config is None:
        evaluation = _Evaluation(errors=[f"{CONFIG_INVALID}: {loaded.error}"])
        return _stored(spec, inventory, config_sha256, evaluation, now)
    if connector == ENDPOINT:
        assert isinstance(loaded.config, EndpointConfig)
        evaluation = _endpoint(store, spec, loaded.config, inventory, now)
    else:
        evaluation = _collector(store, spec, loaded.config, inventory, now)
    return _stored(spec, inventory, config_sha256, evaluation, now)


def _stored(
    spec: EngineConnector,
    inventory: Inventory,
    config_sha256: str,
    evaluation: _Evaluation,
    now: datetime,
) -> StoredCheckRun:
    return StoredCheckRun(
        seq=1,
        connector=spec.name,
        engine_version=__version__,
        started_at=now,
        finished_at=now,
        complete=not evaluation.errors,
        scope_fingerprint=fingerprint(inventory),
        config_sha256=config_sha256,
        inputs=sorted(evaluation.inputs, key=lambda i: i.evidence_id),
        expected_subjects=evaluation.expected,
        results=evaluation.results,
        errors=evaluation.errors,
    )


def _collector(
    store: WorkspaceStore,
    spec: EngineConnector,
    config: BaseModel,
    inventory: Inventory,
    now: datetime,
) -> _Evaluation:
    asset_id = config_asset_id(config)
    selection = select_collector_input(store, spec.name, asset_id)
    try:
        expected = expected_subjects(spec.name, config, inventory, selection.result)
    except ExpectedError as exc:
        return _closed(spec, asset_id, exc.code, {}, (), now)
    if selection.result is None or selection.evidence_id is None or not selection.ok:
        code = selection.error or "no_evidence"
        return _closed(spec, asset_id, code, expected, selection.problems, now)
    evaluation = _Evaluation(expected=expected)
    for check_id, check in spec.checks.items():
        try:
            evaluation.results += check(selection.result, selection.evidence_id, now)
        except Exception as exc:  # un check defectuoso no tumba al resto ni se lee como PASS
            name = type(exc).__name__
            evaluation.errors.append(f"{CHECK_CRASHED}:{check_id}:{name}")
            evaluation.results += _unknown(
                [check_id], asset_id, expected, now, CHECK_CRASHED, f"el check terminó con {name}"
            )
    if selection.manifest_sha256 is not None:
        evaluation.inputs.append(
            CheckInput(evidence_id=selection.evidence_id, manifest_sha256=selection.manifest_sha256)
        )
    return evaluation


def _endpoint(
    store: WorkspaceStore,
    spec: EngineConnector,
    config: EndpointConfig,
    inventory: Inventory,
    now: datetime,
) -> _Evaluation:
    loaded = load_endpoint_inputs(store)
    expected = expected_subjects(spec.name, config, inventory, None)
    problems = [*loaded.unreadable, *loaded.inconsistent]
    evaluation = _Evaluation(expected=expected)
    try:
        evaluation.results = evaluate_host_checks(
            inventory.assets.assets, [i.evidence for i in loaded.valid], config, now, problems
        )
    except Exception as exc:
        name = type(exc).__name__
        evaluation.errors.append(f"{CHECK_CRASHED}:{spec.name}:{name}")
        evaluation.results = _unknown(
            spec.check_ids, None, expected, now, CHECK_CRASHED, f"la evaluación terminó con {name}"
        )
    if not problems:
        shas = {i.evidence.evidence_id: i.manifest_sha256 for i in loaded.valid}
        latest = latest_by_host(i.evidence for i in loaded.valid)
        evaluation.inputs = [
            CheckInput(evidence_id=e.evidence_id, manifest_sha256=shas[e.evidence_id])
            for e in latest.values()
        ]
    return evaluation


def _closed(
    spec: EngineConnector,
    asset_id: str,
    code: str,
    expected: dict[str, list[str]],
    problems: tuple[str, ...],
    now: datetime,
) -> _Evaluation:
    """Cierre seguro: la entrada falló, así que todo es UNKNOWN con el código del fallo."""
    reason = f"no se puede valorar: {code}"
    if problems:
        shown = "; ".join(problems[:_MAX_PROBLEMS_SHOWN])
        more = len(problems) - _MAX_PROBLEMS_SHOWN
        reason += f" ({shown}{f'; y {more} más' if more > 0 else ''})"
    results = _unknown(spec.check_ids, asset_id, expected, now, code, reason)
    return _Evaluation(expected=expected, results=results)


def _unknown(
    check_ids: tuple[str, ...] | list[str],
    asset_id: str | None,
    expected: dict[str, list[str]],
    now: datetime,
    code: str,
    reason: str,
) -> list[CheckResult]:
    """Un UNKNOWN por check y sujeto esperado (o uno sin sujeto si no hay). Con `asset_id` None
    el sujeto es el propio activo (endpoint)."""
    results = []
    for check_id in check_ids:
        for subject in expected.get(check_id) or [None]:
            owner = asset_id if asset_id is not None else subject
            if owner is None:
                continue
            results.append(
                CheckResult(
                    check_id=check_id,
                    asset_id=owner,
                    subject=subject,
                    status=CheckStatus.UNKNOWN,
                    evaluated_at=now,
                    reason=reason,
                    reason_code=code,
                )
            )
    return results
