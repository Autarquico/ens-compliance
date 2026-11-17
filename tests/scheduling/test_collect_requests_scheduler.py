"""Las peticiones de recogida de la UI se comprueban en cada tick, bajo el lock del ciclo."""

import json
import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from ens_evidence.scheduling import scheduler as scheduler_module
from ens_evidence.scheduling.calendar import Job, due_jobs
from ens_evidence.scheduling.collect_requests import pending_keys as pending_collect_keys
from ens_evidence.scheduling.collect_requests import request_collect
from ens_evidence.scheduling.scheduler import CycleExecutor, JobResult, Scheduler
from ens_evidence.workspace.cycle_lock import CycleBusy, CycleLock
from scheduling.fakes import FakeClock, FakeExecutor
from scheduling.test_scheduler import CFG

KEY = "evidence/collect-requests/github/000001.json"
OTHER = "evidence/collect-requests/github/000002.json"
PREAUDIT_KEY = "evidence/preaudits/requests/20261102T000000000000Z-aaaaaaaaaaaa/000001.json"
EARLY = datetime(2026, 11, 2, 0, 0, tzinfo=UTC)
MONTHLY_DAY = datetime(2026, 11, 28, 12, 0, tzinfo=UTC)


def scheduler_with(clock, done, *, collect=None, preaudit=None, logs=None, script=None):
    executor = FakeExecutor(clock, done, script)
    scheduler = Scheduler(
        clock,
        executor,
        lambda: CFG,
        lambda: set(done),
        threading.Event(),
        log=(logs if logs is not None else []).append,
        requests=preaudit,
        collect_requests=collect,
    )
    return scheduler, executor


def test_a_collect_request_is_due_at_once_and_only_until_done():
    requests = [("collect", KEY)]
    assert [(j.kind, j.period) for j in due_jobs(EARLY, CFG, set(), requests)] == [("collect", KEY)]
    assert due_jobs(EARLY, CFG, {("collect", KEY)}, requests) == []


def test_every_tick_looks_for_new_requests_and_runs_each_once():
    clock, done, live = FakeClock(EARLY), set(), []
    scheduler, _ = scheduler_with(clock, done, collect=lambda now: list(live))

    assert scheduler.tick() == []
    live.append(KEY)
    ran = scheduler.tick()
    live.append(OTHER)
    later = scheduler.tick()
    again = scheduler.tick()

    assert [(j.kind, j.period, r.status) for j, r in ran] == [("collect", KEY, "completed")]
    assert [(j.kind, j.period) for j, _ in later] == [("collect", OTHER)]
    assert again == []


def test_a_request_created_during_another_execution_is_picked_up_on_the_next_tick():
    clock, done, live = FakeClock(EARLY), set(), [PREAUDIT_KEY]
    scheduler, executor = scheduler_with(
        clock, done, preaudit=lambda now: [PREAUDIT_KEY], collect=lambda now: list(live[1:])
    )
    executor.during = lambda: live.append(KEY)

    first = scheduler.tick()
    second = scheduler.tick()

    assert [(j.kind, j.period) for j, _ in first] == [("preaudit", PREAUDIT_KEY)]
    assert [(j.kind, j.period) for j, _ in second] == [("collect", KEY)]


def test_a_busy_collect_request_spends_no_attempts_and_is_rediscovered_next_tick():
    clock, done, logs = FakeClock(EARLY), set(), []
    busy = JobResult("busy", "lock_busy")
    scheduler, executor = scheduler_with(
        clock, done, collect=lambda now: [KEY], logs=logs, script=[busy] * 5
    )

    results = [scheduler.tick() for _ in range(5)]
    done_after = scheduler.tick()

    assert all([r.status for _, r in tick] == ["busy"] for tick in results)
    assert executor.periods == [("collect", KEY)] * 6  # sin espera de reintento entre ticks
    assert [r.status for _, r in done_after] == ["completed"]
    assert not any("event=job_failed" in line or "event=job_exhausted" in line for line in logs)
    assert scheduler.tick() == []


def test_unreadable_queues_do_not_silence_each_other():
    def broken(now):
        raise OSError("disco")

    for preaudit, collect, expected, diagnostic in [
        (broken, lambda now: [KEY], ("collect", KEY), "event=requests_unreadable"),
        (lambda now: [PREAUDIT_KEY], broken, ("preaudit", PREAUDIT_KEY), "event=collect_requests"),
    ]:
        logs = []
        scheduler, _ = scheduler_with(
            FakeClock(EARLY), set(), preaudit=preaudit, collect=collect, logs=logs
        )

        ran = scheduler.tick()

        assert [(j.kind, j.period) for j, _ in ran] == [expected]
        assert any(diagnostic in line for line in logs)


def test_requests_run_after_the_daily_and_monthly_and_preaudit_before_collect():
    clock, done = FakeClock(MONTHLY_DAY), set()
    scheduler, executor = scheduler_with(
        clock, done, preaudit=lambda now: [PREAUDIT_KEY], collect=lambda now: [OTHER, KEY]
    )

    scheduler.tick()

    assert [kind for kind, _ in executor.periods] == [
        "daily",
        "monthly",
        "preaudit",
        "collect",
        "collect",
    ]
    assert [period for kind, period in executor.periods if kind == "collect"] == [OTHER, KEY]


def test_collect_requests_of_several_connectors_run_oldest_first_not_by_connector_name(store):
    first = request_collect(store, "github", email="ana@clinica.example", now=EARLY)
    store.write_text(
        "evidence/collect-requests/aaa/000001.json",
        first_document("aaa", EARLY + timedelta(minutes=5)),
    )
    now = EARLY + timedelta(minutes=6)
    clock, done = FakeClock(now), set()
    scheduler, executor = scheduler_with(
        clock, done, collect=lambda at: pending_collect_keys(store, at)
    )

    scheduler.tick()

    assert [period for _, period in executor.periods] == [
        first.key,
        "evidence/collect-requests/aaa/000001.json",
    ]


def first_document(connector, at):
    return json.dumps(
        {
            "format": "ens-collect-request/1",
            "connector": connector,
            "email": "ana@clinica.example",
            "requested_at": at.isoformat(),
        }
    )


def request_executor(clock, stop, tmp_path) -> CycleExecutor:
    return CycleExecutor(
        None,  # type: ignore[arg-type]
        SimpleNamespace(),  # type: ignore[arg-type]
        {},
        clock,
        stop,
        lock_wait_minutes=1.0,
        lock_factory=lambda _settings, _env: CycleLock("ws", tmp_path / "state"),
        plugins="plugins",  # type: ignore[arg-type]
    )


def test_the_executor_runs_a_collect_request_with_the_cycle_lock_held(tmp_path, monkeypatch):
    held = []

    def fake_execute(store, settings, key, *, plugins, cfg, clock):
        try:
            CycleLock("ws", tmp_path / "state").acquire()
        except CycleBusy:
            held.append((key, plugins, cfg))

    monkeypatch.setattr(scheduler_module, "execute_collect_request", fake_execute)

    result = request_executor(FakeClock(EARLY), threading.Event(), tmp_path).run(
        Job("collect", KEY, EARLY), CFG, "scheduled"
    )

    assert result == JobResult("completed")
    assert held == [(KEY, "plugins", CFG.collect)]
    CycleLock("ws", tmp_path / "state").acquire().release()


def test_a_busy_lock_defers_the_collect_request_without_executing_it(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        scheduler_module, "execute_collect_request", lambda *a, **k: calls.append(a)
    )
    holder = CycleLock("ws", tmp_path / "state").acquire()

    result = request_executor(FakeClock(EARLY), threading.Event(), tmp_path).run(
        Job("collect", KEY, EARLY), CFG, "scheduled"
    )

    assert result == JobResult("busy", "lock_busy")
    assert calls == []
    holder.release()


def test_a_crash_is_a_failed_job_and_releases_the_lock(tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("fallo")

    monkeypatch.setattr(scheduler_module, "execute_collect_request", boom)

    result = request_executor(FakeClock(EARLY), threading.Event(), tmp_path).run(
        Job("collect", KEY, EARLY), CFG, "scheduled"
    )

    assert result == JobResult("failed", "collect_request_crashed:RuntimeError")
    CycleLock("ws", tmp_path / "state").acquire().release()


def test_stopping_during_a_collect_request_is_an_interruption_and_releases_the_lock(
    tmp_path, monkeypatch
):
    def stopped(*args, **kwargs):
        raise scheduler_module._Stopped

    monkeypatch.setattr(scheduler_module, "execute_collect_request", stopped)

    result = request_executor(FakeClock(EARLY), threading.Event(), tmp_path).run(
        Job("collect", KEY, EARLY), CFG, "scheduled"
    )

    assert result == JobResult("interrupted")
    CycleLock("ws", tmp_path / "state").acquire().release()
