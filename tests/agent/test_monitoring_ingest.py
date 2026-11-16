import json
from datetime import timedelta

import pytest
import yaml

from ens_evidence.agent.monitoring import MonitoringStore
from ens_evidence.agent.monitoring.queries import tls_query

from .protocol_support import (
    LAPTOP,
    LAPTOP_UUID,
    NOW,
    SERVER,
    SERVER_HOST,
    SERVER_UUID,
    snapshot_event,
    workspace_keys,
)

VPS = {"uuid": SERVER_UUID, "hostname": SERVER_HOST, "platform": "linux"}
LOAD = {"load1": "0.5", "load5": "0.4", "load15": "0.3", "cores": "2"}


@pytest.fixture
def vps(world, monkeypatch):
    monkeypatch.setenv("ENS_STATE_DIR", str(world.state_dir))
    config = {"vps_asset_id": SERVER, "tls_hostnames": []}
    world.workspace.write_text("connectors/monitoring.yaml", yaml.safe_dump(config))
    return world.enroll(SERVER, **VPS)


def _log(world, node_key, *events):
    return world.post("log", {"node_key": node_key, "log_type": "result", "data": list(events)})


def _mon(name, rows, **kwargs):
    return snapshot_event(name, rows, host=SERVER_UUID.upper(), **kwargs)


def _store(world):
    return MonitoringStore(world.state_dir)


def test_vps_samples_land_in_the_monitoring_store_and_never_in_evidence(world, vps):
    before = workspace_keys(world)

    response = _log(world, vps, _mon("mon_load", [{**LOAD, "extra": "no"}]))

    assert response.json() == {"node_invalid": False}
    sample = _store(world).latest()["mon_load"]
    assert sample.rows == (LOAD,)
    assert sample.observed_at == NOW
    assert workspace_keys(world) == before
    assert (world.state_dir / "monitoring" / "vps" / "mon_load.json").is_file()


def test_another_agent_cannot_write_vps_health(world, vps):
    laptop = world.enroll(LAPTOP)
    event = snapshot_event("mon_load", [LOAD], host=LAPTOP_UUID.upper())

    _log(world, laptop, event)

    assert _store(world).latest() == {}


def test_without_monitoring_config_everything_is_dropped(world, monkeypatch):
    monkeypatch.setenv("ENS_STATE_DIR", str(world.state_dir))
    node_key = world.enroll(SERVER, **VPS)

    _log(world, node_key, _mon("mon_load", [LOAD]))

    assert _store(world).latest() == {}


def test_unknown_ids_wrong_host_and_bad_times_are_discarded(world, vps):
    future = NOW + timedelta(hours=1)
    ancient = NOW.replace(year=1999)
    events = [
        _mon("mon_unknown", [LOAD]),
        snapshot_event("mon_load", [LOAD], host=LAPTOP_UUID.upper()),
        _mon("mon_load", [LOAD], unix=future),
        _mon("mon_load", [LOAD], unix=ancient),
        _mon("mon_memory", [{"memory_total": "x" * 513}]),
        _mon("mon_disk", [{"path": "/"}] * 65),
        _mon("mon_updates", "no-es-lista"),
    ]

    assert _log(world, vps, *events).status_code == 200

    assert _store(world).latest() == {}


def test_non_linux_node_is_discarded(world, monkeypatch):
    monkeypatch.setenv("ENS_STATE_DIR", str(world.state_dir))
    config = {"vps_asset_id": SERVER, "tls_hostnames": []}
    world.workspace.write_text("connectors/monitoring.yaml", yaml.safe_dump(config))
    node_key = world.enroll(SERVER, **{**VPS, "platform": "darwin"})

    _log(world, node_key, _mon("mon_load", [LOAD]))

    assert _store(world).latest() == {}


def test_redelivery_and_stale_batches_do_not_overwrite_newer_samples(world, vps):
    newer = {**LOAD, "load1": "9.0"}
    _log(world, vps, _mon("mon_load", [newer]))
    _log(world, vps, _mon("mon_load", [LOAD], unix=NOW - timedelta(minutes=15)))
    _log(world, vps, _mon("mon_load", [LOAD]))

    assert _store(world).latest()["mon_load"].rows == (newer,)


def test_mixed_batch_keeps_pack_snapshots_flowing_to_evidence(world, vps):
    os_version = snapshot_event("os_version", [], host=SERVER_UUID.upper())

    _log(world, vps, _mon("mon_load", [LOAD]), os_version)

    assert "mon_load" in _store(world).latest()


def test_store_writes_are_atomic_and_private(world, vps):
    _log(world, vps, _mon("mon_load", [LOAD]))

    folder = world.state_dir / "monitoring" / "vps"
    assert [p.name for p in folder.iterdir()] == ["mon_load.json"]
    assert folder.stat().st_mode & 0o077 == 0
    assert json.loads((folder / "mon_load.json").read_text())["rows"] == [LOAD]


def test_corrupt_sample_reads_as_missing(world):
    folder = world.state_dir / "monitoring" / "vps"
    folder.mkdir(parents=True)
    (folder / "mon_load.json").write_text("{no json")

    assert _store(world).latest() == {}


@pytest.mark.parametrize(
    "hostname",
    [
        "",
        "a'b.example.org",
        "x.org') OR 1=1--",
        "UPPER.example.org",
        "app.example.org:443",
        "*.example.org",
        "localhost",
        "a b.example.org",
        "app.example.org\n",
        "-a.example.org",
    ],
)
def test_tls_hostnames_must_pass_the_strict_regex(hostname):
    with pytest.raises(ValueError):
        tls_query([hostname])


def test_queries_never_ask_for_docker_env_variables():
    from ens_evidence.agent.monitoring import monitoring_schedule

    sql = " ".join(e["query"] for e in monitoring_schedule(["app.example.org"]).values())
    assert "env_variables" not in sql
