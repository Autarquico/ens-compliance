"""Datos mínimos para evaluar HOST-001…007 sin pasar por el script ni la importación."""

from datetime import UTC, datetime, timedelta
from typing import Any

from ens_evidence.endpoint.checks import evaluate_host_checks
from ens_evidence.endpoint.config import EndpointConfig, HostEnrollment, SupportedOs
from ens_evidence.endpoint.evidence import HostEvidence
from ens_evidence.models.contracts import CheckResult, CollectionResult
from ens_evidence.models.enums import CollectionStatus
from ens_evidence.scope.models import Asset, AssetType

COLLECTED = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)
VALID_UNTIL = COLLECTED + timedelta(days=30)
AT = COLLECTED + timedelta(days=5)
LAPTOP = "lap-ana"
SERVER = "srv-app"

DARWIN_ROWS: dict[str, list[dict[str, str]]] = {
    "os_version": [{"name": "macOS", "platform": "darwin", "version": "15.1"}],
    "osquery_info": [{"version": "5.14.1"}],
    "root_mount": [{"device": "/dev/disk3s1s1", "path": "/"}],
    "disk_encryption_darwin": [
        {"name": "/dev/disk3s1", "encrypted": "1", "filevault_status": "on"}
    ],
    "alf": [{"global_state": "1"}],
    "software_update_policy": [
        {
            "name": "AutomaticallyInstallMacOSUpdates",
            "value": "1",
            "user_scoped": "0",
            "manual": "0",
        }
    ],
    "apps": [{"name": "Safari.app", "bundle_short_version": "18"}],
}

LINUX_ROWS: dict[str, list[dict[str, str]]] = {
    "os_version": [{"name": "Debian GNU/Linux", "platform": "debian", "version": "12.5"}],
    "osquery_info": [{"version": "5.14.1"}],
    "root_mount": [{"device": "/dev/mapper/vg-root", "path": "/"}],
    "disk_encryption": [{"name": "/dev/mapper/vg-root", "encrypted": "1"}],
    "deb_packages": [{"name": "openssl", "version": "3.0", "status": "install ok installed"}],
    "rpm_packages": [],
}


def host_evidence(
    platform: str = "darwin",
    *,
    rows: dict[str, list[dict[str, str]]] | None = None,
    statuses: dict[str, CollectionStatus] | None = None,
    asset_id: str | None = None,
    evidence_id: str = "ev-1",
    collected_at: datetime = COLLECTED,
    valid_until: datetime = VALID_UNTIL,
    identity: tuple[str, str] | None = None,
) -> HostEvidence:
    defaults = DARWIN_ROWS if platform == "darwin" else LINUX_ROWS
    all_rows = {**defaults, **(rows or {})}
    states = statuses or {}
    queries: dict[str, Any] = {
        query_id: {
            "status": states.get(query_id, CollectionStatus.SUCCESS),
            "rows": query_rows,
        }
        for query_id, query_rows in all_rows.items()
    }
    asset = asset_id or (LAPTOP if platform == "darwin" else SERVER)
    hardware_uuid, hostname = identity or (f"uuid-{asset}", f"{asset}.test")
    result = CollectionResult(
        source="endpoint-script",
        collector="endpoint-script",
        collected_at=collected_at,
        asset_id=asset,
        collection_status=CollectionStatus.SUCCESS,
        data={
            "package": {
                "platform": platform,
                "hardware_uuid": hardware_uuid,
                "hostname": hostname,
            },
            "queries": queries,
        },
    )
    return HostEvidence(evidence_id=evidence_id, valid_until=valid_until, result=result)


def config(supported_os: list[SupportedOs] | None = None, *, enrolled=(LAPTOP, SERVER, "lap-bea")):
    policies = (
        [SupportedOs(platform="darwin", name="macOS", release="15")]
        if supported_os is None
        else supported_os
    )
    return EndpointConfig(
        max_evidence_age_days=30,
        supported_os=policies,
        hosts={
            asset_id: HostEnrollment(hardware_uuid=f"uuid-{asset_id}", hostname=f"{asset_id}.test")
            for asset_id in enrolled
        },
    )


ASSET_FIELDS: dict[AssetType, dict[str, Any]] = {
    AssetType.LAPTOP: {"os": "macOS 15", "owner": "Ana", "encrypted": True, "usage": "Dev"},
    AssetType.SERVER: {
        "hostname": "srv.test",
        "function": "Apps",
        "os": "Debian 12",
        "environment": "producción",
        "location": "Oficina",
    },
    AssetType.APPLICATION: {
        "service": "Facturación",
        "domain": "facturas.test",
        "repo": "org/facturas",
        "database": "db-facturas",
    },
}


def assets(*ids: str) -> list[Asset]:
    kinds = {"lap": AssetType.LAPTOP, "srv": AssetType.SERVER}
    return [
        Asset(
            id=asset_id,
            type=(kind := kinds.get(asset_id[:3], AssetType.APPLICATION)),
            deployment="deployed",
            **ASSET_FIELDS[kind],
        )
        for asset_id in ids
    ]


def evaluate(
    *evidence: HostEvidence,
    asset_ids: tuple[str, ...] | None = None,
    endpoint_config: EndpointConfig | None = None,
    at: datetime = AT,
) -> dict[str, CheckResult]:
    """Resultados indexados por `check_id` del primer activo evaluado."""
    ids = asset_ids or tuple(dict.fromkeys(item.asset_id for item in evidence)) or (LAPTOP,)
    results = evaluate_host_checks(assets(*ids), evidence, endpoint_config or config(), at)
    return {result.check_id: result for result in results if result.asset_id == ids[0]}


def evaluate_all(
    *evidence: HostEvidence, asset_ids: tuple[str, ...], at: datetime = AT
) -> list[CheckResult]:
    return evaluate_host_checks(assets(*asset_ids), evidence, config(), at)
