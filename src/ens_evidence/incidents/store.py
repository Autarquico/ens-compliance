"""Registro de incidentes de solo adición: `incidents/<ID>/<seq>-<instante>-<sha12>.yaml`.

Cada orden añade una fotografía completa del incidente. Orden de escritura, que fija qué deja un
fallo a medias:

1. validar todo (modelo, sucesión, privacidad, severidad, procedimiento, EV): un rechazo no escribe;
2. reservar la secuencia `incidents/<ID>/seq/<seq>` con creación exclusiva: si existe, otra orden
   ganó y esta falla;
3. importar los mismos bytes como evidencia `incident-record`;
4. escribir el registro;
5. si el registro es el cierre, generar el historial. Si eso falla, el cierre ya está escrito y es
   definitivo: no se rechaza, se informa en `Recorded.history_problems` y `incident history` lo
   completa sin tocar los registros.

Un fallo entre 2 y 4 deja una reserva (y quizá un manifiesto) sin registro: `verify` lo marca roto
y el incidente no admite más registros hasta que se recupere desde la copia (runbook).
"""

import hashlib
import re
from dataclasses import dataclass, field, replace
from datetime import datetime

from pydantic import ValidationError

from ens_evidence.evidence.importer import INCIDENT_RECORD_SOURCE, import_generated
from ens_evidence.evidence.verify import verify_one
from ens_evidence.incidents.errors import IncidentRejected
from ens_evidence.incidents.history import record_history
from ens_evidence.incidents.layout import (
    INCIDENTS_PREFIX,
    PROCEDURE_DOCUMENT,
    StoredRecord,
    approval_key,
    asset_id,
    incident_ids_in,
    record_key,
    render_record,
    seq_key,
)
from ens_evidence.incidents.model import (
    IncidentRecord,
    IncidentState,
    Procedure,
    RecordKind,
    check_successor,
)
from ens_evidence.incidents.privacy import check_declared_name, check_incident_payload
from ens_evidence.incidents.verify import history_presence_problems, scan_incident
from ens_evidence.models.contracts import EvidenceManifest
from ens_evidence.orgdocs.answers import read_answers, squash, validate_answers
from ens_evidence.orgdocs.approval import document_status
from ens_evidence.orgdocs.contract import INCIDENT_LEVEL_FIELDS, INCIDENT_LEVELS
from ens_evidence.orgdocs.template import load_templates
from ens_evidence.scope.inventory import load_assets
from ens_evidence.scope.messages import describe_validation
from ens_evidence.workspace import AlreadyExists, WorkspaceStore

_RESERVED = b"reservada\n"
_LEVEL = INCIDENT_LEVEL_FIELDS[0]


@dataclass(frozen=True)
class Recorded:
    """Lo escrito por una orden. Si `history_problems` no está vacío, el cierre quedó registrado
    pero el historial no se generó: `incident history` lo completa."""

    key: str
    record: IncidentRecord
    sha256: str
    evidence_id: str
    history_evidence_id: str | None = None
    history_problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def next_incident_id(store: WorkspaceStore, *, simulated: bool, year: int) -> str:
    """Siguiente `INC-AAAA-NNN` (o `SIM-`) libre. Dos altas simultáneas chocan en la reserva."""
    label = "SIM" if simulated else "INC"
    taken = re.compile(rf"^{label}-{year}-(\d{{3}})$")
    numbers = [
        int(m.group(1))
        for incident_id in incident_ids_in(store.list(INCIDENTS_PREFIX))
        if (m := taken.match(incident_id))
    ]
    number = max(numbers, default=0) + 1
    if number > 999:
        raise IncidentRejected([f"no quedan identificadores {label}-{year}-NNN libres"])
    return f"{label}-{year}-{number:03d}"


def declared_levels(store: WorkspaceStore) -> list[str]:
    """Niveles de clasificación declarados en org-docs; vacío si no hay una declaración válida."""
    try:
        report = validate_answers(read_answers(store), load_templates())
    except ValueError:
        return []
    if any(problem.startswith(f"{INCIDENT_LEVELS}:") for problem in report.problems):
        return []
    rows = report.values.get(INCIDENT_LEVELS)
    if not isinstance(rows, list):
        return []
    return [row[_LEVEL] for row in rows if isinstance(row, dict) and row.get(_LEVEL)]


def current_procedure(store: WorkspaceStore) -> Procedure | None:
    """Aprobación vigente de `procedimiento-incidentes`, tal como la guarda un incidente."""
    status = document_status(store, PROCEDURE_DOCUMENT)
    approval = status.approval
    if not status.current or approval is None:
        return None
    return Procedure(
        name=PROCEDURE_DOCUMENT,
        document_sha256=approval.document_sha256,
        approved_at=approval.approved_at,
        approval_key=approval_key(approval.approved_at),
    )


def simulation_procedure(store: WorkspaceStore) -> Procedure:
    """El procedimiento que exige un simulacro: la aprobación vigente, o `IncidentRejected`."""
    procedure = current_procedure(store)
    if procedure is None:
        raise IncidentRejected(
            [f"procedure: un simulacro exige {PROCEDURE_DOCUMENT} aprobado y vigente"]
        )
    return procedure


def record_incident(
    store: WorkspaceStore, state: IncidentState, *, kind: RecordKind, by: str, now: datetime
) -> Recorded:
    """Añade un registro al incidente. `IncidentRejected` si no se escribe nada o el incidente
    está roto; `now` es el `recorded_at` y el instante de la evidencia."""
    if name_errors := check_declared_name(by):
        raise IncidentRejected(name_errors)
    scan = scan_incident(store, state.id)
    if scan.problems:
        raise IncidentRejected([f"{state.id} está roto y no admite más registros", *scan.problems])
    previous = scan.records[-1] if scan.records else None
    if (previous is None) != (kind is RecordKind.OPENED):
        raise IncidentRejected(
            [f"{state.id} ya existe" if previous else f"{state.id} no existe; ábrelo primero"]
        )
    if previous and previous.record.kind is RecordKind.CLOSED:
        raise IncidentRejected([_closed_message(state.id, scan.histories)])
    if presence := history_presence_problems(scan):
        raise IncidentRejected([f"{state.id} no admite más registros", *presence])
    errors: list[str] = []
    warnings: list[str] = []
    state = _with_procedure(store, state, previous is None, errors, warnings)
    state = _with_severity(store, state, previous, errors, warnings)
    try:
        record = IncidentRecord(
            seq=previous.record.seq + 1 if previous else 1,
            kind=kind,
            recorded_at=now,
            previous_sha256=previous.sha256 if previous else None,
            state=state,
        )
    except ValidationError as exc:
        raise IncidentRejected([*errors, *describe_validation("incidente", exc)]) from None
    if previous:
        errors += check_successor(previous.record, record)
    payload = record.model_dump(mode="json")
    found, notices = check_incident_payload(payload)
    errors += found
    warnings += notices
    errors += _broken_evidence(store, state)
    errors += _unknown_assets(store, state, previous)
    if errors:
        raise IncidentRejected(errors)

    data = render_record(payload)
    try:
        store.create_bytes(seq_key(state.id, record.seq), _RESERVED)
    except AlreadyExists:
        raise IncidentRejected(
            [f"{state.id}: otra orden reservó antes la secuencia {record.seq}; revisa y reintenta"]
        ) from None
    manifest = import_generated(
        store,
        data,
        source=INCIDENT_RECORD_SOURCE,
        asset_id=asset_id(state.id),
        submitted_by=by,
        now=now,
        collected_at=now,
    )
    key = record_key(record, data)
    store.create_bytes(key, data)
    recorded = Recorded(
        key, record, hashlib.sha256(data).hexdigest(), manifest.id, warnings=warnings
    )
    if kind is not RecordKind.CLOSED:
        return recorded
    try:
        history = record_history(store, state.id, by=by, now=now)
    except IncidentRejected as exc:
        return replace(recorded, history_problems=exc.errors)
    return replace(recorded, history_evidence_id=history.id)


def _closed_message(incident_id: str, histories: tuple[EvidenceManifest, ...]) -> str:
    message = f"{incident_id} está cerrado y no admite más registros"
    if not histories:
        message += f"; su historial falta: ens-evidence incident history {incident_id}"
    return message


def _with_procedure(
    store: WorkspaceStore,
    state: IncidentState,
    opening: bool,
    errors: list[str],
    warnings: list[str],
) -> IncidentState:
    """Al abrir, el procedimiento es la aprobación vigente de org-docs; después es inmutable."""
    if not opening:
        return state
    current = current_procedure(store)
    if state.procedure is None:
        if current is None:
            warnings.append(
                f"no hay una aprobación vigente de {PROCEDURE_DOCUMENT}: el incidente se registra "
                "sin procedimiento"
            )
            return state
        return state.model_copy(update={"procedure": current})
    if current is None:
        errors.append(f"procedure: {PROCEDURE_DOCUMENT} no está aprobado y vigente")
    elif state.procedure != current:
        errors.append("procedure: no es la aprobación vigente de org-docs")
    return state


def _with_severity(
    store: WorkspaceStore,
    state: IncidentState,
    previous: StoredRecord | None,
    errors: list[str],
    warnings: list[str],
) -> IncidentState:
    """La severidad nueva debe ser un nivel declarado; se guarda con el nombre declarado."""
    before = previous.record.state.severity if previous else None
    if state.severity == before:
        if state.severity is None and previous is None and not declared_levels(store):
            warnings.append(
                f"no hay niveles declarados ({INCIDENT_LEVELS}): se registra con severity: null"
            )
        return state
    levels = declared_levels(store)
    if not levels:
        errors.append(f"severity: no hay niveles declarados ({INCIDENT_LEVELS}); omítela")
        return state
    declared = {squash(level): level for level in levels}
    match = declared.get(squash(state.severity or ""))
    if match is None:
        errors.append(f"severity: no es un nivel declarado ({', '.join(levels)})")
        return state
    return state.model_copy(update={"severity": match})


def _unknown_assets(
    store: WorkspaceStore, state: IncidentState, previous: StoredRecord | None
) -> list[str]:
    """Los activos que se añaden deben existir en el inventario; para registrar sin activo,
    basta con omitir `--asset`. Se identifican por posición, nunca por valor."""
    before = set(previous.record.state.assets if previous else ())
    added = [(i, asset) for i, asset in enumerate(state.assets) if asset not in before]
    if not added:
        return []
    inventory, _ = load_assets(store)
    if inventory is None:
        return ["assets: no se puede leer el inventario (revisa `scope status`); omite --asset"]
    known = {asset.id for asset in inventory.assets}
    return [
        f"state.assets[{i}]: no está en el inventario" for i, asset in added if asset not in known
    ]


def _broken_evidence(store: WorkspaceStore, state: IncidentState) -> list[str]:
    cited = [*state.evidence_ids]
    if state.closure:
        cited += state.closure.preserved_evidence_ids
    return [
        f"evidencia citada rota: {problem}"
        for evidence_id in dict.fromkeys(cited)
        for problem in verify_one(store, evidence_id).problems
    ]
