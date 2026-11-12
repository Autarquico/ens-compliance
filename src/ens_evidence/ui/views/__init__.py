"""Vistas de solo lectura y utilidades comunes de las páginas."""

from datetime import UTC, datetime

from ens_evidence.models.readiness import BlockerKind

NO_DATE = "—"


def format_instant(value: datetime | None) -> str:
    if value is None:
        return NO_DATE
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


BLOCKER_LABELS = {
    BlockerKind.UNDETERMINED_APPLICABILITY: "Aplicabilidad sin decidir",
    BlockerKind.NOT_APPLICABLE_WITHOUT_JUSTIFICATION: "Falta justificar que no aplica",
    BlockerKind.MISSING_EVIDENCE: "Falta evidencia",
    BlockerKind.EXPIRED_EVIDENCE: "Evidencia caducada",
    BlockerKind.MISSING_REVIEW: "Falta revisión",
    BlockerKind.EXPIRED_REVIEW: "Revisión caducada",
    BlockerKind.NOT_COMPLIANT: "Valoración pendiente o desfavorable",
    BlockerKind.OPEN_FINDING: "Hallazgo abierto",
}
