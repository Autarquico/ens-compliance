"""Presentación en texto de un incidente: lo que la CLI imprime en `list` y `show`."""

from ens_evidence.incidents.model import IncidentState
from ens_evidence.incidents.verify import IncidentScan

SIMULATION_LABEL = "SIMULACRO"
SIMULATION_NOTE = "SIMULACRO: ejercicio de prueba del procedimiento, no un incidente real."


def list_line(incident_id: str, state: IncidentState | None, broken: bool) -> str:
    flags = ("[SIMULACRO] " if state and state.simulated else "") + ("[ROTO] " if broken else "")
    if state is None:
        return f"{incident_id}  {flags}sin registros legibles"
    severity = state.severity or "sin severidad"
    return (
        f"{incident_id}  {flags}{state.status.value}  {severity}  "
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
        f"  Estado: {state.status.value}   Severidad declarada: {state.severity or '-'}",
        f"  Detectado: {state.detected_at.isoformat()}",
        f"  Datos personales: {state.personal_data.value}",
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
        f"  Actuación {action.at.isoformat()} [{action.kind.value}]: {action.description}"
        for action in state.actions
    ]
    if state.evidence_ids:
        lines.append(f"  Evidencias: {', '.join(state.evidence_ids)}")
    if state.closure:
        closure = state.closure
        review = closure.auditable_events_review
        lines += [
            f"  Cierre {closure.closed_at.isoformat()}: {closure.conclusion}",
            f"  Relevancia jurisdiccional: {closure.legal_relevance.value}"
            + (f" (asesoramiento: {closure.legal_advice})" if closure.legal_advice else ""),
            f"  Evidencias preservadas: {', '.join(closure.preserved_evidence_ids) or '-'}",
            f"  Eventos auditables: {review.result.value}: {review.detail}",
        ]
    lines.append("  Registros:")
    lines += [
        f"    {s.record.seq:>3} {s.record.kind.value:<8} "
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
