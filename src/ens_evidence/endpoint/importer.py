"""`evidence import --source endpoint`: valida, normaliza y persiste un paquete del script."""

from datetime import datetime, timedelta

from ens_evidence.connectors.config import load_connector_config
from ens_evidence.endpoint.config import CONFIG_NAME, EndpointConfig
from ens_evidence.endpoint.normalize import normalize, verify_enrollment
from ens_evidence.endpoint.package import PackageRejected, read_package
from ens_evidence.evidence.importer import import_attested_package
from ens_evidence.models.contracts import EvidenceManifest
from ens_evidence.scope.inventory import load_assets
from ens_evidence.scope.models import AssetType
from ens_evidence.workspace import WorkspaceStore

ENDPOINT_SOURCE = "endpoint"
PACKAGE_SOURCE = "endpoint-package"
ENDPOINT_ASSET_TYPES = frozenset({AssetType.LAPTOP, AssetType.SERVER})


def import_endpoint(
    store: WorkspaceStore, data: bytes, *, asset_id: str, submitted_by: str, now: datetime
) -> tuple[EvidenceManifest, EvidenceManifest]:
    """Devuelve (manifiesto del paquete, manifiesto del resultado normalizado)."""
    config = load_connector_config(store, CONFIG_NAME, EndpointConfig)
    _require_endpoint_asset(store, asset_id)
    package = read_package(data, now=now)
    verify_enrollment(package, config.hosts.get(asset_id))
    result = normalize(package, asset_id=asset_id)
    return import_attested_package(
        store,
        data,
        result,
        package_source=PACKAGE_SOURCE,
        collector_version=str(package.meta.script_version),
        submitted_by=submitted_by,
        valid_until=package.meta.collected_at + timedelta(days=config.max_evidence_age_days),
        now=now,
    )


def _require_endpoint_asset(store: WorkspaceStore, asset_id: str) -> None:
    assets, errors = load_assets(store)
    if assets is None:
        raise PackageRejected(f"no se puede leer el inventario: {'; '.join(errors)}")
    asset = next((candidate for candidate in assets.assets if candidate.id == asset_id), None)
    if asset is None or asset.type not in ENDPOINT_ASSET_TYPES:
        raise PackageRejected(f"{asset_id} no es un activo de tipo laptop o server del inventario")
