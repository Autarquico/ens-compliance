"""Textos para personas de la matriz: un solo mapa para `status`, la exportación y la vista de la
Declaración.

El `BlockerKind` y el `detail` internos no cambian (un enum por eje, ADR 0002) y siguen en el JSON;
aquí solo se traducen a castellano llano con el siguiente paso concreto. Una medida que nadie ha
revisado figura como «sin revisar», nunca como «no conforme». Nada declara conformidad (regla 12).
"""

from collections.abc import Iterable

from ens_evidence.applicability import DeclarationState
from ens_evidence.matrix.obligations import COMPENSATION, RowKind
from ens_evidence.models.enums import MeasureAssessment, Readiness, Role
from ens_evidence.models.readiness import Blocker, BlockerKind

REVIEW_STEP = (
    "matrix review {m} --assessment COMPLIANT|NON_COMPLIANT|REQUIRES_REVIEW "
    '--by "NOMBRE" --role system_administrator --conclusion "…"'
)
REVIEW_NOT_APPLICABLE_STEP = (
    'matrix review {m} --by "NOMBRE" --role system_administrator --conclusion "…"'
)
LINK_STEP = "matrix link {m} --evidence EV-… --covers {a} (texto de la cláusula: catalog show {m})"

# (tipo, primera parte del detalle) -> texto. {m} medida; {a}, {b} partes siguientes del detalle.
BLOCKER_TEXTS: dict[tuple[BlockerKind, str], str] = {
    (BlockerKind.NOT_COMPLIANT, MeasureAssessment.NOT_ASSESSED.value): (
        "sin revisar: nadie ha valorado esta medida todavía. Siguiente paso: " + REVIEW_STEP
    ),
    (BlockerKind.NOT_COMPLIANT, MeasureAssessment.NON_COMPLIANT.value): (
        "la última revisión la valora como no conforme. Corrige lo que falta y vuelve a "
        "revisarla: " + REVIEW_STEP
    ),
    (BlockerKind.NOT_COMPLIANT, MeasureAssessment.REQUIRES_REVIEW.value): (
        "la última revisión pide revisarla de nuevo. Siguiente paso: " + REVIEW_STEP
    ),
    (BlockerKind.NOT_COMPLIANT, "check_failed"): (
        "el check {a} ha fallado en «{b}». Corrige el problema y espera a una ejecución nueva"
    ),
    (BlockerKind.MISSING_REVIEW, "review_missing"): (
        "sin revisar: nadie ha valorado esta medida todavía. Siguiente paso: " + REVIEW_STEP
    ),
    (BlockerKind.MISSING_REVIEW, ""): (
        "decisión de no aplicabilidad sin revisar. Siguiente paso: " + REVIEW_NOT_APPLICABLE_STEP
    ),
    (BlockerKind.MISSING_REVIEW, "review_in_future"): (
        "la revisión está fechada en el futuro. Registra una revisión con fecha válida: "
        + REVIEW_STEP
    ),
    (BlockerKind.MISSING_REVIEW, "owner_missing"): (
        "sin responsable. Siguiente paso: matrix set {m} --owner NOMBRE --owner-role ROL"
    ),
    (BlockerKind.MISSING_REVIEW, "review_every_days_missing"): (
        "sin periodicidad de revisión. Siguiente paso: matrix set {m} --every DÍAS"
    ),
    (BlockerKind.EXPIRED_REVIEW, ""): "la revisión ha caducado. Siguiente paso: matrix review {m}",
    (BlockerKind.EXPIRED_REVIEW, "context_changed"): (
        "la medida ha cambiado desde la última revisión (decisión de la Declaración, refuerzos, "
        "compensación o texto del catálogo). Hay que revisarla de nuevo: matrix review {m}"
    ),
    (BlockerKind.EXPIRED_REVIEW, "links_changed"): (
        "los enlaces de evidencia han cambiado desde la última revisión. Vuelve a revisarla: "
        "matrix review {m}"
    ),
    (BlockerKind.EXPIRED_REVIEW, "evidence_changed"): (
        "la evidencia {a} ha cambiado desde que se revisó. Vuelve a revisarla: matrix review {m}"
    ),
    (BlockerKind.EXPIRED_REVIEW, "document_changed"): (
        "el documento {a} ha cambiado desde que se revisó. Vuelve a revisarla: matrix review {m}"
    ),
    (BlockerKind.EXPIRED_EVIDENCE, "evidence_expired"): (
        "la evidencia {a} ha caducado. Importa una nueva (evidence import --source manual) y "
        "enlázala: matrix link {m} --evidence EV-… --covers …"
    ),
    (BlockerKind.EXPIRED_EVIDENCE, "document_not_approved"): (
        "el documento {a} está obsoleto. Apruébalo de nuevo (docs approve {a})"
    ),
    (BlockerKind.MISSING_EVIDENCE, ""): (
        "sin evidencia. Siguiente paso: matrix link {m} --evidence EV-… --covers base"
    ),
    (BlockerKind.MISSING_EVIDENCE, "clause"): "la cláusula {a} no tiene evidencia. " + LINK_STEP,
    (BlockerKind.MISSING_EVIDENCE, "obligation"): (
        "la obligación {a} no tiene evidencia. Siguiente paso: matrix link {m} --evidence EV-… "
        "--covers {a}"
    ),
    (BlockerKind.MISSING_EVIDENCE, "evidence_unverified"): (
        "la evidencia {a} no existe, no es manual o no pasa la verificación de integridad. "
        "Comprueba: evidence verify"
    ),
    (BlockerKind.MISSING_EVIDENCE, "evidence_in_future"): (
        "la evidencia {a} es posterior a la fecha evaluada"
    ),
    (BlockerKind.MISSING_EVIDENCE, "evidence_no_validity"): (
        "la evidencia {a} no tiene caducidad y la medida no tiene periodicidad. "
        "Siguiente paso: matrix set {m} --every DÍAS"
    ),
    (BlockerKind.MISSING_EVIDENCE, "source_after_review"): (
        "la fuente {b} se añadió después de la última revisión. Vuelve a revisarla: "
        "matrix review {m}"
    ),
    (BlockerKind.MISSING_EVIDENCE, "check_unknown"): "el check {a} no existe",
    (BlockerKind.MISSING_EVIDENCE, "check_no_results"): (
        "el check {a} aún no tiene ejecución completa; espera a la siguiente ejecución"
    ),
    (BlockerKind.MISSING_EVIDENCE, "check_incomplete"): (
        "el check {a} no cubrió «{b}» en su última ejecución completa"
    ),
    (BlockerKind.MISSING_EVIDENCE, "check_error"): "el check {a} dio error en «{b}»",
    (BlockerKind.MISSING_EVIDENCE, "check_unknown_subject"): "el check {a} no pudo valorar «{b}»",
    (BlockerKind.MISSING_EVIDENCE, "document_unknown"): (
        "el documento {a} no existe. Comprueba: docs status"
    ),
    (BlockerKind.MISSING_EVIDENCE, "document_not_approved"): (
        "el documento {a} no está aprobado (estado: {b}). Siguiente paso: docs approve {a}"
    ),
    (BlockerKind.MISSING_EVIDENCE, "document_not_covering"): (
        "el documento {a} no cubre las cláusulas que se le atribuyen. Revisa: docs status"
    ),
    (BlockerKind.UNDETERMINED_APPLICABILITY, "declaration_stale"): (
        "la Declaración de Aplicabilidad ya no vale. Siguiente paso: applicability status"
    ),
    (BlockerKind.UNDETERMINED_APPLICABILITY, "declaration_not_approved"): (
        "falta firmar la Declaración de Aplicabilidad. Siguiente paso: applicability status"
    ),
    (BlockerKind.UNDETERMINED_APPLICABILITY, "declaration_invalid"): (
        "la Declaración de Aplicabilidad no es válida. Siguiente paso: applicability status"
    ),
    (BlockerKind.UNDETERMINED_APPLICABILITY, ""): (
        "aplicabilidad sin determinar. Siguiente paso: applicability status"
    ),
    (BlockerKind.NOT_APPLICABLE_WITHOUT_JUSTIFICATION, ""): (
        "no aplicable sin justificar. Siguiente paso: applicability propose"
    ),
}

KIND_FALLBACK: dict[BlockerKind, str] = {
    BlockerKind.UNDETERMINED_APPLICABILITY: "aplicabilidad sin determinar",
    BlockerKind.NOT_APPLICABLE_WITHOUT_JUSTIFICATION: "no aplicable sin justificar",
    BlockerKind.MISSING_EVIDENCE: "falta evidencia",
    BlockerKind.EXPIRED_EVIDENCE: "evidencia caducada",
    BlockerKind.MISSING_REVIEW: "falta la revisión",
    BlockerKind.EXPIRED_REVIEW: "revisión caducada",
    BlockerKind.NOT_COMPLIANT: "medida valorada como no conforme",
    BlockerKind.OPEN_FINDING: "hallazgo abierto",
}

ROW_KIND_LABELS = {
    RowKind.SELECTED: "seleccionada",
    RowKind.NOT_SELECTED: "no seleccionada",
    RowKind.ABSENT_ASSETS: "no aplicable por ausencia de activo",
}

ASSESSMENT_LABELS = {
    MeasureAssessment.COMPLIANT: "conforme según la persona que revisa",
    MeasureAssessment.NON_COMPLIANT: "no conforme",
    MeasureAssessment.REQUIRES_REVIEW: "pide revisión",
    MeasureAssessment.NOT_ASSESSED: "sin revisar",
}

DECLARATION_LABELS = {
    DeclarationState.CURRENT: "vigente",
    DeclarationState.STALE: "no vigente (algo cambió después de firmarla)",
    DeclarationState.NOT_APPROVED: "sin firmar",
    DeclarationState.INVALID: "no válida",
}

READINESS_LABELS = {Readiness.READY: "preparada", Readiness.NOT_READY: "no preparada"}

COMPENSATION_LABEL = "compensación (art. 28.3)"

ROLE_LABELS = {
    Role.SYSTEM_ADMINISTRATOR: "administrador del sistema",
    Role.SYSTEM_ADMINISTRATOR_DELEGATE: "delegado del administrador del sistema",
    Role.SECURITY_OFFICER: "responsable de la seguridad",
    Role.SYSTEM_OWNER: "responsable del sistema",
    Role.MANAGEMENT: "dirección",
    Role.INFORMATION_OWNER: "responsable de la información",
    Role.SERVICE_OWNER: "responsable del servicio",
}

_GROUPED = {"clause": "cláusulas", "obligation": "obligaciones"}
_GROUPED_ONE = {"clause": "la cláusula", "obligation": "la obligación"}


def obligation_label(obligation_id: str) -> str:
    return COMPENSATION_LABEL if obligation_id == COMPENSATION else obligation_id


def role_label(role: Role) -> str:
    return ROLE_LABELS.get(role, role.value)


def _fill(template: str, measure_id: str | None, args: list[str]) -> str:
    a = args[0] if args else ""
    b = ":".join(args[1:])
    return template.format(m=measure_id or "MEDIDA", a=a, b=b)


def blocker_text(blocker: Blocker) -> str:
    """Un bloqueo en castellano llano y con el siguiente paso."""
    head, *args = blocker.detail.split(":")
    if head == "check_unknown" and len(args) > 1:
        head = "check_unknown_subject"
    template = BLOCKER_TEXTS.get((blocker.kind, head))
    if template is None:
        fallback = KIND_FALLBACK[blocker.kind]
        return f"{fallback}: {blocker.detail}" if blocker.detail else fallback
    return _fill(template, blocker.measure_id, args)


def blocker_lines(
    blockers: Iterable[Blocker], compensated: frozenset[str] = frozenset()
) -> list[str]:
    """Textos sin repetir, con las cláusulas o las obligaciones sin evidencia en una sola línea.
    `compensated`: cláusulas que sustituye la compensación; se enlazan con
    `--covers compensation`."""
    grouped: dict[tuple[str | None, str], list[str]] = {}
    texts: list[str] = []
    for blocker in blockers:
        head, _, rest = blocker.detail.partition(":")
        if blocker.kind is BlockerKind.MISSING_EVIDENCE and head in _GROUPED and rest:
            grouped.setdefault((blocker.measure_id, head), []).append(rest)
            continue
        text = blocker_text(blocker)
        if text not in texts:
            texts.append(text)
    for (measure_id, head), units in grouped.items():
        covers = [u for u in units if u not in compensated]
        if len(covers) < len(units):
            covers.append(COMPENSATION)
        if len(units) == 1 and covers == units:
            texts.append(
                blocker_text(
                    Blocker(
                        kind=BlockerKind.MISSING_EVIDENCE,
                        measure_id=measure_id,
                        detail=f"{head}:{units[0]}",
                    )
                )
            )
            continue
        step = _fill(
            "Siguiente paso: matrix link {m} --evidence EV-… --covers {a} (texto de cada "
            "cláusula: catalog show {m})",
            measure_id,
            [",".join(covers)],
        )
        subject = (
            f"falta evidencia en {_GROUPED_ONE[head]} {units[0]}"
            if len(units) == 1
            else f"faltan evidencias en {len(units)} {_GROUPED[head]}: {', '.join(units)}"
        )
        texts.append(f"{subject}. {step}")
    return texts
