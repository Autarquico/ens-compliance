"""Señales del VPS a partir de las muestras `mon_*` del agente osquery (solo lectura)."""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

from ens_evidence.agent.monitoring import INTERVAL_SECONDS, Sample
from ens_evidence.monitoring.model import (
    GROUP_VPS,
    Reading,
    Status,
    is_fresh,
    unknown,
    worst,
)
from ens_evidence.monitoring.thresholds import Thresholds

VPS_TTL = timedelta(seconds=3 * INTERVAL_SECONDS)
DEFAULT_DOCKER_ROOT = "/var/lib/docker"
APT_STAMP_MAX_AGE = timedelta(days=7)
_MONTHS = {
    m: i
    for i, m in enumerate(
        ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1
    )
}
Samples = Mapping[str, Sample]


def _row(samples: Samples, query: str, now: datetime) -> dict[str, str] | None:
    sample = samples.get(query)
    if sample is None or not sample.rows or not is_fresh(sample.observed_at, VPS_TTL, now):
        return None
    return sample.rows[0]


def _fresh_rows(samples: Samples, query: str, now: datetime) -> tuple[dict[str, str], ...] | None:
    sample = samples.get(query)
    if sample is None or not is_fresh(sample.observed_at, VPS_TTL, now):
        return None
    return sample.rows


def _number(raw: str | None) -> float | None:
    try:
        value = float(raw) if raw is not None else None
    except ValueError:
        return None
    return value if value is not None and value >= 0 and value == value else None


def _no_data(id: str, title: str) -> Reading:
    return unknown(id, GROUP_VPS, title, "Sin dato reciente del agente del VPS.")


def load(samples: Samples, limits: Thresholds, now: datetime) -> Reading:
    title = "Carga por núcleo"
    row = _row(samples, "mon_load", now)
    cores = _number(row.get("cores")) if row else None
    loads = [_number(row.get(k)) for k in ("load1", "load5", "load15")] if row else []
    if not row or not cores or None in loads:
        return _no_data("vps:load", title)
    per_core = [value / cores for value in loads]  # type: ignore[operator]
    status = Status.FAIL if per_core[1] > limits.load_per_core else Status.OK
    detail = (
        f"{per_core[0]:.2f} / {per_core[1]:.2f} / {per_core[2]:.2f} por núcleo "
        f"(1/5/15 min, {int(cores)} núcleos; umbral {limits.load_per_core:g} a 5 min)."
    )
    observed = samples["mon_load"].observed_at
    return Reading(
        "vps:load", GROUP_VPS, title, status, detail, value=per_core[1], observed_at=observed
    )


def memory(samples: Samples, limits: Thresholds, now: datetime) -> Reading:
    title = "Memoria"
    row = _row(samples, "mon_memory", now)
    total = _number(row.get("memory_total")) if row else None
    available = _number(row.get("memory_available")) if row else None
    if not total or available is None or available > total:
        return _no_data("vps:memory", title)
    used = 100 * (1 - available / total)
    status = Status.FAIL if used > limits.memory_percent else Status.OK
    detail = f"{used:.0f} % usada (umbral {limits.memory_percent:g} %)."
    observed = samples["mon_memory"].observed_at
    return Reading("vps:memory", GROUP_VPS, title, status, detail, value=used, observed_at=observed)


def _mount_for(path: str, mounts: tuple[dict[str, str], ...]) -> dict[str, str] | None:
    """El montaje cuya ruta es el prefijo más largo (por componentes) de `path`."""
    best: dict[str, str] | None = None
    for mount in mounts:
        point = mount.get("path", "")
        covers = point == "/" or path == point or path.startswith(point.rstrip("/") + "/")
        if covers and (best is None or len(point) > len(best["path"])):
            best = mount
    return best


def disks(samples: Samples, limits: Thresholds, now: datetime) -> list[Reading]:
    mounts = _fresh_rows(samples, "mon_disk", now)
    root_row = _row(samples, "mon_docker_root", now)
    docker_root = (root_row or {}).get("root_dir") or DEFAULT_DOCKER_ROOT
    targets = {"/": "Disco (raíz)", docker_root: "Disco (Docker)"}
    found: dict[str, dict[str, str] | None] = {
        path: _mount_for(path, mounts) if mounts else None for path in targets
    }
    if not any(found.values()):
        return [_no_data("vps:disk:/", "Disco")]
    readings = []
    seen: set[str] = set()
    for path, title in targets.items():
        mount = found[path]
        if mount is None:
            readings.append(_no_data(f"vps:disk:{path}", title))
            continue
        point = mount["path"]
        if point in seen:
            continue
        seen.add(point)
        readings.append(_disk(mount, title, limits, samples["mon_disk"].observed_at))
    return readings


def _disk(mount: dict[str, str], title: str, limits: Thresholds, observed: datetime) -> Reading:
    point = mount["path"]
    blocks, free = _number(mount.get("blocks")), _number(mount.get("blocks_available"))
    if not blocks or free is None or free > blocks:
        return _no_data(f"vps:disk:{point}", title)
    used = 100 * (1 - free / blocks)
    status = Status.FAIL if used > limits.disk_percent else Status.OK
    detail = f"{point}: {used:.0f} % usado (umbral {limits.disk_percent:g} %)."
    return Reading(
        f"vps:disk:{point}", GROUP_VPS, title, status, detail, value=used, observed_at=observed
    )


def parse_asn1_time(raw: str) -> datetime | None:
    """`Jan  1 00:00:00 2030 GMT` (ASN1_TIME_print), sin depender de la configuración regional."""
    parts = raw.split()
    if len(parts) != 5 or parts[4] != "GMT" or parts[0] not in _MONTHS:
        return None
    try:
        hour, minute, second = (int(x) for x in parts[2].split(":"))
        return datetime(
            int(parts[3]), _MONTHS[parts[0]], int(parts[1]), hour, minute, second, tzinfo=UTC
        )
    except ValueError:
        return None


def tls(samples: Samples, hostnames: list[str], limits: Thresholds, now: datetime) -> list[Reading]:
    rows = _fresh_rows(samples, "mon_tls", now)
    readings = []
    for hostname in hostnames:
        id, title = f"vps:tls:{hostname}", f"Certificado TLS {hostname}"
        row = next((r for r in rows or () if r.get("hostname") == hostname), None)
        valid_to = parse_asn1_time(row.get("valid_to", "")) if row else None
        if valid_to is None:
            readings.append(_no_data(id, title))
            continue
        days = (valid_to - now).total_seconds() / 86400
        expired = row.get("has_expired") == "1" or days < 0
        status = Status.FAIL if expired or days < limits.tls_days else Status.OK
        detail = (
            "Caducado." if expired else f"Caduca en {int(days)} días (umbral {limits.tls_days})."
        )
        observed = samples["mon_tls"].observed_at
        readings.append(
            Reading(id, GROUP_VPS, title, status, detail, value=days, observed_at=observed)
        )
    return readings


def updates(samples: Samples, now: datetime) -> Reading:
    """Solo indicios: el número pendiente es siempre desconocido y esta señal nunca es verde."""
    id, title = "vps:updates", "Actualizaciones del sistema (indicios)"
    row = _row(samples, "mon_updates", now)
    if row is None:
        return _no_data(id, title)
    hints = []
    if row.get("reboot_required") == "1":
        hints.append("reinicio pendiente")
    stamp = _number(row.get("apt_update_stamp"))
    if stamp is None:
        hints.append("sin registro de la última actualización de apt")
    elif now - datetime.fromtimestamp(stamp, UTC) > APT_STAMP_MAX_AGE:
        hints.append("apt no se actualiza desde hace más de 7 días")
    if row.get("unattended_active") not in {"active", "inactive"} or (
        row.get("apt_timer_active") != "active"
    ):
        hints.append("actualizaciones automáticas no activas")
    observed = samples["mon_updates"].observed_at
    if hints:
        return Reading(
            id,
            GROUP_VPS,
            title,
            Status.WARN,
            "; ".join(hints).capitalize() + ".",
            observed_at=observed,
        )
    return Reading(
        id,
        GROUP_VPS,
        title,
        Status.UNKNOWN,
        "Sin indicios; el número de actualizaciones pendientes es desconocido.",
        observed_at=observed,
    )


def container_status(row: dict[str, str]) -> tuple[Status, str]:
    """Estado y salud de un contenedor a partir de `state` y del texto de `status`."""
    state, text = row.get("state", ""), row.get("status", "").lower()
    if state != "running":
        return Status.FAIL, f"estado {state or 'desconocido'}"
    if "(unhealthy)" in text:
        return Status.FAIL, "en marcha pero no sano"
    if "(healthy)" in text:
        return Status.OK, "en marcha y sano"
    if "health: starting" in text:
        return Status.WARN, "arrancando (comprobación de salud pendiente)"
    return Status.WARN, "en marcha (sin comprobación de salud)"


def project_containers(
    samples: Samples, project: str, now: datetime
) -> dict[str, dict[str, str]] | None:
    """Servicio de Compose -> fila del contenedor del proyecto; `None` sin muestra vigente."""
    rows = _fresh_rows(samples, "mon_containers", now)
    if rows is None:
        return None
    return {
        r["compose_service"]: r
        for r in rows
        if r.get("compose_project") == project and r.get("compose_service")
    }


def containers(samples: Samples, project: str, now: datetime) -> Reading:
    id, title = "vps:containers", "Contenedores de la caja"
    found = project_containers(samples, project, now)
    if not found:
        return (
            _no_data(id, title)
            if found is None
            else unknown(
                id, GROUP_VPS, title, f"No hay contenedores del proyecto {project} en la muestra."
            )
        )
    judged = {name: container_status(row) for name, row in found.items()}
    status = worst([s for s, _ in judged.values()])
    bad = [f"{name}: {why}" for name, (s, why) in sorted(judged.items()) if s is not Status.OK]
    detail = "; ".join(bad[:6]) + "." if bad else f"{len(judged)} contenedores en marcha y sanos."
    return Reading(
        id, GROUP_VPS, title, status, detail, observed_at=samples["mon_containers"].observed_at
    )


def vps_readings(
    samples: Samples, hostnames: list[str], project: str, limits: Thresholds, now: datetime
) -> list[Reading]:
    return [
        load(samples, limits, now),
        memory(samples, limits, now),
        *disks(samples, limits, now),
        *tls(samples, hostnames, limits, now),
        updates(samples, now),
        containers(samples, project, now),
    ]
