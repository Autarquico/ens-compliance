from datetime import timedelta

import pytest

from ens_evidence.agent.state import AgentState
from ens_evidence.evidence import list_manifests
from ens_evidence.evidence.verify import verify_all

from .protocol_support import LAPTOP, OS_VERSION_ROW, SERVER


def test_live_query_round_trip_is_available_to_the_cli(world):
    node_key = world.enroll()
    run = world.state.enqueue(
        "os_version", [LAPTOP], world.clock() + timedelta(hours=1), "alex", now=world.clock()
    )

    queries = world.post("distributed/read", {"node_key": node_key}).json()["queries"]
    assert list(queries) == [run.run_id]
    world.post(
        "distributed/write",
        {
            "node_key": node_key,
            "queries": {run.run_id: [OS_VERSION_ROW]},
            "statuses": {run.run_id: 0},
        },
    )

    status = world.state.show_run(run.run_id)
    (host,) = status.hosts
    assert host.delivered_at == world.clock()
    assert host.answer.evidence_id in {m.id for m in list_manifests(world.workspace).manifests}
    assert [r.run_id for r in world.state.list_runs()] == [run.run_id]
    assert verify_all(world.workspace).ok


def test_run_status_shows_pending_hosts(world):
    run = world.state.enqueue(
        "os_version",
        [LAPTOP, SERVER],
        world.clock() + timedelta(hours=1),
        "alex",
        now=world.clock(),
    )

    status = world.state.show_run(run.run_id)

    assert [(h.asset_id, h.delivered_at, h.answer) for h in status.hosts] == [
        (LAPTOP, None, None),
        (SERVER, None, None),
    ]


@pytest.mark.parametrize(
    ("query_id", "assets", "hours"),
    [
        ("select_all", [LAPTOP], 1),
        ("os_version", [], 1),
        ("os_version", ["../etc"], 1),
        ("os_version", [LAPTOP], -1),
    ],
    ids=["not_in_pack", "no_assets", "unsafe_asset", "already_expired"],
)
def test_enqueue_rejects_invalid_runs(world, query_id, assets, hours):
    with pytest.raises(ValueError):
        world.state.enqueue(
            query_id, assets, world.clock() + timedelta(hours=hours), "alex", now=world.clock()
        )
    assert world.state.list_runs() == []


def test_unknown_or_malformed_run_id_shows_nothing(world):
    assert world.state.show_run("live-0000000000000000") is None
    assert world.state.show_run("../..") is None


def test_state_directories_are_private_and_survive_a_new_instance(world):
    run = world.state.enqueue(
        "os_version", [LAPTOP], world.clock() + timedelta(hours=1), "alex", now=world.clock()
    )

    reopened = AgentState(world.state_dir)

    assert [r.run_id for r in reopened.list_runs()] == [run.run_id]
    queue = world.state_dir / "agent" / "queue" / run.run_id
    assert queue.stat().st_mode & 0o077 == 0
