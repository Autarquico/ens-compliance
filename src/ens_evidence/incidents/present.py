"""Presentación en texto de un incidente: lo que la CLI imprime en `list` y `show`."""

from ens_evidence.incidents.model import (
    ActionKind,
    IncidentState,
    IncidentStatus,
    RecordKind,
    ReviewResult,
    TriState,
)
from ens_evidence.incidents.verify import IncidentScan

SIMULATION_LABEL = "SIMULACRO"
STATUS_LABELS = {IncidentStatus.OPEN: "abierto", IncidentStatus.CLOSED: "cerrado"}
RECORD_LABELS = {
    RecordKind.OPENED: "reporte inicial",
    RecordKind.UPDATED: "reporte intermedio",
    RecordKind.CLOSED: "reporte final",
}
ACTION_LABELS = {
    ActionKind.EMERGENCY: "emergencia",
    ActionKind.SYSTEM_CHANGE: "cambio del sistema",
    ActionKind.NOTIFICATION: "notificación",
    ActionKind.OTHER: "otra",
}
TRISTATE_LABELS = {TriState.YES: "sí", TriState.NO: "no", TriState.UNKNOWN: "sin determinar"}
REVIEW_LABELS = {
    ReviewResult.CHANGED: "se cambiaron los eventos auditables",
    ReviewResult.NO_CHANGE: "sin cambios en los eventos auditables",
}
SIMULATION_NOTE = "SIMULACRO: ejercicio de prueba del procedimiento, no un incidente real."


def list_line(incident_id: str, state: IncidentState | None, broken: bool) -> str:
    flags = (f"[{SIMULATION_LABEL}] " if state and state.simulated else "") + (
        "[ROTO] " if broken else ""
    )
    if state is None:
        return f"{incident_id}  {flags}sin registros legibles"
    severity = state.severity or "sin severidad"
    return (
        f"{incident_id}  {flags}{STATUS_LABELS[state.status]}  {severity}  "
        f"{state.detected_at.date().isoformat()}  {state.title}"
    )


def show_lines(scan: IncidentScan, problems: list[str]) -> list[str]:
    lines = [f"{scan.incident_id}"]
    if not scan.records:
        return [*lines, "  Sin registros legibles."] + _problem_lines(problems)
    state = scan.records[-1].record.state
    if state.simulated:
        lines.append(f"  {SIMULATION_NOTE}")
    lines += [
        f"  Título: {state.title}",
        f"  Estado: {STATUS_LABELS[state.status]}   Severidad declarada: {state.severity or '-'}",
        f"  Detectado: {state.detected_at.isoformat()}",
        f"  Datos personales: {TRISTATE_LABELS[state.personal_data]}",
        f"  Activos: {', '.join(state.assets) or '-'}",
    ]
    if state.source:
        alerts = (
            f" (alertas: {', '.join(state.source.alert_ids)})" if state.source.alert_ids else ""
        )
        lines.append(f"  Origen: {state.source.system}{alerts}")
    lines.append(
        f"  Procedimiento: {state.procedure.name} "
        f"aprobado {state.procedure.approved_at.isoformat()}"
        if state.procedure
        else "  Procedimiento: ninguno vigente al alta"
    )
    if state.analysis.summary:
        lines.append(f"  Análisis: {state.analysis.summary}")
    lines += [f"  Recomendación: {text}" for text in state.analysis.recommendations]
    lines += [
        f"  Actuación {action.at.isoformat()} [{ACTION_LABELS[action.kind]}]: {action.description}"
        for action in state.actions
    ]
    if state.evidence_ids:
        lines.append(f"  Evidencias: {', '.join(state.evidence_ids)}")
    if state.closure:
        closure = state.closure
        review = closure.auditable_events_review
        lines += [
            f"  Cierre {closure.closed_at.isoformat()}: {closure.conclusion}",
            f"  Relevancia jurisdiccional: {TRISTATE_LABELS[closure.legal_relevance]}"
            + (f" (asesoramiento: {closure.legal_advice})" if closure.legal_advice else ""),
            f"  Evidencias preservadas: {', '.join(closure.preserved_evidence_ids) or '-'}",
            f"  Eventos auditables: {REVIEW_LABELS[review.result]}: {review.detail}",
        ]
    lines.append("  Registros:")
    lines += [
        f"    {s.record.seq:>3} {RECORD_LABELS[s.record.kind]:<18} "
        f"{s.record.recorded_at.isoformat()}  {s.key}"
        for s in scan.records
    ]
    return lines + _problem_lines(problems)


def _problem_lines(problems: list[str]) -> list[str]:
    if not problems:
        return []
    return [
        "  PROBLEMAS (el incidente no admite más registros):",
        *[f"    - {p}" for p in problems],
    ]
