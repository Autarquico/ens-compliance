"""Señales de la caja: planificador, agentes callados y copia externa."""

import os
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ens_evidence.agent.registry import RegistryError, load_registry
from ens_evidence.agent.state import AgentState
from ens_evidence.checks.inputs import NO_EVIDENCE, select_collector_input
from ens_evidence.connectors.backups.connector import SOURCE as BACKUPS_SOURCE
from ens_evidence.models.contracts import CollectionResult
from ens_evidence.models.enums import CollectionStatus
from ens_evidence.monitoring.model import (
    FUTURE_SKEW,
    GROUP_BOX,
    Reading,
    Status,
    is_fresh,
    unknown,
    worst,
)
from ens_evidence.monitoring.thresholds import Thresholds, load_thresholds
from ens_evidence.reports.store import scan_cycles
from ens_evidence.scheduling.config import ScheduleConfigError, load_schedule_config
from ens_evidence.scheduling.scheduler import HEARTBEAT_ENV, HEARTBEAT_FILE, scheduler_status
from ens_evidence.workspace import WorkspaceError, WorkspaceStore
from ens_evidence.workspace.cycle_lock import resolve_state_dir

BACKUP_CONNECTOR = "backups"
LISTED = 5


def _heartbeat_path(env: Mapping[str, str]) -> Path:
    configured = env.get(HEARTBEAT_ENV)
    return Path(configured) if configured else resolve_state_dir(env) / HEARTBEAT_FILE


def _heartbeat_at(env: Mapping[str, str]) -> datetime | None:
    try:
        return datetime.fromtimestamp(_heartbeat_path(env).stat().st_mtime, UTC)
    except OSError:
        return None


def scheduler(store: WorkspaceStore, now: datetime) -> Reading:
    """Latido del scheduler (lo renueva su propio bucle, no el del monitor) y resultado de los
    ciclos: sin latido reciente o con un ciclo sin terminar pasado el plazo, rojo."""
    id, title = "caja:scheduler", "Ejecuciones del scheduler"
    limits = load_thresholds(store)
    beat_limit = timedelta(minutes=limits.scheduler_heartbeat_minutes)
    if not is_fresh(_heartbeat_at(os.environ), beat_limit, now):
        return Reading(
            id,
            GROUP_BOX,
            title,
            Status.FAIL,
            f"El scheduler no late desde hace más de {limits.scheduler_heartbeat_minutes:g} min.",
            observed_at=now,
        )
    try:
        status = scheduler_status(store, load_schedule_config(store), now)
        cycles = scan_cycles(store).runs
    except (WorkspaceError, ScheduleConfigError, OSError):
        return unknown(id, GROUP_BOX, title, "No se puede leer el estado del scheduler.")
    grace = timedelta(minutes=limits.cycle_grace_minutes)
    stalled = [c for c in status.interrupted if now - c.started_at > grace]
    unfinished = len(stalled) + len(status.unreadable_cycles)
    backup_failed = _latest_cycle_backup_failed(cycles)
    problems = []
    if unfinished:
        problems.append(f"{unfinished} ciclo(s) fallido(s) o sin terminar")
    if backup_failed:
        problems.append("la copia del último ciclo falló")
    late = [job.kind for job in status.jobs if job.pending]
    if late:
        problems.append("ejecución vencida pendiente: " + ", ".join(late))
    if status.days_without_report:
        shown = ", ".join(status.days_without_report[-LISTED:])
        problems.append(f"días sin informe diario este mes: {shown}")
    last = status.last_reports.get("daily")
    if problems:
        verdict = Status.FAIL if unfinished else Status.WARN
        return Reading(
            id, GROUP_BOX, title, verdict, "; ".join(problems).capitalize() + ".", observed_at=now
        )
    detail = f"Último informe diario: {last.period}." if last else "Aún sin informe diario."
    return Reading(
        id, GROUP_BOX, title, Status.OK if last else Status.UNKNOWN, detail, observed_at=now
    )


def _latest_cycle_backup_failed(cycles: Mapping[str, Any]) -> bool:
    if not cycles:
        return False
    latest = cycles[max(cycles)]
    return any(m.stage == "backup_failed" for m in latest.stages)


def silent_agents(store: WorkspaceStore, limits: Thresholds, now: datetime) -> Reading | None:
    """Agentes admitidos y no revocados cuyo último contacto supera el umbral. `None` si no hay."""
    id, title = "caja:agentes", "Agentes que reportan"
    try:
        registry = load_registry(store)
        contacts = {c.asset_id: c.last_contact for c in AgentState().list_hosts()}
    except (RegistryError, OSError):
        return unknown(id, GROUP_BOX, title, "No se puede leer el registro de agentes.")
    active = sorted(name for name, record in registry.agents.items() if not record.revoked)
    if not active:
        return None
    limit = timedelta(hours=limits.agent_silence_hours)
    silent = [a for a in active if a in contacts and not is_fresh(contacts[a], limit, now)]
    never = [a for a in active if a not in contacts]
    if silent:
        status, detail = (
            Status.FAIL,
            f"Sin reportar desde hace más de {limits.agent_silence_hours:g} h: ",
        )
        detail += ", ".join(silent[:LISTED]) + "."
    elif never:
        status, detail = (
            Status.UNKNOWN,
            "Sin contacto registrado: " + ", ".join(never[:LISTED]) + ".",
        )
    else:
        status, detail = Status.OK, f"{len(active)} agente(s) reportan en plazo."
    return Reading(id, GROUP_BOX, title, status, detail, observed_at=now)


def _instant(raw: Any) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return moment.astimezone(UTC) if moment.tzinfo else None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _section_items(entry: dict[str, Any], name: str) -> list[dict[str, Any]] | None:
    """Los elementos de una sección recogida con éxito y sin registros ilegibles; si no, `None`."""
    section = _dict(_dict(entry.get("sections")).get(name))
    if section.get("status") != CollectionStatus.SUCCESS or _dict(section.get("summary")).get(
        "unreadable"
    ):
        return None
    items = section.get("items")
    if not isinstance(items, list):
        return None
    return [item for item in items if isinstance(item, dict)]


def _declared_offsite(entry: dict[str, Any], now: datetime) -> bool:
    declaration = _dict(entry.get("offsite"))
    reviewed, valid_until = (
        _instant(declaration.get("reviewed_at")),
        _instant(declaration.get("valid_until")),
    )
    return reviewed is not None and valid_until is not None and reviewed <= now < valid_until


def _destination_verdict(
    entry: dict[str, Any], limit: timedelta, now: datetime
) -> tuple[Status, str]:
    """Edad del último snapshot de un destino externo esperado, medida contra `now`."""
    name = str(entry.get("asset_id", "destino"))
    if entry.get("collection_status") != CollectionStatus.SUCCESS:
        return Status.UNKNOWN, f"{name}: la última recogida no se completó"
    repository = _section_items(entry, "repository")
    snapshots = _section_items(entry, "snapshots")
    if not repository or snapshots is None:
        return Status.UNKNOWN, f"{name}: la recogida no permite leer los snapshots"
    if repository[0].get("exists") is False:
        return Status.FAIL, f"{name}: el repositorio no existe"
    times = [_instant(s.get("time")) for s in snapshots]
    if any(t is None for t in times):
        return Status.UNKNOWN, f"{name}: hay snapshots con fecha ilegible"
    if not times:
        return Status.FAIL, f"{name}: no tiene ningún snapshot"
    age = now - max(times)  # type: ignore[type-var]
    hours = age.total_seconds() / 3600
    if age < -FUTURE_SKEW:
        return Status.UNKNOWN, f"{name}: el último snapshot tiene fecha futura"
    if age > limit:
        return Status.FAIL, f"{name}: el último snapshot tiene {hours:.0f} h"
    return Status.OK, f"{name}: el último snapshot tiene {hours:.0f} h"


def offsite_backup(store: WorkspaceStore, limits: Thresholds, now: datetime) -> Reading | None:
    """Edad, a cada vuelta, del último snapshot de cada destino externo declarado y vigente.

    La edad del dato (cuándo se leyó el repositorio) es otra: pasado `backup_evidence_hours` no
    se sabe cuál es la última copia y la señal es desconocida, nunca verde. `None` sin copias."""
    id, title = "caja:copia-externa", "Copia externa al día"
    selection = select_collector_input(store, BACKUP_CONNECTOR, BACKUPS_SOURCE)
    if selection.error == NO_EVIDENCE:
        return None

    def reading(status: Status, detail: str, at: datetime | None = None) -> Reading:
        return Reading(id, GROUP_BOX, title, status, detail, "kit-copias", observed_at=at)

    result: CollectionResult | None = selection.result
    if result is None:
        return reading(Status.UNKNOWN, "No se puede leer la evidencia de las copias.")
    collected = selection.collected_at
    if not is_fresh(collected, timedelta(hours=limits.backup_evidence_hours), now):
        return reading(
            Status.UNKNOWN,
            f"Los datos de las copias tienen más de {limits.backup_evidence_hours:g} h.",
            collected,
        )
    entries = [e for e in _entries(result) if _is_expected_offsite(e)]
    if not entries:
        return reading(Status.UNKNOWN, "No hay un destino externo declarado y vigente.", collected)
    limit = timedelta(hours=limits.backup_hours)
    verdicts = [
        _destination_verdict(e, limit, now)
        if _declared_offsite(e, now)
        else (Status.UNKNOWN, f"{e.get('asset_id', 'destino')}: la declaración no está vigente")
        for e in entries
    ]
    status = worst(v for v, _ in verdicts)
    return reading(status, "; ".join(d for _, d in verdicts) + ".", collected)


def _entries(result: CollectionResult) -> list[dict[str, Any]]:
    entries = result.data.get("repositories")
    return [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []


def _is_expected_offsite(entry: dict[str, Any]) -> bool:
    """Destino que el operador declaró externo. La declaración persiste aunque la recogida falle,
    así que un repositorio ilegible sigue esperándose; uno legible solo si es `remote`."""
    if not _dict(entry.get("offsite")):
        return False
    repository = _section_items(entry, "repository")
    return not repository or repository[0].get("backend") == "remote"
