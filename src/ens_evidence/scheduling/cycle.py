"""El ciclo recogida -> checks -> informes -> copia, con etapas persistidas y reanudables.

Cada etapa deja un marcador de solo adición (`reports.store.write_stage`). Tras una caída, el
siguiente intento del mismo `period` retoma el `run_id` sin terminar y hace solo lo pendiente.
Los checks y los informes usan siempre el reloj real del intento, nunca `due_at`.
"""

import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from ens_evidence.backup import BackupOutcome, run_configured_backup
from ens_evidence.checks.engine import run_all
from ens_evidence.checks.source import StoredCheckResults
from ens_evidence.reports.daily import build_daily
from ens_evidence.reports.expiries import windows_from
from ens_evidence.reports.model import (
    BackupCode,
    ChecksOutcome,
    ConnectorAttempts,
    CycleInfo,
    CycleStage,
    DailyReport,
    InheritedStage,
    InterruptedCycle,
    MonthlyReport,
    is_day,
    is_month,
    render_json,
)
from ens_evidence.reports.monthly import build_monthly, previous_month
from ens_evidence.reports.render_html import render_daily_html, render_monthly_html
from ens_evidence.reports.store import (
    Kind,
    first_stage,
    interrupted_in,
    load_json_strict,
    pairs,
    published_hashes,
    reading_finished,
    save_html,
    save_pair,
    scan_cycles,
    stages_of,
    write_stage,
)
from ens_evidence.scheduling.calendar import JobKind
from ens_evidence.scheduling.collect import (
    CollectOutcome,
    Plugins,
    collect_all,
    error_type,
    load_plugins,
)
from ens_evidence.scheduling.config import ScheduleConfig
from ens_evidence.scope.approval import ScopeNotApproved
from ens_evidence.workspace import Settings, WorkspaceError, WorkspaceStore
from ens_evidence.workspace.cycle_lock import Clock, CycleContext

Trigger = Literal["scheduled", "manual", "recovery"]
ChecksResult = Literal["ok", "skipped", "error"]
RUN_WINDOW = timedelta(days=40)
INHERITED = ("collected", "checked")
_STAMP = "%Y%m%dT%H%M%S"
InterruptedRun = InterruptedCycle


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


def _plausible(reading_stages: list[CycleStage], period: str, now: datetime, zone: tzinfo) -> bool:
    """Un ciclo real no tiene marcadores del futuro ni empezó antes de su periodo."""
    started = next((m for m in reading_stages if m.stage == "started"), None)
    if started is None or any(m.at > now for m in reading_stages):
        return False
    local = started.at.astimezone(zone)
    reached = local.date().isoformat() if is_day(period) else local.strftime("%Y-%m")
    return reached >= period


def _pending_run(
    store: WorkspaceStore, period: str, now: datetime, zone: tzinfo
) -> tuple[str, CycleStage] | None:
    """El `run_id` más reciente del `period` sin terminar, legible y con fechas verosímiles."""
    oldest = (now - RUN_WINDOW).astimezone(UTC).strftime(_STAMP)
    candidates: list[tuple[str, CycleStage]] = []
    for run_id, reading in scan_cycles(store, since=oldest).runs.items():
        started = first_stage(reading, "started")
        if reading.problems or reading_finished(reading) or started is None:
            continue
        if started.period == period and _plausible(reading.stages, period, now, zone):
            candidates.append((run_id, started))
    return max(candidates, key=lambda c: c[1].at, default=None)


def _new_run_id(at: datetime) -> str:
    return f"{at.astimezone(UTC).strftime(_STAMP)}-{secrets.token_hex(3)}"


def _job_kind(period: str) -> JobKind:
    return "daily" if is_day(period) else "monthly"


def finished_periods(store: WorkspaceStore, now: datetime) -> set[tuple[JobKind, str]]:
    """Los `(trabajo, periodo)` con ciclo terminado en los últimos 40 días, según el workspace."""
    oldest = (now - RUN_WINDOW).astimezone(UTC).strftime(_STAMP)
    done: set[tuple[JobKind, str]] = set()
    for reading in scan_cycles(store, since=oldest).runs.values():
        started = first_stage(reading, "started")
        if started is not None and reading_finished(reading):
            done.add((_job_kind(started.period), started.period))
    return done


def interrupted_runs(
    store: WorkspaceStore, now: datetime | None = None
) -> tuple[list[InterruptedRun], list[str]]:
    """Ciclos sin terminar y legibles, y los `run_id` de los que no se pueden leer."""
    return interrupted_in(scan_cycles(store).runs, now=now)


def _attempts(outcomes: list[CollectOutcome]) -> list[ConnectorAttempts]:
    entries = []
    for outcome in outcomes:
        final = outcome.attempts[-1] if outcome.attempts else None
        last = final.status if final else None
        if last is None and outcome.result is not None:
            last = outcome.result.collection_status
        entries.append(
            ConnectorAttempts(
                connector=outcome.connector,
                attempts=len(outcome.attempts),
                last_status=last,
                phase=final.phase.value if final and final.phase else None,
                error_type=error_type(final.error) if final and final.error else None,
            )
        )
    return entries


def _run_checks(store: WorkspaceStore, clock: Clock) -> tuple[ChecksResult, ChecksOutcome]:
    try:
        run_all(store, clock.now())
    except ScopeNotApproved:
        return "skipped", "skipped_scope"
    except (WorkspaceError, OSError):
        return "error", "error"
    return "ok", "ok"


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
        if report.cycle is not None and report.cycle.run_id == run_id:
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
    clock: Clock,
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
    clock: Clock,
) -> tuple[str, BackupCode | None, str | None]:
    """(etapa, código, clave del registro). Nunca lanza: una copia fallida no rompe el ciclo."""
    if not cfg.backup.enabled:
        return "backup_skipped", "disabled", None
    try:
        outcome: BackupOutcome = run_configured_backup(
            store,
            settings.backup,
            env,
            clock=clock.now,
            context=ctx,
            timeout_seconds=cfg.backup.timeout_minutes * 60,
        )
    except Exception:
        return "backup_failed", "crashed", None
    if outcome.status == "ok":
        return "backed_up", None, outcome.run_key
    return "backup_failed", outcome.reason, outcome.run_key


def run_cycle(
    ctx: CycleContext,
    store: WorkspaceStore,
    settings: Settings,
    env: Mapping[str, str],
    job: JobKind,
    period: str,
    due_at: datetime,
    *,
    clock: Clock,
    cfg: ScheduleConfig,
    trigger: Trigger,
    plugins: Plugins | None = None,
) -> CycleOutcome:
    """Un intento del ciclo de `period` (`YYYY-MM-DD` diario, `YYYY-MM` mensual).

    El diario recoge, evalúa, informa y copia. El mensual solo genera el informe del mes natural
    anterior (`started` -> `reported`). Con un `run_id` del mismo `period` sin terminar, se
    reanudan solo las etapas pendientes.
    """
    if not (is_day(period) if job == "daily" else is_month(period)):
        raise ValueError("period no corresponde al tipo de trabajo")
    attempt_at = clock.now()
    pending = _pending_run(store, period, attempt_at, cfg.zone)
    resumed = pending is not None
    markers: dict[str, CycleStage] = {}
    if pending is None:
        started_at = attempt_at
        run_id = _new_run_id(started_at)
        write_stage(store, run_id, "started", at=started_at, period=period)
        done: set[str] = {"started"}
    else:
        run_id, started = pending
        started_at = started.at
        markers = {m.stage: m for m in stages_of(store, run_id).stages}
        done = set(markers)
    inherited = [
        InheritedStage(stage=m.stage, at=m.at) for m in markers.values() if m.stage in INHERITED
    ]

    def mark(stage: str, reference: str | None = None, **details: Any) -> None:
        write_stage(
            store, run_id, stage, at=clock.now(), period=period, reference=reference, **details
        )
        done.add(stage)

    collected: list[CollectOutcome] = []
    collected_marker = markers.get("collected")
    attempts = (collected_marker.attempts or []) if collected_marker else []
    checks: ChecksResult | None = None
    if job == "daily":
        if "collected" not in done:
            loaded = plugins if plugins is not None else load_plugins()
            collected = collect_all(store, settings, loaded, clock.now(), cfg.collect, clock)
            attempts = _attempts(collected)
            mark("collected", attempts=attempts)
        if "checked" not in done:
            checks, marker_checks = _run_checks(store, clock)
            mark("checked", checks=marker_checks)

    reports: list[str] = []
    if "reported" in done:
        reference = markers["reported"].reference
        reports = [reference] if reference else []
    else:
        try:
            reports = _publish_report(
                store, run_id, job, period, due_at, started_at, trigger, attempts, cfg, clock,
                resumed, inherited,
            )  # fmt: skip
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
        json_sha256, html_sha256 = published_hashes(store, reports[-1])
        mark("reported", reports[-1], json_sha256=json_sha256, html_sha256=html_sha256)

    backup_stage = next((s for s in done if s.startswith("backup") or s == "backed_up"), None)
    if job == "daily" and backup_stage is None:
        backup_stage, code, reference = _backup(ctx, store, settings, env, cfg, clock)
        mark(backup_stage, reference, backup_code=code)
    return CycleOutcome(
        run_id, period, "completed", resumed, checks, tuple(reports), tuple(collected), backup_stage
    )


def _publish_report(
    store: WorkspaceStore,
    run_id: str,
    job: JobKind,
    period: str,
    due_at: datetime,
    started_at: datetime,
    trigger: Trigger,
    attempts: list[ConnectorAttempts],
    cfg: ScheduleConfig,
    clock: Clock,
    resumed: bool,
    inherited: list[InheritedStage],
) -> list[str]:
    """El informe del trabajo: el diario del día o el mensual del mes natural anterior."""
    zone = cfg.zone
    cutoff = clock.now()
    info = CycleInfo(
        run_id=run_id,
        trigger=trigger,
        period=period,
        due_at=due_at,
        started_at=started_at,
        data_cutoff=cutoff,
        connector_attempts=attempts,
        resumed=resumed,
        inherited_stages=inherited,
    )
    checks = StoredCheckResults(reference_now=cutoff)
    if job == "daily":
        key = _publish(
            store,
            "daily",
            period,
            run_id,
            DailyReport,
            lambda: build_daily(
                store,
                generated_at=cutoff,
                period=period,
                tz=zone,
                cycle=info,
                checks=checks,
                windows=windows_from(cfg.expiry_warnings),
            ),
            render_daily_html,
            clock,
        )
        return [key]
    month = previous_month(period)
    key = _publish(
        store,
        "monthly",
        month,
        run_id,
        MonthlyReport,
        lambda: build_monthly(
            store,
            generated_at=cutoff,
            period=month,
            tz=zone,
            checks=checks,
            cycle=info,
            expiry_windows=windows_from(cfg.expiry_warnings),
        ),
        lambda report: render_monthly_html(report, zone),
        clock,
    )
    return [key]
