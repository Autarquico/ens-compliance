"""Vista del agente osquery de cada equipo: lo que consta de él, ya verificado, sin evaluarlo.

Entran solo los resultados programados y los contactos cuya identidad coincide con la
inscripción actual (sha256 completo del uuid normalizado). Quedan fuera las respuestas a
consultas en vivo (`agent.query_run_id`) y la evidencia sin `identity_sha256` (anterior a la
identidad verificable): nunca alimentan un resultado. Un cambio solo de nombre del equipo no se
detecta aquí; lo exige el alta (`enrolled_asset`, uuid y nombre).
"""

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from ens_evidence.agent.node_key import normalize_host_identifier
from ens_evidence.endpoint.config import HostEnrollment
from ens_evidence.endpoint.evidence import AGENT_SOURCE, CONTACT_SOURCE, HostEvidence

WINDOWS = "windows"
PLATFORMS = frozenset({"darwin", "linux", WINDOWS})


@dataclass(frozen=True)
class Admission:
    """Alta no revocada del agente en el registro; `generation` es la del node_key vigente."""

    admitted_at: datetime | None
    generation: int


@dataclass(frozen=True)
class QuerySnapshot:
    query_id: str
    data: dict[str, Any]
    collected_at: datetime
    evidence_id: str
    valid_until: datetime | None

    def expires_at(self, max_evidence_age_days: int) -> datetime:
        """Lo primero entre la edad máxima de la consulta y el vencimiento del manifiesto."""
        by_age = self.collected_at + timedelta(days=max_evidence_age_days)
        return by_age if self.valid_until is None else min(self.valid_until, by_age)


@dataclass(frozen=True)
class Contact:
    evidence_id: str
    collected_at: datetime
    generation: int


@dataclass(frozen=True)
class AgentView:
    snapshots: dict[str, QuerySnapshot] = field(default_factory=dict)
    contacts: list[Contact] = field(default_factory=list)
    platform: str | None = None


def identity_digest(hardware_uuid: str) -> str | None:
    """sha256 del uuid normalizado, o `None` si el uuid inscrito no tiene forma de uuid."""
    normalized = normalize_host_identifier(hardware_uuid)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest() if normalized else None


def build_views(
    evidence: Iterable[HostEvidence], hosts: Mapping[str, HostEnrollment]
) -> dict[str, AgentView]:
    """Vista de cada activo inscrito: el último snapshot por consulta y los contactos válidos."""
    digests = {asset_id: identity_digest(host.hardware_uuid) for asset_id, host in hosts.items()}
    ordered = sorted(evidence, key=lambda item: (item.collected_at, item.evidence_id))
    snapshots: dict[str, dict[str, QuerySnapshot]] = {}
    contacts: dict[str, list[Contact]] = {}
    platforms: dict[str, str] = {}
    for item in ordered:
        digest = digests.get(item.asset_id)
        agent = _dict(item.result.data.get("agent"))
        if digest is None or agent.get("identity_sha256") != digest:
            continue
        if item.result.source == CONTACT_SOURCE:
            generation = agent.get("generation")
            if isinstance(generation, int) and not isinstance(generation, bool):
                contacts.setdefault(item.asset_id, []).append(
                    Contact(item.evidence_id, item.collected_at, generation)
                )
        elif item.result.source == AGENT_SOURCE and "query_run_id" not in agent:
            for snapshot in _snapshots(item):
                per_query = snapshots.setdefault(item.asset_id, {})
                current = per_query.get(snapshot.query_id)
                if current is None or _order(snapshot) >= _order(current):
                    per_query[snapshot.query_id] = snapshot
        else:
            continue
        if agent.get("platform") in PLATFORMS:
            platforms[item.asset_id] = agent["platform"]
    return {
        asset_id: AgentView(
            snapshots.get(asset_id, {}), contacts.get(asset_id, []), platforms.get(asset_id)
        )
        for asset_id in digests
        if asset_id in snapshots or asset_id in contacts
    }


def _order(snapshot: QuerySnapshot) -> tuple[datetime, str]:
    return snapshot.collected_at, snapshot.evidence_id


def _snapshots(item: HostEvidence) -> list[QuerySnapshot]:
    found = []
    for query_id, query in _dict(item.result.data.get("queries")).items():
        moment = _instant(_dict(query).get("collected_at"))
        if moment is not None:
            found.append(QuerySnapshot(query_id, query, moment, item.evidence_id, item.valid_until))
    return found


def _instant(raw: Any) -> datetime | None:
    try:
        moment = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo is not None else None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
