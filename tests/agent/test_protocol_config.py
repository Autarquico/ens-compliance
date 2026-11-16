from datetime import timedelta

import yaml

from ens_evidence.agent.monitoring import INTERVAL_SECONDS
from ens_evidence.agent.osquery_config import SCHEDULE_INTERVAL_SECONDS
from ens_evidence.endpoint.pack import PACK_DIR, load_pack

from .protocol_support import LAPTOP, SERVER, SERVER_HOST, SERVER_UUID, workspace_keys


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
    before = workspace_keys(world)

    world.post("config", {"node_key": node_key})

    assert workspace_keys(world) == before


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


VPS_FIELDS = {"uuid": SERVER_UUID, "hostname": SERVER_HOST, "platform": "linux"}


def _configure_monitoring(world, **overrides):
    config = {"vps_asset_id": SERVER, "tls_hostnames": ["app.example.org"], **overrides}
    world.workspace.write_text("connectors/monitoring.yaml", yaml.safe_dump(config))


def test_vps_node_also_gets_the_monitoring_queries(world):
    _configure_monitoring(world)
    node_key = world.enroll(SERVER, **VPS_FIELDS)

    schedule = world.post("config", {"node_key": node_key}).json()["schedule"]

    assert set(schedule) == {q.id for q in load_pack().queries} | {
        "mon_load",
        "mon_memory",
        "mon_disk",
        "mon_docker_root",
        "mon_containers",
        "mon_updates",
        "mon_tls",
    }
    for query_id in schedule.keys() - {q.id for q in load_pack().queries}:
        assert schedule[query_id]["interval"] == INTERVAL_SECONDS == 900
        assert schedule[query_id]["platform"] == "linux"
    assert "'app.example.org'" in schedule["mon_tls"]["query"]
    assert "env_variables" not in "".join(e["query"] for e in schedule.values())


def test_other_agents_never_get_the_monitoring_queries(world):
    _configure_monitoring(world)
    node_key = world.enroll(LAPTOP)

    schedule = world.post("config", {"node_key": node_key}).json()["schedule"]

    assert set(schedule) == {q.id for q in load_pack().queries}


def test_invalid_monitoring_config_keeps_agents_working_without_it(world):
    _configure_monitoring(world, tls_hostnames=["x'; DROP TABLE y;--"])
    node_key = world.enroll(SERVER, **VPS_FIELDS)

    schedule = world.post("config", {"node_key": node_key}).json()["schedule"]

    assert set(schedule) == {q.id for q in load_pack().queries}
