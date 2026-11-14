import os
from datetime import timedelta

import pytest

from ens_evidence.evidence.importer import import_collected
from ens_evidence.models.contracts import CollectionResult
from ens_evidence.monitoring import box
from ens_evidence.monitoring.model import Status
from ens_evidence.monitoring.thresholds import Thresholds
from ens_evidence.reports.store import write_stage

from .support import NOW

LIMITS = Thresholds()
RUN_ID = "20261114T100000-aaaaaaaa"
PERIOD = "2026-11-14"


@pytest.fixture
def heartbeat(tmp_path, monkeypatch):
    path = tmp_path / "scheduler-heartbeat"
    monkeypatch.setenv("ENS_HEARTBEAT_FILE", str(path))

    def beat(at=NOW):
        path.write_text("1\n")
        os.utime(path, (at.timestamp(), at.timestamp()))

    return beat


def stages(store, *names, at, run_id=RUN_ID):
    for name in names:
        write_stage(store, run_id, name, at=at, period=PERIOD)


def test_scheduler_without_heartbeat_is_red(store, heartbeat):
    assert box.scheduler(store, NOW).status is Status.FAIL


def test_scheduler_hung_while_the_monitor_lives_is_red(store, heartbeat):
    heartbeat(NOW - timedelta(minutes=LIMITS.scheduler_heartbeat_minutes + 1))
    reading = box.scheduler(store, NOW)
    assert reading.status is Status.FAIL
    assert "no late" in reading.detail


def test_scheduler_with_fresh_heartbeat_and_no_trouble_is_not_red(store, heartbeat):
    heartbeat()
    assert box.scheduler(store, NOW).status in (Status.OK, Status.UNKNOWN, Status.WARN)


def test_last_cycle_failed_is_red_even_with_a_fresh_heartbeat(store, heartbeat):
    heartbeat()
    at = NOW - timedelta(hours=2)
    stages(store, "started", "collected", "checked", at=at)
    reading = box.scheduler(store, NOW)
    assert reading.status is Status.FAIL
    assert "fallido" in reading.detail


def test_cycle_still_inside_its_grace_is_not_red(store, heartbeat):
    heartbeat()
    stages(store, "started", at=NOW - timedelta(minutes=5))
    assert box.scheduler(store, NOW).status is not Status.FAIL


def test_failed_backup_stage_of_the_last_cycle_warns(store, heartbeat):
    heartbeat()
    at = NOW - timedelta(hours=1)
    stages(store, "started", "collected", "checked", "reported", at=at)
    write_stage(store, RUN_ID, "backup_failed", at=at, period=PERIOD, backup_code="restic_failed")
    reading = box.scheduler(store, NOW)
    assert reading.status is Status.WARN
    assert "copia" in reading.detail


def repository(*, backend="remote", exists=True, snapshots=(), declaration=True):
    snapshot_items = [{"id": f"s{i}", "time": t.isoformat()} for i, t in enumerate(snapshots)]
    return {
        "id": "externa",
        "asset_id": "backup-externo",
        "collection_status": "SUCCESS",
        "offsite": (
            {
                "reviewed_at": (NOW - timedelta(days=10)).isoformat(),
                "valid_until": (NOW + timedelta(days=100)).isoformat(),
            }
            if declaration
            else None
        ),
        "sections": {
            "repository": {
                "status": "SUCCESS",
                "items": [{"backend": backend, "exists": exists}],
            },
            "snapshots": {"status": "SUCCESS", "items": snapshot_items, "summary": {}},
        },
    }


def store_backups(store, *entries, collected_at=NOW):
    result = CollectionResult(
        source="backups",
        collector="backups",
        collected_at=collected_at,
        asset_id="backups",
        data={"repositories": list(entries)},
        collection_status="SUCCESS",
    )
    import_collected(store, result, collector_version="test", now=collected_at)


def offsite(store, at):
    return box.offsite_backup(store, LIMITS, at)


def test_no_backups_evidence_means_no_reading(store):
    assert offsite(store, NOW) is None


def test_recent_snapshot_of_the_external_destination_is_green(store):
    store_backups(store, repository(snapshots=[NOW - timedelta(hours=3)]))
    assert offsite(store, NOW).status is Status.OK


def test_snapshot_age_grows_every_tick_while_the_data_is_still_fresh(store):
    store_backups(store, repository(snapshots=[NOW - timedelta(hours=23)]))
    assert offsite(store, NOW + timedelta(hours=1)).status is Status.OK
    assert offsite(store, NOW + timedelta(hours=4)).status is Status.FAIL


def test_expired_evidence_is_unknown_never_green(store):
    store_backups(store, repository(snapshots=[NOW - timedelta(hours=1)]))
    reading = offsite(store, NOW + timedelta(hours=LIMITS.backup_evidence_hours + 1))
    assert reading.status is Status.UNKNOWN


def test_local_repository_is_not_an_external_destination(store):
    store_backups(store, repository(backend="local", snapshots=[NOW]))
    assert offsite(store, NOW).status is Status.UNKNOWN


def test_remote_without_a_valid_declaration_is_unknown(store):
    store_backups(store, repository(snapshots=[NOW], declaration=False))
    assert offsite(store, NOW).status is Status.UNKNOWN


def test_repository_without_snapshots_is_red(store):
    store_backups(store, repository(snapshots=[]))
    assert offsite(store, NOW).status is Status.FAIL


def test_a_fresh_local_copy_does_not_hide_a_stale_external_one(store):
    store_backups(
        store,
        repository(backend="local", snapshots=[NOW]),
        repository(snapshots=[NOW - timedelta(hours=40)]),
    )
    assert offsite(store, NOW).status is Status.FAIL
