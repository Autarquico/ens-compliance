"""Disposición humana de un hallazgo de juicio: registro de solo adición (`chain.py`).

La disposición cubre las apariciones del hallazgo registradas hasta ahora (`covers_run`, la última
ejecución que lo propuso); si otra posterior lo vuelve a proponer, vuelve a estar abierto.
`ACCEPTED` reconoce el hallazgo y sigue bloqueando. `RESOLVED` cita una evidencia existente e
íntegra que lo corrige. `DISMISSED` lo descarta con un motivo. El nombre y el motivo pasan por los
detectores de privacidad: no se guarda un secreto ni un dato personal. Orden de escritura, como en
las ejecuciones: comprobar (un rechazo no escribe nada), reservar la secuencia, escribir.
"""

from dataclasses import replace
from datetime import datetime

from ens_evidence import chain
from ens_evidence.evidence.verify import verify_one
from ens_evidence.models.preaudit import (
    NEXT_STATES,
    Disposition,
    DispositionRecord,
    DispositionState,
)
from ens_evidence.preaudit.findings import FindingState, findings_view
from ens_evidence.preaudit.records import DISPOSITIONS_PREFIX, scan_dispositions
from ens_evidence.privacy_text import check_declared_name, text_problems
from ens_evidence.scope.secrets import find_secrets
from ens_evidence.workspace import WorkspaceStore


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
    found, warnings = text_problems(reason)
    found += warnings
    if find_secrets(reason):
        found.append("parece un secreto")
    return [f"--reason: {problem}; quítalo" for problem in found]


def _write(store: WorkspaceStore, disposition: Disposition, now: datetime) -> chain.Stored:
    scan = scan_dispositions(store, now=now)
    last = scan.records[-1] if scan.records else None
    record = DispositionRecord(
        seq=(last.record.seq if last else 0) + 1,
        previous_sha256=last.sha256 if last else None,
        recorded_at=now,
        disposition=disposition,
    )
    data = chain.render_record(record.model_dump(mode="json"))
    try:
        chain.reserve_sequence(store, DISPOSITIONS_PREFIX, record.seq)
    except chain.SequenceTaken:
        raise ReviewRejected(
            [f"otra orden reservó antes la disposición {record.seq}; revisa y reintenta"]
        ) from None
    key = chain.record_key(DISPOSITIONS_PREFIX, record.seq, record.recorded_at, data)
    store.create_bytes(key, data)
    return chain.Stored(key, data, record)
