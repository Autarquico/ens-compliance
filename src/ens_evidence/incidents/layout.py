"""Disposición del registro de incidentes en el workspace: claves, nombres y activos.

Es el módulo neutral del que dependen `store`, `verify` e `history`: ninguno depende de otro por
estos nombres.
"""

import re
from datetime import UTC, datetime

from ens_evidence import chain
from ens_evidence.chain import Stored
from ens_evidence.incidents.model import INCIDENT_ID_PATTERN, IncidentRecord
from ens_evidence.orgdocs.approval import APPROVALS_PREFIX, STAMP

INCIDENTS_PREFIX = "incidents"
PROCEDURE_DOCUMENT = "procedimiento-incidentes"
_ID_IN_KEY = re.compile(rf"^{INCIDENTS_PREFIX}/((?:INC|SIM)-\d{{4}}-\d{{3}})/")
ASSET_PREFIX = "incident:"

StoredRecord = Stored[IncidentRecord]


def incident_prefix(incident_id: str) -> str:
    return f"{INCIDENTS_PREFIX}/{incident_id}"


def seq_key(incident_id: str, seq: int) -> str:
    return chain.seq_key(incident_prefix(incident_id), seq)


def asset_id(incident_id: str) -> str:
    return f"{ASSET_PREFIX}{incident_id}"


def incident_of_asset(asset: str) -> str | None:
    """El id de incidente de un `asset_id`; None si no es un activo de incidente válido."""
    incident_id = asset.removeprefix(ASSET_PREFIX)
    if asset.startswith(ASSET_PREFIX) and re.fullmatch(INCIDENT_ID_PATTERN, incident_id):
        return incident_id
    return None


def record_key(record: IncidentRecord, data: bytes) -> str:
    return chain.record_key(incident_prefix(record.state.id), record.seq, record.recorded_at, data)


def incident_ids_in(keys: list[str]) -> set[str]:
    return {m.group(1) for key in keys if (m := _ID_IN_KEY.match(key))}


def approval_key(approved_at: datetime) -> str:
    stamp = approved_at.astimezone(UTC).strftime(STAMP)
    return f"{APPROVALS_PREFIX}/{PROCEDURE_DOCUMENT}/{stamp}.yaml"
