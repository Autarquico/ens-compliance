"""Disposición humana de un hallazgo de juicio: registro de solo adición (`chain.py`).

La disposición cubre las apariciones del hallazgo registradas hasta ahora (`covers_run`, la última
ejecución que lo propuso); si otra posterior lo vuelve a proponer, vuelve a estar abierto.
`ACCEPTED` reconoce el hallazgo y sigue bloqueando. `RESOLVED` cita una evidencia existente e
íntegra que lo corrige: distinta de las que cita el hallazgo y recogida después de la última
ejecución que lo propuso (y no en el futuro). `DISMISSED` lo descarta con un motivo. El nombre y
el motivo pasan por los detectores de privacidad: no se guarda un secreto ni un dato personal.
Orden de escritura, como en las ejecuciones: comprobar (un rechazo no escribe nada), reservar la
secuencia, escribir.
"""

from dataclasses import replace
from datetime import datetime

from ens_evidence import chain
from ens_evidence.evidence.layout import is_evidence_id
from ens_evidence.evidence.manifests import ManifestError, load_manifest_by_id
from ens_evidence.evidence.verify import verify_one
from ens_evidence.models.preaudit import (
    NEXT_STATES,
    Disposition,
    DispositionRecord,
    DispositionState,
    RefKind,
)
from ens_evidence.preaudit.findings import FindingState, findings_view
from ens_evidence.preaudit.records import (
    DISPOSITIONS_PREFIX,
    append,
    next_link,
    scan_dispositions,
)
from ens_evidence.privacy_text import check_declared_name, sensitive_problems
from ens_evidence.workspace import WorkspaceStore

NOT_A_CORRECTION = "--evidence: esa evidencia {why}; importa la que muestra la corrección y cítala"


class ReviewRejected(Exception):
    """No se registra nada; `errors` dice por qué (nunca el valor de lo rechazado)."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


def review(
    store: WorkspaceStore,
    finding_id: str,
    decision: DispositionState,
    *,
    by: str,
    now: datetime,
    evidence_id: str | None = None,
    reason: str | None = None,
) -> tuple[chain.Stored[DispositionRecord], FindingState]:
    """Registra la disposición y devuelve el registro y el hallazgo tal como queda."""
    errors = _field_problems(store, decision, by, evidence_id, reason)
    view = findings_view(store, now)
    if view.problems:
        broken = "el registro de pre-auditorías no se verifica; no admite disposiciones"
        raise ReviewRejected([broken, *view.problems])
    current = view.state_of(finding_id)
    if current is None:
        errors.append(f"{finding_id}: ninguna ejecución propuso ese hallazgo de juicio")
    elif decision not in NEXT_STATES[current.state]:
        errors.append(
            f"el hallazgo está {current.state.value}; no admite {decision.value}"
            + ("" if NEXT_STATES[current.state] else " (ya no bloquea)")
        )
    if current is not None and decision is DispositionState.RESOLVED and evidence_id:
        errors += _correction_problems(store, current, evidence_id, now)
    if errors:
        raise ReviewRejected(errors)
    assert current is not None
    disposition = Disposition(
        finding_id=finding_id,
        covers_run=current.last_run_seq,
        state=decision,
        by=by,
        at=now,
        evidence_id=evidence_id,
        reason=reason,
    )
    stored = _write(store, disposition, now)
    return stored, replace(current, disposition=disposition)


def _field_problems(
    store: WorkspaceStore,
    decision: DispositionState,
    by: str,
    evidence_id: str | None,
    reason: str | None,
) -> list[str]:
    errors = check_declared_name(by)
    match decision:
        case DispositionState.OPEN:
            errors.append("--decision: OPEN es el estado de partida, no una disposición")
        case DispositionState.RESOLVED:
            if evidence_id is None:
                errors.append("--evidence: RESOLVED cita la evidencia que corrige el hallazgo")
            else:
                errors += [f"--evidence: {p}" for p in verify_one(store, evidence_id).problems]
        case DispositionState.DISMISSED:
            if not (reason and reason.strip()):
                errors.append("--reason: DISMISSED explica por qué se descarta")
    if evidence_id is not None and decision is not DispositionState.RESOLVED:
        errors.append("--evidence: solo RESOLVED cita una evidencia")
    if reason is not None:
        errors += _reason_problems(reason)
    return errors


def _reason_problems(reason: str) -> list[str]:
    return [f"--reason: {problem}; quítalo" for problem in sensitive_problems(reason)]


def _correction_problems(
    store: WorkspaceStore, current: FindingState, evidence_id: str, now: datetime
) -> list[str]:
    """`RESOLVED` no se apoya en lo que prueba el incumplimiento: la evidencia no puede ser una
    de las que cita el hallazgo ni haberse recogido antes de la última ejecución que lo propuso."""
    cited = {ref.id for ref in current.finding.refs if ref.kind is RefKind.EVIDENCE}
    if evidence_id in cited:
        return [NOT_A_CORRECTION.format(why="es la que cita el hallazgo como prueba")]
    if not is_evidence_id(evidence_id):
        return []  # `verify_one` ya lo rechaza con su motivo.
    try:
        collected = load_manifest_by_id(store, evidence_id).collected_at
    except ManifestError:
        return []
    if collected <= current.last_run.recorded_at:
        return [
            NOT_A_CORRECTION.format(
                why=f"se recogió antes de la ejecución {current.last_run_seq} que propuso el "
                "hallazgo"
            )
        ]
    if collected > now:
        return [NOT_A_CORRECTION.format(why="tiene fecha de recogida en el futuro")]
    return []


def _write(
    store: WorkspaceStore, disposition: Disposition, now: datetime
) -> chain.Stored[DispositionRecord]:
    seq, previous = next_link(scan_dispositions(store, now=now))
    record = DispositionRecord(
        seq=seq, previous_sha256=previous, recorded_at=now, disposition=disposition
    )
    try:
        return append(store, DISPOSITIONS_PREFIX, record)
    except chain.SequenceTaken:
        raise ReviewRejected(
            [f"otra orden reservó antes la disposición {seq}; revisa y reintenta"]
        ) from None
