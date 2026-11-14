"""Consultas `mon_*` v1 y su lista blanca de columnas.

Tablas y columnas verificadas contra las specs de osquery 5.24.1 (load_average, system_info,
memory_info, mounts, docker_info, docker_containers, docker_container_labels, curl_certificate,
file, systemd_units). Nunca se pide `env_variables` de docker_containers.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from ens_evidence.agent.monitoring.config import HOSTNAME

INTERVAL_SECONDS = 15 * 60
PLATFORM = "linux"
MONITORING_PREFIX = "mon_"
_REAL_FILESYSTEMS = "'ext2','ext3','ext4','xfs','btrfs','zfs','f2fs','vfat'"


@dataclass(frozen=True)
class MonitoringQuery:
    id: str
    sql: str
    columns: tuple[str, ...]
    max_rows: int


def _load(period: str) -> str:
    return f"(SELECT average FROM load_average WHERE period = '{period}')"


def _unit(unit: str, column: str) -> str:
    return f"(SELECT {column} FROM systemd_units WHERE id = '{unit}')"


_STATIC: tuple[MonitoringQuery, ...] = (
    MonitoringQuery(
        "mon_load",
        f"SELECT {_load('1m')} AS load1, {_load('5m')} AS load5, {_load('15m')} AS load15, "
        "(SELECT cpu_logical_cores FROM system_info) AS cores;",
        ("load1", "load5", "load15", "cores"),
        1,
    ),
    MonitoringQuery(
        "mon_memory",
        "SELECT memory_total, memory_available, swap_total, swap_free FROM memory_info;",
        ("memory_total", "memory_available", "swap_total", "swap_free"),
        1,
    ),
    MonitoringQuery(
        "mon_disk",
        "SELECT path, type, blocks_size, blocks, blocks_available FROM mounts "
        f"WHERE type IN ({_REAL_FILESYSTEMS}) AND blocks > 0;",
        ("path", "type", "blocks_size", "blocks", "blocks_available"),
        64,
    ),
    MonitoringQuery("mon_docker_root", "SELECT root_dir FROM docker_info;", ("root_dir",), 1),
    MonitoringQuery(
        "mon_containers",
        "SELECT c.name AS name, c.image AS image, c.image_id AS image_id, c.state AS state, "
        "c.status AS status, "
        "(SELECT value FROM docker_container_labels l WHERE l.id = c.id "
        "AND l.key = 'com.docker.compose.project') AS compose_project, "
        "(SELECT value FROM docker_container_labels l WHERE l.id = c.id "
        "AND l.key = 'com.docker.compose.service') AS compose_service "
        "FROM docker_containers c;",
        ("name", "image", "image_id", "state", "status", "compose_project", "compose_service"),
        200,
    ),
    MonitoringQuery(
        "mon_updates",
        "SELECT (SELECT COUNT(*) FROM file WHERE path = '/run/reboot-required') "
        "AS reboot_required, "
        "(SELECT mtime FROM file WHERE path = '/var/lib/apt/periodic/update-success-stamp') "
        "AS apt_update_stamp, "
        f"{_unit('apt-daily-upgrade.timer', 'active_state')} AS apt_timer_active, "
        f"{_unit('apt-daily-upgrade.timer', 'unit_file_state')} AS apt_timer_enabled, "
        f"{_unit('unattended-upgrades.service', 'active_state')} AS unattended_active, "
        f"{_unit('unattended-upgrades.service', 'unit_file_state')} AS unattended_enabled;",
        (
            "reboot_required",
            "apt_update_stamp",
            "apt_timer_active",
            "apt_timer_enabled",
            "unattended_active",
            "unattended_enabled",
        ),
        1,
    ),
)

_TLS_COLUMNS = ("hostname", "common_name", "issuer_common_name", "valid_to", "has_expired")
_TLS_MAX_ROWS = 16


def tls_query(hostnames: Sequence[str]) -> MonitoringQuery:
    """`mon_tls` para los hostnames dados; cada uno debe pasar la regex estricta."""
    for hostname in hostnames:
        if not HOSTNAME.fullmatch(hostname):
            raise ValueError("hostname TLS no válido")
    quoted = ",".join(f"'{hostname}'" for hostname in hostnames)
    sql = (
        "SELECT hostname, common_name, issuer_common_name, valid_to, has_expired "
        f"FROM curl_certificate WHERE hostname IN ({quoted});"
    )
    return MonitoringQuery("mon_tls", sql, _TLS_COLUMNS, _TLS_MAX_ROWS)


# Los IDs que acepta la ingesta (el SQL de `mon_tls` depende de la configuración).
MONITORING_QUERIES: dict[str, MonitoringQuery] = {
    **{q.id: q for q in _STATIC},
    "mon_tls": MonitoringQuery("mon_tls", "", _TLS_COLUMNS, _TLS_MAX_ROWS),
}


def is_monitoring_event(event: Any) -> bool:
    name = event.get("name") if isinstance(event, dict) else None
    return isinstance(name, str) and name.startswith(MONITORING_PREFIX)


def monitoring_schedule(tls_hostnames: Sequence[str]) -> dict[str, dict[str, Any]]:
    queries = list(_STATIC)
    if tls_hostnames:
        queries.append(tls_query(tls_hostnames))
    return {
        q.id: {"query": q.sql, "interval": INTERVAL_SECONDS, "snapshot": True, "platform": PLATFORM}
        for q in queries
    }
