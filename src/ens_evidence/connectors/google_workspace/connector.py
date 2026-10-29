"""Conector de Google Workspace: solo lectura, clave de la cuenta de servicio solo por entorno."""

import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime

import httpx
from pydantic import BaseModel

from ens_evidence.connectors.base import Connector
from ens_evidence.connectors.google_workspace import sections
from ens_evidence.connectors.google_workspace.auth import GoogleWorkspaceError, ServiceAccountKey
from ens_evidence.connectors.google_workspace.client import WorkspaceClient
from ens_evidence.connectors.google_workspace.config import GoogleWorkspaceConfig
from ens_evidence.connectors.sections import Section
from ens_evidence.models.contracts import CollectionResult
from ens_evidence.models.enums import CollectionStatus
from ens_evidence.scope import load_assets
from ens_evidence.scope.models import AssetType
from ens_evidence.workspace import WorkspaceError, WorkspaceStore

KEY_ENV = "ENS_GOOGLE_SERVICE_ACCOUNT_KEY"
SOURCE = "google-workspace"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class GoogleWorkspaceConnector(Connector):
    name = SOURCE
    config_model = GoogleWorkspaceConfig

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
        """Comprueba que `asset_id` es un activo `identity` del inventario."""
        assert isinstance(config, GoogleWorkspaceConfig)
        assets, errors = load_assets(store)
        if assets is None:
            raise WorkspaceError("No se puede leer el inventario de activos: " + "; ".join(errors))
        asset = next((a for a in assets.assets if a.id == config.asset_id), None)
        if asset is None or asset.type is not AssetType.IDENTITY:
            raise WorkspaceError(
                f"asset_id: debe ser un activo identity del inventario ({config.asset_id})."
            )
        return config

    def collect(self, config: BaseModel) -> CollectionResult:
        assert isinstance(config, GoogleWorkspaceConfig)
        now = self._clock()
        collected: dict[str, Section] = {}
        error: str | None = None
        raw_key = self._env.get(KEY_ENV, "").strip()
        if not raw_key:
            error = f"{KEY_ENV} no está definida"
        else:
            try:
                key = ServiceAccountKey(raw_key)
                with WorkspaceClient(
                    key, config.admin_subject, transport=self._transport, clock=self._clock
                ) as client:
                    self._run(client, config, now, collected)
            except GoogleWorkspaceError as exc:
                error = exc.message
        return CollectionResult(
            source=SOURCE,
            collector=self.name,
            collected_at=now,
            asset_id=config.asset_id,
            data={
                "customer": config.customer,
                "error": error,
                "sections": {name: s.model_dump(mode="json") for name, s in collected.items()},
            },
            collection_status=CollectionStatus.ERROR if error else CollectionStatus.SUCCESS,
        )

    @staticmethod
    def _run(
        client: WorkspaceClient,
        config: GoogleWorkspaceConfig,
        now: datetime,
        collected: dict[str, Section],
    ) -> None:
        """Sonda al inicio y al final; una revocación a mitad invalida el conjunto."""
        customer = sections.verify_customer(client, config)
        users = collected["users"] = sections.collect_users(client, config)
        collected["admins"] = sections.collect_admins(client, config, users)
        collected["oauth_token_events"] = sections.collect_oauth_token_events(client, now)
        collected["admin_events"] = sections.collect_admin_events(client, now)
        sections.verify_customer(client, config, expected=customer)
        collected["customer"] = Section(status=CollectionStatus.SUCCESS, summary=customer)
