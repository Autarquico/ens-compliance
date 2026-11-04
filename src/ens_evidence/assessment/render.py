"""Presentación de `assessment status` en texto y JSON. Determinista: sin reloj ni aleatoriedad."""

import json
from collections.abc import Iterable

from ens_evidence.assessment.status import AssessmentStatus, ConnectorSummary
from ens_evidence.matrix.messages import (
    DECLARATION_LABELS,
    READINESS_LABELS,
    blocker_lines,
    blocker_text,
)
from ens_evidence.matrix.status import NO_CHECK_RUN
from ens_evidence.models.preaudit import LayerStatus, PreAuditRecord
from ens_evidence.models.readiness import Blocker

HUMAN_REVIEW_PENDING = (
    "Revisión humana pendiente: la capa de juicio de la pre-auditoría no se ha ejecutado; "
    "este estado no sustituye la valoración de una persona."
)
NO_PRE_AUDIT = "Pre-auditoría no ejecutada sobre el expediente vigente."
JUDGMENT_LABELS = {
    LayerStatus.DONE: "ejecutada",
    LayerStatus.NOT_RUN: "no ejecutada",
    LayerStatus.ERROR: "con error, sin hallazgos",
}
NO_CONFORMITY = "La herramienta no declara conformidad."


NOT_CONFIGURED = "no configurado (configúralo si usas este conector)"
NO_MEASURE = "general"


def _counts(summary: ConnectorSummary) -> str:
    return "  ".join(f"{status.value} {n}" for status, n in summary.counts.items())


def pre_audit_notice(run: PreAuditRecord | None) -> str:
    """Si hubo pre-auditoría sobre el expediente vigente, cuándo y qué fue de su capa de juicio."""
    if run is None:
        return NO_PRE_AUDIT
    return (
        f"Pre-auditoría {run.seq} del {run.recorded_at.isoformat()} sobre el expediente vigente: "
        f"capa determinista ejecutada; capa de juicio {JUDGMENT_LABELS[run.run.judgment_status]}."
    )


def _connector_line(summary: ConnectorSummary) -> str:
    if summary.finished_at is None:
        detail = NO_CHECK_RUN if summary.configured else NOT_CONFIGURED
        return f"  {summary.connector}: {detail}"
    incomplete = "" if summary.complete else "  (ejecución incompleta)"
    return (
        f"  {summary.connector}: ejecución de {summary.finished_at.isoformat()}  "
        f"{_counts(summary)}{incomplete}"
    )


def blocker_lines_by_measure(blockers: Iterable[Blocker]) -> dict[str, list[str]]:
    """Los textos de bloqueo de cada medida, sin repetir, en orden de aparición."""
    grouped: dict[str, list[Blocker]] = {}
    for blocker in blockers:
        grouped.setdefault(blocker.measure_id or NO_MEASURE, []).append(blocker)
    return {measure: blocker_lines(items) for measure, items in grouped.items()}


def _in_measures(measures: int) -> str:
    return f" en {measures} medida{'' if measures == 1 else 's'}" if measures else ""


def render_status_text(status: AssessmentStatus) -> str:
    result = status.result
    readiness = result.readiness
    by_measure = blocker_lines_by_measure(readiness.blockers)
    count = sum(len(texts) for texts in by_measure.values())
    lines = [
        f"Declaración de Aplicabilidad: {DECLARATION_LABELS[result.declaration_state]}.",
        f"Evaluado: {result.evaluated_at.isoformat()}",
        f"Preparación: {READINESS_LABELS[readiness.readiness]}, "
        f"{count} bloqueo{'' if count == 1 else 's'}"
        f"{_in_measures(len(by_measure))}.",
    ]
    lines += [f"  {measure}: {text}" for measure, texts in by_measure.items() for text in texts]
    lines += [f"  error: {error}" for error in result.errors]
    if status.pending_human_review:
        lines.append(HUMAN_REVIEW_PENDING)
    lines.append(pre_audit_notice(status.pre_audit))
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
        "pre_audit": pre_audit_notice(status.pre_audit),
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
                "configured": s.configured,
                "finished_at": s.finished_at.isoformat() if s.finished_at else None,
                "complete": s.complete,
                "counts": {k.value: n for k, n in s.counts.items()},
            }
            for s in status.connectors
        ],
        "notice": NO_CONFORMITY,
    }
    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
