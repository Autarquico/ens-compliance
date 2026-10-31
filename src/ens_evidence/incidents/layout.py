"""Disposición del registro de incidentes en el workspace: claves, nombres y activos.

Es el módulo neutral del que dependen `store`, `verify` e `history`: ninguno depende de otro por
estos nombres.
"""

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import yaml

from ens_evidence.incidents.model import INCIDENT_ID_PATTERN, IncidentRecord
from ens_evidence.orgdocs.approval import APPROVALS_PREFIX, STAMP

INCIDENTS_PREFIX = "incidents"
PROCEDURE_DOCUMENT = "procedimiento-incidentes"
RECORD_NAME = re.compile(r"(\d{6})-(\d{8}T\d{12}Z)-([0-9a-f]{12})\.yaml")
RESERVATION_NAME = re.compile(r"seq/(\d{6})")
_ID_IN_KEY = re.compile(rf"^{INCIDENTS_PREFIX}/((?:INC|SIM)-\d{{4}}-\d{{3}})/")
ASSET_PREFIX = "incident:"


@dataclass(frozen=True)
class StoredRecord:
    key: str
    data: bytes
    record: IncidentRecord

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


def incident_prefix(incident_id: str) -> str:
    return f"{INCIDENTS_PREFIX}/{incident_id}"


def seq_key(incident_id: str, seq: int) -> str:
    return f"{incident_prefix(incident_id)}/seq/{seq:06d}"


def asset_id(incident_id: str) -> str:
    return f"{ASSET_PREFIX}{incident_id}"


def incident_of_asset(asset: str) -> str | None:
    """El id de incidente de un `asset_id`; None si no es un activo de incidente válido."""
    incident_id = asset.removeprefix(ASSET_PREFIX)
    if asset.startswith(ASSET_PREFIX) and re.fullmatch(INCIDENT_ID_PATTERN, incident_id):
        return incident_id
    return None


def instant_stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")


def render_record(payload: dict[str, Any]) -> bytes:
    """YAML canónico (claves ordenadas) del registro serializado."""
    return yaml.safe_dump(payload, sort_keys=True, allow_unicode=True).encode("utf-8")


def record_key(record: IncidentRecord, data: bytes) -> str:
    stamp = instant_stamp(record.recorded_at)
    sha12 = hashlib.sha256(data).hexdigest()[:12]
    return f"{incident_prefix(record.state.id)}/{record.seq:06d}-{stamp}-{sha12}.yaml"


def incident_ids_in(keys: list[str]) -> set[str]:
    return {m.group(1) for key in keys if (m := _ID_IN_KEY.match(key))}


def approval_key(approved_at: datetime) -> str:
    stamp = approved_at.astimezone(UTC).strftime(STAMP)
    return f"{APPROVALS_PREFIX}/{PROCEDURE_DOCUMENT}/{stamp}.yaml"
