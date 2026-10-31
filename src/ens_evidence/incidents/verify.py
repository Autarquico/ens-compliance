"""Verificación de un incidente: clave, secuencia, cadena, evidencias y referencias.

Todo lo que se informa es bloqueante: un incidente con algún problema no admite más registros y
no cuenta como cobertura de op.exp.9. La copia externa solo cubre las alteraciones coordinadas
que además borren los anclajes (reservas y manifiestos).
"""

from dataclasses import dataclass

import yaml
from pydantic import ValidationError

from ens_evidence.evidence.importer import INCIDENT_HISTORY_SOURCE, INCIDENT_RECORD_SOURCE
from ens_evidence.evidence.manifests import list_manifests
from ens_evidence.evidence.verify import verify_one
from ens_evidence.incidents.model import IncidentRecord, Procedure, RecordKind, check_successor
from ens_evidence.incidents.store import (
    INCIDENTS_PREFIX,
    PROCEDURE_DOCUMENT,
    RECORD_NAME,
    RESERVATION_NAME,
    StoredRecord,
    approval_key,
    asset_id,
    incident_ids_in,
    incident_prefix,
    record_key,
)
from ens_evidence.models.contracts import EvidenceManifest
from ens_evidence.orgdocs.approval import scan_approvals
from ens_evidence.scope.messages import describe_validation
from ens_evidence.workspace import WorkspaceStore
from ens_evidence.yaml_unique import safe_load_unique

_ASSET_PREFIX = "incident:"


@dataclass(frozen=True)
class IncidentScan:
    """`records` por número de orden (los ilegibles quedan fuera y figuran en `problems`).
    `evidence`: manifiesto `incident-record` de cada registro, por huella; `histories`: los
    manifiestos `incident-history` del incidente."""

    incident_id: str
    records: tuple[StoredRecord, ...]
    problems: tuple[str, ...]
    evidence: dict[str, EvidenceManifest]
    histories: tuple[EvidenceManifest, ...]

    @property
    def closed(self) -> bool:
        return any(s.record.kind is RecordKind.CLOSED for s in self.records)


def scan_incident(
    store: WorkspaceStore, incident_id: str, manifests: list[EvidenceManifest] | None = None
) -> IncidentScan:
    manifests = list_manifests(store).manifests if manifests is None else manifests
    problems: list[str] = []
    records, reserved = _read_keys(store, incident_id, problems)
    problems += _sequence_problems(records, reserved)
    problems += _link_problems(records)
    asset = asset_id(incident_id)
    mine = [m for m in manifests if m.asset_id == asset]
    evidence, evidence_problems = _match_evidence(store, records, mine)
    histories = tuple(m for m in mine if m.source == INCIDENT_HISTORY_SOURCE)
    problems += evidence_problems
    problems += _reference_problems(store, records)
    return IncidentScan(incident_id, tuple(records), tuple(problems), evidence, histories)


def verify_incident(store: WorkspaceStore, incident_id: str) -> list[str]:
    """Problemas del incidente, incluida la existencia del historial al cerrar."""
    scan = scan_incident(store, incident_id)
    return [*scan.problems, *history_presence_problems(scan)]


def verify_all_incidents(store: WorkspaceStore) -> dict[str, list[str]]:
    """Incidentes con problemas, por id. Incluye los que solo dejaron manifiestos."""
    manifests = list_manifests(store).manifests
    ids = incident_ids_in(store.list(INCIDENTS_PREFIX))
    ids |= {
        m.asset_id.removeprefix(_ASSET_PREFIX)
        for m in manifests
        if m.source == INCIDENT_RECORD_SOURCE and m.asset_id.startswith(_ASSET_PREFIX)
    }
    found = {}
    for incident_id in sorted(ids):
        scan = scan_incident(store, incident_id, manifests)
        problems = [*scan.problems, *history_presence_problems(scan)]
        if problems:
            found[incident_id] = problems
    return found


def history_presence_problems(scan: IncidentScan) -> list[str]:
    if scan.closed and not scan.histories:
        return [f"{scan.incident_id}: cerrado sin evidencia incident-history"]
    if not scan.closed and scan.histories:
        return [f"{scan.incident_id}: hay un incident-history pero el incidente no está cerrado"]
    if len(scan.histories) > 1:
        ids = ", ".join(m.id for m in scan.histories)
        return [f"{scan.incident_id}: más de un incident-history ({ids})"]
    return []


def _read_keys(
    store: WorkspaceStore, incident_id: str, problems: list[str]
) -> tuple[list[StoredRecord], set[int]]:
    prefix = incident_prefix(incident_id)
    records: list[StoredRecord] = []
    reserved: set[int] = set()
    for key in store.list(prefix):
        name = key.removeprefix(f"{prefix}/")
        if (match := RESERVATION_NAME.fullmatch(name)) is not None:
            reserved.add(int(match.group(1)))
        elif RECORD_NAME.fullmatch(name) is not None:
            stored = _read_record(store, incident_id, key, problems)
            if stored is not None:
                records.append(stored)
        else:
            problems.append(f"{key}: clave inesperada en el registro de incidentes")
    records.sort(key=lambda s: (s.record.seq, s.key))
    return records, reserved


def _read_record(
    store: WorkspaceStore, incident_id: str, key: str, problems: list[str]
) -> StoredRecord | None:
    try:
        data = store.read_bytes(key)
        raw = safe_load_unique(data.decode("utf-8"))
        record = IncidentRecord.model_validate(raw)
    except UnicodeDecodeError:
        problems.append(f"{key}: no es texto UTF-8")
    except yaml.YAMLError:
        problems.append(f"{key}: YAML no válido o con una clave repetida")
    except ValidationError as exc:
        problems += describe_validation(key, exc, raw)
    else:
        if record.state.id != incident_id:
            problems.append(f"{key}: el registro es de otro incidente")
            return None
        if key != record_key(record, data):
            problems.append(
                f"{key}: la clave no coincide con el contenido (número, instante o hash)"
            )
        return StoredRecord(key, data, record)
    return None


def _sequence_problems(records: list[StoredRecord], reserved: set[int]) -> list[str]:
    problems = []
    by_seq: dict[int, list[StoredRecord]] = {}
    for stored in records:
        by_seq.setdefault(stored.record.seq, []).append(stored)
    for seq, group in by_seq.items():
        if len(group) > 1:
            keys = ", ".join(s.key for s in group)
            problems.append(
                f"bifurcación: {len(group)} registros con el mismo número {seq}: {keys}"
            )
    last = max(by_seq, default=0)
    problems += [f"falta el registro {seq}" for seq in range(1, last + 1) if seq not in by_seq]
    problems += [
        f"reserva de la secuencia {seq} sin registro" for seq in sorted(reserved - set(by_seq))
    ]
    if records and records[0].record.kind is not RecordKind.OPENED:
        problems.append("el primer registro no es `opened`")
    return problems


def _link_problems(records: list[StoredRecord]) -> list[str]:
    """Cadena y reglas de sucesión entre registros contiguos y sin bifurcación."""
    counts: dict[int, int] = {}
    for stored in records:
        counts[stored.record.seq] = counts.get(stored.record.seq, 0) + 1
    linear = [s for s in records if counts[s.record.seq] == 1]
    problems = []
    for before, after in zip(linear, linear[1:], strict=False):
        if after.record.seq != before.record.seq + 1:
            continue
        pair = f"registros {before.record.seq} y {after.record.seq}"
        if after.record.previous_sha256 != before.sha256:
            problems.append(f"{pair}: la cadena previous_sha256 está rota")
        problems += [f"{pair}: {error}" for error in check_successor(before.record, after.record)]
    return problems


def _match_evidence(
    store: WorkspaceStore, records: list[StoredRecord], mine: list[EvidenceManifest]
) -> tuple[dict[str, EvidenceManifest], list[str]]:
    by_sha: dict[str, list[EvidenceManifest]] = {}
    for manifest in mine:
        if manifest.source == INCIDENT_RECORD_SOURCE:
            by_sha.setdefault(manifest.artifact.sha256, []).append(manifest)
    problems: list[str] = []
    matched: dict[str, EvidenceManifest] = {}
    for stored in records:
        found = by_sha.get(stored.sha256, [])
        if not found:
            problems.append(f"{stored.key}: falta el manifiesto incident-record")
            continue
        if len(found) > 1:
            problems.append(f"{stored.key}: más de un manifiesto incident-record")
        manifest = found[0]
        matched[stored.sha256] = manifest
        problems += verify_one(store, manifest.id).problems
        if manifest.collected_at != stored.record.recorded_at:
            problems.append(f"{manifest.id}: collected_at no coincide con recorded_at del registro")
    known = {s.sha256 for s in records}
    problems += [
        f"{manifest.id}: manifiesto incident-record sin registro"
        for sha, found in by_sha.items()
        if sha not in known
        for manifest in found
    ]
    return matched, problems


def _reference_problems(store: WorkspaceStore, records: list[StoredRecord]) -> list[str]:
    problems = []
    cited: dict[str, None] = {}
    for stored in records:
        state = stored.record.state
        cited.update(dict.fromkeys(state.evidence_ids))
        if state.closure:
            cited.update(dict.fromkeys(state.closure.preserved_evidence_ids))
    for evidence_id in cited:
        problems += [f"referencia EV rota: {p}" for p in verify_one(store, evidence_id).problems]
    unique = {
        s.record.state.procedure.model_dump_json(): s.record.state.procedure
        for s in records
        if s.record.state.procedure
    }
    for procedure in unique.values():
        problems += _procedure_problems(store, procedure)
    return problems


def _procedure_problems(store: WorkspaceStore, procedure: Procedure) -> list[str]:
    if procedure.name != PROCEDURE_DOCUMENT:
        return [f"el procedimiento citado no es {PROCEDURE_DOCUMENT}"]
    approvals, _ = scan_approvals(store, PROCEDURE_DOCUMENT)
    if any(
        (approval.approved_at, approval.document_sha256, approval_key(approval.approved_at))
        == (procedure.approved_at, procedure.document_sha256, procedure.approval_key)
        for approval in approvals
    ):
        return []
    return [f"la aprobación citada ({procedure.approval_key}) no está en el historial de org-docs"]
