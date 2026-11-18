"""Peticiones de recogida de la página de conectores: proyección a DTO de lo que dejó la UI.

Solo se lee lo que escribieron `request_collect` y el ejecutor: enums, códigos y horas. Toda
etiqueta es fija. `Terminada` nunca es favorable por sí sola: separa la terminación, el resultado
de la recogida y el de las comprobaciones. La web no ejecuta nada: no importa `collect_one` ni
`run_one`.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from ens_evidence.evidence.layout import is_evidence_id
from ens_evidence.models.enums import CollectionStatus
from ens_evidence.scheduling.collect_requests import (
    ChecksOutcome,
    CollectRequestResult,
    ErrorCode,
    Outcome,
    RequestStatus,
    State,
    list_requests,
)
from ens_evidence.ui.store import UiStore
from ens_evidence.ui.views import format_instant
from ens_evidence.ui.views.conectores_recogida import PHASE_LABELS

Tone = Literal["ok", "warn", "bad", "neutral"]

PENDING_TEXT = "Se recogerá en cuanto el servicio esté libre; normalmente en menos de un minuto."
RUNNING_TEXT = "El servicio la está recogiendo ahora."
EXPIRED_TEXT = (
    "Pasaron 24 horas sin que el servicio la atendiera, así que no se ejecutará. Comprueba que el "
    "servicio de recogida está en marcha (runbook de la interfaz web, «Recoger ahora»)."
)
INVALID_ID = "identificador no válido"
CODE_LABELS = {
    ErrorCode.INTERRUPTED: "la ejecución se interrumpió",
    ErrorCode.NOT_CONFIGURED: "el conector no está configurado",
    ErrorCode.CONNECTOR_UNKNOWN: "el conector no está registrado",
    ErrorCode.CONFIG_INVALID: "la configuración no es válida",
    ErrorCode.COLLECT_CRASHED: "la recogida falló de forma inesperada",
    ErrorCode.EXPIRED: "Caducada: no se llegó a ejecutar",
    ErrorCode.UNREADABLE: "la petición o su resultado no se pueden leer",
}
NO_CODE = "sin causa indicada"
COLLECTION_LABELS = {
    CollectionStatus.SUCCESS: "recogida correcta",
    CollectionStatus.ERROR: "recogida con error",
    CollectionStatus.PERMISSION_DENIED: "recogida sin permiso",
    CollectionStatus.NOT_SUPPORTED: "recogida no soportada",
}
NOT_RECORDED = "comprobaciones no registradas"
CHECKS_LABELS = {
    ChecksOutcome.RECORDED: "comprobaciones registradas",
    ChecksOutcome.UNCHANGED: "comprobaciones sin cambios respecto a las anteriores",
    ChecksOutcome.NOT_APPLICABLE: "este conector no tiene comprobaciones",
    ChecksOutcome.SKIPPED_SCOPE: f"{NOT_RECORDED}: el alcance no está aprobado",
    ChecksOutcome.HISTORY_UNREADABLE: f"{NOT_RECORDED}: el historial no se puede leer",
    ChecksOutcome.REJECTED: f"{NOT_RECORDED}: se rechazó el registro (revisa el reloj)",
    ChecksOutcome.ERROR: f"{NOT_RECORDED}: fallaron",
    ChecksOutcome.NOT_RUN: f"{NOT_RECORDED}: no se evaluaron",
}
_CHECKS_DONE = {ChecksOutcome.RECORDED, ChecksOutcome.UNCHANGED, ChecksOutcome.NOT_APPLICABLE}


@dataclass(frozen=True)
class ResultView:
    collection_label: str | None
    phase_label: str | None
    checks_label: str
    evidence_id: str | None  # solo con formato canónico
    evidence_invalid: bool  # había un identificador, pero no es canónico: no se muestra


@dataclass(frozen=True)
class RequestView:
    state: str  # valor de `State`
    label: str
    tone: Tone
    text: str
    requested_at: str | None
    alive: bool
    result: ResultView | None
    code: str | None = None  # causa fija de «No se pudo hacer»; sustituye al estado en la insignia


def index_requests(store: UiStore, now: datetime) -> Mapping[str, RequestStatus]:
    """La última petición de cada conector; un solo `store.list` por página."""
    return {item.connector: item for item in list_requests(store, now)}


def _result_view(result: CollectRequestResult) -> ResultView:
    evidence_id = result.evidence_id
    valid = evidence_id is not None and is_evidence_id(evidence_id)
    return ResultView(
        collection_label=(
            COLLECTION_LABELS.get(result.collection_status) if result.collection_status else None
        ),
        phase_label=PHASE_LABELS.get(result.phase.value) if result.phase else None,
        checks_label=CHECKS_LABELS[result.checks],
        evidence_id=evidence_id if valid else None,
        evidence_invalid=evidence_id is not None and not valid,
    )


def _done(result: CollectRequestResult | None) -> tuple[str, Tone, str]:
    """Con resultado, el detalle vive en `ResultView`: aquí no se repite en `text`."""
    if result is None or result.outcome is Outcome.ERROR:
        code = CODE_LABELS.get(result.code, NO_CODE) if result and result.code else NO_CODE
        return f"No se pudo hacer: {code}", "bad", ""
    if result.collection_status is not CollectionStatus.SUCCESS:
        return "Terminada con error de recogida", "bad", ""
    if result.checks not in _CHECKS_DONE:
        return "Terminada; comprobaciones no registradas", "warn", ""
    return "Terminada", "neutral", ""


def request_view(status: RequestStatus) -> RequestView:
    result = status.result
    requested_at = format_instant(status.request.requested_at) if status.request else None
    fixed: dict[State, tuple[str, Tone, str]] = {
        State.PENDING: ("Pendiente", "warn", PENDING_TEXT),
        State.RUNNING: ("En curso", "warn", RUNNING_TEXT),
        State.EXPIRED: ("Caducada", "neutral", EXPIRED_TEXT),
    }
    label, tone, text = fixed.get(status.state) or _done(result)
    failed = status.state is State.DONE and result is not None and result.outcome is Outcome.ERROR
    return RequestView(
        state=status.state.value,
        label=label,
        tone=tone,
        text=text,
        requested_at=requested_at,
        alive=status.state.alive,
        result=_result_view(result) if result and status.state is State.DONE else None,
        code=result.code.value if failed and result.code else None,
    )
