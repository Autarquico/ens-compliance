"""El planificador: un bucle propio, trabajos en serie y «hecho» derivado del workspace.

No depende de Typer ni de un reloj concreto, así que se puede embeber y probar sin dormir.
Los logs son una línea `clave=valor` por evento; solo llevan valores acotados (nombres de
evento, periodos, tipos de excepción), nunca mensajes de excepción ni el entorno.
"""

import hashlib
import json
import os
import signal
import threading
from collections.abc import Callable, Collection, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal, Protocol

from ens_evidence.reports.model import is_day, is_month
from ens_evidence.reports.monthly import previous_month
from ens_evidence.reports.store import REPORTS_PREFIX, Kind, pairs, reports_in_period
from ens_evidence.scheduling.calendar import (
    Job,
    JobKind,
    due_jobs,
    local_date,
    next_daily,
    next_monthly,
    resolve_local,
)
from ens_evidence.scheduling.collect import Plugins, load_plugins
from ens_evidence.scheduling.config import LoadedSchedule, ScheduleConfig
from ens_evidence.scheduling.cycle import (
    InterruptedCycle,
    Trigger,
    finished_periods,
    interrupted_runs,
    run_cycle,
)
from ens_evidence.workspace import Settings, WorkspaceError, WorkspaceStore
from ens_evidence.workspace.cycle_lock import (
    Clock,
    CycleBusy,
    CycleContext,
    CycleLock,
    acquire_or_wait,
    resolve_state_dir,
)

MAX_SLEEP_SECONDS = 60.0
LATE_AFTER = timedelta(minutes=5)
LOCK_WAIT_MINUTES = 10.0
HEARTBEAT_FILE = "scheduler-heartbeat"
HEARTBEAT_ENV = "ENS_HEARTBEAT_FILE"
BEAT_EVERY_SECONDS = 60.0
Key = tuple[JobKind, str]
Status = Literal["completed", "failed", "busy", "interrupted"]
Heartbeat = Callable[[], None]


@dataclass(frozen=True)
class JobResult:
    """`code` es un identificador acotado (p. ej. `report_failed:ValueError`), nunca un mensaje."""

    status: Status
    code: str | None = None


class JobExecutor(Protocol):
    def run(
        self, job: Job, cfg: ScheduleConfig, trigger: Trigger, *, config_warning: bool = False
    ) -> JobResult: ...


def print_log(line: str) -> None:
    print(line, flush=True)


def config_sha256(cfg: ScheduleConfig) -> str:
    canonical = json.dumps(cfg.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def file_heartbeat(env: Mapping[str, str]) -> Heartbeat:
    """Latido para el healthcheck del contenedor: renueva la fecha de modificación de un fichero.

    `ENS_HEARTBEAT_FILE` fija su ruta; sin ella, vive en el directorio de estado. En el
    contenedor apunta a un tmpfs propio de cada contenedor: el latido de una versión anterior
    no vale para la nueva. Un fallo al escribirlo no para el planificador: el fichero envejece
    y es el healthcheck quien lo avisa."""
    configured = env.get(HEARTBEAT_ENV)
    path = Path(configured) if configured else resolve_state_dir(env) / HEARTBEAT_FILE

    def beat() -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            temporary = path.with_name(f".{path.name}.tmp")
            temporary.write_text(f"{os.getpid()}\n", encoding="utf-8")
            os.replace(temporary, path)
        except OSError:
            pass

    return beat


class BeatingClock:
    """Reloj que late con cada lectura y con cada tramo de espera.

    El latido sale del mismo hilo que hace el trabajo: si el ciclo se cuelga, deja de latir.
    Las esperas largas (reintentos de conectores) se parten en tramos de `BEAT_EVERY_SECONDS`.
    """

    def __init__(self, clock: Clock, heartbeat: Heartbeat) -> None:
        self._clock = clock
        self._heartbeat = heartbeat

    def now(self) -> datetime:
        self._heartbeat()
        return self._clock.now()

    def sleep(self, seconds: float) -> None:
        remaining = seconds
        while True:
            self._heartbeat()
            chunk = min(remaining, BEAT_EVERY_SECONDS)
            self._clock.sleep(chunk)
            remaining -= chunk
            if remaining <= 0:
                return


class Scheduler:
    def __init__(
        self,
        clock: Clock,
        executor: JobExecutor,
        load_config: Callable[[], ScheduleConfig],
        done_periods: Callable[[], Collection[Key]],
        stop: threading.Event,
        *,
        log: Callable[[str], None] = print_log,
        heartbeat: Heartbeat | None = None,
    ) -> None:
        self._heartbeat = heartbeat
        self._clock = clock
        self._executor = executor
        self._load_config = load_config
        self._done_periods = done_periods
        self._stop = stop
        self._log = log
        self._config: ScheduleConfig | None = None
        self._config_sha: str | None = None
        self._config_invalid = False
        self._finished: set[Key] = set()
        self._attempts: dict[Key, int] = {}
        self._retry_at: dict[Key, datetime] = {}
        self._retrying: dict[Key, Job] = {}
        self._exhausted: dict[Key, date] = {}
        self._ticking = False

    @property
    def config_warning(self) -> bool:
        """La última recarga de la configuración falló; se sigue con la última válida."""
        return self._config_invalid

    def run(self) -> None:
        self._event("scheduler_started")
        while not self._stop.is_set():
            self.tick()
            if self._stop.is_set():
                break
            self._clock.sleep(self._sleep_seconds())
        self._event("scheduler_stopped")

    def tick(self) -> list[tuple[Job, JobResult]]:
        """Recarga la configuración y ejecuta, en serie, los trabajos vencidos. No reentra."""
        if self._ticking:
            self._event("tick_skipped", reason="reentrant")
            return []
        self._ticking = True
        self._beat()
        try:
            return self._tick()
        finally:
            self._ticking = False

    def _tick(self) -> list[tuple[Job, JobResult]]:
        cfg = self._reload()
        if cfg is None:
            return []
        now = self._clock.now()
        self._reset_exhausted(now, cfg)
        if not any(self._ready(job, now) for job in self._pending(now, cfg)):
            return []
        try:
            self._finished |= set(self._done_periods())
        except (WorkspaceError, OSError) as error:
            self._event("done_unreadable", error=type(error).__name__)
            return []
        executed: list[tuple[Job, JobResult]] = []
        for job in self._pending(now, cfg):
            if self._stop.is_set() or not self._ready(job, self._clock.now()):
                continue
            result = self._execute(job, cfg)
            self._beat()
            executed.append((job, result))
            if result.status == "interrupted":
                break
        return executed

    def _beat(self) -> None:
        if self._heartbeat is not None:
            self._heartbeat()

    def _pending(self, now: datetime, cfg: ScheduleConfig) -> list[Job]:
        """Los vencidos del periodo en curso y los que reintentan de un periodo anterior (que
        conservan el suyo), el diario antes que el mensual."""
        found = {(job.kind, job.period): job for job in due_jobs(now, cfg, self._finished)}
        for key, job in self._retrying.items():
            if key not in self._finished:
                found.setdefault(key, job)
        return sorted(found.values(), key=lambda job: (job.due_at, job.kind != "daily"))

    def _reset_exhausted(self, now: datetime, cfg: ScheduleConfig) -> None:
        """Un trabajo agotado recupera sus intentos al día siguiente (local) sin reiniciar el
        proceso: así el mensual pendiente se reintenta cada día del mes hasta que se publica.
        El diario de un día pasado no vuelve: su periodo ya no está vencido."""
        today = local_date(now, cfg.zone)
        for key in [key for key, day in self._exhausted.items() if day < today]:
            self._forget(key)

    def _ready(self, job: Job, now: datetime) -> bool:
        key = (job.kind, job.period)
        return key not in self._exhausted and self._retry_at.get(key, now) <= now

    def _execute(self, job: Job, cfg: ScheduleConfig) -> JobResult:
        key = (job.kind, job.period)
        attempt = self._attempts.get(key, 0) + 1
        late = self._clock.now() - job.due_at > LATE_AFTER
        trigger: Trigger = "recovery" if late else "scheduled"
        self._event("job_start", job=job.kind, period=job.period, attempt=attempt, trigger=trigger)
        try:
            result = self._executor.run(job, cfg, trigger, config_warning=self._config_invalid)
        except Exception as error:
            result = JobResult("failed", f"executor_crashed:{type(error).__name__}")
        if result.status == "completed":
            self._finished.add(key)
            self._forget(key)
            self._event("job_done", job=job.kind, period=job.period)
        elif result.status == "interrupted":
            self._event("job_interrupted", job=job.kind, period=job.period)
        else:
            self._record_failure(job, cfg, attempt, result)
        return result

    def _record_failure(
        self, job: Job, cfg: ScheduleConfig, attempt: int, result: JobResult
    ) -> None:
        key = (job.kind, job.period)
        self._attempts[key] = attempt
        fields = {"job": job.kind, "period": job.period, "attempt": attempt, "code": result.code}
        if attempt >= cfg.cycle.attempts:
            self._exhausted[key] = local_date(self._clock.now(), cfg.zone)
            self._retrying.pop(key, None)
            self._event("job_exhausted", **fields)
            return
        self._retrying[key] = job
        retry_at = self._clock.now() + timedelta(minutes=cfg.cycle.retry_interval_minutes)
        self._retry_at[key] = retry_at
        self._event("job_failed", **fields, retry_at=retry_at.isoformat())

    def _forget(self, key: Key) -> None:
        for table in (self._attempts, self._retry_at, self._retrying, self._exhausted):
            table.pop(key, None)

    def _reload(self) -> ScheduleConfig | None:
        try:
            cfg = self._load_config()
        except (WorkspaceError, OSError) as error:
            if not self._config_invalid:
                self._event(
                    "config_invalid",
                    error=type(error).__name__,
                    keeping="last_valid" if self._config else "none",
                )
            self._config_invalid = True
            return self._config
        self._config_invalid = False
        sha = config_sha256(cfg)
        if sha != self._config_sha:
            self._event("config_loaded", sha256=sha)
        self._config, self._config_sha = cfg, sha
        return cfg

    def _sleep_seconds(self) -> float:
        if self._config is None:
            return MAX_SLEEP_SECONDS
        now = self._clock.now()
        upcoming = min(next_daily(now, self._config), next_monthly(now, self._config))
        return min(MAX_SLEEP_SECONDS, max(1.0, (upcoming - now).total_seconds()))

    def _event(self, name: str, **fields: object) -> None:
        parts = [self._clock.now().isoformat(), f"event={name}"]
        parts += [f"{key}={value}" for key, value in fields.items() if value is not None]
        self._log(" ".join(parts))


class _Stopped(BaseException):
    """Se pidió parar durante una espera: aborta el intento sin que lo capture un `except`."""


class _StopAware:
    """Reloj para el ciclo: si se pide parar durante una espera, la interrumpe en vez de volver
    antes de tiempo (que adelantaría los reintentos de la recogida o el sondeo del lock)."""

    def __init__(self, clock: Clock, stop: threading.Event) -> None:
        self._clock = clock
        self._stop = stop

    def now(self) -> datetime:
        return self._clock.now()

    def sleep(self, seconds: float) -> None:
        self._clock.sleep(seconds)
        if self._stop.is_set():
            raise _Stopped


class CycleExecutor:
    """Toma el lock del ciclo (esperando si está ocupado) y ejecuta `run_cycle`."""

    def __init__(
        self,
        store: WorkspaceStore,
        settings: Settings,
        env: Mapping[str, str],
        clock: Clock,
        stop: threading.Event,
        *,
        plugins: Plugins | None = None,
        lock_wait_minutes: float = LOCK_WAIT_MINUTES,
        lock_factory: Callable[[Settings, Mapping[str, str]], CycleLock] = CycleLock.for_settings,
        heartbeat: Heartbeat | None = None,
    ) -> None:
        self._heartbeat = heartbeat
        self._store = store
        self._settings = settings
        self._env = env
        self._clock = _StopAware(clock, stop)
        self._lock_wait_minutes = lock_wait_minutes
        self._lock_factory = lock_factory
        self._plugins = plugins

    def run(
        self, job: Job, cfg: ScheduleConfig, trigger: Trigger, *, config_warning: bool = False
    ) -> JobResult:
        lock = self._lock_factory(self._settings, self._env)
        clock = BeatingClock(self._clock, self._heartbeat) if self._heartbeat else self._clock
        try:
            acquire_or_wait(lock, self._clock, self._lock_wait_minutes)
        except CycleBusy:
            return JobResult("busy", "lock_busy")
        except _Stopped:
            return JobResult("interrupted")
        except OSError:
            return JobResult("failed", "lock_unavailable")
        try:
            outcome = run_cycle(
                CycleContext(lock),
                self._store,
                self._settings,
                self._env,
                job.kind,
                job.period,
                job.due_at,
                clock=clock,
                cfg=cfg,
                trigger=trigger,
                plugins=self._plugins_for(job),
                config_warning=config_warning,
            )
        except _Stopped:
            return JobResult("interrupted")
        except Exception as error:
            return JobResult("failed", f"cycle_crashed:{type(error).__name__}")
        finally:
            lock.release()
        if outcome.completed:
            return JobResult("completed")
        return JobResult("failed", outcome.failure)

    def _plugins_for(self, job: Job) -> Plugins | None:
        if job.kind != "daily":
            return None
        if self._plugins is None:
            self._plugins = load_plugins()
        return self._plugins


@contextmanager
def stop_on_signals(stop: threading.Event) -> Iterator[None]:
    """SIGTERM y SIGINT piden parar. Solo en el hilo principal."""

    def request_stop(signum: int, frame: object) -> None:
        stop.set()

    previous = {sig: signal.signal(sig, request_stop) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        yield
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def workspace_done_periods(store: WorkspaceStore, clock: Clock) -> Callable[[], set[Key]]:
    return lambda: finished_periods(store, clock.now())


# --- scheduler status (solo lectura) -----------------------------------------------------------


@dataclass(frozen=True)
class JobSchedule:
    kind: JobKind
    next_at: datetime
    pending: Job | None


@dataclass(frozen=True)
class LastReport:
    period: str
    stamp: str
    published: bool


@dataclass(frozen=True)
class SchedulerStatus:
    config: ScheduleConfig
    sha256: str
    is_default: bool
    jobs: tuple[JobSchedule, ...]
    last_reports: Mapping[JobKind, LastReport | None]
    interrupted: tuple[InterruptedCycle, ...]
    unreadable_cycles: tuple[str, ...]
    month: str
    days_without_report: tuple[str, ...]


def _report_periods(store: WorkspaceStore, kind: Kind) -> list[str]:
    prefix = f"{REPORTS_PREFIX}/{kind}/"
    valid = is_day if kind == "daily" else is_month
    found = {key.removeprefix(prefix).split("/", 1)[0] for key in store.list(prefix.rstrip("/"))}
    return sorted(period for period in found if valid(period))


def _last_report(store: WorkspaceStore, kind: Kind) -> LastReport | None:
    for period in reversed(_report_periods(store, kind)):
        if found := [p for p in pairs(store, kind, period) if p.json_key]:
            return LastReport(period, found[-1].stamp, found[-1].published)
    return None


def _days_without_report(
    store: WorkspaceStore, cfg: ScheduleConfig, now: datetime
) -> tuple[str, tuple[str, ...]]:
    """Del mes local en curso, los días ya vencidos sin informe diario legible."""
    today = local_date(now, cfg.zone)
    month = today.strftime("%Y-%m")
    through = (
        today if now >= resolve_local(today, cfg.daily.time, cfg.zone) else today - timedelta(1)
    )
    if through.strftime("%Y-%m") != month:
        return month, ()
    have = reports_in_period(store, month, through=through).days
    due = (f"{month}-{day:02d}" for day in range(1, through.day + 1))
    return month, tuple(day for day in due if day not in have)


def scheduler_status(
    store: WorkspaceStore, loaded: LoadedSchedule, now: datetime
) -> SchedulerStatus:
    cfg = loaded.config
    pending = {(job.kind): job for job in due_jobs(now, cfg, finished_periods(store, now))}
    interrupted, unreadable = interrupted_runs(store, now)
    month, missing = _days_without_report(store, cfg, now)
    return SchedulerStatus(
        config=cfg,
        sha256=config_sha256(cfg),
        is_default=loaded.is_default,
        jobs=(
            JobSchedule("daily", next_daily(now, cfg), pending.get("daily")),
            JobSchedule("monthly", next_monthly(now, cfg), pending.get("monthly")),
        ),
        last_reports={
            "daily": _last_report(store, "daily"),
            "monthly": _last_report(store, "monthly"),
        },
        interrupted=tuple(interrupted),
        unreadable_cycles=tuple(unreadable),
        month=month,
        days_without_report=missing,
    )


def _pending_subject(job: JobSchedule) -> str:
    """El mensual se dispara en el mes siguiente: lo pendiente es el informe del mes anterior."""
    assert job.pending is not None
    if job.kind == "monthly":
        return f"informe de {previous_month(job.pending.period)}"
    return job.pending.period


JOB_LABELS: Mapping[JobKind, str] = {"daily": "diario", "monthly": "mensual"}


def _day_ranges(days: tuple[str, ...]) -> list[str]:
    """Los días en rangos de días seguidos: `2026-10-01 a 2026-10-29`."""
    ranges: list[list[date]] = []
    for day in map(date.fromisoformat, sorted(days)):
        if ranges and day - ranges[-1][-1] == timedelta(days=1):
            ranges[-1][-1] = day
        else:
            ranges.append([day, day])
    return [
        first.isoformat() if first == last else f"{first.isoformat()} a {last.isoformat()}"
        for first, last in ranges
    ]


def render_status(status: SchedulerStatus) -> str:
    cfg = status.config
    source = (
        "valores por defecto (sin reports/schedule.yaml)"
        if status.is_default
        else "reports/schedule.yaml"
    )
    zone = cfg.zone
    lines = [
        f"Configuración efectiva: {source}",
        f"  sha256: {status.sha256}",
        f"  zona horaria: {cfg.timezone}",
        f"  diario: {cfg.daily.time:%H:%M}",
        f"  mensual: día {cfg.monthly.day}",
        f"  reintentos del ciclo: {cfg.cycle.attempts} cada {cfg.cycle.retry_interval_minutes} min",
        f"  copia al final del diario: {'sí' if cfg.backup.enabled else 'no'}",
        "",
        "Próximo disparo:",
    ]
    for job in status.jobs:
        line = f"  {JOB_LABELS[job.kind]}: {job.next_at.astimezone(zone).isoformat()}"
        if job.pending is not None:
            since = job.pending.due_at.astimezone(zone).isoformat()
            line += f" (pendiente ahora: {_pending_subject(job)}, vencido desde {since})"
        lines.append(line)
    lines += ["", "Último informe:"]
    for kind, last in status.last_reports.items():
        if last is None:
            lines.append(f"  {JOB_LABELS[kind]}: ninguno")
            continue
        incomplete = "" if last.published else " (incompleto: falta el HTML)"
        lines.append(f"  {JOB_LABELS[kind]}: {last.period} ({last.stamp}){incomplete}")
    lines += [
        "",
        f"Ciclos interrumpidos: {len(status.interrupted) + len(status.unreadable_cycles)}",
    ]
    lines += [
        f"  {run.run_id}: última etapa {run.last_stage}, iniciado {run.started_at.isoformat()}"
        for run in status.interrupted
    ]
    lines += [f"  {run_id}: marcadores ilegibles" for run_id in status.unreadable_cycles]
    lines += ["", f"Días sin informe diario en {status.month}: {len(status.days_without_report)}"]
    lines += [f"  {days}" for days in _day_ranges(status.days_without_report)]
    return "\n".join(lines) + "\n"
