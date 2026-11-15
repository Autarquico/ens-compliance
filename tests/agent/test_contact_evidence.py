import hashlib
import json
from datetime import timedelta

import pytest
from starlette.testclient import TestClient

from ens_evidence.agent.protocol import CONTACT_EVIDENCE_INTERVAL, AgentService
from ens_evidence.agent.results import CONTACT_SOURCE
from ens_evidence.agent.routes import routes
from ens_evidence.agent.state import AgentState
from ens_evidence.evidence import list_manifests
from ens_evidence.evidence.verify import verify_all
from ens_evidence.http.app import create_app

from .conftest import SECRETS
from .protocol_support import (
    LAPTOP,
    LAPTOP_UUID,
    NOW,
    OS_VERSION_ROW,
    Credentials,
    request_for,
    snapshot_event,
)

IDENTITY = hashlib.sha256(LAPTOP_UUID.encode()).hexdigest()


def contacts(world):
    return [m for m in list_manifests(world.workspace).manifests if m.source == CONTACT_SOURCE]


def artifact(world, manifest):
    return json.loads(world.workspace.read_bytes(manifest.artifact.key))


def authenticated(node_key, endpoint):
    return request_for(endpoint, Credentials(node_key=node_key, enroll_token=""))


@pytest.mark.parametrize("endpoint", ["config", "distributed/read", "distributed/write", "log"])
def test_every_authenticated_request_leaves_contact_evidence(world, endpoint):
    node_key = world.enroll()
    world.clock.advance(minutes=5)

    response = world.post(endpoint, authenticated(node_key, endpoint))

    assert response.status_code == 200
    (manifest,) = contacts(world)
    stored = artifact(world, manifest)
    assert stored["source"] == CONTACT_SOURCE
    assert stored["asset_id"] == LAPTOP
    assert stored["collection_status"] == "SUCCESS"
    assert stored["collected_at"].startswith(world.clock().isoformat()[:19])
    assert stored["data"] == {
        "agent": {"identity_sha256": IDENTITY, "platform": "darwin", "generation": 1}
    }
    assert LAPTOP_UUID not in json.dumps(stored).lower()
    assert verify_all(world.workspace).ok


def test_contact_time_is_the_server_time_not_the_hosts(world):
    node_key = world.enroll()
    stale = snapshot_event("os_version", [OS_VERSION_ROW], unix=NOW - timedelta(days=3))

    world.post("log", {"node_key": node_key, "log_type": "result", "data": [stale]})

    (manifest,) = contacts(world)
    assert artifact(world, manifest)["collected_at"].startswith(NOW.isoformat()[:19])


def test_at_most_one_contact_evidence_per_interval_and_asset(world):
    node_key = world.enroll()

    for _ in range(5):
        world.clock.advance(minutes=30)
        world.post("config", {"node_key": node_key})

    assert len(contacts(world)) == 1


def test_a_new_contact_is_written_once_the_interval_has_passed(world):
    node_key = world.enroll()
    world.post("config", {"node_key": node_key})
    world.clock.advance(seconds=CONTACT_EVIDENCE_INTERVAL.total_seconds() - 1)
    world.post("config", {"node_key": node_key})
    assert len(contacts(world)) == 1

    world.clock.advance(seconds=1)
    world.post("config", {"node_key": node_key})

    assert len(contacts(world)) == 2


def test_the_interval_survives_a_service_restart(world):
    node_key = world.enroll()
    world.post("config", {"node_key": node_key})
    world.clock.advance(hours=1)

    restarted = AgentService(
        world.workspace,
        SECRETS,
        AgentState(world.state_dir),
        lambda: world.lock(),
        now=world.clock,
    )
    client = TestClient(create_app(routes(restarted)))
    assert client.post("/agent/config", json={"node_key": node_key}).status_code == 200

    assert len(contacts(world)) == 1


def test_a_busy_workspace_does_not_fail_config_and_the_next_request_retries(world):
    node_key = world.enroll()

    with world.lock():
        response = world.post("config", {"node_key": node_key})
    assert response.status_code == 200
    assert contacts(world) == []

    world.post("config", {"node_key": node_key})
    assert len(contacts(world)) == 1


def test_invalid_credentials_leave_no_contact_evidence(world):
    world.enroll()

    response = world.post("config", {"node_key": "v1.invalid"})

    assert response.json() == {"node_invalid": True}
    assert contacts(world) == []


def test_result_evidence_carries_full_identity_and_platform(world):
    node_key = world.enroll()

    world.post(
        "log",
        {
            "node_key": node_key,
            "log_type": "result",
            "data": [snapshot_event("os_version", [OS_VERSION_ROW])],
        },
    )

    (result,) = [
        m for m in list_manifests(world.workspace).manifests if m.source == "osquery-agent"
    ]
    agent = artifact(world, result)["data"]["agent"]
    assert agent["identity_sha256"] == IDENTITY
    assert agent["platform"] == "darwin"
    assert agent["host_identifier"] == IDENTITY[:8]
    assert LAPTOP_UUID not in json.dumps(artifact(world, result)).lower()
