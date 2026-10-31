"""Presentación de `assessment status` en texto y JSON. Determinista: sin reloj ni aleatoriedad."""

import json

from ens_evidence.assessment.status import AssessmentStatus, ConnectorSummary
from ens_evidence.matrix.messages import (
    DECLARATION_LABELS,
    READINESS_LABELS,
    blocker_lines,
    blocker_text,
)

HUMAN_REVIEW_PENDING = (
    "Revisión humana pendiente: la capa de juicio de la pre-auditoría no se ha ejecutado; "
    "este estado no sustituye la valoración de una persona."
)
NO_PRE_AUDIT = "pre-auditoría no ejecutada: no hay hallazgos registrados"
NO_CONFORMITY = "La herramienta no declara conformidad."


def _counts(summary: ConnectorSummary) -> str:
    return "  ".join(f"{status.value} {n}" for status, n in summary.counts.items())


def _connector_line(summary: ConnectorSummary) -> str:
    if summary.finished_at is None:
        return f"  {summary.connector}: sin ejecución; ejecuta `ens-evidence check --all`"
    incomplete = "" if summary.complete else "  (ejecución incompleta)"
    return (
        f"  {summary.connector}: ejecución de {summary.finished_at.isoformat()}  "
        f"{_counts(summary)}{incomplete}"
    )


def render_status_text(status: AssessmentStatus) -> str:
    result = status.result
    readiness = result.readiness
    count = len(readiness.blockers)
    lines = [
        f"Declaración de Aplicabilidad: {DECLARATION_LABELS[result.declaration_state]}.",
        f"Evaluado: {result.evaluated_at.isoformat()}",
        f"Preparación: {READINESS_LABELS[readiness.readiness]}, "
        f"{count} bloqueo{'' if count == 1 else 's'}.",
    ]
    lines += [f"  bloqueo: {text}" for text in blocker_lines(readiness.blockers)]
    lines += [f"  error: {error}" for error in result.errors]
    if status.pending_human_review:
        lines += [HUMAN_REVIEW_PENDING, NO_PRE_AUDIT]
    lines.append("Fuentes técnicas (última ejecución de cada conector):")
    lines += [_connector_line(s) for s in status.connectors]
    lines.append(NO_CONFORMITY)
    return "\n".join(lines) + "\n"


def render_status_json(status: AssessmentStatus) -> str:
    result = status.result
    readiness = result.readiness
    document = {
        "evaluated_at": result.evaluated_at.isoformat(),
        "declaration_state": result.declaration_state.value,
        "readiness": readiness.readiness.value,
        "pending_human_review": status.pending_human_review,
        "pre_audit": NO_PRE_AUDIT if status.pending_human_review else None,
        "blockers": [
            {
                "kind": b.kind.value,
                "measure_id": b.measure_id,
                "detail": b.detail,
                "text": blocker_text(b),
            }
            for b in readiness.blockers
        ],
        "errors": list(result.errors),
        "checks": [
            {
                "connector": s.connector,
                "finished_at": s.finished_at.isoformat() if s.finished_at else None,
                "complete": s.complete,
                "counts": {k.value: n for k, n in s.counts.items()},
            }
            for s in status.connectors
        ],
        "notice": NO_CONFORMITY,
    }
    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
