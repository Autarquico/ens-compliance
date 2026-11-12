from datetime import timedelta

from ens_evidence.agent.osquery_config import SCHEDULE_INTERVAL_SECONDS
from ens_evidence.endpoint.pack import PACK_DIR, load_pack

from .protocol_support import LAPTOP


def test_config_serves_the_pack_sql_byte_for_byte(world):
    node_key = world.enroll()

    response = world.post("config", {"node_key": node_key})

    config = response.json()
    queries = load_pack().queries
    assert set(config["schedule"]) == {query.id for query in queries}
    for query in queries:
        entry = config["schedule"][query.id]
        assert entry["query"].encode() == (PACK_DIR / f"{query.id}.sql").read_bytes()
        assert entry["platform"] == ",".join(query.platforms)
        assert entry["snapshot"] is True
        assert entry["interval"] == SCHEDULE_INTERVAL_SECONDS == 21600


def test_config_sets_status_log_floor(world):
    config = world.post("config", {"node_key": world.enroll()}).json()

    assert config["options"] == {"logger_min_status": 3}


def test_config_does_not_write_to_the_workspace(world):
    node_key = world.enroll()
    before = world.workspace.list("")

    world.post("config", {"node_key": node_key})

    assert world.workspace.list("") == before


def test_last_contact_is_rewritten_at_most_every_minute(world):
    node_key = world.enroll()
    first = world.state.host(LAPTOP).last_contact

    world.clock.advance(seconds=30)
    world.post("config", {"node_key": node_key})
    assert world.state.host(LAPTOP).last_contact == first

    world.clock.advance(seconds=31)
    world.post("config", {"node_key": node_key})
    assert world.state.host(LAPTOP).last_contact == first + timedelta(seconds=61)
    assert world.state.host(LAPTOP).platform == "darwin"
    assert [h.asset_id for h in world.state.list_hosts()] == [LAPTOP]
