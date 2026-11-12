"""Las peticiones de pre-auditoría de la UI se comprueban en cada tick, bajo el lock del ciclo."""

import threading
from datetime import UTC, datetime
from types import SimpleNamespace

from ens_evidence.scheduling import scheduler as scheduler_module
from ens_evidence.scheduling.calendar import Job, due_jobs
from ens_evidence.scheduling.scheduler import CycleExecutor, JobResult, Scheduler
from ens_evidence.workspace.cycle_lock import CycleBusy, CycleLock
from scheduling.fakes import FakeClock, FakeExecutor
from scheduling.test_scheduler import CFG

KEY = "evidence/preaudits/requests/20261102T000000000000Z-aaaaaaaaaaaa/000001.json"
EARLY = datetime(2026, 11, 2, 0, 0, tzinfo=UTC)


def test_a_request_is_due_at_once_and_only_until_done():
    assert [(j.kind, j.period) for j in due_jobs(EARLY, CFG, set(), [KEY])] == [("preaudit", KEY)]
    assert due_jobs(EARLY, CFG, {("preaudit", KEY)}, [KEY]) == []


def test_every_tick_looks_for_new_requests_and_runs_each_once():
    clock, stop, done, live = FakeClock(EARLY), threading.Event(), set(), []
    executor = FakeExecutor(clock, done)
    scheduler = Scheduler(
        clock, executor, lambda: CFG, lambda: set(done), stop, requests=lambda now: list(live)
    )

    assert scheduler.tick() == []
    live.append(KEY)
    ran = scheduler.tick()
    again = scheduler.tick()

    assert [(job.kind, job.period, result.status) for job, result in ran] == [
        ("preaudit", KEY, "completed")
    ]
    assert again == []


def test_an_unreadable_request_listing_does_not_stop_the_scheduler():
    clock, stop, logs = FakeClock(EARLY), threading.Event(), []

    def broken(now):
        raise OSError("disco")

    scheduler = Scheduler(
        clock, FakeExecutor(clock, set()), lambda: CFG, set, stop, log=logs.append, requests=broken
    )

    assert scheduler.tick() == []
    assert any("event=requests_unreadable" in line for line in logs)


def request_executor(clock, stop, tmp_path) -> CycleExecutor:
    return CycleExecutor(
        None,  # type: ignore[arg-type]
        SimpleNamespace(preaudit="ajustes"),  # type: ignore[arg-type]
        {},
        clock,
        stop,
        lock_wait_minutes=1.0,
        lock_factory=lambda _settings, _env: CycleLock("ws", tmp_path / "state"),
    )


def test_the_executor_runs_a_request_with_the_cycle_lock_held(tmp_path, monkeypatch):
    held = []

    def fake_execute(store, key, settings, *, now, client_factory):
        try:
            CycleLock("ws", tmp_path / "state").acquire()
        except CycleBusy:
            held.append((key, settings))

    monkeypatch.setattr(scheduler_module, "execute_request", fake_execute)

    result = request_executor(FakeClock(EARLY), threading.Event(), tmp_path).run(
        Job("preaudit", KEY, EARLY), CFG, "scheduled"
    )

    assert result == JobResult("completed")
    assert held == [(KEY, "ajustes")]


def test_a_busy_lock_defers_the_request_without_executing_it(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler_module, "execute_request", lambda *a, **k: calls.append(a))
    holder = CycleLock("ws", tmp_path / "state").acquire()

    result = request_executor(FakeClock(EARLY), threading.Event(), tmp_path).run(
        Job("preaudit", KEY, EARLY), CFG, "scheduled"
    )

    assert result == JobResult("busy", "lock_busy")
    assert calls == []
    holder.release()


def test_a_crash_while_executing_is_a_failed_job_and_releases_the_lock(tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("fallo")

    monkeypatch.setattr(scheduler_module, "execute_request", boom)

    result = request_executor(FakeClock(EARLY), threading.Event(), tmp_path).run(
        Job("preaudit", KEY, EARLY), CFG, "scheduled"
    )

    assert result == JobResult("failed", "request_crashed:RuntimeError")
    CycleLock("ws", tmp_path / "state").acquire().release()
