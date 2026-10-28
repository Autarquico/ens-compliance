"""Conector de Cloudflare: solo lectura, token solo por entorno."""

import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime

import httpx
from pydantic import BaseModel

from ens_evidence.connectors.base import Connector
from ens_evidence.connectors.cloudflare import sections
from ens_evidence.connectors.cloudflare.client import (
    CloudflareClient,
    CloudflareError,
    verify_token,
)
from ens_evidence.connectors.cloudflare.config import CloudflareConfig, CloudflareRun
from ens_evidence.connectors.sections import Section
from ens_evidence.models.contracts import CollectionResult
from ens_evidence.models.enums import CollectionStatus
from ens_evidence.scope import load_assets
from ens_evidence.scope.models import AssetType
from ens_evidence.workspace import WorkspaceError, WorkspaceStore

TOKEN_ENV = "ENS_CLOUDFLARE_API_TOKEN"
SOURCE = "cloudflare"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class CloudflareConnector(Connector):
    name = SOURCE
    config_model = CloudflareConfig

    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], datetime] = _utcnow,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._transport = transport
        self._clock = clock
        self._env = os.environ if env is None else env

    def prepare(self, config: BaseModel, store: WorkspaceStore) -> BaseModel:
        """Toma las zonas esperadas del activo `edge_network` del inventario."""
        assert isinstance(config, CloudflareConfig)
        assets, errors = load_assets(store)
        if assets is None:
            raise WorkspaceError("No se puede leer el inventario de activos: " + "; ".join(errors))
        asset = next((a for a in assets.assets if a.id == config.asset_id), None)
        if asset is None or asset.type is not AssetType.EDGE_NETWORK:
            raise WorkspaceError(
                f"asset_id: debe ser un activo edge_network del inventario ({config.asset_id})."
            )
        return CloudflareRun(**config.model_dump(), inventory_zones=asset.zones or [])

    def collect(self, config: BaseModel) -> CollectionResult:
        assert isinstance(config, CloudflareRun)
        now = self._clock()
        collected: dict[str, Section] = {}
        error: str | None = None
        token = self._env.get(TOKEN_ENV, "").strip()
        if not token:
            error = f"{TOKEN_ENV} no está definida"
        elif not (token.isascii() and token.isprintable()):
            error = f"{TOKEN_ENV} contiene caracteres no válidos"
        else:
            try:
                with CloudflareClient(token, transport=self._transport) as client:
                    self._run(client, config, now, collected)
            except CloudflareError as exc:
                error = exc.message
        return CollectionResult(
            source=SOURCE,
            collector=self.name,
            collected_at=now,
            asset_id=config.asset_id,
            data={
                "account_id": config.account_id,
                "token_kind": config.token_kind,
                "error": error,
                "public_routes": [route.model_dump() for route in config.public_routes],
                "sections": {name: s.model_dump(mode="json") for name, s in collected.items()},
            },
            collection_status=CollectionStatus.ERROR if error else CollectionStatus.SUCCESS,
        )

    @staticmethod
    def _run(
        client: CloudflareClient,
        config: CloudflareRun,
        now: datetime,
        collected: dict[str, Section],
    ) -> None:
        """Verifica el token al inicio y al final; una revocación a mitad invalida el conjunto."""

        def verify() -> None:
            verify_token(client, kind=config.token_kind, account_id=config.account_id)

        verify()
        zones = collected["zones"] = sections.collect_zones(client, config)
        dns = collected["dns"] = sections.collect_dns(client, zones)
        collected["tls"] = sections.collect_tls(client, zones)
        collected["waf"] = sections.collect_waf(client, zones)
        apps = collected["access_applications"] = sections.collect_access_applications(
            client, config
        )
        collected["access_policies"] = sections.collect_access_policies(client, config, apps)
        collected["access_groups"] = sections.collect_access_groups(client, config)
        collected["tunnels"] = sections.collect_tunnels(client, config, dns)
        collected["audit_logs"] = sections.collect_audit_logs(client, config, now)
        verify()
