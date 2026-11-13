import json

from ens_evidence.evidence import list_manifests

from .protocol_support import LAPTOP, snapshot_event

BITLOCKER_ROW = {
    "device_id": "\\\\?\\Volume{1}",
    "drive_letter": "C:",
    "protection_status": "1",
    "conversion_status": "1",
    "encryption_method": "XTS_AES_128",
    "lock_status": "0",
}


def stored_queries(world):
    (manifest,) = list_manifests(world.workspace).manifests
    return json.loads(world.workspace.read_bytes(manifest.artifact.key))["data"]["queries"]


def test_enroll_with_a_windows_build_stores_the_platform(world):
    world.enroll(platform="windows")

    assert world.state.host(LAPTOP).platform == "windows"


def test_config_for_a_windows_host_includes_the_windows_queries(world):
    node_key = world.enroll(platform="windows")

    schedule = world.post("config", {"node_key": node_key}).json()["schedule"]

    for query_id in ("bitlocker_info", "windows_security_center", "programs", "system_info"):
        assert "windows" in schedule[query_id]["platform"].split(",")
    assert "windows" not in schedule["alf"]["platform"].split(",")


def test_windows_snapshots_keep_only_the_stored_columns(world):
    node_key = world.enroll(platform="windows")
    events = [
        snapshot_event("bitlocker_info", [BITLOCKER_ROW]),
        snapshot_event("alf", [{"global_state": "1"}]),
    ]

    world.post("log", {"node_key": node_key, "log_type": "result", "data": events})

    queries = stored_queries(world)
    assert queries["bitlocker_info"]["rows"] == [
        {k: v for k, v in BITLOCKER_ROW.items() if k != "lock_status"}
    ]
    assert "alf" not in queries
