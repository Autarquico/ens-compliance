"""Señales de la caja: planificador, agentes callados y copia externa."""

from datetime import datetime, timedelta

from ens_evidence.agent.registry import RegistryError, load_registry
from ens_evidence.agent.state import AgentState
from ens_evidence.checks.store import scan_runs
from ens_evidence.models.enums import CheckStatus
from ens_evidence.monitoring.model import (
    GROUP_BOX,
    Reading,
    Status,
    is_fresh,
    unknown,
    worst,
)
from ens_evidence.monitoring.thresholds import Thresholds
from ens_evidence.scheduling.config import ScheduleConfigError, load_schedule_config
from ens_evidence.scheduling.scheduler import scheduler_status
from ens_evidence.workspace import WorkspaceError, WorkspaceStore

BACKUP_CONNECTOR = "backups"
BACKUP_FRESHNESS_CHECK = "BACKUP-001"
LISTED = 5
_CHECK_TO_STATUS = {
    CheckStatus.PASS: Status.OK,
    CheckStatus.FAIL: Status.FAIL,
    CheckStatus.ERROR: Status.UNKNOWN,
    CheckStatus.UNKNOWN: Status.UNKNOWN,
}


def scheduler(store: WorkspaceStore, now: datetime) -> Reading:
    id, title = "caja:scheduler", "Ejecuciones del scheduler"
    try:
        status = scheduler_status(store, load_schedule_config(store), now)
    except (WorkspaceError, ScheduleConfigError, OSError):
        return unknown(id, GROUP_BOX, title, "No se puede leer el estado del scheduler.")
    problems = []
    if status.interrupted or status.unreadable_cycles:
        problems.append(
            f"{len(status.interrupted) + len(status.unreadable_cycles)} ciclo(s) sin terminar"
        )
    late = [job.kind for job in status.jobs if job.pending]
    if late:
        problems.append("ejecución vencida pendiente: " + ", ".join(late))
    if status.days_without_report:
        shown = ", ".join(status.days_without_report[-LISTED:])
        problems.append(f"días sin informe diario este mes: {shown}")
    last = status.last_reports.get("daily")
    if problems:
        verdict = Status.FAIL if status.interrupted or status.unreadable_cycles else Status.WARN
        return Reading(
            id, GROUP_BOX, title, verdict, "; ".join(problems).capitalize() + ".", observed_at=now
        )
    detail = f"Último informe diario: {last.period}." if last else "Aún sin informe diario."
    return Reading(
        id, GROUP_BOX, title, Status.OK if last else Status.UNKNOWN, detail, observed_at=now
    )


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


def offsite_backup(store: WorkspaceStore, limits: Thresholds, now: datetime) -> Reading | None:
    """Frescura de la copia (BACKUP-001) en la última comprobación; `None` sin copias en la caja."""
    id, title = "caja:copia-externa", "Copia externa al día"
    last = scan_runs(store, BACKUP_CONNECTOR).last(BACKUP_CONNECTOR)
    if last is None:
        return None
    run = last.run
    if not is_fresh(run.finished_at, timedelta(hours=limits.backup_hours), now):
        return Reading(
            id,
            GROUP_BOX,
            title,
            Status.UNKNOWN,
            f"La última comprobación tiene más de {limits.backup_hours:g} h.",
            "kit-copias",
            observed_at=run.finished_at,
        )
    results = [r for r in run.results if r.check_id == BACKUP_FRESHNESS_CHECK]
    verdicts = [_CHECK_TO_STATUS[r.status] for r in results]
    status = worst(verdicts)
    detail = {
        Status.OK: "La última copia es reciente.",
        Status.FAIL: "La última copia no está al día.",
        Status.UNKNOWN: "No se ha podido comprobar la última copia.",
    }[status]
    return Reading(id, GROUP_BOX, title, status, detail, "kit-copias", observed_at=run.finished_at)
