from datetime import timedelta

import pytest

from ens_evidence.endpoint.pack import pack_sha256
from ens_evidence.evidence import list_manifests
from ens_evidence.evidence.verify import verify_all

from .protocol_support import (
    LAPTOP,
    LAPTOP_UUID,
    NOW,
    OS_VERSION_ROW,
    SERVER,
    SERVER_HOST,
    SERVER_UUID,
    snapshot_event,
)


def send(world, node_key, events, log_type="result"):
    return world.post("log", {"node_key": node_key, "log_type": log_type, "data": events})


def manifests(world):
    return list_manifests(world.workspace).manifests


def test_snapshot_result_is_stored_as_verifiable_collector_evidence(world):
    node_key = world.enroll()
    events = [
        snapshot_event("os_version", [OS_VERSION_ROW]),
        snapshot_event("system_info", [{"uuid": LAPTOP_UUID, "hostname": "lap-ana", "cpu": "M2"}]),
    ]

    response = send(world, node_key, events)

    assert response.status_code == 200
    (manifest,) = manifests(world)
    assert (manifest.source, manifest.asset_id) == ("osquery-agent", LAPTOP)
    assert manifest.provenance.kind == "collector"
    assert manifest.provenance.submitted_by == "collector:osquery-agent"
    assert verify_all(world.workspace).ok
    data = _artifact(world, manifest)["data"]
    assert data["queries"]["os_version"]["rows"] == [
        {
            k: OS_VERSION_ROW[k]
            for k in ("name", "platform", "version", "major", "minor", "patch", "build", "codename")
        }
    ]
    assert data["queries"]["os_version"]["status"] == "SUCCESS"
    assert data["queries"]["system_info"] == {
        "status": "SUCCESS",
        "exit_code": 0,
        "permission_hint": False,
        "unsupported_hint": False,
        "row_count": 1,
        "rows": [],
        "collected_at": NOW.isoformat(),
    }
    assert data["agent"]["pack_sha256"] == pack_sha256()
    assert LAPTOP_UUID not in str(data)


def _artifact(world, manifest):
    import json

    return json.loads(world.workspace.read_bytes(manifest.artifact.key))


def test_status_logs_are_discarded(world):
    node_key = world.enroll()
    before = world.workspace.list("")

    response = send(world, node_key, [{"line": 1, "message": "secreto"}], log_type="status")

    assert response.status_code == 200
    assert world.workspace.list("") == before


@pytest.mark.parametrize(
    "event",
    [
        snapshot_event("secret_dump", [{"a": "b"}]),
        snapshot_event("deb_packages", [{"name": "bash"}]),  # solo linux; el host es darwin
        snapshot_event("os_version", [OS_VERSION_ROW], host=SERVER_UUID),
        snapshot_event("os_version", [OS_VERSION_ROW], unix=NOW + timedelta(minutes=6)),
        snapshot_event("os_version", [{"name": 1}]),
        snapshot_event("os_version", "no es una lista"),
        {**snapshot_event("os_version", [OS_VERSION_ROW]), "unixTime": "1"},
        {**snapshot_event("os_version", [OS_VERSION_ROW]), "name": ["os_version"]},
        {
            "action": "added",
            "name": "os_version",
            "hostIdentifier": LAPTOP_UUID,
            "unixTime": 1,
            "columns": OS_VERSION_ROW,
        },
        "texto",
        None,
    ],
    ids=[
        "unknown_name",
        "wrong_platform",
        "other_host",
        "future",
        "non_string_value",
        "snapshot_not_list",
        "time_not_int",
        "name_not_str",
        "differential",
        "str",
        "null",
    ],
)
def test_events_that_do_not_qualify_are_ignored(world, event):
    node_key = world.enroll()
    before = world.workspace.list("")

    response = send(world, node_key, [event])

    assert response.status_code == 200
    assert world.workspace.list("") == before


def test_qualifying_events_are_kept_next_to_ignored_ones(world):
    node_key = world.enroll()

    send(
        world,
        node_key,
        [
            snapshot_event("secret_dump", [{"a": "b"}]),
            snapshot_event("os_version", [OS_VERSION_ROW]),
        ],
    )

    (manifest,) = manifests(world)
    assert list(_artifact(world, manifest)["data"]["queries"]) == ["os_version"]


def test_event_slightly_ahead_of_the_clock_is_accepted(world):
    node_key = world.enroll()

    send(
        world,
        node_key,
        [snapshot_event("os_version", [OS_VERSION_ROW], unix=NOW + timedelta(minutes=4))],
    )

    assert len(manifests(world)) == 1


def test_collected_at_is_the_oldest_snapshot_of_the_batch(world):
    node_key = world.enroll()
    older = NOW - timedelta(hours=2)

    send(
        world,
        node_key,
        [
            snapshot_event("os_version", [OS_VERSION_ROW]),
            snapshot_event("osquery_info", [{"version": "5.14.1"}], unix=older),
        ],
    )

    (manifest,) = manifests(world)
    assert manifest.collected_at == older


def test_retry_of_the_same_batch_is_imported_once(world):
    node_key = world.enroll()
    events = [snapshot_event("os_version", [OS_VERSION_ROW])]

    for _ in range(3):
        assert send(world, node_key, events).status_code == 200

    assert len(manifests(world)) == 1
    assert verify_all(world.workspace).ok


def test_locked_workspace_answers_503_and_writes_nothing_then_retry_imports_once(world):
    node_key = world.enroll()
    events = [snapshot_event("os_version", [OS_VERSION_ROW])]
    before = world.files()

    with world.lock():
        busy = send(world, node_key, events)
        assert busy.status_code == 503
        assert busy.headers["retry-after"]
        assert "node_invalid" not in busy.json()
        files_during = {k: v for k, v in world.files().items() if not k.endswith(".lock")}
    assert files_during == {k: v for k, v in before.items() if not k.endswith(".lock")}
    assert manifests(world) == []

    assert send(world, node_key, events).status_code == 200
    assert send(world, node_key, events).status_code == 200
    assert len(manifests(world)) == 1


def test_log_with_nothing_to_store_does_not_need_the_lock(world):
    node_key = world.enroll()

    with world.lock():
        assert send(world, node_key, [], log_type="result").status_code == 200
        assert send(world, node_key, [{"x": 1}], log_type="status").status_code == 200


def test_result_log_without_data_list_is_a_bad_request(world, caplog):
    node_key = world.enroll()

    caplog.set_level("INFO", logger="ens_evidence.agent")

    response = world.post("log", {"node_key": node_key, "log_type": "result", "data": "x"})

    assert response.status_code == 400
    assert "outcome=invalid_body" in caplog.text
    assert f"asset={LAPTOP}" in caplog.text
    assert LAPTOP_UUID not in caplog.text


def distinct_batch(minor: str, unix=NOW):
    return [snapshot_event("os_version", [{**OS_VERSION_ROW, "minor": minor}], unix=unix)]


def other_query_event():
    return snapshot_event(
        "system_info", [{"uuid": LAPTOP_UUID, "hostname": "lap-ana", "cpu": "M2"}]
    )


def stored_queries(world):
    return sorted(q for m in manifests(world) for q in _artifact(world, m)["data"]["queries"])


def test_different_queries_in_separate_posts_are_all_stored(world, caplog):
    node_key = world.enroll()
    caplog.set_level("INFO", logger="ens_evidence.agent")

    send(world, node_key, distinct_batch("1"))
    send(world, node_key, [other_query_event()])

    assert stored_queries(world) == ["os_version", "system_info"]
    assert "outcome=throttled" not in caplog.text


def test_a_repeated_query_in_one_interval_is_throttled(world, caplog):
    node_key = world.enroll()
    caplog.set_level("INFO", logger="ens_evidence.agent")

    responses = [send(world, node_key, distinct_batch(str(n))) for n in range(5)]

    assert all(r.json() == {"node_invalid": False} for r in responses)
    assert len(manifests(world)) == 1
    assert caplog.text.count("outcome=throttled") == 4


def test_a_mixed_batch_stores_the_new_query_and_throttles_the_repeated_one(world, caplog):
    node_key = world.enroll()
    send(world, node_key, distinct_batch("1"))
    caplog.set_level("INFO", logger="ens_evidence.agent")

    response = send(world, node_key, distinct_batch("2") + [other_query_event()])

    assert response.status_code == 200
    assert stored_queries(world) == ["os_version", "system_info"]
    assert len(manifests(world)) == 2
    assert caplog.text.count("outcome=throttled") == 1


def test_a_new_batch_is_stored_once_the_interval_has_passed(world):
    node_key = world.enroll()
    send(world, node_key, distinct_batch("1"))

    world.clock.advance(hours=6)
    send(world, node_key, distinct_batch("2", unix=NOW + timedelta(hours=6)))
    assert len(manifests(world)) == 2


def test_a_snapshot_brought_forward_by_the_splay_is_stored(world, caplog):
    node_key = world.enroll()
    send(world, node_key, distinct_batch("1"))
    caplog.set_level("INFO", logger="ens_evidence.agent")

    world.clock.advance(hours=5, minutes=25)
    send(world, node_key, distinct_batch("2", unix=NOW + timedelta(hours=5, minutes=25)))

    assert len(manifests(world)) == 2
    assert "outcome=throttled" not in caplog.text


def test_after_a_late_delivery_the_next_snapshot_on_schedule_is_stored(world, caplog):
    node_key = world.enroll()
    world.clock.advance(hours=2)
    send(world, node_key, distinct_batch("1"))  # generado a NOW, entregado tras un corte
    caplog.set_level("INFO", logger="ens_evidence.agent")

    world.clock.advance(hours=4)
    send(world, node_key, distinct_batch("2", unix=NOW + timedelta(hours=6)))

    assert len(manifests(world)) == 2
    assert "outcome=throttled" not in caplog.text


def test_a_snapshot_older_than_the_stored_one_is_throttled(world, caplog):
    node_key = world.enroll()
    send(world, node_key, distinct_batch("1"))
    caplog.set_level("INFO", logger="ens_evidence.agent")

    send(world, node_key, distinct_batch("0", unix=NOW - timedelta(hours=6)))

    assert len(manifests(world)) == 1
    assert caplog.text.count("outcome=throttled") == 1


def test_invented_generation_times_store_at_most_three_per_interval(world, caplog):
    node_key = world.enroll()
    caplog.set_level("INFO", logger="ens_evidence.agent")

    for n in range(6):
        send(world, node_key, distinct_batch(str(n), unix=NOW - timedelta(days=10 - n)))

    assert len(manifests(world)) == 3
    assert caplog.text.count("outcome=throttled") == 3


def test_a_batch_refused_with_503_does_not_use_the_interval(world):
    node_key = world.enroll()
    with world.lock():
        assert send(world, node_key, distinct_batch("1")).status_code == 503
    assert send(world, node_key, distinct_batch("1")).status_code == 200
    assert len(manifests(world)) == 1


def test_the_interval_is_per_host_and_query(world):
    first = world.enroll()
    second = world.enroll(SERVER, uuid=SERVER_UUID, hostname=SERVER_HOST)
    send(world, first, distinct_batch("1"))
    send(world, second, [snapshot_event("os_version", [OS_VERSION_ROW], host=SERVER_UUID.upper())])
    assert len(manifests(world)) == 2
