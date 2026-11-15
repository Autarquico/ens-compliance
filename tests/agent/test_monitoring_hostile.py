"""Entrada hostil sobre la salud del VPS: otro agente, lotes y filas enormes, hostnames TLS."""

import pytest
import yaml

from ens_evidence.agent.monitoring import MonitoringStore
from ens_evidence.agent.monitoring.config import MAX_TLS_HOSTNAMES
from ens_evidence.agent.monitoring.queries import tls_query
from ens_evidence.agent.monitoring.store import MAX_EVENTS_PER_BATCH

from .protocol_support import (
    LAPTOP,
    LAPTOP_UUID,
    SERVER,
    SERVER_HOST,
    SERVER_UUID,
    snapshot_event,
)

VPS = {"uuid": SERVER_UUID, "hostname": SERVER_HOST, "platform": "linux"}
LOAD = {"load1": "0.5", "load5": "0.4", "load15": "0.3", "cores": "2"}
CONTAINER = {"name": "c", "image": "i", "state": "running"}


@pytest.fixture
def monitoring(world, monkeypatch):
    monkeypatch.setenv("ENS_STATE_DIR", str(world.state_dir))

    def configure(**overrides):
        config = {"vps_asset_id": SERVER, "tls_hostnames": [], **overrides}
        world.workspace.write_text("connectors/monitoring.yaml", yaml.safe_dump(config))

    configure()
    return configure


def _log(world, node_key, *events):
    return world.post("log", {"node_key": node_key, "log_type": "result", "data": list(events)})


def _vps_event(name, rows):
    return snapshot_event(name, rows, host=SERVER_UUID.upper())


def _latest(world):
    return MonitoringStore(world.state_dir).latest()


def test_another_agent_posting_every_mon_query_is_rejected(world, monitoring):
    world.enroll(SERVER, **VPS)
    laptop = world.enroll(LAPTOP)
    events = [
        snapshot_event(name, [{}], host=LAPTOP_UUID.upper())
        for name in ("mon_load", "mon_memory", "mon_disk", "mon_containers", "mon_tls")
    ]
    events.append(snapshot_event("mon_load", [LOAD], host=SERVER_UUID.upper()))

    _log(world, laptop, *events)

    assert _latest(world) == {}


def test_the_vps_cannot_impersonate_another_host_identifier(world, monitoring):
    node_key = world.enroll(SERVER, **VPS)

    _log(world, node_key, snapshot_event("mon_load", [LOAD], host=LAPTOP_UUID.upper()))

    assert _latest(world) == {}


def test_events_beyond_the_batch_limit_are_ignored(world, monitoring):
    node_key = world.enroll(SERVER, **VPS)
    junk = [_vps_event("mon_unknown", [LOAD])] * MAX_EVENTS_PER_BATCH

    _log(world, node_key, *junk, _vps_event("mon_load", [LOAD]))

    assert _latest(world) == {}


def test_a_row_count_over_the_query_limit_voids_the_event(world, monitoring):
    node_key = world.enroll(SERVER, **VPS)

    _log(world, node_key, _vps_event("mon_containers", [CONTAINER] * 201))

    assert "mon_containers" not in _latest(world)


def test_the_maximum_row_count_is_still_accepted(world, monitoring):
    node_key = world.enroll(SERVER, **VPS)

    _log(world, node_key, _vps_event("mon_containers", [CONTAINER] * 200))

    assert len(_latest(world)["mon_containers"].rows) == 200


def test_an_oversized_value_voids_the_whole_event(world, monitoring):
    node_key = world.enroll(SERVER, **VPS)

    _log(
        world, node_key, _vps_event("mon_containers", [CONTAINER, {**CONTAINER, "name": "x" * 513}])
    )

    assert "mon_containers" not in _latest(world)


BAD_HOSTNAMES = [
    "Example.org",
    "app.example.org:443",
    "*.example.org",
    "example.org'; DROP TABLE x; --",
    "localhost",
    "app.example.org/path",
    "",
    "-a.example.org",
]


@pytest.mark.parametrize("hostname", BAD_HOSTNAMES)
def test_a_bad_tls_hostname_in_the_config_disables_monitoring(world, monitoring, hostname):
    monitoring(tls_hostnames=[hostname])
    node_key = world.enroll(SERVER, **VPS)

    schedule = world.post("config", {"node_key": node_key}).json()["schedule"]
    _log(world, node_key, _vps_event("mon_load", [LOAD]))

    assert not [name for name in schedule if name.startswith("mon_")]
    assert _latest(world) == {}


def test_too_many_tls_hostnames_disable_monitoring(world, monitoring):
    monitoring(tls_hostnames=[f"h{i}.example.org" for i in range(MAX_TLS_HOSTNAMES + 1)])
    node_key = world.enroll(SERVER, **VPS)

    schedule = world.post("config", {"node_key": node_key}).json()["schedule"]

    assert not [name for name in schedule if name.startswith("mon_")]


@pytest.mark.parametrize("hostname", BAD_HOSTNAMES[:5])
def test_the_sql_builder_refuses_a_bad_hostname_even_without_the_config(hostname):
    with pytest.raises(ValueError):
        tls_query([hostname])
