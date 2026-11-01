"""El ciclo recogida -> checks -> informes -> copia, con etapas persistidas y reanudables.

Cada etapa deja un marcador de solo adición (`reports.store.write_stage`). Tras una caída, el
siguiente intento del mismo `period` retoma el `run_id` sin terminar y hace solo lo pendiente.
Los checks y los informes usan siempre el reloj real del intento, nunca `due_at`.
"""

import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ValidationError

from ens_evidence.backup import BackupOutcome, run_configured_backup
from ens_evidence.checks.engine import run_all
from ens_evidence.checks.source import StoredCheckResults
from ens_evidence.reports.daily import build_daily
from ens_evidence.reports.model import (
    ConnectorAttempts,
    CycleInfo,
    CycleStage,
    DailyReport,
    MonthlyReport,
    is_day,
    is_month,
    render_json,
)
from ens_evidence.reports.monthly import build_monthly, previous_month
from ens_evidence.reports.render_html import render_daily_html, render_monthly_html
from ens_evidence.reports.store import (
    Kind,
    cycle_finished,
    load_json_strict,
    pairs,
    run_ids,
    save_html,
    save_pair,
    stages_of,
    write_stage,
)
from ens_evidence.scheduling.calendar import JobKind, local_date
from ens_evidence.scheduling.collect import CollectOutcome, Plugins, collect_all, load_plugins
from ens_evidence.scheduling.config import ScheduleConfig
from ens_evidence.scheduling.lock import CycleContext, WaitClock
from ens_evidence.scope.approval import ScopeNotApproved
from ens_evidence.workspace import Settings, WorkspaceError, WorkspaceStore

Trigger = Literal["scheduled", "manual", "recovery"]
ChecksResult = Literal["ok", "skipped", "error"]
BACKUP_DISABLED = "disabled"
BACKUP_NOT_CONFIGURED = "not_configured"
SKIPPED_SCOPE = "skipped:scope_not_approved"


@dataclass(frozen=True)
class CycleOutcome:
    """Lo que hizo este intento. `failed`: el informe no se publicó y conviene reintentar."""

    run_id: str
    period: str
    status: Literal["completed", "failed"]
    resumed: bool
    checks: ChecksResult | None
    reports: tuple[str, ...]
    collected: tuple[CollectOutcome, ...] = ()
    backup_stage: str | None = None
    failure: str | None = None

    @property
    def completed(self) -> bool:
        return self.status == "completed"


def _type_name(error: BaseException) -> str:
    return type(error).__name__


def _pending_run(store: WorkspaceStore, period: str) -> tuple[str, CycleStage] | None:
    """El `run_id` más reciente del `period` sin terminar y con marcadores legibles."""
    candidates: list[tuple[str, CycleStage]] = []
    for run_id in run_ids(store):
        reading = stages_of(store, run_id)
        if reading.problems or cycle_finished(store, run_id):
            continue
        started = next((m for m in reading.stages if m.stage == "started"), None)
        if started is not None and started.period == period:
            candidates.append((run_id, started))
    return max(candidates, key=lambda c: c[1].at, default=None)


def _new_run_id(at: datetime) -> str:
    return f"{at.strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(3)}"


def _attempts(outcomes: list[CollectOutcome]) -> list[ConnectorAttempts]:
    entries = []
    for outcome in outcomes:
        last = outcome.attempts[-1].status if outcome.attempts else None
        if last is None and outcome.result is not None:
            last = outcome.result.collection_status
        entries.append(
            ConnectorAttempts(
                connector=outcome.connector, attempts=len(outcome.attempts), last_status=last
            )
        )
    return entries


def _run_checks(store: WorkspaceStore, clock: WaitClock) -> tuple[ChecksResult, str | None]:
    try:
        run_all(store, clock.now())
    except ScopeNotApproved:
        return "skipped", SKIPPED_SCOPE
    except (WorkspaceError, OSError) as error:
        return "error", f"error:{_type_name(error)}"
    return "ok", None


def _own_report[M: BaseModel](
    store: WorkspaceStore, kind: Kind, period: str, run_id: str, model: type[M]
) -> tuple[str, str | None] | None:
    """El par que escribió este `run_id` en una pasada anterior: (clave JSON, clave HTML)."""
    for pair in pairs(store, kind, period):
        if not pair.json_key:
            continue
        try:
            report = model.model_validate(load_json_strict(store.read_bytes(pair.json_key)))
        except (WorkspaceError, ValueError, ValidationError):
            continue
        cycle = getattr(report, "cycle", None)
        if cycle is not None and cycle.run_id == run_id:
            return pair.json_key, pair.html_key
    return None


def _publish[M: DailyReport | MonthlyReport](
    store: WorkspaceStore,
    kind: Kind,
    period: str,
    run_id: str,
    model: type[M],
    build: Callable[[], M],
    render: Callable[[M], str],
    clock: WaitClock,
) -> str:
    """Publica el informe del ciclo; si el JSON ya está, solo completa el HTML desde él."""
    own = _own_report(store, kind, period, run_id, model)
    if own is not None:
        json_key, html_key = own
        if html_key is None:
            stored = model.model_validate(load_json_strict(store.read_bytes(json_key)))
            save_html(store, json_key, render(stored).encode("utf-8"))
        return json_key
    report = build()
    pair = save_pair(
        store,
        kind,
        period,
        render_json(report),
        render(report).encode("utf-8"),
        at=clock.now(),
    )
    return pair.json_key


def _backup(
    ctx: CycleContext,
    store: WorkspaceStore,
    settings: Settings,
    env: Mapping[str, str],
    cfg: ScheduleConfig,
    clock: WaitClock,
) -> tuple[str, str]:
    """(etapa, referencia). Nunca lanza: una copia fallida no rompe el ciclo."""
    if not cfg.backup.enabled:
        return "backup_skipped", BACKUP_DISABLED
    try:
        outcome: BackupOutcome = run_configured_backup(
            store, settings.backup, env, clock.now(), clock=clock.now, context=ctx
        )
    except Exception as error:
        return "backup_failed", f"crashed:{_type_name(error)}"
    if outcome.status == "ok":
        return "backed_up", outcome.run_key or "ok"
    if outcome.status == "skipped_not_configured":
        return "backup_failed", BACKUP_NOT_CONFIGURED
    return "backup_failed", outcome.run_key or outcome.code


def run_cycle(
    ctx: CycleContext,
    store: WorkspaceStore,
    settings: Settings,
    env: Mapping[str, str],
    job: JobKind,
    period: str,
    due_at: datetime,
    *,
    clock: WaitClock,
    cfg: ScheduleConfig,
    trigger: Trigger,
    plugins: Plugins | None = None,
) -> CycleOutcome:
    """Un intento del ciclo de `period` (`YYYY-MM-DD` diario, `YYYY-MM` mensual).

    El trabajo mensual publica además el informe del mes natural anterior. Con un `run_id` del
    mismo `period` sin terminar, se reanudan solo las etapas pendientes.
    """
    if not (is_day(period) if job == "daily" else is_month(period)):
        raise ValueError("period no corresponde al tipo de trabajo")
    pending = _pending_run(store, period)
    resumed = pending is not None
    if pending is None:
        started_at = clock.now()
        run_id = _new_run_id(started_at)
        write_stage(store, run_id, "started", at=started_at, period=period)
        done: set[str] = {"started"}
    else:
        run_id, started = pending
        started_at = started.at
        done = {m.stage for m in stages_of(store, run_id).stages}

    def mark(stage: str, reference: str | None = None) -> None:
        write_stage(store, run_id, stage, at=clock.now(), period=period, reference=reference)
        done.add(stage)

    collected: list[CollectOutcome] = []
    if "collected" not in done:
        loaded = plugins if plugins is not None else load_plugins()
        collected = collect_all(store, settings, loaded, clock.now(), cfg.collect, clock)
        mark("collected")

    checks: ChecksResult | None = None
    if "checked" not in done:
        checks, reference = _run_checks(store, clock)
        mark("checked", reference)

    reports: list[str] = []
    if "reported" not in done:
        try:
            reports = _publish_reports(
                store, run_id, job, period, due_at, started_at, trigger, collected, cfg, clock
            )
        except Exception as error:
            return CycleOutcome(
                run_id,
                period,
                "failed",
                resumed,
                checks,
                (),
                tuple(collected),
                failure=f"report_failed:{_type_name(error)}",
            )
        mark("reported", reports[-1])

    backup_stage = next((s for s in done if s.startswith("backup") or s == "backed_up"), None)
    if backup_stage is None:
        backup_stage, reference = _backup(ctx, store, settings, env, cfg, clock)
        mark(backup_stage, reference)
    return CycleOutcome(
        run_id, period, "completed", resumed, checks, tuple(reports), tuple(collected), backup_stage
    )


def _publish_reports(
    store: WorkspaceStore,
    run_id: str,
    job: JobKind,
    period: str,
    due_at: datetime,
    started_at: datetime,
    trigger: Trigger,
    collected: list[CollectOutcome],
    cfg: ScheduleConfig,
    clock: WaitClock,
) -> list[str]:
    """El diario y, si el trabajo es mensual, el mensual. Cada uno se reanuda por separado."""
    zone = cfg.zone
    cutoff = clock.now()
    info = CycleInfo(
        run_id=run_id,
        trigger=trigger,
        period=period,
        due_at=due_at,
        started_at=started_at,
        data_cutoff=cutoff,
        connector_attempts=_attempts(collected),
    )
    checks = StoredCheckResults(reference_now=cutoff)
    day = period if job == "daily" else local_date(due_at, zone).isoformat()
    keys = [
        _publish(
            store,
            "daily",
            day,
            run_id,
            DailyReport,
            lambda: build_daily(
                store, generated_at=cutoff, period=day, tz=zone, cycle=info, checks=checks
            ),
            render_daily_html,
            clock,
        )
    ]
    if job == "monthly":
        month = previous_month(period)
        keys.append(
            _publish(
                store,
                "monthly",
                month,
                run_id,
                MonthlyReport,
                lambda: build_monthly(
                    store, generated_at=cutoff, period=month, tz=zone, checks=checks, cycle=info
                ),
                lambda report: render_monthly_html(report, zone),
                clock,
            )
        )
    return keys
