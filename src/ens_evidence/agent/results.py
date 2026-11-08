"""De lo que envía osquery a evidencia: solo consultas del paquete, columnas de la lista blanca.

Los resultados tienen la forma de `queries.{id}` de `endpoint-script` más un bloque `agent`.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ens_evidence import __version__
from ens_evidence.agent.osquery_config import pack_queries
from ens_evidence.endpoint.normalize import MAX_STORED_CHARS, STORED_COLUMNS
from ens_evidence.endpoint.pack import pack_sha256
from ens_evidence.endpoint.package import CLOCK_SKEW, parse_rows
from ens_evidence.evidence.importer import canonical_json, import_collected
from ens_evidence.evidence.layout import manifest_key, new_evidence_id, sha256_hex
from ens_evidence.evidence.manifests import ManifestError, load_manifest
from ens_evidence.http.logging import host_hash
from ens_evidence.models.contracts import CollectionResult
from ens_evidence.models.enums import CollectionStatus
from ens_evidence.workspace import WorkspaceStore

SOURCE = "osquery-agent"


@dataclass(frozen=True)
class Snapshot:
    query_id: str
    rows: list[dict[str, str]]
    collected_at: datetime


def snapshots_from_events(
    events: list[Any], *, host_identifier: str, platform: str | None, now: datetime
) -> list[Snapshot]:
    """Snapshots válidos del lote: el más reciente por consulta; el resto se ignora sin error."""
    queries = pack_queries()
    latest: dict[str, Snapshot] = {}
    for event in events:
        snapshot = _snapshot(event, queries, host_identifier, platform, now)
        if snapshot and (
            snapshot.query_id not in latest
            or snapshot.collected_at > latest[snapshot.query_id].collected_at
        ):
            latest[snapshot.query_id] = snapshot
    return list(latest.values())


def scheduled_result(
    asset_id: str, host_identifier: str, snapshots: list[Snapshot]
) -> CollectionResult:
    """`collected_at` es el del snapshot más antiguo: nunca aparenta ser más fresco de lo que es."""
    return CollectionResult(
        source=SOURCE,
        collector=SOURCE,
        collected_at=min(snapshot.collected_at for snapshot in snapshots),
        asset_id=asset_id,
        collection_status=CollectionStatus.SUCCESS,
        data={
            "queries": {
                s.query_id: _query_data(
                    CollectionStatus.SUCCESS, s.query_id, s.rows, s.collected_at
                )
                for s in snapshots
            },
            "agent": _agent(host_identifier),
        },
    )


def live_result(
    asset_id: str,
    host_identifier: str,
    *,
    run_id: str,
    query_id: str,
    collected_at: datetime,
    status: int,
    raw_rows: Any,
) -> CollectionResult:
    """Respuesta a una consulta en vivo; un estado distinto de 0 queda como ERROR, sin filas."""
    rows = _clean_rows(raw_rows) if status == 0 else None
    outcome = CollectionStatus.SUCCESS if rows is not None else CollectionStatus.ERROR
    return CollectionResult(
        source=SOURCE,
        collector=SOURCE,
        collected_at=collected_at,
        asset_id=asset_id,
        collection_status=outcome,
        data={
            "queries": {query_id: _query_data(outcome, query_id, rows or [], collected_at)},
            "agent": {**_agent(host_identifier), "query_run_id": run_id, "osquery_status": status},
        },
    )


def write_result(store: WorkspaceStore, result: CollectionResult, *, now: datetime) -> str:
    """Escribe la evidencia y devuelve su id. Idempotente: el mismo contenido da el mismo id."""
    sha256 = sha256_hex(canonical_json(result))
    evidence_id = f"EV-{result.collected_at.astimezone(UTC):%Y%m%d}-{sha256[:8]}"
    if _already_stored(store, evidence_id, sha256):
        return evidence_id
    proposals = iter([evidence_id])
    manifest = import_collected(
        store,
        result,
        collector_version=__version__,
        now=now,
        id_factory=lambda when: next(proposals, None) or new_evidence_id(when),
    )
    return manifest.id


def _already_stored(store: WorkspaceStore, evidence_id: str, sha256: str) -> bool:
    key = manifest_key(evidence_id)
    if not store.exists(key):
        return False
    try:
        return load_manifest(store, key).artifact.sha256 == sha256
    except ManifestError:
        return False


def _snapshot(
    event: Any,
    queries: dict[str, Any],
    host_identifier: str,
    platform: str | None,
    now: datetime,
) -> Snapshot | None:
    if not isinstance(event, dict):
        return None
    query = queries.get(event.get("name")) if isinstance(event.get("name"), str) else None
    if query is None or not _same_host(event.get("hostIdentifier"), host_identifier):
        return None
    if platform is not None and platform not in query.platforms:
        return None
    collected_at = _event_time(event.get("unixTime"))
    if collected_at is None or collected_at > now + CLOCK_SKEW:
        return None
    rows = _clean_rows(event.get("snapshot"))
    return None if rows is None else Snapshot(query.id, rows, collected_at)


def _same_host(reported: Any, expected: str) -> bool:
    return isinstance(reported, str) and reported.strip().lower() == expected


def _event_time(raw: Any) -> datetime | None:
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    try:
        return datetime.fromtimestamp(raw, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _clean_rows(raw_rows: Any) -> list[dict[str, str]] | None:
    """Filas válidas según los límites del paquete, o `None` si no se pueden leer."""
    if not isinstance(raw_rows, list):
        return None
    try:
        rows = parse_rows(json.dumps(raw_rows).encode())
    except (TypeError, ValueError):
        return None
    if rows is None:
        return None
    return rows


def _query_data(
    status: CollectionStatus, query_id: str, rows: list[dict[str, str]], collected_at: datetime
) -> dict[str, Any]:
    columns = STORED_COLUMNS[query_id]
    stored = [{c: row[c][:MAX_STORED_CHARS] for c in columns if c in row} for row in rows]
    return {
        "status": status,
        "exit_code": 0 if status is CollectionStatus.SUCCESS else 1,
        "permission_hint": False,
        "unsupported_hint": False,
        "row_count": len(rows),
        "rows": stored if columns else [],
        "collected_at": collected_at.isoformat(),
    }


def _agent(host_identifier: str) -> dict[str, str]:
    return {"host_identifier": host_hash(host_identifier), "pack_sha256": pack_sha256()}
