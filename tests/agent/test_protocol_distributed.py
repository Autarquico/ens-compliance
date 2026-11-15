import json
from datetime import timedelta

import pytest

from ens_evidence.agent.osquery_config import query_sql
from ens_evidence.evidence import list_manifests
from ens_evidence.evidence.verify import verify_all

from .protocol_support import LAPTOP, OS_VERSION_ROW, SERVER, SERVER_HOST, SERVER_UUID


def enqueue(world, assets=(LAPTOP,), query_id="os_version", hours=1):
    return world.state.enqueue(
        query_id, assets, world.clock() + timedelta(hours=hours), "alex", now=world.clock()
    )


def read(world, node_key):
    return world.post("distributed/read", {"node_key": node_key})


def write(world, node_key, run_id, rows=(OS_VERSION_ROW,), status=0):
    body = {
        "node_key": node_key,
        "queries": {run_id: list(rows)},
        "statuses": {run_id: status},
        "messages": {},
    }
    return world.post("distributed/write", body)


def manifests(world):
    return list_manifests(world.workspace).manifests


def test_read_without_runs_returns_no_queries(world):
    node_key = world.enroll()

    assert read(world, node_key).json() == {"queries": {}, "node_invalid": False}


def test_read_returns_the_pack_sql_of_pending_runs_once(world):
    node_key = world.enroll()
    run = enqueue(world)

    first = read(world, node_key).json()
    second = read(world, node_key).json()

    assert first["queries"] == {run.run_id: query_sql("os_version")}
    assert second["queries"] == {}


def test_expired_runs_are_not_delivered(world):
    node_key = world.enroll()
    enqueue(world, hours=1)
    world.clock.advance(hours=1)

    assert read(world, node_key).json()["queries"] == {}


def test_run_is_delivered_only_to_its_target(world):
    laptop_key = world.enroll()
    server_key = world.enroll(SERVER, uuid=SERVER_UUID, hostname=SERVER_HOST)
    run = enqueue(world, assets=[SERVER])

    assert read(world, laptop_key).json()["queries"] == {}
    assert list(read(world, server_key).json()["queries"]) == [run.run_id]


def test_answer_is_stored_as_evidence_and_marked(world):
    node_key = world.enroll()
    run = enqueue(world)
    read(world, node_key)

    response = write(world, node_key, run.run_id)

    assert response.status_code == 200
    (manifest,) = manifests(world)
    assert (manifest.source, manifest.asset_id) == ("osquery-agent", LAPTOP)
    data = json.loads(world.workspace.read_bytes(manifest.artifact.key))["data"]
    assert data["agent"]["query_run_id"] == run.run_id
    assert data["queries"]["os_version"]["rows"][0]["version"] == "15.1"
    assert "extra" not in data["queries"]["os_version"]["rows"][0]
    answer = world.state.show_run(run.run_id).hosts[0].answer
    assert (answer.evidence_id, answer.status) == (manifest.id, 0)
    assert verify_all(world.workspace).ok


def test_answer_is_accepted_once(world):
    node_key = world.enroll()
    run = enqueue(world)
    read(world, node_key)

    write(world, node_key, run.run_id)
    write(world, node_key, run.run_id, rows=[{**OS_VERSION_ROW, "version": "99"}])

    assert len(manifests(world)) == 1


def test_answers_for_runs_not_delivered_to_this_host_are_ignored(world):
    laptop_key = world.enroll()
    server_key = world.enroll(SERVER, uuid=SERVER_UUID, hostname=SERVER_HOST)
    undelivered = enqueue(world)
    for_server = enqueue(world, assets=[SERVER])
    read(world, server_key)
    before = world.workspace.list("")

    for run_id in (undelivered.run_id, for_server.run_id, "live-0000000000000000", "../x", 7):
        assert write(world, laptop_key, run_id).status_code == 200

    assert world.workspace.list("") == before
    assert world.state.show_run(undelivered.run_id).hosts[0].answer is None


def test_nonzero_status_is_recorded_as_error_without_rows(world):
    node_key = world.enroll()
    run = enqueue(world)
    read(world, node_key)

    write(world, node_key, run.run_id, rows=[], status=1)

    (manifest,) = manifests(world)
    artifact = json.loads(world.workspace.read_bytes(manifest.artifact.key))
    assert artifact["collection_status"] == "ERROR"
    assert artifact["data"]["queries"]["os_version"]["status"] == "ERROR"
    assert artifact["data"]["queries"]["os_version"]["rows"] == []
    assert world.state.show_run(run.run_id).hosts[0].answer.status == 1


def test_rows_over_the_limits_with_status_zero_are_an_error(world):
    node_key = world.enroll()
    run = enqueue(world)
    read(world, node_key)

    write(world, node_key, run.run_id, rows=[{"name": "x" * 5000}])

    (manifest,) = manifests(world)
    artifact = json.loads(world.workspace.read_bytes(manifest.artifact.key))
    assert artifact["collection_status"] == "ERROR"


def test_busy_lock_answers_503_and_the_retry_imports_once(world):
    node_key = world.enroll()
    run = enqueue(world)
    read(world, node_key)

    with world.lock():
        assert write(world, node_key, run.run_id).status_code == 503
        assert manifests(world) == []
        assert world.state.show_run(run.run_id).hosts[0].answer is None

    assert write(world, node_key, run.run_id).status_code == 200
    assert write(world, node_key, run.run_id).status_code == 200
    assert len(manifests(world)) == 1


@pytest.mark.parametrize("bad", [None, [], "x", 3])
def test_write_without_queries_object_is_a_bad_request(world, bad):
    node_key = world.enroll()

    response = world.post("distributed/write", {"node_key": node_key, "queries": bad})

    assert response.status_code == 400
