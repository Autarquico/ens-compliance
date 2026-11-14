from datetime import timedelta

from ens_evidence.monitoring import vps
from ens_evidence.monitoring.model import Status
from ens_evidence.monitoring.thresholds import Thresholds

from .support import NOW, sample

LIMITS = Thresholds()


def test_disk_uses_the_longest_prefix_mount_and_flags_the_one_over_threshold():
    mounts = [
        {"path": "/", "blocks": "100", "blocks_available": "50"},
        {"path": "/var/lib/docker", "blocks": "100", "blocks_available": "10"},
    ]
    samples = {
        "mon_disk": sample("mon_disk", mounts),
        "mon_docker_root": sample("mon_docker_root", [{"root_dir": "/var/lib/docker"}]),
    }

    readings = {r.id: r for r in vps.disks(samples, LIMITS, NOW)}

    assert readings["vps:disk:/"].status is Status.OK
    assert readings["vps:disk:/var/lib/docker"].status is Status.FAIL
    assert readings["vps:disk:/var/lib/docker"].value == 90


def test_disk_prefix_is_by_path_component_not_by_string():
    mounts = [
        {"path": "/", "blocks": "100", "blocks_available": "50"},
        {"path": "/var/lib/dock", "blocks": "100", "blocks_available": "1"},
    ]
    samples = {"mon_disk": sample("mon_disk", mounts)}

    readings = vps.disks(samples, LIMITS, NOW)

    assert [r.id for r in readings] == ["vps:disk:/"]


def test_stale_or_missing_samples_are_unknown_never_green():
    old = NOW - vps.VPS_TTL - timedelta(seconds=1)
    load = {"load1": "0", "load5": "0", "load15": "0", "cores": "2"}

    stale = vps.load({"mon_load": sample("mon_load", [load], at=old)}, LIMITS, NOW)
    missing = vps.memory({}, LIMITS, NOW)
    future = vps.load(
        {"mon_load": sample("mon_load", [load], at=NOW + timedelta(hours=1))}, LIMITS, NOW
    )

    assert stale.status is missing.status is future.status is Status.UNKNOWN


def test_load_is_per_core_and_invalid_values_are_unknown():
    row = {"load1": "1", "load5": "5", "load15": "1", "cores": "2"}

    reading = vps.load({"mon_load": sample("mon_load", [row])}, LIMITS, NOW)
    broken = vps.load({"mon_load": sample("mon_load", [{**row, "cores": "0"}])}, LIMITS, NOW)

    assert reading.status is Status.FAIL and reading.value == 2.5
    assert broken.status is Status.UNKNOWN


def test_memory_threshold():
    row = {"memory_total": "1000", "memory_available": "50"}

    reading = vps.memory({"mon_memory": sample("mon_memory", [row])}, LIMITS, NOW)

    assert reading.status is Status.FAIL


def test_tls_days_and_locale_free_date_parsing():
    soon = "Dec  1 00:00:00 2026 GMT"
    later = "Jan  1 00:00:00 2030 GMT"
    rows = [
        {"hostname": "a.example.org", "valid_to": soon, "has_expired": "0"},
        {"hostname": "b.example.org", "valid_to": later, "has_expired": "0"},
        {"hostname": "c.example.org", "valid_to": "basura", "has_expired": "0"},
    ]

    readings = vps.tls(
        {"mon_tls": sample("mon_tls", rows)},
        ["a.example.org", "b.example.org", "c.example.org", "d.example.org"],
        LIMITS,
        NOW,
    )

    assert [r.status for r in readings] == [
        Status.FAIL,
        Status.OK,
        Status.UNKNOWN,
        Status.UNKNOWN,
    ]


def test_updates_are_only_hints_and_never_green():
    quiet = {
        "reboot_required": "0",
        "apt_update_stamp": str(int(NOW.timestamp())),
        "apt_timer_active": "active",
        "unattended_active": "inactive",
    }

    clean = vps.updates({"mon_updates": sample("mon_updates", [quiet])}, NOW)
    reboot = vps.updates(
        {"mon_updates": sample("mon_updates", [{**quiet, "reboot_required": "1"}])}, NOW
    )

    assert clean.status is Status.UNKNOWN
    assert reboot.status is Status.WARN and "reinicio" in reboot.detail.lower()


def test_container_without_healthcheck_is_amber_and_unhealthy_is_red():
    assert vps.container_status({"state": "running", "status": "Up 2 hours"})[0] is Status.WARN
    assert vps.container_status({"state": "running", "status": "Up (healthy)"})[0] is Status.OK
    assert vps.container_status({"state": "running", "status": "Up (unhealthy)"})[0] is Status.FAIL
    assert vps.container_status({"state": "exited", "status": "Exited (1)"})[0] is Status.FAIL


def test_containers_only_count_the_compose_project():
    rows = [
        {
            "compose_project": "ens-box",
            "compose_service": "app",
            "state": "running",
            "status": "Up (healthy)",
        },
        {"compose_project": "otro", "compose_service": "x", "state": "exited", "status": "Exited"},
    ]

    reading = vps.containers({"mon_containers": sample("mon_containers", rows)}, "ens-box", NOW)

    assert reading.status is Status.OK
