"""Criterios de aceptación de `kit-monitoring`, de punta a punta con transportes falsos."""

import json
import os
import threading
from datetime import timedelta
from pathlib import Path

import pytest
from starlette.testclient import TestClient
from ui.conftest import NOW as UI_NOW
from ui.conftest import ORIGIN, UiConfig, auth, jwks_for, make_key, mint, seed_example_scope

from ens_evidence.agent.monitoring import MonitoringStore
from ens_evidence.backup.runner import run_backup
from ens_evidence.monitoring import Monitor, MonitorLoop, Status, load_snapshot
from ens_evidence.monitoring.advisories import make_client
from ens_evidence.monitoring.history import RETENTION_DAYS
from ens_evidence.ui.app import create_ui_app
from ens_evidence.ui.routes import ui_routes
from ens_evidence.ui.views.matrix import _status as matrix_rows
from ens_evidence.workspace import LocalStore

from .support import (
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
NOW = UI_NOW


@pytest.fixture
def state(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "state"
    monkeypatch.setenv("ENS_STATE_DIR", str(path))
    beat = tmp_path / "scheduler-heartbeat"
    beat.write_text("1\n")
    os.utime(beat, (NOW.timestamp(), NOW.timestamp()))
    monkeypatch.setenv("ENS_HEARTBEAT_FILE", str(beat))
    return path


@pytest.fixture
def workspace(tmp_path) -> LocalStore:
    root = tmp_path / "ws"
    root.mkdir()
    return seed_example_scope(LocalStore(root))


def feed_failing_world(state: Path, at=NOW) -> None:
    MonitoringStore(state).record(
        [
            sample("mon_containers", [DOWN_BACKREST], at=at),
            sample("mon_disk", [FULL_DISK], at=at),
        ]
    )


def monitor(workspace, state, *, env=None, smtp=None, clock=lambda: NOW) -> Monitor:
    return Monitor(
        workspace,
        state_dir=state,
        env=env if env is not None else {},
        clock=clock,
        github=make_client(github_transport()),
        openbao_transport=unreachable_transport(),
        smtp_factory=smtp,
    )


def run_one_tick(mon: Monitor) -> None:
    """Un único tick de `MonitorLoop`: el bucle real, parado por su propio evento al acabar."""
    stop = threading.Event()
    real_tick = mon.tick

    def tick_then_stop():
        try:
            return real_tick()
        finally:
            stop.set()

    mon.tick = tick_then_stop
    loop = MonitorLoop(mon, stop, first_delay_seconds=0)
    loop.start()
    loop.join(timeout=10)
    assert not loop.is_alive()


@pytest.fixture
def ui(tmp_path, workspace):
    key = make_key()
    config = UiConfig(
        origin=ORIGIN,
        access_aud="aud-tag-123",
        team_domain="acme.cloudflareaccess.com",
        jwks_file=tmp_path / "jwks.json",
    )
    app = create_ui_app(config, workspace, lambda: NOW, lambda: jwks_for(key), routes=ui_routes())
    client = TestClient(app, base_url=ORIGIN)
    headers = auth(mint(key))
    return lambda path: client.get(path, headers=headers, follow_redirects=False)


def workspace_contents(workspace: LocalStore) -> dict[str, bytes]:
    return {key: workspace.read_bytes(key) for key in workspace.list("")}


def test_panel_shows_two_reds_with_runbooks_mails_and_leaves_the_matrix_alone(state, workspace, ui):
    ui("/matriz")  # la primera visita crea la matriz; la referencia es la segunda
    rows_before = matrix_rows(workspace, NOW)
    contents_before = workspace_contents(workspace)
    feed_failing_world(state)
    smtp = fake_smtp()

    run_one_tick(monitor(workspace, state, env=SMTP_ENV, smtp=smtp))
    page = ui("/monitorizacion")

    assert page.status_code == 200
    for title in ("Copias de seguridad", "Disco"):
        assert title in page.text
    assert page.text.count("tone-bad") >= 2
    assert 'href="/ayuda/kit-copias"' in page.text
    assert 'href="/ayuda/monitorizacion"' in page.text
    assert "2026-11-08 12:00 UTC" in page.text  # desde cuándo
    assert len(smtp.sent) == 1
    assert "Disco" in smtp.sent[0].get_content()
    assert workspace_contents(workspace) == contents_before
    assert matrix_rows(workspace, NOW) == rows_before


def test_a_removed_container_turns_its_tool_red_since_it_disappeared(state, workspace, ui):
    running = {**DOWN_BACKREST, "state": "running", "status": "Up 2 hours"}
    MonitoringStore(state).record([sample("mon_containers", [running], at=NOW)])
    run_one_tick(monitor(workspace, state))
    later = NOW + timedelta(minutes=10)
    MonitoringStore(state).record([sample("mon_containers", [], at=later)])
    smtp = fake_smtp()

    run_one_tick(monitor(workspace, state, env=SMTP_ENV, smtp=smtp, clock=lambda: later))
    snapshot = load_snapshot(state, later)

    tool = next(i for i in snapshot.items if i.id == "tool:copias:up")
    assert tool.status is Status.FAIL and tool.since == later
    assert "Copias de seguridad" in ui("/monitorizacion").text
    assert len(smtp.sent) == 1


def _copias_rows(state, workspace) -> list:
    run_one_tick(monitor(workspace, state))
    return [i for i in load_snapshot(state, NOW).items if i.id.startswith("tool:copias")]


def test_an_enabled_tool_without_history_or_checks_and_no_container_is_red(
    state, workspace, monkeypatch
):
    monkeypatch.setenv("ENS_KIT_PROFILES", "copias")
    MonitoringStore(state).record([sample("mon_containers", [], at=NOW)])

    rows = _copias_rows(state, workspace)

    up = next(i for i in rows if i.id == "tool:copias:up")
    assert up.status is Status.FAIL and up.since == NOW


def test_an_enabled_tool_without_a_fresh_sample_is_unknown(state, workspace, monkeypatch):
    monkeypatch.setenv("ENS_KIT_PROFILES", "copias")

    rows = _copias_rows(state, workspace)

    assert next(i for i in rows if i.id == "tool:copias:up").status is Status.UNKNOWN


def test_a_tool_not_in_the_profiles_is_not_shown_even_with_its_container(
    state, workspace, monkeypatch
):
    monkeypatch.setenv("ENS_KIT_PROFILES", "")
    running = {**DOWN_BACKREST, "state": "running", "status": "Up 2 hours"}
    MonitoringStore(state).record([sample("mon_containers", [running], at=NOW)])

    assert _copias_rows(state, workspace) == []


def test_without_the_variable_a_missing_container_without_history_is_not_shown(
    state, workspace, monkeypatch
):
    monkeypatch.delenv("ENS_KIT_PROFILES", raising=False)
    MonitoringStore(state).record([sample("mon_containers", [], at=NOW)])

    assert _copias_rows(state, workspace) == []


def test_without_the_variable_a_present_container_still_shows_the_tool(
    state, workspace, monkeypatch
):
    monkeypatch.delenv("ENS_KIT_PROFILES", raising=False)
    running = {**DOWN_BACKREST, "state": "running", "status": "Up 2 hours"}
    MonitoringStore(state).record([sample("mon_containers", [running], at=NOW)])

    assert _copias_rows(state, workspace) != []


def test_every_page_carries_the_failing_banner(state, workspace, ui):
    feed_failing_world(state)
    run_one_tick(monitor(workspace, state))

    assert "Copias de seguridad" in ui("/herramientas").text


def test_without_smtp_the_web_still_reports_and_nothing_crashes(state, workspace, ui):
    feed_failing_world(state)

    run_one_tick(monitor(workspace, state))

    assert "tone-bad" in ui("/monitorizacion").text


def test_a_failing_smtp_is_recorded_and_the_loop_keeps_ticking(state, workspace):
    feed_failing_world(state)
    mon = monitor(workspace, state, env=SMTP_ENV, smtp=fake_smtp(starttls=False))
    stop = threading.Event()
    ticks = []
    real_tick = mon.tick

    def tick():
        ticks.append(real_tick())
        if len(ticks) == 2:
            stop.set()

    mon.tick = tick
    mon.interval_seconds = 0
    loop = MonitorLoop(mon, stop, first_delay_seconds=0)
    loop.start()
    loop.join(timeout=10)

    assert len(ticks) == 2
    assert ticks[0].mail.last_ok is False and ticks[1].mail.last_ok is False
    assert "s3cret-password" not in json.dumps(ticks[1].mail.last_error)


def test_an_expired_signal_is_unknown_not_red_or_green(state, workspace):
    feed_failing_world(state, at=NOW - timedelta(hours=2))

    snapshot = monitor(workspace, state).tick()

    by_id = {i.id: i.status for i in snapshot.items}
    assert by_id["vps:disk:/"] is Status.UNKNOWN
    assert Status.FAIL not in by_id.values()


def test_a_restarted_loop_does_not_repeat_the_mail_within_24_hours(state, workspace):
    feed_failing_world(state)
    first_smtp = fake_smtp()
    monitor(workspace, state, env=SMTP_ENV, smtp=first_smtp).tick()

    restarted_smtp = fake_smtp()
    later = NOW + timedelta(hours=1)
    os.utime(os.environ["ENS_HEARTBEAT_FILE"], (later.timestamp(), later.timestamp()))
    MonitoringStore(state).record(
        [
            sample("mon_containers", [DOWN_BACKREST], at=later),
            sample("mon_disk", [FULL_DISK], at=later),
        ]
    )
    snapshot = monitor(
        workspace, state, env=SMTP_ENV, smtp=restarted_smtp, clock=lambda: later
    ).tick()

    assert len(first_smtp.sent) == 1 and restarted_smtp.sent == []
    assert next(i for i in snapshot.items if i.id == "vps:disk:/").since == NOW


def test_history_older_than_30_days_is_pruned(state, workspace):
    feed_failing_world(state)
    monitor(workspace, state).tick()
    history = state / "monitoring" / "history"
    old = NOW - timedelta(days=RETENTION_DAYS + 1)
    before = {p.name for p in history.rglob("*") if p.is_file()}

    far_future = NOW + timedelta(days=RETENTION_DAYS + 2)
    feed_failing_world(state, at=far_future)
    monitor(workspace, state, clock=lambda: far_future).tick()

    text = "\n".join(p.read_text() for p in history.rglob("*") if p.is_file())
    assert before and old.isoformat()[:10] not in text
    assert NOW.isoformat()[:10] not in text


class RecordingRestic:
    def __init__(self):
        self.exported: list[str] = []

    def backup(self, directory: Path) -> str:
        self.exported = sorted(
            str(p.relative_to(directory)) for p in directory.rglob("*") if p.is_file()
        )
        return "snapshot-1"


def test_state_monitoring_is_not_part_of_the_evidence_backup(state, workspace):
    feed_failing_world(state)
    monitor(workspace, state).tick()
    assert any((state / "monitoring").rglob("*.json"))
    restic = RecordingRestic()

    run = run_backup(workspace, restic, "repo", None, lambda: NOW)

    assert run.status == "completed"
    assert restic.exported
    assert not [name for name in restic.exported if "monitoring" in name or "snapshot.json" in name]
    assert not [key for key in workspace.list("") if "monitoring" in key.split("/")[0:1]]


def test_state_monitoring_is_outside_the_workspace_even_with_a_default_state_dir(state, workspace):
    monitor(workspace, state).tick()

    assert not list(workspace.list("state"))
    assert (state / "monitoring").is_dir()
