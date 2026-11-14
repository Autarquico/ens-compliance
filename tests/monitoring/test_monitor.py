import threading
from datetime import timedelta

import pytest

from ens_evidence.agent.monitoring import MonitoringStore
from ens_evidence.monitoring import Monitor, MonitorLoop, Status, load_snapshot, render_status
from ens_evidence.monitoring.advisories import make_client
from ens_evidence.monitoring.mail import SmtpSettings
from ens_evidence.monitoring.snapshot import STALE_AFTER_INTERVALS

from .support import (
    NOW,
    SMTP_ENV,
    fake_smtp,
    github_transport,
    sample,
    unreachable_transport,
)

DOWN_BACKREST = {
    "name": "ens-box-backrest-1",
    "compose_project": "ens-box",
    "compose_service": "backrest",
    "state": "exited",
    "status": "Exited (1) 3 minutes ago",
    "image": "ghcr.io/garethgeorge/backrest:v1.14.1-scratch",
}
FULL_DISK = {"path": "/", "type": "ext4", "blocks": "1000", "blocks_available": "20"}


@pytest.fixture(autouse=True)
def state_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ENS_STATE_DIR", str(tmp_path / "state"))
    return tmp_path / "state"


def monitor(store, state, *, env=None, smtp=None, clock=lambda: NOW, github=None):
    return Monitor(
        store,
        state_dir=state,
        env=env if env is not None else {},
        clock=clock,
        github=make_client(github or github_transport()),
        openbao_transport=unreachable_transport(),
        smtp_factory=smtp,
    )


def feed_samples(state, *samples):
    MonitoringStore(state).record(list(samples))


def failing_world(state):
    feed_samples(
        state,
        sample("mon_containers", [DOWN_BACKREST]),
        sample("mon_disk", [FULL_DISK]),
    )


def test_tool_down_and_disk_over_threshold_are_red_with_since_runbook_and_one_mail(
    store, state_env
):
    failing_world(state_env)
    smtp = fake_smtp()

    snapshot = monitor(store, state_env, env=SMTP_ENV, smtp=smtp).tick()

    tool = next(i for i in snapshot.items if i.id == "tool:copias:up")
    disk = next(i for i in snapshot.items if i.id == "vps:disk:/")
    assert (tool.status, disk.status) == (Status.FAIL, Status.FAIL)
    assert tool.since == disk.since == NOW
    assert tool.runbook == "kit-copias" and disk.runbook == "monitorizacion"
    assert len(smtp.sent) == 1
    body = smtp.sent[0].get_content()
    assert "Copias de seguridad" in body and "Disco" in body
    assert "s3cret-password" not in body
    assert snapshot.mail.configured and snapshot.mail.last_ok is True


def test_second_tick_does_not_repeat_the_mail_and_keeps_since(store, state_env):
    failing_world(state_env)
    smtp = fake_smtp()
    now = [NOW]
    mon = monitor(store, state_env, env=SMTP_ENV, smtp=smtp, clock=lambda: now[0])

    mon.tick()
    now[0] = NOW + timedelta(minutes=15)
    second = mon.tick()

    assert len(smtp.sent) == 1
    assert next(i for i in second.items if i.id == "vps:disk:/").since == NOW


def test_without_smtp_alerts_stay_in_the_web(store, state_env):
    failing_world(state_env)

    snapshot = monitor(store, state_env).tick()

    assert not snapshot.mail.configured and snapshot.mail.last_attempt is None
    assert snapshot.failing()
    assert "solo en la web" in render_status(snapshot, False, NOW)


def test_smtp_failure_is_recorded_not_raised_and_retried_next_tick(store, state_env):
    failing_world(state_env)
    broken = fake_smtp(starttls=False)
    first = monitor(store, state_env, env=SMTP_ENV, smtp=broken).tick()
    assert first.mail.last_ok is False and first.mail.last_error == "SMTPNotSupportedError"

    working = fake_smtp()
    retry = monitor(
        store, state_env, env=SMTP_ENV, smtp=working, clock=lambda: NOW + timedelta(minutes=15)
    ).tick()

    assert len(working.sent) == 1 and retry.mail.last_ok is True


def test_everything_unknown_without_samples_and_never_green(store, state_env):
    snapshot = monitor(store, state_env).tick()

    vps = snapshot.group("vps")
    assert vps and all(i.status is not Status.OK for i in vps)
    assert Status.UNKNOWN in {i.status for i in vps}


def test_expired_samples_turn_unknown(store, state_env):
    old = NOW - timedelta(hours=2)
    feed_samples(state_env, sample("mon_disk", [{**FULL_DISK, "blocks_available": "900"}], at=old))

    snapshot = monitor(store, state_env).tick()

    assert next(i for i in snapshot.items if i.id == "vps:disk:/").status is Status.UNKNOWN


def test_snapshot_round_trips_and_goes_stale_after_two_intervals(store, state_env):
    failing_world(state_env)
    mon = monitor(store, state_env)
    mon.tick()

    fresh = load_snapshot(state_env, NOW + timedelta(minutes=1))
    late = load_snapshot(
        state_env, NOW + timedelta(seconds=STALE_AFTER_INTERVALS * mon.interval_seconds + 1)
    )

    assert not fresh.stale and late.stale
    assert fresh.items[0].series  # el tick dejó historia
    assert "DESACTUALIZADA" in render_status(late, False, NOW)
    assert load_snapshot(state_env / "nada", NOW) is None


def test_monitoring_writes_only_under_state_monitoring_and_not_to_the_workspace(store, state_env):
    failing_world(state_env)
    before = store.list("")

    monitor(store, state_env).tick()

    assert store.list("") == before
    written = {p.relative_to(state_env).parts[0] for p in state_env.rglob("*") if p.is_file()}
    assert written == {"monitoring"}


def test_a_probe_that_explodes_does_not_stop_the_tick(store, state_env, monkeypatch):
    from ens_evidence.monitoring import box

    def boom(*args):
        raise RuntimeError("fallo interno con detalle")

    monkeypatch.setattr(box, "scheduler", boom)

    snapshot = monitor(store, state_env).tick()

    assert snapshot.group("vps")


def test_loop_survives_a_failing_tick_and_stops_with_the_event(store, state_env, monkeypatch):
    mon = monitor(store, state_env)
    stop = threading.Event()
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise OSError("disco lleno")
        stop.set()

    monkeypatch.setattr(mon, "tick", flaky)
    mon.interval_seconds = 0
    loop = MonitorLoop(mon, stop, first_delay_seconds=0)

    loop.start()
    loop.join(timeout=5)

    assert not loop.is_alive() and len(calls) == 2


def test_loop_waits_before_the_first_tick_and_stop_cancels_it(store, state_env, monkeypatch):
    mon = monitor(store, state_env)
    stop = threading.Event()
    monkeypatch.setattr(mon, "tick", lambda: pytest.fail("no debía correr"))
    loop = MonitorLoop(mon, stop)
    stop.set()

    loop.start()
    loop.join(timeout=5)

    assert not loop.is_alive()


def test_smtp_settings_from_env_is_used_by_the_monitor_only_when_complete():
    assert SmtpSettings.from_env({**SMTP_ENV, "ENS_SMTP_HOST": ""}) is None
