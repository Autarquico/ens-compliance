"""Evidencia `incident-history`: la lista exacta de registros de un incidente cerrado.

Se genera al cerrar, en JSON canónico, y es lo único que la matriz admite como fuente de
op.exp.9. `verify_history` la contrasta en vivo con los registros reales: omitidos, extra,
sustituidos o un incidente roto la invalidan.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from pydantic import Field, ValidationError

from ens_evidence.evidence.importer import INCIDENT_HISTORY_SOURCE, IdFactory, import_generated
from ens_evidence.evidence.layout import new_evidence_id
from ens_evidence.evidence.verify import verify_one
from ens_evidence.incidents.errors import IncidentRejected
from ens_evidence.incidents.layout import StoredRecord, asset_id, incident_of_asset
from ens_evidence.incidents.model import SCHEMA_VERSION, IncidentStatus, RecordKind
from ens_evidence.incidents.privacy import check_declared_name
from ens_evidence.incidents.verify import IncidentScan, history_presence_problems, scan_incident
from ens_evidence.models.contracts import SHA256_PATTERN, EvidenceManifest, Strict
from ens_evidence.workspace import NotFound, NotText, WorkspaceStore


class HistoryEntry(Strict):
    seq: int = Field(ge=1)
    key: str
    sha256: str = Field(pattern=SHA256_PATTERN)
    evidence_id: str


class IncidentHistory(Strict):
    schema_version: str = SCHEMA_VERSION
    incident_id: str
    simulated: bool
    final_status: IncidentStatus
    records: Annotated[list[HistoryEntry], Field(min_length=1)]


def render_history(history: IncidentHistory) -> bytes:
    document = history.model_dump(mode="json")
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def build_history(scan: IncidentScan) -> IncidentHistory:
    """Historial esperado de los registros reales; `evidence_id` vacío si falta el manifiesto."""
    last = scan.records[-1].record.state
    return IncidentHistory(
        incident_id=scan.incident_id,
        simulated=last.simulated,
        final_status=last.status,
        records=[_entry(scan, stored) for stored in scan.records],
    )


def _entry(scan: IncidentScan, stored: StoredRecord) -> HistoryEntry:
    manifest = scan.evidence.get(stored.sha256)
    return HistoryEntry(
        seq=stored.record.seq,
        key=stored.key,
        sha256=stored.sha256,
        evidence_id=manifest.id if manifest else "",
    )


def record_history(
    store: WorkspaceStore,
    incident_id: str,
    *,
    by: str,
    now: datetime,
    id_factory: IdFactory = new_evidence_id,
) -> EvidenceManifest:
    """Genera la evidencia `incident-history` de un incidente cerrado y sin problemas.

    `collected_at` es el `recorded_at` del cierre y no lleva `valid_until`. Si el cierre quedó
    escrito sin historial, `incident history` lo relanza: rechaza si ya existe uno."""
    if name_errors := check_declared_name(by):
        raise IncidentRejected(name_errors)
    scan = scan_incident(store, incident_id)
    if scan.problems:
        raise IncidentRejected([f"{incident_id} está roto", *scan.problems])
    if not scan.records or scan.records[-1].record.kind is not RecordKind.CLOSED:
        raise IncidentRejected([f"{incident_id} no está cerrado"])
    if scan.histories:
        raise IncidentRejected([f"{incident_id} ya tiene historial ({scan.histories[0].id})"])
    return import_generated(
        store,
        render_history(build_history(scan)),
        source=INCIDENT_HISTORY_SOURCE,
        asset_id=asset_id(incident_id),
        submitted_by=by,
        now=now,
        collected_at=scan.records[-1].record.recorded_at,
        id_factory=id_factory,
    )


@dataclass(frozen=True)
class HistoryCheck:
    """`problems` vacío si el historial es válido; `simulated`: es el de un simulacro."""

    problems: list[str]
    simulated: bool


def verify_history(store: WorkspaceStore, manifest: EvidenceManifest) -> list[str]:
    return check_history(store, manifest).problems


def check_history(store: WorkspaceStore, manifest: EvidenceManifest) -> HistoryCheck:
    """Problemas del historial: vacío si coincide exactamente con todos los registros de un
    incidente cerrado, con un único cierre y sin problemas. Lee el historial una sola vez."""
    if manifest.source != INCIDENT_HISTORY_SOURCE:
        return HistoryCheck(
            [f"{manifest.id}: no es una evidencia {INCIDENT_HISTORY_SOURCE}"], False
        )
    incident_id = incident_of_asset(manifest.asset_id)
    if incident_id is None:
        return HistoryCheck([f"{manifest.id}: el activo no es un incidente válido"], False)
    problems = list(verify_one(store, manifest.id).problems)
    history = _read_history(store, manifest, problems)
    scan = scan_incident(store, incident_id)
    problems += [f"{incident_id}: {problem}" for problem in scan.problems]
    problems += history_presence_problems(scan)
    if manifest.valid_until is not None:
        problems.append(f"{manifest.id}: un historial de incidente no caduca")
    closing = [s for s in scan.records if s.record.kind is RecordKind.CLOSED]
    if len(closing) != 1 or scan.records[-1] is not closing[0]:
        problems.append(f"{incident_id}: debe haber un único cierre, y ser el último registro")
    elif manifest.collected_at != closing[0].record.recorded_at:
        problems.append(f"{manifest.id}: collected_at no es el recorded_at del cierre")
    if history is not None and scan.records:
        problems += _compare(history, build_history(scan), incident_id)
    return HistoryCheck(problems, history is not None and history.simulated)


def _read_history(
    store: WorkspaceStore, manifest: EvidenceManifest, problems: list[str]
) -> IncidentHistory | None:
    try:
        data = store.read_bytes(manifest.artifact.key)
        history = IncidentHistory.model_validate(json.loads(data))
    except (NotFound, NotText):
        return None  # ya informado por verify_one
    except (ValueError, ValidationError):
        problems.append(f"{manifest.id}: el historial no es JSON válido de incident-history")
        return None
    if render_history(history) != data:
        problems.append(f"{manifest.id}: el historial no está en JSON canónico")
    return history


def _compare(actual: IncidentHistory, expected: IncidentHistory, incident_id: str) -> list[str]:
    problems = []
    if actual.incident_id != incident_id:
        problems.append(f"el historial es de otro incidente ({actual.incident_id})")
    if actual.simulated != expected.simulated:
        problems.append("`simulated` del historial no coincide con el incidente")
    if actual.final_status != expected.final_status:
        problems.append("el estado final del historial no coincide con el incidente")
    wanted = {e.seq: e for e in expected.records}
    listed = {e.seq: e for e in actual.records}
    if len(listed) != len(actual.records):
        problems.append("el historial repite algún número de registro")
    problems += [f"el historial omite el registro {seq}" for seq in sorted(wanted.keys() - listed)]
    problems += [
        f"el historial incluye el registro {seq}, que no existe"
        for seq in sorted(listed.keys() - wanted)
    ]
    problems += [
        f"el registro {seq} del historial no coincide con el real"
        for seq in sorted(wanted.keys() & listed.keys())
        if wanted[seq] != listed[seq]
    ]
    return problems
