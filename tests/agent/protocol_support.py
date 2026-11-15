"""Cuerpos realistas de osquery (datos ficticios) y utilidades de los tests del protocolo."""

import hashlib
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

LAPTOP = "lap-ana"
LAPTOP_UUID = "6f1c2d3e-4a5b-4c6d-8e9f-0a1b2c3d4e5f"
LAPTOP_HOST = "lap-ana.taller-norte.example"
SERVER = "srv-app"
SERVER_UUID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
SERVER_HOST = "srv-app.taller-norte.example"
STRANGER_UUID = "99999999-8888-4777-8666-555555555555"
NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)

ENDPOINT_CONFIG: dict[str, Any] = {
    "max_evidence_age_days": 30,
    "supported_os": [{"platform": "darwin", "name": "macOS", "release": "15"}],
    "hosts": {
        LAPTOP: {"hardware_uuid": LAPTOP_UUID, "hostname": LAPTOP_HOST},
        SERVER: {"hardware_uuid": SERVER_UUID, "hostname": SERVER_HOST},
    },
}

ENDPOINTS = ("enroll", "config", "log", "distributed/read", "distributed/write")

OS_VERSION_ROW = {
    "name": "macOS",
    "platform": "darwin",
    "version": "15.1",
    "major": "15",
    "minor": "1",
    "patch": "0",
    "build": "24B83",
    "codename": "",
    "arch": "arm64",
    "extra": "no se guarda",
    "pid_with_namespace": "0",
}


@dataclass(frozen=True)
class Credentials:
    node_key: str
    enroll_token: str
    uuid: str = LAPTOP_UUID
    hostname: str = LAPTOP_HOST
    host_identifier: str | None = None
    platform: str = "darwin"


def enroll_body(c: Credentials) -> dict[str, Any]:
    return {
        "enroll_secret": c.enroll_token,
        "host_identifier": c.host_identifier or c.uuid.upper(),
        "platform_type": "21",
        "host_details": {
            "os_version": {"name": "macOS", "platform": "darwin", "version": "15.1"},
            "osquery_info": {
                "version": "5.14.1",
                "build_platform": c.platform,
                "build_distro": "10.14",
                "pid": "812",
                "extensions": "inactive",
            },
            "system_info": {
                "uuid": c.uuid,
                "hostname": c.hostname,
                "computer_name": "Ana MacBook",
                "cpu_brand": "Apple M2",
                "physical_memory": "17179869184",
                "hardware_vendor": "Apple Inc.",
            },
            "platform_info": {"vendor": "Apple Inc.", "version": "10151.41.12"},
        },
    }


def snapshot_event(
    name: str,
    rows: list[dict[str, str]],
    *,
    unix: datetime = NOW,
    host: str = LAPTOP_UUID.upper(),
) -> dict[str, Any]:
    return {
        "snapshot": rows,
        "action": "snapshot",
        "name": name,
        "hostIdentifier": host,
        "calendarTime": unix.strftime("%a %b %d %H:%M:%S %Y UTC"),
        "unixTime": int(unix.timestamp()),
        "epoch": 0,
        "counter": 0,
        "numerics": False,
        "decorations": {"host_uuid": host},
    }


def request_for(endpoint: str, c: Credentials) -> dict[str, Any]:
    """Cuerpo válido en forma para cada endpoint; con credenciales malas nada debe escribirse."""
    node = {"node_key": c.node_key}
    match endpoint:
        case "enroll":
            return enroll_body(c)
        case "config" | "distributed/read":
            return node
        case "log":
            return {
                **node,
                "log_type": "result",
                "data": [snapshot_event("os_version", [OS_VERSION_ROW])],
            }
        case "distributed/write":
            return {
                **node,
                "queries": {"live-0123456789abcdef": [OS_VERSION_ROW]},
                "statuses": {"live-0123456789abcdef": 0},
                "messages": {},
            }
    raise AssertionError(endpoint)


def tree(*roots: Path) -> dict[str, str]:
    """Ficheros bajo las raíces con el hash de su contenido: detecta cualquier escritura."""
    listing: dict[str, str] = {}
    for root in roots:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                listing[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return listing


def with_(c: Credentials, **changes: Any) -> Credentials:
    return replace(c, **changes)
